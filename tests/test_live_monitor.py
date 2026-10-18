"""桌面版 / 手機版共用的看盤服務（stock_monitor/live.py）、移動停損與支撐線、舊版存檔搬家、手機版 API。"""
import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta

import pandas as pd
import pytest

from stock_monitor.engine import Monitor
from stock_monitor.import_old import convert, import_old, is_old_dir
from stock_monitor.live import LiveMonitor
from stock_monitor.signals import evaluate, risk_levels, snapshot, update_peak
from stock_monitor.storage import DEFAULT_SETTINGS, Store

NOW = datetime(2026, 10, 8, 10, 0)


def _df(vals):
    c = pd.Series(vals, dtype=float, index=pd.bdate_range(end="2026-10-08", periods=len(vals)))
    return pd.DataFrame({"Open": c, "High": c, "Low": c, "Close": c})


def _settings(**kw):
    return {**DEFAULT_SETTINGS, "short_ma": 2, "long_ma": 5, **kw}


class FakeFeed:
    def __init__(self):
        self.data = {}

    def __call__(self, name, settings, now, quotes):
        if name not in self.data:
            raise RuntimeError(f"抓不到「{name}」的資料")
        closes, price = self.data[name]
        return name + ".TW", _df(closes), price


def make_live(tmp_path, **settings):
    feed = FakeFeed()
    m = Monitor(Store(tmp_path), fetcher=feed, realtime=lambda items: {}, name_lookup=lambda c: None)
    m.settings.update(short_ma=2, long_ma=5, stop_loss_pct=0, **settings)
    live = LiveMonitor(tmp_path, monitor=m, source="測試", cloud=False)
    live.m.settings["source"] = "yahoo"   # 測試不查證交所即時報價
    return live, feed


GOLDEN = [10, 10, 10, 10, 9, 9, 9, 9, 9, 9, 20]


# ---------- 移動停損、支撐線 ----------
def test_trailing_stop_follows_peak():
    pos = {"shares": 100, "avg_cost": 100}
    update_peak({}, pos, 130)
    update_peak({}, pos, 120)
    assert pos["peak"] == 130
    r = risk_levels({}, pos, 0, 10)
    assert r["trail_price"] == pytest.approx(117)
    snap = snapshot("X", _df([125] * 30), 116, _settings())
    kinds = [a.kind for a in evaluate(snap, {}, pos, 0, 10)]
    assert kinds == ["trail"]
    # 每檔自己的設定優先：這檔移動停損 20% → 不觸發
    assert evaluate(snap, {"trail": 20}, pos, 0, 10) == []


def test_manual_cost_without_paper_position():
    limits = {"cost": 100, "stop": 5}
    snap = snapshot("X", _df([100] * 30), 94, _settings())
    assert [a.kind for a in evaluate(snap, limits, None, 0)] == ["stop"]
    update_peak(limits, None, 150)
    assert limits["peak"] == 150


def test_support_break_only_when_crossing():
    snap = snapshot("X", _df([100] * 30), 95, _settings())
    [a] = evaluate(snap, {"support": [98, 90]}, None, 0)
    assert a.kind == "support" and "98" in a.message and "90" not in a.message.split("，")[0]
    still_below = snapshot("X", _df([100] * 28 + [95, 95]), 94, _settings())
    assert evaluate(still_below, {"support": [98]}, None, 0) == []   # 昨天就在線下，不再提醒


def test_auto_sell_on_trailing_stop(tmp_path):
    live, feed = make_live(tmp_path, auto_trade=True, trail_stop_pct=10, strategies=["breakout"])
    m = live.m
    m.add("2330")
    feed.data["2330"] = ([100] * 30, 100)
    m.refresh(NOW)
    m.manual_buy("2330", 100)
    feed.data["2330"] = ([100] * 30, 150)
    m.refresh(NOW + timedelta(minutes=1))
    assert m.account.positions["2330"]["peak"] == 150
    feed.data["2330"] = ([100] * 29 + [150], 130)
    res = m.refresh(NOW + timedelta(minutes=2))
    assert [t["reason"] for t in res.trades] == ["自動：移動停損"]
    assert not m.account.holds("2330")


