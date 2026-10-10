"""台灣證券交易所 / 櫃買中心資料。

用到的公開 API：
- 上市個股日成交（每次一個月）  www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY
- 加權指數日 K（每次一個月）    www.twse.com.tw/rwd/zh/TAIEX/MI_5MINS_HIST
- 上櫃個股日成交（每次一個月）  www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock
                               （舊版網址 st43_result.php 當備援）
- 即時報價（上市、上櫃、指數）  mis.twse.com.tw/stock/api/getStockInfo.jsp

證交所會封鎖太頻繁的連線（大約 5 秒 3 次；實測被封鎖後 20 分鐘以上才解除），所以：
- 每次連線至少間隔 MIN_INTERVAL 秒，而且是「跨程式」共用：同時開回測、看盤、看板，
  或連續執行好幾次，都會在快取資料夾的時間紀錄排隊，不會疊加成連發
- 一偵測到被封鎖（轉址、403、502 安全性頁面、回 HTML），就記下來，一段時間內（證交所主站
  BLOCK_COOLDOWN 秒；即時報價的 502 常常十幾秒就恢復，只停 MIS_COOLDOWN 秒）
  不再連那個網站，直接報錯（自動模式會改用 Yahoo），避免一直重試讓封鎖時間拉長
- 歷史資料存在本機快取，過去的月份不會再抓；當月資料 CACHE_TTL 秒後才更新
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import pandas as pd

MIN_INTERVAL = 3.0
BLOCK_COOLDOWN = 5 * 60
MIS_COOLDOWN = 60      # 實測即時報價回 502 後，十幾秒到幾十分鐘都有；看盤每分鐘更新，停一分鐘剛好
BLOCKED_STATUS = (301, 302, 303, 307, 308, 403, 429, 502, 503)
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
URL_MIS_HOME = "https://mis.twse.com.tw/stock/index.jsp"  # 先開這頁拿 cookie，即時報價才會回資料

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


# ---------------------------------------------------------------- 跨程式限速與封鎖紀錄
_WINDOWS = os.name == "nt"
MAX_QUEUE_AHEAD = 120  # 預約的時段最多排到幾秒後；超過代表時鐘被調過，重新起算


@contextmanager
def _file_lock(path: Path):
    """跨程式的互斥鎖（Windows 用 msvcrt，其他系統用 fcntl）。只用來保護很短的讀寫。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT)
    try:
        if _WINDOWS:
            import msvcrt
            while True:  # 不用 LK_LOCK：它每秒才重試一次，別的程式會等很久
                try:
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.02)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            if _WINDOWS:
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _read_float(path: Path) -> float:
    try:
        return float(path.read_text().strip())
    except (OSError, ValueError):
        return 0.0


def _minutes(seconds: float) -> int:
    return max(1, math.ceil(seconds / 60))


