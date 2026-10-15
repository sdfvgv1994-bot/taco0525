"""
個股資料：K 線與技術指標、基本面、籌碼面。給手機版 web_app.py 的個股頁使用。

資料來源：
    K 線：common.market.fetch_history（台股先用證交所 / 櫃買中心，抓不到改 Yahoo；其他用 Yahoo）
    技術指標：common.indicators（和回測、看盤同一套公式）
    部分財務比率：Yahoo（yfinance）
    台股基本面與籌碼：FinMind 開放資料（免註冊，但每小時有請求次數上限，所以有快取）
※ 學習用途，資料可能延遲或有誤，非投資建議。
"""

from __future__ import annotations

import datetime as dt
import math
import threading
import time

import pandas as pd
import requests

from common import indicators as ind
from common.market import fetch_history, parse_symbol

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"

_cache = {}
_cache_lock = threading.Lock()


def cached(key, ttl, fn):
    """同樣的資料在 ttl 秒內直接用快取，省 FinMind 的請求次數。
    結果標記 partial（有部分資料抓失敗）時只快取 60 秒，很快就會重抓。"""
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now < hit[0]:
            return hit[1]
    value = fn()
    partial = isinstance(value, dict) and value.get("partial")
    with _cache_lock:
        _cache[key] = (now + (60 if partial else ttl), value)
    return value


