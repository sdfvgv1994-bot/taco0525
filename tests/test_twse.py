"""證交所模組測試。

這些 JSON 的格式取自證交所 / 櫃買中心 API 的真實回應（2026/10 實測，只留 2～3 筆），
部分數字是手改的，只用來確認解析、快取、限速、上市 / 上櫃判斷的邏輯。
"""
from datetime import date, datetime

import pandas as pd
import pytest

from common import market, twse
from common.market import parse_symbol
from stock_monitor.engine import Monitor, with_live_bar
from stock_monitor.storage import Store

STOCK_DAY_OK = {
    "stat": "OK", "date": "20241001", "title": "113年10月 2330 台積電           各日成交資訊",
    "fields": ["日期", "成交股數", "成交金額", "開盤價", "最高價", "最低價", "收盤價", "漲跌價差",
               "成交筆數", "註記"],
    "data": [
        ["113/10/01", "35,000,000", "34,000,000,000", "980.00", "990.00", "975.00", "985.00", "+5.00", "40,000", ""],
        ["113/10/02", "0", "0", "--", "--", "--", "--", " 0.00", "0", ""],
        ["113/10/03＊", "20,000,000", "19,000,000,000", "990.00", "1,005.00", "985.00", "1,000.00", "+15.00", "30,000", ""],
    ],
    "notes": ["符號說明:+/-/X表示漲/跌/不比價"], "total": 3,
}
STOCK_DAY_EMPTY = {"stat": "很抱歉，沒有符合條件的資料!"}
INDEX_OK = {
    "stat": "OK", "title": "113年10月 發行量加權股價指數歷史資料", "date": "20241001",
    "fields": ["日期", "開盤指數", "最高指數", "最低指數", "收盤指數"],
    "data": [["113/10/01", "22,300.10", "22,500.00", "22,200.00", "22,480.55"]], "total": 1,
}
TPEX_NEW = {
    "date": "20241001", "code": "6488", "name": "環球晶", "flagField": "張數", "stat": "ok",
    "tables": [{
        "title": "個股日成交資訊", "subtitle": "6488 環球晶 113年10月", "date": "20241001",
        "fields": ["日 期", "成交張數", "成交仟元", "開盤", "最高", "最低", "收盤", "漲跌", "筆數"],
        "data": [["113/10/01", "1,234", "500,000", "400.00", "410.00", "395.00", "405.50", "5.50", "900"]],
        "notes": ["以上資料不含上櫃股票鉅額交易"], "totalCount": 1, "summary": [],
    }],
}
TPEX_OLD = {"aaData": [["113/10/01", "1,234", "500,000", "400.00", "410.00", "395.00", "405.50", "5.50", "900"]]}
MIS = {
    "rtcode": "0000", "rtmessage": "OK",
    "msgArray": [
        {"c": "2330", "n": "台積電", "nf": "台灣積體電路製造股份有限公司", "ex": "tse", "z": "1000.0000",
         "y": "985.0000", "o": "990.0000", "h": "1005.0000", "l": "985.0000", "v": "20000",
         "d": "20241003", "t": "13:30:00", "tlong": "1727933400000",
         "b": "995.0000_994.0000_", "a": "1000.0000_1005.0000_"},
        {"tv": "-", "s": "-", "c": "", "z": "-"},   # 查 otc_2330.tw 時的空白項目
        {"c": "6488", "n": "環球晶", "ex": "otc", "z": "-", "y": "400.0000", "o": "401.0000",
         "h": "405.0000", "l": "399.0000", "v": "500", "tlong": "1727933400000",
         "b": "402.5000_402.0000_", "a": "403.0000_"},
        {"c": "t00", "n": "發行量加權股價指數", "ex": "tse", "z": "22480.55", "y": "22300.10",
         "o": "22300.10", "h": "22500.00", "l": "22200.00", "m": "350000", "tlong": "1727933460000"},
        {"c": "o00", "n": "櫃買指數", "ex": "otc", "z": "230.12", "y": "229.00", "o": "229.50",
         "h": "231.00", "l": "228.80", "m": "80000", "tlong": "1727933580000"},
    ],
}

