import numpy as np
import pandas as pd
import pytest

from common import indicators as ind
from common import strategies as strat
from common.market import demo_history
from stock_backtest import backtest as cli
from stock_backtest.engine import compare, run_backtest


def ohlc(closes, spread=0.0):
    c = pd.Series(closes, dtype=float, index=pd.bdate_range("2024-01-01", periods=len(closes)))
    return pd.DataFrame({"Open": c, "High": c + spread, "Low": c - spread, "Close": c})


# ---------- 指標 ----------
def test_macd_matches_manual_ema():
    c = pd.Series(np.linspace(10, 30, 60))
    dif, dea, hist = ind.macd(c, 12, 26, 9)
    e12 = c.ewm(span=12, adjust=False).mean()
    e26 = c.ewm(span=26, adjust=False).mean()
    assert np.allclose(dif, e12 - e26)
    assert np.allclose(hist, dif - dea)


def test_bollinger_band_shape():
    c = pd.Series([10, 12] * 20, dtype=float)
    up, mid, lo = ind.bollinger(c, 20, 2)
    assert mid.iloc[-1] == pytest.approx(11)
    assert up.iloc[-1] - mid.iloc[-1] == pytest.approx(2)  # 母體標準差 1 × 2
    assert mid.iloc[-1] - lo.iloc[-1] == pytest.approx(2)


def test_kd_range_and_direction():
    up = ohlc(list(range(10, 60)), spread=0.5)
    k, d = ind.kd(up["High"], up["Low"], up["Close"], 9)
    assert ((k >= 0) & (k <= 100)).all() and ((d >= 0) & (d <= 100)).all()
    assert k.iloc[-1] > 80 and d.iloc[-1] > 80
    down = ohlc(list(range(60, 10, -1)), spread=0.5)
    k, d = ind.kd(down["High"], down["Low"], down["Close"], 9)
    assert k.iloc[-1] < 20


def test_kd_first_value_follows_taiwan_formula():
    df = ohlc([10, 11, 12], spread=0)
    k, d = ind.kd(df["High"], df["Low"], df["Close"], 3)
    # 第 3 天 RSV = 100，K = 50×2/3 + 100/3，D = 50×2/3 + K/3
    assert k.iloc[2] == pytest.approx(200 / 3)
    assert d.iloc[2] == pytest.approx(100 / 3 + 200 / 9)


# ---------- 策略 ----------
def test_registry_and_lookup():
    assert set(strat.STRATEGIES) == {"ma", "macd", "rsi", "kd", "bbands", "breakout"}
    assert strat.get("MACD").key == "macd"
    with pytest.raises(ValueError):
        strat.get("nope")


def test_parse_params():
    assert strat.parse_params(["short=10", "long = 60"]) == {"short": 10, "long": 60}
    with pytest.raises(ValueError):
        strat.parse_params(["short"])
    with pytest.raises(ValueError):
        strat.parse_params(["short=abc"])


def test_unknown_param_rejected():
    with pytest.raises(ValueError, match="沒有參數"):
        strat.get("rsi").run(ohlc([10] * 50), short=3)


def test_not_enough_data():
    with pytest.raises(ValueError, match="至少需要"):
        strat.get("macd").run(ohlc([10] * 20))


@pytest.mark.parametrize("key,params", [
    ("ma", {"short": 20, "long": 5}),
    ("macd", {"fast": 30, "slow": 26}),
    ("rsi", {"low": 80, "high": 70}),
    ("kd", {"low": 80, "high": 20}),
])
def test_bad_param_combos(key, params):
    with pytest.raises(ValueError):
        strat.get(key).run(ohlc(list(range(1, 100))), **params)


def test_rsi_buys_on_recovery_and_sells_when_overbought():
    closes = [100 - i * 2 for i in range(20)] + [62 + i * 3 for i in range(25)]
    out = strat.get("rsi").run(ohlc(closes))
    buys = list(out.signal[out.signal == 1].index)
    sells = list(out.signal[out.signal == -1].index)
    assert buys and sells and buys[0] < sells[0]


def test_breakout_entry_and_exit():
    closes = [10] * 25 + [11] + [11] * 5 + [9]
    out = strat.get("breakout").run(ohlc(closes, spread=0.1), entry=20, exit=10)
    assert out.signal.iloc[25] == 1          # 創 20 日新高
    assert out.signal.iloc[-1] == -1         # 跌破 10 日新低
    assert (out.signal.iloc[:25] == 0).all()


def test_bbands_signals():
    rng = np.random.default_rng(0)
    base = 100 + rng.normal(0, 0.5, 40)
    closes = list(base) + [90, 101] + list(100 + rng.normal(0, 0.5, 10)) + [115]
    out = strat.get("bbands").run(ohlc(closes))
    assert out.signal.iloc[41] == 1   # 跌破下軌後收回
    assert out.signal.iloc[-1] == -1  # 突破上軌


def test_kd_signals_only_in_zones():
    df = demo_history("2330.TW", 600)
    out = strat.get("kd").run(df)
    k, d = out.panel["K"], out.panel["D"]
    assert (d[out.signal == 1] < 30).all()
    assert (d[out.signal == -1] > 70).all()


@pytest.mark.parametrize("key", list(strat.STRATEGIES))
def test_every_strategy_backtests_on_demo(key):
    df = demo_history("2454.TW", 750)
    res = run_backtest(df, key, stop_loss_pct=8)
    assert (res.data["equity"] > 0).all()
    assert res.stats["交易次數"] >= 1
    for t in res.trades:
        assert t.entry_date <= t.exit_date


def test_compare_sorted():
    results = compare(demo_history("2330.TW", 500))
    rets = [r.stats["策略報酬%"] for r in results]
    assert rets == sorted(rets, reverse=True) and len(results) == 6


def test_cli_strategy_with_params(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "OUT_DIR", tmp_path)
    assert cli.main(["2330", "--demo", "-s", "rsi", "-p", "low=25", "-p", "high=75"]) == 0
    out = capsys.readouterr().out
    assert "RSI" in out and "low=25" in out
    assert list(tmp_path.glob("*rsi*.png"))


def test_cli_compare(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "OUT_DIR", tmp_path)
    assert cli.main(["2330", "--demo", "--compare"]) == 0
    out = capsys.readouterr().out
    assert "策略比較" in out and "買進持有" in out
    assert (tmp_path / "2330.TW_compare.png").exists()


def test_cli_bad_strategy(capsys):
    assert cli.main(["2330", "--demo", "-s", "xyz", "--no-plot"]) == 1
    assert "沒有「xyz」" in capsys.readouterr().out


def test_cli_list(capsys):
    assert cli.main(["--list"]) == 0
    assert "bbands" in capsys.readouterr().out
