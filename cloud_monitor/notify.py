"""雲端看盤的手機推播（ntfy）。

手機裝 ntfy App、訂閱一個只有自己知道的頻道名稱，再把同一個名稱設成 GitHub 的
Actions secret「NTFY_TOPIC」。每次雲端看盤有新的成交或提醒，就合併成一則通知送出。

也可以（或同時）推到 Telegram 機器人：在 GitHub 設 TELEGRAM_TOKEN 和 TELEGRAM_CHAT_ID。

環境變數：
    NTFY_TOPIC    頻道名稱（沒設就不推 ntfy）
    TELEGRAM_TOKEN    BotFather 給的機器人 Token（設了就推 Telegram）
    TELEGRAM_CHAT_ID  自己的 Chat ID（可以不設：雲端會從「誰傳過訊息給機器人」自動找到，存在資料分支）
    NTFY_SERVER   伺服器，預設 https://ntfy.sh
    NTFY_TOKEN    存取權杖（有用 ntfy 帳號保護頻道時才需要）
"""
from __future__ import annotations

import hashlib
import os

MONITOR_URL = "https://sdfvgv1994-bot.github.io/taco0525/monitor/"
MAX_BODY = 3500   # ntfy 單則訊息上限約 4096 位元組
KIND_ICON = {"buy": "🟢", "sell": "🔴", "above": "⬆️", "below": "⬇️", "stop": "🛑",
             "trail": "📉", "support": "🔻"}


def pick_alerts(alerts: list, primary: str, notify: dict) -> list:
    """依設定挑出要推播的提醒。alerts 是 stock_monitor.signals.Alert 物件。"""
    signals = notify.get("signals", "primary")
    out = []
    for a in alerts:
        if a.kind in ("buy", "sell"):
            if signals == "all" or (signals == "primary" and a.strategy == primary):
                out.append(a)
        elif notify.get("price_alerts", True):
            out.append(a)
    return out


def build_message(trades: list, alerts: list, names: dict, when: str, dividends: list | None = None) -> dict | None:
    """回傳 ntfy 的 JSON 內容（不含 topic）；沒有東西要通知時回傳 None。
    dividends 是配息配股的訊息（cloud_monitor.dividends 已經排好的文字）。"""
    dividends = dividends or []
    if not trades and not alerts and not dividends:
        return None
    lines = []
    for t in trades:
        name = names.get(t["code"], "")
        extra = f"，損益 {t['pnl']:+,.0f}" if "pnl" in t else ""
        lines.append(f"💰 {t['side']} {t['code']} {name} {t['shares']:,} 股 @ {t['price']:g}"
                     f"（{t['reason']}）{extra}".replace("  ", " "))
    lines += dividends
    for a in alerts:
        lines.append(f"{KIND_ICON.get(a.kind, '•')} {a.message}")
    parts = []
    if trades:
        parts.append(f"{len(trades)} 筆成交")
    if dividends:
        parts.append("配息配股")
    if alerts:
        parts.append(f"{len(alerts)} 則提醒")
    body = "\n".join(lines)
    if len(body.encode("utf-8")) > MAX_BODY:
        body = body.encode("utf-8")[:MAX_BODY].decode("utf-8", "ignore") + "\n…（其他請看網頁）"
    urgent = bool(trades) or bool(dividends) or any(a.kind == "stop" for a in alerts)
    return {"title": f"雲端看盤：{'、'.join(parts)}（{when}）", "message": body,
            "priority": 4 if urgent else 3, "tags": ["chart_with_upwards_trend"],
            "click": MONITOR_URL}


def topic_fingerprint(topic: str) -> str:
    """只存雜湊（資料分支是公開的，不能存頻道名稱本身），用來判斷是不是新設定的頻道。"""
    return hashlib.sha256(topic.encode("utf-8")).hexdigest()[:16]


