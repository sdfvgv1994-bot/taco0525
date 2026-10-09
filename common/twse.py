"""台灣證券交易所 / 櫃買中心資料。

用到的公開 API：
- 上市個股日成交（每次一個月）  www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY
- 加權指數日 K（每次一個月）    www.twse.com.tw/rwd/zh/TAIEX/MI_5MINS_HIST
- 上櫃個股日成交（每次一個月）  www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock
                               （舊版網址 st43_result.php 當備援）
- 即時報價（上市、上櫃、指數）  mis.twse.com.tw/stock/api/getStockInfo.jsp

證交所會封鎖太頻繁的連線（大約 5 秒 3 次），所以：
- 每次連線至少間隔 MIN_INTERVAL 秒
- 歷史資料存在本機快取，過去的月份不會再抓；當月資料 CACHE_TTL 秒後才更新
"""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

MIN_INTERVAL = 2.0
CACHE_TTL = 15 * 60
TIMEOUT = 15
HEADERS = {"User-Agent": "Mozilla/5.0 (taco0525 stock tools)",
           "Accept": "application/json, text/plain, */*"}
DEFAULT_CACHE = Path(__file__).resolve().parent.parent / ".cache" / "twse"

URL_STOCK_DAY = "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY"
URL_INDEX = "https://www.twse.com.tw/rwd/zh/TAIEX/MI_5MINS_HIST"
URL_TPEX = "https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock"
URL_TPEX_OLD = "https://www.tpex.org.tw/web/stock/aftertrading/daily_trading_info/st43_result.php"
URL_MIS = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"

TAIPEI = ZoneInfo("Asia/Taipei")
# 即時報價裡的指數代號
MIS_INDEX = {"^TWII": "tse_t00.tw", "^TWOII": "otc_o00.tw"}


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


# ---------------------------------------------------------------- 連線與快取
def _months(start: date, end: date) -> list:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


