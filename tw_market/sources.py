"""證交所（上市）、櫃買中心（上櫃）盤後資料：網址與解析。

網址：每個函式回傳 (url, params)，給 common.twse.TwseClient._get_json 使用。

解析：parse_* 都是純函式（不連網），輸入是 API 回傳的 JSON：
- 那天沒有資料（休市、還沒公布）回傳 None
- 格式和預期不同就丟 ValueError（抓資料的程式會沿用上一次的資料）
- 日期一律是 "YYYY-MM-DD"，取自回應本身
- 只留股票和 ETF（contract.classify 不是 None 的代號；權證、特別股、ETN、REITs、
  6 位數代號的存託憑證不收；4 位數的存託憑證如 9105 照收，當作股票）
- 股數一律換成「張」（1 張 = 1000 股），四捨五入，.5 一律往遠離 0 的方向進位，
  所以買超 500 股算 +1 張、賣超 500 股算 −1 張（買賣對稱）
- 沒有數值（'--'、'---'、'N/A'、'X' 等）是 None
"""
from __future__ import annotations

import math
import re
from datetime import date

from common.twse import num
from tw_market.contract import INDUSTRIES, classify

TWSE = "https://www.twse.com.tw/rwd/zh"
TPEX = "https://www.tpex.org.tw/www/zh-tw"
NO_DATA = "很抱歉，沒有符合條件的資料"   # 證交所沒有資料時的 stat（有時候後面多一個 !）


# ---------------------------------------------------------------- 網址
def _ymd(d: date) -> str:
    return f"{d:%Y%m%d}"


def _slash(d: date) -> str:
    return f"{d:%Y/%m/%d}"


def twse_quotes(d: date):
    """上市每日收盤行情＋加權指數＋大盤成交統計。"""
    return f"{TWSE}/afterTrading/MI_INDEX", {"date": _ymd(d), "type": "ALLBUT0999", "response": "json"}


def twse_insti(d: date):
    """上市個股三大法人買賣超（股）。"""
    return f"{TWSE}/fund/T86", {"date": _ymd(d), "selectType": "ALLBUT0999", "response": "json"}


def twse_insti_totals(d: date):
    """上市三大法人買賣金額（元）。"""
    return f"{TWSE}/fund/BFI82U", {"type": "day", "dayDate": _ymd(d), "response": "json"}


def twse_margin(d: date):
    """上市融資融券餘額（張）。"""
    return f"{TWSE}/marginTrading/MI_MARGN", {"date": _ymd(d), "selectType": "ALL", "response": "json"}


def twse_valuation(d: date):
    """上市本益比、殖利率、股價淨值比。"""
    return f"{TWSE}/afterTrading/BWIBBU_d", {"date": _ymd(d), "selectType": "ALL", "response": "json"}


def twse_industry():
    """上市公司基本資料（產業別）。"""
    return "https://openapi.twse.com.tw/v1/opendata/t187ap03_L", {}


def tpex_quotes(d: date):
    """上櫃每日收盤行情（含權證，解析時濾掉）。"""
    return f"{TPEX}/afterTrading/dailyQuotes", {"date": _slash(d), "id": "", "response": "json"}


def tpex_insti(d: date):
    """上櫃個股三大法人買賣超（股）。"""
    return f"{TPEX}/insti/dailyTrade", {"type": "Daily", "sect": "EW", "date": _slash(d), "response": "json"}


def tpex_margin(d: date):
    """上櫃融資融券餘額（張）。"""
    return f"{TPEX}/margin/balance", {"date": _slash(d), "id": "", "response": "json"}


def tpex_valuation(d: date):
    """上櫃本益比、殖利率、股價淨值比。"""
    return f"{TPEX}/afterTrading/peQryDate", {"date": _slash(d), "response": "json"}


def tpex_index_month(d: date):
    """櫃買指數：回傳 d 那個月 1 日到 d 的每日資料。"""
    return f"{TPEX}/indexInfo/inx", {"date": _slash(d), "response": "json"}


