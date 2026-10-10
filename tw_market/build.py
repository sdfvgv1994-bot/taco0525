"""產生台股總覽網站（tw/ 資料夾）：先更新快取，再把快取組成網頁要的資料（格式見 contract.py）。

    python -m tw_market.build --out _site/tw              # 抓新資料＋產生網頁
    python -m tw_market.build --out _site/tw --no-fetch   # 不連網，只用快取重新產生
    python -m tw_market.build --out _site/tw --demo       # 示範資料（不連網）

GitHub Actions 每 15 分鐘執行一次（.github/workflows/board.yml）。快取裡完全沒有行情時，
只放網頁本身（網頁會顯示「台股資料還沒產生」）。資料格式檢查不通過就報錯，不更新網頁。
"""
from __future__ import annotations

import argparse
import math
from datetime import date, datetime, timezone
from pathlib import Path

from tw_market import contract, site
from tw_market.contract import MARKETS, STOCK_FIELDS, classify
from tw_market.store import DEFAULT_DIR, Store
from tw_market.update import PUBLISH, taipei, update

LIMIT_PCT = 9.5               # 漲跌幅超過這個當作漲停 / 跌停
LATE = {"insti": ("三大法人", "下午 3 點半後"), "margin": ("融資融券", "晚上 9 點後"),
        "valuation": ("本益比、殖利率", "下午 3 點後")}


# ---------------------------------------------------------------- 小工具
def _n(v) -> float | int | None:
    """有限的數字才留下（JSON 不能有 NaN）。"""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return v if math.isfinite(v) else None


def _i(v) -> int | None:
    v = _n(v)
    return None if v is None else round(v)


def _pct(change, close) -> float | None:
    change, close = _n(change), _n(close)
    if change is None or close is None or close - change <= 0:
        return None
    return round(change / (close - change) * 100, 2)


def _md(d: date) -> str:
    return f"{d:%m/%d}"


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _rows(js) -> dict:
    rows = (js or {}).get("rows")
    return rows if isinstance(rows, dict) else {}


def _newest(store: Store, dataset: str, market: str, upto: date, since: date | None = None):
    """upto（含）以前最新一個讀得到的檔 → (日期, 內容)；沒有就 (None, None)。"""
    for d in reversed(store.dates(dataset, market)):
        if d > upto:
            continue
        if since and d < since:
            break
        js = store.load(dataset, market, d)
        if js is not None:
            return d, js
    return None, None


# ---------------------------------------------------------------- 組合資料
def _stock(code, q, market, industry, val, ins, mar) -> dict:
    kind = classify(code)
    close, change = _n(q.get("close")), _n(q.get("change"))
    pe = _n(q.get("pe"))
    if pe is None or pe <= 0:
        pe = _n(val.get("pe"))
    return {
        "code": code, "name": str(q.get("name") or "").strip() or code, "market": market, "type": kind,
        "industry": "ETF" if kind == "etf" else industry,
        "close": close, "change": change, "change_pct": _pct(change, close),
        "open": _n(q.get("open")), "high": _n(q.get("high")), "low": _n(q.get("low")),
        "volume": _i(q.get("volume")), "value": _i(q.get("value")),
        "pe": pe if pe is not None and pe > 0 else None,
        "dividend_yield": _n(val.get("dividend_yield")), "pb": _n(val.get("pb")),
        "foreign": _i(ins.get("foreign")), "trust": _i(ins.get("trust")), "dealer": _i(ins.get("dealer")),
        "inst_total": _i(ins.get("total")),
        "margin_balance": _i(mar.get("margin_balance")), "margin_change": _i(mar.get("margin_change")),
        "short_balance": _i(mar.get("short_balance")), "short_change": _i(mar.get("short_change")),
    }


def _market(mk: str, rows: list[dict], quotes, index) -> dict:
    out = {"name": MARKETS[mk], "index": index, "value": None, "volume": None,
           "up": None, "down": None, "flat": None, "limit_up": None, "limit_down": None}
    if not rows:
        return out
    totals = (quotes or {}).get("totals") or {}
    st = [r for r in rows if r["type"] == "stock"]
    chg = [r for r in st if r["change"] is not None]
    pct = [r["change_pct"] for r in st if r["change_pct"] is not None]
    out.update({
        "value": _i(totals.get("value")) if _i(totals.get("value")) is not None
        else sum(r["value"] or 0 for r in rows),
        "volume": _i(totals.get("volume")) if _i(totals.get("volume")) is not None
        else sum(r["volume"] or 0 for r in rows),
        "up": sum(r["change"] > 0 for r in chg), "down": sum(r["change"] < 0 for r in chg),
        "flat": sum(r["change"] == 0 for r in chg),
        "limit_up": sum(p >= LIMIT_PCT for p in pct), "limit_down": sum(p <= -LIMIT_PCT for p in pct),
    })
    return out


