"""模擬帳戶的配息配股：盡量照真的證券戶來算。

規則
- 除權息日「前一天收盤」還持有的股數才有股利：除權息當天才買的沒有，當天賣掉的照樣有
- 台股現金股利：除息日先記成「應收股利」，約 TW_PAY_DAYS 天後入帳（實際發放日看公司公告），
  入帳時扣匯費 10 元
- 台股股票股利：除權日直接加股數（每千股配 N 股），不足 1 股的畸零股按面額 10 元折成現金；
  成本不變、股數變多，所以每股平均成本會變低
- 二代健保補充保費：同一次股利（現金股利＋股票股利面額）達 2 萬元，整筆扣 2.11%
- 美股：現金股利扣 30% 預扣稅，約 US_PAY_DAYS 天後入帳

資料來源
- 台股：台股總覽抓好存在 .cache/tw 的證交所／櫃買中心除權除息表（雲端每次執行之間會保存）
- 美股：Yahoo（yfinance），每檔一天只問一次

紀錄存在 state 資料夾的 dividends.json（雲端是 monitor-data 分支）：處理過的除權息、還沒入帳的股利。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from cloud_monitor.exchange import exchange_of
from common.market import parse_symbol

TW_PAY_DAYS = 30           # 台股現金股利大約除息後一個月發放
US_PAY_DAYS = 7            # 美股大約一週
PAR = 10                   # 台股面額
NHI_RATE = 0.0211          # 二代健保補充保費費率
NHI_THRESHOLD = 20000      # 單次給付達這個金額才扣
TW_REMIT_FEE = 10          # 股利匯費
US_WITHHOLD = 0.30         # 美股股利預扣稅（非美國居民）
KEEP_DONE = 500
ROOT = Path(__file__).resolve().parent.parent
TW_CACHE = ROOT / ".cache" / "tw"


def tw_base(code: str) -> str | None:
    """台股代號（去掉 .TW / .TWO）；不是台股 → None。"""
    try:
        sym = parse_symbol(code)
    except ValueError:
        return None
    return sym.candidates[0].split(".")[0] if sym.is_tw_stock else None


class Source:
    """除權息資料：events(codes, since, today) → [{"code", "date", "cash"（每股元）, "stock"（每千股配股）}]。
    cash 或 stock 是 None 代表資料還不完整（例如上市「權息」明細還沒抓到），下次再試。"""

    def __init__(self, tw_cache: Path = TW_CACHE, us_fetch=None):
        self.tw_cache = Path(tw_cache)
        self.us_fetch = us_fetch or yahoo_dividends
        self.notes: list[str] = []
        self.us_checked: dict = {}     # 由 process() 帶入、存回 dividends.json

    def events(self, codes, since: date, today: date) -> list:
        return self.tw_events(codes, since, today) + self.us_events(codes, since, today)

    def tw_events(self, codes, since, today) -> list:
        want = {b: c for c in codes if (b := tw_base(c))}
        if not want or not self.tw_cache.exists():
            return []
        from tw_market.store import Store
        st = Store(self.tw_cache)
        try:
            details = st.exright_details()
        except Exception:  # noqa: BLE001 - 快取壞掉不影響看盤
            details = {}
        out = []
        for market in ("tse", "otc"):
            for y in range(since.year, today.year + 1):
                try:
                    js = st.exright(market, y) or {}
                except Exception:  # noqa: BLE001
                    continue
                for r in js.get("rows", []):
                    if not isinstance(r, dict) or r.get("code") not in want:
                        continue
                    d = str(r.get("date", ""))
                    if not (since.isoformat() <= d <= today.isoformat()):
                        continue
                    cash, stock = r.get("cash"), r.get("stock")
                    if cash is None or stock is None:     # 上市「權」「權息」：要看明細
                        det = details.get(f"{r['code']}/{d}") or {}
                        if det.get("missing"):
                            self.notes.append(f"{want[r['code']]} {d} 除權息：交易所沒有現金／股票股利明細，先不入帳")
                        cash, stock = det.get("cash"), det.get("stock")
                    out.append({"code": want[r["code"]], "date": d, "cash": cash, "stock": stock})
        return out

    def us_events(self, codes, since, today) -> list:
        out = []
        for c in codes:
            if exchange_of(c) != "US" or self.us_checked.get(c) == today.isoformat():
                continue
            try:
                rows = self.us_fetch(c)
            except Exception as e:  # noqa: BLE001 - 查不到股利不影響看盤
                self.notes.append(f"{c} 股利資料查詢失敗：{e}")
                continue
            self.us_checked[c] = today.isoformat()
            out += [{"code": c, "date": d, "cash": v, "stock": 0.0} for d, v in rows
                    if since.isoformat() <= d <= today.isoformat()]
        return out


def yahoo_dividends(code: str) -> list:
    """Yahoo 的歷年現金股利 → [("YYYY-MM-DD" 除息日, 每股金額)]（只取近兩年）。"""
    import yfinance as yf
    s = yf.Ticker(code).dividends
    cut = date.today() - timedelta(days=730)
    return [(idx.date().isoformat(), float(v)) for idx, v in s.items() if idx.date() >= cut and v > 0]


# ---------------------------------------------------------------- 計算
def held_before(account, code: str, ex_date: str) -> int:
    """除權息日前一天收盤持有的股數：現在的股數，扣回除權息日（含）之後的買賣與配股。
    交易紀錄的時間是 UTC；台股、美股的交易時間在 UTC 和當地都落在同一天，用日期比就夠了。"""
    sh = account.positions.get(code, {}).get("shares", 0)
    for t in account.trades:
        if t.get("code") != code or str(t.get("time", ""))[:10] < ex_date:
            continue
        if t.get("side") in ("買進", "配股"):
            sh -= t.get("shares", 0)
        elif t.get("side") == "賣出":
            sh += t.get("shares", 0)
    return max(0, int(sh))


def entitlement(code: str, shares: int, cash_ps: float, stock_per_1000: float, tw: bool) -> dict:
    """一次除權息拿到多少：現金（扣健保、匯費或預扣稅後）和配股股數。"""
    gross = shares * cash_ps
    stock_total = shares * stock_per_1000 / 1000
    whole = int(stock_total + 1e-9)
    odd_cash = round((stock_total - whole) * PAR, 2) if tw else 0.0   # 畸零股按面額折現金
    nhi = fee = tax = 0.0
    if tw:
        base = gross + odd_cash + whole * PAR
        if base >= NHI_THRESHOLD:
            nhi = round(base * NHI_RATE)
        if gross + odd_cash > 0:
            fee = TW_REMIT_FEE
    else:
        tax = round(gross * US_WITHHOLD, 2)
    net = round(gross + odd_cash - nhi - fee - tax, 2)
    return {"gross": round(gross, 2), "odd_cash": odd_cash, "stock_shares": whole,
            "nhi": nhi, "fee": fee, "tax": tax, "net": net}


def _when(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 9, 0)


def process(account, store, names: dict, now: datetime, source: Source | None = None, log=print) -> dict:
    """處理新的除權息與到期的股利入帳。回傳 {"messages", "receivable", "pending", "notes"}。"""
    source = source or Source()
    today = now.date()
    state = store.load("dividends.json", {}) or {}
    done = list(state.get("done", []))
    seen = set(done)
    pending = list(state.get("pending", []))
    source.us_checked = dict(state.get("us_checked", {}))
    messages = []

    # 這個帳戶交易過或持有的股票；從第一筆交易那天開始找
    codes = sorted({c for c in account.positions} | {t["code"] for t in account.trades if t.get("code")})
    days = [str(t.get("time", ""))[:10] for t in account.trades if t.get("time")]
    since = date.fromisoformat(min(days)) if days else today
    events = sorted(source.events(codes, since, today), key=lambda e: (e["date"], e["code"]))

    for ev in events:
        key = f"{ev['code']}/{ev['date']}"
        if key in seen or ev.get("cash") is None or ev.get("stock") is None:
            continue
        code, ex = ev["code"], date.fromisoformat(ev["date"])
        shares = held_before(account, code, ev["date"])
        seen.add(key)
        done.append(key)
        if shares <= 0:
            continue
        tw = exchange_of(code) == "TW"
        e = entitlement(code, shares, float(ev["cash"]), float(ev["stock"]), tw)
        name = names.get(code, "")
        if e["stock_shares"]:
            pos = account.positions.get(code)
            if pos is None:                       # 除權後才賣光：配股照樣拿到
                pos = account.positions[code] = {"shares": 0, "avg_cost": 0.0, "tw": True}
            total_cost = pos["avg_cost"] * pos["shares"]
            pos["shares"] += e["stock_shares"]
            pos["avg_cost"] = total_cost / pos["shares"]
            account._record("配股", code, 0.0, e["stock_shares"], 0.0, 0.0, 0.0,
                            f"除權 {ex:%m/%d}：每千股配 {float(ev['stock']):g} 股", _when(today))
            messages.append(f"📈 {code} {name} 除權：配股 {e['stock_shares']:,} 股".replace("  ", " "))
        if e["gross"] or e["odd_cash"]:
            pay = ex + timedelta(days=TW_PAY_DAYS if tw else US_PAY_DAYS)
            parts = [f"每股 {float(ev['cash']):g} {'元' if tw else '美元'}"]
            if e["odd_cash"]:
                parts.append(f"畸零股折現 {e['odd_cash']:,.0f}")
            if e["nhi"]:
                parts.append(f"扣健保補充保費 {e['nhi']:,.0f}")
            if e["fee"]:
                parts.append(f"匯費 {e['fee']:,.0f}")
            if e["tax"]:
                parts.append(f"扣預扣稅 30% {e['tax']:,.2f}")
            pending.append({"code": code, "ex_date": ev["date"], "pay_date": pay.isoformat(), "shares": shares,
                            "cash_ps": float(ev["cash"]), **e, "detail": "；".join(parts)})
            unit, net = ("元", f"{e['net']:,.0f}") if tw else ("美元", f"{e['net']:,.2f}")
            messages.append(f"💰 {code} {name} 除息：{shares:,} 股 × {float(ev['cash']):g} {unit}，"
                            f"預計 {pay:%m/%d} 入帳 {net} {unit}".replace("  ", " "))
        log(f"除權息 {key}：持有 {shares:,} 股，現金 {e['net']:,.2f}，配股 {e['stock_shares']}")

    # 到期的股利入帳
    still = []
    for p in pending:
        if p["pay_date"] > today.isoformat():
            still.append(p)
            continue
        account.cash += p["net"]
        account._record("股利入帳", p["code"], p["cash_ps"], p["shares"], p["fee"] + p["nhi"], p["tax"],
                        p["net"], f"除息 {p['ex_date'][5:].replace('-', '/')}：{p['detail']}", _when(today))
        name = names.get(p["code"], "")
        us = exchange_of(p["code"]) != "TW"
        messages.append(f"💵 {p['code']} {name} 股利入帳 {p['net']:,.{2 if us else 0}f} {'美元' if us else '元'}"
                        .replace("  ", " "))
        log(f"股利入帳 {p['code']} {p['ex_date']}：{p['net']:,.2f}")

    store.save("dividends.json", {"done": done[-KEEP_DONE:], "pending": still, "us_checked": source.us_checked})
    return {"messages": messages, "receivable": round(sum(p["net"] for p in still), 2),
            "pending": still, "notes": list(dict.fromkeys(source.notes))}
