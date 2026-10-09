"""看板上的商品清單與各交易所的交易時間。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Exchange:
    name: str
    tz: str
    sessions: tuple          # ((開盤, 收盤), ...) 當地時間；有午休就分兩段
    always: str = ""         # "24/7"（加密貨幣）或 "24/5"（外匯、期貨）


EXCHANGES = {
    "TW": Exchange("台北", "Asia/Taipei", ((time(9, 0), time(13, 30)),)),
    "JP": Exchange("東京", "Asia/Tokyo", ((time(9, 0), time(11, 30)), (time(12, 30), time(15, 30)))),
    "KR": Exchange("首爾", "Asia/Seoul", ((time(9, 0), time(15, 30)),)),
    "HK": Exchange("香港", "Asia/Hong_Kong", ((time(9, 30), time(12, 0)), (time(13, 0), time(16, 0)))),
    "CN": Exchange("上海", "Asia/Shanghai", ((time(9, 30), time(11, 30)), (time(13, 0), time(15, 0)))),
    "SG": Exchange("新加坡", "Asia/Singapore", ((time(9, 0), time(12, 0)), (time(13, 0), time(17, 0)))),
    "IN": Exchange("孟買", "Asia/Kolkata", ((time(9, 15), time(15, 30)),)),
    "AU": Exchange("雪梨", "Australia/Sydney", ((time(10, 0), time(16, 0)),)),
    "UK": Exchange("倫敦", "Europe/London", ((time(8, 0), time(16, 30)),)),
    "EU": Exchange("法蘭克福", "Europe/Berlin", ((time(9, 0), time(17, 30)),)),
    "US": Exchange("紐約", "America/New_York", ((time(9, 30), time(16, 0)),)),
    "CA": Exchange("多倫多", "America/Toronto", ((time(9, 30), time(16, 0)),)),
    "BR": Exchange("聖保羅", "America/Sao_Paulo", ((time(10, 0), time(17, 0)),)),
    "FX": Exchange("外匯 / 期貨", "America/New_York", (), always="24/5"),
    "CRYPTO": Exchange("加密貨幣", "UTC", (), always="24/7"),
}


@dataclass(frozen=True)
class Item:
    symbol: str      # Yahoo 代號（台股項目另由證交所提供）
    name: str
    exchange: str    # EXCHANGES 的 key
    source: str = "yahoo"   # yahoo / twse
    decimals: int = 2


GROUPS: list[tuple[str, list[Item]]] = [
    ("台灣", [
        Item("^TWII", "加權指數", "TW", "twse"),
        Item("^TWOII", "櫃買指數", "TW", "twse"),
        Item("2330", "台積電", "TW", "twse"),
        Item("2317", "鴻海", "TW", "twse"),
        Item("2454", "聯發科", "TW", "twse"),
    ]),
    ("美洲", [
        Item("^GSPC", "S&P 500", "US"),
        Item("^DJI", "道瓊工業", "US"),
        Item("^IXIC", "那斯達克", "US"),
        Item("^SOX", "費城半導體", "US"),
        Item("^RUT", "羅素 2000", "US"),
        Item("^GSPTSE", "加拿大 TSX", "CA"),
        Item("^BVSP", "巴西 Bovespa", "BR"),
    ]),
    ("亞太", [
        Item("^N225", "日經 225", "JP"),
        Item("^KS11", "韓國 KOSPI", "KR"),
        Item("^HSI", "香港恆生", "HK"),
        Item("000001.SS", "上證指數", "CN"),
        Item("399001.SZ", "深證成指", "CN"),
        Item("^STI", "新加坡海峽", "SG"),
        Item("^BSESN", "印度 Sensex", "IN"),
        Item("^AXJO", "澳洲 ASX 200", "AU"),
    ]),
    ("歐洲", [
        Item("^FTSE", "英國 FTSE 100", "UK"),
        Item("^GDAXI", "德國 DAX", "EU"),
        Item("^FCHI", "法國 CAC 40", "EU"),
        Item("^STOXX50E", "歐洲 STOXX 50", "EU"),
    ]),
    ("匯率", [
        Item("TWD=X", "美元 / 台幣", "FX", decimals=3),
        Item("JPY=X", "美元 / 日圓", "FX"),
        Item("EURUSD=X", "歐元 / 美元", "FX", decimals=4),
        Item("CNY=X", "美元 / 人民幣", "FX", decimals=4),
        Item("DX-Y.NYB", "美元指數", "FX"),
    ]),
    ("原物料與債券", [
        Item("GC=F", "黃金", "FX"),
        Item("SI=F", "白銀", "FX"),
        Item("CL=F", "西德州原油", "FX"),
        Item("HG=F", "銅", "FX", decimals=3),
        Item("^TNX", "美國 10 年債殖利率", "FX", decimals=3),
        Item("^VIX", "VIX 恐慌指數", "US"),
    ]),
    ("加密貨幣", [
        Item("BTC-USD", "比特幣", "CRYPTO", decimals=0),
        Item("ETH-USD", "以太幣", "CRYPTO"),
        Item("SOL-USD", "Solana", "CRYPTO"),
    ]),
]

ALL_ITEMS = {it.symbol: it for _, items in GROUPS for it in items}
# 首頁上方顯示的世界時鐘
CLOCKS = ["TW", "JP", "UK", "EU", "US"]


def market_status(ex_key: str, now: datetime | None = None) -> str:
    """回傳 'open' / 'closed'。不考慮國定假日。"""
    ex = EXCHANGES[ex_key]
    now = (now or datetime.now(ZoneInfo("UTC"))).astimezone(ZoneInfo(ex.tz))
    if ex.always == "24/7":
        return "open"
    if ex.always == "24/5":
        # 外匯：週日 17:00 到週五 17:00（紐約時間）
        wd, t = now.weekday(), now.time()
        if wd == 5 or (wd == 6 and t < time(17)) or (wd == 4 and t >= time(17)):
            return "closed"
        return "open"
    if now.weekday() >= 5:
        return "closed"
    return "open" if any(a <= now.time() < b for a, b in ex.sessions) else "closed"


def local_time(ex_key: str, now: datetime | None = None) -> datetime:
    return (now or datetime.now(ZoneInfo("UTC"))).astimezone(ZoneInfo(EXCHANGES[ex_key].tz))
