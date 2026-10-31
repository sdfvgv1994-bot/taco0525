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


def test_find_problems_cancelled_build_is_a_problem():
    # 超過 30 分鐘被強制停掉時 result 是 cancelled：這正是要知道的情況
    p = ba.find_problems(build="cancelled", deploy="skipped", state="", monitor="", save="")
    assert p == ["看板工作被中斷（可能跑超過 30 分鐘）"]
    assert ba.find_problems(**{**OK, "deploy": "cancelled"}) == ["網頁發佈被中斷"]


def test_find_problems_skipped_build_is_undecided():
    assert ba.find_problems(**{**OK, "build": "skipped"}) is None
    assert ba.find_problems(**{**OK, "build": ""}) is None


def test_ok_first_run_no_notify(tmp_path):
    rec = Recorder()
    assert run(tmp_path, rec) == 0
    assert rec.calls == []
    assert saved(tmp_path)["status"] == "ok"


def test_single_blip_is_not_reported(tmp_path):
    rec = Recorder()
    run(tmp_path, rec, monitor="failure")        # 壞一次
    run(tmp_path, rec)                           # 下一次就好了
    assert rec.calls == []
    assert saved(tmp_path) == {"status": "ok", "announced": [], "bad_runs": 0}


def test_fail_needs_two_runs_then_quiet_then_recovers(tmp_path):
    rec = Recorder()
    run(tmp_path, rec, monitor="failure")
    assert rec.calls == []                       # 第一次先觀察
    run(tmp_path, rec, monitor="failure")
    assert len(rec.calls) == 1
    c = rec.calls[0]
    assert c["source"] == "taco0525-board" and c["status"] == "fail"
    assert c["problems"] == ["雲端看盤程式出錯"]
    assert c["time"] == "2026-10-23 12:00"
    assert c["run_url"].startswith("https://github.com/sdfvgv1994-bot/taco0525/actions/runs/")

    run(tmp_path, rec, monitor="failure")          # 還是壞的：不再通知
    assert len(rec.calls) == 1

    run(tmp_path, rec, monitor="failure", save="failure")   # 多了新問題：再說一聲
    assert len(rec.calls) == 2

    run(tmp_path, rec, save="failure")             # 問題變少：不重發
    run(tmp_path, rec, monitor="failure")          # 之前通知過的問題又出現：不重發
    assert len(rec.calls) == 2

    run(tmp_path, rec)                             # 恢復：通知一次
    assert rec.calls[-1]["status"] == "recovered"
    run(tmp_path, rec)
    assert len(rec.calls) == 3


def test_different_problems_in_a_row_still_count(tmp_path):
    rec = Recorder()
    run(tmp_path, rec, monitor="failure")
    run(tmp_path, rec, deploy="failure")
    assert len(rec.calls) == 1 and rec.calls[0]["problems"] == ["網頁發佈到 GitHub Pages 失敗"]


def test_post_failure_keeps_old_status_and_never_leaks_url(tmp_path, capsys):
    rec = Recorder(boom=OSError(f"cannot reach {URL}"))
    run(tmp_path, rec, monitor="failure")
    before = saved(tmp_path)
    assert run(tmp_path, rec, monitor="failure") == 0     # 該通知了，但送不出去
    out = capsys.readouterr().out
    assert URL not in out and "secret-path" not in out
    assert saved(tmp_path) == before                        # 沒記下來，下次會再試
    rec.boom = None
    run(tmp_path, rec, monitor="failure")
    assert len(rec.calls) == 2 and saved(tmp_path)["status"] == "fail"   # 失敗那次＋重試成功那次


def test_http_error_status_treated_as_failure(tmp_path):
    rec = Recorder(status=500)
    run(tmp_path, rec, monitor="failure")
    run(tmp_path, rec, monitor="failure")
    assert saved(tmp_path)["status"] == "ok"


def test_no_webhook_configured_records_but_does_not_send(tmp_path):
    rec = Recorder()
    run(tmp_path, rec, env={}, monitor="failure")
    run(tmp_path, rec, env={}, monitor="failure")
    assert rec.calls == []
    assert saved(tmp_path)["status"] == "fail"


def test_skipped_run_changes_nothing(tmp_path):
    rec = Recorder()
    run(tmp_path, rec, monitor="failure")
    before = saved(tmp_path)
    run(tmp_path, rec, build="skipped", deploy="skipped")
    assert saved(tmp_path) == before and rec.calls == []