def tpex_industry():
    """上櫃公司基本資料（產業別）。"""
    return "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O", {}


# ---------------------------------------------------------------- 小工具
def _iso(s) -> str:
    """'20261008'、'2026/10/08'、'115/10/08'（民國）→ '2026-10-08'。"""
    s = str(s).strip()
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", s) or re.fullmatch(r"(\d{2,4})/(\d{1,2})/(\d{1,2})", s)
    if not m:
        raise ValueError(f"看不懂的日期：{s!r}")
    y, mo, d = (int(x) for x in m.groups())
    if y < 1911:
        y += 1911
    return date(y, mo, d).isoformat()   # 不存在的日期（例如 2 月 30 日）會丟 ValueError


def _f(s) -> float | None:
    """'1,234.50' → 1234.5；'--'、'---'、'N/A'、'X'、'' → None。-0.0 一律變 0.0。"""
    v = num(s)
    if v is None or not math.isfinite(v):
        return None
    return v + 0.0


def _int(s) -> int | None:
    v = _f(s)
    return None if v is None else round(v)


def _lots(s) -> int | None:
    """股數 → 張（四捨五入，.5 往遠離 0 進位）。用整數運算，不會有小數誤差。"""
    v = _int(s)
    if v is None:
        return None
    q = (abs(v) + 500) // 1000
    return q if v >= 0 else -q


def _pe(s) -> float | None:
    """本益比：'-'、'N/A'、0（虧損或不計算）→ None。"""
    v = _f(s)
    return v if v is not None and v > 0 else None


def _signed(sign, diff) -> float | None:
    """證交所的漲跌：符號欄是 HTML（'<p style= color:green>-</p>'），價差欄沒有正負號。
    'X'（不比價）或看不懂的符號 → None。"""
    s = re.sub(r"<[^>]+>", "", str(sign)).strip()
    d = _f(diff)
    if d is None:
        return None
    if s == "-":
        return -abs(d) + 0.0
    if s in ("+", ""):          # 空白是平盤（價差 0.00）
        return abs(d)
    return None


def _cols(fields, *names) -> list:
    """依欄位名稱找位置（忽略空白）；找不到就是格式變了。"""
    if not isinstance(fields, list):
        raise ValueError("沒有欄位名稱（fields）")
    clean = [re.sub(r"\s", "", str(f)) for f in fields]
    missing = [n for n in names if n not in clean]
    if missing:
        raise ValueError(f"找不到欄位 {missing}：{fields}")
    return [clean.index(n) for n in names]


def _rows(data, width: int) -> list:
    """檢查每一列都是清單、而且欄位夠多。"""
    if not isinstance(data, list):
        raise ValueError("沒有資料列（data）")
    for r in data:
        if not isinstance(r, list) or len(r) < width:
            raise ValueError(f"資料列的欄位數不對：{r!r:.200}")
    return data


def _code(r, i: int = 0) -> str | None:
    """取代號（第 i 欄）；不是股票或 ETF 就回傳 None（不收錄）。"""
    code = str(r[i]).strip()
    return code if classify(code) else None


def _twse(js) -> bool:
    """證交所：stat 'OK' → True；沒有資料 → False；其他（錯誤訊息）→ ValueError。"""
    if not isinstance(js, dict):
        raise ValueError("回應不是 JSON 物件")
    stat = str(js.get("stat", "")).strip()
    if stat.upper() == "OK":
        return True
    if stat.startswith(NO_DATA):
        return False
    raise ValueError(f"證交所回應：{stat or '（沒有 stat）'}")


def _twse_table(js, keyword: str) -> dict:
    """依標題找證交所的表格。"""
    for t in js.get("tables") or []:
        if isinstance(t, dict) and keyword in str(t.get("title") or ""):
            return t
    raise ValueError(f"找不到「{keyword}」表格")


