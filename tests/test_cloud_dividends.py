"""模擬帳戶的配息配股（cloud_monitor/dividends.py）。"""
import json
from datetime import date, datetime

import pytest

from cloud_monitor import dividends as dv
from stock_monitor.account import Account
from stock_monitor.storage import Store
from tests.test_cloud_monitor import BASE, Feed, run_once, write_config


class FakeSource:
    def __init__(self, events):
        self._events = events
        self.notes = []
        self.us_checked = {}

    def events(self, codes, since, today):
        return [e for e in self._events if e["code"] in codes and since.isoformat() <= e["date"] <= today.isoformat()]


def account_with(*trades):
    """trades：(時間, 買/賣, 代號, 股數, 價格)"""
    a = Account()
    for when, side, code, shares, price in trades:
        t = datetime.fromisoformat(when)
        if side == "買":
            a.buy(code, price, shares, tw=code.isdigit(), when=t)
        else:
            a.sell(code, price, shares, when=t)
    return a


def test_entitlement_tw_cash_and_nhi():
    e = dv.entitlement("2882", 5000, 3.5, 0, tw=True)
    assert e["gross"] == 17500 and e["nhi"] == 0 and e["fee"] == 10 and e["net"] == 17490
    e = dv.entitlement("2882", 10000, 3.5, 0, tw=True)        # 35,000 ≥ 2 萬 → 扣 2.11%
    assert e["nhi"] == round(35000 * 0.0211) and e["net"] == 35000 - 738 - 10


def test_entitlement_stock_dividend_and_odd_shares():
    e = dv.entitlement("2801", 1500, 0.8, 25, tw=True)       # 1,500 股 × 25/1000 = 37.5 股
    assert e["stock_shares"] == 37 and e["odd_cash"] == 5.0
    assert e["net"] == 1500 * 0.8 + 5 - 10


def test_entitlement_us_withholding():
    e = dv.entitlement("AAPL", 500, 0.26, 0, tw=False)
    assert e["tax"] == 39.0 and e["fee"] == 0 and e["nhi"] == 0 and e["net"] == 91.0


def test_held_before_uses_previous_close():
    a = account_with(("2026-07-01 01:30:00", "買", "2882", 2000, 100),
                     ("2026-07-10 01:30:00", "賣", "2882", 2000, 100),    # 除息當天賣掉：還是有股利
                     ("2026-07-10 02:00:00", "買", "2890", 1000, 40))     # 除息當天才買：沒有
    assert dv.held_before(a, "2882", "2026-07-10") == 2000
    assert dv.held_before(a, "2890", "2026-07-10") == 0
    assert dv.held_before(a, "2882", "2026-07-11") == 0


def test_process_pending_then_paid(tmp_path):
    store = Store(tmp_path)
    a = account_with(("2026-07-01 01:30:00", "買", "2801", 2000, 25))
    cash0, cost0 = a.cash, a.positions["2801"]["avg_cost"] * 2000
    src = FakeSource([{"code": "2801", "date": "2026-08-05", "cash": 0.8, "stock": 25}])
    r = dv.process(a, store, {"2801": "彰銀"}, datetime(2026, 8, 5, 10, 0), source=src, log=lambda *_: None)
    assert a.positions["2801"]["shares"] == 2050                       # 每千股配 25 股
    assert a.positions["2801"]["avg_cost"] == pytest.approx(cost0 / 2050)
    assert r["receivable"] == 2000 * 0.8 - 10 and a.cash == cash0        # 現金還沒入帳
    assert any("除息" in m for m in r["messages"]) and any("配股" in m for m in r["messages"])
    # 同一天再跑一次：不會重複
    r = dv.process(a, store, {}, datetime(2026, 8, 5, 11, 0), source=src, log=lambda *_: None)
    assert a.positions["2801"]["shares"] == 2050 and len(r["pending"]) == 1 and not r["messages"]
    # 30 天後入帳
    r = dv.process(a, store, {}, datetime(2026, 9, 4, 10, 0), source=src, log=lambda *_: None)
    assert a.cash == pytest.approx(cash0 + 1590) and r["receivable"] == 0
    assert a.trades[-1]["side"] == "股利入帳" and a.trades[-1]["cash_flow"] == 1590


