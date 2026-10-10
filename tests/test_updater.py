"""自動更新測試：用暫存資料夾建一個假的「GitHub」（bare repo）和「使用者電腦」（clone）。"""
import subprocess
import sys

import pytest

from common import updater


def git(cwd, *args):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


@pytest.fixture()
def repos(tmp_path, monkeypatch):
    for k, v in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                 "GIT_COMMITTER_EMAIL": "t@t"}.items():
        monkeypatch.setenv(k, v)
    for k in ("TACO_NO_UPDATE", "TACO_UPDATED"):     # 先 setenv 再 delenv，測試結束一定會還原
        monkeypatch.setenv(k, "x")
        monkeypatch.delenv(k)
    remote = tmp_path / "github.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    dev = tmp_path / "dev"
    subprocess.run(["git", "clone", "-q", str(remote), str(dev)], check=True, capture_output=True)
    git(dev, "checkout", "-q", "-B", "main")
    (dev / "app.py").write_text("v1\n")
    (dev / "requirements.txt").write_text("pandas\n")
    (dev / ".gitignore").write_text("data/\n")
    git(dev, "add", "-A")
    git(dev, "commit", "-q", "-m", "v1")
    git(dev, "push", "-q", "origin", "main")
    user = tmp_path / "user"
    subprocess.run(["git", "clone", "-q", str(remote), str(user)], check=True, capture_output=True)
    (user / "data").mkdir()
    (user / "data" / "account.json").write_text('{"cash": 123}')   # 使用者資料（不在 git 裡）
    monkeypatch.setattr(updater, "ROOT", user)

    def publish(content, req=None):
        (dev / "app.py").write_text(content)
        if req is not None:
            (dev / "requirements.txt").write_text(req)
        git(dev, "commit", "-q", "-am", content.strip())
        git(dev, "push", "-q", "origin", "main")
    return remote, dev, user, publish


def run(**kw):
    lines = []
    result = updater.check_and_update(argv=["prog.py"], restart=False, out=lines.append, **kw)
    return result, "\n".join(lines)


def test_latest(repos):
    assert run()[0] == "latest"


def test_updates_to_new_version_and_keeps_user_data(repos):
    _, _, user, publish = repos
    publish("v2\n")
    result, msg = run()
    assert result == "updated" and "1 個更新" in msg
    assert (user / "app.py").read_text() == "v2\n"
    assert (user / "data" / "account.json").read_text() == '{"cash": 123}'


def test_local_changes_are_overwritten_but_backed_up(repos):
    _, _, user, publish = repos
    (user / "app.py").write_text("my local edit\n")
    publish("v2\n")
    result, msg = run()
    assert result == "updated" and "備份" in msg
    assert (user / "app.py").read_text() == "v2\n"
    patches = list((user / ".cache" / "backup").glob("*.patch"))
    assert patches and "my local edit" in patches[0].read_text(encoding="utf-8")
    assert (user / "data" / "account.json").exists()


def test_local_commits_are_overwritten_but_kept_on_backup_branch(repos):
    _, _, user, publish = repos
    (user / "app.py").write_text("my commit\n")
    git(user, "commit", "-q", "-am", "mine")
    mine = git(user, "rev-parse", "HEAD")
    publish("v2\n")
    result, msg = run()
    assert result == "updated" and (user / "app.py").read_text() == "v2\n"
    branches = git(user, "branch", "--list", "backup/*", "--format=%(objectname)")
    assert mine in branches


def test_no_new_version_keeps_local_changes(repos):
    _, _, user, _ = repos
    (user / "app.py").write_text("my local edit\n")
    assert run()[0] == "latest"
    assert (user / "app.py").read_text() == "my local edit\n"


