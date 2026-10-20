"""台股總覽網頁的資料格式（產生資料的程式、網頁、測試共用這份定義）。

輸出到網站的 tw/ 資料夾：

tw/data/summary.json   大盤摘要
    {
      "date": "2026-10-08",                 最新一個有收盤資料的交易日
      "updated_utc": "2026-10-10T11:40:00Z",  最後一次確認資料是最新的時間（任一個市場確認了就算；
                                              兩個市場都抓不到時不會往前走）
      "markets": {
        "tse": {                            上市（otc = 上櫃，格式相同）
          "name": "上市",
          "date": "2026-10-08" 或 null,        這個市場的行情日期（另一個市場晚公布或抓不到時，可能比上面的 date 舊）
          "chips_date": "2026-10-08" 或 null,   這個市場的個股三大法人日期（下面三個也是這個市場自己的）
          "margin_date": "2026-10-08" 或 null,
          "valuation_date": "2026-10-08" 或 null,
          "index": {"name": "加權指數", "close": 49313.44, "change": -492.93, "change_pct": -0.99} 或 null,
          "value": 512345678901,            成交金額（元）
          "volume": 1234567,                成交量（張）
          "up": 400, "down": 500, "flat": 100,       漲跌家數（只算股票，不含 ETF、存託憑證；不比價的不算）
          "limit_up": 5, "limit_down": 2             漲停、跌停家數（同上；收盤價剛好是漲停 / 跌停價）
                                                    和證交所公布的「漲跌證券數合計」股票欄一致
        },
        "otc": {...}
      },
      "institutional": {                    三大法人買賣超（元）；還沒公布時為 null
        "date": "2026-10-08",
        "tse": {"foreign": -1.2e10, "trust": 1.2e9, "dealer": 3e8, "total": -1.07e10},
        "otc": {...} 或 null
      } 或 null,
      "chips_date": "2026-10-08" 或 null,     個股三大法人資料的日期（兩個市場比較新的那個）
      "margin_date": "2026-10-08" 或 null,    融資融券資料的日期（晚上才公布，可能比 date 舊）
      "valuation_date": "2026-10-08" 或 null, 本益比、殖利率、淨值比的日期
      "history_days": 120,                  已經有幾天的歷史 K 線
      "hist_version": "3f2a9c1b7e0d",       歷史檔（hist/*.json）的版本：內容有變（補了歷史、上櫃晚到、補了籌碼）
                                            就不同，網頁用來決定要不要重新下載
      "notes": ["櫃買中心的資料這次抓不到，沿用上一次的資料"]
    }

tw/data/stocks.json    全部上市櫃股票與 ETF（表格形式，省流量）
    {"date": "2026-10-08", "fields": STOCK_FIELDS, "rows": [[...], ...]}
    每一列的值依 STOCK_FIELDS 的順序；沒有資料的格子是 null。
    外幣計價的 ETF（代號第 6 碼是 K、M、S、C，例如 00636K）不收：價格、金額都是外幣。

tw/data/hist/<代號>.json   個股歷史（最多 HIST_MAX_DAYS 天，舊 → 新）
    {
      "code": "2330", "name": "台積電",
      "dates": ["2026-04-01", ...], "open": [...], "high": [...], "low": [...], "close": [...],
      "volume": [...],                      張
      "inst": {"dates": [...], "foreign": [...], "trust": [...], "dealer": [...]} 或 null,   張
      "margin": {"dates": [...], "margin_balance": [...], "short_balance": [...]} 或 null     張
      "dividends": [                        近 DIV_YEARS 年的除權除息（新 → 舊）或 null
        {"date": "2026-08-05", "kind": "權息",   "息" 除息、"權" 除權、"權息" 兩個都有
         "cash": 0.8,                       現金股利（元／股）；上市的「權」「權息」明細還沒問到時 null
         "stock": 0.25,                     股票股利（元／股，每千股配 25 股 = 0.25 元）；同上
         "total": 1.395122,                 權值＋息值（元，交易所算的，一定有）
         "before": 25.2, "ref": 23.8}       除權息前收盤價、參考價
      ]
    }
"""
from __future__ import annotations

import math
import re
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

EXRIGHT_KINDS = ("息", "權", "權息")
HIST_MAX_DAYS = 250          # 個股歷史最多保留幾個交易日（約一年）
CHIPS_MAX_DAYS = 20          # 個股法人、融資融券最多保留幾天
DIV_YEARS = 6                # 除權除息保留幾年（今年＋前 5 年）

