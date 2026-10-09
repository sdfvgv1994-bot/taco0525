"""看盤核心：更新報價 → 判斷訊號 → 去重提醒 → 自動模擬交易 → 存檔。"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime

from common import costs
from common import strategies as strat
from common.market import fetch_history, parse_symbol

from .account import Account, TradeError
from .signals import Alert, Snapshot, evaluate, snapshot

ALERT_HISTORY_MAX = 1000


def default_fetcher(name: str, demo: bool, now: datetime):
    """回傳 (Yahoo 代號, 日 K DataFrame, 最新價)。"""
    sym = parse_symbol(name)
    code, df = fetch_history(sym, period="1y", demo=demo, end=now.date() if demo else None)
    price = float(df["Close"].iloc[-1])
    if demo:
        # 示範模式：每分鐘給一點隨機跳動，模擬盤中價格變化
        h = int(hashlib.md5(f"{code}{now:%Y%m%d%H%M}".encode()).hexdigest()[:8], 16)
        price = round(price * (1 + ((h % 2001) - 1000) / 1000 * 0.02), 2)
    return code, df, price


@dataclass
class RefreshResult:
    snapshots: dict = field(default_factory=dict)   # 名稱 → Snapshot
    errors: dict = field(default_factory=dict)      # 名稱 → 錯誤訊息
    alerts: list = field(default_factory=list)      # 這次新觸發的 Alert
    trades: list = field(default_factory=list)      # 這次自動成交
    notes: list = field(default_factory=list)       # 其他訊息（例如自動買進失敗）


class Monitor:
    def __init__(self, store, fetcher=default_fetcher):
        self.store = store
        self.fetcher = fetcher
        self.settings = store.settings()
        self.watchlist = store.watchlist()
        self.account = Account.from_dict(store.load("account.json", None))
        self.alert_state = store.load("alerts.json", {"date": "", "fired": [], "history": []})
        self.last_prices: dict = {}

    # ---- 存檔 ----
    def save(self):
        self.store.save_settings(self.settings)
        self.store.save_watchlist(self.watchlist)
        self.store.save("account.json", self.account.to_dict())
        self.store.save("alerts.json", self.alert_state)

    # ---- 策略 ----
    @property
    def primary_strategy(self) -> str:
        return (self.settings.get("strategies") or ["ma"])[0]

    def set_strategies(self, keys: list) -> None:
        keys = [strat.get(k).key for k in keys]  # 名稱錯會丟 ValueError
        if not keys:
            raise ValueError("至少要選一個策略")
        self.settings["strategies"] = list(dict.fromkeys(keys))  # 去重、保留順序
        self.save()

    # ---- 自選股 ----
    @staticmethod
    def normalize(name: str) -> str:
        return name.strip().upper()

    def add(self, name: str) -> str:
        key = self.normalize(name)
        parse_symbol(key)  # 格式錯會丟 ValueError
        self.watchlist.setdefault(key, {"lower": None, "upper": None})
        self.save()
        return key

    def remove(self, name: str) -> None:
        key = self.normalize(name)
        if key not in self.watchlist:
            raise KeyError(f"{key} 不在自選股裡")
        if self.account.holds(key):
            raise TradeError(f"{key} 還有持股，請先賣出再刪除")
        del self.watchlist[key]
        self.save()

    def set_limits(self, name: str, lower=None, upper=None) -> None:
        key = self.normalize(name)
        if key not in self.watchlist:
            raise KeyError(f"{key} 不在自選股裡")
        if lower is not None and upper is not None and lower >= upper:
            raise ValueError("下限必須小於上限")
        self.watchlist[key] = {"lower": lower, "upper": upper}
        self.save()

    # ---- 提醒去重：同一提醒一天只響一次 ----
    def _fresh(self, alert: Alert, now: datetime) -> bool:
        today = now.strftime("%Y-%m-%d")
        if self.alert_state.get("date") != today:
            self.alert_state["date"] = today
            self.alert_state["fired"] = []
        if alert.key in self.alert_state["fired"]:
            return False
        self.alert_state["fired"].append(alert.key)
        hist = self.alert_state.setdefault("history", [])
        hist.append({"time": now.strftime("%Y-%m-%d %H:%M:%S"),
                     "code": alert.code, "kind": alert.kind, "message": alert.message})
        del hist[:-ALERT_HISTORY_MAX]
        return True

    # ---- 更新 ----
    def refresh(self, now: datetime | None = None) -> RefreshResult:
        now = now or datetime.now()
        s = self.settings
        res = RefreshResult()
        for name, limits in list(self.watchlist.items()):
            try:
                _, df, price = self.fetcher(name, s["demo"], now)
                snap = snapshot(name, df, price, s)
            except Exception as e:  # noqa: BLE001 - 一檔失敗不影響其他檔
                res.errors[name] = str(e)
                continue
            res.snapshots[name] = snap
            self.last_prices[name] = snap.price
            pos = self.account.positions.get(name)
            for alert in evaluate(snap, limits, pos, s["stop_loss_pct"]):
                if not self._fresh(alert, now):
                    continue
                res.alerts.append(alert)
                self.store.log("提醒 " + alert.message, now)
                if s["auto_trade"]:
                    self._auto_trade(alert, snap, res, now)
        self.save()
        return res

    def _auto_trade(self, alert: Alert, snap: Snapshot, res: RefreshResult, now):
        name, price = alert.code, snap.price
        primary = self.primary_strategy
        # 買賣訊號只跟主策略（清單第一個），避免不同策略互相打架；停損一律執行
        if alert.kind in ("buy", "sell") and alert.strategy != primary:
            return
        why_name = strat.get(primary).name
        try:
            if alert.kind == "buy" and not self.account.holds(name):
                budget = min(self.settings["auto_buy_amount"], self.account.cash)
                shares = costs.max_shares(budget, price, costs.MIN_FEE)
                if shares <= 0:
                    res.notes.append(f"{name} 出現買進訊號但現金不足，未自動買進")
                    self.store.log(res.notes[-1], now)
                    return
                tw = parse_symbol(name).is_tw_stock
                rec = self.account.buy(name, price, shares, tw, f"自動：{why_name}買進", now)
            elif alert.kind in ("sell", "stop") and self.account.holds(name):
                why = f"自動：{why_name}賣出" if alert.kind == "sell" else "自動：停損"
                rec = self.account.sell(name, price, None, why, now)
            else:
                return
        except TradeError as e:
            res.notes.append(f"{name} 自動交易失敗：{e}")
            self.store.log(res.notes[-1], now)
            return
        res.trades.append(rec)
        self.store.log(f"模擬成交 {rec['side']} {name} {rec['shares']} 股 @ {rec['price']}"
                       f"（{rec['reason']}）", now)

    # ---- 手動交易 ----
    def manual_buy(self, name: str, shares: int, price: float | None = None) -> dict:
        key = self.normalize(name)
        if key not in self.watchlist:
            self.add(key)
        price = price or self.last_prices.get(key)
        if not price:
            raise TradeError("還沒有這檔的報價，請先更新一次或指定價格")
        rec = self.account.buy(key, price, shares, parse_symbol(key).is_tw_stock)
        self.store.log(f"模擬成交 買進 {key} {shares} 股 @ {price}（手動）")
        self.save()
        return rec

    def manual_sell(self, name: str, shares: int | None = None,
                    price: float | None = None) -> dict:
        key = self.normalize(name)
        price = price or self.last_prices.get(key)
        if not price:
            raise TradeError("還沒有這檔的報價，請先更新一次或指定價格")
        rec = self.account.sell(key, price, shares)
        self.store.log(f"模擬成交 賣出 {key} {rec['shares']} 股 @ {price}（手動）")
        self.save()
        return rec
