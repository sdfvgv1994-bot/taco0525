"""
自動看盤 桌面版（tkinter 視窗）——只管「畫面」：按鈕、表格、對話框。
看盤規則全在 engine.py（和文字版、手機版、雲端看盤同一套），這裡透過 live.py 使用。

執行：python stock_monitor/desktop_app.py          （Windows 可以雙擊 stock_monitor\啟動桌面版.bat）
      python stock_monitor/desktop_app.py --demo   （示範資料，不連網）

使用方式：
    輸入代號按「加入」（台股只打數字、美股英文代號、加權、BTC…）
    雙擊某一列可以設定：價格上限 / 下限、手動成本、停損 %、移動停損 %
    「策略與停損…」：選策略（第一個是主策略，自動交易只跟它）、整體停損 % 與移動停損 %
模擬下單：選一檔按「模擬買進 / 模擬賣出」；勾「自動模擬交易」就跟著主策略自動買賣
通知：按「通知…」設定 LINE / Telegram / Discord

注意：和手機版、文字版共用 stock_monitor/data 的存檔，一次只開一個。
※ 學習用程式，不是投資建議。
"""
from __future__ import annotations

import argparse
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if __name__ == "__main__":  # 先自動更新，再匯入 pandas 等套件（Windows 更新套件時檔案才不會被占用）
    from common.updater import check_and_update
    check_and_update()

from stock_monitor import notify as N  # noqa: E402
from stock_monitor.import_old import auto_import  # noqa: E402
from stock_monitor.live import LiveMonitor  # noqa: E402
from stock_monitor.storage import INITIAL_CASH  # noqa: E402

try:
    import winsound
except ImportError:   # 不是 Windows 時就只用 tkinter 的 bell
    winsound = None

FONT = ("Microsoft JhengHei", 10)
POLL_MS = 300   # 多久看一次引擎有沒有新資料


