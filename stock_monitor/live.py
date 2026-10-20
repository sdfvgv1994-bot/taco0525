"""畫面用的看盤服務：把 engine.Monitor 包成桌面視窗（desktop_app.py）和手機網頁（web_app.py）共用的積木。

    看盤規則（訊號、停損、移動停損、支撐線、自動交易）  → engine.Monitor（和文字版、雲端看盤同一套）
    這一塊只多做畫面需要的事：
        背景定時更新（抓資料時不鎖住，畫面不會卡）
        提醒紀錄、LINE / Telegram / Discord 通知（notify.py）、每日財經新聞（news.py）
        state()：把目前狀態整理成畫面要的 JSON（桌面版和手機版用同一份）
        雲端帳戶模式（cloud_link.py，預設開啟）：帳戶、持股、自選股、策略設定都以雲端看盤為準，
            這裡只負責每分鐘看報價、提醒、K 線；下單和改設定送到雲端（GitHub），不在本機自動交易

多執行緒：所有操作與 state() 都要在 `with live.lock:` 裡呼叫。

積木（LiveMonitor 繼承這三個 mixin，方法名稱和以前一樣）：
    live_notify.py   紀錄、手機通知、每日財經新聞
    live_cloud.py    雲端帳戶（讀雲端、送自選股和訂單到雲端、開關雲端模式）
    live_actions.py  畫面上的操作（自選股、提醒條件、模擬下單、設定、趨勢線、水平線）
這個檔只留：建立、背景更新、套用抓到的資料、給畫面的 state()。
"""
from __future__ import annotations

import collections
import threading
from datetime import datetime
from pathlib import Path

from common import costs
from common import strategies as strat
from common.market import parse_symbol, uses_twse

from . import notify as N
from .cloud_link import CloudLink
from .engine import Monitor
from .live_actions import ALERT_FIELDS, ActionsMixin  # noqa: F401  ALERT_FIELDS：舊的 live.ALERT_FIELDS 照樣能用
from .live_cloud import CloudMixin
from .live_notify import NotifyMixin
from .signals import evaluate, risk_levels
from .storage import Store

KIND_NAME = {"buy": "買進訊號", "sell": "賣出訊號", "above": "突破上限", "below": "跌破下限",
             "stop": "停損", "trail": "移動停損", "support": "跌破支撐"}
KIND_ICON = {"buy": "🟢", "sell": "🔴", "above": "⬆️", "below": "⬇️", "stop": "🛑",
             "trail": "📉", "support": "🔻"}
UI_DEFAULTS = {"auto": True, "news_enabled": True, "news_time": "08:00"}


