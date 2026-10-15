"""
通知積木：LINE、Telegram、Discord。桌面版 desktop_app.py 與手機版 web_app.py 共用。
（雲端看盤用的是 ntfy，在 cloud_monitor/notify.py）

設定存在 data/notify_config.json（含 Token，不要分享，也不要手動編輯；不在 git 裡）。
"""

import json
import re

import requests

CONFIG_NAME = "notify_config.json"

LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"
TELEGRAM_API = "https://api.telegram.org"
DISCORD_PREFIXES = ("https://discord.com/api/webhooks/", "https://discordapp.com/api/webhooks/",
                    "https://ptb.discord.com/api/webhooks/", "https://canary.discord.com/api/webhooks/")
MAX_TEXT = {"line": 5000, "telegram": 4096, "discord": 2000}   # 各家單則訊息字數上限

CHANNELS = {"line": "LINE", "telegram": "Telegram", "discord": "Discord"}
SECRET_FIELDS = {"line": ("token", "user_id"), "telegram": ("token", "chat_id"), "discord": ("webhook",)}
DEFAULTS = {
    "alerts": True, "trades": True,   # 要通知哪些事：提醒、模擬成交
    "line": {"enabled": False, "token": "", "user_id": ""},
    "telegram": {"enabled": False, "token": "", "chat_id": ""},
    "discord": {"enabled": False, "webhook": ""},
}


# ====================== 設定檔 ======================

def fresh_defaults():
    return json.loads(json.dumps(DEFAULTS))


def merge(data):
    cfg = fresh_defaults()
    cfg["alerts"] = bool(data.get("alerts", True))
    cfg["trades"] = bool(data.get("trades", True))
    for name in CHANNELS:
        if isinstance(data.get(name), dict):
            for key in cfg[name]:
                if key in data[name]:
                    cfg[name][key] = data[name][key]
    return cfg


def load_config(path):
    """回傳 (設定, 問題說明)。path 是 notify_config.json 的位置。內容壞掉就用預設值（全部關閉）並回傳說明。"""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError
        return merge(data), None
    except FileNotFoundError:
        return fresh_defaults(), None
    except (OSError, ValueError):
        return fresh_defaults(), ("notify_config.json 內容格式不對，通知暫時關閉。"
                                  "請不要直接編輯這個檔案，改用「通知設定」填寫後儲存")


def save_config(cfg, path):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def ready(cfg, name):
    """這個管道有開，而且必要欄位都有填。"""
    ch = cfg[name]
    return bool(ch.get("enabled")) and all(ch.get(k) for k in SECRET_FIELDS[name])


def any_ready(cfg):
    return any(ready(cfg, n) for n in CHANNELS)


def public_status(cfg):
    """給畫面顯示用，不含任何 Token / 網址。"""
    out = {"alerts": cfg["alerts"], "trades": cfg["trades"]}
    for name in CHANNELS:
        ch = cfg[name]
        out[name] = {"enabled": bool(ch.get("enabled")),
                     "configured": all(ch.get(k) for k in SECRET_FIELDS[name])}
        if name == "telegram":
            out[name]["chat_id"] = ch.get("chat_id", "")   # Chat ID 不是密碼，顯示出來方便確認
        if name == "line":
            out[name]["user_id"] = ch.get("user_id", "")
    return out


def check(name, ch):
    """檢查設定有沒有常見錯誤，回傳中文說明；沒問題回傳 None。"""
    if name == "line":
        if not ch.get("token") or not ch.get("user_id"):
            return "請填入 LINE 的 Token 和 User ID"
        if not ch["user_id"].startswith("U"):
            return "LINE User ID 應該是 U 開頭（在 Basic settings 頁最下面）"
        if len(ch["token"]) < 100:
            return ("LINE Token 看起來是 Channel secret（%d 個字），不是 access token。請到 Messaging API 頁"
                    "最下面的 Channel access token 按 Issue，複製那串很長的字" % len(ch["token"]))
    elif name == "telegram":
        if not ch.get("token") or not ch.get("chat_id"):
            return "請填入 Telegram 的 Bot Token 和 Chat ID"
        if not re.fullmatch(r"\d+:[\w-]{30,}", ch["token"]):
            return "Telegram Bot Token 格式不對，應該像 123456789:ABCdef…（從 @BotFather 複製）"
        if not re.fullmatch(r"-?\d+|@\w+", str(ch["chat_id"])):
            return "Telegram Chat ID 應該是一串數字（可以按「自動找 Chat ID」）"
    elif name == "discord":
        if not ch.get("webhook"):
            return "請填入 Discord Webhook 網址"
        if not ch["webhook"].startswith(DISCORD_PREFIXES):
            return "Discord Webhook 網址應該是 https://discord.com/api/webhooks/… 開頭"
    return None


# ====================== 送出 ======================

def split_text(text, limit):
    """太長的訊息在換行處切成好幾段（每段不超過 limit），不要直接截斷。"""
    chunks, current = [], ""
    for line in text.split("\n"):
        while len(line) > limit:                 # 單一行就超過上限（很少見）才硬切
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = line if not current else current + "\n" + line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks or [""]


def shorten(text, name):
    limit = MAX_TEXT[name]
    return text if len(text) <= limit else text[:limit - 20] + "\n…（訊息太長已截斷）"


