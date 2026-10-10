"""台股總覽：快取、更新排程、組合資料、輸出網站、GitHub Actions 設定的測試。

不連網：用假的交易所（FakeExchange）回應請求，解析函式也換成假的（直接回傳交易所準備好的結果），
所以這裡只測排程與組合的邏輯；真實格式的解析在 tw_market/sources.py 的測試。
"""
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import pytest
import requests

from common.twse import TwseError
from tw_market import build as tw_build
from tw_market import contract, site
from tw_market.store import Store
from tw_market.update import latest_day, trading_days, update

TPE = ZoneInfo("Asia/Taipei")
ROOT = Path(__file__).resolve().parent.parent
THU, FRI, SAT = date(2026, 10, 8), date(2026, 10, 9), date(2026, 10, 10)


def at(d: date, hh: int, mm: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=TPE)


# ---------------------------------------------------------------- 假的網址與解析（照 sources.py 的介面）
TWSE, TPEX = "https://www.twse.com.tw/rwd/zh", "https://www.tpex.org.tw/www/zh-tw"


def _ymd(d):
    return f"{d:%Y%m%d}"


def _slash(d):
    return f"{d:%Y/%m/%d}"


URLS = {
    "twse_quotes": lambda d: (f"{TWSE}/afterTrading/MI_INDEX", {"date": _ymd(d), "type": "ALLBUT0999", "response": "json"}),
    "twse_insti": lambda d: (f"{TWSE}/fund/T86", {"date": _ymd(d), "selectType": "ALLBUT0999", "response": "json"}),
    "twse_insti_totals": lambda d: (f"{TWSE}/fund/BFI82U", {"type": "day", "dayDate": _ymd(d), "response": "json"}),
    "twse_margin": lambda d: (f"{TWSE}/marginTrading/MI_MARGN", {"date": _ymd(d), "selectType": "ALL", "response": "json"}),
    "twse_valuation": lambda d: (f"{TWSE}/afterTrading/BWIBBU_d", {"date": _ymd(d), "selectType": "ALL", "response": "json"}),
    "twse_industry": lambda: ("https://openapi.twse.com.tw/v1/opendata/t187ap03_L", {}),
    "tpex_quotes": lambda d: (f"{TPEX}/afterTrading/dailyQuotes", {"date": _slash(d), "id": "", "response": "json"}),
    "tpex_insti": lambda d: (f"{TPEX}/insti/dailyTrade", {"type": "Daily", "sect": "EW", "date": _slash(d), "response": "json"}),
    "tpex_margin": lambda d: (f"{TPEX}/margin/balance", {"date": _slash(d), "id": "", "response": "json"}),
    "tpex_valuation": lambda d: (f"{TPEX}/afterTrading/peQryDate", {"date": _slash(d), "response": "json"}),
    "tpex_index_month": lambda d: (f"{TPEX}/indexInfo/inx", {"date": _slash(d), "response": "json"}),
    "tpex_industry": lambda: ("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O", {}),
}
ROUTES = {
    "/rwd/zh/afterTrading/MI_INDEX": ("quotes", "tse"), "/rwd/zh/fund/T86": ("insti", "tse"),
    "/rwd/zh/fund/BFI82U": ("insti_totals", "tse"), "/rwd/zh/marginTrading/MI_MARGN": ("margin", "tse"),
    "/rwd/zh/afterTrading/BWIBBU_d": ("valuation", "tse"), "/v1/opendata/t187ap03_L": ("industry", "tse"),
    "/www/zh-tw/afterTrading/dailyQuotes": ("quotes", "otc"), "/www/zh-tw/insti/dailyTrade": ("insti", "otc"),
    "/www/zh-tw/margin/balance": ("margin", "otc"), "/www/zh-tw/afterTrading/peQryDate": ("valuation", "otc"),
    "/www/zh-tw/indexInfo/inx": ("otc_index", "otc"), "/openapi/v1/mopsfin_t187ap03_O": ("industry", "otc"),
}


def _fake_parse(js):
    if js.get("bad"):
        raise ValueError("欄位和預期不同")
    return js["parsed"]


PARSERS = ["parse_twse_quotes", "parse_tpex_quotes", "parse_twse_insti", "parse_tpex_insti",
           "parse_twse_insti_totals", "parse_twse_margin", "parse_tpex_margin", "parse_twse_valuation",
           "parse_tpex_valuation", "parse_twse_industry", "parse_tpex_industry", "parse_tpex_index_month"]
