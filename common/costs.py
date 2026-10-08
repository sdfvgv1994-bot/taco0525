"""交易成本（台股規則）。

- 手續費 0.1425%，買賣都收，可設最低手續費（券商通常 20 元）
- 證交稅 0.3%，只有賣出台股時收
"""
from __future__ import annotations

FEE_RATE = 0.001425
TAX_RATE = 0.003
MIN_FEE = 20


def fee(amount: float, min_fee: float = 0) -> float:
    if amount <= 0:
        return 0.0
    return max(round(amount * FEE_RATE, 2), min_fee)


def tax(amount: float, is_tw_stock: bool = True) -> float:
    if amount <= 0 or not is_tw_stock:
        return 0.0
    return round(amount * TAX_RATE, 2)


def buy_cost(price: float, shares: int, min_fee: float = 0) -> float:
    """買進總花費 = 成交金額 + 手續費。"""
    amount = price * shares
    return amount + fee(amount, min_fee)


def sell_proceeds(price: float, shares: int, is_tw_stock: bool = True,
                  min_fee: float = 0) -> float:
    """賣出實收 = 成交金額 − 手續費 − 證交稅。"""
    amount = price * shares
    return amount - fee(amount, min_fee) - tax(amount, is_tw_stock)


def max_shares(cash: float, price: float, min_fee: float = 0) -> int:
    """現金最多能買幾股（含手續費）。"""
    if price <= 0 or cash <= 0:
        return 0
    n = int(cash / (price * (1 + FEE_RATE)))
    while n > 0 and buy_cost(price, n, min_fee) > cash:
        n -= 1
    return n
