"""看盤服務的積木：雲端帳戶（帳戶、持股、自選股、策略設定以雲端看盤為準）。

讀雲端（cloud_pull / _cloud_sync）、把自選股或訂單送到雲端（_push_watchlist / _cloud_order）、
開關雲端帳戶模式（set_cloud）。LiveMonitor（live.py）繼承這個 mixin。
"""
from __future__ import annotations

from pathlib import Path

from common.market import parse_symbol

from .account import Account
from .cloud_link import CloudError


class CloudMixin:
    # ====================== 雲端帳戶 ======================
    def cloud_pull(self):
        """讀雲端看盤的最新帳戶、自選股、設定（沒開雲端帳戶就什麼都不做）。"""
        if not self.cloud.enabled:
            return
        try:
            data, err = self.cloud.fetch(), None   # 連網的部分不鎖住
        except CloudError as e:
            data, err = None, str(e)
        with self.lock:
            self._cloud_sync(data, err)

    def _cloud_sync(self, data: dict | None, err: str | None):
        """把雲端看盤的帳戶、自選股、策略設定搬進本機的引擎（本機只看報價、提醒，不自動交易）。"""
        if err:
            if err != self.cloud_error:
                self.log(f"☁ {err}（先用上一次的雲端資料）", "alert")
            self.cloud_error = err
            return
        if self.cloud_error:
            self.log("☁ 雲端帳戶恢復連線")
        self.cloud_error = None
        self.cloud_data = data
        m, s = self.m, data.get("settings", {})
        # 策略設定以雲端為準
        keys = [x["key"] if isinstance(x, dict) else x for x in s.get("strategies", [])]
        upd = {k: s[k] for k in ("short_ma", "long_ma", "stop_loss_pct", "trail_stop_pct", "auto_buy_amount") if k in s}
        if keys:
            upd["strategies"] = keys
        if "strategy_params" in s:
            upd["strategy_params"] = s["strategy_params"]
        m.settings.update(upd)
        self.cloud_auto = bool(s.get("auto_trade"))
        m.settings["auto_trade"] = False
        # 自選股以雲端為準（本機自己加的停損 %、手動成本、趨勢線保留）
        new = {}
        for w in data.get("watch", []):
            code = w["code"]
            entry = dict(m.watchlist.get(code, {}))
            lim = w.get("limits") or {}
            entry["lower"], entry["upper"] = lim.get("lower"), lim.get("upper")
            if lim.get("support"):
                entry["support"] = list(lim["support"])
            else:
                entry.pop("support", None)
            new[code] = entry
            if w.get("name"):
                m.names[code] = w["name"]
        if new:
            for code in set(m.watchlist) - set(new):
                self.snaps.pop(code, None)
            m.watchlist.clear()
            m.watchlist.update(new)
        # 帳戶以雲端為準（唯讀的鏡像，本機不會改它）
        a = data["account"]
        trades = list(reversed(data.get("trades", [])))
        positions = {p["code"]: {"shares": p["shares"], "avg_cost": p["avg_cost"],
                                 "tw": parse_symbol(p["code"]).is_tw_stock,
                                 **({"peak": p["peak"]} if p.get("peak") else {})}
                     for p in data.get("positions", [])}
        m.account = Account(a.get("cash", 0), positions, trades, a.get("initial", 1_000_000))
        for p in data.get("positions", []):
            if p.get("price") and p["code"] not in m.last_prices:
                m.last_prices[p["code"]] = p["price"]
        # 新的雲端成交、訂單結果
        keyof = lambda t: (t.get("time"), t.get("code"), t.get("side"), t.get("shares"))  # noqa: E731
        seen = {keyof(t) for t in trades}
        if self._cloud_trades is not None:
            for t in trades:
                if keyof(t) not in self._cloud_trades:
                    self.log("☁ " + self.trade_text(t), "trade", notify="trades")
        self._cloud_trades = seen
        for o in data.get("orders", []):
            before = self._cloud_orders.get(o["id"])
            if before is not None and before != o["status"] and o["status"] in ("rejected", "expired"):
                self.log(f"☁ 雲端訂單 {o['code']} {'買進' if o['side'] == 'buy' else '賣出'}"
                         f"{'被拒絕' if o['status'] == 'rejected' else '已取消'}：{o.get('message', '')}", "alert")
            self._cloud_orders[o["id"]] = o["status"]
        self.version += 1
        self.flush_notify()

    def _cloud_rows(self, watchlist: dict | None = None) -> list:
        wl = self.m.watchlist if watchlist is None else watchlist
        return [(c, {"lower": e.get("lower"), "upper": e.get("upper"), "support": e.get("support") or []})
                for c, e in wl.items()]

    def _push_watchlist(self, watchlist: dict, message: str):
        rows = self._cloud_rows(watchlist)
        from .cloud_link import set_watchlist
        self.cloud.edit_config(lambda text: set_watchlist(text, rows), message)
        self.log(f"☁ 已送到雲端：{message}（約 1～3 分鐘後雲端套用）", "trade")

    def _backup_local_account(self):
        """改用雲端帳戶時，本機原本的模擬帳戶另外存一份（account_local.json），改回本機帳戶時拿回來。"""
        if self.cloud.enabled and not (Path(self.store.dir) / "account_local.json").exists():
            self.store.save("account_local.json", self.m.account.to_dict())

    def set_cloud(self, token: str | None = None, enabled: bool | None = None, clear: bool = False):
        """設定 GitHub 權杖、開關雲端帳戶模式（只能在電腦本機改）。"""
        self.cloud.configure(token=token, enabled=enabled, clear=clear)
        self._backup_local_account()
        if enabled is False:
            self.m.account = Account.from_dict(self.store.load("account_local.json", None)
                                               or self.store.load("account.json", None))
            self.m.save()
            self.cloud_data = None
            self.log("已改回本機帳戶（電腦版自己模擬交易）", "trade")
        elif enabled:
            self.log("已改用雲端帳戶", "trade")
        elif token:
            self.log("已記住 GitHub 權杖（存在這台電腦的 data/cloud.json）", "trade")
        self.version += 1
        self.wake.set()

    def _cloud_order(self, key: str, side: str, shares: int | None) -> dict:
        if key not in self.m.watchlist:
            raise ValueError(f"{key} 不在自選股裡")
        if shares is not None and shares <= 0:
            raise ValueError("股數要大於 0")
        if side == "sell":
            held = self.m.account.positions.get(key, {}).get("shares", 0)
            if not held:
                raise ValueError(f"{key} 目前沒有持股")
            if shares is not None and shares > held:
                raise ValueError(f"持股只有 {held:,} 股")
        finished = [o["id"] for o in (self.cloud_data or {}).get("orders", []) if o.get("status") != "pending"]
        order = self.cloud.add_order(key, side, shares, finished)
        self._cloud_orders[order["id"]] = "sent"
        what = f"{'買進' if side == 'buy' else '賣出'} {self._label(key)} {'全部' if shares is None else f'{shares:,} 股'}"
        self.log(f"☁ 已送出雲端訂單：{what}，雲端約 1～3 分鐘後用當時價格成交（休市就等開盤）", "trade")
        self.version += 1
        return order