class LiveMonitor(NotifyMixin, CloudMixin, ActionsMixin):
    def __init__(self, data_dir, monitor: Monitor | None = None, source: str = "看盤",
                 demo: bool = False, data_source: str = "auto", cloud: bool | None = None,
                 cloud_session=None):
        self.store = Store(data_dir)
        self.m = monitor or Monitor(self.store)
        for k, v in UI_DEFAULTS.items():
            self.m.settings.setdefault(k, v)
        self.m.settings["demo"] = demo            # 每次執行由參數決定，不沿用上次（和文字版一樣）
        self.m.settings["source"] = data_source
        self.notify_path = Path(self.store.dir) / N.CONFIG_NAME
        self.notify_cfg, notify_problem = N.load_config(self.notify_path)
        self.lock = threading.RLock()
        self.logs = collections.deque(maxlen=100)
        self.log_seq = 0          # 每筆紀錄的編號，畫面用來判斷哪些是新的
        self.version = 0          # 資料每變一次就 +1，畫面用來判斷要不要重畫
        self.notify_pending: list = []
        self.busy = False
        self.updated: str | None = None
        self.wake = threading.Event()
        self.snaps: dict = {}     # 代號 → 最新 Snapshot
        self.errors: dict = {}    # 代號 → 抓不到的原因
        self.symbols: dict = {}   # 代號 → 實際資料代號（2330 → 2330.TW）
        self.live: set = set()    # 價格來自證交所即時報價的代號
        self.realtime_tried = False
        self.notes: list = []
        # 雲端帳戶：cloud=None 依 data/cloud.json（預設開啟）；測試或不想用時傳 False
        self.cloud = CloudLink(self.store, enabled=cloud, session=cloud_session)
        self.cloud_data: dict | None = None
        self.cloud_error: str | None = None
        self.cloud_auto = False                  # 雲端的自動交易開關（本機一律不自動交易）
        self._cloud_trades: set | None = None    # 已經看過的雲端成交（新成交才記錄、推播）
        self._cloud_orders: dict = {}            # 訂單 id → 上次看到的狀態
        self._backup_local_account()
        for h in self.m.alert_state.get("history", [])[-20:]:   # 接續上次的提醒紀錄
            self._push(h["time"][5:], f"{KIND_ICON.get(h['kind'], '•')} {h['message']}", "alert")
        self.log(f"{source}啟動，{len(self.m.watchlist)} 檔自選股"
                 + ("；使用雲端帳戶（帳戶、自選股、策略以雲端看盤為準）" if self.cloud.enabled else ""))
        if notify_problem:
            self.log(notify_problem, "alert")

    # ====================== 背景更新 ======================
    def run_forever(self, respect_auto: bool = False, news_digest: bool = True):
        """每隔 interval 秒更新一次；被叫醒就立刻更新。
        respect_auto：設定裡關掉「自動更新」就只在被叫醒時更新（桌面版用）。
        news_digest：順便檢查每日新聞推播時間到了沒。"""
        while True:
            self.refresh()
            if news_digest:
                try:
                    self.maybe_send_digest()
                except Exception as exc:  # noqa: BLE001 - 新聞出錯不能讓看盤停下來
                    with self.lock:
                        self.log(f"每日新聞推播失敗：{exc.__class__.__name__}", "alert")
            with self.lock:
                s = self.m.settings
                wait = max(15, int(s.get("interval", 60))) if (s.get("auto", True) or not respect_auto) else None
            self.wake.wait(wait)
            self.wake.clear()

    def refresh(self):
        with self.lock:
            if self.busy:
                return
            self.busy = True
            self.version += 1
        try:
            self.cloud_pull()
            with self.lock:
                if not self.m.watchlist:
                    return
                names, settings = list(self.m.watchlist), dict(self.m.settings)
            fetched = None
            try:
                fetched = self.m.fetch_all(names=names, settings=settings)
            except Exception as exc:  # noqa: BLE001
                with self.lock:
                    self.log(f"更新失敗：{exc}", "alert")
            if fetched is not None:
                with self.lock:
                    self._apply(fetched)
        finally:
            with self.lock:
                self.busy = False
                self.version += 1

    def _apply(self, fetched: dict):
        res = self.m.apply(fetched)
        for name, got in fetched["data"].items():
            if isinstance(got, tuple):
                self.symbols[name] = got[0]
        self.snaps.update(res.snapshots)
        self.errors = dict(res.errors)
        for name in res.errors:
            self.snaps.pop(name, None)
        self.live = set(res.live)
        self.realtime_tried = res.realtime_tried
        for a in res.alerts:
            self.log(f"{KIND_ICON.get(a.kind, '•')} {self._label(a.code)} {a.message.split(' ', 1)[-1]}",
                     "alert", notify="alerts")
        for t in res.trades:
            self.log(self.trade_text(t), "trade", notify="trades")
        for n in res.notes:
            if n not in self.notes:   # 同樣的說明（例如即時報價抓不到）不要每分鐘重複
                self.log("ℹ " + n)
        self.notes = list(res.notes)
        self.updated = datetime.now().strftime("%H:%M:%S")
        self.flush_notify()

    def _label(self, code: str) -> str:
        name = self.m.names.get(code, "")
        return f"{code} {name}".strip()

    def trade_text(self, t: dict) -> str:
        text = (f"【模擬{t['side']}】{self._label(t['code'])} {t['shares']:,} 股 @ {t['price']:.2f}，"
                f"手續費 {t['fee']:,.0f}")
        if t["side"] == "賣出":
            text += f"，證交稅 {t['tax']:,.0f}，損益 {t.get('pnl', 0):+,.0f}"
        return text + f"（{t['reason']}）"

    # ====================== 給畫面的資料（不含任何 Token） ======================
    def symbol_of(self, ticker: str) -> str:
        key = Monitor.normalize(str(ticker or ""))
        return self.symbols.get(key) or parse_symbol(key).candidates[0]

    def row(self, code: str, entry: dict) -> dict | None:
        if code in self.errors:
            return {"error": self.errors[code]}
        snap = self.snaps.get(code)
        if snap is None:
            return None
        s = self.m.settings
        pos = self.m.account.positions.get(code)
        r = risk_levels(entry, pos, s["stop_loss_pct"], s.get("trail_stop_pct", 0))
        alerts = []
        for a in evaluate(snap, entry, pos, s["stop_loss_pct"], s.get("trail_stop_pct", 0)):
            alerts.append(f"{KIND_NAME[a.kind][:2]}：{strat.get(a.strategy).name}" if a.strategy
                          else KIND_NAME[a.kind])
        return {
            "price": snap.price, "change": snap.change_pct / 100,
            "ma_short": snap.short_ma, "ma_long": snap.long_ma, "rsi": snap.rsi,
            "trend": "多頭" if snap.short_ma > snap.long_ma else "空頭",
            "stop_price": r["stop_price"], "trail_price": r["trail_price"],
            "pnl": snap.price / r["cost"] - 1 if r["cost"] else None,
            "date": snap.date or "", "alerts": alerts,
            "live": code in self.live,
            # 台股沒拿到證交所即時報價：價格是最近一天的收盤價（畫面標 *）
            "delayed": self.realtime_tried and uses_twse(parse_symbol(code)) and code not in self.live,
        }

    def state(self) -> dict:
        m, a, s = self.m, self.m.account, self.m.settings
        prices = dict(m.last_prices)
        stocks = []
        for code, entry in m.watchlist.items():
            pos = a.positions.get(code)
            price = prices.get(code)
            stocks.append({
                "ticker": code, "symbol": self.symbol_of(code), "name": m.names.get(code, ""),
                "row": self.row(code, entry),
                "settings": {"above": entry.get("upper"), "below": entry.get("lower"),
                             "cost": pos["avg_cost"] if pos else entry.get("cost"),
                             "stop": entry.get("stop"), "trail": entry.get("trail")},
                "position": {"shares": pos["shares"], "cost": pos["avg_cost"]} if pos else None,
                "max_buy": self._max_buy(price),
            })
        equity = a.total_assets(prices)
        return {
            "updated": self.updated, "busy": self.busy,
            "settings": {"short": s["short_ma"], "long": s["long_ma"], "interval": s["interval"],
                         "auto": s.get("auto", True),
                         "auto_trade": self.cloud_auto if self.cloud.enabled else s["auto_trade"],
                         "budget": s["auto_buy_amount"], "stop_loss_pct": s["stop_loss_pct"],
                         "trail_stop_pct": s.get("trail_stop_pct", 0),
                         "news_enabled": s.get("news_enabled", True), "news_time": s.get("news_time", "08:00"),
                         "strategies": list(s["strategies"]),
                         "strategy_list": [{"key": st.key, "name": st.name, "description": st.description}
                                           for st in strat.STRATEGIES.values()]},
            "notify": N.public_status(self.notify_cfg),   # 只有開關與是否填好，不含 Token
            "stocks": stocks,
            "account": {"cash": a.cash, "market_value": a.market_value(prices), "equity": equity,
                        "initial": a.initial, "return": equity / a.initial - 1,
                        "realized": sum(t.get("pnl") or 0 for t in a.trades)},
            "trades": [{"time": t["time"], "ticker": t["code"], "side": t["side"], "shares": t["shares"],
                        "price": t["price"], "fee": round(t["fee"]), "tax": round(t["tax"]),
                        "pnl": t.get("pnl"), "reason": t["reason"]} for t in reversed(a.trades[-50:])],
            "logs": list(self.logs)[:60],
            "notes": list(self.notes),
            "cloud": {**self.cloud.public(), "error": self.cloud_error,
                      "updated": (self.cloud_data or {}).get("updated"),
                      "orders": [dict(o) for o in (self.cloud_data or {}).get("orders", [])][:20]},
        }

    def _max_buy(self, price) -> int:
        if not price:
            return 0
        return costs.max_shares(min(self.m.settings["auto_buy_amount"], self.m.account.cash), price, costs.MIN_FEE)
