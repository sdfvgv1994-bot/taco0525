"""手機版的 API：網址 → 看盤服務（LiveMonitor）的操作。web_app.py 拆出來的積木。

web_app.py 只管 HTTP（密碼 key、回應格式、網頁檔案）；這裡決定每個網址要做什麼，
回傳 (HTTP 狀態碼, JSON 物件)，不碰 socket，比較好測試。

    get(engine, path, query, ...)   /api/state、/api/news、/api/chart、/api/fundamental、/api/chips
    post(engine, path, body, ...)   /api/add、/api/order、/api/settings …、/api/cloud、/api/news/send、/api/notify*
"""
from __future__ import annotations

from stock_monitor import market_data as D   # 個股 K 線 / 基本面 / 籌碼
from stock_monitor import news as NEWS       # 財經新聞
from stock_monitor import notify as N        # LINE / Telegram / Discord

NOT_FOUND = (404, {"error": "not found"})


# ====================== GET ======================
def get(engine, path: str, query: dict, key: str, is_local: bool, urls) -> tuple[int, dict]:
    """urls：key → 手機要用的網址（web_app.access_urls，查網路介面比較慢，只在 /api/state 用）。"""
    if path == "/api/state":
        with engine.lock:
            state = engine.state()
        state["urls"] = urls(key)
        state["is_local"] = is_local
        state["last_digest"] = engine.last_digest_date()
        return 200, state
    if path in ("/api/chart", "/api/fundamental", "/api/chips"):
        return stock_detail(engine, path, query)
    if path == "/api/news":
        force = query.get("force", [""])[0] == "1"
        data = NEWS.get_news(force=force)        # 網路請求不鎖引擎
        with engine.lock:
            watch = engine.watch_names()
            last = engine.last_digest_date()
        items = NEWS.tag_watchlist(data["items"][:400], watch)
        return 200, {"items": items, "status": data["status"], "updated": data["updated"],
                     "names": dict(watch), "last_digest": last}
    return NOT_FOUND


def stock_detail(engine, path: str, query: dict) -> tuple[int, dict]:
    """個股 K 線 / 基本面 / 籌碼。抓資料時不鎖住引擎，其他操作照常。"""
    ticker = query.get("ticker", [""])[0].strip().upper()
    if not ticker:
        return 400, {"error": "缺少 ticker"}
    with engine.lock:
        symbol = engine.symbol_of(ticker)
        stock = engine.find(ticker)
        trendlines = list(stock.get("trendlines", [])) if stock else []
        hlines = list(stock.get("support", [])) if stock else []
        name = engine.m.names.get(ticker, "")
        source, demo = engine.m.settings.get("source", "auto"), engine.m.settings.get("demo", False)
    try:
        if path == "/api/chart":
            period = query.get("period", ["1y"])[0]
            if period not in ("3mo", "6mo", "1y", "2y", "5y"):
                period = "1y"
            data = dict(D.get_chart(symbol, period, source=source, demo=demo),
                        trendlines=trendlines, hlines=hlines)
        elif path == "/api/fundamental":
            data = D.get_fundamental(symbol)
        else:
            data = D.get_chips(symbol)
    except ValueError as exc:
        return 400, {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - 資料源偶爾出錯，不要讓伺服器掛掉
        return 502, {"error": "資料暫時抓不到（%s）" % exc.__class__.__name__}
    return 200, dict(data, ticker=ticker, name=name)


# ====================== POST ======================
def post(engine, path: str, body: dict, is_local: bool) -> tuple[int, dict]:
    if path.startswith("/api/notify"):
        return notify_action(engine, path, body, is_local)
    if path == "/api/cloud":   # 雲端帳戶：GitHub 權杖只能在電腦本機設定
        if not is_local:
            return 403, {"error": "為了安全，GitHub 權杖只能在電腦上的瀏覽器設定"}
        try:
            with engine.lock:
                engine.set_cloud(token=body.get("token") or None, enabled=body.get("enabled"),
                                 clear=bool(body.get("clear")))
        except ValueError as exc:
            return 400, {"error": str(exc)}
        engine.wake.set()
        return 200, {"ok": True}
    if path == "/api/news/send":   # 立即推送一次每日新聞（測試或補送）
        with engine.lock:
            ready = N.any_ready(engine.notify_cfg)
        if not ready:
            return 400, {"error": "還沒有開啟任何通知管道"}
        errors = engine.send_digest()
        if errors:
            return 400, {"error": "%s 失敗：%s" % errors[0]}
        return 200, {"ok": True}
    actions = {
        "/api/refresh": lambda: engine.wake.set(),
        "/api/add": lambda: engine.add(body.get("ticker", "")),
        "/api/remove": lambda: engine.remove(body.get("ticker")),
        "/api/alerts": lambda: engine.set_alerts(body.get("ticker"), body),
        "/api/order": lambda: engine.order(body.get("ticker"), body.get("side"), body.get("shares")),
        "/api/strategies": lambda: engine.update_settings({"strategies": body.get("strategies", [])}),
        "/api/settings": lambda: engine.update_settings(body),
        "/api/reset": lambda: engine.reset_account(),
        "/api/trendlines": lambda: engine.set_trendlines(body.get("ticker"), body.get("lines")),
        "/api/hlines": lambda: engine.set_hlines(body.get("ticker"), body.get("prices")),
    }
    if path not in actions:
        return NOT_FOUND
    try:
        with engine.lock:
            actions[path]()
    except ValueError as exc:
        return 400, {"error": str(exc)}
    return 200, {"ok": True}


def notify_action(engine, path: str, body: dict, is_local: bool) -> tuple[int, dict]:
    """通知設定。改設定、用 Token 查 Chat ID 只能在電腦本機做；測試傳送手機也可以按。"""
    if path in ("/api/notify", "/api/notify/telegram_chats") and not is_local:
        return 403, {"error": "為了安全，通知的 Token 只能在電腦上的瀏覽器設定"}
    try:
        if path == "/api/notify":
            with engine.lock:
                engine.update_notify(body)
            return 200, {"ok": True}
        if path == "/api/notify/test":
            with engine.lock:
                cfg = engine.notify_test_config(body.get("channel"))
            name = N.CHANNELS[body["channel"]]
            errors = N.send_all(cfg, "✅ 自動看盤測試訊息：%s 通知設定成功！" % name)   # 網路請求不鎖引擎
            if errors:
                return 400, {"error": "%s 失敗：%s" % errors[0]}
            return 200, {"ok": True}
        if path == "/api/notify/telegram_chats":
            token = str(body.get("token") or "").strip()
            if not token:
                with engine.lock:
                    token = engine.notify_cfg["telegram"]["token"]
            if not token:
                raise ValueError("請先填 Bot Token")
            chats, error = N.telegram_find_chats(token)
            if error:
                raise ValueError(error)
            return 200, {"chats": chats}
    except ValueError as exc:
        return 400, {"error": str(exc)}
    return NOT_FOUND
