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
    # 上市的法人名單有資料、但沒有 1234：沒有法人買賣，是 0 不是 null
    assert (rows["1234"]["foreign"], rows["1234"]["inst_total"]) == (0, 0)
    assert (rows["2317"]["limit"], rows["1101"]["limit"], rows["2330"]["limit"]) == (1, -1, 0)
    assert rows["0050"]["limit"] is None and rows["1234"]["limit"] is None
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
    # 最新一天沒成交：那天是 null；有成交的日子不在法人名單上 = 0
    assert len(h1234["dates"]) == 5 and h1234["inst"] == {
        "dates": ["2026-10-06", "2026-10-07", "2026-10-08"],
        "foreign": [0, 0, None], "trust": [0, 0, None], "dealer": [0, 0, None]}
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

    def bad(store, now=None, log=print, **kw):                 # 格式檢查不通過：大聲報錯、不動網頁
        s, st, h = real(store, now, log, **kw)
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
    data_path = [".cache/tw", "!.cache/tw/status.json"]            # 抓資料的紀錄另外存
    restore = steps[names.index("取出台股總覽的快取")]
    assert restore["uses"] == "actions/cache/restore@v4"
    assert restore["with"]["path"].split() == data_path
    assert restore["with"]["key"] == "tw-data-${{ github.run_id }}" and restore["with"]["restore-keys"] == "tw-data-"
    status = [s for s in steps if str(s.get("uses", "")).startswith("actions/cache@")
              and s["with"]["path"] == ".cache/tw/status.json"]
    assert status and status[0]["with"]["restore-keys"] == "tw-status-"     # 很小，每次都存
    assert "tw-data-".startswith(status[0]["with"]["restore-keys"]) is False
    i = names.index("產生台股總覽")
    tw = steps[i]
    assert tw["id"] == "tw" and tw["continue-on-error"] is True and tw["timeout-minutes"] == 12
    assert tw["run"].strip() == "python -m tw_market.build --out _site/tw"
    fb = steps[i + 1]
    assert fb["if"].replace('"', "'") == "steps.tw.outcome != 'success'" and fb["continue-on-error"] is True
    assert fb["id"] == "tw_cached"
    assert fb["run"].strip() == "python -m tw_market.build --out _site/tw --no-fetch --fetch-failed"
    save = steps[i + 2]                                             # 快取有變動才存（不然幾天就用完快取空間）
    assert save["uses"] == "actions/cache/save@v4" and save["with"]["path"].split() == data_path
    assert save["with"]["key"] == restore["with"]["key"]
    assert save["if"].replace('"', "'") == ("steps.tw.outputs.changed == 'true' || "
                                            "steps.tw_cached.outputs.changed == 'true'")
    upload = names.index("actions/upload-pages-artifact@v3")
    assert names.index("產生看板網頁") < i < upload and i + 2 < upload
    assert steps.index(restore) < steps.index(status[0]) < names.index("產生看板網頁")
    assert not any(s["with"].get("restore-keys") == "tw-" for s in steps if "with" in s and "cache" in s.get("uses", ""))


# ---------------------------------------------------------------- 審查修正：漲停跌停、漲跌家數
@pytest.mark.parametrize("prev,up,down", [
    (98.1, 107.5, 88.3),       # 1303 南亞：100 元以上一檔 0.5，只漲 9.58% 就漲停
    (45.35, 49.85, 40.85),     # 1568 倉佑收 49.80（+9.81%）不是漲停
    (8.80, 9.68, 7.92),        # 9105 泰金寶-DR
    (76.5, 84.1, 68.9),        # 6456 GIS-KY 收 83.90 不是漲停
    (46.35, 50.9, 41.75),      # 8455 大拓-KY 收 41.80（−9.82%）不是跌停
    # 櫃買中心 10/08 公布的「次日 漲停價 / 跌停價」，各個升降單位都有
    (7.98, 8.77, 7.19), (10.35, 11.35, 9.32), (49.65, 54.6, 44.7), (53.8, 59.1, 48.45),
    (64.2, 70.6, 57.8), (97.0, 106.5, 87.3), (249.5, 274.0, 225.0), (497.0, 546.0, 447.5),
    (581.0, 639.0, 523.0), (1030.0, 1130.0, 927.0), (2960.0, 3255.0, 2665.0),
])
def test_limit_prices_follow_tick_rule(prev, up, down):
    assert contract.limit_prices(prev) == (up, down)


