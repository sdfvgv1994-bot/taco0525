from datetime import datetime, timedelta

import pandas as pd
import pytest

from common import costs
from common.indicators import cross
from common.market import parse_symbol
from stock_monitor.account import Account, TradeError
from stock_monitor.engine import Monitor
from stock_monitor.signals import evaluate, snapshot
from stock_monitor.storage import Store

NOW = datetime(2026, 10, 8, 10, 0)


# ---------- 代號 ----------
@pytest.mark.parametrize("raw,first,kind", [
    ("2330", "2330.TW", "tw_stock"),
    ("6488", "6488.TW", "tw_stock"),
    ("00878", "00878.TW", "tw_stock"),
    ("aapl", "AAPL", "us_stock"),
    ("加權", "^TWII", "index"),
    ("^gspc", "^GSPC", "index"),
    ("btc", "BTC-USD", "crypto"),
])
def test_parse_symbol(raw, first, kind):
    s = parse_symbol(raw)
    assert s.candidates[0] == first and s.kind == kind


def test_tw_symbol_tries_otc_second():
    assert parse_symbol("6488").candidates == ("6488.TW", "6488.TWO")


# ---------- 成本 ----------
def test_costs():
    assert costs.fee(10_000, costs.MIN_FEE) == 20          # 14.25 → 最低 20
    assert costs.fee(1_000_000, costs.MIN_FEE) == 1425
    assert costs.tax(1_000_000) == 3000
    assert costs.tax(1_000_000, is_tw_stock=False) == 0
    n = costs.max_shares(100_000, 100, costs.MIN_FEE)
    assert costs.buy_cost(100, n, costs.MIN_FEE) <= 100_000 < costs.buy_cost(100, n + 1, costs.MIN_FEE)


# ---------- 交叉 ----------
def test_cross_detection():
    s = pd.Series([1, 2, 3, 2.5, 1.0])
    l = pd.Series([2, 2, 2, 2, 2.0])
    assert list(cross(s, l)) == [0, 0, 1, 0, -1]


# ---------- 帳戶 ----------
def test_buy_sell_roundtrip():
    a = Account()
    a.buy("2330", 500, 1000)  # 成交 50 萬
    assert a.cash == pytest.approx(500_000 - 712.5)
    rec = a.sell("2330", 500)
    assert rec["fee"] == 712.5 and rec["tax"] == 1500
    assert rec["pnl"] == pytest.approx(-712.5 * 2 - 1500)
    assert not a.positions
    assert a.cash == pytest.approx(1_000_000 - 712.5 * 2 - 1500)


def test_buy_insufficient_cash():
    a = Account(cash=1000)
    with pytest.raises(TradeError):
        a.buy("2330", 1000, 1)
    assert a.cash == 1000 and not a.trades


def test_oversell_rejected():
    a = Account()
    a.buy("2330", 100, 10)
    with pytest.raises(TradeError):
        a.sell("2330", 100, 11)
    with pytest.raises(TradeError):
        a.sell("0050", 100)
    assert a.positions["2330"]["shares"] == 10


def test_avg_cost_and_us_no_tax():
    a = Account()
    a.buy("AAPL", 100, 10, tw=False)
    a.buy("AAPL", 200, 10, tw=False)
    assert a.positions["AAPL"]["avg_cost"] == pytest.approx((1000 + 20 + 2000 + 20) / 20)
    assert a.sell("AAPL", 150)["tax"] == 0


def test_account_value_and_return():
    a = Account()
    a.buy("2330", 100, 1000)
    prices = {"2330": 110}
    assert a.market_value(prices) == 110_000
    assert a.total_assets(prices) == pytest.approx(a.cash + 110_000)
    assert a.return_pct(prices) == pytest.approx((a.total_assets(prices) / 1e6 - 1) * 100)


# ---------- 訊號 ----------
def _closes(vals):
    return pd.Series(vals, dtype=float)


def test_snapshot_golden_cross_today():
    closes = _closes([10, 10, 10, 10, 9, 9, 9, 9, 9, 9, 20])
    snap = snapshot("X", closes, None, 2, 5)
    assert snap.cross == 1


def test_evaluate_limits_and_stop():
    closes = _closes([100] * 30)
    snap = snapshot("X", closes, 85, 5, 20)
    alerts = evaluate(snap, {"lower": 90, "upper": 120}, {"shares": 10, "avg_cost": 100}, 10)
    kinds = {a.kind for a in alerts}
    assert {"below", "stop"} <= kinds and "above" not in kinds
    # 虧 15%，停損設 20% 則不觸發
    assert "stop" not in {a.kind for a in evaluate(snap, {}, {"shares": 10, "avg_cost": 100}, 20)}


