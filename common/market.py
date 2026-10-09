"""代號辨識與歷史資料下載。

- 只打數字（如 2330）→ 台股，先試上市 .TW，抓不到再試上櫃 .TWO
- 英文字母（如 AAPL）→ 美股
- 常見指數別名（加權、櫃買、SPX、NASDAQ…）→ 對應指數代號
- 常見加密貨幣（BTC、ETH…）→ XXX-USD
- 其他（含 ^ 或 - 或 . 的完整代號）→ 原樣使用

資料來源（source）：
- auto（預設）：台股、加權指數先找證交所 / 櫃買中心，失敗才改用 Yahoo；其他用 Yahoo
- twse：台股只用證交所，失敗就報錯
- yahoo：全部用 Yahoo Finance

示範模式（demo=True）不連網，用代號當種子產生可重現的隨機走勢，方便測試。
"""
from __future__ import annotations

import hashlib
import re
import sys
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

INDEX_ALIASES = {
    "加權": "^TWII", "台股": "^TWII", "TAIEX": "^TWII", "TWII": "^TWII",
    "櫃買": "^TWOII", "TWOII": "^TWOII",
    "SPX": "^GSPC", "SP500": "^GSPC", "S&P500": "^GSPC",
    "NASDAQ": "^IXIC", "那斯達克": "^IXIC",
    "DOW": "^DJI", "道瓊": "^DJI",
    "SOX": "^SOX", "費半": "^SOX",
    "N225": "^N225", "日經": "^N225",
}
CRYPTO = {"BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "USDT"}


@dataclass(frozen=True)
class Symbol:
    raw: str           # 使用者輸入
    candidates: tuple  # 依序嘗試的 Yahoo 代號
    kind: str          # tw_stock / us_stock / index / crypto / other

    @property
    def is_tw_stock(self) -> bool:
        return self.kind == "tw_stock"


def parse_symbol(text: str) -> Symbol:
    s = text.strip()
    up = s.upper()
    if not s:
        raise ValueError("代號不能是空的")
    if s in INDEX_ALIASES or up in INDEX_ALIASES:
        return Symbol(s, (INDEX_ALIASES.get(s) or INDEX_ALIASES[up],), "index")
    if up.startswith("^"):
        return Symbol(s, (up,), "index")
    if re.fullmatch(r"\d{4,6}[A-Z]?", up):
        return Symbol(s, (f"{up}.TW", f"{up}.TWO"), "tw_stock")
    if re.fullmatch(r"\d{4,6}[A-Z]?\.(TW|TWO)", up):
        return Symbol(s, (up,), "tw_stock")
    if up in CRYPTO:
        return Symbol(s, (f"{up}-USD",), "crypto")
    if re.fullmatch(r"[A-Z]+-USD", up):
        return Symbol(s, (up,), "crypto")
    if re.fullmatch(r"[A-Z]{1,5}", up):
        return Symbol(s, (up,), "us_stock")
    return Symbol(s, (up,), "other")


def demo_history(code: str, days: int = 500, end=None) -> pd.DataFrame:
    """以代號為種子產生可重現的日 K（幾何布朗運動，含幾段明顯趨勢）。"""
    seed = int(hashlib.md5(code.encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    end = pd.Timestamp(end) if end is not None else pd.Timestamp.today().normalize()
    idx = pd.bdate_range(end=end, periods=days)
    # 分段漂移讓均線有交叉可測
    drift = np.repeat(rng.normal(0, 0.004, days // 40 + 1), 40)[:days]
    rets = drift + rng.normal(0, 0.018, days)
    start = 20 + (seed % 500)
    close = start * np.exp(np.cumsum(rets))
    open_ = close * (1 + rng.normal(0, 0.005, days))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.008, days)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.008, days)))
    vol = rng.integers(1_000, 50_000, days) * 1000
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": vol},
        index=idx,
    ).round(2)


def _flatten(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = df.columns.get_level_values(0)
    return df


PERIOD_DAYS = {"1mo": 31, "3mo": 92, "6mo": 183, "1y": 366, "2y": 731, "5y": 1827,
               "10y": 3653}
SOURCES = ("auto", "twse", "yahoo")


def uses_twse(sym: Symbol) -> bool:
    return sym.is_tw_stock or sym.candidates[0] == "^TWII"


def _date_range(period: str, start, end) -> tuple[date, date]:
    end_d = pd.Timestamp(end).date() if end else date.today()
    if start:
        return pd.Timestamp(start).date(), end_d
    if period not in PERIOD_DAYS:
        raise ValueError(f"期間只支援 {', '.join(PERIOD_DAYS)}")
    return end_d - timedelta(days=PERIOD_DAYS[period]), end_d


def fetch_twse(sym: Symbol, period: str = "2y", start=None, end=None,
               client=None) -> tuple[str, pd.DataFrame]:
    from . import twse
    cl = client or twse.client()
    s, e = _date_range(period, start, end)
    if sym.candidates[0] == "^TWII":
        df = cl.index_history(s, e)
        code = "^TWII"
    else:
        base = sym.candidates[0].split(".")[0]
        hint = "otc" if sym.candidates[0].endswith(".TWO") and len(sym.candidates) == 1 else "auto"
        market, df = cl.history(base, s, e, hint)
        code = f"{base}.TW" if market == "tse" else f"{base}.TWO"
    if df.empty:
        raise RuntimeError(f"證交所 / 櫃買中心查不到「{sym.raw}」的資料")
    return code, df


def fetch_history(sym: Symbol, period: str = "2y", start=None, end=None,
                  demo: bool = False, source: str = "auto") -> tuple[str, pd.DataFrame]:
    """回傳 (實際使用的代號, 日 K DataFrame)。抓不到就丟 RuntimeError。"""
    if source not in SOURCES:
        raise ValueError(f"資料來源只能是 {', '.join(SOURCES)}")
    if not demo and source != "yahoo" and uses_twse(sym):
        try:
            return fetch_twse(sym, period, start, end)
        except Exception as e:  # noqa: BLE001 - 網路錯誤種類很多
            if source == "twse":
                raise RuntimeError(f"證交所資料抓取失敗：{e}") from e
            print(f"⚠ 證交所資料抓取失敗（{e}），改用 Yahoo Finance", file=sys.stderr)
    if demo:
        code = sym.candidates[0]
        days = {"1mo": 22, "3mo": 66, "6mo": 130, "1y": 250, "2y": 500,
                "5y": 1250, "10y": 2500}.get(period, 500)
        df = demo_history(code, days=days, end=end)
        if start is not None:
            df = df[df.index >= pd.Timestamp(start)]
        return code, df

    import yfinance as yf  # 延遲匯入，示範模式不需要

    last_err = None
    for code in sym.candidates:
        try:
            kw = dict(progress=False, auto_adjust=False)
            if start or end:
                df = yf.download(code, start=start, end=end, **kw)
            else:
                df = yf.download(code, period=period, **kw)
            df = _flatten(df).dropna(subset=["Close"])
            if not df.empty:
                return code, df[["Open", "High", "Low", "Close", "Volume"]]
        except Exception as e:  # noqa: BLE001 - yfinance 例外種類很雜
            last_err = e
    msg = f"抓不到「{sym.raw}」的資料（試過 {', '.join(sym.candidates)}）"
    if last_err:
        msg += f"：{last_err}"
    raise RuntimeError(msg)
