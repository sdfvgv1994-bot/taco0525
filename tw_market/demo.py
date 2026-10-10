"""台股總覽的示範資料（不連網）：格式和真實資料完全一樣，用來開發、測試網頁。

    python -m tw_market.demo --out _site/tw
"""
from __future__ import annotations

import argparse
import hashlib
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from tw_market import site
from tw_market.contract import CHIPS_MAX_DAYS, HIST_MAX_DAYS, INDUSTRIES, classify

# 一些真實的名稱，讓畫面看起來像真的（價格、成交量都是假的）
KNOWN = [
    ("2330", "台積電", "tse", "24"), ("2317", "鴻海", "tse", "31"), ("2454", "聯發科", "tse", "24"),
    ("2308", "台達電", "tse", "28"), ("2382", "廣達", "tse", "25"), ("2412", "中華電", "tse", "27"),
    ("2881", "富邦金", "tse", "17"), ("2882", "國泰金", "tse", "17"), ("2891", "中信金", "tse", "17"),
    ("2303", "聯電", "tse", "24"), ("3711", "日月光投控", "tse", "24"), ("2002", "中鋼", "tse", "10"),
    ("1301", "台塑", "tse", "03"), ("1303", "南亞", "tse", "03"), ("2603", "長榮", "tse", "15"),
    ("2609", "陽明", "tse", "15"), ("2615", "萬海", "tse", "15"), ("1101", "台泥", "tse", "01"),
    ("1216", "統一", "tse", "02"), ("2207", "和泰車", "tse", "12"), ("2912", "統一超", "tse", "18"),
    ("3008", "大立光", "tse", "26"), ("2357", "華碩", "tse", "25"), ("2379", "瑞昱", "tse", "24"),
    ("6505", "台塑化", "tse", "23"), ("1590", "亞德客-KY", "tse", "05"), ("4904", "遠傳", "tse", "27"),
    ("6488", "環球晶", "otc", "24"), ("5483", "中美晶", "otc", "24"), ("3105", "穩懋", "otc", "24"),
    ("8299", "群聯", "otc", "24"), ("6547", "高端疫苗", "otc", "22"), ("4966", "譜瑞-KY", "otc", "24"),
    ("5347", "世界", "otc", "24"), ("3293", "鈊象", "otc", "32"), ("6274", "台燿", "otc", "28"),
    ("0050", "元大台灣50", "tse", None), ("0056", "元大高股息", "tse", None),
    ("00878", "國泰永續高股息", "tse", None), ("00919", "群益台灣精選高息", "tse", None),
    ("00631L", "元大台灣50正2", "tse", None), ("006201", "元大富櫃50", "otc", None),
]
EXTRA_TSE, EXTRA_OTC = 300, 200


def _rng(key: str) -> np.random.Generator:
    return np.random.default_rng(int(hashlib.md5(key.encode()).hexdigest()[:8], 16))


def _universe() -> list[tuple]:
    out = list(KNOWN)
    used = {c for c, *_ in out}
    inds = [k for k in INDUSTRIES if k not in ("80", "91")]
    for market, n, base in (("tse", EXTRA_TSE, 1100), ("otc", EXTRA_OTC, 3200)):
        code, made = base, 0
        while made < n:
            code += 7
            c = f"{code:04d}"
            if c in used:
                continue
            used.add(c)
            ind = inds[int(_rng(c).integers(len(inds)))]
            out.append((c, f"示範{INDUSTRIES[ind][:2]}{made + 1}", market, ind))
            made += 1
    return out