SRC = SimpleNamespace(**URLS, **{p: _fake_parse for p in PARSERS})

# 代號: (名稱, 收盤, 漲跌)；收盤 None = 當天沒成交
TSE_ROWS = {"2330": ("台積電", 1000.0, 10.0), "2317": ("鴻海", 11.0, 1.0), "1101": ("台泥", 9.0, -1.0),
            "1234": ("黑松", 40.0, 0.5), "0050": ("元大台灣50", 150.0, 1.5), "030001": ("某某權證", 1.0, 0.1)}
OTC_ROWS = {"6488": ("環球晶", 400.0, 4.0), "5483": ("中美晶", 100.0, 0.0), "006201": ("元大富櫃50", 20.0, 0.2)}
INDUSTRY = {"tse": {"2330": "半導體業", "2317": "其他電子業", "1101": "水泥工業", "1234": "食品工業"},
            "otc": {"6488": "半導體業", "5483": "半導體業"}}


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class FakeExchange:
    """假的證交所＋櫃買中心：days 是有開盤（資料已公布）的日子。"""

    def __init__(self, days, clock=None, step=0.0):
        self.days = set(days)
        self.missing = set()     # (資料, 市場, 日期)：這天這份資料還沒有
        self.fail = {}           # 網站名稱或 (資料, 市場) → 要丟出的錯誤
        self.bad = set()         # (資料, 市場)：格式壞掉
        self.override = {}       # (資料, 市場, 日期) → 直接回傳的解析結果
        self.calls = []          # (資料, 市場, 日期)
        self.hosts = []
        self.clock, self.step = clock, step

    def _get_json(self, url, params):
        u = urlparse(url)
        ds, mk = ROUTES[u.path]
        raw = params.get("date") or params.get("dayDate")
        raw = (raw or "").replace("/", "")
        d = date(int(raw[:4]), int(raw[4:6]), int(raw[6:])) if raw else None
        self.calls.append((ds, mk, d))
        self.hosts.append(u.hostname)
        if self.clock:
            self.clock.t += self.step
        err = self.fail.get(u.hostname) or self.fail.get((ds, mk))
        if err:
            raise err
        if (ds, mk) in self.bad:
            return {"bad": True}
        if (ds, mk, d) in self.override:
            return {"parsed": self.override[(ds, mk, d)]}
        return {"parsed": self.parsed(ds, mk, d)}

    def parsed(self, ds, mk, d):
        if ds == "industry":
            return dict(INDUSTRY[mk])
        if ds == "otc_index":
            got = sorted(x for x in self.days if (x.year, x.month) == (d.year, d.month) and x <= d)
            return {x.isoformat(): {"open": 420.0, "high": 430.0, "low": 419.0, "close": 426.71, "change": -3.75}
                    for x in got}
        if d not in self.days or (ds, mk, d) in self.missing:
            return None
        iso = d.isoformat()
        src = TSE_ROWS if mk == "tse" else OTC_ROWS
        k = sorted(self.days).index(d)
        if ds == "quotes":
            rows = {}
            for code, (name, close, chg) in src.items():
                if code == "1234" and d == max(self.days):      # 最新一天沒成交
                    close = chg = None
                if code == "2330":
                    close = 1000.0 + k
                rows[code] = {"name": name, "open": close, "high": close, "low": close, "close": close,
                              "change": chg, "volume": 1000 + k, "value": 10_000_000, "trades": 50,
                              "pe": 29.56 if code == "2330" else None}
            return {"date": iso, "rows": rows,
                    "index": {"name": "加權指數", "close": 49313.44, "change": -492.93, "change_pct": -0.99}
                    if mk == "tse" else None,
                    "totals": {"value": 900_000_000_000 if mk == "tse" else 256_231_761_006,
                               "volume": 5_000_000 if mk == "tse" else 1_105_739}}
        if ds == "insti":
            return {"date": iso, "rows": {c: {"foreign": 100 + k, "trust": 10, "dealer": -5, "total": 105 + k}
                                          for c in src if c != "1234"}}
        if ds == "insti_totals":
            return {"date": iso, "foreign": -1.2e10, "trust": 1.2e9, "dealer": 3e8, "total": -1.05e10}
        if ds == "margin":
            return {"date": iso, "rows": {c: {"margin_balance": 1000 + k, "margin_change": 10,
                                              "short_balance": 50, "short_change": -1} for c in src}}
        if ds == "valuation":
            return {"date": iso, "rows": {c: {"pe": 29.56 if c == "2330" else None, "dividend_yield": 0.86,
                                              "pb": 10.28} for c in src if contract.classify(c) == "stock"}}
        raise AssertionError(ds)


