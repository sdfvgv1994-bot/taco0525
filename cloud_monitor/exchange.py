"""代號 → 交易所：決定一檔股票什麼時候算交易時間。

從 cloud_monitor/run.py 拆出來的積木；設定檔檢查（config.py）和看盤主程式（run.py）共用。
"""

from __future__ import annotations

import re

from common.market import parse_symbol, uses_twse
from world_market.data import yahoo_code
from world_market.markets import ALL_ITEMS

# Yahoo 代號的後綴 → 交易所（決定什麼時候算交易時間）
SUFFIX_EXCHANGE = {
    ".T": "JP", ".HK": "HK", ".KS": "KR", ".KQ": "KR", ".SS": "CN", ".SZ": "CN", ".SI": "SG",
    ".NS": "IN", ".BO": "IN", ".AX": "AU", ".L": "UK", ".DE": "EU", ".F": "EU", ".PA": "EU",
    ".AS": "EU", ".MI": "EU", ".MC": "EU", ".SW": "EU", ".BR": "EU", ".VI": "EU",
    ".TO": "CA", ".V": "CA", ".SA": "BR",
}


def exchange_of(name: str) -> str | None:
    """這檔在哪個交易所交易（EXCHANGES 的 key）；不知道時回傳 None。"""
    sym = parse_symbol(name)
    code = sym.candidates[0]
    if uses_twse(sym) or code in ("^TWOII",):
        return "TW"
    if sym.kind == "crypto":
        return "CRYPTO"
    for it in ALL_ITEMS.values():
        if code in (it.symbol, yahoo_code(it)):
            return it.exchange
    if code.endswith(("=X", "=F")):
        return "FX"
    if "." in code:
        return SUFFIX_EXCHANGE.get(code[code.rfind("."):])
    if re.fullmatch(r"[A-Z]{1,5}(-[A-Z]{1,2})?", code):   # 美股代號，例如 AAPL、BRK-B
        return "US"
    return None
