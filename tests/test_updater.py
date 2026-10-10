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