# ---------- 看盤服務 ----------
def test_live_refresh_state_and_logs(tmp_path):
    live, feed = make_live(tmp_path, auto_trade=True)
    with live.lock:
        live.add("2330")
        live.add("BAD")
        with pytest.raises(ValueError):
            live.add("2330")
    feed.data["2330"] = (GOLDEN, 20)
    live.refresh()
    with live.lock:
        st = live.state()
    good = next(s for s in st["stocks"] if s["ticker"] == "2330")
    bad = next(s for s in st["stocks"] if s["ticker"] == "BAD")
    assert good["symbol"] == "2330.TW" and good["row"]["price"] == 20
    assert good["row"]["alerts"] == ["買進：均線交叉"]
    assert good["position"]["shares"] > 0          # 主策略買進訊號 → 自動買進
    assert "error" in bad["row"]
    assert st["settings"]["short"] == 2 and st["settings"]["strategies"] == ["ma"]
    kinds = [l["kind"] for l in st["logs"]]
    assert "alert" in kinds and "trade" in kinds
    assert st["trades"][0]["ticker"] == "2330" and st["trades"][0]["side"] == "買進"
    assert st["account"]["equity"] == pytest.approx(st["account"]["cash"] + st["account"]["market_value"])


def test_live_operations(tmp_path):
    live, feed = make_live(tmp_path)
    with live.lock:
        live.add("2330")
    feed.data["2330"] = ([100] * 30, 100)
    live.refresh()
    with live.lock:
        live.order("2330", "buy", 1000, "手動")
        with pytest.raises(ValueError):
            live.remove("2330")                         # 有持股不能刪
        live.set_alerts("2330", {"above": "120", "below": "", "stop": "5", "trail": "", "cost": "999"})
        entry = live.find("2330")
        assert entry["upper"] == 120 and entry["stop"] == 5 and "cost" not in entry   # 有持股時成本不存
        with pytest.raises(ValueError):
            live.set_alerts("2330", {"above": "abc"})
        with pytest.raises(ValueError):
            live.set_alerts("2330", {"above": "90", "below": "100"})
        with pytest.raises(ValueError):
            live.update_settings({"short": 30, "long": 20})
        with pytest.raises(ValueError):
            live.update_settings({"strategies": ["nope"]})
        live.update_settings({"short": 10, "long": 30, "budget": 50000, "auto": False,
                              "strategies": ["macd", "ma"], "trail_stop_pct": 12})
        s = live.m.settings
        assert (s["short_ma"], s["long_ma"], s["auto_buy_amount"], s["auto"]) == (10, 30, 50000, False)
        assert s["strategies"] == ["macd", "ma"] and s["trail_stop_pct"] == 12
        live.set_hlines("2330", [95, "90"])
        assert live.find("2330")["support"] == [95, 90]
        live.order("2330", "sell", 1000)
        live.remove("2330")
        assert live.m.watchlist == {}
        live.reset_account()
        assert live.m.account.cash == live.m.account.initial
    saved = Store(tmp_path).settings()
    assert saved["strategies"] == ["macd", "ma"]          # 存檔，文字版和下次啟動都會用