def test_works_from_another_branch(repos):
    _, _, user, publish = repos
    git(user, "checkout", "-q", "-b", "experiment")
    publish("v2\n")
    assert run()[0] == "updated"
    assert git(user, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert (user / "app.py").read_text() == "v2\n"


def test_requirements_change_installs_packages(repos, monkeypatch):
    _, _, _, publish = repos
    publish("v2\n", req="pandas\nnewpkg\n")
    calls = []
    real_run = subprocess.run

    def fake_run(cmd, *a, **kw):
        if "pip" in cmd:
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return real_run(cmd, *a, **kw)
    monkeypatch.setattr(updater.subprocess, "run", fake_run)
    result, msg = run()
    assert result == "updated" and "安裝" in msg
    assert calls and calls[0][:3] == [sys.executable, "-m", "pip"]


def test_no_pip_when_requirements_unchanged(repos, monkeypatch):
    _, _, _, publish = repos
    publish("v2\n")
    real_run = subprocess.run
    monkeypatch.setattr(updater.subprocess, "run",
                        lambda cmd, *a, **kw: pytest.fail("不該裝套件") if "pip" in cmd
                        else real_run(cmd, *a, **kw))
    assert run()[0] == "updated"


def test_offline_skips(repos):
    _, _, user, publish = repos
    publish("v2\n")
    git(user, "remote", "set-url", "origin", str(user.parent / "nowhere.git"))
    result, msg = run()
    assert result == "skipped" and "連不上" in msg
    assert (user / "app.py").read_text() == "v1\n"


def test_not_a_git_folder(tmp_path, monkeypatch):
    monkeypatch.delenv("TACO_NO_UPDATE", raising=False)
    monkeypatch.delenv("TACO_UPDATED", raising=False)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    monkeypatch.setattr(updater, "ROOT", tmp_path)
    assert run()[0] == "skipped"


def test_disable_by_flag_and_env(repos, monkeypatch):
    _, _, _, publish = repos
    publish("v2\n")
    monkeypatch.setattr(sys, "argv", ["prog.py", "--no-update", "--demo"])
    assert updater.check_and_update(restart=False, out=lambda *_: None) == "disabled"
    assert sys.argv == ["prog.py", "--demo"]          # 旗標拿掉，不會讓 argparse 報錯
    monkeypatch.setenv("TACO_NO_UPDATE", "1")
    assert run()[0] == "disabled"


def test_restart_runs_new_version_once(repos, monkeypatch):
    _, _, _, publish = repos
    publish("v2\n")
    seen = {}
    monkeypatch.setattr(updater.os, "name", "posix")
    monkeypatch.setattr(updater.os, "execv", lambda exe, args: seen.update(exe=exe, args=args))
    updater.check_and_update(argv=["prog.py", "--demo"], out=lambda *_: None)
    assert seen["args"] == [sys.executable, "prog.py", "--demo"]
    assert updater.os.environ.get("TACO_UPDATED") == "1"   # 重啟後不會再更新一次
    assert run()[0] == "disabled"


# ---------- 第三輪審查補強 ----------
def test_ahead_of_github_is_left_alone(repos):
    """本機有沒推上去的提交、GitHub 上沒有新東西：不能被「更新」成舊版。"""
    _, _, user, _ = repos
    (user / "app.py").write_text("my commit\n")
    git(user, "commit", "-q", "-am", "mine")
    (user / "app.py").write_text("my commit + tweak\n")
    assert run()[0] == "latest"
    assert (user / "app.py").read_text() == "my commit + tweak\n"


def test_chinese_local_edit_on_cp950_locale(repos, monkeypatch):
    """繁中 Windows 預設 cp950：讀到 UTF-8 的中文 diff 也不能當掉。"""
    _, _, user, publish = repos
    (user / "app.py").write_text("# 加入聯電 2303、國泰永續高股息\n")
    publish("v2\n")
    monkeypatch.setattr(subprocess, "_text_encoding", lambda: "cp950", raising=False)
    result, msg = run()
    assert result == "updated" and (user / "app.py").read_text() == "v2\n"
    patch = next((user / ".cache" / "backup").glob("*.patch")).read_bytes().decode("utf-8")
    assert "聯電" in patch


def test_staged_and_unstaged_changes_are_backed_up(repos):
    _, _, user, publish = repos
    (user / "app.py").write_text("staged edit\n")
    git(user, "add", "app.py")
    (user / "requirements.txt").write_text("pandas\nmine\n")      # 未暫存
    publish("v2\n")
    result, _ = run()
    assert result == "updated"
    refs = git(user, "for-each-ref", "refs/backup/", "--format=%(objectname)").split()
    assert refs
    stash = refs[0]
    assert git(user, "show", f"{stash}:app.py") == "staged edit"
    assert "mine" in git(user, "show", f"{stash}:requirements.txt")


def test_unexpected_error_never_blocks_startup(repos, monkeypatch):
    def boom(out):
        raise ValueError("bug")
    monkeypatch.setattr(updater, "_update", boom)
    result, msg = run()
    assert result == "skipped" and "ValueError" in msg


def test_pip_failure_rolls_back_and_retries_next_time(repos, monkeypatch):
    _, _, user, publish = repos
    before = git(user, "rev-parse", "HEAD")
    publish("v2\n", req="pandas\nnewpkg\n")
    real_run = subprocess.run
    pip_ok = {"value": False}

    def fake_run(cmd, *a, **kw):
        if "pip" in cmd:
            return subprocess.CompletedProcess(cmd, 0 if pip_ok["value"] else 1, "", "")
        return real_run(cmd, *a, **kw)
    monkeypatch.setattr(updater.subprocess, "run", fake_run)
    result, msg = run()
    assert result == "skipped" and "退回" in msg
    assert git(user, "rev-parse", "HEAD") == before and (user / "app.py").read_text() == "v1\n"
    pip_ok["value"] = True
    assert run()[0] == "updated" and (user / "app.py").read_text() == "v2\n"


def test_interrupted_install_is_completed_on_next_start(repos, monkeypatch):
    _, _, user, _ = repos
    run()                                                  # 第一次執行：記下目前套件清單
    (user / ".cache" / "requirements.installed").write_text("old-hash")
    calls = []
    real_run = subprocess.run
    monkeypatch.setattr(updater.subprocess, "run",
                        lambda cmd, *a, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", "")
                        if "pip" in cmd else real_run(cmd, *a, **kw))
    assert run()[0] == "latest" and calls
    assert (user / ".cache" / "requirements.installed").read_text() == updater._req_hash()


def test_folder_inside_another_git_repo_is_not_touched(tmp_path, monkeypatch, repos):
    """用 ZIP 解壓縮在別的 git 專案裡面：不能去更新外面那個專案。"""
    _, _, user, publish = repos
    inner = user / "unzipped_copy"
    inner.mkdir()
    (inner / "x.py").write_text("x\n")
    (user / "app.py").write_text("parent's unsaved notes\n")
    publish("v2\n")
    monkeypatch.setattr(updater, "ROOT", inner)
    assert run()[0] == "skipped"
    assert (user / "app.py").read_text() == "parent's unsaved notes\n"


def _update_in_child(root, barrier, out):
    import sys as _sys
    _sys.path.insert(0, str(root))
    from pathlib import Path as P
    from common import updater as up
    up.ROOT = P(out["user"])
    barrier.wait()
    out["results"].append(up.check_and_update(argv=["x"], restart=False, out=lambda *_: None))


def test_two_programs_starting_together_keep_the_backup(repos):
    import multiprocessing as mp
    from pathlib import Path
    _, _, user, publish = repos
    (user / "app.py").write_text("precious local edit\n")
    publish("v2\n")
    ctx = mp.get_context("spawn")
    with ctx.Manager() as m:
        shared = m.dict(user=str(user), results=m.list())
        barrier = ctx.Barrier(2)
        root = Path(__file__).resolve().parents[1]
        ps = [ctx.Process(target=_update_in_child, args=(root, barrier, shared)) for _ in range(2)]
        [p.start() for p in ps]
        [p.join(timeout=60) for p in ps]
        results = sorted(shared["results"])
    assert results == ["latest", "updated"]                 # 只有一個真的做更新
    patches = list((user / ".cache" / "backup").glob("*.patch"))
    assert len(patches) == 1 and "precious local edit" in patches[0].read_text(encoding="utf-8")
