"""雲端帳戶積木：讓電腦版（桌面視窗、手機網頁）改用雲端看盤的帳戶。

雲端看盤（cloud_monitor/，GitHub 每 15 分鐘跑一次）是唯一的模擬帳戶：
    讀：雲端網頁的 data.json（帳戶、持股、交易、自選股、策略設定、訂單結果），不需要權杖
    寫：用 GitHub 權杖改 cloud_monitor/config.toml（自選股、上下限、支撐線、策略設定）
        和 cloud_monitor/orders.json（手動下單）；推上去就會觸發雲端重跑（約 1～3 分鐘）

設定存在 data/cloud.json（含權杖，只在這台電腦，不在 git 裡）。
"""
from __future__ import annotations

import base64
import json
import re
import time
from datetime import datetime, timedelta, timezone

import requests

DEFAULTS = {
    "enabled": True,
    "repo": "sdfvgv1994-bot/taco0525",
    "branch": "main",
    "config_path": "cloud_monitor/config.toml",
    "orders_path": "cloud_monitor/orders.json",
    "data_url": "https://sdfvgv1994-bot.github.io/taco0525/monitor/data.json",
    "page_url": "https://sdfvgv1994-bot.github.io/taco0525/monitor/",
    "token": "",
}
API = "https://api.github.com"
TPE = timezone(timedelta(hours=8))
# 手機輸入法的全形符號當半形看（一個字換一個字，位置不變，才能拿來切原本的文字）
_FULL = str.maketrans({"“": '"', "”": '"', "„": '"', "＂": '"', "‘": "'", "’": "'", "＇": "'", "，": ",",
                       "＝": "=", "［": "[", "］": "]", "｛": "{", "｝": "}", "．": ".",
                       **{chr(0xFF10 + i): str(i) for i in range(10)}})


class CloudError(ValueError):
    pass


def _norm(text: str) -> str:
    return text.translate(_FULL)


# ====================== 改 config.toml（保留檔案裡的說明文字） ======================
def _watch_range(t: str):
    m = re.search(r"^[ \t]*watchlist[ \t]*=[ \t]*\[[\s\S]*?\]", t, re.M)
    return (m.start(), m.end()) if m else None


def _section_range(t: str, name: str):
    """[name] 段落的內容位置（標題那行之後到下一個 [段落] 之前）。"""
    h = re.search(rf"^[ \t]*\[{name}\][^\n]*$", t, re.M)
    if not h:
        return None
    nxt = re.search(r"^[ \t]*\[", t[h.end():], re.M)
    return h.end(), (h.end() + nxt.start() if nxt else len(t))


def _num(v) -> str:
    v = float(v)
    return str(int(v)) if v.is_integer() else repr(v)


def toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return _num(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(toml_value(x) for x in v) + "]"
    return json.dumps(str(v), ensure_ascii=False)


def set_watchlist(text: str, rows: list) -> str:
    """rows = [(代號, {"lower", "upper", "support"})]：換掉 watchlist 那一行和 [limits] 段落的每一檔。"""
    entries = []
    for code, lim in rows:
        parts = []
        if lim.get("lower") is not None:
            parts.append(f"lower = {_num(lim['lower'])}")
        if lim.get("upper") is not None:
            parts.append(f"upper = {_num(lim['upper'])}")
        if lim.get("support"):
            parts.append(f"support = {toml_value(list(lim['support']))}")
        if parts:
            entries.append(f"{json.dumps(code)} = {{ {', '.join(parts)} }}")
    r = _section_range(_norm(text), "limits")
    if r:
        lines = text[r[0]:r[1]].split("\n")
        kept = [ln for ln in lines[1:] if not ln.strip() or ln.strip().startswith("#")]
        text = text[:r[0]] + "\n" + "\n".join(entries + kept) + text[r[1]:]
    elif entries:
        block = "[limits]\n" + "\n".join(entries) + "\n\n"
        at = re.search(r"^[ \t]*\[strategy_params\]", _norm(text), re.M)
        text = text[:at.start()] + block + text[at.start():] if at else text.rstrip("\n") + "\n\n" + block
    line = "watchlist = [" + ", ".join(json.dumps(c) for c, _ in rows) + "]"
    w = _watch_range(_norm(text))
    if w:
        return text[:w[0]] + line + text[w[1]:]
    first = re.search(r"^[ \t]*\[", _norm(text), re.M)
    return text[:first.start()] + line + "\n\n" + text[first.start():] if first else line + "\n" + text


def set_settings(text: str, values: dict) -> str:
    """改 [settings] 段落裡的設定，保留每一行後面的 # 說明；沒有這個設定就加在段落最上面。"""
    r = _section_range(_norm(text), "settings")
    if not r:
        text = text.rstrip("\n") + "\n\n[settings]\n"
        r = _section_range(text, "settings")
    body = text[r[0]:r[1]]
    for key, v in values.items():
        pat = re.compile(rf"^([ \t]*{re.escape(key)}[ \t]*=[ \t]*)([^#\n]*?)([ \t]*(#[^\n]*)?)$", re.M)
        if pat.search(_norm(body)):
            m = pat.search(_norm(body))
            body = body[:m.start(2)] + toml_value(v) + body[m.end(2):]
        else:
            body = "\n" + f"{key} = {toml_value(v)}" + body
    return text[:r[0]] + body + text[r[1]:]


# ====================== 連線 ======================
class CloudLink:
    def __init__(self, store, enabled: bool | None = None, session=None):
        self.store = store
        self.cfg = {**DEFAULTS, **(store.load("cloud.json", {}) or {})}
        if enabled is not None:
            self.cfg["enabled"] = enabled
        self.http = session or requests

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get("enabled"))

    @property
    def has_token(self) -> bool:
        return bool(self.cfg.get("token"))

    def configure(self, token: str | None = None, enabled: bool | None = None, clear: bool = False):
        if clear:
            self.cfg["token"] = ""
        if token:
            token = token.strip()
            if not re.fullmatch(r"(github_pat_|ghp_)\w{20,}", token):
                raise CloudError("看起來不是 GitHub 權杖（應該是 github_pat_ 開頭的一長串）")
            self.cfg["token"] = token
        if enabled is not None:
            self.cfg["enabled"] = bool(enabled)
        self.store.save("cloud.json", self.cfg)

    def public(self) -> dict:
        """給畫面看的（不含權杖）。"""
        return {"enabled": self.enabled, "has_token": self.has_token, "page_url": self.cfg["page_url"],
                "repo": self.cfg["repo"]}

    # ---- 讀 ----
    def fetch(self) -> dict:
        try:
            r = self.http.get(f"{self.cfg['data_url']}?t={int(time.time())}", timeout=20,
                              headers={"Cache-Control": "no-cache"})
            r.raise_for_status()
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            raise CloudError(f"讀不到雲端帳戶（{e.__class__.__name__}）") from None
        if not isinstance(data, dict) or "account" not in data:
            raise CloudError((data or {}).get("fatal") or "雲端帳戶的資料格式不對")
        return data

    # ---- 寫（GitHub API） ----
    def _headers(self):
        if not self.has_token:
            raise CloudError("要先設定 GitHub 權杖才能改雲端（桌面版「雲端帳戶…」，或在電腦上打開手機網頁的「設定」）")
        return {"Authorization": f"Bearer {self.cfg['token']}", "Accept": "application/vnd.github+json"}

    def _url(self, path):
        return f"{API}/repos/{self.cfg['repo']}/contents/{path}"

    def _error(self, r) -> CloudError:
        msg = ""
        try:
            msg = r.json().get("message", "")
        except ValueError:
            pass
        hints = {401: "GitHub 權杖錯誤或已過期，請重新設定", 403: "這組權杖不能修改 taco0525（Contents 要選 Read and write）",
                 404: "這組權杖看不到 taco0525（建立權杖時要選這個倉庫）", 409: "雲端設定剛好被別的地方改過，請再試一次",
                 422: "GitHub 拒絕這次修改"}
        return CloudError(f"{hints.get(r.status_code, 'GitHub 回應錯誤')}（HTTP {r.status_code} {msg}）".strip())

    def get_file(self, path) -> tuple[str | None, str | None]:
        try:
            r = self.http.get(self._url(path), headers=self._headers(), timeout=20,
                              params={"ref": self.cfg["branch"], "t": int(time.time())})
        except requests.RequestException as e:
            raise CloudError(f"連不上 GitHub（{e.__class__.__name__}）") from None
        if r.status_code == 404 and path == self.cfg["orders_path"]:
            return None, None   # 還沒有人下過單
        if r.status_code != 200:
            raise self._error(r)
        j = r.json()
        return base64.b64decode(j["content"]).decode("utf-8"), j["sha"]

    def put_file(self, path, text, sha, message):
        body = {"message": message, "content": base64.b64encode(text.encode("utf-8")).decode(),
                "branch": self.cfg["branch"]}
        if sha:
            body["sha"] = sha
        try:
            r = self.http.put(self._url(path), headers=self._headers(), json=body, timeout=20)
        except requests.RequestException as e:
            raise CloudError(f"連不上 GitHub（{e.__class__.__name__}）") from None
        if r.status_code not in (200, 201):
            raise self._error(r)

    def edit(self, path, change, message):
        """讀檔 → change(舊內容) → 寫回；剛好被別人改過就重讀再試一次。"""
        for attempt in range(2):
            text, sha = self.get_file(path)
            new = change(text)
            if new == text:
                return False
            try:
                self.put_file(path, new, sha, message)
                return True
            except CloudError as e:
                if attempt or "被別的地方改過" not in str(e):
                    raise
        return False

    def edit_config(self, change, message):
        return self.edit(self.cfg["config_path"], change, message)

    def add_order(self, code: str, side: str, shares: int | None, finished_ids=()) -> dict:
        now = datetime.now(TPE)
        order = {"id": f"pc-{int(now.timestamp() * 1000):x}", "time": now.strftime("%Y-%m-%dT%H:%M:%S"),
                 "code": code, "side": side, "shares": shares}
        done = set(finished_ids)

        def change(text):
            try:
                orders = json.loads(text) if text else []
            except ValueError:
                orders = []
            orders = [o for o in orders if isinstance(o, dict) and o.get("id") not in done]
            return json.dumps(orders + [order], ensure_ascii=False, indent=1) + "\n"

        what = f"{'買進' if side == 'buy' else '賣出'} {code} {'全部' if shares is None else f'{shares:,} 股'}"
        self.edit(self.cfg["orders_path"], change, f"電腦版模擬下單：{what}")
        return order