# ---------- 舊版存檔搬家 ----------
OLD_WATCH = {
    "settings": {"short": 23, "long": 60, "interval": 60, "auto": True, "auto_trade": True,
                 "budget": 100000, "news_enabled": True, "news_time": "08:00"},
    "stocks": [
        {"ticker": "2330", "symbol": "2330.TW", "fired": {}},
        {"ticker": "2317", "symbol": "2317.TW", "fired": {"below": "2026-10-13"}, "cost": 249.0,
         "peak": 260.0, "above": 3000.0, "below": 2000.0, "stop": 5.0, "trail": 15.0, "hlines": [240, 230]},
        {"ticker": "6488", "cost": 1115.0, "peak": 1130.0},
    ],
}
OLD_PAPER = {
    "cash": 654584.99, "initial": 1000000,
    "positions": {"2317": {"shares": 100, "cost": 249.0}},
    "trades": [
        {"time": "2026-10-09 23:59:23", "ticker": "2317", "side": "買進", "shares": 100, "price": 249.0,
         "fee": 35, "tax": 0, "reason": "手動", "pnl": None},
        {"time": "2026-10-10 10:00:00", "ticker": "0050", "side": "賣出", "shares": 10, "price": 100.0,
         "fee": 20, "tax": 3, "reason": "手動", "pnl": -50.0},
    ],
}


def test_convert_old_files():
    settings, watch, account = convert(OLD_WATCH, OLD_PAPER)
    assert (settings["short_ma"], settings["long_ma"], settings["auto_buy_amount"]) == (23, 60, 100000)
    assert settings["strategies"] == ["ma"] and settings["auto_trade"] is True
    assert watch["2317"] == {"lower": 2000.0, "upper": 3000.0, "stop": 5.0, "trail": 15.0, "support": [240, 230]}
    assert watch["6488"]["cost"] == 1115.0 and watch["6488"]["peak"] == 1130.0   # 手動成本（沒有模擬持股）
    pos = account["positions"]["2317"]
    assert pos["shares"] == 100 and pos["avg_cost"] == pytest.approx(249 + 35.48 / 100)
    assert pos["peak"] == 260.0 and pos["tw"] is True
    assert account["cash"] == 654584.99
    buy, sell = account["trades"]
    assert buy["code"] == "2317" and buy["cash_flow"] == -24935 and "pnl" not in buy
    assert sell["cash_flow"] == 977 and sell["pnl"] == -50.0


def test_import_old_once(tmp_path):
    old, data = tmp_path / "old", tmp_path / "data"
    old.mkdir()
    (old / "watchlist.json").write_text(json.dumps(OLD_WATCH), encoding="utf-8")
    (old / "paper_account.json").write_text(json.dumps(OLD_PAPER), encoding="utf-8")
    (old / "web_config.json").write_text('{"key": "abc"}', encoding="utf-8")
    assert is_old_dir(old) and not is_old_dir(data)
    msg = import_old(old, data)
    assert msg and "手機書籤" in "".join(msg)
    m = Monitor(Store(data))
    assert list(m.watchlist) == ["2330", "2317", "6488"]
    assert m.account.holds("2317") and m.settings["long_ma"] == 60
    assert import_old(old, data) == []                    # 已經有存檔就不再搬


