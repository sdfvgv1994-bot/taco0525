"""交易策略：每個策略把日 K 轉成買賣訊號。

訊號 +1 = 買進（空手時才會買），-1 = 賣出（有持股時才會賣），0 = 不動作。
回測工具和看盤系統共用這裡的定義。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

from . import indicators as ind


@dataclass
class Output:
    # 線的名稱用英文，畫圖時不怕電腦沒有中文字型
    signal: pd.Series                               # +1 / -1 / 0
    overlays: dict = field(default_factory=dict)    # 疊在價格圖上的線
    panel: dict = field(default_factory=dict)       # 畫在副圖的線
    levels: list = field(default_factory=list)      # 副圖的水平參考線
    buy_reason: str = ""
    sell_reason: str = ""


@dataclass
class Strategy:
    key: str
    name: str
    description: str
    defaults: dict
    func: Callable
    warmup: Callable  # 參數 → 至少需要幾天資料

    def run(self, df: pd.DataFrame, **params) -> Output:
        unknown = set(params) - set(self.defaults)
        if unknown:
            raise ValueError(f"{self.name} 沒有參數：{', '.join(sorted(unknown))}"
                             f"（可用：{', '.join(self.defaults)}）")
        p = {**self.defaults, **params}
        need = int(self.warmup(p)) + 2
        if len(df) < need:
            raise ValueError(f"資料只有 {len(df)} 天，{self.name} 至少需要 {need} 天")
        return self.func(df, **p)

    def label(self, **params) -> str:
        p = {**self.defaults, **params}
        return f"{self.name}（{', '.join(f'{k}={v:g}' for k, v in p.items())}）"


def _ma(df, short, long):
    if short >= long:
        raise ValueError("短均線天數必須小於長均線")
    c = df["Close"]
    s, l = ind.sma(c, int(short)), ind.sma(c, int(long))
    return Output(ind.cross(s, l), overlays={f"MA{short:g}": s, f"MA{long:g}": l},
                  buy_reason="黃金交叉", sell_reason="死亡交叉")


def _macd(df, fast, slow, signal):
    if fast >= slow:
        raise ValueError("MACD 快線天數必須小於慢線")
    dif, dea, hist = ind.macd(df["Close"], int(fast), int(slow), int(signal))
    sig = ind.cross(dif, dea)
    # 前 slow 天 EMA 還沒穩定，不出訊號
    sig.iloc[: int(slow)] = 0
    return Output(sig, panel={"DIF": dif, "MACD": dea}, levels=[0],
                  buy_reason="MACD 黃金交叉", sell_reason="MACD 死亡交叉")


def _rsi(df, n, low, high):
    if low >= high:
        raise ValueError("RSI 低檔必須小於高檔")
    r = ind.rsi(df["Close"], int(n))
    r.iloc[: int(n)] = 50  # 暖機期
    up_low = ind.cross_level(r, low) == 1     # 從超賣區回升
    up_high = ind.cross_level(r, high) == 1   # 進入超買區
    sig = pd.Series(0, index=df.index)
    sig[up_low] = 1
    sig[up_high] = -1
    return Output(sig, panel={f"RSI{n:g}": r}, levels=[low, high],
                  buy_reason=f"RSI 從 {low:g} 以下回升", sell_reason=f"RSI 突破 {high:g}")


def _kd(df, n, low, high):
    if low >= high:
        raise ValueError("KD 低檔必須小於高檔")
    k, d = ind.kd(df["High"], df["Low"], df["Close"], int(n))
    c = ind.cross(k, d)
    sig = pd.Series(0, index=df.index)
    sig[(c == 1) & (d < low)] = 1
    sig[(c == -1) & (d > high)] = -1
    return Output(sig, panel={"K": k, "D": d}, levels=[low, high],
                  buy_reason=f"KD 低檔（<{low:g}）黃金交叉",
                  sell_reason=f"KD 高檔（>{high:g}）死亡交叉")


def _bbands(df, n, k):
    c = df["Close"]
    upper, mid, lower = ind.bollinger(c, int(n), k)
    back_above_lower = ind.cross(c, lower) == 1   # 跌破下軌後收回
    hit_upper = ind.cross(c, upper) == 1          # 突破上軌
    sig = pd.Series(0, index=df.index)
    sig[back_above_lower] = 1
    sig[hit_upper] = -1
    return Output(sig, overlays={"Upper": upper, "Middle": mid, "Lower": lower},
                  buy_reason="跌破布林下軌後收回", sell_reason="突破布林上軌")


def _breakout(df, entry, exit):
    c = df["Close"]
    hh = df["High"].rolling(int(entry)).max().shift(1)
    ll = df["Low"].rolling(int(exit)).min().shift(1)
    sig = pd.Series(0, index=df.index)
    sig[c > hh] = 1
    sig[c < ll] = -1
    return Output(sig, overlays={f"{entry:g}d high": hh, f"{exit:g}d low": ll},
                  buy_reason=f"突破 {entry:g} 日新高", sell_reason=f"跌破 {exit:g} 日新低")


STRATEGIES: dict[str, Strategy] = {s.key: s for s in [
    Strategy("ma", "均線交叉", "短均線往上穿過長均線買，往下穿過賣",
             {"short": 5, "long": 20}, _ma, lambda p: p["long"]),
    Strategy("macd", "MACD", "DIF 往上穿過訊號線買，往下穿過賣",
             {"fast": 12, "slow": 26, "signal": 9}, _macd, lambda p: p["slow"] + p["signal"]),
    Strategy("rsi", "RSI 超買超賣", "RSI 從超賣區回升買，衝進超買區賣",
             {"n": 14, "low": 30, "high": 70}, _rsi, lambda p: p["n"]),
    Strategy("kd", "KD 指標", "KD 在低檔黃金交叉買，在高檔死亡交叉賣",
             {"n": 9, "low": 30, "high": 70}, _kd, lambda p: p["n"]),
    Strategy("bbands", "布林通道", "收盤跌破下軌後收回買，突破上軌賣",
             {"n": 20, "k": 2}, _bbands, lambda p: p["n"]),
    Strategy("breakout", "突破新高", "收盤創 N 日新高買，跌破 M 日新低賣（海龜法則）",
             {"entry": 20, "exit": 10}, _breakout, lambda p: max(p["entry"], p["exit"])),
]}


def get(key: str) -> Strategy:
    try:
        return STRATEGIES[key.lower()]
    except KeyError:
        raise ValueError(f"沒有「{key}」這個策略，可用：{', '.join(STRATEGIES)}") from None


def parse_params(items) -> dict:
    """把 ['short=10', 'long=60'] 轉成 {'short': 10.0, 'long': 60.0}。"""
    out = {}
    for it in items or []:
        if "=" not in it:
            raise ValueError(f"參數格式要像 short=10，收到「{it}」")
        k, v = it.split("=", 1)
        try:
            out[k.strip()] = float(v)
        except ValueError:
            raise ValueError(f"參數 {k} 的值要是數字，收到「{v}」") from None
    return out