# ---------- 解析 ----------
def test_roc_date_and_numbers():
    assert twse.roc_to_date("113/10/01") == pd.Timestamp("2024-10-01")
    assert twse.roc_to_date("99/01/05＊") == pd.Timestamp("2010-01-05")
    assert twse.num("1,005.50") == 1005.5
    assert twse.num("+5.00") == 5.0
    assert twse.num("--") is None and twse.num("") is None and twse.num(None) is None
    assert twse.num("<p style= color:red>+</p>5.00") == 5.0


def test_parse_stock_day():
    df = twse.parse_stock_day(STOCK_DAY_OK)
    assert list(df.index) == [pd.Timestamp("2024-10-01"), pd.Timestamp("2024-10-03")]  # 沒成交的那天跳過
    assert df.loc["2024-10-03", "High"] == 1005.0
    assert df.loc["2024-10-01", "Volume"] == 35_000_000
    assert twse.parse_stock_day(STOCK_DAY_EMPTY).empty


def test_parse_index():
    df = twse.parse_index(INDEX_OK)
    assert df["Close"].iloc[0] == pytest.approx(22480.55)
    assert df["Volume"].iloc[0] == 0


@pytest.mark.parametrize("js", [TPEX_NEW, TPEX_OLD])
def test_parse_tpex_both_formats(js):
    df = twse.parse_tpex(js)
    assert df["Close"].iloc[0] == 405.5
    assert df["Volume"].iloc[0] == 1_234_000  # 張 / 仟股 → 股


def test_parse_tpex_empty():
    assert twse.parse_tpex({"tables": [{"data": []}]}).empty
    assert twse.parse_tpex({}).empty


def test_unexpected_fields_raise():
    with pytest.raises(twse.TwseError):
        twse.parse_stock_day({"stat": "OK", "fields": ["a", "b"], "data": [["1", "2"]]})


def test_parse_mis():
    q = twse.parse_mis(MIS)
    assert q["2330"].price == 1000 and q["2330"].name == "台積電"
    assert q["2330"].change == pytest.approx(15) and q["2330"].volume == 20_000_000
    assert q["6488"].price == 402.5            # 沒成交 → 最佳買價
    assert q["t00"].volume is None             # 指數沒有股數
    assert q["2330"].change_pct == pytest.approx(15 / 985 * 100)
    assert "" not in q and q["o00"].price == pytest.approx(230.12)
    assert q["2330"].time == datetime(2024, 10, 3, 13, 30)  # 台北時間，和電腦時區無關


# ---------- 連線（假的 session） ----------
class FakeResp:
    def __init__(self, js, status=200):
        self._js, self.status_code = js, status

    def json(self):
        if isinstance(self._js, Exception):
            raise self._js
        return self._js


class FakeSession:
    """依網址與參數回傳設定好的 JSON，並記錄每次呼叫。"""
    def __init__(self, router):
        self.router, self.calls = router, []

    def get(self, url, params=None, timeout=None, **kw):
        self.calls.append((url, dict(params or {})))
        return FakeResp(self.router(url, params or {}))


def stock_router(listed=True):
    def route(url, p):
        if url == twse.URL_STOCK_DAY:
            if not listed:
                return STOCK_DAY_EMPTY
            y, m = int(p["date"][:4]), int(p["date"][4:6])
            roc = f"{y - 1911}/{m:02d}"
            return {**STOCK_DAY_OK, "data": [[f"{roc}/0{d}", "1,000", "1", "10", "11", "9", f"{10 + d}", "0", "1"]
                                             for d in (1, 2, 3)]}
        if url == twse.URL_TPEX:
            y, m = p["date"].split("/")[:2]
            roc = f"{int(y) - 1911}/{m}"
            return {"tables": [{"fields": TPEX_NEW["tables"][0]["fields"],
                                "data": [[f"{roc}/01", "1", "1", "40", "41", "39", "40.5", "0", "1"]]}]}
        if url == twse.URL_MIS:
            return MIS
        raise AssertionError(url)
    return route


def make_client(tmp_path, router, **kw):
    s = FakeSession(router)
    return twse.TwseClient(cache_dir=tmp_path, session=s, min_interval=0, **kw), s


def test_history_spans_months_and_caches(tmp_path):
    cl, sess = make_client(tmp_path, stock_router())
    market_, df = cl.history("2330", date(2024, 8, 15), date(2024, 10, 20))
    assert market_ == "tse"
    assert df.index.min() >= pd.Timestamp("2024-08-15")  # 起始日之前的資料被濾掉
    assert {d.month for d in df.index} == {9, 10}
    n = len(sess.calls)
    cl.history("2330", date(2024, 8, 15), date(2024, 10, 20))
    assert len(sess.calls) == n  # 過去的月份全部走快取


