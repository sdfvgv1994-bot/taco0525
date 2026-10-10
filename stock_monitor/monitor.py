"""自動看盤系統（含模擬下單）。

執行：python stock_monitor/monitor.py          （真實資料，需能連 Yahoo Finance）
      python stock_monitor/monitor.py --demo   （示範資料，不連網）
"""
from __future__ import annotations

import argparse
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common import strategies as strat  # noqa: E402
from common.market import parse_symbol, uses_twse  # noqa: E402
from stock_monitor.account import TradeError  # noqa: E402
from stock_monitor.engine import Monitor  # noqa: E402
from stock_monitor.storage import Store  # noqa: E402

def dwidth(text: str) -> int:
    """顯示寬度（中文字佔兩格）。"""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def pad(text: str, width: int) -> str:
    """依顯示寬度補空白，讓表格對齊。"""
    return text + " " * max(0, width - dwidth(text))


KIND_ICON = {"buy": "🟢", "sell": "🔴", "above": "⬆️ ", "below": "⬇️ ", "stop": "🛑",
             "golden": "🟢", "death": "🔴"}  # 後兩個是舊版提醒紀錄用的


def bell():
    print("\a", end="", flush=True)


def show_quotes(m: Monitor, res):
    names = "、".join(strat.get(k).name for k in m.settings["strategies"])
    print(f"\n=== 報價 {datetime.now():%H:%M:%S}（策略：{names}）===")
    labels = {n: f"{n} {m.names.get(n, '')}".strip() for n in m.watchlist}
    width = max([14] + [dwidth(x) + 2 for x in labels.values()])
    print(f"{pad('代號', width)}{'現價':>10}{'漲跌%':>8}{'MA' + str(m.settings['short_ma']):>10}"
          f"{'MA' + str(m.settings['long_ma']):>10}{'RSI':>6}  趨勢  持股")
    for name in m.watchlist:
        if name in res.errors:
            print(f"{pad(labels[name], width)}  ⚠ {res.errors[name][:60]}")
            continue
        s = res.snapshots.get(name)
        if not s:
            continue
        trend = "多" if s.short_ma > s.long_ma else "空"
        pos = m.account.positions.get(name)
        held = f"{pos['shares']} 股" if pos else ""
        # 台股沒拿到即時報價時，價格是最近一天的收盤價，加上 * 標記
        mark = "*" if res.realtime_tried and name not in res.live and \
            uses_twse(parse_symbol(name)) else " "
        print(f"{pad(labels[name], width)}{s.price:>9.2f}{mark}{s.change_pct:>+8.2f}{s.short_ma:>10.2f}"
              f"{s.long_ma:>10.2f}{s.rsi:>6.0f}   {trend}   {held}")
        today = [f"{'買' if v == 1 else '賣'}:{strat.get(k).name}" for k, v in s.signals.items() if v]
        if today:
            print(f"{'':<10}  今日訊號 {'  '.join(today)}")
    for a in res.alerts:
        print(f"  {KIND_ICON.get(a.kind, '!')} {a.message}")
    for t in res.trades:
        print(f"  💰 模擬成交 {t['side']} {t['code']} {t['shares']} 股 @ {t['price']}（{t['reason']}）")
    for n in res.notes:
        print(f"  ℹ {n}")
    if res.alerts:
        bell()


def show_account(m: Monitor):
    a, prices = m.account, m.last_prices
    print("\n=== 模擬帳戶 ===")
    print(f"現金     {a.cash:>15,.0f}")
    print(f"持股市值 {a.market_value(prices):>15,.0f}")
    print(f"總資產   {a.total_assets(prices):>15,.0f}")
    print(f"報酬率   {a.return_pct(prices):>+14.2f}%")
    if a.positions:
        print(f"\n{'代號':<10}{'股數':>8}{'成本':>10}{'現價':>10}{'損益':>12}{'%':>8}")
        for c, p in a.positions.items():
            px = prices.get(c, p["avg_cost"])
            pnl = (px - p["avg_cost"]) * p["shares"]
            pct = (px / p["avg_cost"] - 1) * 100
            print(f"{c:<10}{p['shares']:>8}{p['avg_cost']:>10.2f}{px:>10.2f}{pnl:>12,.0f}{pct:>+8.2f}")
    if not prices and a.positions:
        print("（尚未更新報價，市值以成本計算）")


