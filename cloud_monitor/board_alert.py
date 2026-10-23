"""雲端看板「壞掉」或「恢復正常」時，敲 n8n 的入口，由 n8n 推 Telegram 通知老闆。

只在狀態改變時才通知：一直壞著不會每 15 分鐘吵一次，一直正常也不會通知（n8n 不會被觸發、不吃額度）。
上一次的狀態存在 --status-file（board.yml 用 GitHub 快取保存）。

    python cloud_monitor/board_alert.py --build failure --deploy skipped \
        --state "" --monitor "" --save "" --status-file .cache/board_alert/status.json \
        --run-url https://github.com/sdfvgv1994-bot/taco0525/actions/runs/123

n8n 入口網址放在環境變數 N8N_BOARD_WEBHOOK（GitHub Secrets 設定）；沒設就只記狀態、不通知。
網址等同密碼，任何輸出都不會印出它。
只用 Python 標準函式庫：就算專案的套件裝壞了，這個也能執行。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

SOURCE = "taco0525-board"          # n8n 那邊只接受帶這個 source 的請求
TAIPEI = timezone(timedelta(hours=8))
UNSETTLED = {"cancelled", "skipped", ""}   # 這種結果不算成功也不算失敗：這次不判斷


def find_problems(build: str, deploy: str, state: str, monitor: str, save: str) -> list[str] | None:
    """依各工作／步驟的結果列出問題。回傳 [] 代表正常；None 代表這次無法判斷（例如被手動取消）。"""
    if build in UNSETTLED:
        return None
    problems: list[str] = []
    if build != "success":
        problems.append("看板網頁產生失敗")
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
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("status") in ("ok", "fail"):
            return data
    except (OSError, ValueError):
        pass
    return {"status": "ok", "problems": []}      # 第一次執行或檔案壞掉：當作原本正常


def decide(prev: dict, problems: list[str]) -> str | None:
    """回傳要送的通知種類：'fail'、'recovered'，或 None（不用通知）。"""
    if problems:
        if prev.get("status") != "fail":
            return "fail"
        if sorted(prev.get("problems") or []) != sorted(problems):
            return "fail"                     # 壞的地方變了（多了新問題）也說一聲
        return None
    return "recovered" if prev.get("status") == "fail" else None


def post(url: str, payload: dict, opener=urllib.request.urlopen) -> None:
    req = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with opener(req, timeout=15) as r:
        code = getattr(r, "status", 200)
        if not 200 <= code < 300:
            raise RuntimeError(f"n8n 回應 HTTP {code}")


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
    kind = decide(prev, problems)
    url = (env.get("N8N_BOARD_WEBHOOK") or "").strip()

    if kind and url:
        when = (now or datetime.now(TAIPEI)).strftime("%Y-%m-%d %H:%M")
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
        print(f"狀態變成 {kind}，但沒有設定 N8N_BOARD_WEBHOOK，不通知")
    else:
        print("狀態沒變（" + ("正常" if not problems else "仍有問題") + "），不通知")

    status_file.parent.mkdir(parents=True, exist_ok=True)
    status_file.write_text(json.dumps({"status": "fail" if problems else "ok", "problems": problems},
                                      ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
