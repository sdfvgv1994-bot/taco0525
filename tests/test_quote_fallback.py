"""證交所即時報價被擋時的備援：Yahoo 延遲報價、本機中文股名對照表。全部不連網。"""
import json
from datetime import datetime

import pandas as pd

from common import names as nm
from common import twse
from common.market import YAHOO_DELAY_NOTE, yahoo_quotes
from stock_monitor.engine import Monitor, with_live_bar
from stock_monitor.storage import Store

NOW = datetime(2024, 10, 3, 10, 0)   # 週四盤中（台北時間）


def daily(*closes, end="2024-10-03"):
    idx = pd.bdate_range(end=end, periods=len(closes))
    return pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes,
                         "Volume": [1000.0] * len(closes)}, index=idx)


class FakeYahoo:
    def __init__(self, data):
        self.data, self.calls = data, []

    def __call__(self, tickers):
        self.calls.append(list(tickers))
        return {t: self.data[t] for t in tickers if t in self.data}


# ---------- Yahoo 延遲報價 ----------
def test_yahoo_quotes_picks_market_and_prev_close():
    dl = FakeYahoo({"2330.TW": daily(990.0, 1000.0), "6488.TWO": daily(400.0, 410.0),
                    "^TWII": daily(22000.0, 22100.0)})
    got = yahoo_quotes(["2330", "6488", "^TWII", "9999"], download=dl, now=NOW)
    assert dl.calls == [["2330.TW", "2330.TWO", "6488.TW", "6488.TWO", "^TWII", "9999.TW", "9999.TWO"]]
    assert set(got) == {"2330", "6488", "^TWII"}                 # 查不到的就不回傳
    q = got["2330"]
    assert (q.price, q.prev_close, q.market, q.source, q.name) == (1000.0, 990.0, "tse", "yahoo", "")
    assert q.time == NOW                                          # 今天的 K 棒：時間是現在
    assert got["6488"].market == "otc" and got["^TWII"].volume is None


def test_yahoo_quote_from_previous_day_is_stamped_at_close():
    got = yahoo_quotes(["2330"], download=FakeYahoo({"2330.TW": daily(990.0, 1000.0, end="2024-10-02")}),
                       now=NOW)
    assert got["2330"].time == datetime(2024, 10, 2, 13, 30)


def test_yahoo_quotes_ignores_non_tw_items():
    dl = FakeYahoo({})
    assert yahoo_quotes(["AAPL"], download=dl) == {} and dl.calls == []


# ---------- 看盤引擎：證交所抓不到 → Yahoo ----------
def _fetcher(name, settings, now, q):
    df = daily(*[985.0] * 30, end="2024-10-02")
    return name, with_live_bar(df, q.get(name)), q[name].price if name in q else 985.0


def _blocked(items):
    raise twse.TwseError("mis.twse.com.tw 回應 HTTP 502（連線太頻繁被暫時封鎖，1 分鐘內先不連線）")


def test_monitor_uses_yahoo_when_mis_blocked(tmp_path):
    calls = []

    def fallback(items):
        calls.append(items)
        return yahoo_quotes(items, download=FakeYahoo({"2330.TW": daily(985.0, 1000.0)}), now=NOW)

    m = Monitor(Store(tmp_path), fetcher=_fetcher, realtime=_blocked, fallback=fallback,
                name_lookup={"2330": "台積電"}.get)
    m.add("2330")
    m.add("AAPL")
    res = m.refresh(NOW)
    assert calls == [["2330"]]                                    # 只查一次、只查台股
    assert res.snapshots["2330"].price == 1000.0                  # 不是昨天的 985
    assert res.delayed == {"2330"} and not res.live               # 仍標 *（不是證交所即時）
    assert m.names["2330"] == "台積電"
    assert any("502" in n and YAHOO_DELAY_NOTE in n for n in res.notes)


def test_monitor_partial_fallback_note(tmp_path):
    fb = lambda items: yahoo_quotes(items, download=FakeYahoo({"2330.TW": daily(985.0, 1000.0)}), now=NOW)
    m = Monitor(Store(tmp_path), fetcher=_fetcher, realtime=_blocked, fallback=fb, name_lookup=lambda c: None)
    m.add("2330")
    m.add("6488")
    res = m.refresh(NOW)
    assert res.delayed == {"2330"}
    note = next(n for n in res.notes if "即時報價暫時抓不到" in n)
    assert "2330 " + YAHOO_DELAY_NOTE in note and "其他是最近一天的收盤價" in note


