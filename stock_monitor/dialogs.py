"""桌面版的設定視窗：個股設定、策略與停損、雲端帳戶、通知。

從 stock_monitor/desktop_app.py 拆出來的積木；每個視窗都只透過 app.engine（LiveMonitor）讀寫設定，
主畫面（desktop_app.py）只負責開啟它們。
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

from stock_monitor import notify as N

FONT = ("Microsoft JhengHei", 10)


class SettingsDialog:
    """設定某檔股票的提醒條件。空白 = 不使用。檢查規則在 live.py（和手機版一樣）。"""
    LABELS = {"above": "價格上限（突破提醒）", "below": "價格下限（跌破提醒）",
              "cost": "持有成本（真實持股可填，只提醒）", "stop": "停損 %（空白 = 用整體設定）",
              "trail": "移動停損 %（空白 = 用整體設定）"}

    def __init__(self, app, ticker, title, current, held=False):
        self.app, self.ticker = app, ticker
        win = self.win = tk.Toplevel(app.root)
        win.title("提醒設定 - " + title)
        win.transient(app.root)
        win.resizable(False, False)
        self.vars = {}
        for r, key in enumerate(self.LABELS):
            label = "持有成本（模擬持股自動帶入）" if key == "cost" and held else self.LABELS[key]
            tk.Label(win, text=label, font=FONT).grid(row=r, column=0, sticky="w", padx=12, pady=5)
            var = tk.StringVar(value="" if current.get(key) is None else "%g" % current[key])
            ttk.Entry(win, textvariable=var, width=12, state="disabled" if key == "cost" and held else "normal"
                      ).grid(row=r, column=1, padx=12)
            self.vars[key] = var
        self.msg = tk.Label(win, font=FONT, fg="#c92a2a")
        self.msg.grid(row=len(self.LABELS), column=0, columnspan=2)
        btns = tk.Frame(win)
        btns.grid(row=len(self.LABELS) + 1, column=0, columnspan=2, pady=10)
        ttk.Button(btns, text="儲存", command=self.save).pack(side="left", padx=4)
        ttk.Button(btns, text="取消", command=win.destroy).pack(side="left", padx=4)
        win.bind("<Return>", lambda e: self.save())
        win.grab_set()

    def save(self):
        values = {k: v.get().strip() for k, v in self.vars.items()}
        with self.app.engine.lock:
            try:
                self.app.engine.set_alerts(self.ticker, values)
            except ValueError as exc:
                return self.msg.config(text=str(exc))
        self.win.destroy()


class StrategyDialog:
    """選策略（勾選順序 = 優先順序，第一個是主策略）＋整體停損 % 與移動停損 %。"""

    def __init__(self, app):
        self.app = app
        with app.engine.lock:
            s = app.engine.state()["settings"]
        self.order = list(s["strategies"])
        win = self.win = tk.Toplevel(app.root)
        win.title("策略與停損")
        win.transient(app.root)
        win.resizable(False, False)
        tk.Label(win, font=FONT, fg="#555", justify="left",
                 text="勾選要判斷的策略。第一個勾的是 ★主策略，自動交易只跟它；其他的只提醒。\n"
                      "和回測工具、雲端看盤同一套策略。").grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=8)
        self.checks, self.marks = {}, {}
        for r, st in enumerate(s["strategy_list"], start=1):
            var = tk.BooleanVar(value=st["key"] in self.order)
            ttk.Checkbutton(win, text="%s：%s" % (st["name"], st["description"]), variable=var,
                            command=lambda k=st["key"]: self.toggle(k)).grid(row=r, column=0, sticky="w", padx=12)
            self.marks[st["key"]] = tk.Label(win, font=FONT, fg="#d6336c")
            self.marks[st["key"]].grid(row=r, column=1, sticky="w")
            self.checks[st["key"]] = var
        row = len(s["strategy_list"]) + 1
        self.stop = tk.StringVar(value="%g" % s["stop_loss_pct"])
        self.trail = tk.StringVar(value="%g" % s["trail_stop_pct"])
        for i, (label, var) in enumerate([("停損 %（虧損超過就賣，0 = 不用）", self.stop),
                                          ("移動停損 %（從最高價回落就賣，0 = 不用）", self.trail)]):
            tk.Label(win, text=label, font=FONT).grid(row=row + i, column=0, sticky="w", padx=12, pady=(8, 0))
            ttk.Entry(win, textvariable=var, width=8).grid(row=row + i, column=1, sticky="w", pady=(8, 0))
        self.msg = tk.Label(win, font=FONT, fg="#c92a2a")
        self.msg.grid(row=row + 2, column=0, columnspan=2)
        btns = tk.Frame(win)
        btns.grid(row=row + 3, column=0, columnspan=2, pady=10)
        ttk.Button(btns, text="儲存", command=self.save).pack(side="left", padx=4)
        ttk.Button(btns, text="取消", command=win.destroy).pack(side="left", padx=4)
        self.draw()
        win.grab_set()

    def toggle(self, key):
        self.order = [k for k in self.order if k != key] + ([key] if self.checks[key].get() else [])
        self.draw()

    def draw(self):
        for k, label in self.marks.items():
            label.config(text="★ 主策略" if self.order[:1] == [k] else "")

    def save(self):
        if not self.order:
            return self.msg.config(text="至少要選一個策略")
        with self.app.engine.lock:
            try:
                self.app.engine.update_settings({"strategies": self.order, "stop_loss_pct": self.stop.get(),
                                                 "trail_stop_pct": self.trail.get()})
            except ValueError as exc:
                return self.msg.config(text=str(exc))
        self.win.destroy()


NOTIFY_HELP = {
    "telegram": ("設定步驟（免費、沒有則數上限）：\n"
                 "1. 在 Telegram 搜尋 @BotFather，傳送 /newbot，照指示取名字\n"
                 "2. BotFather 會給你一串 Bot Token（像 123456789:ABC…）→ 貼到下面\n"
                 "3. 打開你剛建立的機器人，按「開始」（或隨便傳一句話）\n"
                 "4. 回來按「自動找 Chat ID」，再按「傳送測試訊息」"),
    "discord": ("設定步驟（免費、沒有則數上限）：\n"
                "1. 在 Discord 建一個自己的伺服器（或用現有的），選一個文字頻道\n"
                "2. 頻道設定（齒輪）→ 整合 → Webhook → 新 Webhook\n"
                "3. 按「複製 Webhook 網址」→ 貼到下面，按「傳送測試訊息」\n"
                "※ 手機要裝 Discord App 並開啟通知"),
    "line": ("設定步驟（LINE Notify 已停止服務，改用官方帳號推播）：\n"
             "1. 到 LINE Developers 用 LINE 帳號登入，建立 Provider\n"
             "2. 建立 Messaging API Channel（會自動產生一個官方帳號）\n"
             "3. Basic settings 頁最下面「Your user ID」→ 貼到 User ID\n"
             "4. Messaging API 頁最下面 Channel access token 按 Issue → 貼到 Token\n"
             "   （不是 Channel secret！secret 只有 32 個字，token 大約 170 個字）\n"
             "5. 用手機掃 Messaging API 頁的 QR code，把官方帳號加為好友\n"
             "※ 免費方案每月推播則數有上限"),
}
NOTIFY_FIELDS = {
    "telegram": [("token", "Bot Token", True), ("chat_id", "Chat ID", False)],
    "discord": [("webhook", "Webhook 網址", True)],
    "line": [("token", "Channel access token", True), ("user_id", "User ID（U 開頭）", False)],
}


class CloudDialog:
    """雲端帳戶：和雲端看盤共用同一個模擬帳戶。GitHub 權杖只存在本機的 data/cloud.json。"""

    def __init__(self, app):
        self.app = app
        with app.engine.lock:
            info = app.engine.state()["cloud"]
        win = self.win = tk.Toplevel(app.root)
        win.title("雲端帳戶")
        win.transient(app.root)
        win.resizable(False, False)
        tk.Label(win, font=("Microsoft JhengHei", 9), justify="left", fg="#555", bg="#f8f9fa", padx=10, pady=8,
                 wraplength=520, text=(
                     "開啟後，電腦版的模擬帳戶、持股、自選股、上下限、策略都以「雲端看盤」為準（只有一個帳戶）。\n"
                     "電腦版每分鐘看報價、提醒、畫圖；下單和改設定會用 GitHub 權杖寫到雲端，"
                     "雲端約 1～3 分鐘後處理（非開盤時間的單會等開盤）。自動交易由雲端每 15 分鐘做一次。\n"
                     "權杖：GitHub → Settings → Developer settings → Fine-grained tokens，"
                     "只選 taco0525、Contents 選 Read and write。")).pack(fill="x", padx=12, pady=(12, 6))
        self.enabled = tk.BooleanVar(value=info["enabled"])
        ttk.Checkbutton(win, text="使用雲端帳戶", variable=self.enabled).pack(anchor="w", padx=14)
        row = tk.Frame(win)
        row.pack(fill="x", padx=14, pady=6)
        tk.Label(row, text="GitHub 權杖", font=FONT).pack(side="left")
        self.token = tk.StringVar()
        ttk.Entry(row, textvariable=self.token, width=46, show="*").pack(side="left", padx=6)
        status = "已設定（留空＝不變）" if info["has_token"] else "還沒設定（設定前只能看，不能下單或改自選股）"
        if info.get("error"):
            status += "\n⚠ " + info["error"]
        elif info.get("updated"):
            status += "\n雲端帳戶更新於 " + info["updated"]
        tk.Label(win, text=status, font=FONT, fg="#555", justify="left").pack(anchor="w", padx=14)
        self.msg = tk.Label(win, font=FONT, fg="#c92a2a", wraplength=520)
        self.msg.pack()
        btns = tk.Frame(win)
        btns.pack(pady=10)
        ttk.Button(btns, text="儲存", command=self.save).pack(side="left", padx=4)
        if info["has_token"]:
            ttk.Button(btns, text="清除權杖", command=lambda: self.save(clear=True)).pack(side="left", padx=4)
        ttk.Button(btns, text="打開雲端看盤", command=lambda: webbrowser.open(info["page_url"])).pack(side="left", padx=4)
        ttk.Button(btns, text="取消", command=win.destroy).pack(side="left", padx=4)
        win.grab_set()

    def save(self, clear=False):
        E = self.app.engine
        on = self.enabled.get()
        if not on and E.cloud.enabled and not messagebox.askyesno(
                "雲端帳戶", "改回電腦版自己的模擬帳戶？（雲端帳戶不受影響，之後可以再切回來）", parent=self.win):
            return
        try:
            with E.lock:
                E.set_cloud(token=self.token.get().strip() or None, enabled=on, clear=clear)
        except ValueError as exc:
            self.msg.config(text=str(exc))
            return
        self.win.destroy()


class NotifyDialog:
    """LINE / Telegram / Discord 通知設定，可以同時開好幾個。Token 只存在本機的 notify_config.json。"""

    def __init__(self, app):
        self.app = app
        with app.engine.lock:
            cfg = N.merge(app.engine.notify_cfg)   # 複製一份來填表
        win = self.win = tk.Toplevel(app.root)
        win.title("通知設定")
        win.transient(app.root)
        win.resizable(False, False)
        self.results = queue.Queue()
        self.vars = {}
        self.msg = {}

        book = ttk.Notebook(win)
        book.pack(fill="both", padx=12, pady=(12, 4))
        for name in ("telegram", "discord", "line"):
            tab = tk.Frame(book, padx=10, pady=8)
            book.add(tab, text=N.CHANNELS[name])
            tk.Label(tab, text=NOTIFY_HELP[name], font=("Microsoft JhengHei", 9), justify="left", fg="#555",
                     bg="#f8f9fa", padx=10, pady=8).grid(row=0, column=0, columnspan=3, sticky="we", pady=(0, 8))
            v = self.vars[name] = {"enabled": tk.BooleanVar(value=cfg[name]["enabled"])}
            ttk.Checkbutton(tab, text="開啟 %s 通知" % N.CHANNELS[name], variable=v["enabled"]
                            ).grid(row=1, column=0, columnspan=3, sticky="w")
            for r, (key, label, secret) in enumerate(NOTIFY_FIELDS[name], start=2):
                v[key] = tk.StringVar(value=cfg[name].get(key, ""))
                tk.Label(tab, text=label, font=FONT).grid(row=r, column=0, sticky="w", pady=4)
                entry = ttk.Entry(tab, textvariable=v[key], width=46, show="*" if secret else "")
                entry.grid(row=r, column=1, pady=4, padx=(6, 0))
                if secret:
                    show = tk.BooleanVar()
                    ttk.Checkbutton(tab, text="顯示", variable=show,
                                    command=lambda e=entry, s=show: e.config(show="" if s.get() else "*")
                                    ).grid(row=r, column=2, padx=(4, 0))
            btns = tk.Frame(tab)
            btns.grid(row=10, column=0, columnspan=3, sticky="w", pady=(6, 0))
            ttk.Button(btns, text="傳送測試訊息", command=lambda n=name: self.test(n)).pack(side="left")
            if name == "telegram":
                ttk.Button(btns, text="自動找 Chat ID", command=self.find_chat_id).pack(side="left", padx=6)
            self.msg[name] = tk.Label(tab, font=FONT, fg="#c92a2a", wraplength=520, justify="left")
            self.msg[name].grid(row=11, column=0, columnspan=3, sticky="w", pady=(6, 0))

        self.alerts = tk.BooleanVar(value=cfg["alerts"])
        self.trades = tk.BooleanVar(value=cfg["trades"])
        opts = tk.Frame(win)
        opts.pack(fill="x", padx=14, pady=4)
        ttk.Checkbutton(opts, text="通知提醒（交叉、上下限、停損、跌破支撐）", variable=self.alerts).pack(side="left")
        ttk.Checkbutton(opts, text="通知模擬成交", variable=self.trades).pack(side="left", padx=12)
        self.save_msg = tk.Label(win, font=FONT, fg="#c92a2a", wraplength=560)
        self.save_msg.pack()
        btns = tk.Frame(win)
        btns.pack(pady=10)
        ttk.Button(btns, text="儲存", command=self.save).pack(side="left", padx=4)
        ttk.Button(btns, text="取消", command=win.destroy).pack(side="left", padx=4)
        win.grab_set()
        self.poll()

    def channel_values(self, name):
        return {k: (var.get() if k == "enabled" else var.get().strip()) for k, var in self.vars[name].items()}

    def form(self):
        out = {"alerts": bool(self.alerts.get()), "trades": bool(self.trades.get())}
        for name in self.vars:
            out[name] = self.channel_values(name)
        return out

    def say(self, name, text, ok=False):
        self.msg[name].config(text=text, fg="#2b8a3e" if ok else "#c92a2a")

    def test(self, name):
        try:
            temp = self.app.engine.notify_test_config(name, self.channel_values(name))
        except ValueError as exc:
            return self.say(name, str(exc))
        self.say(name, "傳送中…", ok=True)
        text = "✅ 自動看盤測試訊息：%s 通知設定成功！" % N.CHANNELS[name]
        threading.Thread(target=lambda: self.results.put(("test", name, N.send_all(temp, text))), daemon=True).start()

    def find_chat_id(self):
        token = self.vars["telegram"]["token"].get().strip()
        if not token:
            return self.say("telegram", "請先貼上 Bot Token")
        self.say("telegram", "尋找中…", ok=True)
        threading.Thread(target=lambda: self.results.put(("chat", "telegram", N.telegram_find_chats(token))),
                         daemon=True).start()

    def poll(self):
        """背景執行緒的結果回到畫面（tkinter 只能在主執行緒操作）。"""
        if not self.win.winfo_exists():
            return
        while not self.results.empty():
            kind, name, result = self.results.get_nowait()
            if kind == "test":
                if result:
                    self.say(name, "失敗：" + result[0][1])
                else:
                    self.say(name, "已送出，請看手機 %s" % N.CHANNELS[name], ok=True)
            else:
                chats, error = result
                if error:
                    self.say(name, error)
                else:
                    self.vars["telegram"]["chat_id"].set(chats[-1]["id"])
                    self.say(name, "找到了：%s（Chat ID %s）。可以按「傳送測試訊息」" % (chats[-1]["name"], chats[-1]["id"]),
                             ok=True)
        self.win.after(200, self.poll)

    def save(self):
        with self.app.engine.lock:
            try:   # 桌面版畫面上看得到 Token，所以從全新預設值套用（欄位清空就是真的清掉）
                self.app.engine.update_notify(self.form(), base=N.fresh_defaults())
            except ValueError as exc:
                return self.save_msg.config(text=str(exc))
        self.app.update_notify_button()
        self.win.destroy()