class HostGate:
    """同一個網站的連線排隊與封鎖紀錄。

    排隊方式是「預約時段」：在鎖裡讀出上一個預約的時間，往後排 min_interval 秒
    寫回去，馬上放開鎖，再睡到自己的時段才連線。鎖只握幾毫秒，所以不同程式
    （回測、看盤、看板）可以交錯排隊，誰也不會被長時間卡住。

    state_dir 為 None、或資料夾不能寫入時，只在程式內限速。
    """

    def __init__(self, host: str, state_dir: Path | None, min_interval: float):
        self.host = host
        self.min_interval = min_interval
        self.state_dir = state_dir
        self._thread_lock = threading.Lock()
        self._next_slot = 0.0     # 程式內的預約（沒有 state_dir 時用）
        self._blocked_at = 0.0    # 程式內的封鎖紀錄（沒有 state_dir 時用）
        self._file_ok = state_dir is not None

    @property
    def cooldown(self) -> float:
        return MIS_COOLDOWN if self.host == "mis.twse.com.tw" else BLOCK_COOLDOWN

    def _paths(self):
        d = self.state_dir
        return d / f".gate_{self.host}.lock", d / f".gate_{self.host}.next", d / f".blocked_{self.host}"

    def _file_failed(self, e: OSError) -> None:
        if self._file_ok:
            self._file_ok = False
            print(f"⚠ 快取資料夾 {self.state_dir} 無法寫入（{e}），只在這個程式內限速",
                  file=sys.stderr)

    # ---- 封鎖紀錄 ----
    def blocked_remaining(self) -> float:
        """還要等幾秒才解除封鎖紀錄；0 = 沒有被封鎖。最多 cooldown 秒（時鐘被調過也不會卡更久）。"""
        at = self._blocked_at
        if self._file_ok:
            at = max(at, _read_float(self._paths()[2]))
        cd = self.cooldown
        return max(0.0, min(cd, at + cd - time.time()))

    def mark_blocked(self) -> None:
        now = time.time()
        self._blocked_at = now
        if self._file_ok:
            try:
                self._paths()[2].write_text(f"{now}")
            except OSError as e:
                self._file_failed(e)

    def clear_blocked(self, since: float = float("inf")) -> None:
        """連線成功後清掉封鎖紀錄；但如果紀錄是在 since（自己送出請求）之後才寫的，表示別的請求
        剛被封鎖，就保留。"""
        if self._blocked_at < since:
            self._blocked_at = 0.0
        if self._file_ok:
            path = self._paths()[2]
            if _read_float(path) >= since:
                return
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError as e:
                self._file_failed(e)

    def raise_if_blocked(self) -> None:
        left = self.blocked_remaining()
        if left > 0:
            raise TwseError(f"{self.host} 剛剛暫時封鎖了這台電腦的連線，"
                            f"約 {_minutes(left)} 分鐘內先不連線（避免封鎖時間被拉長）")

    # ---- 排隊 ----
    def _reserve(self) -> float:
        """預約下一個可以連線的時間點（time.time() 的秒數）。"""
        now = time.time()
        with self._thread_lock:
            if self._file_ok:
                lock_path, next_path, _ = self._paths()
                try:
                    with _file_lock(lock_path):
                        prev = _read_float(next_path)
                        if prev > now + MAX_QUEUE_AHEAD:
                            prev = 0.0
                        slot = max(now, prev + self.min_interval if prev else now)
                        next_path.write_text(f"{slot}")
                    return slot
                except OSError as e:
                    self._file_failed(e)
            prev = self._next_slot
            if prev > now + MAX_QUEUE_AHEAD:
                prev = 0.0
            slot = max(now, prev + self.min_interval if prev else now)
            self._next_slot = slot
            return slot

    def wait_turn(self) -> None:
        """排隊等到自己的時段；輪到時如果網站已經被記為封鎖（可能是排在前面的請求剛被封），就不連線。"""
        self.raise_if_blocked()
        wait = self._reserve() - time.time()
        if wait > 0:
            time.sleep(wait)
        self.raise_if_blocked()


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
        self._gates: dict = {}
        self._gates_lock = threading.Lock()
        self.names: dict = {}       # 代號 → 股名（從歷史資料標題或即時報價取得）
        self._mis_warm = False

    def gate(self, url: str) -> HostGate:
        host = urlparse(url).hostname or url
        with self._gates_lock:
            if host not in self._gates:
                self._gates[host] = HostGate(host, self.cache_dir, self.min_interval)
            return self._gates[host]

    @property
    def session(self):
        if self._session is None:
            import requests
            self._session = requests.Session()
            self._session.headers.update(HEADERS)
        return self._session

    def _get_json(self, url: str, params: dict, redirect_is_block: bool = True) -> dict:
        gate = self.gate(url)
        gate.wait_turn()
        sent_at = time.time()
        r = self.session.get(url, params=params, timeout=TIMEOUT, allow_redirects=False)
        if not redirect_is_block and 300 <= r.status_code < 400:
            raise TwseError(f"{url} 回應 HTTP {r.status_code}（這個網址可能已經停用）")
        if r.status_code in BLOCKED_STATUS:
            # 實測被封鎖時：證交所轉址（307），即時報價回 502 安全性頁面
            gate.mark_blocked()
            raise TwseError(f"{gate.host} 回應 HTTP {r.status_code}"
                            f"（連線太頻繁被暫時封鎖，{_minutes(gate.cooldown)} 分鐘內先不連線）")
        if r.status_code != 200:
            raise TwseError(f"{url} 回應 HTTP {r.status_code}")
        try:
            js = r.json()
        except ValueError:
            # 被封鎖時也可能回 200 的 HTML 頁面
            gate.mark_blocked()
            raise TwseError(f"{gate.host} 回應不是 JSON（可能被暫時封鎖，"
                            f"{_minutes(gate.cooldown)} 分鐘內先不連線）") from None
        gate.clear_blocked(since=sent_at)
        return js

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
        name = name_from_stock_day(js)
        if name:
            self.names[code] = name
        return parse_stock_day(js)

    def tpex_month(self, code: str, y: int, m: int, today: date | None = None) -> pd.DataFrame:
        cur = (y, m) == ((today or date.today()).year, (today or date.today()).month)

        def fetch():
            js = self._get_json(URL_TPEX, {"code": code, "date": f"{y}/{m:02d}/01",
                                           "response": "json"})
            # 新版格式正常就用（就算那個月沒資料）；只有格式認不得時才試舊網址
            if "tables" in js or str(js.get("stat", "")).lower() == "ok":
                return js
            return self._get_json(URL_TPEX_OLD, {"l": "zh-tw", "d": f"{y - 1911}/{m:02d}",
                                                 "stkno": code}, redirect_is_block=False)
        js = self._cached(f"otc_{code}_{y}{m:02d}", cur, fetch)
        name = name_from_tpex(js)
        if name:
            self.names[code] = name
        return parse_tpex(js)

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
        self._warm_mis()
        js = self._get_json(URL_MIS, {"ex_ch": "|".join(ex_ch), "json": "1", "delay": "0",
                                      "_": str(int(time.time() * 1000))})
        rtcode = str(js.get("rtcode", "0000"))
        if rtcode != "0000":
            self._mis_warm = False  # cookie 可能過期，下次重新暖機
            raise TwseError(f"即時報價回應錯誤 rtcode={rtcode} {js.get('rtmessage') or ''}".strip())
        quotes = parse_mis(js)
        got = {lookup[c]: q for c, q in quotes.items() if c in lookup}
        for c, q in quotes.items():
            if q.name and c in lookup and not c.startswith(("t00", "o00")):
                self.names[c] = q.name
        if items and not got:
            self._mis_warm = False
            raise TwseError(f"即時報價沒有回傳任何資料（{js.get('rtmessage') or '空白回應'}）")
        return got

    def _warm_mis(self) -> None:
        """第一次查即時報價前先開看盤首頁拿 session cookie（twstock 等套件也這樣做）。"""
        if self._mis_warm:
            return
        gate = self.gate(URL_MIS_HOME)
        gate.wait_turn()
        try:
            r = self.session.get(URL_MIS_HOME, timeout=TIMEOUT)
        except Exception:  # noqa: BLE001 - 暖機失敗不影響接著查報價
            return
        # 實測首頁可能回 502，但接著查報價仍會成功（並拿到 cookie），所以這裡不判定封鎖
        self._mis_warm = r.status_code == 200


