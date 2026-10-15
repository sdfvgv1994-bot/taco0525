"""畫面用的看盤服務：把 engine.Monitor 包成桌面視窗（desktop_app.py）和手機網頁（web_app.py）共用的積木。

    看盤規則（訊號、停損、移動停損、支撐線、自動交易）  → engine.Monitor（和文字版、雲端看盤同一套）
    這一塊只多做畫面需要的事：
        背景定時更新（抓資料時不鎖住，畫面不會卡）
        提醒紀錄、LINE / Telegram / Discord 通知（notify.py）、每日財經新聞（news.py）
        state()：把目前狀態整理成畫面要的 JSON（桌面版和手機版用同一份）

多執行緒：所有操作與 state() 都要在 `with live.lock:` 裡呼叫。
"""
from __future__ import annotations

import collections
import json
import re
import threading
from datetime import datetime
from pathlib import Path

from common import costs
from common import strategies as strat
from common.market import parse_symbol, uses_twse

from . import news as NEWS
from . import notify as N
from .account import Account, TradeError
from .engine import Monitor
from .signals import evaluate, risk_levels
from .storage import Store

KIND_NAME = {"buy": "買進訊號", "sell": "賣出訊號", "above": "突破上限", "below": "跌破下限",
             "stop": "停損", "trail": "移動停損", "support": "跌破支撐"}
KIND_ICON = {"buy": "🟢", "sell": "🔴", "above": "⬆️", "below": "⬇️", "stop": "🛑",
             "trail": "📉", "support": "🔻"}
# 畫面上的欄位名稱 → 自選股設定裡的名稱
ALERT_FIELDS = {"above": ("upper", "價格上限"), "below": ("lower", "價格下限"), "cost": ("cost", "持有成本"),
                "stop": ("stop", "停損 %"), "trail": ("trail", "移動停損 %")}
UI_DEFAULTS = {"auto": True, "news_enabled": True, "news_time": "08:00"}