def test_limit_of_needs_exact_limit_price():
    assert contract.limit_of(107.5, 9.4) == 1               # 南亞 +9.58% 是漲停
    assert contract.limit_of(49.8, 4.45) == 0               # 倉佑 +9.81% 不是
    assert contract.limit_of(41.75, -4.6) == -1
    assert contract.limit_of(112.0, 12.0) == 0              # 沒有漲跌幅限制的新股 +12% 不算漲停
    assert contract.limit_of(None, 1.0) is None and contract.limit_of(10.0, None) is None
    assert contract.limit_prices(0) is None and contract.limit_prices(float("nan")) is None


def _cache_with(tmp_path, tse_rows, otc_rows=None, d=THU, industry=None, insti=None):
    """直接寫一天的快取（不經過 update）。rows：{代號: (名稱, 收盤, 漲跌)}。"""
    store = Store(tmp_path / "cache")
    for mk, src in (("tse", tse_rows), ("otc", otc_rows or {})):
        if src:
            store.save("quotes", mk, d, {"date": d.isoformat(), "index": None, "totals": None, "rows": {
                c: {"name": n, "open": cl, "high": cl, "low": cl, "close": cl, "change": ch,
                    "volume": None if cl is None else 10, "value": 1000, "trades": 1, "pe": None}
                for c, (n, cl, ch) in src.items()}})
    for mk, rows in (insti or {}).items():
        store.save("insti", mk, d, {"date": d.isoformat(), "rows": rows})
    store.save_industry(industry or {})
    return store


def test_breadth_and_limits_match_exchange_rules(tmp_path):
    """漲跌家數、漲停跌停：只算股票、不算存託憑證，漲停要剛好等於漲停價（審查：6/1 多算 8 檔漲停）。"""
    store = _cache_with(tmp_path, {
        "1303": ("南亞", 107.5, 9.4),          # 漲停（+9.58%）
        "1568": ("倉佑", 49.8, 4.45),          # +9.81% 但不是漲停
        "7827": ("漢康-KY創", 112.0, 12.0),     # 新股沒有漲跌幅限制
        "1101": ("台泥", 9.0, -1.0),           # 跌停
        "8455": ("大拓-KY", 41.8, -4.55),      # −9.82% 但不是跌停
        "1103": ("嘉泥", 13.15, 0.0),
        "1341": ("富林-KY", 59.1, None),       # 不比價（除權息等）
        "9105": ("泰金寶-DR", 9.68, 0.88),      # 存託憑證：證交所不算
        "9103": ("美德醫療-DR", 5.16, -0.07),   # 存託憑證（產業別還沒抓到，看名稱）
        "0050": ("元大台灣50", 55.0, 5.0),      # ETF 不算
        "00636K": ("國泰中國A50+U", 9.21, -0.08),   # 外幣 ETF（舊快取裡可能有）：不收
    }, industry={"tse": {"9105": "存託憑證"}})
    summary, stocks, _ = tw_build.assemble(store, at(THU, 22))
    tse = summary["markets"]["tse"]
    assert (tse["up"], tse["down"], tse["flat"]) == (3, 2, 1)
    assert (tse["limit_up"], tse["limit_down"]) == (1, 1)
    rows = {s["code"]: s for s in stocks}
    assert "00636K" not in rows
    assert {c: rows[c]["limit"] for c in ("1303", "1568", "7827", "1101", "8455", "1341", "9105", "0050")} == {
        "1303": 1, "1568": 0, "7827": 0, "1101": -1, "8455": 0, "1341": None, "9105": 1, "0050": None}
    assert tw_build.validate(summary, stocks, {}) == []


