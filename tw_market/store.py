"""台股總覽的本機快取（預設 .cache/tw，GitHub Actions 每次執行之間會保存）。

    <資料>/<市場>/<YYYYMMDD>.json   每天的資料（解析後的結果原樣存下）
        資料：quotes 行情、insti 個股三大法人、margin 融資融券、valuation 本益比
              insti_totals 三大法人買賣金額（只有上市 tse）
        市場：tse 上市、otc 上櫃
    otc_index/<YYYYMM>.json          櫃買指數（一個月一個檔，{日期: 開高低收}）
    industry.json                    產業別 {"fetched": 時間, "tse": {代號: 產業}, "otc": {...}}
    calendar.json                    {"closed": [休市的日期]}（平日但沒開盤，例如颱風假、連假）
    status.json                      抓資料的紀錄（不是資料本身，GitHub Actions 另外保存，每次都存）：
                                     {"checked_utc": 任一個市場最後一次確認資料是最新的時間,
                                      "checked": {"tse": 上市最後一次確認的時間, "otc": ...},
                                      "missing": {"quotes/otc/20260915": [問了幾次, 最後一次問的日期]}}

過去的資料不會再變，存了就不會重抓。寫檔一律先寫暫存檔再改名，
程式中途被停掉也不會留下寫一半的檔案；萬一有壞掉的檔案（不是 JSON、或內容的格式不對），
讀取時會刪掉，重抓。
資料有變動（存檔、刪檔、記休市日）時 Store.changed 會變成 True，GitHub Actions 才需要存快取。
"""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIR = ROOT / ".cache" / "tw"
DATASETS = ("quotes", "insti", "margin", "valuation", "insti_totals")
MARKETS = ("tse", "otc")
_DAY_FILE = re.compile(r"(\d{4})(\d{2})(\d{2})\.json")


def clean(obj):
    """把 NaN、無限大換成 None（JSON 不允許），其他原樣。"""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    return obj