def weekdays(start: date, end: date, skip=()):
    out, d = [], start
    while d <= end:
        if d.weekday() < 5 and d not in skip:
            out.append(d)
        d += timedelta(days=1)
    return out


@pytest.fixture
def small(monkeypatch):
    """把歷史天數調小，測試比較快。"""
    monkeypatch.setattr(contract, "HIST_MAX_DAYS", 6)
    monkeypatch.setattr(contract, "CHIPS_MAX_DAYS", 3)


def run(store, ex, now, **kw):
    logs = []
    kw.setdefault("max_per_host", 100)
    stats = update(store, ex, now, sources=SRC, log=logs.append, **kw)
    return stats, logs


DAYS = weekdays(date(2026, 9, 1), THU)       # 9/1～10/8 每個平日都有開盤


# ---------------------------------------------------------------- 交易日
def test_latest_day_follows_publish_time():
    assert latest_day("quotes", at(THU, 13, 59), set()) == date(2026, 10, 7)
    assert latest_day("quotes", at(THU, 14, 0), set()) == THU
    assert latest_day("insti", at(THU, 15, 29), set()) == date(2026, 10, 7)
    assert latest_day("margin", at(THU, 21, 0), set()) == THU
    assert latest_day("quotes", at(SAT, 10), set()) == FRI
    assert latest_day("quotes", at(SAT, 10), {FRI}) == THU
    # 沒帶時區的當作台北時間；其他時區會先換算
    assert latest_day("quotes", datetime(2026, 10, 8, 14, 0), set()) == THU  # noqa: DTZ001
    assert latest_day("quotes", datetime(2026, 10, 8, 5, 59, tzinfo=ZoneInfo("UTC")), set()) == date(2026, 10, 7)
    assert trading_days(THU, 3, {date(2026, 10, 7)}) == [THU, date(2026, 10, 6), date(2026, 10, 5)]


# ---------------------------------------------------------------- 更新排程
def test_priority_order_and_interleaving(tmp_path, small):
    ex = FakeExchange(DAYS)
    stats, _ = run(Store(tmp_path), ex, at(THU, 22))
    c = ex.calls
    assert c[:2] == [("quotes", "tse", THU), ("quotes", "otc", THU)]                       # (a) 最新行情
    assert set(c[2:10]) == {("otc_index", "otc", THU), ("insti", "tse", THU), ("insti", "otc", THU),
                            ("insti_totals", "tse", THU), ("valuation", "tse", THU),
                            ("valuation", "otc", THU), ("margin", "tse", THU), ("margin", "otc", THU)}
    assert set(c[10:12]) == {("industry", "tse", None), ("industry", "otc", None)}           # (c) 產業別
    back = [d for d in trading_days(THU, 6, set())][1:]
    assert c[12:22] == [("quotes", m, d) for d in back for m in ("tse", "otc")]             # (d) 新 → 舊、交錯
    assert set(c[22:]) == {(ds, m, d) for d in back[:2] for ds in ("insti", "margin") for m in ("tse", "otc")}
    assert len(c) == 30
    assert stats["requests"] == {"www.twse.com.tw": 14, "www.tpex.org.tw": 15, "openapi.twse.com.tw": 1}
    assert stats["history_days"] == 6 and not stats["errors"]


@pytest.mark.parametrize("hh,mm,today_sets", [
    (13, 59, set()),
    (14, 0, {"quotes", "otc_index"}),
    (15, 0, {"quotes", "otc_index", "valuation"}),
    (15, 30, {"quotes", "otc_index", "valuation", "insti", "insti_totals"}),
    (20, 59, {"quotes", "otc_index", "valuation", "insti", "insti_totals"}),
    (21, 0, {"quotes", "otc_index", "valuation", "insti", "insti_totals", "margin"}),
])
def test_publish_time_gating(tmp_path, small, hh, mm, today_sets):
    ex = FakeExchange(DAYS)
    run(Store(tmp_path), ex, at(THU, hh, mm))
    assert {ds for ds, _, d in ex.calls if d == THU} == today_sets
    if not today_sets:     # 還沒公布：最新一天是昨天
        assert ex.calls[0] == ("quotes", "tse", date(2026, 10, 7))


