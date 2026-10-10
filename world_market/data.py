"""看板資料：台灣項目找證交所，其他找 Yahoo Finance。"""
from __future__ import annotations

import hashlib
import sys
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.market import PERIOD_DAYS, demo_history, fetch_history, fetch_twse, parse_symbol  # noqa: E402
from world_market.markets import (ALL_ITEMS, CLOCKS, EXCHANGES, GROUPS, Item,  # noqa: E402
                                  local_time, market_status)

SPARK_DAYS = 30
OVERVIEW_TTL = 60
HISTORY_TTL = 10 * 60


def yahoo_code(it: Item) -> str:
    if it.source == "twse" and not it.symbol.startswith("^"):
        return f"{it.symbol}.TW"
    return it.symbol


def default_yahoo_download(symbols: list, period: str) -> dict:
    """一次下載多檔，回傳 {代號: 日 K DataFrame}。"""
    import yfinance as yf
    raw = yf.download(symbols, period=period, interval="1d", group_by="ticker",
                      progress=False, auto_adjust=False, threads=True)
    out = {}
    if raw is None or raw.empty:
        return out
    if isinstance(raw.columns, pd.MultiIndex):
        for sym in symbols:
            if sym in raw.columns.get_level_values(0):
                df = raw[sym].dropna(subset=["Close"])
                if not df.empty:
                    out[sym] = df
    elif len(symbols) == 1:
        out[symbols[0]] = raw.dropna(subset=["Close"])
    return out