def _tpex(js) -> list | None:
    """櫃買中心：回傳 tables；第一個表格沒有資料（休市、還沒公布）→ None。"""
    if not isinstance(js, dict):
        raise ValueError("回應不是 JSON 物件")
    stat = str(js.get("stat", "")).strip()
    if stat.lower() != "ok":
        raise ValueError(f"櫃買中心回應：{stat or '（沒有 stat）'}")
    tables = js.get("tables")
    if not isinstance(tables, list) or not tables or not isinstance(tables[0], dict):
        raise ValueError("櫃買中心回應沒有表格（tables）")
    data = tables[0].get("data")
    if not isinstance(data, list):
        raise ValueError("櫃買中心回應沒有資料列（data）")
    return tables if data else None


def _tpex_date(js, table: dict) -> str:
    """櫃買中心的日期：優先用表格自己的日期（民國 '115/10/08'），沒有才用最外層的 '20261008'。"""
    return _iso(table.get("date") or js.get("date"))


# ---------------------------------------------------------------- 每日收盤行情
def parse_twse_quotes(js) -> dict | None:
    """上市 MI_INDEX →
        {"date": "2026-10-08",
         "index": {"name": "加權指數", "close", "change", "change_pct"} 或 None,
         "totals": {"value": 成交金額（元）, "volume": 成交量（張）} 或 None,
         "rows": {代號: {"name", "open", "high", "low", "close", "change", "volume"（張）, "value"（元）,
                         "trades"（筆）, "pe"}}}

    totals 用「大盤統計資訊」的「總計(1~15)」（含 ETF、權證等，和證交所公布的大盤成交金額一致）。
    加權指數或大盤統計找不到時是 None（行情照樣回傳）。沒有成交的股票 OHLC、漲跌都是 None。
    """
    if not _twse(js):
        return None
    t = _twse_table(js, "每日收盤行情")
    i = _cols(t.get("fields"), "證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額", "開盤價",
              "最高價", "最低價", "收盤價", "漲跌(+/-)", "漲跌價差", "本益比")
    data = _rows(t.get("data"), max(i) + 1)
    if not data:
        return None
    ic, iname, ivol, itr, ival, io, ih, il, icl, isign, idiff, ipe = i
    rows = {}
    for r in data:
        code = _code(r, ic)
        if code is None:
            continue
        close = _f(r[icl])
        rows[code] = {
            "name": str(r[iname]).strip(),
            "open": _f(r[io]), "high": _f(r[ih]), "low": _f(r[il]), "close": close,
            "change": None if close is None else _signed(r[isign], r[idiff]),
            "volume": _lots(r[ivol]), "value": _int(r[ival]), "trades": _int(r[itr]),
            "pe": _pe(r[ipe]),
        }
    return {"date": _iso(js.get("date")), "index": _twse_index(js), "totals": _twse_totals(js),
            "rows": rows}


def _twse_index(js) -> dict | None:
    """加權指數；找不到或格式不對 → None（不影響個股行情）。"""
    try:
        return _twse_index_strict(js)
    except ValueError:
        return None


def _twse_index_strict(js) -> dict | None:
    for t in js.get("tables") or []:
        fields = t.get("fields") if isinstance(t, dict) else None
        if not fields or str(fields[0]).strip() != "指數":
            continue
        ic, iclose, isign, idiff, ipct = _cols(fields, "指數", "收盤指數", "漲跌(+/-)", "漲跌點數",
                                               "漲跌百分比(%)")
        for r in _rows(t.get("data"), ipct + 1):
            if str(r[ic]).strip() == "發行量加權股價指數":
                change, pct = _signed(r[isign], r[idiff]), _f(r[ipct])
                if change is not None and pct is not None:
                    pct = -abs(pct) + 0.0 if change < 0 else abs(pct)   # 百分比的正負號跟著漲跌
                return {"name": "加權指數", "close": _f(r[iclose]), "change": change, "change_pct": pct}
    return None


