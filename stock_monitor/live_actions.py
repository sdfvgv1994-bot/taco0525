"""看盤服務的積木：畫面上的操作（加減自選股、提醒條件、模擬下單、設定、趨勢線、水平線、重設帳戶）。

桌面版、手機版按下按鈕都會呼叫這裡；開了雲端帳戶時改的是雲端（live_cloud.py）。
LiveMonitor（live.py）繼承這個 mixin。
"""
from __future__ import annotations

import re
from datetime import datetime

from common import strategies as strat
from common.market import parse_symbol

from .account import Account, TradeError
from .engine import Monitor

# 畫面上的欄位名稱 → 自選股設定裡的名稱
ALERT_FIELDS = {"above": ("upper", "價格上限"), "below": ("lower", "價格下限"), "cost": ("cost", "持有成本"),
                "stop": ("stop", "停損 %"), "trail": ("trail", "移動停損 %")}


class ActionsMixin:
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
        if self.cloud.enabled:
            parse_symbol(key)   # 代號格式不對會丟 ValueError
            self._push_watchlist({**self.m.watchlist, key: {"lower": None, "upper": None}}, f"加入自選股 {key}")
        self.m.add(key)   # 代號格式不對會丟 ValueError
        self.version += 1
        self.wake.set()
        return key

    def remove(self, ticker: str):
        key = self._key(ticker)
        if self.cloud.enabled:
            if self.m.account.holds(key):
                raise ValueError(f"{key} 還有持股，請先賣出再刪除")
            self._push_watchlist({c: e for c, e in self.m.watchlist.items() if c != key}, f"刪除自選股 {key}")
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
        if self.cloud.enabled and (clean["lower"], clean["upper"]) != (entry.get("lower"), entry.get("upper")):
            wl = {c: dict(e) for c, e in self.m.watchlist.items()}
            wl[key].update(lower=clean["lower"], upper=clean["upper"])
            self._push_watchlist(wl, f"設定 {key} 上下限")
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
        if self.cloud.enabled:
            return self._cloud_order(key, side, shares)
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
        if self.cloud.enabled:   # 策略、均線、停損、每筆金額、自動交易：改雲端的 config.toml
            cur = {**self.m.settings, "auto_trade": self.cloud_auto}
            names = {"short_ma": "短均線", "long_ma": "長均線", "auto_buy_amount": "每筆金額", "stop_loss_pct": "停損",
                     "trail_stop_pct": "移動停損", "strategies": "策略", "auto_trade": "自動交易"}
            changed = {k: s[k] for k in names if s.get(k) != cur.get(k)}
            if changed:
                from .cloud_link import set_settings
                self.cloud.edit_config(lambda text: set_settings(text, changed),
                                       "電腦版修改雲端設定：" + "、".join(names[k] for k in changed))
                self.log("☁ 已送到雲端：修改" + "、".join(names[k] for k in changed) + "（約 1～3 分鐘後雲端套用）", "trade")
                self.cloud_auto = s["auto_trade"]
            s["auto_trade"] = False
        elif s["auto_trade"] != self.m.settings["auto_trade"]:
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
        if self.cloud.enabled and clean != (self.m.watchlist[key].get("support") or []):
            wl = {c: dict(e) for c, e in self.m.watchlist.items()}
            wl[key]["support"] = clean
            self._push_watchlist(wl, f"設定 {key} 支撐線")
        self.m.watchlist[key]["support"] = clean
        self.m.alert_state["fired"] = [k for k in self.m.alert_state.get("fired", [])
                                       if not k.startswith(f"{key}|support|")]
        self._changed()

    def reset_account(self):
        if self.cloud.enabled:
            raise ValueError("雲端帳戶要到 GitHub 刪除 monitor-data 分支才能重設（見 cloud_monitor/README.md「想重來」）")
        self.m.account = Account()
        for entry in self.m.watchlist.values():
            if not entry.get("cost"):
                entry.pop("peak", None)
        self._changed()
        self.log("模擬帳戶已重設", "trade", persist=True)