def show_trades(m: Monitor, n=30):
    print("\n=== 交易紀錄（最近 %d 筆）===" % n)
    if not m.account.trades:
        print("（沒有交易）")
    for t in m.account.trades[-n:]:
        extra = f" 損益 {t['pnl']:+,.0f}" if "pnl" in t else ""
        print(f"{t['time']} {t['side']} {t['code']:<8} {t['shares']:>6} 股 @ {t['price']:<10}"
              f"手續費 {t['fee']:,.0f} 稅 {t['tax']:,.0f} 金額 {t['cash_flow']:+,.0f}"
              f"{extra}（{t['reason']}）")


def show_alerts(m: Monitor, n=30):
    print("\n=== 提醒紀錄（最近 %d 筆）===" % n)
    hist = m.alert_state.get("history", [])
    if not hist:
        print("（沒有提醒）")
    for h in hist[-n:]:
        print(f"{h['time']} {KIND_ICON.get(h['kind'], '!')} {h['message']}")


def ask(prompt: str, cast=str, default=None, allow_empty=False):
    while True:
        raw = input(prompt).strip()
        if not raw:
            if default is not None or allow_empty:
                return default
            continue
        try:
            return cast(raw)
        except ValueError:
            print("格式不對，請再輸入一次")


def edit_settings(m: Monitor):
    s = m.settings
    print("\n=== 設定（直接按 Enter 保持不變）===")
    s["short_ma"] = ask(f"短均線天數 [{s['short_ma']}]: ", int, s["short_ma"])
    s["long_ma"] = ask(f"長均線天數 [{s['long_ma']}]: ", int, s["long_ma"])
    if s["short_ma"] >= s["long_ma"]:
        print("短均線要比長均線短，已還原為 5 / 20")
        s["short_ma"], s["long_ma"] = 5, 20
    s["interval"] = max(10, ask(f"更新間隔秒數 [{s['interval']}]: ", int, s["interval"]))
    s["stop_loss_pct"] = ask(f"停損 %（0 = 關閉）[{s['stop_loss_pct']}]: ", float, s["stop_loss_pct"])
    yn = ask(f"自動模擬交易 y/n [{'y' if s['auto_trade'] else 'n'}]: ", str, "")
    if yn:
        s["auto_trade"] = yn.lower().startswith("y")
    s["auto_buy_amount"] = ask(f"自動買進金額 [{s['auto_buy_amount']}]: ", float, s["auto_buy_amount"])
    m.save()
    print("已儲存")


def edit_strategies(m: Monitor):
    keys = list(strat.STRATEGIES)
    cur = m.settings["strategies"]
    print("\n=== 策略設定 ===")
    for i, st in enumerate(strat.STRATEGIES.values(), 1):
        mark = "★" if st.key == cur[0] else ("✓" if st.key in cur else " ")
        print(f" {mark} {i}. {st.name:<8} {st.description}")
    print("★ = 主策略（自動交易只跟它）  ✓ = 只提醒")
    raw = ask(f"要用哪些策略？輸入編號，用逗號分隔，第一個當主策略 "
              f"[{','.join(str(keys.index(k) + 1) for k in cur)}]: ", str, "")
    if raw:
        try:
            picked = [keys[int(x) - 1] for x in raw.replace("，", ",").split(",") if x.strip()]
        except (ValueError, IndexError):
            print("⚠ 編號不對，策略沒有變更")
            return
        m.set_strategies(picked)
    yn = ask("要調整策略參數嗎？y/n [n]: ", str, "n")
    if yn.lower().startswith("y"):
        sp = m.settings.setdefault("strategy_params", {})
        for key in m.settings["strategies"]:
            if key == "ma":
                print("  均線交叉的天數請在「11 設定」調整")
                continue
            st = strat.get(key)
            cur_p = {**st.defaults, **sp.get(key, {})}
            print(f"  {st.name}：")
            for k, v in cur_p.items():
                cur_p[k] = ask(f"    {k} [{v:g}]: ", float, v)
            try:
                st.run(_dummy_df(st), **cur_p)
            except ValueError as e:
                print(f"  ⚠ {e}，{st.name} 參數沒有變更")
                continue
            sp[key] = {k: v for k, v in cur_p.items() if v != st.defaults[k]}
    m.save()
    print("已儲存，主策略：" + strat.get(m.primary_strategy).name)


def _dummy_df(st):
    """產生足夠長的假資料，用來檢查參數組合是否合理。"""
    import pandas as pd
    n = 400
    c = pd.Series(range(1, n + 1), dtype=float)
    return pd.DataFrame({"Open": c, "High": c, "Low": c, "Close": c})


