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
    """假行情：最後一根 K 棒預設是執行當天（台北日期），end 可指定別的日期（模擬資料還沒更新）。"""
    def __init__(self):
        self.data = {}
        self.end = {}
        self.calls = []

    def __call__(self, name, settings, now, quotes):
        self.calls.append(name)
        if isinstance(self.data.get(name), Exception):
            raise self.data[name]
        closes = self.data.get(name, [100.0] * 30)
        end = self.end.get(name, now.date())
        c = pd.Series(closes, dtype=float, index=pd.bdate_range(end=end, periods=len(closes)))
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


# ---------- 審查找到的問題 ----------
def strict_json(path):
    """瀏覽器的 JSON.parse 不接受 NaN / Infinity。"""
    return json.loads(path.read_text(encoding="utf-8"),
                      parse_constant=lambda c: pytest.fail(f"data.json 裡有 {c}，網頁會打不開"))


def test_holiday_or_stale_bar_does_not_trade(tmp_path):
    """時鐘上是交易時間、但還沒有今天的 K 棒（國定假日、Yahoo 延遲）：不能把昨天的交叉再做一次。"""
    feed = Feed()
    feed.data = {"2330": GOLDEN}
    feed.end = {"2330": "2026-10-08"}
    holiday = datetime(2026, 10, 9, 10, 0)            # 週五國慶補假
    data = run_once(tmp_path, write_config(tmp_path / "c.toml", BASE), feed, holiday)
    assert not data["trades"] and not data["alerts"]
    w = {x["code"]: x for x in data["watch"]}
    assert w["2330"]["stale"] is True and w["2330"]["bar_date"] == "2026-10-08"
    assert w["2330"]["price"] == 20                   # 報價照樣顯示
    assert any("還沒有今天的資料" in n and "2330（10/08）" in n for n in data["notes"])


def test_flaky_realtime_does_not_rebuy_at_yesterdays_price(tmp_path):
    cfg = write_config(tmp_path / "c.toml", BASE)
    feed = Feed()
    feed.data = {"2330": GOLDEN}
    run_once(tmp_path, cfg, feed, TW_OPEN)                              # 週四：黃金交叉買進
    feed.data = {"2330": DEATH}
    run_once(tmp_path, cfg, feed, datetime(2026, 10, 9, 9, 0))          # 週五 9:00 有即時價：死亡交叉賣出
    feed.data, feed.end = {"2330": GOLDEN}, {"2330": "2026-10-08"}      # 9:15 即時報價斷線、Yahoo 還是昨天
    data = run_once(tmp_path, cfg, feed, datetime(2026, 10, 9, 9, 15))
    assert [t["side"] for t in reversed(data["trades"])] == ["買進", "賣出"]


def test_us_alert_not_repeated_after_taipei_midnight(tmp_path):
    """美股一根 K 棒跨過台北午夜：午夜後不能再響一次，停損賣出後也不能用同一個訊號再買回來。"""
    cfg = write_config(tmp_path / "c.toml", BASE.replace("stop_loss_pct = 0", "stop_loss_pct = 8"))
    feed = Feed()
    feed.end = {"AAPL": "2026-10-08"}                                   # 紐約還是 10/08
    feed.data = {"AAPL": GOLDEN}
    data = run_once(tmp_path, cfg, feed, datetime(2026, 10, 8, 22, 0))
    assert [t["side"] for t in data["trades"]] == ["買進"]
    feed.data = {"AAPL": GOLDEN[:-1] + [18]}                            # 跌 10%：停損
    data = run_once(tmp_path, cfg, feed, datetime(2026, 10, 8, 23, 45))
    assert data["trades"][0]["reason"] == "自動：停損"
    feed.data = {"AAPL": GOLDEN}
    data = run_once(tmp_path, cfg, feed, datetime(2026, 10, 9, 0, 15))  # 台北過午夜，紐約同一天
    assert data["new_alerts"] == 0 and len(data["trades"]) == 2