def test_current_month_cache_expires(tmp_path, monkeypatch):
    cl, sess = make_client(tmp_path, stock_router())
    today = date.today()
    cl.stock_month("2330", today.year, today.month)
    cl.stock_month("2330", today.year, today.month)
    assert len(sess.calls) == 1
    monkeypatch.setattr(twse, "CACHE_TTL", -1)
    cl.stock_month("2330", today.year, today.month)
    assert len(sess.calls) == 2


def test_otc_fallback(tmp_path):
    cl, sess = make_client(tmp_path, stock_router(listed=False))
    market_, df = cl.history("6488", date(2024, 9, 1), date(2024, 10, 20))
    assert market_ == "otc" and len(df) == 2
    assert any(u == twse.URL_TPEX for u, _ in sess.calls)


def test_tpex_old_api_backup(tmp_path):
    def route(url, p):
        if url == twse.URL_TPEX:
            return {"unexpected": "format"}   # 新版格式認不得 → 改用舊網址
        if url == twse.URL_TPEX_OLD:
            assert p["d"] == "113/10"
            return TPEX_OLD
        raise AssertionError(url)
    cl, _ = make_client(tmp_path, route)
    assert cl.tpex_month("6488", 2024, 10)["Close"].iloc[0] == 405.5


def test_blocked_html_response(tmp_path):
    cl, _ = make_client(tmp_path, lambda u, p: ValueError("not json"))
    with pytest.raises(twse.TwseError, match="封鎖"):
        cl.stock_month("2330", 2024, 10)


def test_http_error(tmp_path):
    class S(FakeSession):
        def get(self, url, params=None, timeout=None, **kw):
            return FakeResp({}, status=503)
    cl = twse.TwseClient(cache_dir=tmp_path, session=S(None), min_interval=0)
    with pytest.raises(twse.TwseError, match="503"):
        cl.index_month(2024, 10)


def test_throttle(tmp_path, monkeypatch):
    sleeps = []
    monkeypatch.setattr(twse.time, "sleep", lambda s: sleeps.append(s))
    cl, _ = make_client(tmp_path, stock_router())
    cl.min_interval = 2.0
    cl.stock_month("2330", 2024, 1)
    cl.stock_month("2330", 2024, 2)
    assert sleeps and sleeps[-1] > 1.5


def test_realtime_maps_back_to_input(tmp_path):
    cl, sess = make_client(tmp_path, stock_router())
    got = cl.realtime(["2330", "6488", "^TWII"])
    assert set(got) == {"2330", "6488", "^TWII"}
    ex = sess.calls[-1][1]["ex_ch"].split("|")
    assert {"tse_2330.tw", "otc_2330.tw", "tse_t00.tw"} <= set(ex)


# ---------- 接到 fetch_history ----------
def test_fetch_history_uses_twse(tmp_path, monkeypatch):
    cl, _ = make_client(tmp_path, stock_router())
    monkeypatch.setattr(twse, "client", lambda: cl)
    code, df = market.fetch_history(parse_symbol("2330"), start="2024-09-01", end="2024-10-31")
    assert code == "2330.TW" and len(df) == 6


def test_fetch_history_otc_code(tmp_path, monkeypatch):
    cl, _ = make_client(tmp_path, stock_router(listed=False))
    monkeypatch.setattr(twse, "client", lambda: cl)
    code, _ = market.fetch_history(parse_symbol("6488"), start="2024-10-01", end="2024-10-31")
    assert code == "6488.TWO"


def test_fetch_history_twse_only_raises(tmp_path, monkeypatch):
    cl, _ = make_client(tmp_path, lambda u, p: STOCK_DAY_EMPTY if u == twse.URL_STOCK_DAY
                        else {"tables": [{"data": []}]} if u == twse.URL_TPEX else {})
    monkeypatch.setattr(twse, "client", lambda: cl)
    with pytest.raises(RuntimeError, match="證交所"):
        market.fetch_history(parse_symbol("9999"), start="2024-10-01", end="2024-10-31",
                             source="twse")


