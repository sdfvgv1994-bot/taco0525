"""技術指標。"""
from __future__ import annotations

import pandas as pd


def sma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n).mean()


def rsi(series: pd.Series, n: int = 14) -> pd.Series:
    diff = series.diff()
    gain = diff.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-diff.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = gain / loss.replace(0, float("nan"))
    return (100 - 100 / (1 + rs)).fillna(100)


def cross(short: pd.Series, long: pd.Series) -> pd.Series:
    """+1 = 黃金交叉（短線由下往上穿過長線），-1 = 死亡交叉，0 = 無。"""
    above = short > long
    prev = above.shift(1)
    valid = short.notna() & long.notna() & short.shift(1).notna() & long.shift(1).notna()
    out = pd.Series(0, index=short.index)
    out[valid & above & (prev == False)] = 1  # noqa: E712
    out[valid & ~above & (prev == True)] = -1  # noqa: E712
    return out


def ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False).mean()


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """回傳 (DIF, MACD 訊號線, 柱狀體)。"""
    dif = ema(series, fast) - ema(series, slow)
    dea = ema(dif, signal)
    return dif, dea, dif - dea


def bollinger(series: pd.Series, n: int = 20, k: float = 2.0):
    """回傳 (上軌, 中軌, 下軌)。"""
    mid = sma(series, n)
    std = series.rolling(n).std(ddof=0)
    return mid + k * std, mid, mid - k * std


def kd(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 9):
    """台灣常用的 KD：RSV 取 n 日，K、D 以 1/3 權重平滑，起始值 50。"""
    hh, ll = high.rolling(n).max(), low.rolling(n).min()
    rsv = ((close - ll) / (hh - ll).replace(0, float("nan")) * 100).fillna(50)
    k_vals, d_vals = [], []
    k = d = 50.0
    for i, r in enumerate(rsv):
        if i >= n - 1:
            k = k * 2 / 3 + r / 3
            d = d * 2 / 3 + k / 3
        k_vals.append(k)
        d_vals.append(d)
    return pd.Series(k_vals, index=close.index), pd.Series(d_vals, index=close.index)


def cross_level(series: pd.Series, level: float) -> pd.Series:
    """+1 = 由下往上穿過 level，-1 = 由上往下跌破 level。"""
    return cross(series, pd.Series(level, index=series.index))
