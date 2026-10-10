import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from cloud_monitor import run as cm

TW_OPEN = datetime(2026, 10, 8, 10, 0)      # 週四 10:00（台北）：台股開盤、美股休市
TW_NIGHT = datetime(2026, 10, 8, 23, 0)     # 週四 23:00（台北）：台股休市、美股開盤
SATURDAY = datetime(2026, 10, 10, 10, 0)

GOLDEN = [10, 10, 10, 10, 9, 9, 9, 9, 9, 9, 20]
DEATH = [10, 10, 10, 10, 11, 11, 11, 11, 11, 12, 2]


def write_config(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


BASE = '''
watchlist = ["2330", "AAPL"]
[settings]
strategies = ["ma"]
auto_trade = true
auto_buy_amount = 100000
stop_loss_pct = 0
short_ma = 2
long_ma = 5
'''


class Feed:
    def __init__(self):
        self.data = {}

    def __call__(self, name, settings, now, quotes):
        closes = self.data.get(name, [100.0] * 30)
        c = pd.Series(closes, dtype=float, index=pd.bdate_range(end="2026-10-08", periods=len(closes)))
        df = pd.DataFrame({"Open": c, "High": c, "Low": c, "Close": c, "Volume": 0})
        return name, df, float(closes[-1])


def run_once(tmp_path, config, feed, now, names=None):
    return cm.run(tmp_path / "state", tmp_path / "site", config, now=now, log=lambda *_: None,
                  monitor_kwargs={"fetcher": feed, "realtime": lambda items: {},
                                  "name_lookup": (names or {}).get}, fetch_names=False)


def test_default_config_is_valid():
    cfg, warnings = cm.load_config(cm.CONFIG)
    assert not warnings
    assert "2330" in cfg["watchlist"] and cfg["settings"]["strategies"][0] == "ma"
    assert cfg["limits"]["2330"]["upper"] > cfg["limits"]["2330"]["lower"]


def test_config_warnings_are_specific(tmp_path):
    cfg, warnings = cm.load_config(write_config(tmp_path / "c.toml", '''
watchlist = ["2330", "", "2330", "aapl"]
[settings]
strategies = ["nope", "kd"]
auto_trade = "yes"
stop_loss_pct = 500
short_ma = 30
long_ma = 20
[limits]
"2330" = { lower = 1200, upper = 900 }
[strategy_params]
rsi = { low = 25, foo = 1 }
'''))
    assert cfg["watchlist"] == ["2330", "AAPL"]
    assert cfg["settings"]["strategies"] == ["kd"] and cfg["settings"]["auto_trade"] is False
    assert cfg["settings"]["short_ma"] == 5 and "stop_loss_pct" not in cfg["settings"]
    assert cfg["limits"] == {} and cfg["strategy_params"]["rsi"] == {"low": 25.0}
    text = "\n".join(warnings)
    for word in ("nope", "auto_trade", "stop_loss_pct", "short_ma", "2330", "foo", "看不懂"):
        assert word in text


def test_exchange_mapping():
    assert [cm.exchange_of(x) for x in ("2330", "6488", "加權", "AAPL", "BTC", "^N225", "TWD=X")] == \
        ["TW", "TW", "TW", "US", "CRYPTO", "JP", "FX"]


def test_trades_only_during_that_markets_session(tmp_path):
    cfg = write_config(tmp_path / "c.toml", BASE)
    feed = Feed()
    feed.data = {"2330": GOLDEN, "AAPL": GOLDEN}
    data = run_once(tmp_path, cfg, feed, TW_OPEN, {"2330": "台積電"})
    held = {p["code"] for p in data["positions"]}
    assert held == {"2330"}                                  # 美股這時休市，不交易
    assert data["trades"][0]["reason"].startswith("自動")
    w = {x["code"]: x for x in data["watch"]}
    assert w["2330"]["name"] == "台積電" and w["2330"]["signals"][0]["dir"] == 1
    assert w["AAPL"]["price"] == 20 and w["AAPL"]["held"] is False   # 休市時照樣顯示報價
    data = run_once(tmp_path, cfg, feed, TW_NIGHT)
    assert {p["code"] for p in data["positions"]} == {"2330", "AAPL"}  # 晚上換美股開盤


def test_weekend_does_nothing(tmp_path):
    cfg = write_config(tmp_path / "c.toml", BASE)
    feed = Feed()
    feed.data = {"2330": GOLDEN, "AAPL": GOLDEN}
    data = run_once(tmp_path, cfg, feed, SATURDAY)
    assert not data["trades"] and not data["alerts"]


def test_state_persists_and_equity_accumulates(tmp_path):
    cfg = write_config(tmp_path / "c.toml", BASE)
    feed = Feed()
    feed.data = {"2330": GOLDEN}
    run_once(tmp_path, cfg, feed, TW_OPEN)
    feed.data = {"2330": DEATH}
    data = run_once(tmp_path, cfg, feed, datetime(2026, 10, 9, 10, 0))
    assert [t["side"] for t in reversed(data["trades"])] == ["買進", "賣出"]
    assert len(data["equity"]) == 2 and data["account"]["total"] != 1_000_000
    acct = json.loads((tmp_path / "state" / "account.json").read_text(encoding="utf-8"))
    assert acct["trades"] and not acct["positions"]


def test_removed_but_held_stock_is_still_tracked(tmp_path):
    feed = Feed()
    feed.data = {"2330": GOLDEN}
    run_once(tmp_path, write_config(tmp_path / "c.toml", BASE), feed, TW_OPEN)
    cfg2 = write_config(tmp_path / "c2.toml", BASE.replace('["2330", "AAPL"]', '["AAPL"]'))
    feed.data = {"2330": DEATH}
    data = run_once(tmp_path, cfg2, feed, datetime(2026, 10, 9, 10, 0))
    w = {x["code"]: x for x in data["watch"]}
    assert w["2330"]["held_only"] is True                               # 已移出自選股但還有持股
    assert any(t["side"] == "賣出" and t["code"] == "2330" for t in data["trades"])   # 照樣賣出


def test_broken_config_uses_last_good(tmp_path):
    feed = Feed()
    run_once(tmp_path, write_config(tmp_path / "c.toml", BASE), feed, TW_OPEN)
    broken = write_config(tmp_path / "bad.toml", 'watchlist = ["2330"\n[settings')
    data = run_once(tmp_path, broken, feed, TW_OPEN)
    assert data["config_errors"] and "格式錯誤" in data["config_errors"][0]
    assert {w["code"] for w in data["watch"]} == {"2330", "AAPL"}       # 沿用上一次的自選股


def test_outputs_page_files(tmp_path):
    run_once(tmp_path, write_config(tmp_path / "c.toml", BASE), Feed(), TW_OPEN)
    site = tmp_path / "site"
    data = json.loads((site / "data.json").read_text(encoding="utf-8"))
    assert (site / "index.html").exists()
    assert data["edit_url"].startswith("https://github.com/") and data["exchanges"]["TW"]["tz"] == "Asia/Taipei"
    assert data["updated_utc"] == "2026-10-08T02:00:00Z"                 # 台北 10:00 = UTC 02:00


def test_prefetch_names_from_twse_titles(tmp_path, monkeypatch):
    from common import twse
    from stock_monitor.engine import Monitor
    from stock_monitor.storage import Store

    class FakeClient:
        def __init__(self):
            self.names, self.calls = {}, []

        def cached_name(self, code):
            return self.names.get(code)

        def stock_month(self, code, y, m):
            self.calls.append(("tse", code))
            if code == "2330":
                self.names[code] = "台積電"
                return pd.DataFrame({"Close": [1.0]})
            return pd.DataFrame()

        def tpex_month(self, code, y, m):
            self.calls.append(("otc", code))
            self.names[code] = "環球晶"
            return pd.DataFrame({"Close": [1.0]})

    fake = FakeClient()
    monkeypatch.setattr(twse, "client", lambda: fake)
    m = Monitor(Store(tmp_path), fetcher=Feed(), realtime=lambda items: {})
    m.watchlist = {"2330": {}, "6488": {}, "AAPL": {}}
    m.names = {}
    cm.prefetch_names(m, TW_OPEN, log=lambda *_: None)
    assert m.names == {"2330": "台積電", "6488": "環球晶"}
    assert ("tse", "AAPL") not in fake.calls
    fake.calls.clear()
    cm.prefetch_names(m, TW_OPEN, log=lambda *_: None)          # 已經有名字的不再查
    assert fake.calls == []


# ---------- 手機推播 ----------
from cloud_monitor import notify  # noqa: E402
from stock_monitor.signals import Alert  # noqa: E402


def test_pick_alerts_by_setting():
    alerts = [Alert("2330", "buy", "買 ma", "ma"), Alert("2330", "sell", "賣 macd", "macd"),
              Alert("2330", "above", "突破", ""), Alert("2330", "stop", "停損", "")]
    kinds = lambda cfg: [(a.kind, a.strategy) for a in notify.pick_alerts(alerts, "ma", cfg)]
    assert kinds({"signals": "primary"}) == [("buy", "ma"), ("above", ""), ("stop", "")]
    assert kinds({"signals": "all"})[:2] == [("buy", "ma"), ("sell", "macd")]
    assert kinds({"signals": "none", "price_alerts": False}) == []


def test_build_message():
    assert notify.build_message([], [], {}, "10/08 10:00") is None
    t = {"side": "買進", "code": "2330", "shares": 1000, "price": 1000.0, "reason": "自動：均線交叉買進"}
    msg = notify.build_message([t], [Alert("2330", "buy", "2330 買進訊號", "ma")], {"2330": "台積電"},
                               "10/08 10:00")
    assert msg["title"] == "雲端看盤：1 筆成交、1 則提醒（10/08 10:00）"
    assert "台積電" in msg["message"] and "1,000 股" in msg["message"]
    assert msg["priority"] == 4 and msg["click"].endswith("/monitor/")
    long = notify.build_message([], [Alert("X", "above", "很長的訊息" * 200, "")], {}, "t")
    assert len(long["message"].encode("utf-8")) < 3700


def test_send_uses_json_and_env():
    calls = []

    class R:
        status_code = 200

    def post(url, json=None, headers=None, timeout=None):
        calls.append((url, json, headers))
        return R()
    assert "不推播" in notify.send({"title": "t"}, post=post, env={})
    assert notify.send({"title": "台積電"}, post=post,
                       env={"NTFY_TOPIC": "abc", "NTFY_TOKEN": "tk", "NTFY_SERVER": "https://n.example/"}) == "已推播"
    url, body, headers = calls[0]
    assert url == "https://n.example/" and body == {"topic": "abc", "title": "台積電"}
    assert headers["Authorization"] == "Bearer tk"


def test_run_pushes_once_per_new_event_and_welcome_once(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "send", lambda payload, **kw: sent.append(payload) or "已推播")
    monkeypatch.setenv("NTFY_TOPIC", "secret-topic")
    cfg = write_config(tmp_path / "c.toml", BASE)
    feed = Feed()
    feed.data = {"2330": GOLDEN}
    data = run_once(tmp_path, cfg, feed, TW_OPEN)
    assert [p["title"].split("（")[0] for p in sent] == ["雲端看盤：手機推播設定成功", "雲端看盤：1 筆成交、1 則提醒"]
    assert data["settings"]["notify"]["status"] == "已開啟"
    state = (tmp_path / "state" / "notify.json").read_text(encoding="utf-8")
    assert "secret-topic" not in state                       # 公開的資料分支只存雜湊
    sent.clear()
    run_once(tmp_path, cfg, feed, datetime(2026, 10, 8, 10, 15))
    assert sent == []                                        # 同一天同一個提醒不會再推


def test_run_survives_push_failure(tmp_path, monkeypatch):
    def boom(payload, **kw):
        raise RuntimeError("ntfy 回應 HTTP 429")
    monkeypatch.setattr(notify, "send", boom)
    monkeypatch.setenv("NTFY_TOPIC", "t")
    feed = Feed()
    feed.data = {"2330": GOLDEN}
    data = run_once(tmp_path, write_config(tmp_path / "c.toml", BASE), feed, TW_OPEN)
    assert data["trades"] and "推播失敗" in data["settings"]["notify"]["status"]


def test_notify_disabled_or_not_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(notify, "send", lambda *a, **k: pytest.fail("不該推播"))
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    feed = Feed()
    feed.data = {"2330": GOLDEN}
    data = run_once(tmp_path, write_config(tmp_path / "c.toml", BASE), feed, TW_OPEN)
    assert "還沒設定" in data["settings"]["notify"]["status"]
    monkeypatch.setenv("NTFY_TOPIC", "t")
    off = write_config(tmp_path / "off.toml", BASE + "\n[notify]\nenabled = false\n")
    data = run_once(tmp_path / "b", off, feed, TW_OPEN)
    assert data["settings"]["notify"]["status"] == "關閉"


def test_notify_config_validation(tmp_path):
    cfg, warnings = cm.load_config(write_config(tmp_path / "c.toml", BASE + '''
[notify]
enabled = "yes"
signals = "some"
'''))
    assert cfg["notify"]["enabled"] is True and cfg["notify"]["signals"] == "primary"
    assert any("enabled" in w for w in warnings) and any("signals" in w for w in warnings)