def test_auto_falls_back_to_yahoo(tmp_path, monkeypatch, capsys):
    cl, _ = make_client(tmp_path, lambda u, p: ValueError("blocked"))
    monkeypatch.setattr(twse, "client", lambda: cl)
    called = {}

    class FakeYF:
        @staticmethod
        def download(code, **kw):
            called["code"] = code
            idx = pd.bdate_range("2024-10-01", periods=3)
            return pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": [1.0, 2, 3],
                                 "Volume": 0}, index=idx)
    import sys
    monkeypatch.setitem(sys.modules, "yfinance", FakeYF)
    code, df = market.fetch_history(parse_symbol("2330"), start="2024-10-01", end="2024-10-31")
    assert code == "2330.TW" and called["code"] == "2330.TW"
    assert "改用 Yahoo" in capsys.readouterr().err


def test_us_stock_never_touches_twse(monkeypatch):
    monkeypatch.setattr(twse, "client", lambda: pytest.fail("不該連證交所"))
    market.fetch_history(parse_symbol("AAPL"), demo=True)
    assert not market.uses_twse(parse_symbol("AAPL"))
    assert market.uses_twse(parse_symbol("加權"))


# ---------- 看盤系統的即時報價 ----------
def test_with_live_bar_appends_today():
    df = pd.DataFrame({"Open": [10.0], "High": [10.0], "Low": [10.0], "Close": [10.0], "Volume": [0]},
                      index=[pd.Timestamp("2024-10-02")])
    q = twse.parse_mis(MIS)["2330"]
    q.time = datetime(2024, 10, 3, 10, 0)
    out = with_live_bar(df, q)
    assert len(out) == 2 and out["Close"].iloc[-1] == 1000
    q.time = datetime(2024, 10, 2, 13, 30)    # 同一天就不重複加
    assert len(with_live_bar(df, q)) == 1


def test_monitor_uses_realtime_quotes(tmp_path):
    quotes = twse.parse_mis(MIS)
    asked = []

    def realtime(items):
        asked.append(items)
        return {"2330": quotes["2330"]}

    def fetcher(name, settings, now, q):
        idx = pd.bdate_range(end="2024-10-02", periods=30)
        df = pd.DataFrame({"Open": 985.0, "High": 985.0, "Low": 985.0, "Close": 985.0, "Volume": 0},
                          index=idx)
        return name, with_live_bar(df, q.get(name)), q[name].price if name in q else 985.0

    m = Monitor(Store(tmp_path), fetcher=fetcher, realtime=realtime)
    m.add("2330")
    m.add("AAPL")
    res = m.refresh(datetime(2024, 10, 3, 10, 0))
    assert asked == [["2330"]]                  # 只查台股，一次查完
    assert res.snapshots["2330"].price == 1000
    assert m.names["2330"] == "台積電"


def test_monitor_skips_realtime_for_yahoo_source(tmp_path):
    m = Monitor(Store(tmp_path), fetcher=lambda *a: pytest.fail(),
                realtime=lambda items: pytest.fail("不該查證交所"))
    m.settings["source"] = "yahoo"
    m.watchlist = {"2330": {}}
    assert m.realtime_quotes() == {}


# ---------- 跨程式限速與封鎖保護 ----------
def test_gate_shared_between_clients(tmp_path, monkeypatch):
    """兩個不同的 TwseClient（模擬兩個程式）共用快取資料夾時，也要排隊。"""
    sleeps = []
    monkeypatch.setattr(twse.time, "sleep", lambda s: sleeps.append(s))
    a = twse.TwseClient(cache_dir=tmp_path, session=FakeSession(stock_router()), min_interval=3)
    b = twse.TwseClient(cache_dir=tmp_path, session=FakeSession(stock_router()), min_interval=3)
    a.stock_month("2330", 2024, 1)
    b.stock_month("2330", 2024, 2)
    assert sleeps and 2.5 < sleeps[-1] <= 3


def test_gate_per_host(tmp_path, monkeypatch):
    """證交所和櫃買中心是不同網站，各自排隊。"""
    sleeps = []
    monkeypatch.setattr(twse.time, "sleep", lambda s: sleeps.append(s))
    cl, _ = make_client(tmp_path, stock_router(listed=False))
    cl.min_interval = 3
    cl._gates.clear()
    cl.stock_month("6488", 2024, 1)   # www.twse.com.tw
    cl.tpex_month("6488", 2024, 1)    # www.tpex.org.tw
    assert not sleeps