def test_holiday_marked_only_for_past_days(tmp_path, small):
    days = [d for d in DAYS if d != date(2026, 10, 2)] + [FRI]    # 10/2 休市
    ex = FakeExchange(days)
    ex.missing |= {("quotes", m, FRI) for m in ("tse", "otc")}     # 10/9 當天還沒公布
    store = Store(tmp_path)
    stats, _ = run(store, ex, at(FRI, 15))
    assert store.closed() == {date(2026, 10, 2)}                   # 今天抓不到不算休市
    assert stats["closed_added"] == ["2026-10-02"]
    assert ex.calls.count(("quotes", "otc", date(2026, 10, 2))) == 1   # 兩個市場都問過才記
    assert ex.calls.count(("quotes", "tse", FRI)) == 1            # 同一次執行不會重問
    assert ("insti", "tse", THU) in ex.calls                       # 今天還沒有：最新一天是 10/8

    ex2 = FakeExchange(days)                                       # 隔天（週六）：10/9 已經過去了
    ex2.missing |= {("quotes", m, FRI) for m in ("tse", "otc")}
    run(store, ex2, at(SAT, 10))
    assert store.closed() == {date(2026, 10, 2), FRI}
    assert ex2.calls[:2] == [("quotes", "tse", FRI), ("quotes", "otc", FRI)]
    assert ("quotes", "tse", date(2026, 10, 1)) in ex2.calls       # 少算的交易日往前補


def test_holiday_needs_both_markets(tmp_path, small):
    ex = FakeExchange(DAYS + [FRI])
    ex.missing.add(("quotes", "tse", FRI))         # 上市沒有、上櫃有：不當成休市
    store = Store(tmp_path)
    run(store, ex, at(SAT, 10))
    assert FRI not in store.closed()
    assert store.has("quotes", "otc", FRI) and not store.has("quotes", "tse", FRI)


def test_network_errors_and_blocks_never_mark_holidays(tmp_path, small):
    ex = FakeExchange(DAYS)
    ex.fail["www.twse.com.tw"] = TwseError("www.twse.com.tw 回應 HTTP 307（連線太頻繁被暫時封鎖）")
    ex.fail["www.tpex.org.tw"] = requests.ConnectionError("連不上")
    store = Store(tmp_path)
    stats, _ = run(store, ex, at(SAT, 10))
    assert store.closed() == set() and stats["errors"]


def test_response_date_mismatch_not_saved(tmp_path, small):
    ex = FakeExchange(DAYS)
    old = ex.parsed("quotes", "tse", date(2026, 10, 7))
    ex.override[("quotes", "tse", THU)] = old                    # 要 10/8 卻回 10/7 的資料
    store = Store(tmp_path)
    _, logs = run(store, ex, at(THU, 22))
    assert not store.has("quotes", "tse", THU) and store.has("quotes", "otc", THU)
    assert store.closed() == set()
    assert any("日期是 2026-10-07" in x for x in logs)


def test_per_host_budget(tmp_path, small):
    ex = FakeExchange(DAYS)
    stats, _ = run(Store(tmp_path), ex, at(THU, 22), max_per_host=3)
    per_host = {h: ex.hosts.count(h) for h in set(ex.hosts)}
    assert per_host == {"www.twse.com.tw": 3, "www.tpex.org.tw": 3, "openapi.twse.com.tw": 1}
    assert stats["requests"] == per_host
    assert ex.calls[:2] == [("quotes", "tse", THU), ("quotes", "otc", THU)]   # 最重要的先抓


def test_deadline_with_fake_clock(tmp_path, small):
    clock = Clock()
    ex = FakeExchange(DAYS, clock=clock, step=10)
    stats, _ = run(Store(tmp_path), ex, at(THU, 22), deadline_s=35, clock=clock)
    assert len(ex.calls) == 4 and stats["timed_out"]