def test_incomplete_data_waits(tmp_path):
    store = Store(tmp_path)
    a = account_with(("2026-07-01 01:30:00", "買", "2890", 1000, 40))
    src = FakeSource([{"code": "2890", "date": "2026-07-23", "cash": None, "stock": None}])
    dv.process(a, store, {}, datetime(2026, 7, 23, 10, 0), source=src, log=lambda *_: None)
    assert json.loads((tmp_path / "dividends.json").read_text(encoding="utf-8"))["done"] == []
    src._events[0].update(cash=1.1, stock=20)                           # 明細抓到了
    r = dv.process(a, store, {}, datetime(2026, 7, 24, 10, 0), source=src, log=lambda *_: None)
    assert a.positions["2890"]["shares"] == 1020 and r["receivable"] == 1100 - 10


def test_tw_cache_source_reads_exright(tmp_path):
    from tw_market.store import Store as TwStore
    st = TwStore(tmp_path)
    st.save_exright("tse", 2026, date(2026, 8, 31), "2026-08-31T20:00:00", [
        {"code": "2882", "date": "2026-06-30", "kind": "息", "cash": 3.5, "stock": 0.0},
        {"code": "2801", "date": "2026-08-05", "kind": "權息", "cash": None, "stock": None},
        {"code": "2330", "date": "2026-06-12", "kind": "息", "cash": 5.0, "stock": 0.0}])
    st.save_exright_details({"2801/2026-08-05": {"cash": 0.8, "stock": 25.0}})
    src = dv.Source(tw_cache=tmp_path, us_fetch=lambda c: [])
    ev = src.events(["2882", "2801"], date(2026, 1, 1), date(2026, 10, 21))
    assert sorted((e["code"], e["cash"], e["stock"]) for e in ev) == [("2801", 0.8, 25.0), ("2882", 3.5, 0.0)]


def test_us_source_checked_once_a_day():
    calls = []
    src = dv.Source(tw_cache="/nonexistent", us_fetch=lambda c: calls.append(c) or [("2026-08-11", 0.26)])
    ev = src.events(["AAPL", "BTC", "2330"], date(2026, 8, 1), date(2026, 8, 11))
    assert ev == [{"code": "AAPL", "date": "2026-08-11", "cash": 0.26, "stock": 0.0}]
    assert src.events(["AAPL"], date(2026, 8, 1), date(2026, 8, 11)) == [] and calls == ["AAPL"]


def test_run_shows_receivable_on_page(tmp_path):
    cfg = write_config(tmp_path / "c.toml", BASE)
    feed = Feed()
    feed.data = {"2330": [10, 10, 10, 10, 9, 9, 9, 9, 9, 9, 20]}          # 黃金交叉 → 自動買進
    run_once(tmp_path, cfg, feed, datetime(2026, 10, 8, 10, 0))
    src = FakeSource([{"code": "2330", "date": "2026-10-09", "cash": 5.0, "stock": 0.0}])
    data = run_once(tmp_path, cfg, feed, datetime(2026, 10, 9, 10, 0), dividend_source=src)
    shares = data["positions"][0]["shares"]
    want = dv.entitlement("2330", shares, 5.0, 0, tw=True)
    assert want["nhi"] > 0                                                 # 超過 2 萬：有扣健保補充保費
    assert data["dividends"][0]["code"] == "2330" and data["account"]["receivable"] == want["net"]
    assert data["account"]["total"] == pytest.approx(data["account"]["cash"] + data["account"]["market_value"]
                                                     + data["account"]["receivable"], abs=0.01)