def loop(m: Monitor):
    print(f"開始自動看盤，每 {m.settings['interval']} 秒更新，按 Ctrl+C 回選單")
    try:
        while True:
            show_quotes(m, m.refresh())
            time.sleep(m.settings["interval"])
    except KeyboardInterrupt:
        print("\n停止自動看盤")


MENU = """
──────── 自動看盤 ────────
 1 立即更新報價      2 開始自動看盤
 3 新增自選股        4 刪除自選股       5 設定價格上下限
 6 帳戶總覽          7 買進             8 賣出
 9 交易紀錄         10 提醒紀錄        11 設定
12 策略設定
 0 離開
自選股：{watch}
策略：{strategies}
自動交易：{auto}   資料：{src}"""


def main(argv=None):
    ap = argparse.ArgumentParser(description="自動看盤系統")
    ap.add_argument("--demo", action="store_true", help="使用示範資料（不連網）")
    ap.add_argument("--source", choices=["auto", "twse", "yahoo"], default="auto",
                    help="資料來源：auto（台股用證交所，失敗改 Yahoo）/ twse / yahoo")
    ap.add_argument("--data", default=str(Path(__file__).parent / "data"), help="存檔資料夾")
    args = ap.parse_args(argv)

    m = Monitor(Store(args.data))
    m.settings["demo"] = args.demo  # 每次執行由參數決定，不沿用上次
    m.settings["source"] = args.source
    if not m.watchlist:
        for code in ("2330", "0050"):
            m.add(code)
        print("第一次使用，先幫你加入 2330、0050 當自選股")

    while True:
        names = [strat.get(k).name for k in m.settings["strategies"]]
        names[0] = f"★{names[0]}"
        print(MENU.format(watch=", ".join(m.watchlist) or "（空）", strategies="、".join(names),
                          auto="開" if m.settings["auto_trade"] else "關",
                          src="示範" if m.settings["demo"] else
                          {"auto": "證交所（台股）＋ Yahoo（其他）", "twse": "證交所",
                           "yahoo": "Yahoo Finance"}[m.settings["source"]]))
        try:
            choice = input("> ").strip()
            if choice == "1":
                show_quotes(m, m.refresh())
            elif choice == "2":
                loop(m)
            elif choice == "3":
                print("已加入", m.add(ask("代號（例 2330、AAPL、BTC、加權）: ")))
            elif choice == "4":
                m.remove(ask("要刪除的代號: "))
                print("已刪除")
            elif choice == "5":
                name = ask("代號: ")
                lo = ask("下限（空白 = 不設）: ", float, allow_empty=True)
                hi = ask("上限（空白 = 不設）: ", float, allow_empty=True)
                m.set_limits(name, lo, hi)
                print("已設定")
            elif choice == "6":
                show_account(m)
            elif choice == "7":
                name = ask("代號: ")
                shares = ask("股數（1 張 = 1000 股）: ", int)
                px = ask("價格（空白 = 用最新報價）: ", float, allow_empty=True)
                t = m.manual_buy(name, shares, px)
                print(f"成交：買進 {t['code']} {t['shares']} 股 @ {t['price']}，"
                      f"手續費 {t['fee']:,.0f}，共付 {-t['cash_flow']:,.0f}")
            elif choice == "8":
                name = ask("代號: ")
                shares = ask("股數（空白 = 全部）: ", int, allow_empty=True)
                px = ask("價格（空白 = 用最新報價）: ", float, allow_empty=True)
                t = m.manual_sell(name, shares, px)
                print(f"成交：賣出 {t['code']} {t['shares']} 股 @ {t['price']}，"
                      f"手續費 {t['fee']:,.0f}、稅 {t['tax']:,.0f}，實收 {t['cash_flow']:,.0f}，"
                      f"損益 {t['pnl']:+,.0f}")
            elif choice == "9":
                show_trades(m)
            elif choice == "10":
                show_alerts(m)
            elif choice == "11":
                edit_settings(m)
            elif choice == "12":
                edit_strategies(m)
            elif choice == "0":
                m.save()
                print("已存檔，再見！")
                return
        except (TradeError, KeyError, ValueError) as e:
            print("⚠", e.args[0] if e.args else e)
        except (KeyboardInterrupt, EOFError):
            m.save()
            print("\n已存檔，再見！")
            return


if __name__ == "__main__":
    main()
