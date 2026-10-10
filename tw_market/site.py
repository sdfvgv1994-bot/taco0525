"""把台股總覽的資料寫成靜態網站（tw/ 資料夾）。"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from tw_market.contract import STOCK_FIELDS

STATIC = Path(__file__).resolve().parent / "static"


def _dump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False),
                    encoding="utf-8")


def write(out: Path, summary: dict, stocks: list[dict], hists: dict[str, dict]) -> None:
    """stocks 是 dict 的清單（key 為 STOCK_FIELDS）；hists 是 {代號: 個股歷史}。"""
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    (out / "data" / "hist").mkdir(parents=True)
    _dump(out / "data" / "summary.json", summary)
    _dump(out / "data" / "stocks.json", {"date": summary["date"], "fields": STOCK_FIELDS,
                                         "rows": [[s.get(k) for k in STOCK_FIELDS] for s in stocks]})
    for code, h in hists.items():
        _dump(out / "data" / "hist" / f"{code}.json", h)
    shutil.copyfile(STATIC / "index.html", out / "index.html")
