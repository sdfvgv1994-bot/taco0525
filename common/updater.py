"""程式啟動時自動從 GitHub 更新到最新版。

流程：git fetch → GitHub 上有本機沒有的新版 → 先備份本機的修改 → 直接覆蓋成最新版
→ requirements.txt 有變就安裝套件（安裝失敗就退回原本的版本，下次啟動再試）→ 用新版重新啟動。

- 本機改過的程式碼、沒推上去的提交會被覆蓋，但會先備份：
  .cache/backup/ 裡的 .patch 檔，以及 git 的 refs/backup/… 與 backup/… 分支
- GitHub 上沒有新東西時，不會動到本機的任何修改
- 你的資料（模擬帳戶、自選股、提醒紀錄、快取、畫作、最高分）都不在 git 裡，不會被動到
- 沒有網路、GitHub 連不上、電腦沒有 git、這個資料夾不是 git 下載的：照常用目前版本
- 不想自動更新：執行時加 --no-update，或設環境變數 TACO_NO_UPDATE=1

這個模組只用標準函式庫，要在程式匯入 pandas 等套件「之前」呼叫，
Windows 上安裝新版套件時才不會因為檔案被占用而失敗。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRANCH = "main"
TIMEOUT = 20
_ENV_DONE = "TACO_UPDATED"   # 重新啟動後設這個，避免無限循環


def _git(*args, timeout=TIMEOUT, binary=False) -> subprocess.CompletedProcess:
    # 一律用 UTF-8 解碼：繁中 Windows 預設是 cp950，讀到程式碼裡的中文會出錯
    kw = {} if binary else {"text": True, "encoding": "utf-8", "errors": "replace"}
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True,
                          timeout=timeout, **kw)


def _req_hash() -> str:
    try:
        return hashlib.sha256((ROOT / "requirements.txt").read_bytes()).hexdigest()
    except OSError:
        return ""


@contextmanager
def _update_lock():
    """同時開好幾個程式時，只讓一個做更新；其他的等它做完再看狀態。"""
    path = ROOT / ".cache" / "update.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT)
    try:
        if os.name == "nt":
            import msvcrt
            deadline = time.monotonic() + 120
            while True:
                try:
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(0.1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


def _installed_hash_path() -> Path:
    return ROOT / ".cache" / "requirements.installed"


def _write_installed_hash() -> None:
    try:
        p = _installed_hash_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_req_hash())
    except OSError:
        pass


def _install_requirements(out) -> bool:
    out("   套件清單有變，正在安裝…")
    try:
        r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r",
                            str(ROOT / "requirements.txt")], timeout=900)
    except (OSError, subprocess.SubprocessError):
        return False
    if r.returncode != 0:
        return False
    _write_installed_hash()
    return True


def _ensure_requirements(out) -> None:
    """上次安裝被中斷或失敗時，下次啟動補裝。第一次執行（沒有紀錄）視為已裝好。"""
    p = _installed_hash_path()
    if not p.exists():
        _write_installed_hash()
        return
    if p.read_text().strip() != _req_hash():
        if not _install_requirements(out):
            out("⚠ 套件安裝失敗，請手動執行：pip install -r requirements.txt")


def _backup(local: str, remote: str, dirty: bool) -> str | None:
    """覆蓋前把本機的修改和沒推上去的提交備份起來。"""
    own_commits = _git("merge-base", "--is-ancestor", local, remote).returncode != 0
    if not dirty and not own_commits:
        return None
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    parts = []
    if dirty:
        # stash create 會把已暫存和未暫存的修改都存成 git 物件（含二進位檔），不會遺失
        sha = _git("stash", "create", "自動更新前的本機修改").stdout.strip()
        if sha:
            _git("update-ref", f"refs/backup/{stamp}", sha)
        diff = _git("diff", "--binary", "HEAD", binary=True).stdout or b""
        if diff:
            folder = ROOT / ".cache" / "backup"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{stamp}_{os.getpid()}_本機修改.patch"
            with open(path, "xb") as f:
                f.write(diff)
            parts.append(str(path.relative_to(ROOT)))
        if sha:
            parts.append(f"git refs/backup/{stamp}")
    if own_commits:
        name = f"backup/{stamp}"
        _git("branch", "--force", name, local)
        parts.append(f"git 分支 {name}")
    return "、".join(parts) or None


def _update(out) -> tuple[str, bool]:
    """回傳 (結果, 是否要重啟)。"""
    if _git("rev-parse", "--is-inside-work-tree").returncode != 0:
        return "skipped", False
    top = _git("rev-parse", "--show-toplevel").stdout.strip()
    if not top or Path(top).resolve() != ROOT.resolve():
        return "skipped", False   # 例如用 ZIP 解壓縮在別的 git 專案裡面：不要動到外面的專案
    if _git("fetch", "--quiet", "origin", BRANCH).returncode != 0:
        out("ℹ 連不上 GitHub，先用目前的版本")
        return "skipped", False
    local = _git("rev-parse", "HEAD").stdout.strip()
    remote = _git("rev-parse", f"origin/{BRANCH}").stdout.strip()
    if not remote:
        return "skipped", False
    behind = int(_git("rev-list", "--count", f"{local}..{remote}").stdout.strip() or 0)
    if behind == 0:            # GitHub 上沒有本機沒有的東西：不去動本機的任何修改
        return "latest", False
    # 只看被 git 追蹤的檔案；data、.cache 等都在 .gitignore 裡，不受影響
    dirty = bool(_git("status", "--porcelain", "--untracked-files=no").stdout.strip())
    old_req = _req_hash()
    backup = _backup(local, remote, dirty)
    if _git("checkout", "--quiet", "--force", "-B", BRANCH, f"origin/{BRANCH}").returncode != 0:
        out("⚠ 自動更新失敗，先用目前的版本")
        return "skipped", False
    if _req_hash() != old_req and not _install_requirements(out):
        _git("checkout", "--quiet", "--force", "-B", BRANCH, local)
        out("⚠ 新版需要的套件安裝失敗，先退回原本的版本，下次啟動會再試"
            + (f"（本機修改備份在 {backup}）" if backup else ""))
        return "skipped", False
    out(f"✅ 已自動更新到最新版（{behind} 個更新）" + (f"；舊的修改備份在 {backup}" if backup else ""))
    return "updated", True


def check_and_update(argv: list | None = None, restart: bool = True, out=print) -> str:
    """回傳 'disabled' / 'skipped' / 'latest' / 'updated'。更新且 restart=True 時直接用新版重啟、不會回傳。

    任何意外錯誤都只印一行提示，絕不讓程式因為自動更新而無法啟動。
    """
    argv = list(sys.argv if argv is None else argv)
    if "--no-update" in argv:
        argv.remove("--no-update")
        sys.argv[:] = argv
        return "disabled"
    if os.environ.get("TACO_NO_UPDATE") or os.environ.get(_ENV_DONE):
        return "disabled"
    try:
        with _update_lock():
            result, need_restart = _update(out)
            if result != "updated":
                _ensure_requirements(out)
    except Exception as e:  # noqa: BLE001 - 自動更新失敗不能擋住程式啟動
        out(f"ℹ 無法檢查更新（{e.__class__.__name__}），先用目前的版本")
        return "skipped"
    if need_restart and restart:
        os.environ[_ENV_DONE] = "1"
        out("   用新版重新啟動…\n")
        sys.stdout.flush()
        if os.name == "nt":
            # Windows 的 execv 會讓舊程式先結束、命令列提早跳回來，改成等新版跑完
            sys.exit(subprocess.call([sys.executable, *argv]))
        os.execv(sys.executable, [sys.executable, *argv])
    return result