def test_file_lock_across_processes(tmp_path):
    """真的開另一個程式拿著鎖，這邊要等它放開。"""
    import subprocess
    import sys
    import time as _t
    lock = tmp_path / ".gate_x.lock"
    code = ("import sys, time; sys.path.insert(0, '.'); from common.twse import _file_lock; "
            "from pathlib import Path\n"
            f"with _file_lock(Path({str(lock)!r})):\n"
            "    print('locked', flush=True); time.sleep(1.0)")
    p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "locked"
    t0 = _t.monotonic()
    with twse._file_lock(lock):
        waited = _t.monotonic() - t0
    p.wait(timeout=10)
    assert waited > 0.5


class StatusSession(FakeSession):
    """先回指定的狀態碼，之後回正常 JSON。"""
    def __init__(self, first_status, body=None):
        super().__init__(stock_router())
        self.first_status, self.body = first_status, body

    def get(self, url, params=None, timeout=None, **kw):
        self.calls.append((url, dict(params or {}), kw))
        if len(self.calls) == 1:
            return FakeResp(self.body if self.body is not None else {}, status=self.first_status)
        return FakeResp(self.router(url, params or {}))


@pytest.mark.parametrize("status,body", [(307, {}), (502, {}), (403, {}), (200, ValueError("html"))])
def test_block_detected_and_cooldown(tmp_path, status, body):
    sess = StatusSession(status, body)
    cl = twse.TwseClient(cache_dir=tmp_path, session=sess, min_interval=0)
    with pytest.raises(twse.TwseError, match="封鎖"):
        cl.stock_month("2330", 2024, 1)
    assert sess.calls[0][2].get("allow_redirects") is False   # 轉址要自己看到，不能被自動跟過去
    # 冷卻期間：不再連線，直接報錯；其他程式（另一個 client）也看得到
    other = twse.TwseClient(cache_dir=tmp_path, session=sess, min_interval=0)
    for c in (cl, other):
        with pytest.raises(twse.TwseError, match="分鐘內先不連線"):
            c.stock_month("2330", 2024, 2)
    assert len(sess.calls) == 1
    # 櫃買中心不受影響
    cl.tpex_month("6488", 2024, 1)


def test_block_cooldown_expires_and_clears(tmp_path, monkeypatch):
    sess = StatusSession(307)
    cl = twse.TwseClient(cache_dir=tmp_path, session=sess, min_interval=0)
    with pytest.raises(twse.TwseError):
        cl.stock_month("2330", 2024, 1)
    monkeypatch.setattr(twse, "BLOCK_COOLDOWN", 0)
    assert not cl.stock_month("2330", 2024, 1).empty     # 冷卻結束，成功後清掉紀錄
    monkeypatch.setattr(twse, "BLOCK_COOLDOWN", 3600)
    assert cl.gate(twse.URL_STOCK_DAY).blocked_remaining() == 0


def test_auto_source_skips_twse_while_blocked(tmp_path, monkeypatch, capsys):
    sess = StatusSession(307)
    cl = twse.TwseClient(cache_dir=tmp_path, session=sess, min_interval=0)
    with pytest.raises(twse.TwseError):
        cl.stock_month("2330", 2024, 1)
    monkeypatch.setattr(twse, "client", lambda: cl)

    class FakeYF:
        @staticmethod
        def download(code, **kw):
            idx = pd.bdate_range("2024-10-01", periods=3)
            return pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": [1.0, 2, 3],
                                 "Volume": 0}, index=idx)
    import sys
    monkeypatch.setitem(sys.modules, "yfinance", FakeYF)
    code, _ = market.fetch_history(parse_symbol("2330"), start="2024-10-01", end="2024-10-31")
    assert code == "2330.TW" and len(sess.calls) == 1     # 沒有再去敲證交所
    assert "分鐘內先不連線" in capsys.readouterr().err


def test_tpex_empty_month_does_not_touch_old_url(tmp_path):
    """新版 API 正常回應但那個月沒資料：不要去打舊網址（舊網址可能轉址，會被誤判成封鎖）。"""
    def route(url, p):
        if url == twse.URL_TPEX:
            return {"stat": "ok", "tables": [{"data": []}]}
        raise AssertionError("不該連舊網址")
    cl, _ = make_client(tmp_path, route)
    assert cl.tpex_month("6488", 2024, 10).empty