class DesktopApp:
    COLUMNS = [("ticker", "代號 / 名稱", 140), ("price", "現價", 80), ("change", "漲跌", 70),
               ("ma_short", "短均", 80), ("ma_long", "長均", 80), ("trend", "趨勢", 55),
               ("cost", "成本 / 損益", 120), ("shares", "模擬持股", 80), ("stop_price", "停損價", 80),
               ("trail_price", "移動停損價", 90), ("status", "狀態", 190)]

    def __init__(self, root, engine):
        self.root = root
        self.engine = engine
        self.seen_log = 0          # 已經顯示到第幾筆紀錄
        self.drawn_version = -1    # 表格畫到引擎的哪個版本
        self.state = None          # 最近一次從引擎拿到的資料
        root.title("自動看盤")
        root.geometry("1240x720")

        with engine.lock:
            settings = engine.state()["settings"]
        self.build_toolbar(settings)
        self.build_paper_bar(settings)
        self.build_table()
        self.build_log()
        self.update_notify_button()

        threading.Thread(target=engine.run_forever, kwargs={"respect_auto": True, "news_digest": False},
                         daemon=True).start()
        self.poll()

    # ====================== 版面 ======================
    def build_toolbar(self, s):
        bar = tk.Frame(self.root, pady=8, padx=10)
        bar.pack(fill="x")
        self.add_var = tk.StringVar()
        entry = ttk.Entry(bar, textvariable=self.add_var, width=12)
        entry.pack(side="left")
        entry.bind("<Return>", lambda e: self.add_stock())
        ttk.Button(bar, text="加入", command=self.add_stock).pack(side="left", padx=(4, 4))
        ttk.Button(bar, text="刪除選取", command=self.remove_stock).pack(side="left")
        ttk.Button(bar, text="設定提醒…", command=self.edit_selected).pack(side="left", padx=4)
        ttk.Button(bar, text="策略與停損…", command=lambda: StrategyDialog(self)).pack(side="left")

        self.short_var = tk.IntVar(value=s["short"])
        self.long_var = tk.IntVar(value=s["long"])
        self.interval_var = tk.IntVar(value=s["interval"])
        self.auto_var = tk.BooleanVar(value=s["auto"])
        for label, var, lo, hi in [("短均", self.short_var, 2, 200), ("長均", self.long_var, 3, 240),
                                   ("每幾秒更新", self.interval_var, 15, 3600)]:
            tk.Label(bar, text=label, font=FONT).pack(side="left", padx=(12, 3))
            ttk.Spinbox(bar, from_=lo, to=hi, textvariable=var, width=5).pack(side="left")
        ttk.Checkbutton(bar, text="自動更新", variable=self.auto_var,
                        command=self.apply_and_refresh).pack(side="left", padx=10)
        self.refresh_btn = ttk.Button(bar, text="立即更新", command=self.apply_and_refresh)
        self.refresh_btn.pack(side="left")
        self.clock = tk.Label(bar, font=FONT, fg="#666")
        self.clock.pack(side="right")

    def build_paper_bar(self, s):
        bar = tk.LabelFrame(self.root, text=" 模擬帳戶（不會真的下單） ", font=FONT, padx=8, pady=6)
        bar.pack(fill="x", padx=10, pady=(0, 8))
        ttk.Button(bar, text="模擬買進…", command=lambda: self.manual_order("buy")).pack(side="left")
        ttk.Button(bar, text="模擬賣出…", command=lambda: self.manual_order("sell")).pack(side="left", padx=4)
        self.auto_trade_var = tk.BooleanVar(value=s["auto_trade"])
        ttk.Checkbutton(bar, text="自動模擬交易", variable=self.auto_trade_var,
                        command=self.apply_settings).pack(side="left", padx=(12, 4))
        tk.Label(bar, text="每筆投入", font=FONT).pack(side="left")
        self.budget_var = tk.IntVar(value=s["budget"])
        ttk.Spinbox(bar, from_=1000, to=10_000_000, increment=10_000, textvariable=self.budget_var,
                    width=9).pack(side="left", padx=3)
        tk.Label(bar, text="元", font=FONT).pack(side="left")
        ttk.Button(bar, text="交易紀錄…", command=self.show_trades).pack(side="left", padx=(12, 4))
        ttk.Button(bar, text="重設帳戶", command=self.reset_account).pack(side="left")
        self.notify_btn = ttk.Button(bar, command=lambda: NotifyDialog(self))
        self.notify_btn.pack(side="left", padx=(12, 0))
        self.account_label = tk.Label(bar, font=("Microsoft JhengHei", 10, "bold"))
        self.account_label.pack(side="right")

    def build_table(self):
        frame = tk.Frame(self.root, padx=10)
        frame.pack(fill="both", expand=True)
        style = ttk.Style()
        style.configure("Treeview", font=("Microsoft JhengHei", 11), rowheight=28)
        style.configure("Treeview.Heading", font=("Microsoft JhengHei", 10, "bold"))
        self.tree = ttk.Treeview(frame, columns=[c[0] for c in self.COLUMNS], show="headings",
                                 selectmode="browse")
        for key, title, width in self.COLUMNS:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor="w" if key in ("ticker", "status") else "e")
        self.tree.tag_configure("up", foreground="#d6336c")
        self.tree.tag_configure("down", foreground="#2b8a3e")
        self.tree.tag_configure("alert", background="#fff3bf")
        self.tree.tag_configure("error", foreground="#868e96")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda e: self.edit_selected())
        self.tree.bind("<Delete>", lambda e: self.remove_stock())

    def build_log(self):
        frame = tk.LabelFrame(self.root, text=" 提醒紀錄 ", font=FONT, padx=6, pady=4)
        frame.pack(fill="x", padx=10, pady=8)
        self.log_box = tk.Text(frame, height=8, font=("Microsoft JhengHei", 10), state="disabled",
                               relief="flat", bg="#f8f9fa")
        self.log_box.pack(fill="x")
        self.log_box.tag_configure("alert", foreground="#c92a2a")
        self.log_box.tag_configure("trade", foreground="#1864ab")
        tk.Label(self.root, text="※ 模擬下單不會真的下單。Yahoo 報價可能延遲，模擬成交價與實際會有差距，僅供學習參考，非投資建議。",
                 font=("Microsoft JhengHei", 9), fg="#868e96").pack(pady=(0, 6))

    # ====================== 和引擎溝通 ======================
    def call(self, fn, *args, **kwargs):
        """在鎖裡呼叫引擎的操作；失敗就把原因寫進提醒紀錄。成功回傳 True。"""
        with self.engine.lock:
            try:
                fn(*args, **kwargs)
                return True
            except ValueError as exc:
                if not self.engine.logs or str(exc) not in self.engine.logs[0]["msg"]:   # 引擎已記錄就不重複
                    self.engine.log(str(exc), "alert")
                return False

    def poll(self):
        """定時看引擎：有新紀錄就顯示（有提醒或成交就響），資料有變就重畫表格。"""
        E = self.engine
        with E.lock:
            fresh = [l for l in E.logs if l["id"] > self.seen_log]
            state = E.state() if E.version != self.drawn_version else None
            version, busy, updated = E.version, E.busy, E.updated
        for item in reversed(fresh):
            self.show_log(item)
        if fresh:
            self.seen_log = fresh[0]["id"]
            if self.drawn_version >= 0 and any(l["kind"] != "info" for l in fresh):
                self.ring()
        if state is not None:
            self.state = state
            self.render(state)
            self.drawn_version = version
        self.refresh_btn.state(["disabled"] if busy else ["!disabled"])
        if updated:
            self.clock.config(text="更新於 " + updated)
        self.root.after(POLL_MS, self.poll)

    def show_log(self, item):
        self.log_box.config(state="normal")
        self.log_box.insert("1.0", "%s  %s\n" % (item["time"][-8:], item["msg"]),
                            item["kind"] if item["kind"] != "info" else "")
        self.log_box.config(state="disabled")

    def ring(self):
        if winsound:
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        else:
            self.root.bell()

    def form_settings(self):
        """把工具列上的數字讀出來；格子裡不是數字就回傳 None。"""
        try:
            return {"short": int(self.short_var.get()), "long": int(self.long_var.get()),
                    "interval": int(self.interval_var.get()), "auto": bool(self.auto_var.get()),
                    "auto_trade": bool(self.auto_trade_var.get()), "budget": int(self.budget_var.get())}
        except (tk.TclError, ValueError):
            with self.engine.lock:
                self.engine.log("設定欄位要填整數", "alert")
            return None

    def apply_settings(self):
        values = self.form_settings()
        return values is not None and self.call(self.engine.update_settings, values)

    def apply_and_refresh(self):
        if self.apply_settings():
            self.engine.wake.set()

    # ====================== 自選股 ======================
    def selected(self):
        sel = self.tree.selection()
        return sel[0] if sel else None

    def add_stock(self):
        ticker = self.add_var.get().strip()
        if ticker and self.call(self.engine.add, ticker):
            self.add_var.set("")

    def remove_stock(self):
        ticker = self.selected()
        if ticker:
            self.call(self.engine.remove, ticker)

    def stock(self, ticker):
        with self.engine.lock:
            st = self.engine.state()
        self.state = st
        return next((x for x in st["stocks"] if x["ticker"] == ticker), None)

    def edit_selected(self):
        ticker = self.selected()
        st = self.stock(ticker) if ticker else None
        if st:
            SettingsDialog(self, ticker, st["symbol"], st["settings"], held=st["position"] is not None)

    # ====================== 模擬下單 ======================
    def manual_order(self, side):
        ticker = self.selected()
        E = self.engine
        if not ticker:
            with E.lock:
                E.log("請先在表格選一檔股票", "alert")
            return
        if not self.apply_settings():   # 先套用「每筆投入」
            return
        st = self.stock(ticker)
        price = (st["row"] or {}).get("price") if st else None
        held = (st["position"] or {}).get("shares", 0) if st else 0
        problem = (f"{ticker} 還沒有報價，無法模擬下單" if price is None
                   else f"{ticker} 沒有模擬持股" if side == "sell" and not held else None)
        if problem:
            with E.lock:
                E.log(problem, "alert")
            return
        if side == "buy":
            prompt = "現價 %.2f，現金 %s 元\n要買幾股？（1 張 = 1000 股）" % (price, f"{self.state['account']['cash']:,.0f}")
            shares = self.ask_shares("模擬買進 %s" % ticker, prompt, st["max_buy"])
        else:
            prompt = "現價 %.2f，持有 %s 股\n要賣幾股？" % (price, f"{held:,}")
            shares = self.ask_shares("模擬賣出 %s" % ticker, prompt, held)
        if shares:
            self.call(E.order, ticker, side, shares, "手動")

    def ask_shares(self, title, prompt, default):
        return simpledialog.askinteger(title, prompt, parent=self.root, initialvalue=max(default, 1), minvalue=1)

    def show_trades(self):
        with self.engine.lock:
            st = self.engine.state()
        trades, realized = st["trades"], st["account"]["realized"]
        win = tk.Toplevel(self.root)
        win.title("模擬交易紀錄")
        win.geometry("820x400")
        cols = [("time", "時間", 150), ("ticker", "代號", 80), ("side", "買賣", 50), ("shares", "股數", 70),
                ("price", "價格", 80), ("fee", "手續費", 60), ("tax", "證交稅", 60), ("pnl", "損益", 90),
                ("reason", "原因", 150)]
        tree = ttk.Treeview(win, columns=[c[0] for c in cols], show="headings")
        for key, title, width in cols:
            tree.heading(key, text=title)
            tree.column(key, width=width, anchor="w" if key in ("time", "ticker", "reason") else "e")
        tree.tag_configure("buy", foreground="#d6336c")
        tree.tag_configure("sell", foreground="#2b8a3e")
        for t in trades:
            tree.insert("", "end", tags=("buy" if t["side"] == "買進" else "sell",), values=[
                t["time"], t["ticker"], t["side"], f"{t['shares']:,}", "%.2f" % t["price"], t["fee"], t["tax"],
                "" if t["pnl"] is None else f"{t['pnl']:+,.0f}", t["reason"]])
        tree.pack(fill="both", expand=True, padx=8, pady=8)
        tk.Label(win, font=FONT, text="共 %d 筆，已實現損益 %s 元" % (len(trades), f"{realized:+,.0f}")).pack(pady=(0, 8))

    def reset_account(self):
        if messagebox.askyesno("重設模擬帳戶", "清空所有模擬持股與交易紀錄，現金回到 %s 元？" % f"{INITIAL_CASH:,}",
                               parent=self.root):
            self.call(self.engine.reset_account)

    # ====================== 通知 ======================
    def update_notify_button(self):
        with self.engine.lock:
            cfg = self.engine.notify_cfg
            on = [N.CHANNELS[n] for n in N.CHANNELS if N.ready(cfg, n)]
        self.notify_btn.config(text="通知：%s…" % ("、".join(on) if on else "關"))

    # ====================== 表格 ======================
    def render(self, state):
        selected = self.selected()
        self.tree.delete(*self.tree.get_children())
        num = lambda v: "" if v is None else ("%.2f" % v if v < 1000 else "%.1f" % v)
        for s in state["stocks"]:
            t, row, pos = s["ticker"], s["row"], s["position"]
            name = s["ticker"] + (" " + s["name"] if s["name"] else "")
            shares = f"{pos['shares']:,}" if pos else ""
            if row is None:
                values, tags = [name] + [""] * 6 + [shares, "", "", "更新中…"], ()
            elif "error" in row:
                values, tags = [name] + [""] * 6 + [shares, "", "", row["error"]], ("error",)
            else:
                cost = s["settings"]["cost"]
                cost_text = "%s / %+.1f%%" % (num(cost), row["pnl"] * 100) if cost and row["pnl"] is not None else ""
                values = [name, num(row["price"]) + ("*" if row.get("delayed") else ""),
                          "%+.2f%%" % (row["change"] * 100),
                          num(row["ma_short"]), num(row["ma_long"]), row["trend"], cost_text, shares,
                          num(row["stop_price"]), num(row["trail_price"]),
                          "、".join(row.get("alerts", [])) or ("資料日 " + row["date"])]
                tags = ["up" if row["change"] > 0 else "down" if row["change"] < 0 else ""]
                if row.get("alerts"):
                    tags.append("alert")
            self.tree.insert("", "end", iid=t, values=values, tags=tags)
        if selected and self.tree.exists(selected):
            self.tree.selection_set(selected)
        a = state["account"]
        ret = a["return"]
        self.account_label.config(
            fg="#d6336c" if ret > 0 else "#2b8a3e" if ret < 0 else "#333",
            text="現金 %s　股票市值 %s　總資產 %s（%+.2f%%）" % (
                f"{a['cash']:,.0f}", f"{a['market_value']:,.0f}", f"{a['equity']:,.0f}", ret * 100))


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


def main(argv=None):
    ap = argparse.ArgumentParser(description="自動看盤 桌面版")
    ap.add_argument("--demo", action="store_true", help="使用示範資料（不連網）")
    ap.add_argument("--source", choices=["auto", "twse", "yahoo"], default="auto",
                    help="資料來源：auto（台股用證交所，失敗改 Yahoo）/ twse / yahoo")
    ap.add_argument("--data", default=str(Path(__file__).resolve().parent / "data"), help="存檔資料夾")
    ap.add_argument("--no-update", action="store_true", help="不要檢查 GitHub 新版")
    args = ap.parse_args(argv)
    moved = auto_import(args.data)
    engine = LiveMonitor(args.data, source="桌面版", demo=args.demo, data_source=args.source)
    with engine.lock:
        for line in moved:
            engine.log(line, "trade")
    root = tk.Tk()
    DesktopApp(root, engine)
    root.mainloop()


if __name__ == "__main__":
    main()
