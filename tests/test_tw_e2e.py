"""台股總覽從頭到尾的測試：真實格式的回應 → tw_market.sources 解析 → 快取 → 組合 → 寫出網站 → 格式檢查。

假的交易所用 tests/fixtures/tw/ 的真實回應（2026/10/08 交易日、10/09 休市）：
- 每個請求的網址和參數都要和 sources.py 的網址函式產生的一模一樣，否則測試失敗（介面對不上）
- 10/08 回原本的資料；10/09 回休市的格式；10/08 以前的平日回「把 10/08 的資料改成那天的日期」
- 其他日子（週末、未來）回「沒有資料」
"""
import copy
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tw_market import contract
from tw_market import sources as S
from tw_market.build import build
from tw_market.contract import STOCK_FIELDS

TPE = ZoneInfo("Asia/Taipei")
FIX = Path(__file__).parent / "fixtures" / "tw"
REAL, HOLIDAY = date(2026, 10, 8), date(2026, 10, 9)
NO_DATA_TWSE = {"stat": "很抱歉，沒有符合條件的資料!"}

DATED = ["twse_quotes", "twse_insti", "twse_insti_totals", "twse_margin", "twse_valuation",
         "tpex_quotes", "tpex_insti", "tpex_margin", "tpex_valuation", "tpex_index_month"]
UNDATED = ["twse_industry", "tpex_industry"]


