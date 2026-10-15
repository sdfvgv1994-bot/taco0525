"""把舊版電腦看盤（py618\\stock_monitor，tkinter / 手機網頁版）的存檔搬進來。

會搬：自選股（上下限、手動成本、停損 %、移動停損 %、水平線、趨勢線）、均線與自動交易設定、
      模擬帳戶（現金、持股、交易紀錄）、LINE / Telegram / Discord 通知設定、手機網址的 key。
只在這裡還沒有存檔（data 資料夾沒有 account.json 和 watchlist.json）時才搬，舊檔案不會被改動。

手動執行：python -m stock_monitor.import_old 舊資料夾路徑
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from common import costs
from common.market import parse_symbol

from .storage import DEFAULT_SETTINGS, Store

ROOT = Path(__file__).resolve().parent.parent
COPY_AS_IS = ["notify_config.json", "web_config.json", "news_state.json"]


def find_old_dir() -> Path | None:
    """預設位置：和這個專案放在同一層的 stock_monitor 資料夾（py618\\stock_monitor）。"""
    for cand in (ROOT.parent / "stock_monitor",):
        if is_old_dir(cand):
            return cand
    return None


def is_old_dir(path: Path) -> bool:
    try:
        data = json.loads((Path(path) / "watchlist.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and isinstance(data.get("stocks"), list)   # 舊格式才有 stocks 清單


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def convert(old_watch: dict, old_paper: dict) -> tuple[dict, dict, dict]:
    """舊格式 → (settings, watchlist, account)。"""
    o = old_watch.get("settings", {})
    settings = {**DEFAULT_SETTINGS,
                "strategies": ["ma"],
                "short_ma": int(o.get("short", DEFAULT_SETTINGS["short_ma"])),
                "long_ma": int(o.get("long", DEFAULT_SETTINGS["long_ma"])),
                "interval": int(o.get("interval", 60)),
                "auto": bool(o.get("auto", True)),
                "auto_trade": bool(o.get("auto_trade", False)),
                "auto_buy_amount": float(o.get("budget", DEFAULT_SETTINGS["auto_buy_amount"])),
                "stop_loss_pct": 0.0,     # 舊版沒有整體停損，只有每檔自己設的
                "trail_stop_pct": 0.0,
                "news_enabled": bool(o.get("news_enabled", True)),
                "news_time": o.get("news_time", "08:00")}

    positions_old = (old_paper or {}).get("positions", {})
    watchlist, peaks = {}, {}
    for st in old_watch.get("stocks", []):
        code = str(st.get("ticker", "")).strip().upper()
        try:
            parse_symbol(code)
        except ValueError:
            continue
        entry = {"lower": st.get("below"), "upper": st.get("above")}
        if entry["lower"] is not None and entry["upper"] is not None and entry["lower"] >= entry["upper"]:
            entry["lower"] = entry["upper"] = None
        for old_key, new_key in (("stop", "stop"), ("trail", "trail")):
            if st.get(old_key):
                entry[new_key] = float(st[old_key])
        if st.get("hlines"):
            entry["support"] = sorted({float(x) for x in st["hlines"]}, reverse=True)
        if st.get("trendlines"):
            entry["trendlines"] = st["trendlines"]
        if st.get("cost") and code not in positions_old:   # 沒有模擬持股的手動成本（例如真實持股）
            entry["cost"] = float(st["cost"])
            if st.get("peak"):
                entry["peak"] = float(st["peak"])
        if st.get("peak"):
            peaks[code] = float(st["peak"])
        watchlist[code] = entry

    account = {"initial": (old_paper or {}).get("initial", 1_000_000),
               "cash": round(float((old_paper or {}).get("cash", 1_000_000)), 2),
               "positions": {}, "trades": []}
    for code, p in positions_old.items():
        code = code.upper()
        shares, cost = int(p["shares"]), float(p["cost"])
        fee = costs.fee(cost * shares, costs.MIN_FEE)
        pos = {"shares": shares, "avg_cost": (cost * shares + fee) / shares,   # 新版的成本含買進手續費
               "tw": parse_symbol(code).is_tw_stock}
        if code in peaks:
            pos["peak"] = max(peaks[code], pos["avg_cost"])
        account["positions"][code] = pos
        watchlist.setdefault(code, {"lower": None, "upper": None})
    for t in (old_paper or {}).get("trades", []):
        amount = float(t["price"]) * int(t["shares"])
        buy = t["side"] == "買進"
        rec = {"time": t.get("time", ""), "side": t["side"], "code": str(t["ticker"]).upper(),
               "price": round(float(t["price"]), 4), "shares": int(t["shares"]),
               "fee": float(t.get("fee") or 0), "tax": float(t.get("tax") or 0),
               "cash_flow": round(-(amount + (t.get("fee") or 0)) if buy
                                  else amount - (t.get("fee") or 0) - (t.get("tax") or 0), 2),
               "reason": t.get("reason", "")}
        if not buy and t.get("pnl") is not None:
            rec["pnl"] = round(float(t["pnl"]), 2)
        account["trades"].append(rec)
    return settings, watchlist, account


def import_old(old_dir, data_dir, force: bool = False) -> list[str]:
    """搬舊版存檔。回傳說明文字（沒搬就是空的）。"""
    old_dir, store = Path(old_dir), Store(data_dir)
    if not is_old_dir(old_dir):
        return []
    if not force and ((store.dir / "account.json").exists() or (store.dir / "watchlist.json").exists()):
        return []
    settings, watchlist, account = convert(_load(old_dir / "watchlist.json", {}),
                                           _load(old_dir / "paper_account.json", {}))
    store.save_settings(settings)
    store.save_watchlist(watchlist)
    store.save("account.json", account)
    copied = []
    for name in COPY_AS_IS:
        src = old_dir / name
        if src.is_file() and not (store.dir / name).exists():
            shutil.copy2(src, store.dir / name)
            copied.append(name)
    store.log(f"從舊版 {old_dir} 搬入存檔：自選股 {len(watchlist)} 檔、模擬持股 {len(account['positions'])} 檔")
    msg = [f"已從舊版（{old_dir}）搬入：自選股 {len(watchlist)} 檔、模擬持股 {len(account['positions'])} 檔、"
           f"交易紀錄 {len(account['trades'])} 筆"]
    if "notify_config.json" in copied:
        msg.append("通知設定（LINE / Telegram / Discord）也一起搬過來了")
    if "web_config.json" in copied:
        msg.append("手機網址的 key 沿用舊版，手機書籤不用改")
    return msg


def auto_import(data_dir) -> list[str]:
    """桌面版、手機版啟動時呼叫：找得到舊版、這裡又還沒有存檔，就自動搬一次。"""
    old = find_old_dir()
    return import_old(old, data_dir) if old else []


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    data = Path(__file__).resolve().parent / "data"
    out = import_old(sys.argv[1], data, force="--force" in sys.argv)
    print("\n".join(out) or "沒有搬：找不到舊版存檔，或這裡已經有存檔（要覆蓋請加 --force）")
