"""從最新行情判斷要不要提醒。"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from common.indicators import cross, rsi, sma


@dataclass
class Alert:
    code: str
    kind: str      # golden / death / above / below / stop
    message: str

    @property
    def key(self) -> str:
        return f"{self.code}|{self.kind}"


@dataclass
class Snapshot:
    code: str
    price: float
    prev_close: float
    short_ma: float
    long_ma: float
    rsi: float
    cross: int  # 今天的交叉：+1 黃金、-1 死亡、0 無

    @property
    def change_pct(self) -> float:
        return (self.price / self.prev_close - 1) * 100 if self.prev_close else 0.0


def snapshot(code: str, closes: pd.Series, price: float | None,
             short_n: int, long_n: int) -> Snapshot:
    """closes 為日收盤；price 為最新價（盤中），會取代最後一根。"""
    closes = closes.dropna().astype(float).copy()
    if len(closes) < 2:
        raise ValueError(f"{code} 資料太少")
    if price is not None:
        closes.iloc[-1] = price
    s, l = sma(closes, short_n), sma(closes, long_n)
    c = cross(s, l)
    return Snapshot(code, float(closes.iloc[-1]), float(closes.iloc[-2]),
                    float(s.iloc[-1]), float(l.iloc[-1]),
                    float(rsi(closes).iloc[-1]), int(c.iloc[-1]))


def evaluate(snap: Snapshot, limits: dict, position: dict | None,
             stop_loss_pct: float) -> list[Alert]:
    out = []
    code, p = snap.code, snap.price
    if snap.cross == 1:
        out.append(Alert(code, "golden",
                         f"{code} 黃金交叉（短均 {snap.short_ma:.2f} > 長均 {snap.long_ma:.2f}）"))
    elif snap.cross == -1:
        out.append(Alert(code, "death",
                         f"{code} 死亡交叉（短均 {snap.short_ma:.2f} < 長均 {snap.long_ma:.2f}）"))
    upper, lower = limits.get("upper"), limits.get("lower")
    if upper is not None and p >= upper:
        out.append(Alert(code, "above", f"{code} 突破上限 {upper}，現價 {p:.2f}"))
    if lower is not None and p <= lower:
        out.append(Alert(code, "below", f"{code} 跌破下限 {lower}，現價 {p:.2f}"))
    if position and position.get("shares", 0) > 0 and stop_loss_pct > 0:
        cost = position["avg_cost"]
        loss = (p / cost - 1) * 100
        if loss <= -stop_loss_pct:
            out.append(Alert(code, "stop",
                             f"{code} 觸發停損：成本 {cost:.2f}，現價 {p:.2f}（{loss:.1f}%）"))
    return out
