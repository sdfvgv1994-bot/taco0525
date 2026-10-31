"""雲端看板「壞掉」或「恢復正常」時，直接推 Telegram 通知老闆。

通知方式（照順序挑第一個有設定的）：
  1. 直接推 Telegram：環境變數 TELEGRAM_TOKEN ＋ TELEGRAM_CHAT_ID（GitHub Secrets，雲端看盤本來就在用）
  2. 敲 n8n 的入口，由 n8n 推 Telegram：環境變數 N8N_BOARD_WEBHOOK（舊做法，n8n 試用到期後不能用，留著當備用）
  3. 都沒設就只記狀態、不通知

只在狀態改變時才通知：一直壞著不會每 15 分鐘吵一次，一直正常也不會通知。
連續壞 2 次（約 30 分鐘）才算壞掉，一次就好的小失誤不通知；壞著的時候多了新問題會再說一聲。
看不到的情況：整個 workflow 沒在跑（排程被停用、GitHub Actions 掛掉）或這個工作自己失敗。
上一次的狀態存在 --status-file（board.yml 用 GitHub 快取保存）。

    python cloud_monitor/board_alert.py --build failure --deploy skipped \
        --state "" --monitor "" --save "" --status-file .cache/board_alert/status.json \
        --run-url https://github.com/sdfvgv1994-bot/taco0525/actions/runs/123

Telegram Token 和 n8n 入口網址都等同密碼，任何輸出都不會印出它們。
只用 Python 標準函式庫：就算專案的套件裝壞了，這個也能執行。
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

SOURCE = "taco0525-board"          # n8n 那邊只接受帶這個 source 的請求
RUN_URL_PREFIX = "https://github.com/sdfvgv1994-bot/taco0525/actions/runs/"   # 只有這種網址才附在通知裡
TAIPEI = timezone(timedelta(hours=8))
UNSETTLED = {"cancelled", "skipped", ""}   # 步驟層級：這種結果不算成功也不算失敗
CONFIRM_RUNS = 2       # 連續壞這麼多次才通知：一次性的網路小失誤（下一次就好）不吵老闆


def find_problems(build: str, deploy: str, state: str, monitor: str, save: str) -> list[str] | None:
    """依各工作／步驟的結果列出問題。回傳 [] 代表正常；None 代表這次無法判斷。"""
    if build in ("skipped", ""):
        return None
    problems: list[str] = []
    if build == "cancelled":            # 超過 30 分鐘被強制停掉（或被手動取消）也要知道
        problems.append("看板工作被中斷（可能跑超過 30 分鐘）")
    elif build != "success":
        problems.append("看板網頁產生失敗")
    elif deploy == "cancelled":
        problems.append("網頁發佈被中斷")
    elif deploy != "success":
        if deploy in UNSETTLED:
            return None
        problems.append("網頁發佈到 GitHub Pages 失敗")
    # 雲端看盤的幾步都設了「出錯也繼續」，整個工作不會因此失敗，所以要個別看
    if state not in UNSETTLED:          # 空字串 = 還沒跑到雲端看盤就停了（上面已經報 build 失敗）
        if state != "success":
            problems.append("讀不到雲端看盤的資料（monitor-data 分支）")
        else:
            if monitor != "success":
                problems.append("雲端看盤程式出錯")
            if save != "success":
                problems.append("雲端看盤的資料存不回去（monitor-data 分支）")
    return problems


def load_status(path: Path) -> dict:
    """狀態檔：status＝已經通知過的狀態（ok／fail）、announced＝通知過的問題、bad_runs＝連續壞了幾次。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("status") in ("ok", "fail"):
            data.setdefault("announced", [])
            data["bad_runs"] = int(data.get("bad_runs") or 0)
            return data
    except (OSError, ValueError, TypeError):
        pass
    return {"status": "ok", "announced": [], "bad_runs": 0}   # 第一次執行或檔案壞掉：當作原本正常


