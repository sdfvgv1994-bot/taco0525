"""策略回測工具。

範例：
    python stock_backtest/backtest.py 2330                         # 均線交叉 MA5/MA20
    python stock_backtest/backtest.py 2330 -s macd                 # 換成 MACD 策略
    python stock_backtest/backtest.py 2330 -s rsi -p low=25 -p high=75
    python stock_backtest/backtest.py 2330 --short 10 --long 60 --period 5y
    python stock_backtest/backtest.py AAPL -s kd --stop-loss 8 --trailing 12
    python stock_backtest/backtest.py 2330 --compare               # 所有策略一起比
    python stock_backtest/backtest.py --list                       # 列出所有策略
    python stock_backtest/backtest.py 2330 --demo                  # 示範資料，不連網
不給代號會進入互動模式，一題一題問。
"""
from __future__ import annotations

import argparse
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common import strategies as strat  # noqa: E402
from common.market import fetch_history, parse_symbol  # noqa: E402
from stock_backtest.engine import EXIT_END, EXIT_FIXED, EXIT_TRAIL, compare, run_backtest  # noqa: E402

OUT_DIR = Path(__file__).parent / "output"
STOP_COLOR = {EXIT_FIXED: "purple", EXIT_TRAIL: "cyan", EXIT_END: "gray"}
STOP_LABEL = {EXIT_FIXED: "Sell (fixed stop)", EXIT_TRAIL: "Sell (trailing stop)",
              EXIT_END: "Open at end"}


def pad(text: str, width: int) -> str:
    """依「顯示寬度」補空白（中文字佔兩格），讓表格對齊。"""
    w = sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)
    return text + " " * max(0, width - w)


def list_strategies():
    print("可用策略（-s 代號，-p 參數=值 可調整）：\n")
    for s in strat.STRATEGIES.values():
        params = "  ".join(f"{k}={v:g}" for k, v in s.defaults.items())
        print(f"  {s.key:<9}{pad(s.name, 14)}{s.description}")
        print(f"  {'':<9}預設參數：{params}\n")


def stop_text(args):
    stops = []
    if args.stop_loss:
        stops.append(f"固定停損 {args.stop_loss:g}%")
    if args.trailing:
        stops.append(f"移動停損 {args.trailing:g}%")
    return "、".join(stops) if stops else "無"


def print_report(code, res, args):
    s, d = res.stats, res.data
    print(f"\n===== {code} 回測：{res.strategy.name} =====")
    print(f"期間：{d.index[0]:%Y-%m-%d} ~ {d.index[-1]:%Y-%m-%d}（{len(d)} 個交易日）")
    print(f"策略：{res.label}，本金 {args.capital:,.0f}")
    print(f"規則：{res.output.buy_reason} → 買進；{res.output.sell_reason} → 賣出")
    print(f"停損：{stop_text(args)}")
    print()
    print(f"{'':12}{'策略':>12}{'買進持有':>12}")
    print(f"{'總報酬':12}{s['策略報酬%']:>+11.2f}%{s['買進持有報酬%']:>+11.2f}%")
    print(f"{'年化報酬':12}{s['策略年化%']:>+11.2f}%{s['買進持有年化%']:>+11.2f}%")
    print(f"{'最大回撤':12}{s['策略最大回撤%']:>11.2f}%{s['買進持有最大回撤%']:>11.2f}%")
    print(f"{'期末資產':12}{d['equity'].iloc[-1]:>12,.0f}{d['hold_equity'].iloc[-1]:>12,.0f}")
    print()
    print(f"交易次數 {s['交易次數']}，勝率 {s['勝率%']:.1f}%，持股時間 {s['持股時間%']:.0f}%，"
          f"手續費＋稅共 {s['總手續費+稅']:,.0f}")
    st = s["停損出場"]
    if args.stop_loss or args.trailing:
        print(f"停損出場：固定 {st[EXIT_FIXED]} 次、移動 {st[EXIT_TRAIL]} 次")
    winner = "這個策略" if s["策略報酬%"] > s["買進持有報酬%"] else "買進持有"
    print(f"👉 這段期間 {winner} 比較好")

    if res.trades:
        print(f"\n{'買進日':<12}{'買價':>10}{'賣出日':>12}{'賣價':>10}{'股數':>8}{'報酬%':>9}  出場原因")
        for t in res.trades:
            print(f"{t.entry_date:%Y-%m-%d}  {t.entry_price:>10.2f}  {t.exit_date:%Y-%m-%d}"
                  f"{t.exit_price:>10.2f}{t.shares:>8}{t.return_pct:>+9.2f}  {t.reason}")
    else:
        print("\n這段期間沒有出現買進訊號")