def test_exchange_of_foreign_suffixes_and_unknown(tmp_path):
    codes = ("7203.T", "0700.HK", "005930.KS", "600519.SS", "BRK-B", "VOD.L", "^HSCE", "ABC.XYZ")
    assert [cm.exchange_of(x) for x in codes] == ["JP", "HK", "KR", "CN", "US", "UK", None, None]
    assert cm.is_active("7203.T", datetime(2026, 10, 8, 9, 0))          # 台北 9:00 = 東京 10:00
    assert not cm.is_active("7203.T", datetime(2026, 10, 8, 22, 0))
    assert not cm.is_active("^HSCE", TW_OPEN) and cm.session_date("^HSCE", TW_OPEN) is None
    assert cm.session_date("AAPL", datetime(2026, 10, 9, 0, 15)) == "2026-10-08"
    _, warnings = cm.load_config(write_config(tmp_path / "c.toml", 'watchlist = ["2330", "^HSCE"]\n'))
    assert any("^HSCE" in w and "不知道" in w for w in warnings)


def test_bad_strategy_params_are_reported(tmp_path):
    cfg, warnings = cm.load_config(write_config(tmp_path / "c.toml", BASE + '''
[strategy_params]
ma = { short = 30 }
rsi = { n = 0 }
macd = { fast = 30 }
kd = { low = "20" }
breakout = { entry = 400 }
bbands = { n = 10 }
'''))
    assert cfg["strategy_params"] == {"kd": {}, "bbands": {"n": 10.0}}
    text = "\n".join(warnings)
    assert "均線交叉 的參數不能用" in text and "短均線天數必須小於長均線" in text
    assert "RSI 超買超賣 的參數不能用（n 要在 1～240 之間）" in text
    assert "MACD 的參數不能用" in text and "KD 指標 的 low 要填數字" in text
    assert "突破新高 的參數不能用" in text
    _, warnings = cm.load_config(write_config(tmp_path / "d.toml", BASE.replace("long_ma = 5", "long_ma = 300")))
    assert any("long_ma 要填 2～240" in w for w in warnings)


def test_short_history_gives_valid_json_and_note(tmp_path):
    feed = Feed()
    feed.data = {"2330": [10, 11, 12, 13]}                              # 剛上市：比長均線天數還少
    data = run_once(tmp_path, write_config(tmp_path / "c.toml", BASE), feed, TW_OPEN)
    saved = strict_json(tmp_path / "site" / "data.json")
    w = {x["code"]: x for x in saved["watch"]}
    assert w["2330"]["long_ma"] is None and w["2330"]["trend"] is None and w["2330"]["price"] == 13
    assert any("均線交叉" in n and "無法判斷 2330" in n for n in data["notes"])


def test_failed_quote_uses_last_price_and_skips_equity_point(tmp_path):
    cfg = write_config(tmp_path / "c.toml", BASE)
    feed = Feed()
    feed.data = {"2330": GOLDEN}
    first = run_once(tmp_path, cfg, feed, TW_OPEN)
    feed.data = {"2330": RuntimeError("Yahoo 限流")}
    data = run_once(tmp_path, cfg, feed, datetime(2026, 10, 8, 10, 15))
    [p] = data["positions"]
    assert p["price"] == 20 and p["price_time"] == "2026-10-08 10:00"
    assert data["account"]["total"] == first["account"]["total"]        # 不會被當成成本價
    assert len(data["equity"]) == 1 and any("抓不到報價" in n for n in data["notes"])


def test_typos_and_misplaced_keys_are_explained(tmp_path):
    cfg, warnings = cm.load_config(write_config(tmp_path / "c.toml", '''
Watchlist = ["2330"]
[settings]
autotrade = false
stop_loss = 5
[limits]
"TSLA" = { upper = 400 }
"2330" = { low = 900 }
[strategy_params]
rsi = { low = 25 }
short_ma = 10
'''))
    text = "\n".join(warnings)
    assert cfg["settings"]["auto_trade"] is False                       # 打錯字：為了安全先關閉
    for part in ("是不是要寫「watchlist」", "是不是要寫「auto_trade」", "是不是要寫「stop_loss_pct」",
                 "「short_ma」寫在[strategy_params] 裡了，要搬到 [settings] 底下",
                 "「TSLA」有設上下限，但不在自選股裡", "是不是要寫「lower」", "沒有設定自選股"):
        assert part in text
    cfg, warnings = cm.load_config(write_config(tmp_path / "d.toml",
                                                'watchlist = ["2330"]\n[Settings]\nauto_trade = true\n'))
    assert cfg["settings"]["auto_trade"] is False and any("是不是要寫「settings」" in w for w in warnings)
    cfg, warnings = cm.load_config(write_config(tmp_path / "e.toml", BASE.replace("auto_trade = true\n", "")))
    assert "auto_trade" not in cfg["settings"] and not warnings          # [notify] 的 trades 不算打錯字