def test_corrupt_status_file_treated_as_ok(tmp_path):
    f = tmp_path / "s" / "status.json"
    f.parent.mkdir(parents=True)
    f.write_text("{not json", encoding="utf-8")
    rec = Recorder()
    run(tmp_path, rec)
    assert rec.calls == [] and saved(tmp_path)["status"] == "ok"
    f.write_text('{"status": "fail", "bad_runs": "x"}', encoding="utf-8")
    run(tmp_path, rec)
    assert saved(tmp_path)["status"] == "ok"


# ---------------- 直接推 Telegram（n8n 不用了之後的做法） ----------------
TG_ENV = {"TELEGRAM_TOKEN": "123:SECRET-TOKEN", "TELEGRAM_CHAT_ID": "999"}


class TgRecorder:
    """記下送去 Telegram 的網址和內容。"""
    def __init__(self, status=200, boom=None):
        self.calls, self.status, self.boom = [], status, boom

    def __call__(self, req, timeout=None):
        self.calls.append((req.full_url, json.loads(req.data.decode("utf-8"))))
        if self.boom:
            raise self.boom
        return FakeResp(self.status)


def test_telegram_fail_message_format(tmp_path):
    rec = TgRecorder()
    run(tmp_path, rec, env=TG_ENV, monitor="failure")
    run(tmp_path, rec, env=TG_ENV, monitor="failure")
    assert len(rec.calls) == 1
    url, body = rec.calls[0]
    assert url == "https://api.telegram.org/bot123:SECRET-TOKEN/sendMessage"
    assert body["chat_id"] == "999" and body["parse_mode"] == "HTML"
    assert body["disable_web_page_preview"] is True
    assert body["text"] == (
        "⚠️ <b>雲端看板出問題了</b>\n• 雲端看盤程式出錯\n🕒 2026-10-23 12:00\n"
        "https://github.com/sdfvgv1994-bot/taco0525/actions/runs/1")
    assert saved(tmp_path)["status"] == "fail"


def test_telegram_recovered_message(tmp_path):
    rec = TgRecorder()
    run(tmp_path, rec, env=TG_ENV, monitor="failure")
    run(tmp_path, rec, env=TG_ENV, monitor="failure")
    run(tmp_path, rec, env=TG_ENV)
    assert rec.calls[-1][1]["text"].startswith("✅ <b>雲端看板恢復正常</b>\n🕒 2026-10-23 12:00")


def test_telegram_message_escapes_html_and_ignores_foreign_run_url():
    text = ba.format_text("fail", ["<b>壞了</b> & 更壞"], "2026-10-23 12:00", "https://evil.example/x")
    assert "&lt;b&gt;壞了&lt;/b&gt; &amp; 更壞" in text
    assert "evil.example" not in text


def test_telegram_has_priority_over_n8n_and_does_not_double_send(tmp_path):
    rec = TgRecorder()
    env = {**TG_ENV, "N8N_BOARD_WEBHOOK": URL}
    run(tmp_path, rec, env=env, monitor="failure")
    run(tmp_path, rec, env=env, monitor="failure")
    assert len(rec.calls) == 1 and rec.calls[0][0].startswith("https://api.telegram.org/")


def test_telegram_needs_both_token_and_chat(tmp_path):
    rec = TgRecorder()
    only_token = {"TELEGRAM_TOKEN": "123:SECRET-TOKEN"}
    run(tmp_path, rec, env=only_token, monitor="failure")
    run(tmp_path, rec, env=only_token, monitor="failure")
    assert rec.calls == []
    assert saved(tmp_path)["status"] == "fail"       # 狀態照記，只是沒通知


def test_telegram_failure_never_leaks_token_and_retries_next_time(tmp_path, capsys):
    rec = TgRecorder(boom=OSError("cannot reach https://api.telegram.org/bot123:SECRET-TOKEN/sendMessage"))
    run(tmp_path, rec, env=TG_ENV, monitor="failure")
    before = saved(tmp_path)
    assert run(tmp_path, rec, env=TG_ENV, monitor="failure") == 0
    out = capsys.readouterr().out
    assert "SECRET-TOKEN" not in out and "<token>" in out
    assert saved(tmp_path) == before                  # 沒記下來，下次會再試
    rec.boom = None
    run(tmp_path, rec, env=TG_ENV, monitor="failure")
    assert len(rec.calls) == 2 and saved(tmp_path)["status"] == "fail"


def test_telegram_http_error_status_treated_as_failure(tmp_path):
    rec = TgRecorder(status=500)
    run(tmp_path, rec, env=TG_ENV, monitor="failure")
    run(tmp_path, rec, env=TG_ENV, monitor="failure")
    assert saved(tmp_path)["status"] == "ok"