class LiveMonitor:
    def __init__(self, data_dir, monitor: Monitor | None = None, source: str = "看盤",
                 demo: bool = False, data_source: str = "auto"):
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
        for h in self.m.alert_state.get("history", [])[-20:]:   # 接續上次的提醒紀錄
            self._push(h["time"][5:], f"{KIND_ICON.get(h['kind'], '•')} {h['message']}", "alert")
        self.log(f"{source}啟動，{len(self.m.watchlist)} 檔自選股")
        if notify_problem:
            self.log(notify_problem, "alert")

    # ====================== 紀錄與通知 ======================
    def _push(self, when: str, msg: str, kind: str):
        self.log_seq += 1
        self.logs.appendleft({"id": self.log_seq, "time": when, "msg": msg, "kind": kind})

    def log(self, msg: str, kind: str = "info", notify: str | None = None, persist: bool = False):
        """kind：info / alert / trade。notify="alerts" 或 "trades" 時排進手機通知。"""
        self._push(datetime.now().strftime("%m/%d %H:%M:%S"), msg, kind)
        if notify and self.notify_cfg.get(notify) and N.any_ready(self.notify_cfg):
            self.notify_pending.append(msg)
        if persist:
            self.store.log(msg)

    def flush_notify(self):
        """把累積的訊息合併成一則，送到所有開啟的管道（背景執行，LINE 免費則數有限）。"""
        if not self.notify_pending:
            return
        text = "📈 自動看盤 %s\n%s" % (datetime.now().strftime("%m/%d %H:%M"), "\n".join(self.notify_pending))
        self.notify_pending = []
        self.send_background(text)

    def send_background(self, text: str, cfg: dict | None = None):
        cfg = json.loads(json.dumps(cfg or self.notify_cfg))   # 複製一份，送出途中改設定也不影響

        def send():
            for channel, error in N.send_all(cfg, text):
                with self.lock:
                    self.log(f"{channel} 傳送失敗：{error}", "alert")
        threading.Thread(target=send, daemon=True).start()

    def update_notify(self, form: dict, base: dict | None = None):
        """套用通知設定表單。base=None：空白欄位保留原值（手機版）；桌面版傳全新預設值。"""
        new, problem = N.update_from_form(self.notify_cfg if base is None else base, form)
        if problem:
            raise ValueError(problem)
        self.notify_cfg = new
        N.save_config(new, self.notify_path)
        on = [N.CHANNELS[n] for n in N.CHANNELS if N.ready(new, n)]
        self.log("通知設定已儲存：%s" % ("、".join(on) if on else "全部關閉"), "trade")
        self.version += 1

    def notify_test_config(self, channel: str, values: dict | None = None) -> dict:
        if channel not in N.CHANNELS:
            raise ValueError("沒有這個通知管道")
        ch = dict(values if values is not None else self.notify_cfg[channel], enabled=True)
        problem = N.check(channel, ch)
        if problem:
            raise ValueError(problem)
        temp = N.fresh_defaults()
        temp[channel] = ch
        return temp

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
            if self.busy or not self.m.watchlist:
                return
            self.busy = True
            self.version += 1
            names, settings = list(self.m.watchlist), dict(self.m.settings)
        fetched = None
        try:
            fetched = self.m.fetch_all(names=names, settings=settings)   # 連網的部分不鎖住
        except Exception as exc:  # noqa: BLE001
            with self.lock:
                self.log(f"更新失敗：{exc}", "alert")
        with self.lock:
            try:
                if fetched is not None:
                    self._apply(fetched)
            finally:
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

    # ====================== 畫面上的操作 ======================
    def find(self, ticker: str) -> dict | None:
        return self.m.watchlist.get(Monitor.normalize(str(ticker or "")))

    def _key(self, ticker) -> str:
        key = Monitor.normalize(str(ticker or ""))
        if key not in self.m.watchlist:
            raise ValueError(f"找不到 {key}")
        return key

    def _changed(self):
        self.m.save()
        self.version += 1

    def add(self, ticker: str) -> str:
        key = Monitor.normalize(str(ticker or ""))
        if not key:
            raise ValueError("請輸入代號")
        if key in self.m.watchlist:
            raise ValueError(f"{key} 已經在清單裡")
        self.m.add(key)   # 代號格式不對會丟 ValueError
        self.version += 1
        self.wake.set()
        return key

    def remove(self, ticker: str):
        key = self._key(ticker)
        try:
            self.m.remove(key)
        except TradeError as e:
            raise ValueError(str(e)) from None
        self.snaps.pop(key, None)
        self.version += 1

    def set_alerts(self, ticker: str, values: dict):
        """價格上下限、手動成本、停損 %、移動停損 %。空白 = 不使用（停損 / 移動停損空白 = 用整體設定）。"""
        key = self._key(ticker)
        clean = {}
        for ui, (field, label) in ALERT_FIELDS.items():
            v = values.get(ui)
            if v is None or str(v).strip() == "":
                clean[field] = None
                continue
            try:
                clean[field] = float(v)
            except (TypeError, ValueError):
                raise ValueError(f"「{label}」要是數字") from None
            if clean[field] < 0 or (clean[field] == 0 and field not in ("stop", "trail")):
                raise ValueError(f"「{label}」要大於 0")
        if clean["lower"] is not None and clean["upper"] is not None and clean["lower"] >= clean["upper"]:
            raise ValueError("價格下限要小於上限")
        entry = self.m.watchlist[key]
        if self.m.account.holds(key):   # 有模擬持股時成本跟著持股走，畫面上的成本欄位不用存
            clean["cost"] = entry.get("cost")
        if clean["cost"] != entry.get("cost"):
            entry.pop("peak", None)   # 成本改了，最高價重新記
        for field, v in clean.items():
            if v is None and field not in ("lower", "upper"):
                entry.pop(field, None)
            else:
                entry[field] = v
        # 條件改了，這檔的提醒可以重新響
        self.m.alert_state["fired"] = [k for k in self.m.alert_state.get("fired", [])
                                       if not k.startswith(f"{key}|")]
        self._changed()
        self.wake.set()

    def order(self, ticker: str, side: str, shares, reason: str = "手動（手機）") -> dict:
        if side not in ("buy", "sell"):
            raise ValueError("side 錯誤")
        try:
            shares = int(shares)
        except (TypeError, ValueError):
            raise ValueError("股數要是整數") from None
        key = Monitor.normalize(str(ticker or ""))
        if not self.m.last_prices.get(key):
            raise ValueError(f"{key} 還沒有報價，無法模擬下單")
        try:
            if side == "buy":
                rec = self.m.manual_buy(key, shares, reason=reason)
            else:
                rec = self.m.manual_sell(key, shares, reason=reason)
        except TradeError as e:
            self.log(f"模擬{'買進' if side == 'buy' else '賣出'} {key} 失敗：{e}", "alert")
            raise ValueError(str(e)) from None
        self.log(self.trade_text(rec), "trade", notify="trades")
        self.version += 1
        self.flush_notify()
        return rec

    def update_settings(self, values: dict):
        s = dict(self.m.settings)
        ints = {"short": "short_ma", "long": "long_ma", "interval": "interval", "budget": "auto_buy_amount"}
        try:
            for ui, key in ints.items():
                if ui in values:
                    s[key] = int(float(values[ui]))
            for key in ("stop_loss_pct", "trail_stop_pct"):
                if key in values:
                    s[key] = float(values[key] or 0)
        except (TypeError, ValueError):
            raise ValueError("設定要填數字") from None
        for key in ("auto", "auto_trade", "news_enabled"):
            if key in values:
                s[key] = bool(values[key])
        if "news_time" in values:
            t = str(values["news_time"]).strip()
            if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t):
                raise ValueError("新聞推播時間格式要像 08:00")
            s["news_time"] = t
        if not 2 <= s["short_ma"] < s["long_ma"] <= 240:
            raise ValueError("均線天數：2 ≤ 短均 < 長均 ≤ 240")
        if not (0 <= s["stop_loss_pct"] < 100 and 0 <= s.get("trail_stop_pct", 0) < 100):
            raise ValueError("停損 % 要在 0～99 之間")
        s["interval"] = max(15, s["interval"])
        s["auto_buy_amount"] = max(1000, s["auto_buy_amount"])
        if "strategies" in values:
            keys = values["strategies"]
            if isinstance(keys, str):
                keys = [k for k in keys.split(",") if k.strip()]
            keys = [strat.get(str(k).strip()).key for k in keys]   # 名稱錯會丟 ValueError
            if not keys:
                raise ValueError("至少要選一個策略")
            s["strategies"] = list(dict.fromkeys(keys))
        if s["auto_trade"] != self.m.settings["auto_trade"]:
            self.log("自動模擬交易已%s" % (f"開啟：主策略「{strat.get(s['strategies'][0]).name}」買進訊號買進 "
                                           f"{s['auto_buy_amount']:,.0f} 元，賣出訊號 / 停損 / 移動停損賣出"
                                           if s["auto_trade"] else "關閉"), "trade")
        self.m.settings.clear()
        self.m.settings.update(s)
        self._changed()

    def set_trendlines(self, ticker: str, lines):
        key = self._key(ticker)
        if not isinstance(lines, list) or len(lines) > 30:
            raise ValueError("趨勢線最多 30 條")
        clean = []
        for ln in lines:
            try:
                item = {"t1": str(ln["t1"])[:10], "p1": float(ln["p1"]), "t2": str(ln["t2"])[:10], "p2": float(ln["p2"])}
                datetime.fromisoformat(item["t1"]), datetime.fromisoformat(item["t2"])
            except (KeyError, TypeError, ValueError):
                raise ValueError("趨勢線資料格式錯誤") from None
            clean.append(item)
        self.m.watchlist[key]["trendlines"] = clean
        self._changed()

    def set_hlines(self, ticker: str, prices):
        """水平線（支撐 / 壓力）：只存價格。跌破時發出「跌破支撐」提醒。"""
        key = self._key(ticker)
        if not isinstance(prices, list) or len(prices) > 30:
            raise ValueError("水平線最多 30 條")
        try:
            clean = sorted({round(float(p), 4) for p in prices}, reverse=True)
        except (TypeError, ValueError):
            raise ValueError("水平線價格要是數字") from None
        if any(p <= 0 for p in clean):
            raise ValueError("價格要大於 0")
        self.m.watchlist[key]["support"] = clean
        self.m.alert_state["fired"] = [k for k in self.m.alert_state.get("fired", [])
                                       if not k.startswith(f"{key}|support|")]
        self._changed()

    def reset_account(self):
        self.m.account = Account()
        for entry in self.m.watchlist.values():
            if not entry.get("cost"):
                entry.pop("peak", None)
        self._changed()
        self.log("模擬帳戶已重設", "trade", persist=True)

    # ====================== 每日財經新聞 ======================
    def watch_names(self) -> list:
        return [(c, self.m.names.get(c, "")) for c in self.m.watchlist]

    def last_digest_date(self) -> str:
        data = self.store.load("news_state.json", {})
        return data.get("last_digest", "") if isinstance(data, dict) else ""

    def maybe_send_digest(self, now: datetime | None = None):
        """每天設定的時間之後送一次（電腦開著才會送）。還沒設定任何通知管道就先不送，設定好當天會補送。"""
        now = now or datetime.now()
        with self.lock:
            s = self.m.settings
            due = (s.get("news_enabled") and now.strftime("%H:%M") >= s.get("news_time", "08:00")
                   and self.last_digest_date() != now.date().isoformat() and N.any_ready(self.notify_cfg))
        if due:
            self.send_digest(now)

    def send_digest(self, now: datetime | None = None) -> list:
        now = now or datetime.now()
        data = NEWS.get_news()                       # 網路請求不鎖
        with self.lock:
            text = NEWS.digest_text(data["items"], self.watch_names(), now=now)
            cfg = json.loads(json.dumps(self.notify_cfg))
        errors = N.send_all(cfg, text)
        with self.lock:
            self.store.save("news_state.json", {"last_digest": now.date().isoformat()})
            if errors:
                for channel, error in errors:
                    self.log(f"每日新聞 {channel} 傳送失敗：{error}", "alert")
            else:
                self.log(f"已推送每日財經新聞（共抓到 {len(data['items'])} 則新聞）", "trade")
        return errors

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
                         "auto": s.get("auto", True), "auto_trade": s["auto_trade"],
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
        }

    def _max_buy(self, price) -> int:
        if not price:
            return 0
        return costs.max_shares(min(self.m.settings["auto_buy_amount"], self.m.account.cash), price, costs.MIN_FEE)
