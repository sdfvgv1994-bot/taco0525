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
RSS = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>t</title>
<item><title>台積電法說會優於預期 外資連買 - 經濟日報</title><link>https://news.google.com/a1</link>
<pubDate>Thu, 08 Oct 2026 03:10:00 GMT</pubDate><source url="https://money.udn.com">經濟日報</source></item>
<item><title>台股早盤上漲百點 - 鉅亨網</title><link>https://news.google.com/a2</link>
<pubDate>Thu, 08 Oct 2026 05:00:00 GMT</pubDate><source url="https://news.cnyes.com">鉅亨網</source></item>
<item><title>台股早盤上漲百點 - 中央社</title><link>https://news.google.com/a3</link>
<pubDate>Thu, 08 Oct 2026 04:00:00 GMT</pubDate><source url="https://www.cna.com.tw">中央社</source></item>
<item><title>沒有連結</title><link>javascript:alert(1)</link></item>
</channel></rss>""".encode("utf-8")


def fake_news_get(url):
    return RSS


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
    def __init__(self, fail=False, realtime_fail=False):
        self.fail = fail                      # 證交所整個連不上
        self.realtime_fail = realtime_fail or fail

    def realtime(self, items):
        if self.realtime_fail:
            raise twse.TwseError("blocked")
        t = datetime(2026, 10, 9, 10, 0)
        return {i: twse.Quote(i, f"名稱{i}", "tse", 200.0, 190.0, 195, 201, 189, 1000, t) for i in items}

    def index_history(self, s, e):
        if self.fail:
            raise twse.TwseError("blocked")
        return fake_df()

    def history(self, code, s, e, market="auto"):
        if self.fail:
            raise twse.TwseError("blocked")
        return "tse", fake_df()


def test_overview_mixes_sources():
    yh = FakeYahoo(missing={"SOL-USD"})
    ov = DataService(yahoo_download=yh, twse_client=FakeTwse(), news_get=fake_news_get).overview()
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


def test_overview_realtime_failure_uses_twse_close(capsys, monkeypatch):
    """只有即時報價抓不到（收盤後）：台灣項目照樣用證交所的歷史收盤價，不改用 Yahoo。"""
    from world_market import data as wm_data
    monkeypatch.setattr(wm_data, "market_status", lambda key, now=None: "closed")
    yh = FakeYahoo()
    ov = DataService(yahoo_download=yh, twse_client=FakeTwse(realtime_fail=True)).overview()
    items = {i["symbol"]: i for g in ov["groups"] for i in g["items"]}
    assert items["2330"]["source"] == "證交所（收盤價）" and items["2330"]["price"] == 139
    assert items["^TWII"]["source"] == "證交所（收盤價）"
    assert all("2330.TW" not in syms for syms, _ in yh.calls)
    assert "證交所" in items["^TWOII"]["error"]          # 櫃買指數只能靠即時報價
    assert "即時報價抓不到" in capsys.readouterr().err


def test_overview_twse_failure_falls_back_to_yahoo(capsys):
    yh = FakeYahoo()
    ov = DataService(yahoo_download=yh, twse_client=FakeTwse(fail=True)).overview()
    items = {i["symbol"]: i for g in ov["groups"] for i in g["items"]}
    assert items["2330"]["source"] == "Yahoo Finance"
    assert "2330.TW" in yh.calls[0][0]
    assert "^TWOII" not in yh.calls[0][0] and "證交所" in items["^TWOII"]["error"]
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
    svc = DataService(yahoo_download=yh, twse_client=FakeTwse(), news_get=fake_news_get)
    svc.overview()
    n = len(yh.calls)
    svc.overview()
    assert len(yh.calls) == n
    svc.overview(force=True)
    assert len(yh.calls) > n


def test_history_and_validation():
    svc = DataService(yahoo_download=FakeYahoo(), twse_client=FakeTwse(), news_get=fake_news_get)
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


def test_overview_realtime_failure_during_session_prefers_yahoo(monkeypatch):
    """盤中即時報價抓不到：證交所日 K 只到昨天，改用 Yahoo（有今天的延遲報價）。"""
    from world_market import data as wm_data
    tpe = ZoneInfo("Asia/Taipei")
    monkeypatch.setattr(wm_data, "market_status", lambda key, now=None: "open")
    monkeypatch.setattr(wm_data, "local_time", lambda key, now=None: datetime(2026, 10, 9, 10, 0, tzinfo=tpe))
    yh = FakeYahoo()
    ov = DataService(yahoo_download=yh, twse_client=FakeTwse(realtime_fail=True)).overview()
    items = {i["symbol"]: i for g in ov["groups"] for i in g["items"]}
    assert items["2330"]["source"] == "Yahoo Finance"
    assert any("2330.TW" in syms for syms, _ in yh.calls)


# ---------- 雲端版（GitHub Pages） ----------
def test_build_static_demo(tmp_path):
    from world_market import build_static as bs
    out = tmp_path / "site"
    result = bs.build(out, DataService(demo=True), log=lambda *_: None, history_cache=tmp_path / "hc")
    assert result["ok"] == result["items"] == len(ALL_ITEMS)
    ov = json.loads((out / "data" / "overview.json").read_text(encoding="utf-8"))
    assert ov["static"] and ov["exchanges"]["TW"]["tz"] == "Asia/Taipei"
    assert ov["exchanges"]["JP"]["sessions"] == [["09:00", "11:30"], ["12:30", "15:30"]]
    assert ov["updated_utc"].endswith("Z")
    files = {p.name for p in (out / "data" / "history").glob("*.json")}
    assert len(files) == len(ALL_ITEMS) and "_GSPC.json" in files and "BTC_USD.json" in files
    h = json.loads((out / "data" / "history" / "_GSPC.json").read_text(encoding="utf-8"))
    assert len(h["dates"]) == len(h["close"]) > 1000          # 5 年日 K
    html = (out / "index.html").read_text(encoding="utf-8")
    assert "window.WM_STATIC = true" in html and (out / ".nojekyll").exists()


def test_safe_name_matches_js_rule():
    from world_market.build_static import safe_name
    assert [safe_name(s) for s in ("^GSPC", "BTC-USD", "000001.SS", "TWD=X", "2330")] == \
        ["_GSPC", "BTC_USD", "000001_SS", "TWD_X", "2330"]


def test_build_static_real_path_uses_one_yahoo_batch(tmp_path, monkeypatch):
    from world_market import build_static as bs
    from world_market import data as wm_data
    monkeypatch.setattr(wm_data, "fetch_twse", lambda sym, period: ("2330.TW", fake_df(300)))
    yh = FakeYahoo()
    svc = DataService(yahoo_download=yh, twse_client=FakeTwse(), news_get=fake_news_get)
    result = bs.build(tmp_path / "site", svc, log=lambda *_: None, history_cache=tmp_path / "hc")
    five_year = [c for c in yh.calls if c[1] == "5y"]
    assert len(five_year) == 1                                  # Yahoo 的歷史一次批次下載
    assert "2330.TW" not in five_year[0][0]                     # 台股走證交所
    assert "^TWOII" in result["errors"]                         # 櫃買指數沒有歷史來源
    h = json.loads((tmp_path / "site" / "data" / "history" / "2330.json").read_text(encoding="utf-8"))
    assert h["source"] == "證交所" and len(h["close"]) == 300


def test_build_static_refuses_when_most_items_fail(tmp_path):
    from world_market import build_static as bs

    def boom(symbols, period):
        raise ConnectionError("down")
    svc = DataService(yahoo_download=boom, twse_client=FakeTwse(fail=True))
    with pytest.raises(SystemExit, match="太少"):
        bs.build(tmp_path / "site", svc, log=lambda *_: None, history_cache=tmp_path / "hc")


class FlakyYahoo(FakeYahoo):
    """3 個月的總覽正常，5 年的歷史下載失敗（GitHub 機器常被 Yahoo 限流）。"""
    def __call__(self, symbols, period):
        if period == "5y":
            raise ConnectionError("rate limited")
        return super().__call__(symbols, period)


def test_build_static_reuses_last_good_history(tmp_path, monkeypatch):
    from world_market import build_static as bs
    from world_market import data as wm_data
    monkeypatch.setattr(wm_data, "fetch_twse", lambda sym, period: ("2330.TW", fake_df(300)))
    cache = tmp_path / "hc"
    bs.build(tmp_path / "s1", DataService(yahoo_download=FakeYahoo(), twse_client=FakeTwse(), news_get=fake_news_get),
             log=lambda *_: None, history_cache=cache)
    assert (cache / "_GSPC.json").exists()
    result = bs.build(tmp_path / "s2", DataService(yahoo_download=FlakyYahoo(), twse_client=FakeTwse(), news_get=fake_news_get),
                      log=lambda *_: None, history_cache=cache)
    assert "^GSPC" in result["reused"] and (tmp_path / "s2" / "data" / "history" / "_GSPC.json").exists()
    assert "^GSPC" not in result["errors"]


def test_build_static_refuses_when_most_histories_missing(tmp_path, monkeypatch):
    from world_market import build_static as bs
    from world_market import data as wm_data
    monkeypatch.setattr(wm_data, "fetch_twse", lambda sym, period: ("2330.TW", fake_df(300)))
    svc = DataService(yahoo_download=FlakyYahoo(), twse_client=FakeTwse(), news_get=fake_news_get)
    with pytest.raises(SystemExit, match="歷史資料太少"):
        bs.build(tmp_path / "site", svc, log=lambda *_: None, history_cache=tmp_path / "empty")


def test_history_source_says_yahoo_when_twse_fails(monkeypatch, capsys):
    from world_market import data as wm_data

    def blocked(sym, period):
        raise RuntimeError("證交所回應 HTTP 307")
    monkeypatch.setattr(wm_data, "fetch_twse", blocked)
    monkeypatch.setattr(wm_data, "fetch_history", lambda sym, period, source: ("2330.TW", fake_df(50)))
    h = DataService(yahoo_download=FakeYahoo(), twse_client=FakeTwse(), news_get=fake_news_get).history("2330", "3mo")
    assert h["source"] == "Yahoo Finance"
    assert "改用 Yahoo" in capsys.readouterr().err


# ---------- 財經新聞 ----------
def test_parse_google_news_rss():
    from world_market import news as nw
    items = nw.parse_rss(RSS)
    assert [i["title"] for i in items] == ["台股早盤上漲百點", "台積電法說會優於預期 外資連買"]   # 新的在前、重複標題只留一則
    assert items[0]["source"] == "鉅亨網" and items[0]["published_utc"] == "2026-10-08T05:00:00Z"
    assert all(i["link"].startswith("https://") for i in items)                       # 不安全的連結被擋掉


def test_feed_urls_are_encoded():
    from world_market import news as nw
    url = nw.feed_url(nw.CATEGORIES[0][2])
    assert url.startswith("https://news.google.com/rss/search?q=") and " " not in url and "ceid=TW:zh-Hant" in url


def test_fetch_news_one_category_failing():
    from world_market import news as nw
    calls = []

    def get(url):
        calls.append(url)
        if len(calls) == 2:
            raise ConnectionError("down")
        return RSS
    out = nw.fetch_news(get=get, log=lambda *_: None)
    assert [len(c["items"]) for c in out["categories"]] == [2, 0, 2, 2]
    assert "down" in out["categories"][1]["error"]
    merged = nw.merge_with_previous(out, {"categories": [{"key": "us", "items": [{"title": "舊"}]}]})
    assert merged["categories"][1]["items"] == [{"title": "舊"}] and merged["categories"][1]["stale"]


def test_service_news_cached_and_demo():
    calls = []
    svc = DataService(yahoo_download=FakeYahoo(), twse_client=FakeTwse(),
                      news_get=lambda url: calls.append(url) or RSS)
    svc.news()
    n = len(calls)
    svc.news()
    assert len(calls) == n == 4
    demo = DataService(demo=True).news()
    assert all(c["items"] and "示範" in c["items"][0]["title"] for c in demo["categories"])


def test_build_static_writes_news_and_reuses_previous(tmp_path, monkeypatch):
    from world_market import build_static as bs
    from world_market import data as wm_data
    monkeypatch.setattr(wm_data, "fetch_twse", lambda sym, period: ("2330.TW", fake_df(300)))
    cache = tmp_path / "hc"
    bs.build(tmp_path / "s1", DataService(yahoo_download=FakeYahoo(), twse_client=FakeTwse(),
                                          news_get=fake_news_get), log=lambda *_: None, history_cache=cache)
    n1 = json.loads((tmp_path / "s1" / "data" / "news.json").read_text(encoding="utf-8"))
    assert all(len(c["items"]) == 2 for c in n1["categories"]) and (cache / "news.json").exists()

    def down(url):
        raise ConnectionError("blocked")
    bs.build(tmp_path / "s2", DataService(yahoo_download=FakeYahoo(), twse_client=FakeTwse(), news_get=down),
             log=lambda *_: None, history_cache=cache)
    n2 = json.loads((tmp_path / "s2" / "data" / "news.json").read_text(encoding="utf-8"))
    assert all(c["items"] and c.get("stale") for c in n2["categories"])


def test_server_news_route(server):
    code, body, _ = get(server + "/api/news")
    assert code == 200 and len(json.loads(body)["categories"]) == 4