def test_monitor_fallback_errors_fall_back_to_close(tmp_path):
    def broken(items):
        raise RuntimeError("Yahoo 也掛了")
    m = Monitor(Store(tmp_path), fetcher=_fetcher, realtime=_blocked, fallback=broken, name_lookup=lambda c: None)
    m.add("2330")
    res = m.refresh(NOW)
    assert res.snapshots["2330"].price == 985.0 and not res.delayed
    assert any("最近一天的收盤價" in n for n in res.notes)


def test_monitor_no_fallback_for_yahoo_or_twse_only_source(tmp_path):
    calls = []
    for source in ("yahoo", "twse"):
        m = Monitor(Store(tmp_path / source), fetcher=_fetcher, realtime=_blocked,
                    fallback=lambda items: calls.append(items) or {}, name_lookup=lambda c: None)
        m.settings.update(source=source, realtime=True)
        m.add("2330")
        m.refresh(NOW)
    assert calls == []


def test_monitor_fallback_only_for_missing_items(tmp_path):
    q = twse.Quote("2330", "台積電", "tse", 1001.0, 985.0, None, None, None, None, NOW)
    calls = []
    m = Monitor(Store(tmp_path), fetcher=_fetcher, realtime=lambda items: {"2330": q},
                fallback=lambda items: calls.append(items) or {}, name_lookup=lambda c: None)
    m.add("2330")
    m.add("6488")
    res = m.refresh(NOW)
    assert calls == [["6488"]] and res.live == {"2330"}


def test_injected_realtime_without_fallback_never_calls_yahoo(tmp_path):
    m = Monitor(Store(tmp_path), fetcher=_fetcher, realtime=_blocked, name_lookup=lambda c: None)
    assert m._fallback is None


# ---------- 本機中文股名對照表 ----------
def _write_day(root, market, day, rows):
    p = root / "quotes" / market / f"{day}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"date": f"{day[:4]}-{day[4:6]}-{day[6:]}",
                             "rows": {c: {"name": n} for c, n in rows.items()}}, ensure_ascii=False),
                 encoding="utf-8")


def test_listing_names_newest_day_of_both_markets(tmp_path):
    _write_day(tmp_path, "tse", "20241001", {"2330": "舊名"})
    _write_day(tmp_path, "tse", "20241002", {"2330": "台積電", "0050": "元大台灣50"})
    _write_day(tmp_path, "otc", "20241002", {"6488": "環球晶"})
    assert nm.listing_names(tmp_path) == {"2330": "台積電", "0050": "元大台灣50", "6488": "環球晶"}
    assert nm.listing_name("6488", tmp_path) == "環球晶"


def test_listing_names_skips_broken_newest_file(tmp_path):
    _write_day(tmp_path, "tse", "20241001", {"2330": "台積電"})
    (tmp_path / "quotes" / "tse" / "20241002.json").write_text("{壞掉", encoding="utf-8")
    assert nm.listing_names(tmp_path) == {"2330": "台積電"}


def test_listing_names_without_cache(tmp_path):
    assert nm.listing_names(tmp_path / "沒有") == {}


def test_monitor_default_name_lookup_uses_listing(tmp_path, monkeypatch):
    _write_day(tmp_path / "tw", "otc", "20241002", {"6674": "鋐寶科技"})
    monkeypatch.setattr(nm, "TW_CACHE", tmp_path / "tw")

    class NoNet:
        def cached_name(self, code):
            return None
    monkeypatch.setattr(twse, "client", lambda: NoNet())
    m = Monitor(Store(tmp_path / "s"), fetcher=_fetcher, realtime=_blocked, fallback=lambda items: {})
    assert m.lookup_name("6674") == "鋐寶科技"


def test_cloud_prefetch_names_prefers_listing(tmp_path, monkeypatch):
    from cloud_monitor import run as cm
    monkeypatch.setattr(nm, "listing_names", lambda root=None: {"2330": "台積電"})

    class Blocked:
        names = {}
        calls = []

        def cached_name(self, code):
            return None

        def stock_month(self, *a):
            self.calls.append(a)
            raise twse.TwseError("www.twse.com.tw 剛剛暫時封鎖了這台電腦的連線")
        tpex_month = stock_month
    fake = Blocked()
    monkeypatch.setattr(twse, "client", lambda: fake)
    m = Monitor(Store(tmp_path / "s"), fetcher=_fetcher, realtime=lambda items: {})
    m.watchlist = {"2330": {}, "6488": {}}
    m.names = {}
    cm.prefetch_names(m, NOW, log=lambda *_: None)
    assert m.names == {"2330": "台積電"}                          # 證交所被擋也有股名
    assert [a[0] for a in fake.calls] == ["6488"]                 # 對照表有的就不連網