def send(payload: dict, post=None, env=None) -> str:
    """送出通知；回傳結果說明。沒設定頻道時不送。"""
    env = os.environ if env is None else env
    topic = (env.get("NTFY_TOPIC") or "").strip()
    if not topic:
        return "沒有設定 NTFY_TOPIC，不推播"
    server = (env.get("NTFY_SERVER") or "https://ntfy.sh").rstrip("/")
    headers = {}
    if env.get("NTFY_TOKEN"):
        headers["Authorization"] = f"Bearer {env['NTFY_TOKEN']}"
    if post is None:
        import requests
        post = requests.post
    # 用 JSON 送，標題和內容的中文才不會被 HTTP 標頭的編碼限制卡住
    r = post(server + "/", json={"topic": topic, **payload}, headers=headers, timeout=15)
    code = getattr(r, "status_code", 0)
    if code != 200:
        raise RuntimeError(f"ntfy 回應 HTTP {code}：{getattr(r, 'text', '')[:200]}")
    return "已推播"


TG_MAX = 4000   # Telegram 單則上限 4096 字


def telegram_ready(env=None) -> bool:
    env = os.environ if env is None else env
    return bool((env.get("TELEGRAM_TOKEN") or "").strip())


def telegram_find_chat(env=None, get=None) -> str | None:
    """問 Telegram「最近誰傳過私訊給這個機器人」，回傳最新那個人的 Chat ID；沒有人傳過 → None。
    （Telegram 只留最近 24 小時、還沒被讀走的訊息，所以找到後要存起來。）"""
    env = os.environ if env is None else env
    token = (env.get("TELEGRAM_TOKEN") or "").strip()
    if not token:
        return None
    if get is None:
        import requests
        get = requests.get
    base = f"https://api.telegram.org/bot{token}"
    r = get(base + "/getUpdates", timeout=15)
    if getattr(r, "status_code", 0) == 409:
        # 機器人被設了 webhook（例如把 Token 給過別的機器人），getUpdates 會被擋：清掉再試一次
        get(base + "/deleteWebhook", timeout=15)
        r = get(base + "/getUpdates", timeout=15)
    code = getattr(r, "status_code", 0)
    if code != 200:
        why = {401: "Token 錯了或已經作廢", 404: "Token 格式不對", 409: "機器人被設了 webhook，清不掉"}.get(code, "")
        raise RuntimeError(f"Telegram 回應 HTTP {code}" + (f"（{why}）" if why else ""))
    chats = [u.get("message", {}).get("chat", {}) for u in (r.json().get("result") or [])]
    chats = [c for c in chats if c.get("type") == "private" and c.get("id") is not None]
    return str(chats[-1]["id"]) if chats else None


def telegram_send(payload: dict, post=None, env=None, chat: str | None = None) -> str:
    """把和 ntfy 一樣的通知內容（title、message、click）送到 Telegram 機器人。"""
    env = os.environ if env is None else env
    token = (env.get("TELEGRAM_TOKEN") or "").strip()
    chat = (chat or env.get("TELEGRAM_CHAT_ID") or "").strip()
    if not (token and chat):
        return "沒有設定 TELEGRAM_TOKEN / TELEGRAM_CHAT_ID，不推播"
    text = payload.get("title", "")
    if payload.get("message"):
        text += "\n" + payload["message"]
    if payload.get("click"):
        text += "\n" + payload["click"]
    if len(text) > TG_MAX:
        text = text[:TG_MAX] + "\n…（其他請看網頁）"
    if post is None:
        import requests
        post = requests.post
    r = post(f"https://api.telegram.org/bot{token}/sendMessage",
             json={"chat_id": chat, "text": text, "disable_web_page_preview": True}, timeout=15)
    code = getattr(r, "status_code", 0)
    if code != 200:
        # 不把網址印出來（裡面有 Token）
        raise RuntimeError(f"Telegram 回應 HTTP {code}：{getattr(r, 'text', '')[:200]}")
    return "已推播"