def _tse_index(quotes) -> dict | None:
    idx = (quotes or {}).get("index")
    if not isinstance(idx, dict) or _n(idx.get("close")) is None:
        return None
    close, change = _n(idx.get("close")), _n(idx.get("change"))
    pct = _n(idx.get("change_pct"))
    return {"name": idx.get("name") or "加權指數", "close": close, "change": change,
            "change_pct": pct if pct is not None else _pct(change, close)}


def _otc_index(store: Store, d: date | None) -> dict | None:
    row = store.otc_index(d.year, d.month).get(d.isoformat()) if d else None
    if not isinstance(row, dict) or _n(row.get("close")) is None:
        return None
    close, change = _n(row.get("close")), _n(row.get("change"))
    return {"name": "櫃買指數", "close": close, "change": change, "change_pct": _pct(change, close)}


def _lag_notes(dates: dict, shown: dict, latest: date, now: datetime, markets_with_rows) -> list[str]:
    """本益比、法人、融資融券比行情舊，或某個市場沒有時的說明。
    今天還沒到公布時間的合成一句，例如「三大法人（下午 3 點半後）、融資融券（晚上 9 點後）才公布」。"""
    notes, waiting = [], []
    for ds in ("valuation", "insti", "margin"):
        label, _ = LATE[ds]
        sd = shown[ds]
        if sd is None:
            notes.append(f"{label}還沒有資料")
            continue
        if sd < latest:
            if now.date() == latest and now.time() < PUBLISH[ds]:
                waiting.append((ds, sd))
            else:
                notes.append(f"{label}還沒抓到 {_md(latest)} 的，先顯示 {_md(sd)} 的")
        for mk in markets_with_rows:
            d = dates[ds].get(mk)
            if d != sd:
                notes.append(f"{MARKETS[mk]}{label}是 {_md(d)} 的資料" if d else f"{MARKETS[mk]}{label}還沒有資料")
    if len(waiting) == 1 or len({sd for _, sd in waiting}) > 1:   # 一項、或日期不一樣（少見）就分開寫
        notes[:0] = [f"{LATE[ds][0]}{LATE[ds][1]}才公布，先顯示 {_md(sd)} 的" for ds, sd in waiting]
    elif waiting:
        what = "、".join(f"{LATE[ds][0]}（{LATE[ds][1]}）" for ds, _ in waiting)
        notes.insert(0, f"{what}才公布，先顯示 {_md(waiting[0][1])} 的")
    return notes


def _hist_window(store: Store, latest: date) -> list[date]:
    days = set(store.dates("quotes", "tse")) | set(store.dates("quotes", "otc"))
    return sorted(d for d in days if d <= latest)[-contract.HIST_MAX_DAYS:]


def _hists(store: Store, window: list[date], names: dict) -> tuple[dict, int]:
    """個股歷史 K 線：一天一天讀（不一次全部載入，省記憶體），只留有收盤價的日子。
    回傳 (歷史, 讀得到的天數)。"""
    cols = ("open", "high", "low", "close", "volume")
    acc: dict[str, dict] = {}
    days = 0
    for d in window:
        iso = d.isoformat()
        seen = set()
        maps = [_rows(store.load("quotes", mk, d)) for mk in ("tse", "otc")]
        days += any(maps)
        for rows in maps:
            for code, r in rows.items():
                if code in seen or code not in names or not isinstance(r, dict):
                    continue
                close = _n(r.get("close"))
                if close is None:
                    continue
                seen.add(code)
                h = acc.setdefault(code, {"dates": [], **{c: [] for c in cols}})
                h["dates"].append(iso)
                for c in ("open", "high", "low"):
                    h[c].append(_n(r.get(c)))
                h["close"].append(close)
                h["volume"].append(_i(r.get("volume")))
    return {code: {"code": code, "name": names[code], **h, "inst": None, "margin": None}
            for code, h in acc.items()}, days