STOCK_FIELDS = [
    "code",            # 代號
    "name",            # 名稱
    "market",          # "tse" 上市 / "otc" 上櫃
    "type",            # "stock" 股票 / "etf" ETF
    "industry",        # 產業（中文，例如 "半導體業"；ETF 為 "ETF"；不知道時 null）
    "close",           # 收盤價
    "change",          # 漲跌（元）
    "change_pct",      # 漲跌幅（%）
    "open", "high", "low",
    "volume",          # 成交量（張）
    "value",           # 成交金額（元）
    "pe",              # 本益比（虧損或沒有時 null）
    "dividend_yield",  # 殖利率（%）
    "pb",              # 股價淨值比
    "foreign",         # 外資買賣超（張）
    "trust",           # 投信買賣超（張）
    "dealer",          # 自營商買賣超（張）
    "inst_total",      # 三大法人合計（張）
    "margin_balance",  # 融資餘額（張）
    "margin_change",   # 融資增減（張）
    "short_balance",   # 融券餘額（張）
    "short_change",    # 融券增減（張）
    "limit",           # 1 漲停、-1 跌停、0 都不是；ETF、沒成交、不比價（除權息等）是 null
]
TEXT_FIELDS = {"code", "name", "market", "type", "industry"}
DATE_KEYS = ("date", "chips_date", "margin_date", "valuation_date")   # summary 和 summary.markets[市場] 都有
MARKETS = {"tse": "上市", "otc": "上櫃"}
TYPES = ("stock", "etf")

# 證交所 / 櫃買中心公司基本資料的「產業別」代碼
INDUSTRIES = {
    "01": "水泥工業", "02": "食品工業", "03": "塑膠工業", "04": "紡織纖維", "05": "電機機械",
    "06": "電器電纜", "08": "玻璃陶瓷", "09": "造紙工業", "10": "鋼鐵工業", "11": "橡膠工業",
    "12": "汽車工業", "14": "建材營造", "15": "航運業", "16": "觀光餐旅", "17": "金融保險",
    "18": "貿易百貨", "19": "綜合", "20": "其他", "21": "化學工業", "22": "生技醫療業",
    "23": "油電燃氣業", "24": "半導體業", "25": "電腦及週邊設備業", "26": "光電業",
    "27": "通信網路業", "28": "電子零組件業", "29": "電子通路業", "30": "資訊服務業",
    "31": "其他電子業", "32": "文化創意業", "33": "農業科技業", "34": "電子商務",
    "35": "綠能環保", "36": "數位雲端", "37": "運動休閒", "38": "居家生活",
    "80": "管理股票", "91": "存託憑證",
}

STOCK_CODE = re.compile(r"\d{4}")                 # 一般股票：4 位數字
ETF_CODE = re.compile(r"00\d{2,4}[A-Z]?")         # ETF：00 開頭，例如 0050、00878、00631L
FOREIGN_CURRENCY = "KMSC"   # ETF 代號第 6 碼是這些字母：用外幣交易（證交所註明），價格、金額不是台幣


def classify(code: str) -> str | None:
    """代號 → "stock" / "etf"；權證、特別股、外幣 ETF 等其他商品回傳 None（不收錄）。"""
    if ETF_CODE.fullmatch(code):
        return None if len(code) == 6 and code[5] in FOREIGN_CURRENCY else "etf"
    if STOCK_CODE.fullmatch(code):
        return "stock"
    return None


# 股票的升降單位（tick）：價格未滿多少 → 一檔多少元；1000 元以上一檔 5 元
_TICKS = ((10, Decimal("0.01")), (50, Decimal("0.05")), (100, Decimal("0.1")), (500, Decimal("0.5")),
          (1000, Decimal("1")))


def _tick(p: Decimal) -> Decimal:
    return next((t for lim, t in _TICKS if p < lim), Decimal("5"))


def limit_prices(prev) -> tuple[float, float] | None:
    """前一天收盤價 → (漲停價, 跌停價)（股票）。

    漲停 = 前一天收盤 × 1.1 往下取到有效價位；跌停 = × 0.9 往上取（價位依那個價格的升降單位）。
    例如南亞前一天 98.1 → 漲停 107.5（100 元以上一檔 0.5），只漲 9.58% 也是漲停。
    用十進位計算，不會有小數誤差。"""
    if not isinstance(prev, (int, float)) or isinstance(prev, bool) or not math.isfinite(prev) or prev <= 0:
        return None
    p = Decimal(str(prev))
    up, down = p * Decimal("1.1"), p * Decimal("0.9")
    up = (up / _tick(up)).to_integral_value(ROUND_FLOOR) * _tick(up)
    down = (down / _tick(down)).to_integral_value(ROUND_CEILING) * _tick(down)
    return float(up), float(down)


def limit_of(close, change) -> int | None:
    """收盤價、漲跌 → 1 漲停、-1 跌停、0 都不是；沒有收盤價或不比價（漲跌是 null）→ None。
    要剛好等於漲停 / 跌停價才算（沒有漲跌幅限制的新股漲 12% 也不算漲停）。"""
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
               for v in (close, change)):
        return None
    lim = limit_prices(float(Decimal(str(close)) - Decimal(str(change))))
    if lim is None:
        return None
    return 1 if close == lim[0] else -1 if close == lim[1] else 0


# ---------------------------------------------------------------- 檢查（測試用）
def _num_or_none(v) -> bool:
    return v is None or (isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v))


def _date(v) -> bool:
    return isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) is not None


