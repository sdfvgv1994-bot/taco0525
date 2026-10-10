"""雲端看盤這次沒有成功時（讀不到帳戶資料、程式出錯、存檔失敗），保留上一次發佈的網頁，
不要讓手機上的 /monitor/ 變成 404，也不要顯示沒存檔成功的交易。

    python cloud_monitor/keep_last.py --out _site/monitor --state success --monitor failure --save skipped

只用 Python 標準函式庫：就算看盤程式本身壞掉（例如少了套件），這個也能執行。
"""
from __future__ import annotations

import argparse
import json
import shutil
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIVE_DATA = "https://sdfvgv1994-bot.github.io/taco0525/monitor/data.json"   # 目前網頁上的資料


def reason_for(state: str, monitor: str, save: str) -> str:
    """依 GitHub Actions 各步驟的結果（success / failure / skipped / cancelled）說明原因。"""
    if state != "success":
        return "雲端看盤這次讀不到帳戶資料（monitor-data 分支）"
    if monitor != "success":
        return "雲端看盤程式這次執行失敗"
    if save != "success":
        return "雲端看盤這次存檔失敗，這次的訊號和交易下一次會重新判斷"
    return "雲端看盤這次沒有完成"


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "taco0525-monitor"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def keep_last(out_dir: Path, reason: str, get=_get, log=print) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(HERE / "index.html", out / "index.html")
    try:
        data = get(f"{LIVE_DATA}?t={int(time.time())}")
        if not isinstance(data, dict) or "updated" not in data:
            raise ValueError(data.get("fatal", "上一次也沒有資料") if isinstance(data, dict) else "格式不對")
        data["run_error"] = f"{reason}，下面先顯示上一次（{data['updated'][5:16]}）的資料"
    except Exception as e:  # noqa: BLE001 - 第一次執行、網路問題：只顯示錯誤
        data = {"fatal": f"{reason}，也讀不到上一次的資料（{e}）"}
    (out / "data.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                                   encoding="utf-8")
    log(f"保留上一次的雲端看盤網頁：{data.get('run_error') or data.get('fatal')}")
    return data


def main(argv=None):
    ap = argparse.ArgumentParser(description="雲端看盤失敗時，保留上一次的網頁")
    ap.add_argument("--out", required=True)
    ap.add_argument("--state", default="")
    ap.add_argument("--monitor", default="")
    ap.add_argument("--save", default="")
    a = ap.parse_args(argv)
    keep_last(Path(a.out), reason_for(a.state, a.monitor, a.save))


if __name__ == "__main__":
    main()
