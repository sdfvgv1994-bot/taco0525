"""產生台股總覽網站（tw/ 資料夾）：先更新快取，再把快取組成網頁要的資料（格式見 contract.py）。

    python -m tw_market.build --out _site/tw              # 抓新資料＋產生網頁
    python -m tw_market.build --out _site/tw --no-fetch   # 不連網，只用快取重新產生
    python -m tw_market.build --out _site/tw --demo       # 示範資料（不連網）

GitHub Actions 每 15 分鐘執行一次（.github/workflows/board.yml）。快取裡完全沒有行情時，
只放網頁本身（網頁會顯示「台股資料還沒產生」）。資料格式檢查不通過就報錯，不更新網頁。
抓資料那步失敗時改用 --no-fetch --fetch-failed：用快取產生，網頁上註明這次抓資料失敗。
在 GitHub Actions 裡快取有變動時會輸出 changed=true（決定要不要存快取）。
"""
from __future__ import annotations

import argparse
import hashlib
import math
import os
from datetime import date, datetime, timezone
from pathlib import Path

from tw_market import contract, site
from tw_market.contract import MARKETS, STOCK_FIELDS, classify
from tw_market.store import DEFAULT_DIR, Store
from tw_market.update import PUBLISH, latest_day, taipei, update

ZERO_INSTI = {"foreign": 0, "trust": 0, "dealer": 0, "total": 0}
FETCH_FAILED = "這次抓資料失敗，顯示的是之前抓到的資料"
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
    """ins 是 None 表示那天的法人資料整個沒有；有資料但沒有這檔，傳進來的是 0（沒有法人買賣）。"""
    kind = classify(code)
    ins = ins or {}
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
        "limit": contract.limit_of(close, change) if kind == "stock" else None,
    }


def _is_dr(s: dict) -> bool:
    """存託憑證（例如 9105 泰金寶-DR）：證交所的漲跌家數不算它們。"""
    return s["industry"] == "存託憑證" or s["name"].upper().endswith("-DR")


