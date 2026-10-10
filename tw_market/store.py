"""台股總覽的本機快取（預設 .cache/tw，GitHub Actions 每次執行之間會保存）。

    <資料>/<市場>/<YYYYMMDD>.json   每天的資料（解析後的結果原樣存下）
        資料：quotes 行情、insti 個股三大法人、margin 融資融券、valuation 本益比
              insti_totals 三大法人買賣金額（只有上市 tse）
        市場：tse 上市、otc 上櫃
    otc_index/<YYYYMM>.json          櫃買指數（一個月一個檔，{日期: 開高低收}）
    industry.json                    產業別 {"fetched": 時間, "tse": {代號: 產業}, "otc": {...}}
    calendar.json                    {"closed": [休市的日期]}（平日但沒開盤，例如颱風假、連假）

過去的資料不會再變，存了就不會重抓。寫檔一律先寫暫存檔再改名，
程式中途被停掉也不會留下寫一半的檔案；萬一有壞掉的檔案，讀取時會刪掉，下次重抓。
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


def read_json(path: Path):
    """讀不到或壞掉時回傳 None；壞掉的檔案順便刪掉（下次會重抓）。"""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        try:
            Path(path).unlink()
        except OSError:
            pass
        return None


class Store:
    def __init__(self, root: str | Path = DEFAULT_DIR):
        self.root = Path(root)

    # ---- 每天的資料 ----
    def path(self, dataset: str, market: str, d: date) -> Path:
        return self.root / dataset / market / f"{d:%Y%m%d}.json"

    def has(self, dataset: str, market: str, d: date) -> bool:
        return self.path(dataset, market, d).is_file()

    def load(self, dataset: str, market: str, d: date) -> dict | None:
        js = read_json(self.path(dataset, market, d))
        return js if isinstance(js, dict) else None

    def save(self, dataset: str, market: str, d: date, obj: dict) -> None:
        write_json(self.path(dataset, market, d), obj)

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
            if d < keep_from:
                try:
                    self.path(dataset, market, d).unlink()
                    n += 1
                except OSError:
                    pass
        return n

    # ---- 櫃買指數（一個月一個檔） ----
    def _index_path(self, y: int, m: int) -> Path:
        return self.root / "otc_index" / f"{y}{m:02d}.json"

    def otc_index(self, y: int, m: int) -> dict:
        js = read_json(self._index_path(y, m))
        return js if isinstance(js, dict) else {}

    def save_otc_index(self, y: int, m: int, rows: dict) -> None:
        write_json(self._index_path(y, m), dict(sorted(rows.items())))

    def prune_otc_index(self, keep_from: date) -> None:
        folder = self.root / "otc_index"
        if not folder.is_dir():
            return
        for p in folder.glob("*.json"):
            if re.fullmatch(r"\d{6}\.json", p.name) and p.name[:6] < f"{keep_from:%Y%m}":
                try:
                    p.unlink()
                except OSError:
                    pass

    # ---- 產業別 ----
    def industry(self) -> dict:
        js = read_json(self.root / "industry.json")
        return js if isinstance(js, dict) else {}

    def save_industry(self, js: dict) -> None:
        write_json(self.root / "industry.json", js)

    # ---- 休市日 ----
    def closed(self) -> set[date]:
        js = read_json(self.root / "calendar.json")
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