def decide(prev: dict, problems: list[str]) -> tuple[str | None, dict]:
    """回傳（要送的通知：'fail'／'recovered'／None，新的狀態）。"""
    if not problems:
        kind = "recovered" if prev.get("status") == "fail" else None
        return kind, {"status": "ok", "announced": [], "bad_runs": 0}

    bad_runs = int(prev.get("bad_runs") or 0) + 1
    if prev.get("status") == "fail":
        announced = list(prev.get("announced") or [])
        new = [p for p in problems if p not in announced]
        state = {"status": "fail", "announced": announced + new, "bad_runs": bad_runs}
        return ("fail" if new else None), state       # 只有多了新問題才再說一聲
    if bad_runs >= CONFIRM_RUNS:
        return "fail", {"status": "fail", "announced": list(problems), "bad_runs": bad_runs}
    return None, {"status": "ok", "announced": [], "bad_runs": bad_runs}  # 先觀察，下次還壞才通知


def post(url: str, payload: dict, opener=urllib.request.urlopen) -> None:
    req = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with opener(req, timeout=15) as r:
        code = getattr(r, "status", 200)
        if not 200 <= code < 300:
            raise RuntimeError(f"n8n 回應 HTTP {code}")


def format_text(kind: str, problems: list[str], when: str, run_url: str) -> str:
    """Telegram 訊息（HTML 格式），內容和以前 n8n 推的一樣。"""
    def esc(s: str) -> str:
        return html.escape(str(s), quote=False)

    if kind == "fail":
        text = "⚠️ <b>雲端看板出問題了</b>\n" + "\n".join("• " + esc(p) for p in problems[:5])
    else:
        text = "✅ <b>雲端看板恢復正常</b>"
    text += "\n🕒 " + esc(when)
    if run_url.startswith(RUN_URL_PREFIX):
        text += "\n" + esc(run_url)
    return text


def post_telegram(token: str, chat: str, text: str, opener=urllib.request.urlopen) -> None:
    payload = {"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with opener(req, timeout=15) as r:
        code = getattr(r, "status", 200)
        if not 200 <= code < 300:
            raise RuntimeError(f"Telegram 回應 HTTP {code}")


def main(argv=None, env=None, opener=urllib.request.urlopen, now=None) -> int:
    env = os.environ if env is None else env
    ap = argparse.ArgumentParser()
    for name in ("build", "deploy", "state", "monitor", "save"):
        ap.add_argument(f"--{name}", default="")
    ap.add_argument("--status-file", required=True)
    ap.add_argument("--run-url", default="")
    a = ap.parse_args(argv)

    problems = find_problems(a.build, a.deploy, a.state, a.monitor, a.save)
    if problems is None:
        print("這次被取消或沒跑完，不判斷、不通知")
        return 0

    status_file = Path(a.status_file)
    prev = load_status(status_file)
    kind, new_state = decide(prev, problems)
    url = (env.get("N8N_BOARD_WEBHOOK") or "").strip()
    tg_token = (env.get("TELEGRAM_TOKEN") or "").strip()
    tg_chat = (env.get("TELEGRAM_CHAT_ID") or "").strip()
    when = (now or datetime.now(TAIPEI)).strftime("%Y-%m-%d %H:%M")

    if kind and tg_token and tg_chat:
        try:
            post_telegram(tg_token, tg_chat, format_text(kind, problems, when, a.run_url), opener=opener)
            print(f"已通知（Telegram）：{kind}")
        except Exception as e:                       # 通知失敗不要讓看板工作變紅；也不記狀態，下次再試
            msg = str(e).replace(tg_token, "<token>")
            print(f"::warning::通知 Telegram 失敗（下次再試）：{type(e).__name__} {msg[:150]}")
            return 0
    elif kind and url:
        payload = {"source": SOURCE, "status": kind, "problems": problems,
                   "time": when, "run_url": a.run_url}
        try:
            post(url, payload, opener=opener)
            print(f"已通知：{kind}")
        except Exception as e:                       # 通知失敗不要讓看板工作變紅；也不記狀態，下次再試
            msg = str(e).replace(url, "<網址>")
            print(f"::warning::通知 n8n 失敗（下次再試）：{type(e).__name__} {msg[:150]}")
            return 0
    elif kind:
        print(f"狀態變成 {kind}，但沒有設定 TELEGRAM_TOKEN／TELEGRAM_CHAT_ID 或 N8N_BOARD_WEBHOOK，不通知")
    elif problems and new_state["status"] == "ok":
        print(f"這次有問題（連續第 {new_state['bad_runs']} 次），連續 {CONFIRM_RUNS} 次才通知")
    else:
        print("狀態沒變（" + ("正常" if not problems else "仍有問題") + "），不通知")

    status_file.parent.mkdir(parents=True, exist_ok=True)
    status_file.write_text(json.dumps(new_state, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