def _series(code: str, days: pd.DatetimeIndex) -> pd.DataFrame:
    rng = _rng(code)
    n = len(days)
    start = float(rng.choice([12, 25, 48, 90, 160, 320, 650]) * rng.uniform(0.8, 1.3))
    drift = np.repeat(rng.normal(0, 0.004, n // 30 + 1), 30)[:n]
    rets = np.clip(drift + rng.normal(0, 0.02, n), -0.095, 0.095)
    close = start * np.exp(np.cumsum(rets))
    open_ = close / (1 + rets) * (1 + rng.normal(0, 0.004, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.008, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.008, n)))
    vol = (rng.lognormal(7, 1.2, n)).round()
    tick = lambda x: np.round(x, 2 if start < 50 else 1 if start < 500 else 0)  # noqa: E731
    return pd.DataFrame({"open": tick(open_), "high": tick(high), "low": tick(low),
                         "close": tick(close), "volume": vol}, index=days)


def dataset(today: date | None = None) -> tuple[dict, list[dict], dict]:
    """回傳 (summary, stocks, hists)，格式見 tw_market/contract.py。"""
    today = today or date.today()
    days = pd.bdate_range(end=pd.Timestamp(today), periods=HIST_MAX_DAYS)
    d = f"{days[-1]:%Y-%m-%d}"
    stocks, hists = [], {}
    for code, name, market, ind in _universe():
        df = _series(code, days)
        rng = _rng(code + "chips")
        last, prev = df.iloc[-1], df.iloc[-2]
        kind = classify(code)
        change = round(float(last.close - prev.close), 2)
        f, t, dl = (int(x) for x in rng.normal(0, [800, 120, 200]).round())
        mb = int(rng.integers(0, 40000))
        eps = rng.normal(3, 4)
        pe = round(float(last.close / eps), 2) if kind == "stock" and eps > 0.2 else None
        stocks.append({
            "code": code, "name": name, "market": market, "type": kind,
            "industry": "ETF" if kind == "etf" else INDUSTRIES[ind],
            "close": float(last.close), "change": change,
            "change_pct": round(change / float(prev.close) * 100, 2),
            "open": float(last.open), "high": float(last.high), "low": float(last.low),
            "volume": int(last.volume), "value": int(last.volume * 1000 * last.close),
            "pe": pe, "dividend_yield": round(float(rng.uniform(0, 8)), 2) if kind == "stock" else None,
            "pb": round(float(rng.uniform(0.5, 6)), 2) if kind == "stock" else None,
            "foreign": f, "trust": t, "dealer": dl, "inst_total": f + t + dl,
            "margin_balance": mb, "margin_change": int(rng.normal(0, mb * 0.02 + 1)),
            "short_balance": int(mb * rng.uniform(0, 0.2)), "short_change": int(rng.normal(0, 30)),
        })
        chip_days = [f"{x:%Y-%m-%d}" for x in days[-CHIPS_MAX_DAYS:]]
        hists[code] = {
            "code": code, "name": name, "dates": [f"{x:%Y-%m-%d}" for x in days],
            **{k: [float(v) for v in df[k]] for k in ("open", "high", "low", "close")},
            "volume": [int(v) for v in df["volume"]],
            "inst": {"dates": chip_days,
                     **{k: [int(v) for v in rng.normal(0, s, CHIPS_MAX_DAYS).round()]
                        for k, s in (("foreign", 800), ("trust", 120), ("dealer", 200))}},
            "margin": {"dates": chip_days,
                       "margin_balance": [int(mb * (1 + 0.01 * i)) for i in range(CHIPS_MAX_DAYS)],
                       "short_balance": [int(mb * 0.1) for _ in range(CHIPS_MAX_DAYS)]},
        }

    def market(mk):
        rows = [s for s in stocks if s["market"] == mk]
        st = [s for s in rows if s["type"] == "stock"]
        idx_close = 49313.44 if mk == "tse" else 426.71
        idx_chg = -492.93 if mk == "tse" else -3.75
        return {"name": "上市" if mk == "tse" else "上櫃",
                "index": {"name": "加權指數" if mk == "tse" else "櫃買指數", "close": idx_close,
                          "change": idx_chg, "change_pct": round(idx_chg / (idx_close - idx_chg) * 100, 2)},
                "value": sum(s["value"] for s in rows), "volume": sum(s["volume"] for s in rows),
                "up": sum(s["change"] > 0 for s in st), "down": sum(s["change"] < 0 for s in st),
                "flat": sum(s["change"] == 0 for s in st),
                "limit_up": sum(s["change_pct"] >= 9.5 for s in st),
                "limit_down": sum(s["change_pct"] <= -9.5 for s in st)}

    summary = {
        "date": d, "updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "markets": {"tse": market("tse"), "otc": market("otc")},
        "institutional": {"date": d,
                          "tse": {"foreign": -1.23e10, "trust": 1.2e9, "dealer": 3.4e8, "total": -1.076e10},
                          "otc": {"foreign": 8.5e8, "trust": 2.1e8, "dealer": -5e7, "total": 1.01e9}},
        "chips_date": d, "margin_date": d, "valuation_date": d,
        "history_days": len(days), "demo": True,
        "notes": ["這是示範資料：價格、成交量、籌碼都是隨機產生的"],
    }
    return summary, stocks, hists


def main(argv=None):
    ap = argparse.ArgumentParser(description="產生台股總覽的示範網頁")
    ap.add_argument("--out", default="_site/tw")
    a = ap.parse_args(argv)
    summary, stocks, hists = dataset()
    site.write(Path(a.out), summary, stocks, hists)
    print(f"示範資料：{len(stocks)} 檔，輸出到 {a.out}")


if __name__ == "__main__":
    main()
