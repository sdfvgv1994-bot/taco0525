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