def _chips(store: Store, dataset: str, cols: tuple, latest: date, hists: dict) -> None:
    """最近 CHIPS_MAX_DAYS 天的法人或融資融券，放進每檔的歷史；完全沒有就是 null。"""
    days = sorted(set(store.dates(dataset, "tse")) | set(store.dates(dataset, "otc")))
    loaded = []
    for d in reversed([d for d in days if d <= latest]):
        maps = [_rows(store.load(dataset, mk, d)) for mk in ("tse", "otc")]
        if any(maps):
            loaded.append((d.isoformat(), maps))
        if len(loaded) >= contract.CHIPS_MAX_DAYS:
            break
    loaded.reverse()
    key = "inst" if dataset == "insti" else "margin"
    for code, h in hists.items():
        vals = {c: [] for c in cols}
        for _, (tse, otc) in loaded:
            r = tse.get(code) or otc.get(code)
            for c in cols:
                vals[c].append(_i(r.get(c)) if isinstance(r, dict) else None)
        if any(v is not None for c in cols for v in vals[c]):
            h[key] = {"dates": [iso for iso, _ in loaded], **vals}


def assemble(store: Store, now: datetime | None = None, log=print):
    """快取 → (summary, stocks, hists)；快取裡完全沒有行情時回傳 None。"""
    now = taipei(now)
    notes = []
    tse_d, tse_q = _newest(store, "quotes", "tse", date.max)
    if tse_d is None:
        otc_d, otc_q = _newest(store, "quotes", "otc", date.max)
        if otc_d is None:
            return None
        latest = otc_d
        notes.append("上市行情還沒抓到，目前只有上櫃股票")
    else:
        latest = tse_d
        otc_d, otc_q = _newest(store, "quotes", "otc", latest)
        newer = [d for d in store.dates("quotes", "otc") if d > latest]
        if newer:
            notes.append(f"上市行情還沒抓到 {_md(newer[-1])} 的，先顯示 {_md(latest)} 的")
        if otc_d is None:
            notes.append("上櫃行情還沒抓到，目前只有上市股票")
        elif otc_d < latest:
            notes.append(f"上櫃行情是 {_md(otc_d)} 的（{_md(latest)} 的還沒抓到）")
    quotes = {"tse": tse_q, "otc": otc_q}
    window = _hist_window(store, latest)
    since = window[-contract.CHIPS_MAX_DAYS:][0]      # 太舊的籌碼資料不拿來當最新的

    # 本益比、法人、融資融券：各市場用 latest（含）以前最新的一天
    extra, dates = {}, {}
    for ds in ("valuation", "insti", "margin"):
        dates[ds] = {}
        for mk in MARKETS:
            d, js = _newest(store, ds, mk, latest, since)
            dates[ds][mk], extra[(ds, mk)] = d, _rows(js)
    ind = store.industry()

    stocks, seen = [], set()
    for mk in MARKETS:
        imap = ind.get(mk) if isinstance(ind.get(mk), dict) else {}
        other = ind.get("otc" if mk == "tse" else "tse")
        other = other if isinstance(other, dict) else {}
        rows = _rows(quotes[mk])
        for code in sorted(rows):
            # 權證等不收；同一天兩個市場都有（轉上市）時用上市的
            if classify(code) is None or not isinstance(rows[code], dict) or code in seen:
                continue
            seen.add(code)
            val, ins, mar = (extra[(ds, mk)].get(code) or {} for ds in ("valuation", "insti", "margin"))
            stocks.append(_stock(code, rows[code], mk, imap.get(code) or other.get(code), val, ins, mar))
    by_market = {mk: [s for s in stocks if s["market"] == mk] for mk in MARKETS}
    with_rows = [mk for mk in MARKETS if by_market[mk]]

    shown = {ds: max((d for d in dates[ds].values() if d), default=None) for ds in dates}
    notes += _lag_notes(dates, shown, latest, now, with_rows)

    tot_d, tot = _newest(store, "insti_totals", "tse", latest, since)
    institutional = None
    if tot_d:
        institutional = {"date": tot_d.isoformat(), "otc": None,
                         "tse": {k: _n(tot.get(k)) for k in ("foreign", "trust", "dealer", "total")}}
        notes.append("三大法人買賣金額只有上市（上櫃暫不提供）")

    hists, history_days = _hists(store, window, {s["code"]: s["name"] for s in stocks})
    _chips(store, "insti", ("foreign", "trust", "dealer"), latest, hists)
    _chips(store, "margin", ("margin_balance", "short_balance"), latest, hists)
    if history_days < contract.HIST_MAX_DAYS:
        notes.append(f"歷史 K 線還在補齊中（目前 {history_days} 天）")

    summary = {
        "date": latest.isoformat(),
        "updated_utc": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "markets": {"tse": _market("tse", by_market["tse"], tse_q, _tse_index(tse_q)),
                    "otc": _market("otc", by_market["otc"], otc_q, _otc_index(store, otc_d))},
        "institutional": institutional,
        "chips_date": _iso(shown["insti"]), "margin_date": _iso(shown["margin"]),
        "valuation_date": _iso(shown["valuation"]),
        "history_days": history_days,
        "notes": notes,
    }
    return summary, stocks, hists


