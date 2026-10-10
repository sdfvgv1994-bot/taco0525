"""台股總覽網頁的資料格式（產生資料的程式、網頁、測試共用這份定義）。

輸出到網站的 tw/ 資料夾：

tw/data/summary.json   大盤摘要
    {
      "date": "2026-10-08",                 最新一個有收盤資料的交易日
      "updated_utc": "2026-10-10T11:40:00Z",
      "markets": {
        "tse": {                            上市（otc = 上櫃，格式相同）
          "name": "上市",
          "index": {"name": "加權指數", "close": 49313.44, "change": -492.93, "change_pct": -0.99} 或 null,
          "value": 512345678901,            成交金額（元）
          "volume": 1234567,                成交量（張）
          "up": 400, "down": 500, "flat": 100,       漲跌家數（只算股票，不含 ETF）
          "limit_up": 5, "limit_down": 2             漲停、跌停家數
        },
        "otc": {...}
      },
      "institutional": {                    三大法人買賣超（元）；還沒公布時為 null
        "date": "2026-10-08",
        "tse": {"foreign": -1.2e10, "trust": 1.2e9, "dealer": 3e8, "total": -1.07e10},
        "otc": {...} 或 null
      } 或 null,
      "chips_date": "2026-10-08" 或 null,     個股三大法人資料的日期
      "margin_date": "2026-10-08" 或 null,    融資融券資料的日期（晚上才公布，可能比 date 舊）
      "valuation_date": "2026-10-08" 或 null, 本益比、殖利率、淨值比的日期
      "history_days": 120,                  已經有幾天的歷史 K 線
      "notes": ["櫃買中心的資料這次抓不到，沿用上一次的資料"]
    }

tw/data/stocks.json    全部上市櫃股票與 ETF（表格形式，省流量）
    {"date": "2026-10-08", "fields": STOCK_FIELDS, "rows": [[...], ...]}
    每一列的值依 STOCK_FIELDS 的順序；沒有資料的格子是 null。

tw/data/hist/<代號>.json   個股歷史（最多 HIST_MAX_DAYS 天，舊 → 新）
    {
      "code": "2330", "name": "台積電",
      "dates": ["2026-04-01", ...], "open": [...], "high": [...], "low": [...], "close": [...],
      "volume": [...],                      張
      "inst": {"dates": [...], "foreign": [...], "trust": [...], "dealer": [...]} 或 null,   張
      "margin": {"dates": [...], "margin_balance": [...], "short_balance": [...]} 或 null     張
    }
"""
from __future__ import annotations

import math
import re

HIST_MAX_DAYS = 250          # 個股歷史最多保留幾個交易日（約一年）
CHIPS_MAX_DAYS = 20          # 個股法人、融資融券最多保留幾天

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
]
TEXT_FIELDS = {"code", "name", "market", "type", "industry"}
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


def classify(code: str) -> str | None:
    """代號 → "stock" / "etf"；權證、特別股等其他商品回傳 None（不收錄）。"""
    if ETF_CODE.fullmatch(code):
        return "etf"
    if STOCK_CODE.fullmatch(code):
        return "stock"
    return None


# ---------------------------------------------------------------- 檢查（測試用）
def _num_or_none(v) -> bool:
    return v is None or (isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v))


def _date(v) -> bool:
    return isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) is not None


def check_summary(d: dict) -> list:
    p = []
    if not _date(d.get("date")):
        p.append("date 格式不對")
    for key in ("chips_date", "margin_date", "valuation_date"):
        if d.get(key) is not None and not _date(d[key]):
            p.append(f"{key} 格式不對")
    for mk, name in MARKETS.items():
        m = (d.get("markets") or {}).get(mk)
        if m is None:
            p.append(f"markets.{mk} 不見了")
            continue
        if m.get("name") != name:
            p.append(f"markets.{mk}.name 應該是 {name}")
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
    return p