def test_block_on_one_host_does_not_stop_the_other(tmp_path, small):
    ex = FakeExchange(DAYS)
    ex.fail["www.twse.com.tw"] = TwseError("www.twse.com.tw 回應 HTTP 307（連線太頻繁被暫時封鎖）")
    store = Store(tmp_path)
    stats, _ = run(store, ex, at(THU, 22))
    assert ex.hosts.count("www.twse.com.tw") == 1
    assert store.dates("quotes", "otc") == trading_days(THU, 6, set())[::-1]
    assert store.has("margin", "otc", THU) and store.industry()["tse"]   # 證交所 OpenAPI 是另一個網站
    assert len(stats["errors"]) == 1


def test_non_block_error_skips_only_that_dataset(tmp_path, small):
    class Gate:
        def blocked_remaining(self):
            return 0.0

    ex = FakeExchange(DAYS)
    ex.gate = lambda url: Gate()
    ex.fail[("margin", "tse")] = TwseError("https://www.twse.com.tw/... 回應 HTTP 404")
    ex.bad.add(("insti", "otc"))
    store = Store(tmp_path)
    stats, _ = run(store, ex, at(THU, 22))
    assert ex.calls.count(("margin", "tse", THU)) == 1
    assert sum(1 for ds, m, _ in ex.calls if (ds, m) in {("margin", "tse"), ("insti", "otc")}) == 2
    assert store.has("insti", "tse", date(2026, 10, 6)) and store.has("margin", "otc", date(2026, 10, 6))
    assert len(stats["errors"]) == 2


def test_cached_dates_never_refetched(tmp_path, small):
    store = Store(tmp_path)
    run(store, FakeExchange(DAYS), at(THU, 22))
    again = FakeExchange(DAYS)
    stats, _ = run(store, again, at(THU, 23, 45))
    assert again.calls == [] and stats["requests"] == {}


def test_corrupt_cache_file_is_ignored_and_refetched(tmp_path, small):
    store = Store(tmp_path)
    run(store, FakeExchange(DAYS), at(THU, 22))
    store.path("quotes", "tse", date(2026, 10, 7)).write_text("{壞掉", encoding="utf-8")
    _, _, hists = tw_build.assemble(store, at(THU, 22))
    assert "2026-10-07" not in hists["2330"]["dates"] and len(hists["2330"]["dates"]) == 5
    assert "2026-10-07" in hists["6488"]["dates"]                 # 上櫃那天的檔沒壞
    assert not store.has("quotes", "tse", date(2026, 10, 7))       # 壞掉的檔刪掉，下次重抓
    ex = FakeExchange(DAYS)
    run(store, ex, at(THU, 22, 15))
    assert ex.calls == [("quotes", "tse", date(2026, 10, 7))]


def test_backfill_depth(tmp_path, monkeypatch):
    monkeypatch.setattr(contract, "HIST_MAX_DAYS", 5)
    monkeypatch.setattr(contract, "CHIPS_MAX_DAYS", 2)
    ex = FakeExchange(DAYS)
    run(Store(tmp_path), ex, at(THU, 22))
    want = set(trading_days(THU, 5, set()))
    for m in ("tse", "otc"):
        assert {d for ds, mk, d in ex.calls if ds == "quotes" and mk == m} == want
        for ds in ("insti", "margin"):
            assert {d for x, mk, d in ex.calls if x == ds and mk == m} == {THU, date(2026, 10, 7)}
    assert {d for ds, _, d in ex.calls if ds == "valuation"} == {THU}      # 本益比只要最新一天


def test_industry_refreshed_after_seven_days(tmp_path, small):
    store = Store(tmp_path)
    run(store, FakeExchange(DAYS), at(THU, 22))
    ex = FakeExchange(DAYS)
    run(store, ex, at(THU + timedelta(days=6), 22))
    assert not any(ds == "industry" for ds, _, _ in ex.calls)
    ex = FakeExchange(DAYS)
    run(store, ex, at(THU + timedelta(days=8), 22))
    assert {(ds, m) for ds, m, _ in ex.calls if ds == "industry"} == {("industry", "tse"), ("industry", "otc")}