def clean(v):
    """NaN / numpy 數字轉成 JSON 能用的值。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    return None if math.isnan(f) or math.isinf(f) else round(f, 4)


def stock_id(symbol):
    """2330.TW / 6488.TWO -> 2330 / 6488；不是台股回傳 None。"""
    head, _, tail = symbol.upper().partition(".")
    return head if tail in ("TW", "TWO") else None


# ====================== FinMind ======================

def finmind(dataset, data_id=None, start=None):
    params = {"dataset": dataset}
    if data_id:
        params["data_id"] = data_id
    if start:
        params["start_date"] = start
    try:
        r = requests.get(FINMIND_URL, params=params, timeout=20)
        j = r.json()
    except (requests.RequestException, ValueError):
        raise ValueError("連不上 FinMind，請確認網路")
    if j.get("status") != 200:
        msg = j.get("msg", "")
        if "upper limit" in msg or r.status_code in (402, 429):
            raise ValueError("FinMind 免費查詢次數已達上限，請一小時後再試")
        raise ValueError("FinMind 錯誤：%s" % msg)
    return j.get("data", [])


def days_ago(n):
    return (dt.date.today() - dt.timedelta(days=n)).isoformat()


# ====================== K 線與技術指標 ======================

def indicators(df):
    """df 需要 open/high/low/close/volume 欄位。回傳加上各種指標的 DataFrame（公式都在 common.indicators）。"""
    c, h, l = df["close"], df["high"], df["low"]
    out = df.copy()
    for n in (5, 20, 60):
        out["ma%d" % n] = ind.sma(c, n)
    k, d = ind.kd(h, l, c, 9)
    warm = pd.Series(range(len(c)), index=c.index) >= 8   # 前 8 天還算不出 KD
    out["k"], out["d"] = k.where(warm), d.where(warm)
    out["dif"], out["macd"], out["osc"] = ind.macd(c, 12, 26, 9)
    rsi = ind.rsi(c, 14)
    rsi.iloc[:14] = None
    out["rsi"] = rsi
    out["bb_up"], _, out["bb_low"] = ind.bollinger(c, 20, 2)
    return out


PERIOD_MONTHS = {"3mo": 3, "6mo": 6, "1y": 12, "2y": 24, "5y": 60}


def get_chart(symbol, period="1y", source="auto", demo=False):
    """symbol 可以是 2330、2330.TW、AAPL…（和自選股同一套代號規則）。"""
    def load():
        # 多抓一段，讓 60 日均線在畫面一開始就有值
        extra = {"3mo": "6mo", "6mo": "1y", "1y": "2y", "2y": "5y", "5y": "10y"}.get(period, "2y")
        code, raw = fetch_history(parse_symbol(symbol), period=extra, demo=demo, source=source)
        df = raw.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].dropna()
        if df.empty:
            raise ValueError("抓不到 %s 的 K 線" % symbol)
        if getattr(df.index, "tz", None) is not None:
            df.index = df.index.tz_localize(None)
        df = indicators(df.astype(float))
        keep_from = df.index[-1] - pd.DateOffset(months=PERIOD_MONTHS.get(period, 12))
        df = df[df.index >= keep_from]
        cols = ["open", "high", "low", "close", "volume", "ma5", "ma20", "ma60",
                "k", "d", "dif", "macd", "osc", "rsi", "bb_up", "bb_low"]
        rows = [{"time": i.strftime("%Y-%m-%d"), **{k: clean(r[k]) for k in cols}} for i, r in df.iterrows()]
        return {"symbol": code, "rows": rows}
    return cached(("chart", symbol, period, source, demo), 300, load)


# ====================== 基本面 ======================

def get_fundamental(symbol):
    def load():
        sid = stock_id(symbol)
        result = {"symbol": symbol, "metrics": {}, "revenue": [], "eps": [], "dividends": [], "notes": []}
        m = result["metrics"]

        try:   # Yahoo 的財務比率（台股、美股都有）
            import yfinance as yf
            info = yf.Ticker(symbol).info or {}
            for key, name in [("marketCap", "market_cap"), ("trailingEps", "eps_ttm"), ("returnOnEquity", "roe"),
                              ("grossMargins", "gross_margin"), ("operatingMargins", "operating_margin"),
                              ("profitMargins", "profit_margin"), ("revenueGrowth", "revenue_growth"),
                              ("debtToEquity", "debt_to_equity"), ("fiftyTwoWeekHigh", "high_52w"),
                              ("fiftyTwoWeekLow", "low_52w"), ("beta", "beta"), ("trailingPE", "pe"),
                              ("priceToBook", "pb")]:
                m[name] = clean(info.get(key))
            m["currency"] = info.get("currency")
            if not sid:
                m["name"] = info.get("longName") or info.get("shortName")
                yld = clean(info.get("dividendYield"))
                m["dividend_yield"] = yld
        except Exception:
            result["notes"].append("Yahoo 財務資料暫時抓不到"); result["partial"] = True

        if not sid:
            result["notes"].append("月營收、股利、本益比歷史等資料只提供台股")
            return result

        try:   # 本益比、淨值比、殖利率（證交所每日資料，比 Yahoo 新）
            per = finmind("TaiwanStockPER", sid, days_ago(20))
            if per:
                last = per[-1]
                m.update(pe=clean(last["PER"]), pb=clean(last["PBR"]),
                         dividend_yield=clean(last["dividend_yield"]), per_date=last["date"])
        except ValueError as e:
            result["notes"].append(str(e)); result["partial"] = True

        try:   # 月營收：抓 25 個月才算得出 13 個月的年增率
            rev = finmind("TaiwanStockMonthRevenue", sid, days_ago(800))
            by_month = {(r["revenue_year"], r["revenue_month"]): r["revenue"] for r in rev}
            for (y, mo) in sorted(by_month)[-13:]:
                v = by_month[(y, mo)]
                last_year = by_month.get((y - 1, mo))
                prev = by_month.get((y, mo - 1) if mo > 1 else (y - 1, 12))
                result["revenue"].append({
                    "month": "%d/%02d" % (y, mo), "revenue": v,
                    "yoy": clean(v / last_year - 1) if last_year else None,
                    "mom": clean(v / prev - 1) if prev else None})
        except ValueError as e:
            result["notes"].append(str(e)); result["partial"] = True

        try:   # 每季 EPS
            fs = finmind("TaiwanStockFinancialStatements", sid, days_ago(800))
            eps = [r for r in fs if r.get("type") == "EPS"]
            result["eps"] = [{"quarter": quarter_label(r["date"]), "eps": clean(r["value"])} for r in eps[-8:]]
            if len(eps) >= 4:
                m["eps_4q"] = clean(sum(r["value"] for r in eps[-4:]))
        except ValueError as e:
            result["notes"].append(str(e)); result["partial"] = True

        try:   # 股利
            div = finmind("TaiwanStockDividend", sid, days_ago(365 * 4))
            for r in reversed(div[-10:]):
                cash = (r.get("CashEarningsDistribution") or 0) + (r.get("CashStatutorySurplus") or 0)
                stock = (r.get("StockEarningsDistribution") or 0) + (r.get("StockStatutorySurplus") or 0)
                result["dividends"].append({"period": r.get("year"), "cash": clean(cash), "stock": clean(stock),
                                            "ex_date": r.get("CashExDividendTradingDate") or "",
                                            "pay_date": r.get("CashDividendPaymentDate") or ""})
        except ValueError as e:
            result["notes"].append(str(e)); result["partial"] = True
        return result
    return cached(("fundamental", symbol), 1800, load)


def quarter_label(date_text):
    d = dt.date.fromisoformat(date_text[:10])
    return "%dQ%d" % (d.year, (d.month - 1) // 3 + 1)


# ====================== 籌碼面 ======================

INSTITUTION_GROUPS = {
    "Foreign_Investor": "foreign", "Foreign_Dealer_Self": "foreign",
    "Investment_Trust": "trust",
    "Dealer_self": "dealer", "Dealer_Hedging": "dealer", "Dealer": "dealer",
}


def get_chips(symbol):
    def load():
        sid = stock_id(symbol)
        if not sid:
            return {"symbol": symbol, "notes": ["籌碼資料只提供台股"], "institutions": [], "margin": [], "foreign_ratio": []}
        result = {"symbol": symbol, "notes": [], "institutions": [], "margin": [], "foreign_ratio": []}
        start = days_ago(45)

        try:   # 三大法人買賣超（單位：張）
            days = {}
            for r in finmind("TaiwanStockInstitutionalInvestorsBuySell", sid, start):
                group = INSTITUTION_GROUPS.get(r["name"])
                if group:
                    day = days.setdefault(r["date"], {"date": r["date"], "foreign": 0, "trust": 0, "dealer": 0})
                    day[group] += (r["buy"] - r["sell"]) / 1000
            rows = [days[d] for d in sorted(days)][-20:]
            for row in rows:
                for k in ("foreign", "trust", "dealer"):
                    row[k] = round(row[k])
                row["total"] = row["foreign"] + row["trust"] + row["dealer"]
            result["institutions"] = rows
        except ValueError as e:
            result["notes"].append(str(e)); result["partial"] = True

        try:   # 融資融券（單位：張）
            for r in finmind("TaiwanStockMarginPurchaseShortSale", sid, start)[-20:]:
                result["margin"].append({
                    "date": r["date"],
                    "margin": r["MarginPurchaseTodayBalance"],
                    "margin_change": r["MarginPurchaseTodayBalance"] - r["MarginPurchaseYesterdayBalance"],
                    "short": r["ShortSaleTodayBalance"],
                    "short_change": r["ShortSaleTodayBalance"] - r["ShortSaleYesterdayBalance"],
                })
        except ValueError as e:
            result["notes"].append(str(e)); result["partial"] = True

        try:   # 外資持股比例
            result["foreign_ratio"] = [{"date": r["date"], "ratio": clean(r["ForeignInvestmentSharesRatio"])}
                                       for r in finmind("TaiwanStockShareholding", sid, start)[-20:]]
        except ValueError as e:
            result["notes"].append(str(e)); result["partial"] = True
        return result
    return cached(("chips", symbol), 1800, load)
