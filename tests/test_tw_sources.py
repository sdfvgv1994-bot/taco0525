"""台股總覽：證交所、櫃買中心盤後資料的解析測試。

tests/fixtures/tw/ 的 JSON 是 2026/10/08（交易日）、10/09（休市）的真實回應，只留幾筆代表性的資料列
（2330、0050、00878、00631L、沒成交、不比價、平盤、特別股 / 權證 / 存託憑證等要濾掉的代號），
其他欄位、表格結構都沒改。期望值都是從原始資料列手算的（股數 ÷ 1000 四捨五入成張）。
"""
import copy
import json
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

import pytest

from tw_market import sources as S
from tw_market.contract import classify

FIX = Path(__file__).parent / "fixtures" / "tw"


def fx(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def pick(row, *keys):
    return tuple(row[k] for k in keys)


D = date(2026, 10, 8)


# ---------------------------------------------------------------- 網址
@pytest.mark.parametrize("fn,url", [
    (S.twse_quotes, "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date=20261008&type=ALLBUT0999&response=json"),
    (S.twse_insti, "https://www.twse.com.tw/rwd/zh/fund/T86?date=20261008&selectType=ALLBUT0999&response=json"),
    (S.twse_insti_totals, "https://www.twse.com.tw/rwd/zh/fund/BFI82U?type=day&dayDate=20261008&response=json"),
    (S.twse_margin, "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date=20261008&selectType=ALL&response=json"),
    (S.twse_valuation, "https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d?date=20261008&selectType=ALL&response=json"),
    (S.tpex_quotes, "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date=2026%2F10%2F08&id=&response=json"),
    (S.tpex_insti, "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade?type=Daily&sect=EW&date=2026%2F10%2F08&response=json"),
    (S.tpex_margin, "https://www.tpex.org.tw/www/zh-tw/margin/balance?date=2026%2F10%2F08&id=&response=json"),
    (S.tpex_valuation, "https://www.tpex.org.tw/www/zh-tw/afterTrading/peQryDate?date=2026%2F10%2F08&response=json"),
    (S.tpex_index_month, "https://www.tpex.org.tw/www/zh-tw/indexInfo/inx?date=2026%2F10%2F08&response=json"),
])
def test_urls_match_probed_requests(fn, url):
    """和實測成功的網址一字不差。"""
    u, params = fn(D)
    assert f"{u}?{urlencode(params)}" == url


def test_industry_urls():
    assert S.twse_industry() == ("https://openapi.twse.com.tw/v1/opendata/t187ap03_L", {})
    assert S.tpex_industry() == ("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O", {})


# ---------------------------------------------------------------- 小工具
@pytest.mark.parametrize("shares,lots", [
    (0, 0), (300, 0), (499, 0), (500, 1), (1499, 1), (1500, 2), (2500, 3), (-500, -1), (-2500, -3),
    (23_145_193, 23145), (-12_302_987, -12303), ("20,500", 21), ("-136,500", -137), ("--", None), (None, None),
])
def test_lots_round_half_away_from_zero(shares, lots):
    """.5 張一律往遠離 0 進位（不是銀行家捨入），買超、賣超對稱。"""
    assert S._lots(shares) == lots


@pytest.mark.parametrize("s,iso", [("20261008", "2026-10-08"), ("115/10/08", "2026-10-08"),
                                   ("2026/10/01", "2026-10-01"), ("115/6/1", "2026-06-01")])
def test_iso_dates(s, iso):
    assert S._iso(s) == iso


@pytest.mark.parametrize("s", ["", "115/10", "2026-13-01", "115/02/30", None])
def test_iso_bad_dates(s):
    with pytest.raises(ValueError):
        S._iso(s)


@pytest.mark.parametrize("sign,diff,expect", [
    ("<p style= color:green>-</p>", "35.00", -35.0), ("<p style= color:red>+</p>", "0.20", 0.2),
    ("<p> </p>", "0.00", 0.0), ("<p>X</p>", "0.00", None), ("<p style ='color:green'>-</p>", "492.93", -492.93),
    ("<p style= color:green>-</p>", "0.00", 0.0), ("<p>?</p>", "1.00", None), ("<p>+</p>", "--", None),
])
def test_signed_change(sign, diff, expect):
    v = S._signed(sign, diff)
    assert v == expect
    if v == 0:
        assert str(v) == "0.0"   # 不會出現 -0.0


@pytest.mark.parametrize("code,kind", [("2330", "stock"), ("9105", "stock"), ("0050", "etf"), ("00878", "etf"),
                                       ("00631L", "etf"), ("00625K", "etf"), ("006201", "etf"), ("00679B", "etf"),
                                       ("2881A", None), ("910322", None), ("01001T", None), ("020000", None),
                                       ("72479U", None), ("8349A", None)])
def test_classify_codes_seen_in_data(code, kind):
    assert classify(code) == kind


# ---------------------------------------------------------------- 休市、還沒公布
@pytest.mark.parametrize("fn,name", [
    (S.parse_twse_quotes, "twse_quotes_20261009_holiday.json"),      # stat 有「!」
    (S.parse_twse_insti, "twse_insti_20261009_holiday.json"),
    (S.parse_twse_margin, "twse_margin_20261009_holiday.json"),      # stat 沒有「!」
    (S.parse_tpex_quotes, "tpex_quotes_20261009_holiday.json"),      # stat ok、data 是空的
    (S.parse_tpex_insti, "tpex_insti_20261009_holiday.json"),
    (S.parse_tpex_margin, "tpex_margin_20261009_holiday.json"),
])
def test_holiday_samples_are_none(fn, name):
    assert fn(fx(name)) is None


@pytest.mark.parametrize("fn", [S.parse_twse_insti_totals, S.parse_twse_valuation, S.parse_twse_quotes,
                                S.parse_twse_insti, S.parse_twse_margin])
@pytest.mark.parametrize("stat", ["很抱歉，沒有符合條件的資料!", "很抱歉，沒有符合條件的資料"])
def test_twse_no_data_is_none(fn, stat):
    assert fn({"stat": stat}) is None


@pytest.mark.parametrize("fn", [S.parse_tpex_valuation, S.parse_tpex_index_month, S.parse_tpex_quotes,
                                S.parse_tpex_insti, S.parse_tpex_margin])
def test_tpex_empty_table_is_none(fn):
    js = fx("tpex_margin_20261009_holiday.json")   # 休市的形狀：stat ok、tables[0].data == []
    assert fn(js) is None


# ---------------------------------------------------------------- 上市行情 MI_INDEX
def test_twse_quotes():
    q = S.parse_twse_quotes(fx("twse_quotes_20261008.json"))
    assert q["date"] == "2026-10-08"
    assert q["index"] == {"name": "加權指數", "close": 49313.44, "change": -492.93, "change_pct": -0.99}
    # 大盤統計的「總計(1~15)」：和證交所 FMTQIK 公布的 10/08 成交金額、成交股數一致
    assert q["totals"] == {"value": 924_509_097_873, "volume": 10_437_289}
    r = q["rows"]
    assert r["2330"] == {"name": "台積電", "open": 2550.0, "high": 2575.0, "low": 2550.0, "close": 2550.0,
                         "change": -35.0, "volume": 23145, "value": 59_167_379_273, "trades": 141_976,
                         "pe": 29.56}
    assert pick(r["2317"], "close", "change", "volume", "pe") == (249.0, -3.0, 40251, 16.41)
    assert pick(r["0050"], "close", "change", "volume", "pe") == (114.95, -1.1, 99878, None)
    assert pick(r["00878"], "close", "change", "volume") == (34.99, -0.23, 39642)
    assert pick(r["00631L"], "close", "change", "volume") == (40.9, -0.85, 148412)
    assert pick(r["1101"], "change", "volume") == (0.2, 33061)               # 上漲
    assert pick(r["1103"], "change", "pe") == (0.0, 9.81)                    # 平盤
    assert r["1341"]["close"] == 59.1 and r["1341"]["change"] is None         # X 不比價
    assert r["00400A"]["close"] == 16.15 and r["00400A"]["change"] is None    # ETF 不比價
    assert r["9105"]["change"] == -0.26                                       # 4 位數存託憑證照收


def test_twse_quotes_untraded():
    r = S.parse_twse_quotes(fx("twse_quotes_20261008.json"))["rows"]
    assert r["00625K"] == {"name": "富邦上証+R", "open": None, "high": None, "low": None, "close": None,
                           "change": None, "volume": 0, "value": 0, "trades": 0, "pe": None}
    # 只有零股成交 300 股：收盤是 '--'，成交量 0 張，但成交金額照實記
    assert r["4581"]["close"] is None and r["4581"]["change"] is None
    assert pick(r["4581"], "volume", "value", "trades", "pe") == (0, 14639, 2, 11.81)


def test_twse_quotes_filters_other_products():
    r = S.parse_twse_quotes(fx("twse_quotes_20261008.json"))["rows"]
    assert set(r) == {"0050", "00400A", "00625K", "00631L", "00878", "1101", "1103", "1341", "2317", "2330",
                      "4581", "9105"}
    assert {classify(c) for c in r} == {"stock", "etf"}


def test_twse_quotes_without_index_tables_still_parses():
    js = fx("twse_quotes_20261008.json")
    js["tables"] = [t for t in js["tables"] if "每日收盤行情" in str(t.get("title"))]
    q = S.parse_twse_quotes(js)
    assert q["index"] is None and q["totals"] is None and q["rows"]["2330"]["close"] == 2550.0


# ---------------------------------------------------------------- 上櫃行情 dailyQuotes
def test_tpex_quotes():
    q = S.parse_tpex_quotes(fx("tpex_quotes_20261008.json"))
    assert q["date"] == "2026-10-08" and q["index"] is None
    # 櫃買中心公布的總成交金額 / 股數（只算上櫃股票）
    assert q["totals"] == {"value": 256_231_761_006, "volume": 1_105_739}
    r = q["rows"]
    assert r["6488"] == {"name": "環球晶", "open": 1180.0, "high": 1195.0, "low": 1095.0, "close": 1130.0,
                         "change": -85.0, "volume": 20608, "value": 23_258_740_205, "trades": 49_278, "pe": None}
    assert pick(r["5483"], "close", "change", "volume") == (216.5, -7.5, 21543)
    assert pick(r["006201"], "name", "change", "volume") == ("元大富櫃50", -0.66, 243)
    assert pick(r["00679B"], "change", "volume") == (0.05, 16795)
    assert r["00411A"]["change"] == -0.1
    assert r["8299"]["change"] == 0.0
    assert r["2073"] == {"name": "雄順", "open": None, "high": None, "low": None, "close": None, "change": None,
                         "volume": 0, "value": 7449, "trades": 2, "pe": None}
    assert set(r) == {"00411A", "006201", "00679B", "2073", "5483", "6488", "8299"}   # 特別股、ETN、權證濾掉


def test_tpex_quotes_ex_rights_has_no_change():
    r = S.parse_tpex_quotes(fx("tpex_quotes_20260601.json"))["rows"]
    assert r["3709"]["close"] == 86.7 and r["3709"]["change"] is None    # 除權
    assert r["4527"]["close"] == 42.5 and r["4527"]["change"] is None    # 除息
    assert r["006201"]["change"] == 0.05 and r["6488"]["change"] == -65.0


def test_tpex_quotes_includes_managed_stock_table():
    js = fx("tpex_quotes_20261008.json")
    row = copy.deepcopy(js["tables"][0]["data"][5])
    row[0], row[1] = "9999", "管理測試"
    js["tables"][1]["data"].append(row)
    assert S.parse_tpex_quotes(js)["rows"]["9999"]["close"] == 1130.0


# ---------------------------------------------------------------- 三大法人
def test_twse_insti():
    p = S.parse_twse_insti(fx("twse_insti_20261008.json"))
    assert p["date"] == "2026-10-08"
    r = p["rows"]
    # 2330：外資 -12,302,987 股、投信 792,863、自營商 509,848（自行 241,100 + 避險 268,748）
    assert r["2330"] == {"foreign": -12303, "trust": 793, "dealer": 510, "total": -11000}
    assert r["2317"] == {"foreign": -25605, "trust": 147, "dealer": -70, "total": -25528}
    assert r["0050"] == {"foreign": -51509, "trust": 0, "dealer": -12764, "total": -64273}
    assert r["00631L"] == {"foreign": -15119, "trust": 0, "dealer": -48978, "total": -64097}
    assert r["9105"] == {"foreign": -7597, "trust": 0, "dealer": -191, "total": -7788}
    assert r["6225"]["foreign"] == 21 and r["8438"]["foreign"] == -137     # 剛好 .5 張
    assert set(r) == {"0050", "00631L", "00878", "1216", "2317", "2330", "6225", "8438", "9105"}


def test_insti_total_is_sum_of_rounded_parts():
    """合計 = 四捨五入後的三項相加，所以偶爾會和「合計股數直接四捨五入」差 1 張。"""
    r = S.parse_twse_insti(fx("twse_insti_20261008.json"))["rows"]
    for v in r.values():
        assert v["total"] == v["foreign"] + v["trust"] + v["dealer"]
    assert r["00878"] == {"foreign": -4596, "trust": 0, "dealer": -27908, "total": -32504}   # 合計 -32,504,605 股
    assert r["1216"] == {"foreign": 4818, "trust": 1911, "dealer": 387, "total": 7116}       # 合計 7,116,646 股


def test_tpex_insti():
    p = S.parse_tpex_insti(fx("tpex_insti_20261008.json"))
    assert p["date"] == "2026-10-08"
    r = p["rows"]
    # 6488：外資 -1,301,158、投信 -988,900、自營商 4,250（自行 60,305 + 避險 -56,055）
    assert r["6488"] == {"foreign": -1301, "trust": -989, "dealer": 4, "total": -2286}
    assert r["5483"] == {"foreign": -4001, "trust": 198, "dealer": -700, "total": -4503}
    assert r["006201"] == {"foreign": 19, "trust": -22, "dealer": -10, "total": -13}
    assert r["00411A"] == {"foreign": -316, "trust": 0, "dealer": -3195, "total": -3511}   # -315,500 股 → -316
    assert r["00772B"] == {"foreign": 975, "trust": 0, "dealer": -157, "total": 818}       # -156,500 股 → -157
    assert r["2937"] == {"foreign": -1, "trust": 0, "dealer": 0, "total": -1}              # -500 股 → -1
    assert r["2718"] == {"foreign": 0, "trust": 0, "dealer": 0, "total": 0}                # 每項都不到半張
    assert "020001" not in r


def test_twse_insti_totals():
    p = S.parse_twse_insti_totals(fx("twse_insti_totals_20261008.json"))
    assert p == {"date": "2026-10-08", "foreign": -75_852_293_490, "trust": 3_008_930_013,
                 "dealer": -2_131_785_955 - 16_599_475_649, "total": -91_574_625_081}
    assert p["foreign"] + p["trust"] + p["dealer"] == p["total"]


# ---------------------------------------------------------------- 融資融券
def test_twse_margin():
    p = S.parse_twse_margin(fx("twse_margin_20261008.json"))
    assert p["date"] == "2026-10-08"
    r = p["rows"]
    assert r["2330"] == {"margin_balance": 31586, "margin_change": 1308, "short_balance": 45, "short_change": -5}
    assert r["0050"] == {"margin_balance": 40109, "margin_change": -8249, "short_balance": 661, "short_change": -26}
    assert r["00631L"] == {"margin_balance": 184221, "margin_change": 4297, "short_balance": 20078,
                           "short_change": -1122}
    assert r["9105"] == {"margin_balance": 46045, "margin_change": -29, "short_balance": 106, "short_change": -152}
    assert "910322" not in r and "912000" not in r


def test_tpex_margin():
    p = S.parse_tpex_margin(fx("tpex_margin_20261008.json"))
    assert p["date"] == "2026-10-08"
    r = p["rows"]
    assert r["6488"] == {"margin_balance": 17036, "margin_change": -933, "short_balance": 500, "short_change": -65}
    assert r["5483"] == {"margin_balance": 16430, "margin_change": -151, "short_balance": 513, "short_change": -51}
    assert r["00679B"] == {"margin_balance": 4440, "margin_change": -178, "short_balance": 5, "short_change": 0}
    assert set(r) == {"00411A", "006201", "00679B", "5483", "6488"}


# ---------------------------------------------------------------- 本益比、殖利率、淨值比
def test_twse_valuation():
    p = S.parse_twse_valuation(fx("twse_valuation_20261008.json"))
    assert p["date"] == "2026-10-08"
    r = p["rows"]
    assert r["2330"] == {"pe": 29.56, "dividend_yield": 0.86, "pb": 10.28}
    assert r["1101"] == {"pe": None, "dividend_yield": 3.11, "pb": 0.83}     # 本益比 '-'
    assert r["2321"] == {"pe": 640.0, "dividend_yield": 0.0, "pb": 4.1}      # 沒配股利：殖利率 0


def test_tpex_valuation():
    p = S.parse_tpex_valuation(fx("tpex_valuation_20261008.json"))
    assert p["date"] == "2026-10-08"
    r = p["rows"]
    assert r["6488"] == {"pe": 54.85, "dividend_yield": 0.68, "pb": 5.59}
    assert r["3086"] == {"pe": None, "dividend_yield": 0.0, "pb": 24.55}     # 本益比 'N/A'
    assert r["5314"]["dividend_yield"] == 116.46                             # 配股很多，交易所算出來就是這樣


# ---------------------------------------------------------------- 櫃買指數、產業別
def test_tpex_index_month():
    p = S.parse_tpex_index_month(fx("tpex_index_month_20261008.json"))
    assert list(p) == ["2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08"]
    assert p["2026-10-08"] == {"open": 428.67, "high": 428.67, "low": 420.56, "close": 426.71, "change": -3.75}
    assert p["2026-10-06"]["change"] == -1.62
    days = list(p)
    for a, b in zip(days, days[1:]):   # 漲跌 = 今天收盤 − 前一天收盤
        assert round(p[b]["close"] - p[a]["close"], 2) == p[b]["change"]


def test_industry():
    tse = S.parse_twse_industry(fx("twse_industry.json"))
    assert tse == {"1101": "水泥工業", "2317": "其他電子業", "2330": "半導體業", "9105": "存託憑證"}
    otc = S.parse_tpex_industry(fx("tpex_industry.json"))
    assert otc == {"1240": "農業科技業", "5483": "半導體業", "6488": "半導體業", "8299": "半導體業"}


def test_industry_unknown_code_is_none():
    js = fx("tpex_industry.json")
    js[0]["SecuritiesIndustryCode"] = "99"
    js[1]["SecuritiesIndustryCode"] = ""
    otc = S.parse_tpex_industry(js)
    assert otc["1240"] is None and otc["5483"] is None and otc["6488"] == "半導體業"


# ---------------------------------------------------------------- 格式不對 → ValueError
def _drop_field(js, name):
    t = js["tables"][8] if "tables" in js else js
    t["fields"] = ["改名了" if f == name else f for f in t["fields"]]
    return js


@pytest.mark.parametrize("fn,js", [
    (S.parse_twse_quotes, "<html>blocked</html>"),
    (S.parse_twse_quotes, {"stat": "查詢日期大於今日，請重新查詢!"}),
    (S.parse_twse_quotes, {"stat": "OK", "date": "20261008"}),                       # 沒有 tables
    (S.parse_twse_quotes, _drop_field(fx("twse_quotes_20261008.json"), "收盤價")),
    (S.parse_twse_insti, _drop_field(fx("twse_insti_20261008.json"), "投信買賣超股數")),
    (S.parse_twse_insti, {"stat": "OK", "date": "20261008", "fields": []}),
    (S.parse_twse_valuation, {"stat": "OK", "date": "20261008", "fields": ["證券代號"], "data": []}),
    (S.parse_tpex_quotes, {"stat": "ok"}),
    (S.parse_tpex_quotes, {"stat": "error", "tables": []}),
    (S.parse_tpex_insti, {"stat": "ok", "tables": [{"fields": ["代號"], "data": [["6488"]]}]}),
    (S.parse_tpex_index_month, {"stat": "ok", "tables": [{"fields": ["日期", "開市", "最高", "最低", "收市", "漲/跌"],
                                                          "data": [["明天", "1", "1", "1", "1", "0"]]}]}),
    (S.parse_twse_industry, []),
    (S.parse_tpex_industry, [{"代號": "6488"}]),
])
def test_unexpected_format_raises(fn, js):
    with pytest.raises(ValueError):
        fn(js)


def test_short_row_raises():
    js = fx("tpex_margin_20261008.json")
    js["tables"][0]["data"][0] = js["tables"][0]["data"][0][:5]
    with pytest.raises(ValueError):
        S.parse_tpex_margin(js)


def test_tpex_insti_column_shift_raises():
    js = fx("tpex_insti_20261008.json")
    js["tables"][0]["fields"] = js["tables"][0]["fields"][:-1]
    with pytest.raises(ValueError):
        S.parse_tpex_insti(js)


def test_twse_margin_needs_two_balance_groups():
    js = fx("twse_margin_20261008.json")
    js["tables"][1]["fields"][12] = "餘額"
    with pytest.raises(ValueError):
        S.parse_twse_margin(js)


def test_insti_totals_missing_row_raises():
    js = fx("twse_insti_totals_20261008.json")
    js["data"] = [r for r in js["data"] if r[0] != "合計"]
    with pytest.raises(ValueError):
        S.parse_twse_insti_totals(js)


# ---------------------------------------------------------------- 快取：原樣存成 JSON 再讀回來要一樣
@pytest.mark.parametrize("fn,name", [
    (S.parse_twse_quotes, "twse_quotes_20261008.json"), (S.parse_tpex_quotes, "tpex_quotes_20261008.json"),
    (S.parse_twse_insti, "twse_insti_20261008.json"), (S.parse_tpex_insti, "tpex_insti_20261008.json"),
    (S.parse_twse_insti_totals, "twse_insti_totals_20261008.json"),
    (S.parse_twse_margin, "twse_margin_20261008.json"), (S.parse_tpex_margin, "tpex_margin_20261008.json"),
    (S.parse_twse_valuation, "twse_valuation_20261008.json"), (S.parse_tpex_valuation, "tpex_valuation_20261008.json"),
    (S.parse_tpex_index_month, "tpex_index_month_20261008.json"),
    (S.parse_twse_industry, "twse_industry.json"), (S.parse_tpex_industry, "tpex_industry.json"),
])
def test_output_is_plain_json(fn, name):
    out = fn(fx(name))
    text = json.dumps(out, ensure_ascii=False, allow_nan=False)
    assert json.loads(text) == out
    codes = out.get("rows", out if "industry" in name else {})
    assert all(classify(c) is not None for c in codes)   # 只有股票和 ETF