def _twse_totals(js) -> dict | None:
    """大盤成交金額、成交量；找不到或格式不對 → None（不影響個股行情）。"""
    try:
        return _twse_totals_strict(js)
    except ValueError:
        return None


def _twse_totals_strict(js) -> dict | None:
    t = _twse_table(js, "大盤統計資訊")
    ic, ival, ivol = _cols(t.get("fields"), "成交統計", "成交金額(元)", "成交股數(股)")
    for r in _rows(t.get("data"), max(ic, ival, ivol) + 1):
        if str(r[ic]).strip().startswith("總計"):
            value, volume = _int(r[ival]), _lots(r[ivol])
            return None if value is None or volume is None else {"value": value, "volume": volume}
    return None


def parse_tpex_quotes(js) -> dict | None:
    """上櫃 dailyQuotes → {"date", "index": None, "totals", "rows"}。

    totals 用櫃買中心在這張表上公布的「總成交金額 / 總成交股數」（只算上櫃股票，不含 ETF、權證，
    和上市的 totals 含 ETF、權證不同）。「管理股票」表格（格式相同）也一起收。
    """
    tables = _tpex(js)
    if tables is None:
        return None
    t0 = tables[0]
    fields = [re.sub(r"\s", "", str(f)) for f in t0.get("fields") or []]
    i = _cols(fields, "代號", "名稱", "收盤", "漲跌", "開盤", "最高", "最低", "成交股數", "成交金額(元)",
              "成交筆數")
    ic, iname, icl, ich, io, ih, il, ivol, ival, itr = i
    rows = {}
    for t in tables:
        if not isinstance(t, dict) or [re.sub(r"\s", "", str(f)) for f in t.get("fields") or []] != fields:
            continue
        for r in _rows(t.get("data"), max(i) + 1):
            code = _code(r, ic)
            if code is None:
                continue
            close = _f(r[icl])
            rows[code] = {
                "name": str(r[iname]).strip(),
                "open": _f(r[io]), "high": _f(r[ih]), "low": _f(r[il]), "close": close,
                "change": None if close is None else _f(r[ich]),   # '除權'、'除息'、'---' → None
                "volume": _lots(r[ivol]), "value": _int(r[ival]), "trades": _int(r[itr]),
                "pe": None,
            }
    value, volume = _int(t0.get("totalTradingAmount")), _lots(t0.get("totalTradingShares"))
    totals = None if value is None or volume is None else {"value": value, "volume": volume}
    return {"date": _tpex_date(js, t0), "index": None, "totals": totals, "rows": rows}


# ---------------------------------------------------------------- 三大法人
def _insti_row(foreign, trust, dealer) -> dict:
    """合計用「四捨五入後的三項相加」，網頁上的數字才會剛好加得起來。"""
    parts = (foreign, trust, dealer)
    total = None if None in parts else foreign + trust + dealer
    return {"foreign": foreign, "trust": trust, "dealer": dealer, "total": total}


def _add(*vals):
    """股數相加（任一個沒有資料就是 None）。"""
    return None if None in vals else sum(vals)


def parse_twse_insti(js) -> dict | None:
    """上市 T86 → {"date", "rows": {代號: {"foreign", "trust", "dealer", "total"}}}（張）。

    外資 = 外陸資（不含外資自營商）＋外資自營商；自營商 = 自行買賣＋避險。
    """
    if not _twse(js):
        return None
    i = _cols(js.get("fields"), "證券代號", "外陸資買賣超股數(不含外資自營商)", "外資自營商買賣超股數",
              "投信買賣超股數", "自營商買賣超股數")
    data = _rows(js.get("data"), max(i) + 1)
    if not data:
        return None
    ic, ifo, ifd, itr, idl = i
    rows = {}
    for r in data:
        code = _code(r, ic)
        if code is not None:
            rows[code] = _insti_row(_lots(_add(_int(r[ifo]), _int(r[ifd]))), _lots(r[itr]), _lots(r[idl]))
    return {"date": _iso(js.get("date")), "rows": rows}