def _market(mk: str, rows: list[dict], quotes, index, dates: dict) -> dict:
    """漲跌、漲停跌停家數只算股票（不含 ETF、存託憑證），和證交所公布的股票家數一致。
    dates：這個市場各項資料的日期（另一個市場比較新時，網頁要標這個市場自己的日期）。"""
    out = {"name": MARKETS[mk], **{k: _iso(d) for k, d in dates.items()}, "index": index,
           "value": None, "volume": None,
           "up": None, "down": None, "flat": None, "limit_up": None, "limit_down": None}
    if not rows:
        return out
    totals = (quotes or {}).get("totals") or {}
    st = [r for r in rows if r["type"] == "stock" and not _is_dr(r)]
    chg = [r for r in st if r["change"] is not None]
    out.update({
        "value": _i(totals.get("value")) if _i(totals.get("value")) is not None
        else sum(r["value"] or 0 for r in rows),
        "volume": _i(totals.get("volume")) if _i(totals.get("volume")) is not None
        else sum(r["volume"] or 0 for r in rows),
        "up": sum(r["change"] > 0 for r in chg), "down": sum(r["change"] < 0 for r in chg),
        "flat": sum(r["change"] == 0 for r in chg),
        "limit_up": sum(r["limit"] == 1 for r in st), "limit_down": sum(r["limit"] == -1 for r in st),
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


def _hists(store: Store, window: list[date], names: dict, used: list) -> tuple[dict, int, dict]:
    """個股歷史 K 線：一天一天讀（不一次全部載入，省記憶體），只留有收盤價的日子。
    回傳 (歷史, 讀得到的天數, 各市場讀得到的天數)；讀到的檔記在 used（算歷史檔的版本）。"""
    cols = ("open", "high", "low", "close", "volume")
    acc: dict[str, dict] = {}
    days, per_market = 0, {mk: 0 for mk in MARKETS}
    for d in window:
        iso = d.isoformat()
        seen = set()
        maps = [_rows(store.load("quotes", mk, d)) for mk in ("tse", "otc")]
        days += any(maps)
        for mk, rows in zip(("tse", "otc"), maps):
            per_market[mk] += bool(rows)
            if rows:
                used.append(f"quotes/{mk}/{iso}")
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
            for code, h in acc.items()}, days, per_market


def _chips(store: Store, dataset: str, cols: tuple, latest: date, hists: dict, markets: dict,
           used: list) -> None:
    """最近 CHIPS_MAX_DAYS 天的法人或融資融券，放進每檔的歷史；完全沒有就是 null。
    法人：那天那個市場有資料、這檔那天有成交卻不在名單上 = 沒有法人買賣，記 0
    （融資融券不在名單上是「不能信用交易」，維持 null）。讀到的檔記在 used。"""
    days = sorted(set(store.dates(dataset, "tse")) | set(store.dates(dataset, "otc")))
    loaded = []
    for d in reversed([d for d in days if d <= latest]):
        maps = [_rows(store.load(dataset, mk, d)) for mk in ("tse", "otc")]
        if any(maps):
            loaded.append((d.isoformat(), maps))
            used += [f"{dataset}/{mk}/{d.isoformat()}" for mk, rows in zip(("tse", "otc"), maps) if rows]
        if len(loaded) >= contract.CHIPS_MAX_DAYS:
            break
    loaded.reverse()
    key = "inst" if dataset == "insti" else "margin"
    for code, h in hists.items():
        vals = {c: [] for c in cols}
        traded = set(h["dates"])
        for iso, (tse, otc) in loaded:
            r = tse.get(code) or otc.get(code)
            if r is None and key == "inst" and iso in traded and (tse if markets.get(code) == "tse" else otc):
                r = ZERO_INSTI
            for c in cols:
                vals[c].append(_i(r.get(c)) if isinstance(r, dict) else None)
        if any(v is not None for c in cols for v in vals[c]):
            h[key] = {"dates": [iso for iso, _ in loaded], **vals}


def _dividends(store: Store, latest: date, hists: dict, markets: dict, used: list) -> None:
    """近 DIV_YEARS 年的除權除息放進每檔的歷史（新 → 舊）；沒有就是 null。
    股票股利換成元（每千股配 25 股 = 0.25 元）。上市「權」「權息」的現金、股票要等明細問到才有，
    在那之前是 null，只有 total（權值＋息值）。"""
    details = store.exright_details()
    used.append(f"exright/detail/{sum(1 for v in details.values() if 'cash' in v)}")
    events: dict[str, list] = {}
    for mk in MARKETS:
        for y in range(latest.year - contract.DIV_YEARS + 1, latest.year + 1):
            js = store.exright(mk, y)
            if not js:
                continue
            used.append(f"exright/{mk}/{y}/{js['upto']}/{len(js['rows'])}")
            for r in js["rows"]:
                if not isinstance(r, dict):
                    continue
                code, d = r.get("code"), r.get("date")
                if code not in hists or markets.get(code) != mk or not contract._date(d) or d > latest.isoformat():
                    continue
                cash, stock = _n(r.get("cash")), _n(r.get("stock"))
                if cash is None and mk == "tse":
                    det = details.get(f"{code}/{d}") or {}
                    cash, stock = _n(det.get("cash")), _n(det.get("stock"))
                kind = r.get("kind") if r.get("kind") in contract.EXRIGHT_KINDS else None
                events.setdefault(code, {})[d] = {
                    "date": d, "kind": kind, "cash": cash,
                    "stock": None if stock is None else round(stock / 100, 4) + 0.0,
                    "total": _n(r.get("value")), "before": _n(r.get("before")), "ref": _n(r.get("ref"))}
    for code, h in hists.items():
        ev = events.get(code)
        h["dividends"] = [ev[d] for d in sorted(ev, reverse=True)] if ev else None


def _checked(store: Store) -> datetime | None:
    """上一次確認資料是最新的時間（update 記在 status.json）。"""
    try:
        return datetime.fromisoformat(store.status()["checked_utc"].replace("Z", "+00:00"))
    except (KeyError, AttributeError, TypeError, ValueError):
        return None


def _stale_note(store: Store, latest: date, now: datetime, checked: datetime | None) -> str | None:
    """現在應該要有的行情（due）比顯示的新，而且上一次確認是在 due 公布之前 → 說明還沒抓到。
    交易所還沒公布、或平日休市還沒發現時，那次執行已經確認過，不會誤報。"""
    closed = store.closed()
    due = latest_day("quotes", now, closed)
    if latest >= due or (checked and latest_day("quotes", checked, closed) >= due):
        return None
    return f"行情還沒抓到 {_md(due)} 的，先顯示 {_md(latest)} 的"


def assemble(store: Store, now: datetime | None = None, log=print, fetch_failed: bool = False):
    """快取 → (summary, stocks, hists)；快取裡完全沒有行情時回傳 None。
    fetch_failed：這次抓資料失敗（GitHub Actions 改用快取重新產生），網頁上註明。"""
    now = taipei(now)
    notes = [FETCH_FAILED] if fetch_failed else []
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
    checked = _checked(store)
    stale = _stale_note(store, max(store.dates("quotes", "tse")[-1:] + store.dates("quotes", "otc")[-1:]),
                        now, checked)
    if stale and not any(n.startswith("上市行情還沒抓到") for n in notes):
        notes.insert(int(fetch_failed), stale)
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
            val, mar = (extra[(ds, mk)].get(code) or {} for ds in ("valuation", "margin"))
            ins = extra[("insti", mk)].get(code) or (ZERO_INSTI if extra[("insti", mk)] else None)
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

    used: list[str] = []      # 歷史檔用到的每一個快取檔：有變（補了歷史、上櫃晚到、補了籌碼）版本就不同
    hists, history_days, per_market = _hists(store, window, {s["code"]: s["name"] for s in stocks}, used)
    markets = {s["code"]: s["market"] for s in stocks}
    _chips(store, "insti", ("foreign", "trust", "dealer"), latest, hists, markets, used)
    _chips(store, "margin", ("margin_balance", "short_balance"), latest, hists, markets, used)
    _dividends(store, latest, hists, markets, used)
    if history_days < contract.HIST_MAX_DAYS:
        notes.append(f"歷史 K 線還在補齊中（目前 {history_days} 天）")
    for mk in with_rows:      # 一個市場一直補不到（例如網站改了），另一個市場已經補齊
        if per_market[mk] < history_days - 5:
            notes.append(f"{MARKETS[mk]}的歷史 K 線還在補齊中（目前 {per_market[mk]} 天）")

    summary = {
        "date": latest.isoformat(),
        "updated_utc": (checked or now).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "markets": {mk: _market(mk, by_market[mk], quotes[mk], idx, {
                        "date": d, "chips_date": dates["insti"][mk], "margin_date": dates["margin"][mk],
                        "valuation_date": dates["valuation"][mk]})
                    for mk, d, idx in (("tse", tse_d, _tse_index(tse_q)), ("otc", otc_d, _otc_index(store, otc_d)))},
        "institutional": institutional,
        "chips_date": _iso(shown["insti"]), "margin_date": _iso(shown["margin"]),
        "valuation_date": _iso(shown["valuation"]),
        "history_days": history_days,
        "hist_version": hashlib.sha1("\n".join(sorted(used)).encode()).hexdigest()[:12],
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
          demo: bool = False, log=print, fetch_failed: bool = False, on_change=None, **update_kwargs) -> dict:
    """產生 out 資料夾。fetch=False 時完全不連網，只用快取。回傳簡單的統計。
    on_change：快取第一次有變動時呼叫（GitHub Actions 用來決定要不要存快取）。"""
    out, now = Path(out), taipei(now)
    stats = None
    if demo:
        from tw_market import demo as demo_data
        summary, stocks, hists = demo_data.dataset(now.date())
    else:
        store = Store(cache_dir, on_change=on_change)
        if fetch:
            if client is None:
                from common.twse import TwseClient
                client = TwseClient()
            stats = update(store, client, now, log=log, **update_kwargs)
        data = assemble(store, now, log, fetch_failed=fetch_failed)
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
    ap.add_argument("--fetch-failed", action="store_true",
                    help="抓資料那步失敗了（GitHub Actions 用）：網頁上註明顯示的是之前抓到的資料")
    ap.add_argument("--cache", default=str(DEFAULT_DIR), help="快取資料夾（預設 .cache/tw）")
    ap.add_argument("--max-per-host", type=int, default=25, help="每個網站最多連幾次（預設 25）")
    ap.add_argument("--deadline", type=float, default=240, help="抓資料最多幾秒（預設 240）")
    a = ap.parse_args(argv)
    r = build(Path(a.out), cache_dir=Path(a.cache), fetch=not a.no_fetch, demo=a.demo,
              fetch_failed=a.fetch_failed, on_change=_github_output("changed", "true"),
              max_per_host=a.max_per_host, deadline_s=a.deadline)
    errors = (r.get("update") or {}).get("errors") or []
    if errors and os.environ.get("GITHUB_ACTIONS"):
        print(f"::warning::台股資料：{len(errors)} 個錯誤（詳細看上面有 ⚠ 的行）")
    if r["date"]:
        print(f"台股總覽完成：{r['date']}，{r['stocks']} 檔、{r['hists']} 檔歷史，輸出到 {a.out}")
    else:
        print(f"台股總覽：還沒有資料，只放網頁到 {a.out}")


def _github_output(key: str, value: str):
    """在 GitHub Actions 裡回傳一個函式：呼叫時把 key=value 寫進這一步的輸出（steps.<id>.outputs.key）。
    快取一有變動就寫（不等程式結束），中途出錯或被停掉也會存到已經抓到的資料。"""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return None

    def write():
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")
    return write


if __name__ == "__main__":
    main()