class DataService:
    def __init__(self, demo: bool = False, yahoo_download=None, twse_client=None):
        self.demo = demo
        self.yahoo_download = yahoo_download or default_yahoo_download
        self._twse = twse_client
        self._lock = threading.Lock()
        self._overview = None
        self._overview_at = 0.0
        self._history: dict = {}

    @property
    def twse(self):
        if self._twse is None:
            from common import twse
            self._twse = twse.client()  # 和其他工具共用同一個限速與快取
        return self._twse

    # ------------------------------------------------------------ 總覽
    def overview(self, force: bool = False) -> dict:
        with self._lock:
            if not force and self._overview and time.time() - self._overview_at < OVERVIEW_TTL:
                return self._overview
            now = datetime.now(ZoneInfo("UTC"))
            data = self._load_all(now)
            self._overview = self._build(data, now)
            self._overview_at = time.time()
            return self._overview

    def _load_all(self, now) -> dict:
        """回傳 {代號: {"df": 日 K, "price": 最新價, "prev": 昨收, "source": 來源} 或 {"error": ...}}。"""
        if self.demo:
            return {sym: self._demo_entry(it, now) for sym, it in ALL_ITEMS.items()}
        out = {}
        tw_items = [it for it in ALL_ITEMS.values() if it.source == "twse"]
        try:
            out.update(self._load_twse(tw_items))
        except Exception as e:  # noqa: BLE001
            print(f"⚠ 證交所資料抓取失敗（{e}），台灣項目改用 Yahoo", file=sys.stderr)
        if "^TWOII" not in out:  # Yahoo 沒有櫃買指數，只能靠證交所即時報價
            out["^TWOII"] = {"error": "證交所即時報價暫時抓不到"}
        yahoo_items = [it for it in ALL_ITEMS.values() if it.symbol not in out]
        codes = {yahoo_code(it): it.symbol for it in yahoo_items}
        try:
            got = self.yahoo_download(list(codes), "3mo")
        except Exception as e:  # noqa: BLE001
            got = {}
            err = f"Yahoo Finance 抓取失敗：{e}"
        else:
            err = "查不到資料"
        for code, sym in codes.items():
            df = got.get(code)
            if df is None or df.empty:
                out[sym] = {"error": err}
                continue
            closes = df["Close"]
            out[sym] = {"df": df, "price": float(closes.iloc[-1]),
                        "prev": float(closes.iloc[-2]) if len(closes) > 1 else None,
                        "source": "Yahoo Finance"}
        return out

    def _load_twse(self, items: list) -> dict:
        end = date.today()
        start = end - timedelta(days=62)
        try:
            quotes = self.twse.realtime([it.symbol for it in items])
        except Exception as e:  # noqa: BLE001 - 即時報價失敗時，照樣用證交所的歷史收盤價
            print(f"⚠ 證交所即時報價抓不到（{e}），台灣項目先用最近一天的收盤價", file=sys.stderr)
            quotes = {}
        out = {}
        for it in items:
            q = quotes.get(it.symbol)
            if it.symbol == "^TWII":
                df = self.twse.index_history(start, end)
            elif it.symbol == "^TWOII":
                df = None  # 證交所沒有櫃買指數歷史，Yahoo 也沒有 ^TWOII，只顯示即時價
            else:
                _, df = self.twse.history(it.symbol, start, end)
            if q is None or q.price is None:
                if df is None or df.empty:
                    continue
                if market_status("TW") == "open" and df.index[-1].date() < local_time("TW").date():
                    # 盤中沒有即時報價：證交所日 K 只到昨天，交給 Yahoo（有今天的延遲報價）
                    continue
                closes = df["Close"]
                out[it.symbol] = {"df": df, "price": float(closes.iloc[-1]),
                                  "prev": float(closes.iloc[-2]) if len(closes) > 1 else None,
                                  "source": "證交所（收盤價）"}
                continue
            if df is not None and not df.empty and q.time is not None:
                today = pd.Timestamp(q.time.date())
                if df.index[-1] < today:  # 盤中把今天的價格接上走勢圖
                    df = pd.concat([df, pd.DataFrame({"Close": [q.price]}, index=[today])])
            out[it.symbol] = {"df": df, "price": float(q.price), "prev": q.prev_close,
                              "source": "證交所即時", "name": q.name}
        return out

    @staticmethod
    def _demo_df(it: Item, days: int, end=None) -> pd.DataFrame:
        """同一個商品永遠從同一條 10 年示範走勢取最後幾天，總覽和詳細走勢才會一致。"""
        return demo_history(yahoo_code(it), days=2600, end=end).iloc[-days:].copy()

    def _demo_entry(self, it: Item, now) -> dict:
        df = self._demo_df(it, 70, now.date())
        h = int(hashlib.md5(f"{it.symbol}{now:%Y%m%d%H%M}".encode()).hexdigest()[:8], 16)
        price = float(df["Close"].iloc[-1]) * (1 + ((h % 2001) - 1000) / 1000 * 0.01)
        df.iloc[-1, df.columns.get_loc("Close")] = price
        return {"df": df, "price": price, "prev": float(df["Close"].iloc[-2]), "source": "示範資料"}

    def _build(self, data: dict, now) -> dict:
        groups = []
        for gname, items in GROUPS:
            rows = []
            for it in items:
                d = data.get(it.symbol, {"error": "查不到資料"})
                row = {"symbol": it.symbol, "name": it.name, "exchange": it.exchange,
                       "exchange_name": EXCHANGES[it.exchange].name,
                       "status": market_status(it.exchange, now), "decimals": it.decimals}
                if "error" in d:
                    row["error"] = d["error"]
                else:
                    price, prev = d["price"], d["prev"]
                    row.update(price=price, prev_close=prev, source=d["source"],
                               change=(price - prev) if prev else None,
                               change_pct=((price / prev - 1) * 100) if prev else None)
                    df = d.get("df")
                    if df is not None and not df.empty:
                        s = df["Close"].dropna().iloc[-SPARK_DAYS:]
                        row["spark"] = [round(float(v), 6) for v in s]
                        row["spark_dates"] = [f"{i:%Y-%m-%d}" for i in s.index]
                rows.append(row)
            groups.append({"name": gname, "items": rows})
        clocks = []
        for key in CLOCKS:
            t = local_time(key, now)
            clocks.append({"key": key, "city": EXCHANGES[key].name, "time": f"{t:%H:%M}",
                           "weekday": "一二三四五六日"[t.weekday()],
                           "status": market_status(key, now)})
        tpe = local_time("TW", now)
        return {"updated": f"{tpe:%Y-%m-%d %H:%M:%S}",          # 台北時間（雲端機器是 UTC）
                "updated_utc": now.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "demo": self.demo, "clocks": clocks, "groups": groups,
                # 讓網頁自己用當下時間判斷開收盤（雲端資料可能是十幾分鐘前產生的）
                "clock_keys": CLOCKS,
                "exchanges": {k: {"name": e.name, "tz": e.tz, "always": e.always,
                                  "sessions": [[f"{a:%H:%M}", f"{b:%H:%M}"] for a, b in e.sessions]}
                              for k, e in EXCHANGES.items()}}

    # ------------------------------------------------------------ 單一商品走勢
    def history(self, symbol: str, period: str = "1y") -> dict:
        if symbol not in ALL_ITEMS:
            raise KeyError(f"不支援的代號：{symbol}")
        if period not in PERIOD_DAYS:
            raise ValueError(f"期間只支援 {', '.join(PERIOD_DAYS)}")
        key = (symbol, period)
        hit = self._history.get(key)
        if hit and time.time() - hit[0] < HISTORY_TTL:
            return hit[1]
        it = ALL_ITEMS[symbol]
        if symbol == "^TWOII" and not self.demo:
            raise RuntimeError("櫃買指數沒有歷史資料來源（證交所不提供，Yahoo 也沒有 ^TWOII）")
        if self.demo:
            days = {"1mo": 22, "3mo": 66, "6mo": 130, "1y": 250, "2y": 500,
                    "5y": 1250, "10y": 2500}[period]
            df, source = self._demo_df(it, days), "示範資料"
        elif it.source == "twse":
            sym = parse_symbol(symbol)
            try:
                _, df = fetch_twse(sym, period=period)
                source = "證交所"
            except Exception as e:  # noqa: BLE001 - 證交所抓不到就改用 Yahoo，來源照實標示
                print(f"⚠ 證交所資料抓取失敗（{e}），{it.name} 改用 Yahoo Finance", file=sys.stderr)
                _, df = fetch_history(sym, period=period, source="yahoo")
                source = "Yahoo Finance"
        else:
            got = self.yahoo_download([yahoo_code(it)], period)
            df = got.get(yahoo_code(it))
            if df is None or df.empty:
                raise RuntimeError(f"查不到 {it.name} 的歷史資料")
            source = "Yahoo Finance"
        df = df.dropna(subset=["Close"])
        closes = df["Close"]
        result = {
            "symbol": symbol, "name": it.name, "period": period, "source": source,
            "decimals": it.decimals,
            "dates": [f"{i:%Y-%m-%d}" for i in df.index],
            "close": [round(float(v), 6) for v in closes],
            "high": float(df["High"].max()) if "High" in df else float(closes.max()),
            "low": float(df["Low"].min()) if "Low" in df else float(closes.min()),
            "change_pct": float((closes.iloc[-1] / closes.iloc[0] - 1) * 100),
        }
        self._history[key] = (time.time(), result)
        return result
