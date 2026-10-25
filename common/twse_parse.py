"""證交所 / 櫃買中心回應的解析（純函式，不連網）：common/twse.py 拆出來的積木。

連線、限速、快取在 common/twse.py；這裡的名字 twse.py 都會再匯出，
所以 `from common.twse import num, parse_mis, Quote` 等舊寫法照樣能用。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

TAIPEI = ZoneInfo("Asia/Taipei")


class TwseError(RuntimeError):
    pass


# ---------------------------------------------------------------- 解析（純函式，方便測試）
def roc_to_date(s: str) -> pd.Timestamp:
    """'113/10/01' 或 '113/10/01＊' → 2024-10-01。"""
    m = re.search(r"(\d{2,3})/(\d{1,2})/(\d{1,2})", s)
    if not m:
        raise ValueError(f"看不懂的日期：{s}")
    y, mo, d = (int(x) for x in m.groups())
    return pd.Timestamp(y + 1911, mo, d)


def num(s) -> float | None:
    """'1,234.50' → 1234.5；'--'、'-'、'' → None。"""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    s = str(s).replace(",", "").replace("+", "").strip()
    s = re.sub(r"<[^>]+>", "", s)  # 有些欄位會夾 HTML 標籤
    if s in ("", "-", "--", "---", "X", "除權息", "除權", "除息"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _find(fields: list, *keywords) -> int | None:
    clean = [re.sub(r"\s", "", f) for f in fields]
    for kw in keywords:
        for i, f in enumerate(clean):
            if kw in f:
                return i
    return None


def _rows_to_df(fields: list, rows: list, volume_multiplier: float = 1.0) -> pd.DataFrame:
    i_date = _find(fields, "日期")
    i_open = _find(fields, "開盤")
    i_high = _find(fields, "最高")
    i_low = _find(fields, "最低")
    i_close = _find(fields, "收盤")
    i_vol = _find(fields, "成交股數", "成交仟股", "成交張數")
    if None in (i_date, i_open, i_high, i_low, i_close):
        raise TwseError(f"欄位格式和預期不同：{fields}")
    if i_vol is not None and ("仟股" in fields[i_vol] or "張數" in fields[i_vol]):
        volume_multiplier = 1000.0
    recs = []
    for r in rows:
        try:
            dt = roc_to_date(str(r[i_date]))
        except ValueError:
            continue
        c = num(r[i_close])
        if c is None:  # 當天沒有成交
            continue
        o, h, l = num(r[i_open]), num(r[i_high]), num(r[i_low])
        v = num(r[i_vol]) if i_vol is not None else None
        recs.append({"Date": dt, "Open": o if o is not None else c,
                     "High": h if h is not None else c, "Low": l if l is not None else c,
                     "Close": c, "Volume": (v or 0) * volume_multiplier})
    df = pd.DataFrame(recs, columns=["Date", "Open", "High", "Low", "Close", "Volume"])
    return df.set_index("Date").sort_index()


def parse_stock_day(js: dict) -> pd.DataFrame:
    """上市個股 STOCK_DAY。沒資料回傳空表（stat 不是 OK）。"""
    if str(js.get("stat", "")).upper() != "OK" or not js.get("data"):
        return _rows_to_df(["日期", "開盤價", "最高價", "最低價", "收盤價"], [])
    return _rows_to_df(js["fields"], js["data"])


def name_from_stock_day(js: dict) -> str | None:
    """標題像「113年10月 2330 台積電           各日成交資訊」，取出股名。"""
    m = re.search(r"\d{4,6}[A-Z]?\s+(\S+)\s+各日成交資訊", str(js.get("title") or ""))
    return m.group(1) if m else None


def name_from_tpex(js: dict) -> str | None:
    """櫃買中心新版頂層有 name；也可以從 subtitle「6488 環球晶 113年10月」取。"""
    if js.get("name"):
        return str(js["name"]).strip() or None
    for t in js.get("tables") or []:
        m = re.match(r"\s*\S+\s+(\S+)\s", str(t.get("subtitle") or ""))
        if m:
            return m.group(1)
    return None


def parse_index(js: dict) -> pd.DataFrame:
    """加權指數 MI_5MINS_HIST（沒有成交量）。"""
    if str(js.get("stat", "")).upper() != "OK" or not js.get("data"):
        return _rows_to_df(["日期", "開盤指數", "最高指數", "最低指數", "收盤指數"], [])
    return _rows_to_df(js["fields"], js["data"])


def parse_tpex(js: dict) -> pd.DataFrame:
    """上櫃個股：新版 {'tables':[{'fields','data'}]} 或舊版 {'aaData': [...]}。"""
    if js.get("tables"):
        t = js["tables"][0]
        if t.get("data"):
            return _rows_to_df(t.get("fields") or [], t["data"])
    if js.get("aaData"):
        # 舊版欄位固定：日期, 成交仟股, 成交仟元, 開盤, 最高, 最低, 收盤, 漲跌, 筆數
        fields = ["日期", "成交仟股", "成交仟元", "開盤", "最高", "最低", "收盤", "漲跌", "筆數"]
        return _rows_to_df(fields, js["aaData"])
    return _rows_to_df(["日期", "開盤", "最高", "最低", "收盤"], [])


@dataclass
class Quote:
    code: str
    name: str
    market: str            # tse / otc
    price: float | None    # 最新成交價（沒成交時用最佳買價或昨收）
    prev_close: float | None
    open: float | None
    high: float | None
    low: float | None
    volume: float | None   # 股（指數為 None）
    time: datetime | None
    source: str = "mis"    # mis = 證交所即時報價；yahoo = Yahoo 延遲報價（證交所抓不到時的備援）

    @property
    def change(self) -> float | None:
        if self.price is None or not self.prev_close:
            return None
        return self.price - self.prev_close

    @property
    def change_pct(self) -> float | None:
        ch = self.change
        return None if ch is None else ch / self.prev_close * 100


def parse_mis(js: dict) -> dict:
    """即時報價 → {代號: Quote}。指數的代號是 t00 / o00。"""
    out = {}
    for it in js.get("msgArray", []) or []:
        code = it.get("c") or ""
        if not code:  # 同時查上市和上櫃時，不存在的那邊會回一筆空白項目
            continue
        price = num(it.get("z"))
        if price is None:  # 這一刻沒有成交，用最佳買價第一檔
            bid = (it.get("b") or "").split("_")[0]
            price = num(bid)
        prev = num(it.get("y"))
        if price is None:
            price = prev
        ts = None
        if it.get("tlong"):
            try:
                # 一律換成台北時間（不帶時區），電腦不在台灣時日期才不會錯
                ts = datetime.fromtimestamp(int(it["tlong"]) / 1000, TAIPEI).replace(tzinfo=None)
            except (TypeError, ValueError):
                pass
        vol = num(it.get("v"))
        is_index = code in ("t00", "o00")
        out[code] = Quote(code, it.get("n") or code, it.get("ex") or "", price, prev,
                          num(it.get("o")), num(it.get("h")), num(it.get("l")),
                          None if is_index or vol is None else vol * 1000, ts)
    return out
