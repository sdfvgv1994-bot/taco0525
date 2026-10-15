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
    live = LiveMonitor(tmp_path, monitor=m, source="測試")
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
