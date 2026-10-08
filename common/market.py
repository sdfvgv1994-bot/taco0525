"""代號辨識與歷史資料下載。

- 只打數字（如 2330）→ 台股，先試上市 .TW，抓不到再試上櫃 .TWO
- 英文字母（如 AAPL）→ 美股
- 常見指數別名（加權、櫃買、SPX、NASDAQ…）→ 對應指數代號
- 常見加密貨幣（BTC、ETH…）→ XXX-USD
- 其他（含 ^ 或 - 或 . 的完整代號）→ 原樣使用

示範模式（demo=True）不連網，用代號當種子產生可重現的隨機走勢，方便測試。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

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


def fetch_history(sym: Symbol, period: str = "2y", start=None, end=None,
                  demo: bool = False) -> tuple[str, pd.DataFrame]:
    """回傳 (實際使用的代號, 日 K DataFrame)。抓不到就丟 RuntimeError。"""
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
