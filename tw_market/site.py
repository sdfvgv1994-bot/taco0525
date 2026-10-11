"""把台股總覽的資料寫成靜態網站（tw/ 資料夾）。

先寫到旁邊的暫存資料夾，全部寫完才換掉原本的資料夾：中途出錯或被停掉，原本的網頁不受影響。
"""
from __future__ import annotations

import json
import os
import shutil
from contextlib import contextmanager
from pathlib import Path

from tw_market.contract import STOCK_FIELDS

STATIC = Path(__file__).resolve().parent / "static"


def _dump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False),
                    encoding="utf-8")


@contextmanager
def _staging(out: Path):
    """給一個暫存資料夾寫檔；成功結束才換成 out，出錯就刪掉暫存、保留原本的 out。"""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".{out.name}.tmp")
    old = out.with_name(f".{out.name}.old")
    for p in (tmp, old):              # 上一次被中途停掉留下來的
        if p.exists():
            shutil.rmtree(p)
    tmp.mkdir()
    try:
        yield tmp
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    if out.exists():
        os.replace(out, old)
    try:
        os.replace(tmp, out)
    except BaseException:
        if old.exists():
            os.replace(old, out)
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    shutil.rmtree(old, ignore_errors=True)


def write(out: Path, summary: dict, stocks: list[dict], hists: dict[str, dict]) -> None:
    """stocks 是 dict 的清單（key 為 STOCK_FIELDS）；hists 是 {代號: 個股歷史}。"""
    with _staging(out) as tmp:
        (tmp / "data" / "hist").mkdir(parents=True)
        _dump(tmp / "data" / "summary.json", summary)
        _dump(tmp / "data" / "stocks.json", {"date": summary["date"], "fields": STOCK_FIELDS,
                                             "rows": [[s.get(k) for k in STOCK_FIELDS] for s in stocks]})
        for code, h in hists.items():
            _dump(tmp / "data" / "hist" / f"{code}.json", h)
        shutil.copyfile(STATIC / "index.html", tmp / "index.html")


def write_placeholder(out: Path) -> None:
    """還沒有任何資料時只放網頁（網頁讀不到 summary.json 會顯示「台股資料還沒產生」）。"""
    with _staging(out) as tmp:
        shutil.copyfile(STATIC / "index.html", tmp / "index.html")
