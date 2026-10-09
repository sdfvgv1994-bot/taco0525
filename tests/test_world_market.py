import json
import threading
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from common import twse
from world_market import app
from world_market.data import DataService, yahoo_code
from world_market.markets import ALL_ITEMS, GROUPS, market_status

TPE, NY, UTC = ZoneInfo("Asia/Taipei"), ZoneInfo("America/New_York"), ZoneInfo("UTC")


# ---------- 交易時間 ----------
@pytest.mark.parametrize("ex,when,expect", [
    ("TW", datetime(2026, 10, 8, 10, 0, tzinfo=TPE), "open"),      # 週四盤中
    ("TW", datetime(2026, 10, 8, 13, 31, tzinfo=TPE), "closed"),   # 收盤後
    ("TW", datetime(2026, 10, 10, 10, 0, tzinfo=TPE), "closed"),   # 週六
    ("US", datetime(2026, 10, 8, 9, 30, tzinfo=NY), "open"),
    ("US", datetime(2026, 10, 8, 9, 29, tzinfo=NY), "closed"),
    ("JP", datetime(2026, 10, 8, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo")), "closed"),  # 午休
    ("JP", datetime(2026, 10, 8, 13, 0, tzinfo=ZoneInfo("Asia/Tokyo")), "open"),
    ("CRYPTO", datetime(2026, 10, 10, 3, 0, tzinfo=UTC), "open"),  # 週六也開
    ("FX", datetime(2026, 10, 10, 12, 0, tzinfo=NY), "closed"),    # 週六
    ("FX", datetime(2026, 10, 11, 18, 0, tzinfo=NY), "open"),      # 週日晚上開盤
    ("FX", datetime(2026, 10, 9, 17, 0, tzinfo=NY), "closed"),     # 週五收盤
])
def test_market_status(ex, when, expect):
    assert market_status(ex, when) == expect


def test_catalog_consistent():
    syms = [it.symbol for _, items in GROUPS for it in items]
    assert len(syms) == len(set(syms)) == len(ALL_ITEMS)
    assert yahoo_code(ALL_ITEMS["2330"]) == "2330.TW"
    assert yahoo_code(ALL_ITEMS["^TWII"]) == "^TWII"


# ---------- 資料 ----------
def fake_df(n=40, start=100.0, step=1.0):
    idx = pd.bdate_range(end="2026-10-08", periods=n)
    c = [start + i * step for i in range(n)]
    return pd.DataFrame({"Open": c, "High": c, "Low": c, "Close": c, "Volume": 0}, index=idx)


class FakeYahoo:
    def __init__(self, missing=()):
        self.calls, self.missing = [], set(missing)

    def __call__(self, symbols, period):
        self.calls.append((list(symbols), period))
        return {s: fake_df() for s in symbols if s not in self.missing}


class FakeTwse:
    def __init__(self, fail=False):
        self.fail = fail

    def realtime(self, items):
        if self.fail:
            raise twse.TwseError("blocked")
        t = datetime(2026, 10, 9, 10, 0)
        return {i: twse.Quote(i, f"名稱{i}", "tse", 200.0, 190.0, 195, 201, 189, 1000, t) for i in items}

    def index_history(self, s, e):
        return fake_df()

    def history(self, code, s, e, market="auto"):
        return "tse", fake_df()


def test_overview_mixes_sources():
    yh = FakeYahoo(missing={"SOL-USD"})
    ov = DataService(yahoo_download=yh, twse_client=FakeTwse()).overview()
    items = {i["symbol"]: i for g in ov["groups"] for i in g["items"]}
    tw = items["2330"]
    assert tw["source"] == "證交所即時" and tw["price"] == 200 and tw["change"] == 10
    assert tw["spark"][-1] == 200          # 盤中價接在走勢圖最後
    assert len(tw["spark"]) == 30
    assert items["^GSPC"]["source"] == "Yahoo Finance"
    assert items["^GSPC"]["change_pct"] == pytest.approx((139 / 138 - 1) * 100)
    assert "error" in items["SOL-USD"]
    # 台灣項目不會送去 Yahoo
    assert all("2330.TW" not in syms for syms, _ in yh.calls)
    assert {c["key"] for c in ov["clocks"]} == {"TW", "JP", "UK", "EU", "US"}


def test_overview_twse_failure_falls_back_to_yahoo(capsys):
    yh = FakeYahoo()
    ov = DataService(yahoo_download=yh, twse_client=FakeTwse(fail=True)).overview()
    items = {i["symbol"]: i for g in ov["groups"] for i in g["items"]}
    assert items["2330"]["source"] == "Yahoo Finance"
    assert "2330.TW" in yh.calls[0][0]
    assert "改用 Yahoo" in capsys.readouterr().err


def test_overview_yahoo_failure_shows_errors():
    def boom(symbols, period):
        raise ConnectionError("network down")
    ov = DataService(yahoo_download=boom, twse_client=FakeTwse()).overview()
    items = {i["symbol"]: i for g in ov["groups"] for i in g["items"]}
    assert "network down" in items["^GSPC"]["error"]
    assert items["2330"]["price"] == 200  # 台灣項目不受影響


def test_overview_cached():
    yh = FakeYahoo()
    svc = DataService(yahoo_download=yh, twse_client=FakeTwse())
    svc.overview()
    n = len(yh.calls)
    svc.overview()
    assert len(yh.calls) == n
    svc.overview(force=True)
    assert len(yh.calls) > n


def test_history_and_validation():
    svc = DataService(yahoo_download=FakeYahoo(), twse_client=FakeTwse())
    h = svc.history("^GSPC", "3mo")
    assert h["close"][-1] == 139 and h["change_pct"] == pytest.approx((139 / 100 - 1) * 100)
    assert len(h["dates"]) == len(h["close"])
    with pytest.raises(KeyError):
        svc.history("XYZ")
    with pytest.raises(ValueError):
        svc.history("^GSPC", "7y")
    with pytest.raises(RuntimeError, match="櫃買指數"):
        svc.history("^TWOII")


def test_demo_overview_and_history_consistent():
    svc = DataService(demo=True)
    ov = svc.overview()
    assert ov["demo"] and all("price" in i for g in ov["groups"] for i in g["items"])
    gspc = next(i for g in ov["groups"] for i in g["items"] if i["symbol"] == "^GSPC")
    h = svc.history("^GSPC", "1y")
    # 總覽有模擬盤中跳動（±1%），其餘應來自同一條走勢
    assert h["close"][-1] == pytest.approx(gspc["price"], rel=0.011)
    assert h["close"][-2] == pytest.approx(gspc["prev_close"])


# ---------- 網頁伺服器 ----------
@pytest.fixture()
def server():
    svc = DataService(demo=True)
    srv = app.ThreadingHTTPServer(("127.0.0.1", 0), app.make_handler(svc))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def get(url):
    try:
        with urllib.request.urlopen(url) as r:
            return r.status, r.read().decode("utf-8"), r.headers.get("Content-Type")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8"), e.headers.get("Content-Type")


def test_server_routes(server):
    code, body, ctype = get(server + "/")
    assert code == 200 and "全球股市看板" in body and "text/html" in ctype
    code, body, _ = get(server + "/api/overview")
    assert code == 200 and json.loads(body)["groups"]
    code, body, _ = get(server + "/api/history?symbol=%5EN225&period=6mo")
    assert code == 200 and json.loads(body)["name"] == "日經 225"
    code, body, _ = get(server + "/api/history?symbol=NOPE")
    assert code == 400 and "不支援" in json.loads(body)["error"]
    code, _, _ = get(server + "/nothing")
    assert code == 404


def test_text_mode(capsys):
    app.main(["--demo", "--text"])
    out = capsys.readouterr().out
    assert "全球股市總覽" in out and "【亞太】" in out and "日經 225" in out