# ---------------------------------------------------------------- 組合資料
def test_assemble_matches_contract(tmp_path, small):
    store = Store(tmp_path)
    run(store, FakeExchange(DAYS), at(THU, 22))
    out = tmp_path / "site"
    r = tw_build.build(out, cache_dir=tmp_path, now=at(THU, 22), fetch=False, log=lambda *_: None)
    assert r["date"] == "2026-10-08"
    summary = json.loads((out / "data" / "summary.json").read_text(encoding="utf-8"))
    stocks = json.loads((out / "data" / "stocks.json").read_text(encoding="utf-8"))
    assert contract.check_summary(summary) == [] and contract.check_stocks(stocks) == []
    rows = {r[0]: dict(zip(contract.STOCK_FIELDS, r)) for r in stocks["rows"]}
    assert set(rows) == {"2330", "2317", "1101", "1234", "0050", "6488", "5483", "006201"}   # 權證不收
    tsmc = rows["2330"]
    assert tsmc["industry"] == "半導體業" and tsmc["market"] == "tse" and tsmc["type"] == "stock"
    assert tsmc["change_pct"] == round(10 / (tsmc["close"] - 10) * 100, 2)
    assert tsmc["pe"] == 29.56 and tsmc["foreign"] is not None and tsmc["margin_balance"] is not None
    assert rows["0050"]["industry"] == "ETF" and rows["0050"]["pb"] is None
    assert rows["1234"]["close"] is None and rows["1234"]["change_pct"] is None
    tse, otc = summary["markets"]["tse"], summary["markets"]["otc"]
    assert (tse["up"], tse["down"], tse["flat"], tse["limit_up"], tse["limit_down"]) == (2, 1, 0, 1, 1)
    assert (otc["up"], otc["down"], otc["flat"]) == (1, 0, 1)
    assert tse["value"] == 900_000_000_000 and otc["volume"] == 1_105_739
    assert tse["index"]["close"] == 49313.44
    assert otc["index"] == {"name": "櫃買指數", "close": 426.71, "change": -3.75,
                            "change_pct": round(-3.75 / (426.71 + 3.75) * 100, 2)}
    assert summary["institutional"]["tse"]["total"] == -1.05e10 and summary["institutional"]["otc"] is None
    assert summary["chips_date"] == summary["margin_date"] == summary["valuation_date"] == "2026-10-08"
    assert summary["history_days"] == 6
    assert summary["notes"] == ["三大法人買賣金額只有上市（上櫃暫不提供）"]
    h = json.loads((out / "data" / "hist" / "2330.json").read_text(encoding="utf-8"))
    assert contract.check_hist(h) == [] and h["name"] == "台積電"
    assert h["dates"] == [d.isoformat() for d in sorted(trading_days(THU, 6, set()))]
    assert h["close"] == sorted(h["close"]) and len(h["inst"]["dates"]) == 3 and len(h["margin"]["dates"]) == 3
    h1234 = json.loads((out / "data" / "hist" / "1234.json").read_text(encoding="utf-8"))
    assert len(h1234["dates"]) == 5 and h1234["inst"] is None      # 最新一天沒成交；沒有法人資料
    assert not (out / "data" / "hist" / "030001.json").exists()
    assert (out / "index.html").exists()


def test_notes_for_missing_and_late_datasets(tmp_path, small):
    ex = FakeExchange(DAYS)
    for d in DAYS:
        for m in ("tse", "otc"):
            ex.missing |= {("insti", m, d), ("margin", m, d)}
        ex.missing.add(("insti_totals", "tse", d))
    store = Store(tmp_path)
    run(store, ex, at(THU, 16))
    summary, _, _ = tw_build.assemble(store, at(THU, 16))
    assert summary["chips_date"] is None and summary["margin_date"] is None
    assert summary["institutional"] is None and summary["valuation_date"] == "2026-10-08"
    assert "三大法人還沒有資料" in summary["notes"] and "融資融券還沒有資料" in summary["notes"]

    ex = FakeExchange(DAYS)                                    # 16:00：融資融券要晚上才有
    store = Store(tmp_path / "b")
    run(store, ex, at(THU, 16))
    summary, _, _ = tw_build.assemble(store, at(THU, 16))
    assert summary["margin_date"] == "2026-10-07" and summary["chips_date"] == "2026-10-08"
    assert "融資融券晚上 9 點後才公布，先顯示 10/07 的" in summary["notes"]

    ex = FakeExchange(DAYS)                                    # 14:30：三樣都還沒公布，合成一句
    store = Store(tmp_path / "c")
    run(store, ex, at(THU, 14, 30))
    summary, _, _ = tw_build.assemble(store, at(THU, 14, 30))
    assert summary["date"] == "2026-10-08" and summary["notes"][0] == (
        "本益比、殖利率（下午 3 點後）、三大法人（下午 3 點半後）、融資融券（晚上 9 點後）才公布，先顯示 10/07 的")
    summary, _, _ = tw_build.assemble(store, at(FRI, 10))     # 隔天還是舊的：說還沒抓到
    assert "三大法人還沒抓到 10/08 的，先顯示 10/07 的" in summary["notes"]


