"""從證交所、櫃買中心抓台股總覽要用的資料，存進快取（tw_market/store.py）。

GitHub Actions 每 15 分鐘執行一次，每次依重要性排隊抓：
  1. 最新一個交易日的行情（上市、上櫃）
  2. 最新那天的三大法人、本益比、融資融券，以及櫃買指數
  3. 產業別（沒有或超過 7 天才抓）
  4. 往回補歷史行情（新 → 舊），最多 HIST_MAX_DAYS 個交易日
  5. 往回補三大法人、融資融券，最多 CHIPS_MAX_DAYS 個交易日
每個網站每次最多連 max_per_host 次、總共最多 deadline_s 秒，抓不完的下次繼續。
證交所和櫃買中心的請求交錯送出，兩邊各自的 3 秒間隔可以重疊，比較快。

今天的資料要等公布後才抓（行情 14:00、本益比 15:00、三大法人 15:30、融資融券 21:00），
在那之前「最新一天」是前一個交易日。平日抓不到過去某天的行情（兩個市場都沒有）就記成休市。
"""
from __future__ import annotations

import importlib
import time as _time
from collections import Counter, deque
from datetime import date, datetime, time, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests

from common.twse import TwseError
from tw_market import contract
from tw_market.store import MARKETS, Store

TAIPEI = ZoneInfo("Asia/Taipei")

# 今天的資料大約幾點公布（台北時間）
PUBLISH = {"quotes": time(14, 0), "otc_index": time(14, 0), "valuation": time(15, 0),
           "insti": time(15, 30), "insti_totals": time(15, 30), "margin": time(21, 0)}
INDUSTRY_MAX_AGE = timedelta(days=7)
NET_ERRORS_PER_HOST = 2        # 同一個網站連線失敗（逾時等）幾次就先不連
KEEP_EXTRA_DAYS = 30           # 清快取時多留幾天，避免剛好用得到的被刪掉

LABELS = {"quotes": "行情", "insti": "三大法人", "insti_totals": "三大法人買賣金額",
          "margin": "融資融券", "valuation": "本益比", "otc_index": "櫃買指數", "industry": "產業別"}
MARKET_NAMES = {"tse": "上市", "otc": "上櫃"}
HOST_NAMES = {"www.twse.com.tw": "證交所", "openapi.twse.com.tw": "證交所 OpenAPI",
              "www.tpex.org.tw": "櫃買中心"}

# (資料, 市場) → (網址函式, 解析函式)，都在 tw_market/sources.py
SOURCES = {
    ("quotes", "tse"): ("twse_quotes", "parse_twse_quotes"),
    ("quotes", "otc"): ("tpex_quotes", "parse_tpex_quotes"),
    ("insti", "tse"): ("twse_insti", "parse_twse_insti"),
    ("insti", "otc"): ("tpex_insti", "parse_tpex_insti"),
    ("insti_totals", "tse"): ("twse_insti_totals", "parse_twse_insti_totals"),
    ("margin", "tse"): ("twse_margin", "parse_twse_margin"),
    ("margin", "otc"): ("tpex_margin", "parse_tpex_margin"),
    ("valuation", "tse"): ("twse_valuation", "parse_twse_valuation"),
    ("valuation", "otc"): ("tpex_valuation", "parse_tpex_valuation"),
    ("otc_index", "otc"): ("tpex_index_month", "parse_tpex_index_month"),
    ("industry", "tse"): ("twse_industry", "parse_twse_industry"),
    ("industry", "otc"): ("tpex_industry", "parse_tpex_industry"),
}
DAILY = ("quotes", "insti", "insti_totals", "margin", "valuation")   # 一天一個檔的資料


# ---------------------------------------------------------------- 交易日（純函式）
def taipei(now: datetime | None = None) -> datetime:
    """轉成台北時間；沒帶時區的當作台北時間。"""
    if now is None:
        return datetime.now(TAIPEI)
    return now.replace(tzinfo=TAIPEI) if now.tzinfo is None else now.astimezone(TAIPEI)


def is_trading_day(d: date, closed) -> bool:
    return d.weekday() < 5 and d not in closed


def trading_days(end: date, n: int, closed) -> list[date]:
    """end（含）往前 n 個交易日，新 → 舊。"""
    out, d = [], end
    for _ in range(n * 3 + 400):      # 防呆：休市日寫壞了也不會無窮迴圈
        if len(out) >= n:
            break
        if is_trading_day(d, closed):
            out.append(d)
        d -= timedelta(days=1)
    return out


def latest_day(dataset: str, now: datetime, closed) -> date:
    """這份資料目前應該有的最新交易日：今天已經過了公布時間就是今天，否則是前一個交易日。"""
    now = taipei(now)
    today = now.date()
    if is_trading_day(today, closed) and now.time() >= PUBLISH[dataset]:
        return today
    return trading_days(today - timedelta(days=1), 1, closed)[0]