def write_json(path: Path, obj) -> None:
    """先寫到同一個資料夾的暫存檔，再一次換成正式檔名。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(clean(obj), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _unlink(path: Path) -> bool:
    try:
        Path(path).unlink()
        return True
    except OSError:
        return False


def read_json(path: Path, on_delete=None):
    """讀不到或壞掉時回傳 None；壞掉的檔案順便刪掉（下次會重抓），有刪就呼叫 on_delete()。"""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        if _unlink(path) and on_delete:
            on_delete()
        return None


def valid_day(dataset: str, d: date, js) -> bool:
    """每天的資料檔內容對不對：日期要和檔名一樣，而且要有資料
    （三大法人買賣金額要有數字，其他要有至少一檔股票）。"""
    if not isinstance(js, dict) or js.get("date") != d.isoformat():
        return False
    if dataset == "insti_totals":
        return any(isinstance(js.get(k), (int, float)) and not isinstance(js.get(k), bool)
                   for k in ("foreign", "trust", "dealer", "total"))
    rows = js.get("rows")
    return isinstance(rows, dict) and bool(rows)


class Store:
    def __init__(self, root: str | Path = DEFAULT_DIR, on_change=None):
        self.root = Path(root)
        self.changed = False          # 這次有沒有改到快取裡的資料
        self.on_change = on_change    # 第一次改到時呼叫（GitHub Actions 用來決定要不要存快取）
        self._ok: dict[Path, tuple] = {}   # 已經檢查過、內容正確的檔案（檔案沒變就不再讀一次）

    def _touch(self) -> None:
        if not self.changed:
            self.changed = True
            if self.on_change:
                self.on_change()

    def _remember(self, path: Path) -> None:
        try:
            st = path.stat()
            self._ok[path] = (st.st_mtime_ns, st.st_size)
        except OSError:
            self._ok.pop(path, None)

    # ---- 每天的資料 ----
    def path(self, dataset: str, market: str, d: date) -> Path:
        return self.root / dataset / market / f"{d:%Y%m%d}.json"

    def has(self, dataset: str, market: str, d: date) -> bool:
        """有這天的檔案、而且內容正確（格式不對的檔會被刪掉，當作沒有）。"""
        p = self.path(dataset, market, d)
        try:
            st = p.stat()
        except OSError:
            return False
        if self._ok.get(p) == (st.st_mtime_ns, st.st_size):
            return True
        return self.load(dataset, market, d) is not None

    def load(self, dataset: str, market: str, d: date) -> dict | None:
        """讀一天的資料；格式不對（例如 {}、null、日期和檔名不同）的檔案刪掉，回傳 None（之後會重抓）。"""
        p = self.path(dataset, market, d)
        js = read_json(p, self._touch)
        if js is None:
            return None
        if not valid_day(dataset, d, js):
            if _unlink(p):
                self._touch()
            self._ok.pop(p, None)
            return None
        self._remember(p)
        return js

    def save(self, dataset: str, market: str, d: date, obj: dict) -> None:
        p = self.path(dataset, market, d)
        write_json(p, obj)
        self._touch()
        self._remember(p)

    def dates(self, dataset: str, market: str) -> list[date]:
        """有存檔的日期（舊 → 新）。"""
        folder = self.root / dataset / market
        out = []
        if folder.is_dir():
            for p in folder.iterdir():
                m = _DAY_FILE.fullmatch(p.name)
                if m:
                    try:
                        out.append(date(*(int(x) for x in m.groups())))
                    except ValueError:
                        continue
        return sorted(out)

    def prune(self, dataset: str, market: str, keep_from: date) -> int:
        """刪掉 keep_from 之前的檔案（太舊、用不到了），回傳刪了幾個。"""
        n = 0
        for d in self.dates(dataset, market):
            if d < keep_from and _unlink(self.path(dataset, market, d)):
                n += 1
        if n:
            self._touch()
        return n

    # ---- 櫃買指數（一個月一個檔） ----
    def _index_path(self, y: int, m: int) -> Path:
        return self.root / "otc_index" / f"{y}{m:02d}.json"

    def otc_index(self, y: int, m: int) -> dict:
        js = read_json(self._index_path(y, m), self._touch)
        return js if isinstance(js, dict) else {}

    def save_otc_index(self, y: int, m: int, rows: dict) -> None:
        write_json(self._index_path(y, m), dict(sorted(rows.items())))
        self._touch()

    def prune_otc_index(self, keep_from: date) -> None:
        folder = self.root / "otc_index"
        if not folder.is_dir():
            return
        for p in folder.glob("*.json"):
            if re.fullmatch(r"\d{6}\.json", p.name) and p.name[:6] < f"{keep_from:%Y%m}" and _unlink(p):
                self._touch()

    # ---- 產業別 ----
    def industry(self) -> dict:
        js = read_json(self.root / "industry.json", self._touch)
        return js if isinstance(js, dict) else {}

    def save_industry(self, js: dict) -> None:
        write_json(self.root / "industry.json", js)
        self._touch()

    # ---- 休市日 ----
    def closed(self) -> set[date]:
        js = read_json(self.root / "calendar.json", self._touch)
        items = js.get("closed") if isinstance(js, dict) else None
        out = set()
        for s in items if isinstance(items, list) else []:
            try:
                out.add(date.fromisoformat(s))
            except (TypeError, ValueError):
                continue
        return out

    def add_closed(self, days) -> None:
        old = self.closed()
        if set(days) - old:
            write_json(self.root / "calendar.json",
                       {"closed": sorted(d.isoformat() for d in old | set(days))})
            self._touch()

    # ---- 抓資料的紀錄（不算資料變動） ----
    def status(self) -> dict:
        js = read_json(self.root / "status.json")
        return js if isinstance(js, dict) else {}

    def save_status(self, js: dict) -> None:
        write_json(self.root / "status.json", js)