def test_otc_missing_latest_uses_earlier_day(tmp_path, small):
    ex = FakeExchange(DAYS)
    ex.missing.add(("quotes", "otc", THU))
    store = Store(tmp_path)
    run(store, ex, at(THU, 22))
    summary, stocks, _ = tw_build.assemble(store, at(THU, 22))
    assert summary["date"] == "2026-10-08"
    assert "上櫃行情是 10/07 的（10/08 的還沒抓到）" in summary["notes"]
    assert any(s["code"] == "6488" for s in stocks)
    assert summary["markets"]["otc"]["index"]["close"] == 426.71      # 用 10/7 的櫃買指數
    assert "上櫃三大法人是 10/07 的資料" in summary["notes"]


def test_tse_behind_otc_is_noted(tmp_path, small):
    ex = FakeExchange(DAYS)
    ex.missing.add(("quotes", "tse", THU))
    store = Store(tmp_path)
    run(store, ex, at(THU, 22))
    summary, stocks, _ = tw_build.assemble(store, at(THU, 22))
    assert summary["date"] == "2026-10-07"                     # 以上市最新的一天為準
    assert "上市行情還沒抓到 10/08 的，先顯示 10/07 的" in summary["notes"]
    assert {s["market"] for s in stocks} == {"tse", "otc"}


def test_history_note_while_filling(tmp_path, monkeypatch):
    monkeypatch.setattr(contract, "HIST_MAX_DAYS", 6)
    monkeypatch.setattr(contract, "CHIPS_MAX_DAYS", 3)
    store = Store(tmp_path)
    run(store, FakeExchange(DAYS), at(THU, 22), max_per_host=6)
    summary, _, _ = tw_build.assemble(store, at(THU, 22))
    n = summary["history_days"]
    assert n < 6 and f"歷史 K 線還在補齊中（目前 {n} 天）" in summary["notes"]


# ---------------------------------------------------------------- 輸出網站
def test_empty_cache_writes_only_the_page(tmp_path):
    out = tmp_path / "tw"
    logs = []
    r = tw_build.build(out, cache_dir=tmp_path / "cache", now=at(THU, 22), fetch=False, log=logs.append)
    assert r["date"] is None and [p.name for p in out.iterdir()] == ["index.html"]
    assert any("還沒有任何行情" in x for x in logs)


def test_failed_build_keeps_previous_output(tmp_path, small, monkeypatch):
    store = Store(tmp_path / "cache")
    run(store, FakeExchange(DAYS), at(THU, 22))
    out = tmp_path / "tw"
    tw_build.build(out, cache_dir=store.root, now=at(THU, 22), fetch=False, log=lambda *_: None)
    before = (out / "data" / "summary.json").read_text(encoding="utf-8")

    def boom(*a, **k):
        raise OSError("磁碟滿了")
    monkeypatch.setattr(site.shutil, "copyfile", boom)          # 寫到一半失敗
    with pytest.raises(OSError):
        tw_build.build(out, cache_dir=store.root, now=at(THU, 23), fetch=False, log=lambda *_: None)
    assert (out / "data" / "summary.json").read_text(encoding="utf-8") == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["cache", "tw"]      # 沒留下暫存資料夾
    monkeypatch.undo()

    def bad(store, now=None, log=print):                       # 格式檢查不通過：大聲報錯、不動網頁
        s, st, h = real(store, now, log)
        st[0]["type"] = "warrant"
        return s, st, h
    real = tw_build.assemble
    monkeypatch.setattr(tw_build, "assemble", bad)
    monkeypatch.setattr(contract, "HIST_MAX_DAYS", 6)
    monkeypatch.setattr(contract, "CHIPS_MAX_DAYS", 3)
    with pytest.raises(SystemExit, match="格式有問題"):
        tw_build.build(out, cache_dir=store.root, now=at(THU, 23), fetch=False, log=lambda *_: None)
    assert (out / "data" / "summary.json").read_text(encoding="utf-8") == before


