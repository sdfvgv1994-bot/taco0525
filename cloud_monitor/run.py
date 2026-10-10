"""雲端看盤：GitHub Actions 每 15 分鐘執行一次（和全球看板同一個排程）。

    python cloud_monitor/run.py --state _state --out _site/monitor

- 設定來自 cloud_monitor/config.toml（在 GitHub 網頁就能改）
- 帳戶、提醒紀錄、股名、資產走勢存在 --state 資料夾（雲端是 monitor-data 分支），每次執行後存回去
- 只在該檔所屬市場的交易時間內判斷訊號、自動交易；休市時只更新報價顯示
- 歷史資料用 Yahoo（有今天的延遲價格）；台股另外試證交所即時報價
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tomllib
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cloud_monitor import notify  # noqa: E402
from common import strategies as strat  # noqa: E402
from common.market import parse_symbol, uses_twse  # noqa: E402
from stock_monitor.engine import Monitor  # noqa: E402
from stock_monitor.storage import DEFAULT_SETTINGS, Store  # noqa: E402
from world_market.data import yahoo_code  # noqa: E402
from world_market.markets import ALL_ITEMS, EXCHANGES, market_status  # noqa: E402

TPE = ZoneInfo("Asia/Taipei")
HERE = Path(__file__).resolve().parent
CONFIG = HERE / "config.toml"
PAGE = HERE / "index.html"
EDIT_URL = "https://github.com/sdfvgv1994-bot/taco0525/edit/main/cloud_monitor/config.toml"
EQUITY_MAX = 5000
NOTIFY_DEFAULT = {"enabled": True, "trades": True, "signals": "primary", "price_alerts": True}
NUMBER_SETTINGS = {"auto_buy_amount": (1, 1e9), "stop_loss_pct": (0, 100),
                   "short_ma": (1, 250), "long_ma": (2, 500)}


class ConfigError(Exception):
    pass


# ---------------------------------------------------------------- 設定檔
def load_config(path: Path) -> tuple[dict, list]:
    """回傳 (設定, 警告)。格式錯到無法使用時丟 ConfigError。

    設定 = {"watchlist": [...], "limits": {...}, "settings": {...}, "strategy_params": {...}}
    """
    try:
        raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError("找不到設定檔 cloud_monitor/config.toml") from None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"設定檔格式錯誤：{e}") from None
    warnings = []

    wl = raw.get("watchlist", [])
    if not isinstance(wl, list) or not all(isinstance(x, (str, int)) for x in wl):
        raise ConfigError("watchlist 要寫成清單，例如 watchlist = [\"2330\", \"AAPL\"]")
    watchlist = []
    for x in wl:
        code = str(x).strip().upper()
        try:
            parse_symbol(code)
        except ValueError as e:
            warnings.append(f"自選股「{x}」看不懂，先略過（{e}）")
            continue
        if code not in watchlist:
            watchlist.append(code)

    s = raw.get("settings", {})
    if not isinstance(s, dict):
        raise ConfigError("[settings] 的格式不對")
    settings = {}
    keys = s.get("strategies", ["ma"])
    if isinstance(keys, str):
        keys = [keys]
    good = []
    for k in keys if isinstance(keys, list) else []:
        try:
            good.append(strat.get(str(k)).key)
        except ValueError:
            warnings.append(f"沒有「{k}」這個策略，可用：{', '.join(strat.STRATEGIES)}")
    settings["strategies"] = list(dict.fromkeys(good)) or ["ma"]
    if "auto_trade" in s:
        if not isinstance(s["auto_trade"], bool):
            warnings.append("auto_trade 只能填 true 或 false，先維持關閉")
            settings["auto_trade"] = False
        else:
            settings["auto_trade"] = s["auto_trade"]
    for key, (lo, hi) in NUMBER_SETTINGS.items():
        if key not in s:
            continue
        v = s[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
            warnings.append(f"{key} 要填 {lo:g}～{hi:g} 的數字，先用預設值")
            continue
        settings[key] = int(v) if key.endswith("_ma") else float(v)
    if settings.get("short_ma", DEFAULT_SETTINGS["short_ma"]) >= settings.get("long_ma", DEFAULT_SETTINGS["long_ma"]):
        warnings.append("short_ma 要比 long_ma 小，先用 5 / 20")
        settings["short_ma"], settings["long_ma"] = 5, 20

    limits = {}
    for code, v in (raw.get("limits") or {}).items():
        c = str(code).strip().upper()
        if not isinstance(v, dict):
            warnings.append(f"「{code}」的上下限格式不對，例：\"2330\" = {{ lower = 900, upper = 1200 }}")
            continue
        lo, hi = v.get("lower"), v.get("upper")
        bad = [x for x in (lo, hi) if x is not None and (isinstance(x, bool) or not isinstance(x, (int, float)))]
        if bad or (lo is not None and hi is not None and lo >= hi):
            warnings.append(f"「{code}」的上下限要是數字，而且下限要小於上限")
            continue
        limits[c] = {"lower": lo, "upper": hi}

    params = {}
    for key, p in (raw.get("strategy_params") or {}).items():
        try:
            st = strat.get(str(key))
        except ValueError:
            warnings.append(f"strategy_params 裡沒有「{key}」這個策略")
            continue
        if not isinstance(p, dict):
            warnings.append(f"{key} 的參數格式不對，例：rsi = {{ low = 25, high = 75 }}")
            continue
        unknown = [k for k in p if k not in st.defaults]
        nums = {k: float(v) for k, v in p.items() if k in st.defaults
                and isinstance(v, (int, float)) and not isinstance(v, bool)}
        if unknown:
            warnings.append(f"{st.name} 沒有參數 {', '.join(unknown)}（可用：{', '.join(st.defaults)}）")
        params[st.key] = nums
    n = raw.get("notify") or {}
    notify_cfg = dict(NOTIFY_DEFAULT)
    if not isinstance(n, dict):
        warnings.append("[notify] 的格式不對，先用預設值")
        n = {}
    for key in ("enabled", "trades", "price_alerts"):
        if key in n:
            if isinstance(n[key], bool):
                notify_cfg[key] = n[key]
            else:
                warnings.append(f"notify 的 {key} 只能填 true 或 false")
    if "signals" in n:
        if n["signals"] in ("primary", "all", "none"):
            notify_cfg["signals"] = n["signals"]
        else:
            warnings.append('notify 的 signals 只能填 "primary"、"all" 或 "none"')
    return {"watchlist": watchlist, "limits": limits, "settings": settings,
            "strategy_params": params, "notify": notify_cfg}, warnings


# ---------------------------------------------------------------- 交易時間
def exchange_of(name: str) -> str:
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
    return "US"


def is_active(name: str, now: datetime) -> bool:
    """這檔現在是不是交易時間（now 是台北時間、不帶時區）。"""
    return market_status(exchange_of(name), now.replace(tzinfo=TPE)) == "open"


# ---------------------------------------------------------------- 執行
def prefetch_names(m, now: datetime, log=print) -> None:
    """還沒有股名的台股，向證交所 / 櫃買中心查一次當月資料，從標題取出股名（之後存在 names.json）。
    雲端機器常拿不到即時報價，這樣股名才不會空白。"""
    from common import twse
    cl = twse.client()
    for code in m.watchlist:
        sym = parse_symbol(code)
        if not sym.is_tw_stock or code in m.names:
            continue
        base = sym.candidates[0].split(".")[0]
        try:
            name = cl.cached_name(base)
            if not name:
                if cl.stock_month(base, now.year, now.month).empty and base not in cl.names:
                    cl.tpex_month(base, now.year, now.month)
                name = cl.names.get(base)
        except Exception as e:  # noqa: BLE001 - 查不到股名不影響看盤
            log(f"  ⚠ {code} 股名查詢失敗：{e}")
            continue
        if name:
            m.names[code] = name


def run(state_dir: Path, out_dir: Path, config_path: Path = CONFIG, now: datetime | None = None,
        monitor_kwargs: dict | None = None, log=print, fetch_names: bool = True) -> dict:
    now = now or datetime.now(TPE).replace(tzinfo=None)
    store = Store(state_dir)
    config_errors = []
    try:
        cfg, warnings = load_config(config_path)
        store.save("last_good_config.json", cfg)
    except ConfigError as e:
        config_errors.append(str(e) + "；先沿用上一次正確的設定")
        warnings = []
        cfg = store.load("last_good_config.json", None) or \
            {"watchlist": ["2330"], "limits": {}, "settings": {}, "strategy_params": {}}
    config_errors += warnings

    settings = {**DEFAULT_SETTINGS, "auto_trade": True, **cfg["settings"],
                "strategy_params": cfg["strategy_params"],
                "source": "yahoo", "realtime": True, "demo": False}
    store.save_settings(settings)
    store.save_watchlist({c: cfg["limits"].get(c, {"lower": None, "upper": None})
                          for c in cfg["watchlist"]})

    m = Monitor(store, is_active=is_active, **(monitor_kwargs or {}))
    held_only = [c for c in m.account.positions if c not in m.watchlist]
    for c in held_only:   # 從自選股刪掉但還有持股：繼續追蹤，停損和賣出訊號才會照常運作
        m.watchlist[c] = {"lower": None, "upper": None}
    if fetch_names:
        prefetch_names(m, now, log)
    res = m.refresh(now)

    prices = m.last_prices
    total = m.account.total_assets(prices)
    updated_utc = now.replace(tzinfo=TPE).astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
    equity = store.load("equity.json", [])
    equity.append([updated_utc, round(total, 2)])
    del equity[:-EQUITY_MAX]
    store.save("equity.json", equity)

    notify_cfg = {**NOTIFY_DEFAULT, **cfg.get("notify", {})}
    notify_status = push_notifications(store, m, res, notify_cfg, now, log)

    data = page_data(m, res, now, updated_utc, total, equity, config_errors, held_only)
    data["settings"]["notify"] = {**notify_cfg, "status": notify_status}
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "data.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                                   encoding="utf-8")
    shutil.copyfile(PAGE, out / "index.html")
    log(f"雲端看盤：{len(res.snapshots)}/{len(m.watchlist)} 檔有報價，新提醒 {len(res.alerts)} 則，"
        f"自動成交 {len(res.trades)} 筆，總資產 {total:,.0f}")
    for c, e in res.errors.items():
        log(f"  ⚠ {c}：{e}")
    for e in config_errors:
        log(f"  ⚠ 設定：{e}")
    return data


def push_notifications(store, m, res, cfg: dict, now: datetime, log=print) -> str:
    """把這次新的成交和提醒合併成一則手機通知。回傳顯示在網頁上的狀態。"""
    topic = (os.environ.get("NTFY_TOPIC") or "").strip()
    if not cfg["enabled"]:
        return "關閉"
    if not topic:
        return "還沒設定（GitHub 的 NTFY_TOPIC）"
    status = "已開啟"
    # 第一次看到這個頻道：送一則測試通知，確認手機收得到（只存雜湊，不存頻道名稱）
    fp = notify.topic_fingerprint(topic)
    state = store.load("notify.json", {})
    try:
        if state.get("fingerprint") != fp:
            notify.send({"title": "雲端看盤：手機推播設定成功", "tags": ["white_check_mark"],
                         "message": "之後有自動成交、買賣訊號、突破上下限或停損時會通知你。",
                         "click": notify.MONITOR_URL})
            store.save("notify.json", {"fingerprint": fp})
            log("  已送出推播測試通知")
        trades = res.trades if cfg["trades"] else []
        alerts = notify.pick_alerts(res.alerts, m.settings["strategies"][0], cfg)
        payload = notify.build_message(trades, alerts, m.names, f"{now:%m/%d %H:%M}")
        if payload:
            notify.send(payload)
            log(f"  已推播：{payload['title']}")
    except Exception as e:  # noqa: BLE001 - 推播失敗不影響看盤
        log(f"  ⚠ 推播失敗：{e}")
        status = f"已開啟，但這次推播失敗（{e}）"
    return status


def page_data(m, res, now, updated_utc, total, equity, config_errors, held_only) -> dict:
    a, prices = m.account, m.last_prices
    positions = []
    for code, p in a.positions.items():
        px = prices.get(code, p["avg_cost"])
        positions.append({"code": code, "name": m.names.get(code, ""), "shares": p["shares"],
                          "avg_cost": round(p["avg_cost"], 4), "price": px,
                          "pnl": round((px - p["avg_cost"]) * p["shares"], 2),
                          "pnl_pct": round((px / p["avg_cost"] - 1) * 100, 2)})
    watch = []
    for code, limits in m.watchlist.items():
        row = {"code": code, "name": m.names.get(code, ""), "exchange": exchange_of(code),
               "limits": limits, "held": code in a.positions, "held_only": code in held_only}
        snap = res.snapshots.get(code)
        if snap is None:
            row["error"] = res.errors.get(code, "查不到資料")
        else:
            row.update(price=snap.price, change_pct=round(snap.change_pct, 2),
                       short_ma=round(snap.short_ma, 4), long_ma=round(snap.long_ma, 4),
                       rsi=round(snap.rsi, 1), trend="多" if snap.short_ma > snap.long_ma else "空",
                       live=code in res.live, tw=uses_twse(parse_symbol(code)),
                       signals=[{"strategy": k, "name": strat.get(k).name, "dir": v,
                                 "reason": snap.reasons[k][0 if v == 1 else 1]}
                                for k, v in snap.signals.items() if v])
        watch.append(row)
    s = m.settings
    return {
        "updated": f"{now:%Y-%m-%d %H:%M:%S}", "updated_utc": updated_utc,
        "edit_url": EDIT_URL, "config_errors": config_errors, "notes": res.notes,
        "settings": {"strategies": [{"key": k, "name": strat.get(k).name} for k in s["strategies"]],
                     "auto_trade": s["auto_trade"], "auto_buy_amount": s["auto_buy_amount"],
                     "stop_loss_pct": s["stop_loss_pct"], "short_ma": s["short_ma"],
                     "long_ma": s["long_ma"]},
        "account": {"initial": a.initial, "cash": round(a.cash, 2),
                    "market_value": round(a.market_value(prices), 2), "total": round(total, 2),
                    "return_pct": round(a.return_pct(prices), 3)},
        "positions": positions, "watch": watch,
        "alerts": list(reversed(m.alert_state.get("history", [])[-50:])),
        "new_alerts": len(res.alerts),
        "trades": list(reversed(a.trades[-50:])),
        "equity": equity[-2000:],
        "exchanges": {k: {"name": e.name, "tz": e.tz, "always": e.always,
                          "sessions": [[f"{x:%H:%M}", f"{y:%H:%M}"] for x, y in e.sessions]}
                      for k, e in EXCHANGES.items()},
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="雲端看盤（給 GitHub Actions 執行）")
    ap.add_argument("--state", required=True, help="帳戶等資料存放的資料夾")
    ap.add_argument("--out", required=True, help="網頁輸出資料夾")
    ap.add_argument("--config", default=str(CONFIG))
    args = ap.parse_args(argv)
    run(Path(args.state), Path(args.out), Path(args.config))


if __name__ == "__main__":
    main()
