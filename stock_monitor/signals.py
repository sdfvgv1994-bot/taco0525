"""從最新行情判斷要不要提醒。"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from common import strategies as strat
from common.indicators import rsi, sma


@dataclass
class Alert:
    code: str
    kind: str          # buy / sell / above / below / stop
    message: str
    strategy: str = ""  # 哪個策略發出的（價格、停損提醒為空）

    @property
    def key(self) -> str:
        return f"{self.code}|{self.kind}|{self.strategy}"


@dataclass
class Snapshot:
    code: str
    price: float
    prev_close: float
    short_ma: float
    long_ma: float
    rsi: float
    signals: dict = field(default_factory=dict)   # 策略代號 → 今天的訊號 +1 / -1 / 0
    reasons: dict = field(default_factory=dict)   # 策略代號 → (買進理由, 賣出理由)
    date: str | None = None                       # 最後一根 K 棒的日期（YYYY-MM-DD）
    skipped: dict = field(default_factory=dict)   # 策略代號 → 這次無法判斷的原因

    @property
    def change_pct(self) -> float:
        return (self.price / self.prev_close - 1) * 100 if self.prev_close else 0.0


def strategy_params(settings: dict, key: str) -> dict:
    """均線策略的天數沿用 short_ma / long_ma 設定，其他策略用 strategy_params。"""
    p = dict(settings.get("strategy_params", {}).get(key, {}))
    if key == "ma":
        p.setdefault("short", settings["short_ma"])
        p.setdefault("long", settings["long_ma"])
    return p


def snapshot(code: str, df: pd.DataFrame, price: float | None, settings: dict) -> Snapshot:
    """df 為日 K；price 為最新價（盤中），會取代最後一根的收盤並更新高低點。"""
    df = df.dropna(subset=["Close"]).astype(float).copy()
    if len(df) < 2:
        raise ValueError(f"{code} 資料太少")
    if "High" not in df:
        df["High"] = df["Close"]
    if "Low" not in df:
        df["Low"] = df["Close"]
    if price is not None:
        last = df.index[-1]
        df.loc[last, "Close"] = price
        df.loc[last, "High"] = max(df.loc[last, "High"], price)
        df.loc[last, "Low"] = min(df.loc[last, "Low"], price)
    closes = df["Close"]

    signals, reasons, skipped = {}, {}, {}
    for key in settings.get("strategies", ["ma"]):
        st = strat.get(key)
        try:
            out = st.run(df, **strategy_params(settings, key))
        except Exception as e:  # noqa: BLE001 - 資料不夠或參數不對：這個策略不判斷，其他照常
            skipped[key] = str(e)
            continue
        signals[key] = int(out.signal.iloc[-1])
        reasons[key] = (out.buy_reason, out.sell_reason)

    last = df.index[-1]
    date = last.strftime("%Y-%m-%d") if hasattr(last, "strftime") else None
    return Snapshot(code, float(closes.iloc[-1]), float(closes.iloc[-2]),
                    float(sma(closes, settings["short_ma"]).iloc[-1]),
                    float(sma(closes, settings["long_ma"]).iloc[-1]),
                    float(rsi(closes).iloc[-1]), signals, reasons, date, skipped)


def evaluate(snap: Snapshot, limits: dict, position: dict | None,
             stop_loss_pct: float) -> list[Alert]:
    out = []
    code, p = snap.code, snap.price
    for key, sig in snap.signals.items():
        if sig == 0:
            continue
        name = strat.get(key).name
        buy_why, sell_why = snap.reasons[key]
        if sig == 1:
            out.append(Alert(code, "buy", f"{code} 買進訊號【{name}】{buy_why}，現價 {p:.2f}", key))
        else:
            out.append(Alert(code, "sell", f"{code} 賣出訊號【{name}】{sell_why}，現價 {p:.2f}", key))
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
