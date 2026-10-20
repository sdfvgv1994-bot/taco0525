"""台股總覽：除權除息的排程（tw_market/update.py 的第 6 步，拆出來的積木）。

    phase(u)         今年＋前幾年（contract.DIV_YEARS），一年問一次；過去的年份存了就不再問，
                     今年的有新的交易日（行情的最新一天往後）才再問一次
    phase_detail(u)  上市「權」「權息」：計算結果表只有合計，一筆一筆問明細分出現金和股票，
                     每次最多 DETAIL_PER_RUN 筆（新 → 舊）

u 是 update.py 的 _Updater（用它的 store、src、log、error，以及 get_raw 的連線限制）。
"""
from __future__ import annotations

from datetime import date

from tw_market import contract
from tw_market.store import MARKETS

DETAIL_PER_RUN = 8             # 上市除權息明細每次最多問幾筆（歷史一次補不完，慢慢補）
MARKET_NAMES = {"tse": "上市", "otc": "上櫃"}


def years(u) -> tuple[date, list[int]]:
    end = u.latest("quotes")
    return end, list(range(end.year, end.year - contract.DIV_YEARS, -1))   # 新 → 舊


def phase(u) -> None:
    if not hasattr(u.src, "twse_exright"):     # 測試用的假來源沒有這項
        return
    end, ys = years(u)
    for y in ys:
        upto = end if y == end.year else date(y, 12, 31)
        for m in MARKETS:
            if u.timed_out:
                return
            old = u.store.exright(m, y)
            if (old and old["upto"] >= upto.isoformat()) or ("exright", m) in u.broken:
                continue
            fetch_year(u, m, y, upto)


def fetch_year(u, market: str, year: int, upto: date) -> None:
    what = f"{MARKET_NAMES[market]}除權除息 {year}"
    name = "twse_exright" if market == "tse" else "tpex_exright"
    url, params = getattr(u.src, name)(upto)
    js = u.get_raw(url, params, what)
    if js is None:
        return
    try:
        parsed = getattr(u.src, "parse_" + name)(js)
    except (ValueError, KeyError, TypeError, IndexError, AttributeError) as e:
        u.broken.add(("exright", market))
        u.error(f"{what}：格式和預期不同（{e}），這次先不抓")
        return
    rows = [r for r in (parsed or {}).get("rows", [])
            if isinstance(r, dict) and f"{year}-01-01" <= str(r.get("date")) <= upto.isoformat()]
    if parsed and not rows:
        u.broken.add(("exright", market))
        u.error(f"{what}：回應的日期不對，先不存")
        return
    if not rows and year < years(u)[0].year:
        u.error(f"{what}：一整年都沒有資料，先不存")   # 過去整年都沒有除權息不可能
        return
    u.store.save_exright(market, year, upto, u.now.isoformat(timespec="seconds"), rows)
    u.saved.append(f"exright/{market}/{year}")
    u.log(f"  {what}：{len(rows)} 筆")


def phase_detail(u) -> None:
    """上市「權」「權息」：計算結果表只有合計，一筆一筆問明細（新 → 舊，每次最多 DETAIL_PER_RUN 筆）。"""
    if not hasattr(u.src, "twse_exright_detail"):
        return
    details = u.store.exright_details()
    want = []
    for y in years(u)[1]:
        js = u.store.exright("tse", y)
        for r in reversed((js or {}).get("rows", [])):
            if isinstance(r, dict) and r.get("kind") in ("權", "權息") and r.get("cash") is None:
                key = f"{r.get('code')}/{r.get('date')}"
                if key not in details:
                    want.append((r["code"], r["date"], key))
    want.sort(key=lambda t: t[1], reverse=True)
    added = 0
    for code, d, key in want[:DETAIL_PER_RUN]:
        what = f"上市除權息明細 {code} {d}"
        url, params = u.src.twse_exright_detail(code, date.fromisoformat(d))
        js = u.get_raw(url, params, what)
        if js is None:
            break
        try:
            got = u.src.parse_twse_exright_detail(js)
        except (ValueError, KeyError, TypeError, IndexError, AttributeError) as e:
            u.error(f"{what}：格式和預期不同（{e}），這次先不問明細")
            break
        details[key] = ({"cash": got["cash"], "stock": got["stock"]} if got and got.get("code") in (code, None)
                        else {"missing": True})     # 交易所沒有明細：記下來，不再一直問
        added += 1
    if added:
        u.store.save_exright_details(details)
        u.saved.append("exright/detail_tse")
        u.log(f"  上市除權息明細：{added} 筆（還有 {max(0, len(want) - added)} 筆下次再問）")