# ---------------------------------------------------------------- 抓資料
class _Updater:
    def __init__(self, store, client, now, src, max_per_host, deadline_s, log, clock):
        self.store, self.client, self.src, self.log, self.clock = store, client, src, log, clock
        self.now = taipei(now)
        self.today = self.now.date()
        self.max_per_host, self.deadline_s = max_per_host, deadline_s
        self.t0 = clock()
        self.closed = store.closed()
        self.requests: Counter = Counter()
        self.saved: list[str] = []
        self.errors: list[str] = []
        self.closed_added: list[str] = []
        self.stopped: set[str] = set()          # 這次不再連的網站
        self.net_errors: Counter = Counter()
        self.broken: set[tuple] = set()         # 這次格式不對或網址有問題的 (資料, 市場)
        self.tried: set[tuple] = set()          # 這次已經問過的（沒資料也不再問）
        self.no_quotes: dict[date, set] = {}    # 過去某天哪個市場回答「沒有行情」
        self.timed_out = False

    # ---- 小工具 ----
    def latest(self, dataset: str) -> date:
        return latest_day(dataset, self.now, self.closed)

    def label(self, task) -> str:
        dataset, market, d = task
        if dataset == "otc_index":
            return LABELS[dataset]
        s = f"{MARKET_NAMES[market]}{LABELS[dataset]}"
        return f"{s} {d}" if d else s

    def request_of(self, task):
        dataset, market, d = task
        builder = getattr(self.src, SOURCES[(dataset, market)][0])
        return builder() if dataset == "industry" else builder(d)

    def _blocked(self, url: str) -> bool | None:
        """證交所連線模組記錄的封鎖狀態；不知道（例如測試用的假連線）時回傳 None。"""
        gate = getattr(self.client, "gate", None)
        if gate is None:
            return None
        try:
            return gate(url).blocked_remaining() > 0
        except Exception:  # noqa: BLE001
            return None

    def error(self, msg: str) -> None:
        self.errors.append(msg)
        self.log(f"  ⚠ {msg}")

    def stop_host(self, host: str, why: str) -> None:
        if host not in self.stopped:
            self.stopped.add(host)
            self.log(f"  {HOST_NAMES.get(host, host)}{why}")

    def done(self, task) -> bool:
        """已經有了（或這次確定不用抓）。"""
        dataset, market, d = task
        if task in self.tried or (dataset, market) in self.broken:
            return True
        if dataset == "quotes" and d in self.closed:
            return True
        if dataset in DAILY:
            return self.store.has(dataset, market, d)
        if dataset == "otc_index":
            return d.isoformat() in self.store.otc_index(d.year, d.month)
        return False

    # ---- 執行 ----
    def run(self, tasks) -> None:
        """依順序執行；不同網站的工作輪流送出（兩邊的 3 秒間隔可以重疊）。"""
        queues: dict[str, deque] = {}
        for t in tasks:
            url, params = self.request_of(t)
            host = urlparse(url).hostname or url
            queues.setdefault(host, deque()).append((t, url, params, host))
        while queues and not self.timed_out:
            for host in list(queues):
                q = queues[host]
                if not q or host in self.stopped:
                    del queues[host]
                    continue
                self.fetch(*q.popleft())
                if self.timed_out:
                    break
        self.settle_holidays()

    def fetch(self, task, url, params, host) -> None:
        if host in self.stopped or self.done(task):
            return
        if self.clock() - self.t0 >= self.deadline_s:
            self.timed_out = True
            self.log(f"  已經用了 {self.deadline_s:.0f} 秒，剩下的下次再抓")
            return
        if self.requests[host] >= self.max_per_host:
            self.stop_host(host, f"這次已經連了 {self.requests[host]} 次，剩下的下次再抓")
            return
        if self._blocked(url):
            self.stop_host(host, "之前被暫時封鎖，這次先不連")
            return
        dataset, market, d = task
        what = self.label(task)
        self.requests[host] += 1
        self.tried.add(task)
        try:
            js = self.client._get_json(url, params)
        except TwseError as e:
            self.error(f"{what}：{e}")
            if self._blocked(url) is False:     # 不是封鎖（例如網址停用）：這類資料這次先不抓
                self.broken.add((dataset, market))
            else:
                self.stop_host(host, "這次先不連（被封鎖或回應異常）")
            return
        except (requests.RequestException, OSError) as e:
            self.error(f"{what}：連線失敗（{type(e).__name__}）")
            self.net_errors[host] += 1
            if self.net_errors[host] >= NET_ERRORS_PER_HOST:
                self.stop_host(host, "連線一直失敗，這次先不連")
            return
        parser = getattr(self.src, SOURCES[(dataset, market)][1])
        try:
            parsed = parser(js)
        except (ValueError, KeyError, TypeError, IndexError, AttributeError) as e:
            self.broken.add((dataset, market))
            self.error(f"{what}：格式和預期不同（{e}），這類資料這次先不抓")
            return
        try:
            if dataset == "industry":
                self.save_industry(market, parsed, what)
            elif dataset == "otc_index":
                self.save_index(d, parsed, what)
            else:
                self.save_day(task, parsed, what)
        except (TypeError, ValueError) as e:      # 解析結果不能存成 JSON：程式問題，這類資料先不抓
            self.broken.add((dataset, market))
            self.error(f"{what}：存檔失敗（{e}）")

    # ---- 存檔 ----
    def save_day(self, task, parsed, what) -> None:
        dataset, market, d = task
        if parsed is None:
            if dataset == "quotes" and d < self.today:
                self.no_quotes.setdefault(d, set()).add(market)
            self.log(f"  {what}：沒有資料")
            return
        if not isinstance(parsed, dict) or parsed.get("date") != d.isoformat():
            got = parsed.get("date") if isinstance(parsed, dict) else None
            self.log(f"  {what}：回應的日期是 {got}，和要的不一樣，先不存")
            return
        rows = parsed.get("rows")
        if dataset != "insti_totals" and not (isinstance(rows, dict) and rows):
            self.log(f"  {what}：沒有任何一檔的資料，先不存")
            return
        self.store.save(dataset, market, d, parsed)
        self.saved.append(f"{dataset}/{market}/{d:%Y%m%d}")
        self.log(f"  {what}：{len(rows)} 檔" if rows else f"  {what}：完成")

    def settle_holidays(self) -> None:
        """過去某個平日沒有行情：另一個市場也沒有（或這次沒問到）才當成休市。
        連線失敗、被封鎖不算「沒有」，所以不會因為網路問題記錯。"""
        for d, markets in sorted(self.no_quotes.items()):
            if any(self.store.has("quotes", m, d) for m in MARKETS):
                self.log(f"  {d}：{'、'.join(MARKET_NAMES[m] for m in sorted(markets))}沒有行情，"
                         "但另一個市場有，先不當成休市")
                continue
            self.closed.add(d)
            self.closed_added.append(d.isoformat())
            self.store.add_closed([d])
            self.log(f"  {d} 沒有開盤（休市），記下來以後不再抓")
        self.no_quotes.clear()

    def save_index(self, d: date, parsed, what) -> None:
        if not isinstance(parsed, dict):
            self.log(f"  {what}：還沒有資料")
            return
        month = f"{d:%Y-%m}-"
        rows = {k: v for k, v in parsed.items() if isinstance(k, str) and k.startswith(month)
                and isinstance(v, dict)}
        if rows:
            self.store.save_otc_index(d.year, d.month, {**self.store.otc_index(d.year, d.month), **rows})
            self.saved.append(f"otc_index/{d:%Y%m}")
        self.log(f"  {what} {d:%Y-%m}：{len(rows)} 天" + ("" if d.isoformat() in rows else f"（還沒有 {d}）"))

    def save_industry(self, market: str, parsed, what) -> None:
        if not isinstance(parsed, dict) or not parsed:
            self.error(f"{what}：沒有資料")
            return
        ind = self.store.industry()
        stamps = ind.get("fetched_markets") if isinstance(ind.get("fetched_markets"), dict) else {}
        stamps = {**stamps, market: self.now.isoformat(timespec="seconds")}
        ind.update({market: parsed, "fetched_markets": stamps,
                    "fetched": min(stamps.get(m, stamps[market]) for m in MARKETS)})
        self.store.save_industry(ind)
        self.saved.append(f"industry/{market}")
        self.log(f"  {what}：{len(parsed)} 家")

    # ---- 排程（依重要性） ----
    def newest_quotes(self, market: str, upto: date, days: int) -> date | None:
        """upto 以前最近 days 個交易日內，最新一個有行情的日子。"""
        window = set(trading_days(upto, days, self.closed))
        got = [d for d in self.store.dates("quotes", market) if d in window]
        return got[-1] if got else None

    def phase_latest_quotes(self) -> None:
        """兩個市場最新一天的行情；今天還沒公布或發現是休市，就改抓前一個交易日（最多往前 5 天）。"""
        want = {m: self.latest("quotes") for m in MARKETS}
        for _ in range(5):
            self.run([("quotes", m, d) for m, d in want.items()])
            if self.timed_out:
                return
            # 過去的日子抓不到又不是休市（多半是連線問題）就不再往前
            want = {m: trading_days(d - timedelta(days=1), 1, self.closed)[0] for m, d in want.items()
                    if not self.store.has("quotes", m, d) and (d == self.today or d in self.closed)}
            if not want:
                break
        # 本益比、法人、融資融券今天還沒公布時，它們的最新一天是前一個交易日：那天的行情也先抓，
        # 第二步才知道要抓哪一天（第一次執行、快取是空的時候用得到）
        days = sorted({self.latest(ds) for ds in ("valuation", "insti", "margin")}, reverse=True)
        self.run([("quotes", m, d) for d in days for m in MARKETS])

    def phase_latest_details(self) -> None:
        tasks = []
        d = self.newest_quotes("otc", self.latest("otc_index"), contract.CHIPS_MAX_DAYS)
        if d:
            tasks.append(("otc_index", "otc", d))
        for dataset in ("insti", "insti_totals", "valuation", "margin"):
            for m in (("tse",) if dataset == "insti_totals" else MARKETS):
                d = self.newest_quotes(m, self.latest(dataset), contract.CHIPS_MAX_DAYS)
                if d:
                    tasks.append((dataset, m, d))
        self.run([t for t in tasks if not self.done(t)])

    def phase_industry(self) -> None:
        ind = self.store.industry()
        stamps = ind.get("fetched_markets") if isinstance(ind.get("fetched_markets"), dict) else {}
        tasks = []
        for m in MARKETS:
            try:
                when = datetime.fromisoformat(stamps.get(m) or ind.get("fetched") or "")
                fresh = self.now - taipei(when) < INDUSTRY_MAX_AGE
            except (TypeError, ValueError):
                fresh = False
            if not (isinstance(ind.get(m), dict) and ind[m] and fresh):
                tasks.append(("industry", m, None))
        self.run(tasks)

    def phase_backfill_quotes(self) -> None:
        days = trading_days(self.latest("quotes"), contract.HIST_MAX_DAYS, self.closed)
        self.run([("quotes", m, d) for d in days for m in MARKETS if not self.store.has("quotes", m, d)])

    def phase_backfill_chips(self) -> None:
        per = {ds: set(trading_days(self.latest(ds), contract.CHIPS_MAX_DAYS, self.closed))
               for ds in ("insti", "margin")}
        tasks = []
        for d in sorted(per["insti"] | per["margin"], reverse=True):
            for ds in ("insti", "margin"):
                for m in MARKETS:
                    if d in per[ds] and self.store.has("quotes", m, d) and not self.store.has(ds, m, d):
                        tasks.append((ds, m, d))
        self.run(tasks)

    def prune(self) -> None:
        """刪掉用不到的舊檔，快取才不會一直變大。"""
        def start(dataset, n):
            days = trading_days(self.latest(dataset), n, self.closed)
            return days[-1] - timedelta(days=KEEP_EXTRA_DAYS)
        for m in MARKETS:
            self.store.prune("quotes", m, start("quotes", contract.HIST_MAX_DAYS))
            for ds in ("insti", "margin", "valuation", "insti_totals"):
                self.store.prune(ds, m, start(ds, contract.CHIPS_MAX_DAYS))
        self.store.prune_otc_index(self.latest("quotes") - timedelta(days=62))


