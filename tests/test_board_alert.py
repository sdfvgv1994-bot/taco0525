"""雲端看板壞掉／恢復時通知 n8n（cloud_monitor/board_alert.py）"""
import json
from datetime import datetime

from cloud_monitor import board_alert as ba

URL = "https://example.n8n.cloud/webhook/secret-path"
OK = dict(build="success", deploy="success", state="success", monitor="success", save="success")


class FakeResp:
    def __init__(self, status=200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Recorder:
    def __init__(self, status=200, boom=None):
        self.calls, self.status, self.boom = [], status, boom

    def __call__(self, req, timeout=None):
        self.calls.append(json.loads(req.data.decode("utf-8")))
        if self.boom:
            raise self.boom
        return FakeResp(self.status)


def run(tmp_path, opener, env=None, **kw):
    args = {**OK, **kw}
    argv = [f"--{k}={v}" for k, v in args.items()]
    argv += ["--status-file", str(tmp_path / "s" / "status.json"),
             "--run-url", "https://github.com/sdfvgv1994-bot/taco0525/actions/runs/1"]
    env = {"N8N_BOARD_WEBHOOK": URL} if env is None else env
    return ba.main(argv, env=env, opener=opener, now=datetime(2026, 10, 23, 12, 0))


def saved(tmp_path):
    return json.loads((tmp_path / "s" / "status.json").read_text(encoding="utf-8"))


def test_find_problems_all_ok():
    assert ba.find_problems(**OK) == []


def test_find_problems_monitor_step_fails_even_though_job_green():
    assert ba.find_problems(**{**OK, "monitor": "failure"}) == ["雲端看盤程式出錯"]


def test_find_problems_state_fails_skips_other_monitor_checks():
    p = ba.find_problems(**{**OK, "state": "failure", "monitor": "skipped", "save": "skipped"})
    assert p == ["讀不到雲端看盤的資料（monitor-data 分支）"]


def test_find_problems_build_failed_before_monitor():
    p = ba.find_problems(build="failure", deploy="skipped", state="", monitor="", save="")
    assert p == ["看板網頁產生失敗"]


def test_find_problems_deploy_failed():
    assert ba.find_problems(**{**OK, "deploy": "failure"}) == ["網頁發佈到 GitHub Pages 失敗"]


def test_find_problems_cancelled_is_undecided():
    assert ba.find_problems(**{**OK, "build": "cancelled"}) is None
    assert ba.find_problems(**{**OK, "deploy": "cancelled"}) is None


def test_ok_first_run_no_notify(tmp_path):
    rec = Recorder()
    assert run(tmp_path, rec) == 0
    assert rec.calls == []
    assert saved(tmp_path)["status"] == "ok"


def test_fail_notifies_once_then_quiet_then_recovers(tmp_path):
    rec = Recorder()
    run(tmp_path, rec, monitor="failure")
    assert len(rec.calls) == 1
    c = rec.calls[0]
    assert c["source"] == "taco0525-board" and c["status"] == "fail"
    assert c["problems"] == ["雲端看盤程式出錯"]
    assert c["time"] == "2026-10-23 12:00"

    run(tmp_path, rec, monitor="failure")          # 還是壞的：不再通知
    assert len(rec.calls) == 1

    run(tmp_path, rec, monitor="failure", save="failure")   # 多了新問題：再說一聲
    assert len(rec.calls) == 2

    run(tmp_path, rec)                             # 恢復：通知一次
    assert rec.calls[-1]["status"] == "recovered"
    run(tmp_path, rec)
    assert len(rec.calls) == 3


def test_post_failure_keeps_old_status_and_never_leaks_url(tmp_path, capsys):
    rec = Recorder(boom=OSError(f"cannot reach {URL}"))
    assert run(tmp_path, rec, monitor="failure") == 0
    out = capsys.readouterr().out
    assert URL not in out and "secret-path" not in out
    assert not (tmp_path / "s" / "status.json").exists()   # 沒記下來，下次會再試
    rec.boom = None
    run(tmp_path, rec, monitor="failure")
    assert len(rec.calls) == 2 and saved(tmp_path)["status"] == "fail"


def test_http_error_status_treated_as_failure(tmp_path):
    rec = Recorder(status=500)
    run(tmp_path, rec, monitor="failure")
    assert not (tmp_path / "s" / "status.json").exists()


def test_no_webhook_configured_records_but_does_not_send(tmp_path):
    rec = Recorder()
    run(tmp_path, rec, env={}, monitor="failure")
    assert rec.calls == []
    assert saved(tmp_path)["status"] == "fail"


def test_cancelled_run_changes_nothing(tmp_path):
    rec = Recorder()
    run(tmp_path, rec, monitor="failure")
    run(tmp_path, rec, build="cancelled", deploy="skipped")
    assert saved(tmp_path)["status"] == "fail" and len(rec.calls) == 1


def test_corrupt_status_file_treated_as_ok(tmp_path):
    f = tmp_path / "s" / "status.json"
    f.parent.mkdir(parents=True)
    f.write_text("{not json", encoding="utf-8")
    rec = Recorder()
    run(tmp_path, rec)
    assert rec.calls == [] and saved(tmp_path)["status"] == "ok"