def parse_tpex_insti(js) -> dict | None:
    """上櫃 dailyTrade（sect=EW，24 欄）→ 同 parse_twse_insti。

    欄位名稱重複（「買賣超股數」出現 7 次），只能依位置：外資合計在第 11 欄、投信第 14 欄、
    自營商合計第 23 欄、三大法人合計第 24 欄。
    """
    tables = _tpex(js)
    if tables is None:
        return None
    t = tables[0]
    fields = [re.sub(r"\s", "", str(f)) for f in t.get("fields") or []]
    if (len(fields) != 24 or fields[:2] != ["代號", "名稱"] or "三大法人" not in fields[23]
            or any(fields[k] != "買賣超股數" for k in (4, 7, 10, 13, 16, 19, 22))):
        raise ValueError(f"三大法人欄位和預期不同：{t.get('fields')}")
    rows = {}
    for r in _rows(t.get("data"), 24):
        code = _code(r)
        if code is not None:
            rows[code] = _insti_row(_lots(r[10]), _lots(r[13]), _lots(r[22]))
    return {"date": _tpex_date(js, t), "rows": rows}


def parse_twse_insti_totals(js) -> dict | None:
    """上市 BFI82U → {"date", "foreign", "trust", "dealer", "total"}（元，買賣差額）。

    total 用證交所公布的「合計」。證交所註明外資自營商已算在自營商裡、不重複計入合計，
    所以外資自營商不是 0 的日子，foreign + trust + dealer 會比 total 多出外資自營商那一塊
    （近年外資自營商都是 0）。
    """
    if not _twse(js):
        return None
    iname, idiff = _cols(js.get("fields"), "單位名稱", "買賣差額")
    data = _rows(js.get("data"), max(iname, idiff) + 1)
    if not data:
        return None
    got = {re.sub(r"\s", "", str(r[iname])): _int(r[idiff]) for r in data}
    need = ["外資及陸資(不含外資自營商)", "外資自營商", "投信", "自營商(自行買賣)", "自營商(避險)", "合計"]
    missing = [k for k in need if k not in got]
    if missing:
        raise ValueError(f"三大法人買賣金額少了 {missing}")
    return {"date": _iso(js.get("date")),
            "foreign": _add(got["外資及陸資(不含外資自營商)"], got["外資自營商"]),
            "trust": got["投信"],
            "dealer": _add(got["自營商(自行買賣)"], got["自營商(避險)"]),
            "total": got["合計"]}


# ---------------------------------------------------------------- 融資融券
def _margin_row(m_prev, m_now, s_prev, s_now) -> dict:
    return {"margin_balance": m_now, "margin_change": _add(m_now, None if m_prev is None else -m_prev),
            "short_balance": s_now, "short_change": _add(s_now, None if s_prev is None else -s_prev)}


def parse_twse_margin(js) -> dict | None:
    """上市 MI_MARGN → {"date", "rows": {代號: {"margin_balance", "margin_change", "short_balance",
    "short_change"}}}（張；增減 = 今日餘額 − 前日餘額）。

    欄位名稱重複（融資、融券都有「前日餘額」「今日餘額」），第一組是融資、第二組是融券。
    """
    if not _twse(js):
        return None
    t = _twse_table(js, "融資融券彙總")
    fields = [re.sub(r"\s", "", str(f)) for f in t.get("fields") or []]
    prev = [k for k, f in enumerate(fields) if f == "前日餘額"]
    now = [k for k, f in enumerate(fields) if f == "今日餘額"]
    if not fields or fields[0] != "代號" or len(prev) != 2 or len(now) != 2:
        raise ValueError(f"融資融券欄位和預期不同：{t.get('fields')}")
    data = _rows(t.get("data"), max(now) + 1)
    if not data:
        return None
    rows = {}
    for r in data:
        code = _code(r)
        if code is not None:
            rows[code] = _margin_row(_int(r[prev[0]]), _int(r[now[0]]), _int(r[prev[1]]), _int(r[now[1]]))
    return {"date": _iso(js.get("date")), "rows": rows}