def print_compare(code, results, args):
    d = results[0].data
    print(f"\n===== {code} 策略比較 =====")
    print(f"期間：{d.index[0]:%Y-%m-%d} ~ {d.index[-1]:%Y-%m-%d}（{len(d)} 個交易日），"
          f"本金 {args.capital:,.0f}，停損：{stop_text(args)}\n")
    print(f"{pad('名次', 6)}{pad('策略', 14)}{pad('總報酬', 10)}{pad('年化', 10)}"
          f"{pad('最大回撤', 10)}{pad('交易', 6)}{pad('勝率', 7)}持股時間")
    for i, r in enumerate(results, 1):
        s = r.stats
        print(f"{i:<6}{pad(r.strategy.name, 14)}{s['策略報酬%']:>+7.1f}%  {s['策略年化%']:>+7.1f}%  "
              f"{s['策略最大回撤%']:>7.1f}%  {s['交易次數']:>4}  {s['勝率%']:>4.0f}%  {s['持股時間%']:>5.0f}%")
    s = results[0].stats
    print(f"{'—':<6}{pad('買進持有', 14)}{s['買進持有報酬%']:>+7.1f}%  {s['買進持有年化%']:>+7.1f}%  "
          f"{s['買進持有最大回撤%']:>7.1f}%")
    best = results[0]
    print(f"\n👉 報酬最高：{best.strategy.name}（-s {best.strategy.key}）")
    calm = max(results, key=lambda r: r.stats["策略最大回撤%"])
    if calm is not best:
        print(f"   回撤最小：{calm.strategy.name}（-s {calm.strategy.key}）")
    print("⚠ 過去績效不代表未來，參數也可能只是剛好適合這段行情")


def _setup_matplotlib():
    import logging

    import matplotlib
    matplotlib.use("Agg")
    logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
    import matplotlib.pyplot as plt
    plt.rcParams["axes.unicode_minus"] = False
    return plt


def plot(code, res, path: Path):
    plt = _setup_matplotlib()
    d, out = res.data, res.output
    has_panel = bool(out.panel)
    ratios = [3, 1.4, 2] if has_panel else [3, 2]
    fig, axes = plt.subplots(len(ratios), 1, figsize=(13, 9 if has_panel else 8), sharex=True,
                             gridspec_kw={"height_ratios": ratios})
    ax1, ax_eq = axes[0], axes[-1]

    ax1.plot(d.index, d["Close"], color="black", lw=1, label="Close")
    for (name, line), color in zip(out.overlays.items(), ["orange", "royalblue", "seagreen"]):
        ax1.plot(d.index, line, color=color, lw=1, label=name)
    ax1.scatter([t.entry_date for t in res.trades], [t.entry_price for t in res.trades],
                marker="^", color="green", s=70, zorder=5, label="Buy")
    sig_exits = [t for t in res.trades if t.reason not in STOP_COLOR]
    if sig_exits:
        ax1.scatter([t.exit_date for t in sig_exits], [t.exit_price for t in sig_exits],
                    marker="v", color="red", s=70, zorder=5, label="Sell (signal)")
    for reason, color in STOP_COLOR.items():
        ts = [t for t in res.trades if t.reason == reason]
        if ts:
            ax1.scatter([t.exit_date for t in ts], [t.exit_price for t in ts], marker="v",
                        color=color, s=70, zorder=5, label=STOP_LABEL[reason])
    p = ", ".join(f"{k}={v:g}" for k, v in {**res.strategy.defaults, **res.params}.items())
    ax1.set_title(f"{code}  strategy: {res.strategy.key} ({p})")
    ax1.legend(loc="upper left", fontsize=8)
    ax1.grid(alpha=0.3)

    if has_panel:
        ax2 = axes[1]
        for (name, line), color in zip(out.panel.items(), ["tab:orange", "tab:blue", "tab:green"]):
            ax2.plot(d.index, line, color=color, lw=1, label=name)
        for lv in out.levels:
            ax2.axhline(lv, color="gray", ls="--", lw=0.8)
        ax2.legend(loc="upper left", fontsize=8)
        ax2.grid(alpha=0.3)

    ax_eq.plot(d.index, d["equity"], color="tab:orange", label="Strategy")
    ax_eq.plot(d.index, d["hold_equity"], color="tab:blue", label="Buy & hold")
    ax_eq.axhline(res.capital, color="gray", ls="--", lw=0.8)
    ax_eq.set_ylabel("Equity")
    ax_eq.legend(loc="upper left", fontsize=8)
    ax_eq.grid(alpha=0.3)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=110)
    plt.close(fig)


def plot_compare(code, results, path: Path):
    plt = _setup_matplotlib()
    fig, ax = plt.subplots(figsize=(13, 6))
    d = results[0].data
    ax.plot(d.index, d["hold_equity"], color="black", lw=1.6, ls="--", label="Buy & hold")
    for r in results:
        ax.plot(r.data.index, r.data["equity"], lw=1.2,
                label=f"{r.strategy.key} ({r.stats['策略報酬%']:+.1f}%)")
    ax.axhline(results[0].capital, color="gray", ls=":", lw=0.8)
    ax.set_title(f"{code}  strategy comparison")
    ax.set_ylabel("Equity")
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=110)
    plt.close(fig)