def test_demo_build(tmp_path):
    out = tmp_path / "tw"
    tw_build.main(["--out", str(out), "--demo"])
    summary = json.loads((out / "data" / "summary.json").read_text(encoding="utf-8"))
    assert summary["demo"] is True and contract.check_summary(summary) == []
    assert contract.check_stocks(json.loads((out / "data" / "stocks.json").read_text(encoding="utf-8"))) == []
    assert (out / "data" / "hist" / "2330.json").exists() and (out / "index.html").exists()


def test_no_fetch_makes_zero_requests(tmp_path, small, monkeypatch):
    store = Store(tmp_path / "cache")
    run(store, FakeExchange(DAYS), at(THU, 22))
    ex = FakeExchange(DAYS)
    tw_build.build(tmp_path / "a", cache_dir=store.root, now=at(THU, 23), client=ex, fetch=False,
                   log=lambda *_: None)
    assert ex.calls == []

    import common.twse

    class NoNetwork:
        def __init__(self, *a, **k):
            raise AssertionError("--no-fetch 不應該連網")
    monkeypatch.setattr(common.twse, "TwseClient", NoNetwork)
    tw_build.main(["--out", str(tmp_path / "b"), "--no-fetch", "--cache", str(store.root)])
    assert (tmp_path / "b" / "data" / "summary.json").exists()


def test_build_with_fetch_uses_update(tmp_path, small):
    ex = FakeExchange(DAYS)
    r = tw_build.build(tmp_path / "tw", cache_dir=tmp_path / "cache", now=at(THU, 22), client=ex,
                       log=lambda *_: None, sources=SRC, max_per_host=100)
    assert r["date"] == "2026-10-08" and r["update"]["requests"]["www.twse.com.tw"] == 14


# ---------------------------------------------------------------- 快取
def test_store_atomic_and_tolerant(tmp_path):
    store = Store(tmp_path)
    store.save("quotes", "tse", THU, {"date": "2026-10-08", "rows": {"2330": {"close": float("nan")}}})
    assert store.load("quotes", "tse", THU)["rows"]["2330"]["close"] is None     # NaN 存成 null
    assert [p.name for p in (tmp_path / "quotes" / "tse").iterdir()] == ["20261008.json"]
    (tmp_path / "quotes" / "tse" / "notes.txt").write_text("x")
    assert store.dates("quotes", "tse") == [THU]
    (tmp_path / "calendar.json").write_text("壞掉")
    assert store.closed() == set()
    store.add_closed([FRI])
    assert store.closed() == {FRI}
    store.save_otc_index(2026, 10, {"2026-10-08": {"close": 1.0}})
    assert store.otc_index(2026, 10) == {"2026-10-08": {"close": 1.0}} and store.otc_index(2026, 9) == {}


# ---------------------------------------------------------------- GitHub Actions
def test_workflow_has_tw_steps():
    yaml = pytest.importorskip("yaml")      # 沒裝 PyYAML 就跳過（requirements.txt 沒有它）
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "board.yml").read_text(encoding="utf-8"))
    on = wf.get("on", wf.get(True))            # YAML 1.1 會把 on 讀成 True
    assert "tw_market/**" in on["push"]["paths"]
    steps = wf["jobs"]["build"]["steps"]
    names = [s.get("name") or s.get("uses") for s in steps]
    caches = [s for s in steps if str(s.get("uses", "")).startswith("actions/cache")]
    tw_cache = [s for s in caches if s["with"]["path"] == ".cache/tw"]
    assert tw_cache and tw_cache[0]["with"]["key"] == "tw-${{ github.run_id }}"
    assert tw_cache[0]["with"]["restore-keys"] == "tw-"
    i = names.index("產生台股總覽")
    tw = steps[i]
    assert tw["id"] == "tw" and tw["continue-on-error"] is True and tw["timeout-minutes"] == 12
    assert tw["run"].strip() == "python -m tw_market.build --out _site/tw"
    fb = steps[i + 1]
    assert fb["if"].replace('"', "'") == "steps.tw.outcome != 'success'" and fb["continue-on-error"] is True
    assert fb["run"].strip() == "python -m tw_market.build --out _site/tw --no-fetch"
    upload = names.index("actions/upload-pages-artifact@v3")
    assert names.index("產生看板網頁") < i < upload and i + 1 < upload
    assert steps.index(tw_cache[0]) < names.index("產生看板網頁")
