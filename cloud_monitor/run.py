"""雲端看盤：GitHub Actions 每 15 分鐘執行一次（和全球看板同一個排程）。

    python cloud_monitor/run.py --state _state --out _site/monitor

- 設定來自 cloud_monitor/config.toml（在 GitHub 網頁就能改）
- 帳戶、提醒紀錄、股名、資產走勢存在 --state 資料夾（雲端是 monitor-data 分支），每次執行後存回去
- 只在該檔所屬市場的交易時間內、而且已經有今天的 K 棒時才判斷訊號、自動交易；
  休市時（含國定假日）只更新報價顯示
- 歷史資料用 Yahoo（有今天的延遲價格）；台股另外試證交所即時報價
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


from cloud_monitor import dividends, notify  # noqa: E402
from cloud_monitor.exchange import SUFFIX_EXCHANGE, exchange_of  # noqa: E402,F401
from cloud_monitor.config import (  # noqa: E402,F401  設定檔積木；舊名字照樣能用
    HISTORY_BARS, NOTIFY_DEFAULT, ConfigError, load_config,
)
from cloud_monitor.keep_last import copy_page  # noqa: E402
from common import strategies as strat  # noqa: E402
from common.market import parse_symbol, uses_twse  # noqa: E402
from stock_monitor.engine import Monitor  # noqa: E402
from stock_monitor.storage import DEFAULT_SETTINGS, Store  # noqa: E402
from world_market.markets import EXCHANGES, market_status  # noqa: E402

TPE = ZoneInfo("Asia/Taipei")
HERE = Path(__file__).resolve().parent
CONFIG = HERE / "config.toml"
PAGE = HERE / "index.html"
EDIT_URL = "https://github.com/sdfvgv1994-bot/taco0525/edit/main/cloud_monitor/config.toml"
EQUITY_MAX = 5000


# ---------------------------------------------------------------- 交易時間
def is_active(name: str, now: datetime) -> bool:
    """這檔現在是不是交易時間（now 是台北時間、不帶時區）。"""
    ex = exchange_of(name)
    return ex is not None and market_status(ex, now.replace(tzinfo=TPE)) == "open"


def session_date(name: str, now: datetime) -> str | None:
    """這檔所屬市場「今天」的日期（當地時間）。最後一根 K 棒比這個舊，就代表休市或資料還沒更新。"""
    ex = exchange_of(name)
    if ex is None:
        return None
    return now.replace(tzinfo=TPE).astimezone(ZoneInfo(EXCHANGES[ex].tz)).strftime("%Y-%m-%d")


# ---------------------------------------------------------------- 手動下單
ORDER_KEEP = 50          # data.json 裡保留最近幾筆下單結果
ORDER_EXPIRE_DAYS = 5    # 這麼多天都沒成交（例如一直休市）就取消


def load_orders(path: Path) -> list:
    """網頁「手動下單」寫進 cloud_monitor/orders.json 的訂單。格式不對的略過。"""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [o for o in (raw if isinstance(raw, list) else []) if isinstance(o, dict) and o.get("id")]


def process_orders(m, res, store, orders: list, now: datetime, log=print) -> list:
    """執行還沒處理過的手動訂單：只在該市場交易時間內、用這次抓到的價格成交。
    處理結果存在 monitor-data 的 orders.json（同一張訂單只會成交一次）。回傳最近的結果給網頁。"""
    from stock_monitor.account import TradeError

    done = store.load("orders.json", [])
    seen = {d["id"] for d in done}
    pending = []
    for o in orders:
        oid = str(o["id"])
        if oid in seen:
            continue
        code = str(o.get("code", "")).strip().upper()
        side = o.get("side")
        rec = {"id": oid, "time": str(o.get("time", ""))[:19], "code": code, "side": side,
               "shares": o.get("shares")}
        try:
            placed = datetime.fromisoformat(rec["time"]) if rec["time"] else now
        except ValueError:
            placed = now
        try:
            shares = None if side == "sell" and o.get("shares") in (None, "", "all") else int(o.get("shares"))
            if side not in ("buy", "sell"):
                raise ValueError("買賣方向不對")
            if shares is not None and shares <= 0:
                raise ValueError("股數要大於 0")
            if code not in m.watchlist:
                raise ValueError("不在自選股裡，請先加入自選股")
            if code not in res.snapshots:
                raise LookupError(res.errors.get(code) or "這次沒有抓到報價")
            if code in res.inactive:
                raise LookupError("不在交易時間，等開盤後的第一次更新成交")
            if side == "buy":
                t = m.manual_buy(code, shares, reason="手動（雲端網頁）")
            else:
                t = m.manual_sell(code, shares, reason="手動（雲端網頁）")
        except LookupError as e:   # 還不能成交：留著下次再試
            if (now - placed).days >= ORDER_EXPIRE_DAYS:
                rec.update(status="expired", message=f"{ORDER_EXPIRE_DAYS} 天都沒成交，已取消（{e.args[0]}）")
            else:
                rec.update(status="pending", message=e.args[0])
                pending.append(rec)
                continue
        except (ValueError, TypeError, TradeError) as e:
            rec.update(status="rejected", message=str(e.args[0] if e.args else e))
        else:
            rec.update(status="done", price=t["price"], shares=t["shares"], fee=t["fee"], tax=t["tax"],
                       message=f"{t['side']} {t['shares']:,} 股 @ {t['price']:,.2f}"
                               + (f"，損益 {t['pnl']:+,.0f}" if "pnl" in t else ""))
            res.trades.append(t)
        rec["handled"] = f"{now:%Y-%m-%d %H:%M}"
        done.append(rec)
        log(f"手動訂單 {code} {side}：{rec['status']} {rec.get('message', '')}")
    store.save("orders.json", done[-500:])
    return list(reversed(pending)) + list(reversed(done[-ORDER_KEEP:]))


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


def finite(x):
    """NaN / 無限大換成 None：瀏覽器的 JSON 不接受 NaN，一個 NaN 就會讓整頁打不開。"""
    if isinstance(x, float) and not math.isfinite(x):
        return None
    if isinstance(x, dict):
        return {k: finite(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [finite(v) for v in x]
    return x


def run(state_dir: Path, out_dir: Path, config_path: Path = CONFIG, now: datetime | None = None,
        monitor_kwargs: dict | None = None, log=print, fetch_names: bool = True,
        orders_path: Path | None = None, dividend_source=None) -> dict:
    now = now or datetime.now(TPE).replace(tzinfo=None)
    store = Store(state_dir)
    config_errors = []
    try:
        cfg, warnings = load_config(config_path)
        store.save("last_good_config.json", cfg)
    except ConfigError as e:
        warnings = []
        last = store.load("last_good_config.json", None)
        if last:
            config_errors.append(f"{e}；先沿用上一次正確的設定")
            cfg = last
        else:
            config_errors.append(f"{e}；還沒有上一次正確的設定可以沿用，這次先只更新報價、不自動交易")
            cfg = {"watchlist": list(store.watchlist()), "limits": {},
                   "settings": {"auto_trade": False}, "strategy_params": {}}
    config_errors += warnings

    settings = {**DEFAULT_SETTINGS, "auto_trade": True, **cfg["settings"],
                "strategy_params": cfg["strategy_params"],
                "source": "yahoo", "realtime": True, "demo": False}
    store.save_settings(settings)
    store.save_watchlist({c: cfg["limits"].get(c, {"lower": None, "upper": None})
                          for c in cfg["watchlist"]})

    m = Monitor(store, is_active=is_active, session_date=session_date, **(monitor_kwargs or {}))
    held_only = [c for c in m.account.positions if c not in m.watchlist]
    for c in held_only:   # 從自選股刪掉但還有持股：繼續追蹤，停損和賣出訊號才會照常運作
        m.watchlist[c] = {"lower": None, "upper": None}
    if fetch_names:
        prefetch_names(m, now, log)
    res = m.refresh(now)
    orders = process_orders(m, res, store, load_orders(orders_path or Path(config_path).parent / "orders.json"),
                            now, log)
    try:     # 配息配股：除權息日照持股入帳（出錯不影響看盤）
        div = dividends.process(m.account, store, m.names, now, source=dividend_source, log=log)
        m.save()
    except Exception as e:  # noqa: BLE001
        log(f"  ⚠ 配息配股處理失敗：{e}")
        div = {"messages": [], "receivable": 0.0, "pending": [], "notes": [f"配息配股這次處理失敗：{e}"]}
    res.notes += div["notes"]

    # 這次抓不到報價的持股用上一次的價格計算（不然會被當成成本價，資產走勢出現假的跳動）
    stamp = f"{now:%Y-%m-%d %H:%M}"
    known = store.load("prices.json", {})
    known.update({c: {"price": p, "time": stamp} for c, p in m.last_prices.items()})
    known = {c: v for c, v in known.items() if c in m.watchlist}
    store.save("prices.json", known)
    prices = {c: v["price"] for c, v in known.items()}
    missing = [c for c in m.account.positions if c not in m.last_prices]
    total = m.account.total_assets(prices) + div["receivable"]   # 應收股利也算資產（除息當天股價會掉）
    updated_utc = now.replace(tzinfo=TPE).astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
    equity = store.load("equity.json", [])
    if missing:
        res.notes.append(f"持股 {'、'.join(missing)} 這次抓不到報價，先用上一次抓到的價格計算，"
                         f"資產走勢這次不記錄")
    else:
        equity.append([updated_utc, round(total, 2)])
        del equity[:-EQUITY_MAX]
        store.save("equity.json", equity)

    notify_cfg = {**NOTIFY_DEFAULT, **cfg.get("notify", {})}
    notify_status = push_notifications(store, m, res, notify_cfg, now, log, extra=div["messages"])

    data = page_data(m, res, now, updated_utc, total, equity, config_errors, held_only,
                     prices, {c: v["time"] for c, v in known.items()})
    data["account"].update(receivable=div["receivable"],
                           return_pct=round((total / m.account.initial - 1) * 100, 3))
    data["dividends"] = [{**p, "name": m.names.get(p["code"], "")} for p in div["pending"]]
    data["settings"]["notify"] = {**notify_cfg, "status": notify_status}
    data["orders"] = orders
    data = finite(data)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "data.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":"),
                                              allow_nan=False), encoding="utf-8")
    copy_page(out)
    log(f"雲端看盤：{len(res.snapshots)}/{len(m.watchlist)} 檔有報價，新提醒 {len(res.alerts)} 則，"
        f"自動成交 {len(res.trades)} 筆，總資產 {total:,.0f}")
    for c, e in res.errors.items():
        log(f"  ⚠ {c}：{e}")
    for e in config_errors:
        log(f"  ⚠ 設定：{e}")
    for n in res.notes:
        log(f"  ・{n}")
    return data


def push_notifications(store, m, res, cfg: dict, now: datetime, log=print, extra: list | None = None) -> str:
    """把這次新的成交和提醒合併成一則手機通知，送到有設定的管道（ntfy、Telegram）。回傳顯示在網頁上的狀態。"""
    topic = (os.environ.get("NTFY_TOPIC") or "").strip()
    tg = notify.telegram_ready()
    if not cfg["enabled"]:
        return "關閉"
    if not topic and not tg:
        return "還沒設定（GitHub 的 NTFY_TOPIC 或 TELEGRAM_TOKEN）"
    channels, waiting = [], []
    state = store.load("notify.json", {})
    if topic:
        channels.append(("ntfy", "fingerprint", notify.topic_fingerprint(topic), notify.send))
    if tg:
        # Chat ID：GitHub 有設就用；沒設就用上次自動找到的；再沒有就問 Telegram 誰傳過訊息給機器人
        chat = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip() or state.get("telegram_chat")
        if not chat:
            try:
                chat = notify.telegram_find_chat()
            except Exception as e:  # noqa: BLE001
                log(f"  ⚠ 找 Telegram Chat ID 失敗：{e}")
            if chat:
                state["telegram_chat"] = chat          # 公開的資料分支：只有 Chat ID，沒有 Token
                store.save("notify.json", state)
                log("  已自動找到 Telegram Chat ID")
        if chat:
            tg_fp = notify.topic_fingerprint(os.environ.get("TELEGRAM_TOKEN", "").strip() + "|" + chat)
            channels.append(("Telegram", "telegram", tg_fp,
                             lambda payload, _c=chat: notify.telegram_send(payload, chat=_c)))
        else:
            waiting.append("Telegram：請先在機器人對話框傳一句話（例如 hi），下次更新就會接上")
    trades = res.trades if cfg["trades"] else []
    alerts = notify.pick_alerts(res.alerts, m.settings["strategies"][0], cfg)
    payload = notify.build_message(trades, alerts, m.names, f"{now:%m/%d %H:%M}",
                                   dividends=extra if cfg["trades"] else [])
    failed = []
    for name, key, fp, send in channels:
        try:
            # 第一次看到這個頻道／機器人：送一則測試通知，確認手機收得到（只存雜湊，不存名稱或 Token）
            if state.get(key) != fp:
                send({"title": "雲端看盤：手機推播設定成功", "tags": ["white_check_mark"],
                      "message": "之後有自動成交、買賣訊號、突破上下限、停損或配息時會通知你。",
                      "click": notify.MONITOR_URL})
                state[key] = fp
                store.save("notify.json", state)
                log(f"  已送出 {name} 測試通知")
            if payload:
                send(payload)
                log(f"  已推播（{name}）：{payload['title']}")
        except Exception as e:  # noqa: BLE001 - 推播失敗不影響看盤
            log(f"  ⚠ {name} 推播失敗：{e}")
            failed.append(f"{name}（{e}）")
    names = "、".join(n for n, *_ in channels)
    status = (f"已開啟（{names}）" if channels else "等待設定") + (f"，但這次推播失敗：{'；'.join(failed)}" if failed else "")
    return status + ("；" + "；".join(waiting) if waiting else "")


def page_data(m, res, now, updated_utc, total, equity, config_errors, held_only,
              prices: dict, price_time: dict) -> dict:
    a = m.account
    positions = []
    for code, p in a.positions.items():
        px = prices.get(code, p["avg_cost"])
        positions.append({"code": code, "name": m.names.get(code, ""), "shares": p["shares"],
                          "avg_cost": round(p["avg_cost"], 4), "price": px, "peak": p.get("peak"),
                          "pnl": round((px - p["avg_cost"]) * p["shares"], 2),
                          "pnl_pct": round((px / p["avg_cost"] - 1) * 100, 2),
                          # 這次抓不到報價時，價格是哪個時間抓到的
                          "price_time": None if code in m.last_prices else price_time.get(code)})
    watch = []
    for code, limits in m.watchlist.items():
        row = {"code": code, "name": m.names.get(code, ""), "exchange": exchange_of(code),
               "limits": limits, "held": code in a.positions, "held_only": code in held_only}
        snap = res.snapshots.get(code)
        if snap is None:
            row["error"] = res.errors.get(code, "查不到資料")
        else:
            trend = None
            if math.isfinite(snap.short_ma) and math.isfinite(snap.long_ma):
                trend = "多" if snap.short_ma > snap.long_ma else "空"
            row.update(price=snap.price, change_pct=round(snap.change_pct, 2),
                       short_ma=round(snap.short_ma, 4), long_ma=round(snap.long_ma, 4),
                       rsi=round(snap.rsi, 1), trend=trend, bar_date=snap.date,
                       stale=code in res.stale,
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
                     "stop_loss_pct": s["stop_loss_pct"], "trail_stop_pct": s.get("trail_stop_pct", 0),
                     "short_ma": s["short_ma"], "strategy_params": s.get("strategy_params", {}),
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