def line_push(token, user_id, text):
    """成功回傳 None，失敗回傳錯誤說明。長訊息切段後放在同一次推播（最多 5 段，只算 1 則額度）。"""
    parts = split_text(text, MAX_TEXT["line"])
    if len(parts) > 5:
        parts = parts[:4] + [shorten("\n".join(parts[4:]), "line")]
    try:
        r = requests.post(LINE_PUSH_URL, timeout=10, headers={"Authorization": "Bearer " + token},
                          json={"to": user_id, "messages": [{"type": "text", "text": p} for p in parts]})
    except requests.RequestException as exc:
        return "連線失敗：%s" % exc.__class__.__name__
    if r.status_code == 200:
        return None
    try:
        detail = r.json().get("message", "")
    except ValueError:
        detail = r.text[:100]
    hints = {401: "Token 錯誤或過期", 400: "User ID 錯誤，或還沒加官方帳號好友",
             403: "沒有權限", 429: "超過每月免費則數或傳太快"}
    return "HTTP %d %s（%s）" % (r.status_code, hints.get(r.status_code, ""), detail)


def telegram_push(token, chat_id, text):
    for part in split_text(text, MAX_TEXT["telegram"]):   # 太長就分成好幾則依序送出
        error = telegram_send_one(token, chat_id, part)
        if error:
            return error
    return None


def telegram_send_one(token, chat_id, text):
    try:
        r = requests.post("%s/bot%s/sendMessage" % (TELEGRAM_API, token), timeout=10,
                          json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True})
    except requests.RequestException as exc:
        return "連線失敗：%s" % exc.__class__.__name__
    if r.status_code == 200:
        return None
    try:
        detail = r.json().get("description", "")
    except ValueError:
        detail = r.text[:100]
    hints = {401: "Bot Token 錯誤", 400: "Chat ID 錯誤，或還沒對機器人按「開始」",
             403: "你封鎖了這個機器人", 404: "Bot Token 錯誤", 429: "傳太快了"}
    return "HTTP %d %s（%s）" % (r.status_code, hints.get(r.status_code, ""), detail)


def telegram_find_chats(token):
    """找出最近傳訊息給機器人的聊天室。回傳 ([{"id", "name"}], 錯誤說明)。"""
    try:
        r = requests.get("%s/bot%s/getUpdates" % (TELEGRAM_API, token), timeout=10)
        data = r.json()
    except (requests.RequestException, ValueError) as exc:
        return [], "連線失敗：%s" % exc.__class__.__name__
    if not data.get("ok"):
        return [], "Bot Token 錯誤（%s）" % data.get("description", r.status_code)
    chats = {}
    for upd in data.get("result", []):
        msg = upd.get("message") or upd.get("channel_post") or upd.get("my_chat_member") or {}
        chat = msg.get("chat") or {}
        if "id" in chat:
            name = chat.get("title") or " ".join(filter(None, [chat.get("first_name"), chat.get("last_name")]))
            chats[chat["id"]] = name or chat.get("username") or str(chat["id"])
    if not chats:
        return [], "找不到聊天室：請先在 Telegram 打開你的機器人，按「開始」或隨便傳一句話，再按一次"
    return [{"id": str(k), "name": v} for k, v in chats.items()], None


def discord_push(webhook, text):
    if not webhook.startswith(DISCORD_PREFIXES):
        return "Webhook 網址不對"
    for part in split_text(text, MAX_TEXT["discord"]):    # Discord 一則最多 2000 字，太長就分幾則
        try:
            # flags 4 = 不展開連結預覽，新聞摘要才不會被一堆預覽卡片洗版
            r = requests.post(webhook, timeout=10, json={"content": part, "flags": 4})
        except requests.RequestException as exc:
            return "連線失敗：%s" % exc.__class__.__name__
        if r.status_code not in (200, 204):
            hints = {401: "Webhook 無效", 404: "Webhook 已被刪除或網址錯誤", 429: "傳太快了"}
            return "HTTP %d %s" % (r.status_code, hints.get(r.status_code, ""))
    return None


def send_one(name, ch, text):
    if name == "line":
        return line_push(ch["token"], ch["user_id"], text)
    if name == "telegram":
        return telegram_push(ch["token"], ch["chat_id"], text)
    return discord_push(ch["webhook"], text)


def send_all(cfg, text):
    """送到所有已開啟的管道。回傳 [(管道名稱, 錯誤說明)]，全部成功就是空的。"""
    errors = []
    for name in CHANNELS:
        if ready(cfg, name):
            error = send_one(name, cfg[name], text)
            if error:
                errors.append((CHANNELS[name], error))
    return errors


def update_from_form(cfg, form):
    """套用表單內容。Token / 網址欄位留空 = 保留原本的值（畫面上不會顯示舊值）。
    只檢查表單裡有出現的管道，避免某個舊設定有錯就擋住其他管道的儲存。
    回傳 (新設定, 錯誤說明)。"""
    new = json.loads(json.dumps(cfg))
    for key in ("alerts", "trades"):
        if key in form:
            new[key] = bool(form[key])
    for name in CHANNELS:
        part = form.get(name)
        if not isinstance(part, dict):
            continue
        if "enabled" in part:
            new[name]["enabled"] = bool(part["enabled"])
        for field in SECRET_FIELDS[name]:
            value = str(part.get(field) or "").strip()
            if value:
                new[name][field] = value
        if part.get("clear"):
            for field in SECRET_FIELDS[name]:
                new[name][field] = ""
            new[name]["enabled"] = False
        if new[name]["enabled"]:
            problem = check(name, new[name])
            if problem:
                return cfg, problem
    return new, None