def test_institutional_absent_means_zero(tmp_path):
    """交易所的法人名單不列「沒有法人買賣」的股票：名單有資料但沒有這檔 → 0；整天沒有資料 → null。"""
    store = _cache_with(tmp_path, {"2330": ("台積電", 1000.0, 5.0), "2314": ("台揚", 20.0, 0.1)},
                        {"6488": ("環球晶", 400.0, 4.0)},
                        insti={"tse": {"2330": {"foreign": 5, "trust": 1, "dealer": 0, "total": 6}}})
    _, stocks, hists = tw_build.assemble(store, at(THU, 22))
    rows = {s["code"]: s for s in stocks}
    assert [rows["2314"][k] for k in ("foreign", "trust", "dealer", "inst_total")] == [0, 0, 0, 0]
    assert rows["2330"]["inst_total"] == 6
    assert rows["6488"]["foreign"] is None                     # 上櫃那天的法人資料整個沒有
    assert hists["2314"]["inst"] == {"dates": ["2026-10-08"], "foreign": [0], "trust": [0], "dealer": [0]}
    assert hists["6488"]["inst"] is None
    assert rows["2314"]["margin_balance"] is None              # 融資融券不在名單上 = 不能信用交易，維持 null


# ---------------------------------------------------------------- 審查修正：應該有卻沒有的資料不要一直重問
def _past(days, d):
    return [x for x in days if x < d]


def test_wrong_date_answers_cost_one_request_per_run(tmp_path, small):
    """櫃買中心不管日期都回最新一天（網站改版）：這類資料每次只浪費 1 次，錯誤會記下來，籌碼照樣補。"""
    ex = FakeExchange(DAYS)
    for d in _past(DAYS, THU):
        ex.override[("quotes", "otc", d)] = ex.parsed("quotes", "otc", THU)
    store = Store(tmp_path)
    stats, _ = run(store, ex, at(THU, 22))
    wasted = [c for c in ex.calls if c[:2] == ("quotes", "otc") and c[2] != THU]
    assert len(wasted) == 1 and any("日期是 2026-10-08" in e for e in stats["errors"])
    assert store.has("insti", "otc", THU) and store.has("margin", "otc", THU)
    assert stats["closed_added"] == []
    first = wasted[0][2]
    for k in range(2, 6):                                       # 之後每次也只浪費 1 次
        ex.calls.clear()
        stats, _ = run(store, ex, at(THU, 22) + timedelta(minutes=15 * k))
        wasted = [c for c in ex.calls if c[:2] == ("quotes", "otc") and c[2] != THU]
        assert len(wasted) == 1 and stats["errors"]
    # 同一天問了 3 次就不再問（之後一天只問一次）
    assert ex.calls.count(("quotes", "otc", first)) == 0
    status = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert status["missing"][f"quotes/otc/{first:%Y%m%d}"] == [3, "2026-10-08"]


def test_one_market_missing_past_days_stops_after_streak(tmp_path, small):
    """櫃買中心過去的日子都回空的、證交所有：連續 2 天就停，不會把每次的次數用完。"""
    ex = FakeExchange(DAYS)
    ex.missing |= {("quotes", "otc", d) for d in _past(DAYS, THU)}
    store = Store(tmp_path)
    stats, _ = run(store, ex, at(THU, 22))
    asked = [c for c in ex.calls if c[:2] == ("quotes", "otc") and c[2] != THU]
    assert len(asked) == 2 and len([e for e in stats["errors"] if "那天有開盤" in e]) == 2
    assert store.closed() == set()                              # 證交所有開盤：不是休市
    assert len(store.dates("quotes", "tse")) == 6               # 上市照樣補齊


def test_holiday_needs_both_markets_even_across_runs(tmp_path, small):
    """只有一個市場說沒有、另一個這次沒問到（被封鎖）：先不記休市；下次另一個市場有資料就補回來。"""
    ex = FakeExchange(DAYS + [FRI])
    ex.fail["www.twse.com.tw"] = TwseError("www.twse.com.tw 回應 HTTP 307（連線太頻繁被暫時封鎖）")
    ex.missing.add(("quotes", "otc", FRI))                      # 櫃買中心這次剛好回空的
    store = Store(tmp_path)
    stats, _ = run(store, ex, at(SAT, 10))
    assert FRI not in store.closed() and stats["closed_added"] == []
    assert not stats["confirmed"]
    ex.fail.clear()
    ex.missing.clear()
    run(store, ex, at(SAT, 10, 15))
    assert store.has("quotes", "tse", FRI) and store.has("quotes", "otc", FRI) and store.closed() == set()