def validate(summary: dict, stocks: list[dict], hists: dict) -> list[str]:
    p = contract.check_summary(summary)
    p += contract.check_stocks({"date": summary.get("date"), "fields": STOCK_FIELDS,
                                "rows": [[s.get(k) for k in STOCK_FIELDS] for s in stocks]})
    for code, h in hists.items():
        p += [f"hist/{code}：{x}" for x in contract.check_hist(h)]
    return p


# ---------------------------------------------------------------- 產生網站
def build(out, cache_dir=DEFAULT_DIR, now: datetime | None = None, client=None, fetch: bool = True,
          demo: bool = False, log=print, **update_kwargs) -> dict:
    """產生 out 資料夾。fetch=False 時完全不連網，只用快取。回傳簡單的統計。"""
    out, now = Path(out), taipei(now)
    stats = None
    if demo:
        from tw_market import demo as demo_data
        summary, stocks, hists = demo_data.dataset(now.date())
    else:
        store = Store(cache_dir)
        if fetch:
            if client is None:
                from common.twse import TwseClient
                client = TwseClient()
            stats = update(store, client, now, log=log, **update_kwargs)
        data = assemble(store, now, log)
        if data is None:
            log("台股：快取裡還沒有任何行情，只放網頁（會顯示「台股資料還沒產生」）")
            site.write_placeholder(out)
            return {"date": None, "stocks": 0, "hists": 0, "update": stats}
        summary, stocks, hists = data
    problems = validate(summary, stocks, hists)
    if problems:
        shown = "\n".join(f"  - {p}" for p in problems[:20])
        more = f"\n  …還有 {len(problems) - 20} 個" if len(problems) > 20 else ""
        raise SystemExit(f"台股資料格式有問題，這次不更新網頁：\n{shown}{more}")
    site.write(out, summary, stocks, hists)
    for n in summary["notes"]:
        log(f"  · {n}")
    return {"date": summary["date"], "stocks": len(stocks), "hists": len(hists), "update": stats}


def main(argv=None):
    ap = argparse.ArgumentParser(description="產生台股總覽網頁（資料來自證交所、櫃買中心）")
    ap.add_argument("--out", default="_site/tw", help="輸出資料夾（預設 _site/tw）")
    ap.add_argument("--demo", action="store_true", help="示範資料（不連網）")
    ap.add_argument("--no-fetch", action="store_true", help="不連網，只用快取裡的資料重新產生")
    ap.add_argument("--cache", default=str(DEFAULT_DIR), help="快取資料夾（預設 .cache/tw）")
    ap.add_argument("--max-per-host", type=int, default=25, help="每個網站最多連幾次（預設 25）")
    ap.add_argument("--deadline", type=float, default=240, help="抓資料最多幾秒（預設 240）")
    a = ap.parse_args(argv)
    r = build(Path(a.out), cache_dir=Path(a.cache), fetch=not a.no_fetch, demo=a.demo,
              max_per_host=a.max_per_host, deadline_s=a.deadline)
    if r["date"]:
        print(f"台股總覽完成：{r['date']}，{r['stocks']} 檔、{r['hists']} 檔歷史，輸出到 {a.out}")
    else:
        print(f"台股總覽：還沒有資料，只放網頁到 {a.out}")


if __name__ == "__main__":
    main()