# ---------- 引擎 ----------
class FakeFeed:
    """可控制的行情：每次呼叫回傳指定的收盤序列與價格。"""
    def __init__(self):
        self.data = {}

    def __call__(self, name, demo, now):
        closes, price = self.data[name]
        return name, _closes(closes), price


def make_monitor(tmp_path, **settings):
    feed = FakeFeed()
    m = Monitor(Store(tmp_path), fetcher=feed)
    m.settings.update(short_ma=2, long_ma=5, stop_loss_pct=10, **settings)
    return m, feed


GOLDEN = [10, 10, 10, 10, 9, 9, 9, 9, 9, 9, 20]
DEATH = [10, 10, 10, 10, 11, 11, 11, 11, 11, 11, 2]


def test_alert_only_once_per_day(tmp_path):
    m, feed = make_monitor(tmp_path)
    m.add("2330")
    feed.data["2330"] = (GOLDEN, 20)
    assert [a.kind for a in m.refresh(NOW).alerts] == ["golden"]
    assert m.refresh(NOW + timedelta(minutes=5)).alerts == []
    # 隔天同樣狀況會再響
    assert [a.kind for a in m.refresh(NOW + timedelta(days=1)).alerts] == ["golden"]
    assert "黃金交叉" in (tmp_path / "monitor.log").read_text(encoding="utf-8")


def test_auto_trade_buy_then_sell(tmp_path):
    m, feed = make_monitor(tmp_path, auto_trade=True, auto_buy_amount=100_000)
    m.add("2330")
    feed.data["2330"] = (GOLDEN, 20)
    res = m.refresh(NOW)
    assert len(res.trades) == 1 and res.trades[0]["side"] == "買進"
    shares = m.account.positions["2330"]["shares"]
    assert shares == costs.max_shares(100_000, 20, costs.MIN_FEE)

    feed.data["2330"] = (DEATH, 2)
    res = m.refresh(NOW + timedelta(days=1))
    sells = [t for t in res.trades if t["side"] == "賣出"]
    assert len(sells) == 1 and sells[0]["shares"] == shares
    assert not m.account.holds("2330")


def test_auto_stop_loss(tmp_path):
    m, feed = make_monitor(tmp_path, auto_trade=True)
    m.add("2330")
    m.last_prices["2330"] = 100
    m.manual_buy("2330", 100)
    feed.data["2330"] = ([100] * 30, 85)
    res = m.refresh(NOW)
    assert any(t["reason"] == "自動：停損" for t in res.trades)
    assert not m.account.holds("2330")


def test_auto_buy_skipped_when_no_cash(tmp_path):
    m, feed = make_monitor(tmp_path, auto_trade=True)
    m.account.cash = 10
    m.add("2330")
    feed.data["2330"] = (GOLDEN, 20)
    res = m.refresh(NOW)
    assert not res.trades and any("現金不足" in n for n in res.notes)


def test_cannot_remove_held_stock(tmp_path):
    m, _ = make_monitor(tmp_path)
    m.add("2330")
    m.manual_buy("2330", 10, 100)
    with pytest.raises(TradeError):
        m.remove("2330")
    m.manual_sell("2330", None, 100)
    m.remove("2330")
    assert "2330" not in m.watchlist


def test_persistence(tmp_path):
    m, _ = make_monitor(tmp_path)
    m.add("2330")
    m.set_limits("2330", 500, 1200)
    m.manual_buy("2330", 10, 600)
    m2 = Monitor(Store(tmp_path))
    assert m2.watchlist["2330"] == {"lower": 500, "upper": 1200}
    assert m2.account.positions["2330"]["shares"] == 10
    assert m2.account.cash == pytest.approx(m.account.cash)


def test_one_bad_symbol_does_not_break_others(tmp_path):
    m, feed = make_monitor(tmp_path)
    m.add("2330")
    m.add("9999")
    feed.data["2330"] = ([100] * 30, 100)
    res = m.refresh(NOW)
    assert "2330" in res.snapshots and "9999" in res.errors


def test_demo_fetcher_runs(tmp_path):
    m = Monitor(Store(tmp_path))
    m.settings["demo"] = True
    m.add("2330")
    m.add("AAPL")
    res = m.refresh(NOW)
    assert not res.errors and set(res.snapshots) == {"2330", "AAPL"}