def interactive(ap):
    print("=== 策略回測（直接按 Enter 用預設值）===")
    sym = ""
    while not sym:
        sym = input("股票代號（例 2330、AAPL、BTC、加權）: ").strip()
    argv = [sym]
    keys = list(strat.STRATEGIES)
    print("\n策略：")
    for i, s in enumerate(strat.STRATEGIES.values(), 1):
        print(f"  {i}. {s.name} — {s.description}")
    print(f"  {len(keys) + 1}. 全部比較")
    choice = input(f"選擇 1~{len(keys) + 1} [1]: ").strip()
    if choice == str(len(keys) + 1):
        argv.append("--compare")
    else:
        idx = int(choice) - 1 if choice.isdigit() and 1 <= int(choice) <= len(keys) else 0
        st = strat.STRATEGIES[keys[idx]]
        argv += ["-s", st.key]
        for k, v in st.defaults.items():
            val = input(f"  {k} [{v:g}]: ").strip()
            if val:
                argv += ["-p", f"{k}={val}"]
    for flag, q in (("--period", "期間 1y/2y/5y/10y [2y]: "),
                    ("--stop-loss", "固定停損 %（0 = 不用）[0]: "),
                    ("--trailing", "移動停損 %（0 = 不用）[0]: ")):
        v = input(q).strip()
        if v:
            argv += [flag, v]
    return ap.parse_args(argv)


def build_parser():
    ap = argparse.ArgumentParser(description="策略回測")
    ap.add_argument("symbol", nargs="?", help="股票代號")
    ap.add_argument("-s", "--strategy", default="ma", help="策略代號（預設 ma，--list 看全部）")
    ap.add_argument("-p", "--param", action="append", default=[], metavar="名稱=值",
                    help="調整策略參數，可重複，例 -p low=25 -p high=75")
    ap.add_argument("--short", type=int, help="均線策略的短均線天數（等於 -p short=N）")
    ap.add_argument("--long", type=int, help="均線策略的長均線天數（等於 -p long=N）")
    ap.add_argument("--compare", action="store_true", help="所有策略用預設參數一起比較")
    ap.add_argument("--list", action="store_true", help="列出所有策略")
    ap.add_argument("--period", default="2y", help="資料期間，例 1y、2y、5y、10y（預設 2y）")
    ap.add_argument("--start", help="開始日期 YYYY-MM-DD（會蓋過 --period）")
    ap.add_argument("--end", help="結束日期 YYYY-MM-DD")
    ap.add_argument("--capital", type=float, default=1_000_000, help="本金（預設 100 萬）")
    ap.add_argument("--stop-loss", type=float, default=0, help="固定停損 %%，0 = 不用")
    ap.add_argument("--trailing", type=float, default=0, help="移動停損 %%，0 = 不用")
    ap.add_argument("--source", choices=["auto", "twse", "yahoo"], default="auto",
                    help="資料來源：auto（台股用證交所，失敗改 Yahoo）/ twse / yahoo")
    ap.add_argument("--demo", action="store_true", help="示範資料，不連網")
    ap.add_argument("--no-plot", action="store_true", help="不產生圖表")
    return ap


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    if args.list:
        list_strategies()
        return 0
    if not args.symbol:
        demo = args.demo
        args = interactive(ap)
        args.demo = args.demo or demo

    try:
        sym = parse_symbol(args.symbol)
        code, df = fetch_history(sym, period=args.period, start=args.start, end=args.end,
                                 demo=args.demo, source=args.source)
        safe = code.replace("^", "")
        if args.compare:
            results = compare(df, args.capital, args.stop_loss, args.trailing, sym.is_tw_stock)
            if not results:
                raise ValueError("資料太少，沒有策略能跑")
        else:
            params = strat.parse_params(args.param)
            if args.short is not None:
                params["short"] = args.short
            if args.long is not None:
                params["long"] = args.long
            res = run_backtest(df, args.strategy, params, args.capital,
                               args.stop_loss, args.trailing, sym.is_tw_stock)
    except (ValueError, RuntimeError) as e:
        print("⚠", e)
        if "抓不到" in str(e):
            print("  可以加上 --demo 先用示範資料試試看")
        return 1

    if args.compare:
        print_compare(code, results, args)
        path = OUT_DIR / f"{safe}_compare.png"
        if not args.no_plot:
            plot_compare(code, results, path)
    else:
        print_report(code, res, args)
        tag = "_".join(f"{v:g}" for v in {**res.strategy.defaults, **res.params}.values())
        path = OUT_DIR / f"{safe}_{res.strategy.key}_{tag}.png"
        if not args.no_plot:
            plot(code, res, path)
    if not args.no_plot:
        print(f"\n📈 圖表已存到 {path}")
    return 0


if __name__ == "__main__":
    from common.updater import check_and_update
    check_and_update()
    sys.exit(main())
