"""模擬交易帳戶。"""
from __future__ import annotations

from datetime import datetime

from common import costs

from .storage import INITIAL_CASH


class TradeError(Exception):
    pass


class Account:
    def __init__(self, cash: float = INITIAL_CASH, positions=None, trades=None,
                 initial: float = INITIAL_CASH):
        self.initial = initial
        self.cash = cash
        # {代號: {"shares": int, "avg_cost": float, "tw": bool}}，avg_cost 含買進手續費
        self.positions: dict = positions or {}
        self.trades: list = trades or []

    # ---- 存讀檔 ----
    def to_dict(self) -> dict:
        return {"initial": self.initial, "cash": round(self.cash, 2),
                "positions": self.positions, "trades": self.trades}

    @classmethod
    def from_dict(cls, d: dict | None) -> "Account":
        if not d:
            return cls()
        return cls(d.get("cash", INITIAL_CASH), d.get("positions", {}),
                   d.get("trades", []), d.get("initial", INITIAL_CASH))

    # ---- 交易 ----
    def buy(self, code: str, price: float, shares: int, tw: bool = True,
            reason: str = "手動", when: datetime | None = None) -> dict:
        if shares <= 0:
            raise TradeError("股數必須大於 0")
        if price <= 0:
            raise TradeError("價格不正確")
        amount = price * shares
        f = costs.fee(amount, costs.MIN_FEE)
        total = amount + f
        if total > self.cash + 1e-9:
            raise TradeError(f"現金不足：需要 {total:,.0f}，可用 {self.cash:,.0f}")
        self.cash -= total
        pos = self.positions.get(code, {"shares": 0, "avg_cost": 0.0, "tw": tw})
        new_shares = pos["shares"] + shares
        pos["avg_cost"] = (pos["avg_cost"] * pos["shares"] + total) / new_shares
        pos["shares"] = new_shares
        pos["tw"] = tw
        self.positions[code] = pos
        return self._record("買進", code, price, shares, f, 0.0, -total, reason, when)

    def sell(self, code: str, price: float, shares: int | None = None,
             reason: str = "手動", when: datetime | None = None) -> dict:
        pos = self.positions.get(code)
        if not pos or pos["shares"] <= 0:
            raise TradeError(f"沒有 {code} 的持股")
        shares = pos["shares"] if shares is None else shares
        if shares <= 0:
            raise TradeError("股數必須大於 0")
        if shares > pos["shares"]:
            raise TradeError(f"持股只有 {pos['shares']} 股，不能賣 {shares} 股")
        if price <= 0:
            raise TradeError("價格不正確")
        amount = price * shares
        f = costs.fee(amount, costs.MIN_FEE)
        t = costs.tax(amount, pos.get("tw", True))
        net = amount - f - t
        pnl = net - pos["avg_cost"] * shares
        self.cash += net
        pos["shares"] -= shares
        if pos["shares"] == 0:
            del self.positions[code]
        rec = self._record("賣出", code, price, shares, f, t, net, reason, when)
        rec["pnl"] = round(pnl, 2)
        return rec

    def _record(self, side, code, price, shares, f, t, cash_flow, reason, when):
        rec = {
            "time": (when or datetime.now()).strftime("%Y-%m-%d %H:%M:%S"),
            "side": side, "code": code, "price": round(price, 4), "shares": shares,
            "fee": round(f, 2), "tax": round(t, 2), "cash_flow": round(cash_flow, 2),
            "reason": reason,
        }
        self.trades.append(rec)
        return rec

    # ---- 統計 ----
    def holds(self, code: str) -> bool:
        return self.positions.get(code, {}).get("shares", 0) > 0

    def market_value(self, prices: dict) -> float:
        return sum(p["shares"] * prices.get(c, p["avg_cost"])
                   for c, p in self.positions.items())

    def total_assets(self, prices: dict) -> float:
        return self.cash + self.market_value(prices)

    def return_pct(self, prices: dict) -> float:
        return (self.total_assets(prices) / self.initial - 1) * 100
