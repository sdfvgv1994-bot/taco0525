"""看盤服務的積木：紀錄、手機通知（LINE / Telegram / Discord）、每日財經新聞。

LiveMonitor（live.py）繼承這個 mixin；用到 LiveMonitor 的 lock、logs、notify_cfg、store、m 等屬性。
"""
from __future__ import annotations

import json
import threading
from datetime import datetime

from . import news as NEWS
from . import notify as N


class NotifyMixin:
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