def test_holiday_confirmed_by_second_market_in_a_later_run(tmp_path, small):
    ex = FakeExchange(DAYS)                                     # 10/9 休市
    ex.fail["www.twse.com.tw"] = TwseError("www.twse.com.tw 回應 HTTP 307（連線太頻繁被暫時封鎖）")
    store = Store(tmp_path)
    run(store, ex, at(SAT, 10))
    assert FRI not in store.closed()
    ex.fail.clear()
    run(store, ex, at(SAT, 10, 15))
    run(store, ex, at(SAT, 10, 30))
    assert store.closed() == {FRI}
    assert ex.calls.count(("quotes", "otc", FRI)) == 1          # 等證交所回答時，不再重問櫃買中心
    assert ex.calls.count(("quotes", "tse", FRI)) == 2          # 第一次被封鎖、第二次回答沒有


# ---------------------------------------------------------------- 審查修正：格式不對的快取檔
@pytest.mark.parametrize("content", ["{}", "null", "[]", '{"date": "2026-10-07", "rows": {"2330": {}}}',
                                     '{"date": "2026-10-08", "rows": {}}'])
def test_wrong_shape_cache_file_is_refetched_in_the_same_run(tmp_path, small, content):
    store = Store(tmp_path)
    run(store, FakeExchange(DAYS), at(THU, 22))
    store.path("quotes", "tse", THU).write_text(content, encoding="utf-8")
    store.path("insti", "otc", THU).write_text(content.replace("2026-10-08", "2026-10-09"), encoding="utf-8")
    ex = FakeExchange(DAYS)
    stats, _ = run(Store(tmp_path), ex, at(THU, 22, 15))
    assert set(ex.calls) == {("quotes", "tse", THU), ("insti", "otc", THU)} and stats["changed"]
    summary, stocks, _ = tw_build.assemble(Store(tmp_path), at(THU, 22, 15))
    assert summary["date"] == "2026-10-08" and any(s["code"] == "2330" for s in stocks)


def test_wrong_shape_cache_file_is_dropped_when_building(tmp_path, small):
    store = Store(tmp_path)
    run(store, FakeExchange(DAYS), at(THU, 22))
    store.path("quotes", "tse", THU).write_text("{}", encoding="utf-8")
    summary, stocks, _ = tw_build.assemble(Store(tmp_path), at(THU, 22))
    assert summary["date"] == "2026-10-07" and any(s["code"] == "2330" for s in stocks)
    assert not store.path("quotes", "tse", THU).exists()          # 刪掉，下次重抓


# ---------------------------------------------------------------- 審查修正：抓不到時不能假裝剛更新
class Down:
    """兩個網站都連不上。"""

    def __init__(self):
        self.calls = 0

    def _get_json(self, url, params):
        self.calls += 1
        raise TwseError(f"{urlparse(url).hostname} 回應 HTTP 500")


def test_failed_fetch_keeps_last_confirmed_time_and_says_data_is_behind(tmp_path, small):
    cache = tmp_path / "cache"
    tw_build.build(tmp_path / "a", cache_dir=cache, now=at(THU, 22), client=FakeExchange(DAYS), sources=SRC,
                   max_per_host=100, log=lambda *_: None)
    tue = date(2026, 10, 13)
    r = tw_build.build(tmp_path / "b", cache_dir=cache, now=at(tue, 16), client=Down(), sources=SRC,
                       log=lambda *_: None)
    assert r["update"]["errors"] and not r["update"]["confirmed"]
    summary = json.loads((tmp_path / "b" / "data" / "summary.json").read_text(encoding="utf-8"))
    assert summary["updated_utc"] == "2026-10-08T14:00:00Z"            # 上一次確認的時間，不是現在
    assert summary["notes"][0] == "行情還沒抓到 10/13 的，先顯示 10/08 的"
    # GitHub Actions 抓資料那步失敗、改用快取重新產生
    tw_build.build(tmp_path / "c", cache_dir=cache, now=at(tue, 16, 5), fetch=False, fetch_failed=True,
                   log=lambda *_: None)
    summary = json.loads((tmp_path / "c" / "data" / "summary.json").read_text(encoding="utf-8"))
    assert summary["notes"][:2] == [tw_build.FETCH_FAILED, "行情還沒抓到 10/13 的，先顯示 10/08 的"]
    assert summary["updated_utc"] == "2026-10-08T14:00:00Z"