def test_wrong_types_do_not_crash(tmp_path):
    cfg, warnings = cm.load_config(write_config(tmp_path / "c.toml", '''
watchlist = ["2330"]
limits = ["2330"]
strategy_params = "rsi"
notify = 3
'''))
    assert cfg["watchlist"] == ["2330"] and cfg["limits"] == {} and cfg["strategy_params"] == {}
    text = "\n".join(warnings)
    assert "[limits] 的格式不對" in text and "[strategy_params] 的格式不對" in text and "[notify] 的格式不對" in text


def test_fullwidth_bom_and_percent_are_fixed(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('﻿# 註解裡的全形，不用管\nwatchlist = [“2330”，“AAPL”]\n[settings]\n'
                 'stop_loss_pct = ８%\nauto_trade　= false\n', encoding="utf-8")
    cfg, warnings = cm.load_config(p)
    assert cfg["watchlist"] == ["2330", "AAPL"]
    assert cfg["settings"]["stop_loss_pct"] == 8 and cfg["settings"]["auto_trade"] is False
    lines = [w.split(" 行")[0] for w in warnings]
    assert lines == ["第 2", "第 4", "第 5"]                             # 註解那行不算


def test_syntax_error_is_explained_in_chinese(tmp_path):
    with pytest.raises(cm.ConfigError) as e:
        cm.load_config(write_config(tmp_path / "c.toml", 'watchlist = ["2330"]\n[settings]\nstop_loss_pct = 8 趴\n'))
    msg = str(e.value)
    assert "第 3 行「stop_loss_pct = 8 趴」" in msg and "一行只能寫一個設定" in msg


def test_broken_config_on_first_run_does_not_auto_trade(tmp_path):
    feed = Feed()
    feed.data = {"2330": GOLDEN}
    broken = write_config(tmp_path / "bad.toml", 'watchlist = ["2330"\n[settings')
    data = run_once(tmp_path, broken, feed, TW_OPEN)
    assert not data["trades"] and data["settings"]["auto_trade"] is False
    assert "還沒有上一次正確的設定" in data["config_errors"][0]
    assert "沿用上一次正確的設定；" not in data["config_errors"][0]


def test_keep_last_republishes_previous_page(tmp_path):
    from cloud_monitor import keep_last as kl
    assert kl.LIVE_DATA == notify.MONITOR_URL + "data.json"
    prev = {"updated": "2026-10-08 10:00:00", "watch": [], "run_error": "上一次的錯誤"}
    data = kl.keep_last(tmp_path / "out", kl.reason_for("success", "failure", "success"),
                        get=lambda url: dict(prev), log=lambda *_: None)
    assert (tmp_path / "out" / "index.html").exists()
    assert "程式這次執行失敗" in data["run_error"] and "10-08 10:00" in data["run_error"]
    assert strict_json(tmp_path / "out" / "data.json") == data

    def offline(url):
        raise OSError("HTTP 404")
    data = kl.keep_last(tmp_path / "out", kl.reason_for("failure", "skipped", "skipped"),
                        get=offline, log=lambda *_: None)
    assert "讀不到帳戶資料" in data["fatal"] and "404" in data["fatal"]
    assert "存檔失敗" in kl.reason_for("success", "success", "failure")


def test_workflow_keeps_last_page_and_detects_missing_branch():
    yaml = pytest.importorskip("yaml")
    wf = yaml.safe_load((Path(cm.ROOT) / ".github/workflows/board.yml").read_text(encoding="utf-8"))
    steps = {s.get("id") or s.get("name"): s for s in wf["jobs"]["build"]["steps"]}
    assert "--exit-code" in steps["state"]["run"] and '"$rc" = 2' in steps["state"]["run"]
    assert steps["save"]["if"] == "steps.state.outcome == 'success'"
    keep = steps["雲端看盤沒成功時，保留上一次的網頁"]
    assert "keep_last.py" in keep["run"] and "steps.save.outcome != 'success'" in keep["if"]
    names = [s.get("id") or s.get("name") or s.get("uses") for s in wf["jobs"]["build"]["steps"]]
    assert names.index("雲端看盤沒成功時，保留上一次的網頁") < names.index("actions/upload-pages-artifact@v3")