class TwseClient:
    def __init__(self, cache_dir: str | Path | None = DEFAULT_CACHE, session=None,
                 min_interval: float = MIN_INTERVAL, progress=None):
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self._session = session
        self.min_interval = min_interval
        self.progress = progress   # 例如 print，用來顯示「下載中 3/24」
        self._last = 0.0
        self._lock = threading.Lock()

    @property
    def session(self):
        if self._session is None:
            import requests
            self._session = requests.Session()
            self._session.headers.update(HEADERS)
        return self._session

    def _get_json(self, url: str, params: dict) -> dict:
        with self._lock:  # 多執行緒共用同一個限速
            wait = self._last + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            try:
                r = self.session.get(url, params=params, timeout=TIMEOUT)
            finally:
                self._last = time.monotonic()
        if r.status_code in (302, 307, 403, 502):
            # 實測被封鎖時：證交所轉址（307），即時報價回 502 安全性頁面
            raise TwseError(f"{url} 回應 HTTP {r.status_code}（可能連線太頻繁被暫時封鎖，請過幾分鐘再試）")
        if r.status_code != 200:
            raise TwseError(f"{url} 回應 HTTP {r.status_code}")
        try:
            return r.json()
        except ValueError:
            # 被封鎖時常回 HTML 頁面
            raise TwseError("回應不是 JSON（可能連線太頻繁被暫時封鎖，請過幾分鐘再試）") from None

    def _cached(self, key: str, is_current: bool, fetch) -> dict:
        path = self.cache_dir / f"{key}.json" if self.cache_dir else None
        if path and path.exists():
            age = time.time() - path.stat().st_mtime
            if not is_current or age < CACHE_TTL:
                try:
                    return json.loads(path.read_text(encoding="utf-8"))
                except ValueError:
                    pass
        js = fetch()
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(js, ensure_ascii=False), encoding="utf-8")
        return js

    # ---- 單月 ----
    def stock_month(self, code: str, y: int, m: int, today: date | None = None) -> pd.DataFrame:
        cur = (y, m) == ((today or date.today()).year, (today or date.today()).month)
        js = self._cached(f"tse_{code}_{y}{m:02d}", cur, lambda: self._get_json(
            URL_STOCK_DAY, {"date": f"{y}{m:02d}01", "stockNo": code, "response": "json"}))
        return parse_stock_day(js)

    def tpex_month(self, code: str, y: int, m: int, today: date | None = None) -> pd.DataFrame:
        cur = (y, m) == ((today or date.today()).year, (today or date.today()).month)

        def fetch():
            try:
                js = self._get_json(URL_TPEX, {"code": code, "date": f"{y}/{m:02d}/01",
                                               "response": "json"})
                if js.get("tables") and js["tables"][0].get("data"):
                    return js
            except TwseError:
                pass
            return self._get_json(URL_TPEX_OLD, {"l": "zh-tw", "d": f"{y - 1911}/{m:02d}",
                                                 "stkno": code})
        return parse_tpex(self._cached(f"otc_{code}_{y}{m:02d}", cur, fetch))

    def index_month(self, y: int, m: int, today: date | None = None) -> pd.DataFrame:
        cur = (y, m) == ((today or date.today()).year, (today or date.today()).month)
        js = self._cached(f"taiex_{y}{m:02d}", cur, lambda: self._get_json(
            URL_INDEX, {"date": f"{y}{m:02d}01", "response": "json"}))
        return parse_index(js)

    # ---- 多月 ----
    def _collect(self, fetch_month, start: date, end: date, label: str) -> pd.DataFrame:
        months = _months(start, end)
        frames = []
        for i, (y, m) in enumerate(months, 1):
            if self.progress and len(months) > 3:
                self.progress(f"\r從證交所下載 {label} {y}/{m:02d}（{i}/{len(months)}）", end="")
            frames.append(fetch_month(y, m))
        if self.progress and len(months) > 3:
            self.progress("")
        frames = [f for f in frames if not f.empty]
        if not frames:
            return _rows_to_df(["日期", "開盤", "最高", "最低", "收盤"], [])
        df = pd.concat(frames)
        df = df[~df.index.duplicated(keep="last")].sort_index()
        return df[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))]

    def history(self, code: str, start: date, end: date, market: str = "auto") -> tuple:
        """回傳 (market, DataFrame)。market = tse（上市）/ otc（上櫃）。"""
        if market in ("auto", "tse"):
            # 先試最後一個月判斷是不是上市，避免上櫃股浪費好幾次連線
            probe = self.stock_month(code, end.year, end.month)
            if probe.empty and end.day < 8:  # 月初可能還沒有資料，再試上個月
                py, pm = (end.year - 1, 12) if end.month == 1 else (end.year, end.month - 1)
                probe = self.stock_month(code, py, pm)
            if not probe.empty or market == "tse":
                return "tse", self._collect(lambda y, m: self.stock_month(code, y, m),
                                            start, end, code)
        return "otc", self._collect(lambda y, m: self.tpex_month(code, y, m), start, end, code)

    def index_history(self, start: date, end: date) -> pd.DataFrame:
        return self._collect(self.index_month, start, end, "加權指數")

    # ---- 即時 ----
    def realtime(self, items: list) -> dict:
        """items 例：['2330', '6488', '^TWII']。股票會同時查上市和上櫃，回傳 {輸入: Quote}。"""
        ex_ch, lookup = [], {}
        for it in items:
            if it in MIS_INDEX:
                ex_ch.append(MIS_INDEX[it])
                lookup[MIS_INDEX[it].split("_")[1].split(".")[0]] = it
            else:
                ex_ch += [f"tse_{it}.tw", f"otc_{it}.tw"]
                lookup[it] = it
        js = self._get_json(URL_MIS, {"ex_ch": "|".join(ex_ch), "json": "1", "delay": "0",
                                      "_": str(int(time.time() * 1000))})
        quotes = parse_mis(js)
        return {lookup[c]: q for c, q in quotes.items() if c in lookup}


_default_client: TwseClient | None = None


def client() -> TwseClient:
    global _default_client
    if _default_client is None:
        _default_client = TwseClient(progress=print)
    return _default_client