@pytest.mark.parametrize("now", [at(FRI, 16), at(THU, 14, 5)])
def test_no_false_alarm_when_exchange_has_nothing_newer(tmp_path, small, now):
    """平日休市還沒發現（10/9）、或今天的行情還沒公布：交易所確認過沒有更新的，不會說「還沒抓到」。"""
    ex = FakeExchange(DAYS)
    if now.date() == THU:
        ex.missing |= {("quotes", m, THU) for m in ("tse", "otc")}
    cache = tmp_path / "cache"
    r = tw_build.build(tmp_path / "s", cache_dir=cache, now=now, client=ex, sources=SRC, max_per_host=100,
                       log=lambda *_: None)
    assert r["update"]["confirmed"]
    summary = json.loads((tmp_path / "s" / "data" / "summary.json").read_text(encoding="utf-8"))
    assert summary["updated_utc"] == now.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert not any("行情還沒抓到" in n for n in summary["notes"])


# ---------------------------------------------------------------- 審查修正：沒變動就不存快取
def test_changed_flag_only_when_cache_data_changes(tmp_path, small, monkeypatch):
    calls = []
    r = tw_build.build(tmp_path / "s", cache_dir=tmp_path / "cache", now=at(THU, 22), client=FakeExchange(DAYS),
                       sources=SRC, max_per_host=100, log=lambda *_: None, on_change=lambda: calls.append(1))
    assert r["update"]["changed"] and calls == [1]                 # 第一次有變動時通知一次
    ex = FakeExchange(DAYS)
    r = tw_build.build(tmp_path / "s", cache_dir=tmp_path / "cache", now=at(THU, 22, 15), client=ex,
                       sources=SRC, log=lambda *_: None, on_change=lambda: calls.append(2))
    assert ex.calls == [] and not r["update"]["changed"] and calls == [1]
    status = json.loads((tmp_path / "cache" / "status.json").read_text(encoding="utf-8"))
    assert status["checked_utc"] == "2026-10-08T14:15:00Z"         # 抓資料的紀錄每次都更新（另外存）

    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    tw_build.main(["--out", str(tmp_path / "t"), "--no-fetch", "--cache", str(tmp_path / "cache")])
    assert not out.exists()                                         # 沒變動：不存快取
    Store(tmp_path / "cache").path("quotes", "tse", THU).write_text("{}", encoding="utf-8")
    tw_build.main(["--out", str(tmp_path / "t"), "--no-fetch", "--fetch-failed", "--cache", str(tmp_path / "cache")])
    assert out.read_text(encoding="utf-8") == "changed=true\n"      # 刪了壞掉的檔：要存


def test_prune_keeps_only_what_is_read(tmp_path, monkeypatch):
    monkeypatch.setattr(contract, "HIST_MAX_DAYS", 30)
    monkeypatch.setattr(contract, "CHIPS_MAX_DAYS", 3)
    store = Store(tmp_path)
    for d in weekdays(date(2026, 7, 1), THU):
        for ds in ("valuation", "insti_totals", "insti"):
            store.save(ds, "tse", d, {"date": d.isoformat(), "rows": {"2330": {}}, "total": 1})
    store.save_status({"missing": {"insti/tse/20260701": [1, "2026-07-02"], "quotes/tse/20261007": [1, "2026-10-08"]}})
    run(store, FakeExchange([]), at(THU, 22), max_per_host=0)
    assert store.dates("valuation", "tse") == trading_days(THU, 3, set())[::-1]     # 只用最新一天：不多留
    assert store.dates("insti_totals", "tse") == trading_days(THU, 3, set())[::-1]
    assert store.dates("insti", "tse")[0] == date(2026, 9, 7)      # 10/6 往前多留 30 天（9/6 是週日）
    assert list(store.status()["missing"]) == ["quotes/tse/20261007"]                  # 太舊的紀錄清掉