def check_summary(d: dict) -> list:
    p = []
    if not _date(d.get("date")):
        p.append("date 格式不對")
    for key in DATE_KEYS[1:]:
        if d.get(key) is not None and not _date(d[key]):
            p.append(f"{key} 格式不對")
    if not isinstance(d.get("hist_version"), str) or not d["hist_version"]:
        p.append("hist_version 不是文字")
    for mk, name in MARKETS.items():
        m = (d.get("markets") or {}).get(mk)
        if m is None:
            p.append(f"markets.{mk} 不見了")
            continue
        if m.get("name") != name:
            p.append(f"markets.{mk}.name 應該是 {name}")
        for key in DATE_KEYS:
            if m.get(key) is not None and not _date(m[key]):
                p.append(f"markets.{mk}.{key} 格式不對")
            if m.get(key) and d.get(key) and m[key] > d[key]:
                p.append(f"markets.{mk}.{key} 比 {key} 新")
        for k in ("value", "volume", "up", "down", "flat", "limit_up", "limit_down"):
            if not _num_or_none(m.get(k)):
                p.append(f"markets.{mk}.{k} 不是數字")
        idx = m.get("index")
        if idx is not None:
            for k in ("close", "change", "change_pct"):
                if not _num_or_none(idx.get(k)):
                    p.append(f"markets.{mk}.index.{k} 不是數字")
    inst = d.get("institutional")
    if inst is not None:
        for mk in MARKETS:
            v = inst.get(mk)
            if v is not None and not all(_num_or_none(v.get(k)) for k in ("foreign", "trust", "dealer", "total")):
                p.append(f"institutional.{mk} 不是數字")
    if not isinstance(d.get("history_days"), int):
        p.append("history_days 不是整數")
    if not isinstance(d.get("notes"), list):
        p.append("notes 不是清單")
    return p


def check_stocks(d: dict) -> list:
    p = []
    if d.get("fields") != STOCK_FIELDS:
        return ["fields 和 STOCK_FIELDS 不一樣"]
    if not _date(d.get("date")):
        p.append("date 格式不對")
    seen = set()
    for row in d.get("rows", []):
        if len(row) != len(STOCK_FIELDS):
            p.append(f"{row[:1]} 欄位數不對")
            continue
        r = dict(zip(STOCK_FIELDS, row))
        if r["code"] in seen:
            p.append(f"{r['code']} 重複")
        seen.add(r["code"])
        if r["market"] not in MARKETS or r["type"] not in TYPES or classify(r["code"]) != r["type"]:
            p.append(f"{r['code']} 的 market / type 不對")
        if not r["name"]:
            p.append(f"{r['code']} 沒有名稱")
        for k in STOCK_FIELDS:
            if k not in TEXT_FIELDS and not _num_or_none(r[k]):
                p.append(f"{r['code']}.{k} 不是數字")
        if r["limit"] not in (1, -1, 0, None) or (r["limit"] is not None and r["type"] != "stock"):
            p.append(f"{r['code']}.limit 只能是 1、-1、0 或 null（ETF 一律 null）")
    return p


def check_hist(d: dict) -> list:
    p = []
    n = len(d.get("dates", []))
    if not n:
        p.append("沒有日期")
    if any(not _date(x) for x in d.get("dates", [])) or d.get("dates") != sorted(set(d.get("dates", []))):
        p.append("dates 要由舊到新、不重複")
    if n > HIST_MAX_DAYS:
        p.append("太多天")
    for k in ("open", "high", "low", "close", "volume"):
        v = d.get(k)
        if not isinstance(v, list) or len(v) != n or not all(_num_or_none(x) for x in v):
            p.append(f"{k} 長度或內容不對")
    for key, cols in (("inst", ("foreign", "trust", "dealer")), ("margin", ("margin_balance", "short_balance"))):
        sub = d.get(key)
        if sub is None:
            continue
        m = len(sub.get("dates", []))
        if m > CHIPS_MAX_DAYS or sub.get("dates") != sorted(set(sub.get("dates", []))):
            p.append(f"{key}.dates 不對")
        for c in cols:
            v = sub.get(c)
            if not isinstance(v, list) or len(v) != m or not all(_num_or_none(x) for x in v):
                p.append(f"{key}.{c} 長度或內容不對")
    div = d.get("dividends")
    if div is not None:
        if not isinstance(div, list) or not div or not all(isinstance(x, dict) for x in div):
            p.append("dividends 要是清單（沒有就是 null）")
        else:
            ds = [x.get("date") for x in div]
            if not all(_date(x) for x in ds) or ds != sorted(set(ds), reverse=True):
                p.append("dividends 的日期要由新到舊、不重複")
            for x in div:
                if x.get("kind") not in EXRIGHT_KINDS:
                    p.append(f"dividends {x.get('date')} 的 kind 不對")
                if not all(_num_or_none(x.get(k)) for k in ("cash", "stock", "total", "before", "ref")):
                    p.append(f"dividends {x.get('date')} 的數字不對")
    return p
