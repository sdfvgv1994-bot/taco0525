"""產生雲端版看板（靜態網頁），給 GitHub Actions 定時執行後放到 GitHub Pages。

    python world_market/build_static.py --out _site          # 真實資料
    python world_market/build_static.py --out _site --demo   # 示範資料

產出：
    _site/index.html                    看板網頁（和本機版同一份，切成讀靜態檔的模式）
    _site/data/overview.json            總覽
    _site/data/history/<代號>.json      每個商品近 5 年日收盤，網頁自己切 1 個月～5 年
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_market.data import DataService, yahoo_code  # noqa: E402
from world_market.markets import ALL_ITEMS  # noqa: E402

STATIC = Path(__file__).parent / "static"
ROOT = Path(__file__).resolve().parent.parent
HISTORY_CACHE = ROOT / ".cache" / "board_history"   # 上一次成功抓到的歷史資料（GitHub Actions 快取會保存）
HISTORY_PERIOD = "5y"
MIN_OK_RATIO = 0.5   # 抓得到的項目少於一半就當作失敗，保留上一次部署的網頁


def safe_name(symbol: str) -> str:
    """檔名用：^GSPC → _GSPC、BTC-USD → BTC_USD（網頁的 JS 用同一條規則）。"""
    return re.sub(r"[^A-Za-z0-9]", "_", symbol)


def _dump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def _history_rows(df) -> dict:
    df = df.dropna(subset=["Close"])
    return {"dates": [f"{i:%Y-%m-%d}" for i in df.index],
            "close": [round(float(v), 6) for v in df["Close"]]}


def build(out: Path, service: DataService | None = None, log=print,
          history_cache: Path | None = HISTORY_CACHE) -> dict:
    svc = service or DataService()
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    (out / "data" / "history").mkdir(parents=True)

    ov = svc.overview(force=True)
    items = [it for g in ov["groups"] for it in g["items"]]
    ok = [it for it in items if "error" not in it]
    log(f"總覽：{len(ok)}/{len(items)} 個項目有資料")
    for it in items:
        if "error" in it:
            log(f"  ⚠ {it['name']}（{it['symbol']}）：{it['error']}")
    if len(ok) < len(items) * MIN_OK_RATIO:
        raise SystemExit(f"抓得到的項目太少（{len(ok)}/{len(items)}），這次不更新網頁")

    # 歷史資料：Yahoo 的項目一次批次下載，台灣項目走證交所（有快取，只會抓當月）
    histories, errors = {}, {}
    yahoo_items = [it for it in ALL_ITEMS.values() if it.source != "twse"]
    if svc.demo:
        targets = list(ALL_ITEMS)
    else:
        try:
            got = svc.yahoo_download([yahoo_code(it) for it in yahoo_items], HISTORY_PERIOD)
        except Exception as e:  # noqa: BLE001
            got = {}
            log(f"  ⚠ Yahoo 歷史資料下載失敗：{e}")
        for it in yahoo_items:
            df = got.get(yahoo_code(it))
            if df is not None and not df.empty:
                histories[it.symbol] = {**_history_rows(df), "source": "Yahoo Finance"}
        targets = [s for s, it in ALL_ITEMS.items() if it.source == "twse"]
    for sym in targets:
        try:
            h = svc.history(sym, HISTORY_PERIOD)
            histories[sym] = {"dates": h["dates"], "close": h["close"], "source": h["source"]}
        except Exception as e:  # noqa: BLE001 - 一檔失敗不影響其他檔
            errors[sym] = str(e)
    reused = []
    for sym, it in ALL_ITEMS.items():
        name = f"{safe_name(sym)}.json"
        if sym in histories:
            doc = {"symbol": sym, "name": it.name, "decimals": it.decimals, **histories[sym]}
            if history_cache and not svc.demo:
                _dump(history_cache / name, doc)          # 存一份，下次抓不到時用
        elif history_cache and (history_cache / name).exists():
            doc = json.loads((history_cache / name).read_text(encoding="utf-8"))
            reused.append(sym)                            # 這次抓不到，沿用上一次成功的資料
        else:
            errors.setdefault(sym, "查不到歷史資料")
            continue
        _dump(out / "data" / "history" / name, doc)
    written = len(histories) + len(reused)
    log(f"歷史資料：{written}/{len(ALL_ITEMS)} 個項目（其中 {len(reused)} 個沿用上一次的資料）")
    for sym, e in errors.items():
        if sym not in reused:
            log(f"  ⚠ {ALL_ITEMS[sym].name}（{sym}）：{e}")
    if written < len(ALL_ITEMS) * MIN_OK_RATIO:
        raise SystemExit(f"歷史資料太少（{written}/{len(ALL_ITEMS)}），這次不更新網頁")

    # 財經新聞：某一類抓不到時沿用上一次成功的
    news_cache = history_cache / "news.json" if history_cache and not svc.demo else None
    previous = None
    if news_cache and news_cache.exists():
        try:
            previous = json.loads(news_cache.read_text(encoding="utf-8"))
        except ValueError:
            previous = None
    from world_market import news as nw
    news = nw.merge_with_previous(svc.news(force=True), previous)
    _dump(out / "data" / "news.json", news)
    if news_cache and any(c["items"] and not c.get("stale") for c in news["categories"]):
        _dump(news_cache, news)
    log("新聞：" + "、".join(f"{c['name']} {len(c['items'])} 則" + ("（沿用上次）" if c.get("stale") else "")
                           for c in news["categories"]))

    _dump(out / "data" / "overview.json", {**ov, "static": True, "history_period": HISTORY_PERIOD})
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    marker = '<script>\n"use strict";'
    if marker not in html:
        raise SystemExit("index.html 的格式和預期不同，找不到主程式的位置")
    html = html.replace(marker, "<script>window.WM_STATIC = true;</script>\n" + marker, 1)
    (out / "index.html").write_text(html, encoding="utf-8")
    (out / ".nojekyll").write_text("")
    return {"items": len(items), "ok": len(ok), "histories": written, "reused": reused,
            "errors": {k: v for k, v in errors.items() if k not in reused}}


def main(argv=None):
    ap = argparse.ArgumentParser(description="產生雲端版全球股市看板")
    ap.add_argument("--out", default="_site", help="輸出資料夾（預設 _site）")
    ap.add_argument("--demo", action="store_true", help="示範資料（不連網）")
    args = ap.parse_args(argv)
    result = build(Path(args.out), DataService(demo=args.demo))
    print(f"完成：{args.out}（總覽 {result['ok']}/{result['items']}，歷史 {result['histories']} 個）")


if __name__ == "__main__":
    main()