def parse_tpex_margin(js) -> dict | None:
    """上櫃 margin/balance → 同 parse_twse_margin。"""
    tables = _tpex(js)
    if tables is None:
        return None
    t = tables[0]
    i = _cols(t.get("fields"), "代號", "前資餘額(張)", "資餘額", "前券餘額(張)", "券餘額")
    rows = {}
    for r in _rows(t.get("data"), max(i) + 1):
        code = _code(r, i[0])
        if code is not None:
            rows[code] = _margin_row(*(_int(r[k]) for k in i[1:]))
    return {"date": _tpex_date(js, t), "rows": rows}


# ---------------------------------------------------------------- 本益比、殖利率、淨值比
def _valuation(data, ic, ipe, iy, ipb) -> dict:
    rows = {}
    for r in data:
        code = _code(r, ic)
        if code is not None:
            rows[code] = {"pe": _pe(r[ipe]), "dividend_yield": _f(r[iy]), "pb": _f(r[ipb])}
    return rows


def parse_twse_valuation(js) -> dict | None:
    """上市 BWIBBU_d → {"date", "rows": {代號: {"pe", "dividend_yield", "pb"}}}。"""
    if not _twse(js):
        return None
    i = _cols(js.get("fields"), "證券代號", "本益比", "殖利率(%)", "股價淨值比")
    data = _rows(js.get("data"), max(i) + 1)
    if not data:
        return None
    return {"date": _iso(js.get("date")), "rows": _valuation(data, *i)}


def parse_tpex_valuation(js) -> dict | None:
    """上櫃 peQryDate → 同 parse_twse_valuation。"""
    tables = _tpex(js)
    if tables is None:
        return None
    t = tables[0]
    i = _cols(t.get("fields"), "股票代號", "本益比", "殖利率(%)", "股價淨值比")
    return {"date": _tpex_date(js, t), "rows": _valuation(_rows(t.get("data"), max(i) + 1), *i)}


# ---------------------------------------------------------------- 櫃買指數
def parse_tpex_index_month(js) -> dict | None:
    """櫃買指數（月查詢）→ {"YYYY-MM-DD": {"open", "high", "low", "close", "change"}}。"""
    tables = _tpex(js)
    if tables is None:
        return None
    t = tables[0]
    i = _cols(t.get("fields"), "日期", "開市", "最高", "最低", "收市", "漲/跌")
    out = {}
    for r in _rows(t.get("data"), max(i) + 1):
        o, h, lo, c, ch = (_f(r[k]) for k in i[1:])
        out[_iso(r[i[0]])] = {"open": o, "high": h, "low": lo, "close": c, "change": ch}
    return out


# ---------------------------------------------------------------- 產業別
def _industry(items, code_key: str, ind_key: str) -> dict:
    if not isinstance(items, list) or not items:
        raise ValueError("公司基本資料是空的或格式不對")
    out = {}
    for it in items:
        if not isinstance(it, dict) or code_key not in it:
            raise ValueError(f"公司基本資料少了「{code_key}」")
        code = str(it[code_key]).strip()
        if classify(code):
            ind = str(it.get(ind_key) or "").strip()
            out[code] = INDUSTRIES.get(ind.zfill(2) if ind.isdigit() else ind)
    return out


def parse_twse_industry(js) -> dict:
    """上市公司基本資料 → {代號: 產業名稱}（不認得的產業代碼是 None）。"""
    return _industry(js, "公司代號", "產業別")


def parse_tpex_industry(js) -> dict:
    """上櫃公司基本資料 → {代號: 產業名稱}。"""
    return _industry(js, "SecuritiesCompanyCode", "SecuritiesIndustryCode")