# ---------- 手機版 API ----------
def test_web_api(tmp_path):
    from http.server import ThreadingHTTPServer

    from stock_monitor import web_app

    live, feed = make_live(tmp_path)
    with live.lock:
        live.add("2330")
    feed.data["2330"] = ([100] * 30, 100)
    live.refresh()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), web_app.make_handler(live, "k3y"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def post(path, body):
        req = urllib.request.Request(f"{base}{path}?key=k3y", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            return json.loads(urllib.request.urlopen(req).read())
        except urllib.error.HTTPError as e:
            return json.loads(e.read())

    try:
        page = urllib.request.urlopen(f"{base}/?key=k3y").read().decode()
        assert "stratList" in page
        st = json.loads(urllib.request.urlopen(f"{base}/api/state?key=k3y").read())
        assert st["stocks"][0]["row"]["price"] == 100 and st["is_local"] is True
        assert post("/api/order", {"ticker": "2330", "side": "buy", "shares": 10}) == {"ok": True}
        assert "error" in post("/api/order", {"ticker": "2330", "side": "buy", "shares": 10 ** 9})
        assert post("/api/strategies", {"strategies": ["rsi", "ma"]}) == {"ok": True}
        assert post("/api/hlines", {"ticker": "2330", "prices": [90]}) == {"ok": True}
        assert live.m.settings["strategies"] == ["rsi", "ma"] and live.find("2330")["support"] == [90]
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(f"{base}/api/state")
        assert e.value.code == 403
    finally:
        srv.shutdown()


# ---------- 雲端帳戶模式（電腦版用雲端看盤的帳戶） ----------
import base64  # noqa: E402

from stock_monitor.cloud_link import set_settings, set_watchlist  # noqa: E402

CLOUD_CONFIG = '''# 說明
watchlist = ["2330", "AAPL"]

[settings]
# 策略
  strategies = ["smc", "ma"]
auto_trade = true          # 自動模擬交易
auto_buy_amount = 100000   # 每次買進
stop_loss_pct = 8
short_ma = 5
long_ma = 20

# 上下限
[limits]
"2330" = { lower = 1800, upper = 3000 }
# "AAPL" = { upper = 400 }

[strategy_params]
'''


def test_config_editing_keeps_comments():
    t = set_watchlist(CLOUD_CONFIG, [("2330", {"lower": 1800, "upper": 3000, "support": [2500]}),
                                     ("2603", {"lower": None, "upper": 250})])
    assert 'watchlist = ["2330", "2603"]' in t and '"2330" = { lower = 1800, upper = 3000, support = [2500] }' in t
    assert '"2603" = { upper = 250 }' in t and '# "AAPL" = { upper = 400 }' in t and "# 說明" in t
    t = set_settings(t, {"strategies": ["ma", "orderflow"], "auto_trade": False, "trail_stop_pct": 12})
    assert '  strategies = ["ma", "orderflow"]' in t
    assert "auto_trade = false          # 自動模擬交易" in t and "trail_stop_pct = 12" in t
    import tomllib
    cfg = tomllib.loads(t)
    assert cfg["settings"]["strategies"] == ["ma", "orderflow"] and cfg["limits"]["2603"] == {"upper": 250}


class FakeGitHub:
    """假的雲端網頁（data.json）＋ GitHub API。"""
    def __init__(self, data):
        self.data, self.files, self.puts = data, {"cloud_monitor/config.toml": CLOUD_CONFIG}, []

    class R:
        def __init__(self, code, body):
            self.status_code, self._body = code, body

        def json(self):
            return self._body

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(self.status_code)

    def _path(self, url):
        return url.split("/contents/")[1]

    def get(self, url, **kw):
        if url.startswith("https://sdfvgv1994-bot.github.io"):
            return self.R(200, self.data)
        path = self._path(url)
        if path not in self.files:
            return self.R(404, {"message": "Not Found"})
        return self.R(200, {"content": base64.b64encode(self.files[path].encode()).decode(), "sha": "s"})

    def put(self, url, json=None, **kw):
        path = self._path(url)
        self.files[path] = base64.b64decode(json["content"]).decode()
        self.puts.append((path, json["message"]))
        return self.R(200, {})


def cloud_data():
    return {"updated": "2026-10-19 10:00:00",
            "settings": {"strategies": [{"key": "smc", "name": "SMC"}, {"key": "ma", "name": "均線"}],
                         "auto_trade": True, "auto_buy_amount": 100000.0, "stop_loss_pct": 8.0,
                         "trail_stop_pct": 0, "short_ma": 5, "long_ma": 20, "strategy_params": {}},
            "account": {"initial": 1_000_000, "cash": 900_000.0},
            "positions": [{"code": "2330", "name": "台積電", "shares": 40, "avg_cost": 2500.0, "price": 2450.0}],
            "watch": [{"code": "2330", "name": "台積電", "limits": {"lower": 1800, "upper": 3000}},
                      {"code": "AAPL", "name": "", "limits": {"lower": None, "upper": None}}],
            "trades": [{"time": "2026-10-19 09:30:00", "side": "買進", "code": "2330", "price": 2500.0, "shares": 40,
                        "fee": 142.5, "tax": 0, "cash_flow": -100142.5, "reason": "手動（雲端網頁）"}],
            "orders": []}


def make_cloud_live(tmp_path, token=True):
    gh = FakeGitHub(cloud_data())
    store = Store(tmp_path)
    if token:
        store.save("cloud.json", {"token": "github_pat_" + "x" * 30})
    feed = FakeFeed()
    feed.data = {"2330": ([2450.0] * 30, 2450.0), "AAPL": ([300.0] * 30, 300.0)}
    m = Monitor(store, fetcher=feed, realtime=lambda items: {}, name_lookup=lambda c: None)
    live = LiveMonitor(tmp_path, monitor=m, source="測試", cloud_session=gh)
    live.m.settings["source"] = "yahoo"
    return live, gh, feed


def test_cloud_mode_mirrors_account_and_settings(tmp_path):
    live, gh, _ = make_cloud_live(tmp_path)
    live.refresh()
    with live.lock:
        st = live.state()
    assert st["cloud"]["enabled"] and st["cloud"]["has_token"] and "token" not in st["cloud"]
    assert st["account"]["cash"] == 900_000 and st["stocks"][0]["position"]["shares"] == 40
    assert [s["ticker"] for s in st["stocks"]] == ["2330", "AAPL"]
    assert st["settings"]["strategies"] == ["smc", "ma"] and st["settings"]["auto_trade"] is True
    assert live.m.settings["auto_trade"] is False                  # 本機不自動交易
    assert st["trades"][0]["ticker"] == "2330"


def test_cloud_mode_writes_go_to_github(tmp_path):
    live, gh, feed = make_cloud_live(tmp_path)
    live.refresh()
    with live.lock:
        order = live.order("2330", "sell", 40)
        assert order["side"] == "sell" and order["shares"] == 40
        with pytest.raises(ValueError):
            live.order("AAPL", "sell", 1)                           # 沒有持股
        live.add("2603")
        live.set_alerts("2603", {"above": "250"})
        live.set_hlines("2330", [2400])
        live.update_settings({"strategies": ["ma", "orderflow"], "auto_trade": False, "short": 10, "long": 30})
        with pytest.raises(ValueError):
            live.reset_account()
    orders = json.loads(gh.files["cloud_monitor/orders.json"])
    assert orders[0]["code"] == "2330" and orders[0]["side"] == "sell" and orders[0]["shares"] == 40
    import tomllib
    cfg = tomllib.loads(gh.files["cloud_monitor/config.toml"])
    assert cfg["watchlist"] == ["2330", "AAPL", "2603"]
    assert cfg["limits"]["2603"] == {"upper": 250} and cfg["limits"]["2330"]["support"] == [2400]
    assert cfg["settings"]["strategies"] == ["ma", "orderflow"] and cfg["settings"]["auto_trade"] is False
    assert (cfg["settings"]["short_ma"], cfg["settings"]["long_ma"]) == (10, 30)
    assert "# 說明" in gh.files["cloud_monitor/config.toml"]


def test_cloud_mode_needs_token_and_can_switch_back(tmp_path):
    Store(tmp_path).save("account.json", {"cash": 123456, "initial": 1_000_000, "positions": {}, "trades": []})
    live, gh, _ = make_cloud_live(tmp_path, token=False)
    live.refresh()
    with live.lock:
        with pytest.raises(ValueError, match="權杖"):
            live.order("2330", "buy", 1)
        assert live.m.account.cash == 900_000
        live.set_cloud(enabled=False)                               # 改回本機帳戶：原本的帳戶還在
        assert live.m.account.cash == 123456
        with pytest.raises(ValueError):
            live.set_cloud(token="abc")
