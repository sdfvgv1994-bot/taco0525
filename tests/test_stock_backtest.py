import pandas as pd
import pytest

from common import costs
from common.market import demo_history
from stock_backtest import backtest as cli
from stock_backtest.engine import EXIT_CROSS, EXIT_END, EXIT_FIXED, EXIT_TRAIL, run_backtest


def frame(closes):
    idx = pd.bdate_range("2024-01-01", periods=len(closes))
    return pd.DataFrame({"Close": closes}, index=idx, dtype=float)


# 下跌→上漲（黃金交叉）→下跌（死亡交叉）
UP_DOWN = [10, 9, 8, 7, 6, 7, 8, 9, 10, 11, 12, 11, 10, 9, 8, 7, 6]


def test_single_round_trip_costs():
    res = run_backtest(frame(UP_DOWN), 2, 4, capital=100_000)
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.reason == EXIT_CROSS
    assert t.entry_date < t.exit_date
    assert t.cost == pytest.approx(costs.buy_cost(t.entry_price, t.shares))
    assert t.proceeds == pytest.approx(costs.sell_proceeds(t.exit_price, t.shares))
    # 最後一天權益 = 現金（已出場）
    assert res.data["equity"].iloc[-1] == pytest.approx(100_000 - t.cost + t.proceeds)


def test_no_tax_for_non_tw():
    a = run_backtest(frame(UP_DOWN), 2, 4, capital=100_000, is_tw_stock=True)
    b = run_backtest(frame(UP_DOWN), 2, 4, capital=100_000, is_tw_stock=False)
    assert b.data["equity"].iloc[-1] > a.data["equity"].iloc[-1]


def test_fixed_stop_loss():
    # 在 8 黃金交叉買進，隔天跌到 7.5（-6.25%），均線還沒死亡交叉
    closes = [10, 9, 8, 7, 6, 7, 8, 7.5, 7.6, 8, 9, 10]
    res = run_backtest(frame(closes), 2, 4, capital=100_000, stop_loss_pct=5)
    assert res.trades[0].reason == EXIT_FIXED
    assert res.stats["停損出場"][EXIT_FIXED] == 1


def test_trailing_stop():
    # 買進後漲到 20 再回落到 18（回落 10%），但仍高於買價
    closes = [10, 9, 8, 7, 6, 7, 8, 10, 14, 18, 20, 18, 17]
    res = run_backtest(frame(closes), 2, 4, capital=100_000, trailing_pct=8)
    t = res.trades[0]
    assert t.reason == EXIT_TRAIL and t.exit_price == 18 and t.pnl > 0


def test_stop_then_wait_for_next_cross():
    closes = [10, 9, 8, 7, 6, 7, 8, 7.5, 7.6, 8, 9, 10, 11, 12]
    res = run_backtest(frame(closes), 2, 4, capital=100_000, stop_loss_pct=5)
    # 停損後短均仍在長均之上，不應馬上又買
    assert len(res.trades) == 1


def test_open_position_marked_at_end():
    closes = [10, 9, 8, 7, 6, 7, 8, 9, 10, 11, 12]
    res = run_backtest(frame(closes), 2, 4, capital=100_000)
    assert res.trades[-1].reason == EXIT_END
    assert res.stats["交易次數"] == 1


def test_buy_and_hold_matches_manual():
    closes = [10, 11, 12, 13, 12, 11, 12, 13, 14]
    res = run_backtest(frame(closes), 2, 4, capital=100_000)
    n = costs.max_shares(100_000, 10)
    expect = 100_000 - costs.buy_cost(10, n) + costs.sell_proceeds(14, n)
    assert res.data["hold_equity"].iloc[-1] == pytest.approx(expect)


def test_no_signal_no_trades():
    res = run_backtest(frame(list(range(1, 40))), 5, 20)
    assert res.trades == [] and res.stats["策略報酬%"] == 0


@pytest.mark.parametrize("short,long,n", [(20, 5, 100), (5, 20, 10)])
def test_bad_inputs(short, long, n):
    with pytest.raises(ValueError):
        run_backtest(frame([10] * n), short, long)


def test_equity_never_negative_on_demo_data():
    df = demo_history("2330.TW", 1000)
    res = run_backtest(df, 5, 20, stop_loss_pct=7, trailing_pct=10)
    assert (res.data["equity"] > 0).all()
    assert res.stats["交易次數"] > 5


def test_cli_demo_with_plot(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "OUT_DIR", tmp_path)
    assert cli.main(["2330", "--demo", "--stop-loss", "8", "--trailing", "12"]) == 0
    out = capsys.readouterr().out
    assert "均線策略" in out and "買進持有" in out
    assert list(tmp_path.glob("*.png"))