def _fx(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def _shift(js, d: date):
    """把 10/08 的回應改成 d 那天（數字不變，只換日期）。"""
    text = json.dumps(js, ensure_ascii=False)
    text = text.replace('"20261008"', f'"{d:%Y%m%d}"')
    text = text.replace('"115/10/08"', f'"{d.year - 1911}/{d:%m/%d}"')
    return json.loads(text)


def _empty_tpex(name, d: date):
    """櫃買中心「沒有資料」的格式：stat ok、第一個表格沒有資料列。"""
    js = copy.deepcopy(_fx(f"{name}_20261008.json"))
    js["date"] = f"{d:%Y%m%d}"
    for t in js["tables"]:
        t["data"] = []
        t["date"] = f"{d.year - 1911}/{d:%m/%d}"
    return js


class FixtureExchange:
    """依網址＋參數回傳 tests/fixtures/tw 的真實回應。"""

    def __init__(self):
        self.calls = []        # (網址函式名稱, 日期)

    @staticmethod
    def _match(url, params):
        for name in UNDATED:
            if getattr(S, name)() == (url, params):
                return name, None
        raw = str(params.get("date") or params.get("dayDate") or "").replace("/", "")
        d = date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
        for name in DATED:
            if getattr(S, name)(d) == (url, params):
                return name, d
        raise AssertionError(f"sources.py 沒有這種網址：{url} {params}")

    def _get_json(self, url, params):
        name, d = self._match(url, params)
        self.calls.append((name, d))
        if d is None:
            return _fx(f"{name}.json")
        if name == "tpex_index_month":
            js = _fx("tpex_index_month_20261008.json")
            if (d.year, d.month) != (2026, 10):
                return _empty_tpex(name, d)
            js["tables"][0]["data"] = [r for r in js["tables"][0]["data"] if r[0] <= f"{d:%Y/%m/%d}"]
            return js
        if d == REAL:
            return _fx(f"{name}_20261008.json")
        if d < REAL and d.weekday() < 5:
            return _shift(_fx(f"{name}_20261008.json"), d)
        if d == HOLIDAY and (FIX / f"{name}_20261009_holiday.json").exists():
            return _fx(f"{name}_20261009_holiday.json")
        return copy.deepcopy(NO_DATA_TWSE) if name.startswith("twse") else _empty_tpex(name, d)


def _build(tmp_path, now, ex=None, **kw):
    ex = ex or FixtureExchange()
    logs = []
    r = build(tmp_path / "site", cache_dir=tmp_path / "cache", now=now, client=ex, log=logs.append, **kw)
    return ex, r, logs


def _read(tmp_path):
    data = tmp_path / "site" / "data"
    summary = json.loads((data / "summary.json").read_text(encoding="utf-8"))
    stocks = json.loads((data / "stocks.json").read_text(encoding="utf-8"))
    rows = {r[0]: dict(zip(STOCK_FIELDS, r)) for r in stocks["rows"]}
    return data, summary, stocks, rows


def _check_files(data, summary, stocks):
    assert contract.check_summary(summary) == []
    assert contract.check_stocks(stocks) == []
    hist = sorted((data / "hist").glob("*.json"))
    assert hist
    for p in hist:
        h = json.loads(p.read_text(encoding="utf-8"))
        assert contract.check_hist(h) == [], p.name
    return {p.stem for p in hist}


def test_saturday_after_holiday(tmp_path):
    """週六中午：10/09 休市（自己發現），顯示 10/08 的完整資料。"""
    ex, r, logs = _build(tmp_path, datetime(2026, 10, 10, 12, 0, tzinfo=TPE))
    assert r["date"] == "2026-10-08"
    assert r["update"]["errors"] == []
    assert r["update"]["closed_added"] == ["2026-10-09"]
    assert all(d is None or d <= HOLIDAY for _, d in ex.calls)
    assert json.loads((tmp_path / "cache" / "calendar.json").read_text())["closed"] == ["2026-10-09"]

    data, summary, stocks, rows = _read(tmp_path)
    hist_codes = _check_files(data, summary, stocks)
    assert stocks["date"] == "2026-10-08"

    tse, otc = summary["markets"]["tse"], summary["markets"]["otc"]
    assert tse["index"] == {"name": "加權指數", "close": 49313.44, "change": -492.93, "change_pct": -0.99}
    assert (tse["value"], tse["volume"]) == (924_509_097_873, 10_437_289)
    assert otc["index"] == {"name": "櫃買指數", "close": 426.71, "change": -3.75, "change_pct": -0.87}
    assert (otc["value"], otc["volume"]) == (256_231_761_006, 1_105_739)
    for m in (tse, otc):
        assert all(isinstance(m[k], int) for k in ("up", "down", "flat", "limit_up", "limit_down"))
    # 上市股票：1101 漲、1103 平、2317 / 2330 跌；1341 不比價、4581 沒成交、9105 存託憑證不算
    assert (tse["up"], tse["down"], tse["flat"]) == (1, 2, 1)
    assert (tse["limit_up"], tse["limit_down"]) == (0, 0)
    # 上櫃股票：5483 / 6488 跌、8299 平；2073 沒成交不算
    assert (otc["up"], otc["down"], otc["flat"]) == (0, 2, 1)
    assert summary["institutional"] == {
        "date": "2026-10-08", "otc": None,
        "tse": {"foreign": -75_852_293_490, "trust": 3_008_930_013, "dealer": -18_731_261_604,
                "total": -91_574_625_081}}
    assert summary["chips_date"] == summary["margin_date"] == summary["valuation_date"] == "2026-10-08"
    assert isinstance(summary["history_days"], int) and summary["history_days"] >= 2
    assert not any("才公布" in n for n in summary["notes"])

    assert rows["2330"] == {
        "code": "2330", "name": "台積電", "market": "tse", "type": "stock", "industry": "半導體業",
        "close": 2550.0, "change": -35.0, "change_pct": -1.35, "open": 2550.0, "high": 2575.0, "low": 2550.0,
        "volume": 23145, "value": 59_167_379_273, "pe": 29.56, "dividend_yield": 0.86, "pb": 10.28,
        "foreign": -12303, "trust": 793, "dealer": 510, "inst_total": -11000,
        "margin_balance": 31586, "margin_change": 1308, "short_balance": 45, "short_change": -5, "limit": 0}
    assert rows["6488"] == {
        "code": "6488", "name": "環球晶", "market": "otc", "type": "stock", "industry": "半導體業",
        "close": 1130.0, "change": -85.0, "change_pct": -7.0, "open": 1180.0, "high": 1195.0, "low": 1095.0,
        "volume": 20608, "value": 23_258_740_205, "pe": 54.85, "dividend_yield": 0.68, "pb": 5.59,
        "foreign": -1301, "trust": -989, "dealer": 4, "inst_total": -2286,
        "margin_balance": 17036, "margin_change": -933, "short_balance": 500, "short_change": -65, "limit": 0}
    assert rows["0050"]["type"] == "etf" and rows["0050"]["industry"] == "ETF"
    assert rows["006201"]["market"] == "otc" and rows["006201"]["type"] == "etf"
    assert rows["1341"]["change"] is None and rows["1341"]["change_pct"] is None   # 不比價
    assert rows["4581"]["close"] is None                                         # 沒成交
    assert not {"1101B", "2881A", "020000", "8349A", "72479U"} & set(rows)      # 特別股、權證不收

    h = json.loads((data / "hist" / "2330.json").read_text(encoding="utf-8"))
    assert h["dates"][-1] == "2026-10-08" and h["close"][-1] == 2550.0 and h["volume"][-1] == 23145
    assert "2026-10-09" not in h["dates"]
    assert len(h["dates"]) == summary["history_days"]
    assert h["inst"]["dates"][-1] == "2026-10-08" and h["inst"]["foreign"][-1] == -12303
    assert h["margin"]["dates"][-1] == "2026-10-08" and h["margin"]["margin_balance"][-1] == 31586
    h = json.loads((data / "hist" / "6488.json").read_text(encoding="utf-8"))
    assert h["close"][-1] == 1130.0 and h["inst"]["trust"][-1] == -989
    assert "4581" not in hist_codes and "2073" not in hist_codes     # 從來沒有收盤價
    assert (tmp_path / "site" / "index.html").is_file()


def test_thursday_afternoon_before_margin(tmp_path):
    """週四 16:00：行情、本益比、三大法人已公布，融資融券（21:00）先用前一天的。"""
    ex, r, logs = _build(tmp_path, datetime(2026, 10, 8, 16, 0, tzinfo=TPE))
    assert r["date"] == "2026-10-08" and r["update"]["errors"] == []
    assert r["update"]["closed_added"] == []
    assert all(d is None or d <= REAL for _, d in ex.calls)
    assert ("twse_margin", REAL) not in ex.calls and ("tpex_margin", REAL) not in ex.calls

    data, summary, stocks, rows = _read(tmp_path)
    _check_files(data, summary, stocks)
    assert summary["chips_date"] == "2026-10-08" and summary["valuation_date"] == "2026-10-08"
    assert summary["margin_date"] == "2026-10-07"
    assert "融資融券晚上 9 點後才公布，先顯示 10/07 的" in summary["notes"]
    assert summary["institutional"]["date"] == "2026-10-08"
    assert summary["markets"]["tse"]["index"]["close"] == 49313.44
    assert rows["2330"]["foreign"] == -12303 and rows["2330"]["margin_balance"] == 31586
    h = json.loads((data / "hist" / "2330.json").read_text(encoding="utf-8"))
    assert h["margin"]["dates"][-1] == "2026-10-07" and h["inst"]["dates"][-1] == "2026-10-08"

    # 同一個快取，晚上 9 點半再跑一次：只補 10/08 的融資融券
    ex2, r2, _ = _build(tmp_path, datetime(2026, 10, 8, 21, 30, tzinfo=TPE))
    assert ("twse_margin", REAL) in ex2.calls and ("tpex_margin", REAL) in ex2.calls
    assert ("twse_quotes", REAL) not in ex2.calls and ("twse_insti", REAL) not in ex2.calls
    _, summary, _, rows = _read(tmp_path)
    assert summary["margin_date"] == "2026-10-08"
    assert not any("融資融券" in n and "才公布" in n for n in summary["notes"])


def test_no_fetch_rebuild_uses_cache_only(tmp_path):
    """抓資料那步失敗時，GitHub Actions 改用 --no-fetch：完全不連網，產生一樣的網頁。"""
    _build(tmp_path, datetime(2026, 10, 10, 12, 0, tzinfo=TPE))
    before = (tmp_path / "site" / "data" / "stocks.json").read_text(encoding="utf-8")

    class NoNetwork:
        def _get_json(self, url, params):
            raise AssertionError("不應該連網")

    r = build(tmp_path / "site", cache_dir=tmp_path / "cache", now=datetime(2026, 10, 10, 12, 15, tzinfo=TPE),
              client=NoNetwork(), fetch=False, log=lambda *_: None)
    assert r["date"] == "2026-10-08" and r["update"] is None
    assert (tmp_path / "site" / "data" / "stocks.json").read_text(encoding="utf-8") == before


@pytest.mark.parametrize("name", DATED)
def test_fixture_exchange_serves_every_source(name):
    """假的交易所認得 sources.py 的每一個網址（介面對得上）。"""
    ex = FixtureExchange()
    url, params = getattr(S, name)(REAL)
    assert ex._match(url, params) == (name, REAL)
    parser = getattr(S, "parse_" + name)
    out = parser(ex._get_json(url, params))
    assert out is not None
    later = REAL + timedelta(days=7)
    assert ex._match(*getattr(S, name)(later)) == (name, later)
