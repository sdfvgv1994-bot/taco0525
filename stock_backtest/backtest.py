"""均線交叉回測工具。

範例：
    python stock_backtest/backtest.py 2330
    python stock_backtest/backtest.py 2330 --short 10 --long 60 --period 5y
    python stock_backtest/backtest.py AAPL --stop-loss 8 --trailing 12
    python stock_backtest/backtest.py 2330 --demo          # 示範資料，不連網
不給代號會進入互動模式，一題一題問。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.market import fetch_history, parse_symbol  # noqa: E402
from stock_backtest.engine import EXIT_CROSS, EXIT_END, EXIT_FIXED, EXIT_TRAIL, run_backtest  # noqa: E402

OUT_DIR = Path(__file__).parent / "output"
EXIT_COLOR = {EXIT_CROSS: "red", EXIT_FIXED: "purple", EXIT_TRAIL: "cyan", EXIT_END: "gray"}


def print_report(code, res, args):
    s = res.stats
    d = res.data
    print(f"\n===== {code} 均線交叉回測 =====")
    print(f"期間：{d.index[0]:%Y-%m-%d} ~ {d.index[-1]:%Y-%m-%d}（{len(d)} 個交易日）")
    print(f"策略：MA{args.short} / MA{args.long}，本金 {args.capital:,.0f}")
    stops = []
    if args.stop_loss:
        stops.append(f"固定停損 {args.stop_loss}%")
    if args.trailing:
        stops.append(f"移動停損 {args.trailing}%")
    print("停損：" + ("、".join(stops) if stops else "無"))
    print()
    print(f"{'':12}{'均線策略':>12}{'買進持有':>12}")
    print(f"{'總報酬':12}{s['策略報酬%']:>+11.2f}%{s['買進持有報酬%']:>+11.2f}%")
    print(f"{'年化報酬':12}{s['策略年化%']:>+11.2f}%{s['買進持有年化%']:>+11.2f}%")
    print(f"{'最大回撤':12}{s['策略最大回撤%']:>11.2f}%{s['買進持有最大回撤%']:>11.2f}%")
    print(f"{'期末資產':12}{d['equity'].iloc[-1]:>12,.0f}{d['hold_equity'].iloc[-1]:>12,.0f}")
    print()
    print(f"交易次數 {s['交易次數']}，勝率 {s['勝率%']:.1f}%，手續費＋稅共 {s['總手續費+稅']:,.0f}")
    st = s["停損出場"]
    if args.stop_loss or args.trailing:
        print(f"停損出場：固定 {st[EXIT_FIXED]} 次、移動 {st[EXIT_TRAIL]} 次")
    winner = "均線策略" if s["策略報酬%"] > s["買進持有報酬%"] else "買進持有"
    print(f"👉 這段期間 {winner} 比較好")

    if res.trades:
        print(f"\n{'買進日':<12}{'買價':>10}{'賣出日':>12}{'賣價':>10}{'股數':>8}{'報酬%':>9}  出場原因")
        for t in res.trades:
            print(f"{t.entry_date:%Y-%m-%d}  {t.entry_price:>10.2f}  {t.exit_date:%Y-%m-%d}"
                  f"{t.exit_price:>10.2f}{t.shares:>8}{t.return_pct:>+9.2f}  {t.reason}")


def plot(code, res, args, path: Path):
    import logging

    import matplotlib
    matplotlib.use("Agg")
    logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    # 盡量找可以顯示中文的字型
    for name in ("Microsoft JhengHei", "PingFang TC", "Noto Sans CJK TC", "Noto Sans CJK JP",
                 "WenQuanYi Zen Hei", "Arial Unicode MS"):
        if any(f.name == name for f in font_manager.fontManager.ttflist):
            plt.rcParams["font.sans-serif"] = [name]
            break
    plt.rcParams["axes.unicode_minus"] = False

    d = res.data
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(13, 8), sharex=True,
                                   gridspec_kw={"height_ratios": [3, 2]})
    ax1.plot(d.index, d["Close"], color="black", lw=1, label="Close")
    ax1.plot(d.index, d["short"], color="orange", lw=1, label=f"MA{args.short}")
    ax1.plot(d.index, d["long"], color="royalblue", lw=1, label=f"MA{args.long}")
    ax1.scatter([t.entry_date for t in res.trades], [t.entry_price for t in res.trades],
                marker="^", color="green", s=70, zorder=5, label="Buy")
    labels = {EXIT_CROSS: "Sell (death cross)", EXIT_FIXED: "Sell (fixed stop)",
              EXIT_TRAIL: "Sell (trailing stop)", EXIT_END: "Open at end"}
    for reason, color in EXIT_COLOR.items():
        ts = [t for t in res.trades if t.reason == reason]
        if ts:
            ax1.scatter([t.exit_date for t in ts], [t.exit_price for t in ts], marker="v",
                        color=color, s=70, zorder=5, label=labels[reason])
    ax1.set_title(f"{code}  MA{args.short}/MA{args.long} crossover backtest")
    ax1.legend(loc="upper left", fontsize=8)
    ax1.grid(alpha=0.3)

    ax2.plot(d.index, d["equity"], color="tab:orange", label="Strategy")
    ax2.plot(d.index, d["hold_equity"], color="tab:blue", label="Buy & hold")
    ax2.axhline(res.capital, color="gray", ls="--", lw=0.8)
    ax2.set_ylabel("Equity")
    ax2.legend(loc="upper left", fontsize=8)
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=110)
    plt.close(fig)


def interactive(ap):
    print("=== 均線交叉回測（直接按 Enter 用預設值）===")
    sym = ""
    while not sym:
        sym = input("股票代號（例 2330、AAPL、BTC、加權）: ").strip()
    argv = [sym]
    for flag, q in (("--short", "短均線天數 [5]: "), ("--long", "長均線天數 [20]: "),
                    ("--period", "期間 1y/2y/5y/10y [2y]: "), ("--stop-loss", "固定停損 %（0 = 不用）[0]: "),
                    ("--trailing", "移動停損 %（0 = 不用）[0]: ")):
        v = input(q).strip()
        if v:
            argv += [flag, v]
    return ap.parse_args(argv)


def main(argv=None):
    ap = argparse.ArgumentParser(description="均線交叉策略回測")
    ap.add_argument("symbol", nargs="?", help="股票代號")
    ap.add_argument("--short", type=int, default=5, help="短均線天數（預設 5）")
    ap.add_argument("--long", type=int, default=20, help="長均線天數（預設 20）")
    ap.add_argument("--period", default="2y", help="資料期間，例 1y、2y、5y、10y（預設 2y）")
    ap.add_argument("--start", help="開始日期 YYYY-MM-DD（會蓋過 --period）")
    ap.add_argument("--end", help="結束日期 YYYY-MM-DD")
    ap.add_argument("--capital", type=float, default=1_000_000, help="本金（預設 100 萬）")
    ap.add_argument("--stop-loss", type=float, default=0, help="固定停損 %%，0 = 不用")
    ap.add_argument("--trailing", type=float, default=0, help="移動停損 %%，0 = 不用")
    ap.add_argument("--demo", action="store_true", help="示範資料，不連網")
    ap.add_argument("--no-plot", action="store_true", help="不產生圖表")
    args = ap.parse_args(argv)
    if not args.symbol:
        demo = args.demo
        args = interactive(ap)
        args.demo = args.demo or demo

    try:
        sym = parse_symbol(args.symbol)
        code, df = fetch_history(sym, period=args.period, start=args.start, end=args.end,
                                 demo=args.demo)
        res = run_backtest(df, args.short, args.long, args.capital,
                           args.stop_loss, args.trailing, sym.is_tw_stock)
    except (ValueError, RuntimeError) as e:
        print("⚠", e)
        if "抓不到" in str(e):
            print("  可以加上 --demo 先用示範資料試試看")
        return 1

    print_report(code, res, args)
    if not args.no_plot:
        path = OUT_DIR / f"{code.replace('^', '')}_MA{args.short}_{args.long}.png"
        plot(code, res, args, path)
        print(f"\n📈 圖表已存到 {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
