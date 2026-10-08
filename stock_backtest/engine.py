"""均線交叉策略回測。

規則（都以當天收盤價成交）：
- 黃金交叉（短均線由下往上穿過長均線）且空手 → 全部資金買進
- 死亡交叉且持股 → 全部賣出
- 固定停損：收盤價比買進價跌超過 X% → 賣出
- 移動停損：收盤價比買進後的最高收盤價回落超過 X% → 賣出
- 停損出場後要等下一次黃金交叉才會再買
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from common import costs
from common.indicators import cross, sma

EXIT_CROSS, EXIT_FIXED, EXIT_TRAIL, EXIT_END = "死亡交叉", "固定停損", "移動停損", "期末未平倉"


@dataclass
class Trade:
    entry_date: pd.Timestamp
    entry_price: float
    shares: int
    cost: float                      # 含手續費
    exit_date: pd.Timestamp | None = None
    exit_price: float | None = None
    proceeds: float | None = None    # 扣手續費、稅
    reason: str = ""

    @property
    def pnl(self) -> float:
        return (self.proceeds or 0) - self.cost

    @property
    def return_pct(self) -> float:
        return (self.pnl / self.cost) * 100


@dataclass
class Result:
    data: pd.DataFrame               # 含 short / long / equity / hold_equity 欄
    trades: list = field(default_factory=list)
    capital: float = 0
    stats: dict = field(default_factory=dict)


def _max_drawdown(equity: pd.Series) -> float:
    peak = equity.cummax()
    return float(((equity / peak) - 1).min() * 100)


def _annualized(total_ret: float, days: int) -> float:
    years = days / 365.25
    if years <= 0 or total_ret <= -100:
        return float("nan")
    return ((1 + total_ret / 100) ** (1 / years) - 1) * 100


def run_backtest(df: pd.DataFrame, short_n: int = 5, long_n: int = 20,
                 capital: float = 1_000_000, stop_loss_pct: float = 0,
                 trailing_pct: float = 0, is_tw_stock: bool = True) -> Result:
    if short_n >= long_n:
        raise ValueError("短均線天數必須小於長均線")
    if len(df) < long_n + 2:
        raise ValueError(f"資料只有 {len(df)} 天，不夠算 {long_n} 日均線")

    d = df.copy()
    close = d["Close"].astype(float)
    d["short"], d["long"] = sma(close, short_n), sma(close, long_n)
    signal = cross(d["short"], d["long"])

    cash, shares = float(capital), 0
    trades: list[Trade] = []
    open_trade: Trade | None = None
    peak = 0.0
    equity = np.empty(len(d))

    for i, (dt, px) in enumerate(close.items()):
        if open_trade is not None:
            peak = max(peak, px)
            reason = None
            if stop_loss_pct > 0 and px <= open_trade.entry_price * (1 - stop_loss_pct / 100):
                reason = EXIT_FIXED
            elif trailing_pct > 0 and px <= peak * (1 - trailing_pct / 100):
                reason = EXIT_TRAIL
            elif signal.iloc[i] == -1:
                reason = EXIT_CROSS
            if reason:
                got = costs.sell_proceeds(px, shares, is_tw_stock)
                cash += got
                open_trade.exit_date, open_trade.exit_price = dt, px
                open_trade.proceeds, open_trade.reason = got, reason
                shares, open_trade = 0, None
        elif signal.iloc[i] == 1:
            n = costs.max_shares(cash, px)
            if n > 0:
                spent = costs.buy_cost(px, n)
                cash -= spent
                shares, peak = n, px
                open_trade = Trade(dt, px, n, spent)
                trades.append(open_trade)
        # 權益以「若今天全部賣出可拿回的錢」計算
        equity[i] = cash + (costs.sell_proceeds(px, shares, is_tw_stock) if shares else 0)

    if open_trade is not None:
        last_dt, last_px = close.index[-1], float(close.iloc[-1])
        open_trade.exit_date, open_trade.exit_price = last_dt, last_px
        open_trade.proceeds = costs.sell_proceeds(last_px, shares, is_tw_stock)
        open_trade.reason = EXIT_END

    d["equity"] = equity

    # 買進持有：第一天收盤全買，同樣扣成本
    first = float(close.iloc[0])
    hold_n = costs.max_shares(capital, first)
    hold_cash = capital - costs.buy_cost(first, hold_n)
    d["hold_equity"] = hold_cash + close.apply(lambda p: costs.sell_proceeds(p, hold_n, is_tw_stock))

    days = (d.index[-1] - d.index[0]).days if isinstance(d.index, pd.DatetimeIndex) else len(d)
    strat_ret = (equity[-1] / capital - 1) * 100
    hold_ret = (d["hold_equity"].iloc[-1] / capital - 1) * 100
    closed = [t for t in trades if t.reason]
    wins = [t for t in closed if t.pnl > 0]
    stats = {
        "策略報酬%": strat_ret,
        "買進持有報酬%": hold_ret,
        "策略年化%": _annualized(strat_ret, days),
        "買進持有年化%": _annualized(hold_ret, days),
        "策略最大回撤%": _max_drawdown(d["equity"]),
        "買進持有最大回撤%": _max_drawdown(d["hold_equity"]),
        "交易次數": len(closed),
        "勝率%": (len(wins) / len(closed) * 100) if closed else 0.0,
        "總手續費+稅": sum(t.cost - t.entry_price * t.shares for t in closed)
                     + sum(t.exit_price * t.shares - t.proceeds for t in closed),
        "停損出場": {k: sum(t.reason == k for t in closed) for k in (EXIT_FIXED, EXIT_TRAIL)},
    }
    return Result(d, trades, capital, stats)