_default_client: TwseClient | None = None


def client() -> TwseClient:
    global _default_client
    if _default_client is None:
        _default_client = TwseClient(progress=print)
    return _default_client


# ---------------------------------------------------------------- 診斷
def diagnose(codes: list, cl: TwseClient | None = None, out=print) -> None:
    """檢查即時報價與股名抓不抓得到，輸出可以直接貼給別人看。只會送出很少的請求。"""
    cl = cl or client()
    now = datetime.now(TAIPEI)
    out(f"== 證交所連線診斷　台北時間 {now:%Y-%m-%d %H:%M}（週{'一二三四五六日'[now.weekday()]}）")
    out("   交易時間：週一～五 09:00–13:30；假日或盤前盤後，即時報價可能只有昨收或沒有資料")
    out(f"== 快取資料夾：{cl.cache_dir}")
    for url in (URL_STOCK_DAY, URL_MIS, URL_TPEX):
        g = cl.gate(url)
        left = g.blocked_remaining()
        out(f"   {g.host:<22}{'封鎖暫停中，還剩 %d 秒' % left if left else '正常'}")
    out(f"== 即時報價（{', '.join(codes)}）")
    ex_ch = "|".join(f"{m}_{c}.tw" for c in codes for m in ("tse", "otc"))
    try:
        cl._warm_mis()
        out(f"   看盤首頁暖機：{'成功' if cl._mis_warm else '沒有拿到 cookie（不一定有影響）'}")
        js = cl._get_json(URL_MIS, {"ex_ch": ex_ch, "json": "1", "delay": "0",
                                    "_": str(int(time.time() * 1000))})
        arr = [it for it in js.get("msgArray") or [] if it.get("c")]
        out(f"   rtcode={js.get('rtcode')} rtmessage={js.get('rtmessage')} 回傳 {len(arr)} 筆")
        for it in arr:
            out(f"   {it.get('c')} {it.get('n')} 市場={it.get('ex')} 成交價 z={it.get('z')} "
                f"昨收 y={it.get('y')} 最佳買價 b={(it.get('b') or '')[:20]} 時間 {it.get('d')} {it.get('t')}")
        if not arr:
            out("   ⚠ 沒有任何資料：可能是非交易時間、cookie 沒拿到，或代號錯誤")
    except Exception as e:  # noqa: BLE001
        out(f"   ⚠ 失敗：{e}")
    out("== 從歷史資料取股名")
    today = date.today()
    for c in codes:
        try:
            df = cl.stock_month(c, today.year, today.month)
            if df.empty and c not in cl.names:
                df = cl.tpex_month(c, today.year, today.month)
            out(f"   {c} → {cl.names.get(c) or '（查不到股名）'}，本月 {len(df)} 筆日 K")
        except Exception as e:  # noqa: BLE001
            out(f"   {c} → ⚠ 失敗：{e}")


if __name__ == "__main__":
    import sys as _sys
    diagnose(_sys.argv[1:] or ["2330", "6488"])
    print("\n把上面的輸出整段貼給 Claude，就能判斷問題在哪裡。")
