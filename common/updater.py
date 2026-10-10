"""程式啟動時自動從 GitHub 更新到最新版。

流程：git fetch → GitHub 上有新版就直接覆蓋成最新版（本機改過的程式碼、沒推上去的提交
也會被蓋掉，但會先備份到 .cache/backup/ 和 backup/ 分支；沒有新版時不會動到本機修改）→ requirements.txt 有變就自動安裝套件 → 用新版重新啟動。

你的資料（模擬帳戶、自選股、提醒紀錄、快取、畫作、最高分）都不在 git 裡，不會被動到。

不會更新的情況（都只印一行提示，照常用目前版本執行）：
- 沒有網路、GitHub 連不上、這裡不是 git 下載的資料夾、電腦沒有 git
- 設了環境變數 TACO_NO_UPDATE=1，或執行時加 --no-update
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRANCH = "main"
TIMEOUT = 20
_ENV_DONE = "TACO_UPDATED"   # 重新啟動後設這個，避免無限循環


def _git(*args, timeout=TIMEOUT) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True,
                          timeout=timeout)


def _backup(local: str, remote: str, dirty: str, out) -> str | None:
    """覆蓋前把本機的修改和沒推上去的提交備份起來，萬一需要還找得回來。"""
    has_own_commits = _git("merge-base", "--is-ancestor", local, remote).returncode != 0
    if not dirty and not has_own_commits:
        return None
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    folder = ROOT / ".cache" / "backup"
    folder.mkdir(parents=True, exist_ok=True)
    parts = []
    if dirty:
        path = folder / f"{stamp}_本機修改.patch"
        path.write_text(_git("diff", "HEAD").stdout, encoding="utf-8")
        parts.append(str(path.relative_to(ROOT)))
    if has_own_commits:
        name = f"backup/{stamp}"
        _git("branch", "--force", name, local)
        parts.append(f"git 分支 {name}")
    return "、".join(parts)


def check_and_update(argv: list | None = None, restart: bool = True, out=print) -> str:
    """回傳結果：'disabled' / 'skipped' / 'latest' / 'updated'。updated 且 restart=True 時不會回傳（直接重啟）。"""
    argv = list(sys.argv if argv is None else argv)
    if "--no-update" in argv:
        argv.remove("--no-update")
        sys.argv[:] = argv
        return "disabled"
    if os.environ.get("TACO_NO_UPDATE") or os.environ.get(_ENV_DONE):
        return "disabled"
    try:
        if _git("rev-parse", "--is-inside-work-tree").returncode != 0:
            return "skipped"
        if _git("fetch", "--quiet", "origin", BRANCH).returncode != 0:
            out("ℹ 連不上 GitHub，先用目前的版本")
            return "skipped"
        local = _git("rev-parse", "HEAD").stdout.strip()
        remote = _git("rev-parse", f"origin/{BRANCH}").stdout.strip()
        # 只看被 git 追蹤的檔案；data、.cache 等都在 .gitignore 裡，不受影響
        dirty = _git("status", "--porcelain", "--untracked-files=no").stdout.strip()
        if not remote or local == remote:   # 沒有新版：不去動本機的任何修改
            return "latest"
        old_req = _git("show", f"{local}:requirements.txt").stdout
        new_req = _git("show", f"{remote}:requirements.txt").stdout
        count = _git("rev-list", "--count", f"{local}..{remote}").stdout.strip() or "0"
        backup = _backup(local, remote, dirty, out)
        if _git("checkout", "--quiet", "--force", "-B", BRANCH, f"origin/{BRANCH}").returncode != 0:
            out("⚠ 自動更新失敗，先用目前的版本")
            return "skipped"
        out(f"✅ 已自動更新到最新版（{count} 個更新）" + (f"；舊的修改備份在 {backup}" if backup else ""))
        if old_req != new_req:
            out("   套件清單有變，正在安裝…")
            subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r",
                            str(ROOT / "requirements.txt")], timeout=600)
    except (OSError, subprocess.SubprocessError) as e:  # 沒有 git、逾時等
        out(f"ℹ 無法檢查更新（{e.__class__.__name__}），先用目前的版本")
        return "skipped"
    if restart:
        os.environ[_ENV_DONE] = "1"
        out("   用新版重新啟動…\n")
        sys.stdout.flush()
        if os.name == "nt":
            # Windows 的 execv 會讓舊程式先結束、命令列提早跳回來，改成等新版跑完
            sys.exit(subprocess.call([sys.executable, *argv]))
        os.execv(sys.executable, [sys.executable, *argv])
    return "updated"
