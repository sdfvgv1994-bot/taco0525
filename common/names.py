"""台股代號 → 中文股名的本機對照表（不連網）。

來源是台股總覽（tw_market）存下的每日收盤行情：.cache/tw/quotes/tse|otc/YYYYMMDD.json，
一個檔就有當天全部上市或上櫃股票（含 ETF）的名稱。雲端每次執行前會先取出這份快取，
所以證交所、櫃買中心暫時封鎖連線時，自選股照樣查得到中文股名，也不用多送請求。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

TW_CACHE = Path(__file__).resolve().parent.parent / ".cache" / "tw"
_DAY = re.compile(r"\d{8}\.json")
_memo: dict = {}   # 資料夾 → ((最新檔名, 修改時間), 對照表)


def _newest(folder: Path) -> dict:
    """資料夾裡最新、讀得懂的一天 → {代號: 股名}。"""
    try:
        files = sorted((p for p in folder.iterdir() if _DAY.fullmatch(p.name)), reverse=True)
    except OSError:
        return {}
    for p in files[:5]:   # 最新的檔壞掉就往前找幾天
        try:
            key = (p.name, p.stat().st_mtime_ns)
            hit = _memo.get(folder)
            if hit and hit[0] == key:
                return hit[1]
            rows = json.loads(p.read_text(encoding="utf-8")).get("rows")
        except (OSError, ValueError, AttributeError):
            continue
        if not isinstance(rows, dict) or not rows:
            continue
        names = {c: str(r["name"]).strip() for c, r in rows.items()
                 if isinstance(r, dict) and str(r.get("name") or "").strip()}
        _memo[folder] = (key, names)
        return names
    return {}


def listing_names(root: str | Path | None = None) -> dict:
    """{代號: 股名}，上市和上櫃合在一起；沒有快取就是空的。"""
    root = Path(root) if root else TW_CACHE
    out = _newest(root / "quotes" / "otc")
    return {**out, **_newest(root / "quotes" / "tse")}


def listing_name(code: str, root: str | Path | None = None) -> str | None:
    return listing_names(root).get(code)