def update(cache, client, now: datetime | None = None, max_per_host: int = 25, deadline_s: float = 240,
           log=print, sources=None, clock=_time.monotonic) -> dict:
    """更新快取。cache 是 Store 或資料夾；client 要有 _get_json(url, params)（common.twse.TwseClient）。

    sources 預設是 tw_market.sources（測試可以換成假的）；clock 用來算 deadline_s（測試可以換成假時鐘）。
    回傳統計：每個網站連了幾次、新存了哪些檔、錯誤訊息、新發現的休市日、歷史 K 線天數。
    """
    store = cache if isinstance(cache, Store) else Store(cache)
    src = sources or importlib.import_module("tw_market.sources")
    u = _Updater(store, client, now, src, max_per_host, deadline_s, log, clock)
    log(f"台股資料更新（台北時間 {u.now:%Y-%m-%d %H:%M}）")
    for phase in (u.phase_latest_quotes, u.phase_latest_details, u.phase_industry,
                  u.phase_backfill_quotes, u.phase_backfill_chips):
        if u.timed_out:
            break
        phase()
    u.prune()
    history_days = min(len(store.dates("quotes", "tse")), contract.HIST_MAX_DAYS)
    hosts = "、".join(f"{HOST_NAMES.get(h, h)} {n} 次" for h, n in u.requests.items()) or "沒有連線"
    log(f"台股資料：{hosts}，新存 {len(u.saved)} 個檔，歷史 K 線 {history_days} 天"
        + (f"，{len(u.errors)} 個錯誤" if u.errors else ""))
    return {"requests": dict(u.requests), "saved": u.saved, "errors": u.errors,
            "closed_added": u.closed_added, "history_days": history_days, "timed_out": u.timed_out}
