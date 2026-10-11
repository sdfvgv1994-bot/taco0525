"""台股總覽網頁（tw_market/static/index.html）與其他頁面連過去的連結。"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "tw_market" / "static" / "index.html"
WORLD = ROOT / "world_market" / "static" / "index.html"
MONITOR = ROOT / "cloud_monitor" / "index.html"


def _html(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_page_exists_and_reads_the_data_files():
    html = _html(PAGE)
    assert "<title>台股總覽</title>" in html
    for ref in ("data/summary.json", "data/stocks.json", "data/hist/"):
        assert ref in html, ref
    # 讀不到資料時的訊息（tw_market.site.write_placeholder 只放網頁時會看到）
    assert "台股資料還沒產生，請等下一次雲端更新" in html


def test_page_loads_nothing_external():
    html = _html(PAGE)
    assert not re.search(r"<script[^>]+src\s*=", html, re.I), "要把程式寫在頁面裡"
    assert not re.search(r"<link[^>]+href\s*=\s*[\"']?(https?:)?//", html, re.I), "不能載入外部樣式或字型"
    assert not re.search(r"@import|url\(\s*[\"']?(https?:)?//", html, re.I), "CSS 不能載入外部資源"
    assert not re.search(r"fetch\(\s*[`\"']https?://", html), "資料要讀同一個網站的檔案"


def test_preferences_shared_with_other_pages():
    html = _html(PAGE)
    assert '"wm." + key' in html and "localStorage" in html
    # localStorage 一律包在 try/catch 裡（私密模式等情況會丟例外）
    for m in re.finditer(r"localStorage\.", html):
        before = html[:m.start()]
        assert before.rfind("try {") > before.rfind("function "), "localStorage 要包在 try/catch 裡"


def test_script_syntax_is_valid(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("沒有 node")
    html = _html(PAGE)
    scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    assert len(scripts) == 1
    js = tmp_path / "page.js"
    js.write_text(scripts[0], encoding="utf-8")
    r = subprocess.run([node, "--check", str(js)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def _js(html: str, name: str) -> str:
    """頁面程式裡某個 const / let 或 function 的原始碼（拿出來給 node 單獨執行）。"""
    one_line = re.search(rf"^function {name}\(.*\}}$", html, re.M)      # 寫成一行的 function
    if one_line:
        return one_line.group(0)
    m = re.search(rf"^(?:const|let) {name} = .*?;$|^function {name}\(.*?^}}$", html, re.S | re.M)
    assert m, f"頁面裡找不到 {name}"
    return m.group(0)


def test_page_reads_every_stock_field():
    from tw_market.contract import STOCK_FIELDS
    fields = json.loads(_js(_html(PAGE), "FIELDS").split("=", 1)[1].rstrip(";"))
    assert fields == STOCK_FIELDS, "網頁的 FIELDS 要和 contract.STOCK_FIELDS 一樣（新增欄位時網頁也要讀）"


def test_limit_badges_follow_the_limit_field(tmp_path):
    """漲停、跌停標籤看 stocks.json 的 limit 欄（和大盤的漲停家數同一個規則）：
    漲 9.58% 的也可能是漲停、沒有漲跌幅限制的新股漲 12% 不是；舊的 stocks.json 沒有 limit 欄時才用 9.5% 估計。"""
    node = shutil.which("node")
    if not node:
        pytest.skip("沒有 node")
    html = _html(PAGE)
    code = "\n".join(_js(html, n) for n in ("FIELDS", "TEXT_FIELDS", "isNum", "normQ", "hasLimit", "limitOf", "setStocks"))
    js = tmp_path / "limit.js"
    js.write_text("let stocks, byCode, indStats, groupNames;\n" + code + """
const ROWS = [
  { code: "1303", type: "stock", change_pct: 9.58, limit: 1 },     // 南亞：漲停價只漲 9.58%
  { code: "6488", type: "stock", change_pct: 12, limit: 0 },       // 新股沒有漲跌幅限制
  { code: "2330", type: "stock", change_pct: -9.6, limit: -1 },
  { code: "2317", type: "stock", change_pct: -9.7, limit: 0 },
  { code: "00631L", type: "etf", change_pct: 10, limit: null },
];
const run = (fields) => {
  setStocks({ fields, rows: ROWS.map((r) => fields.map((k) => (k in r ? r[k] : k === "name" ? r.code : null))) });
  return Object.fromEntries(stocks.map((r) => [r.code, limitOf(r)]));
};
console.log(JSON.stringify([run(FIELDS), run(FIELDS.filter((k) => k !== "limit"))]));
""", encoding="utf-8")
    r = subprocess.run([node, str(js)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    with_field, without = json.loads(r.stdout)
    assert with_field == {"1303": "up", "6488": None, "2330": "down", "2317": None, "00631L": None}
    assert without == {"1303": "up", "6488": "up", "2330": "down", "2317": "down", "00631L": None}


def test_page_warns_when_cloud_updates_stop():
    html = _html(PAGE)
    # updated_utc 是最後一次確認資料是最新的時間（雲端每 15 分鐘確認一次），太久沒動就提醒（和全球看板一樣）
    assert "const STALE_MS = 90 * 60 * 1000;" in html
    assert "雲端更新可能暫停了" in html and "雲端更新可能暫停了" in _html(WORLD)


def test_world_board_links_to_tw_only_in_static_version():
    html = _html(WORLD)
    assert "🇹🇼 台股總覽" in html
    m = re.search(r"if \(STATIC\) \{(.*?)\n\}", html, re.S)
    assert m, "找不到雲端版的區塊"
    block = m.group(1)
    assert 'href: "tw/"' in block and "🇹🇼 台股總覽" in block, "台股總覽的連結只在雲端版出現（本機版沒有 /tw/）"


def test_cloud_monitor_links_to_tw():
    html = _html(MONITOR)
    # 手機上「總覽」兩個字會藏起來（四個按鈕才排得下一行），所以中間可能夾著 <span>
    m = re.search(r'<a class="btn" href="\.\./tw/">(.*?)</a>', html)
    assert m, "我的看盤的標題列要有連到 ../tw/ 的按鈕"
    assert re.sub(r"<[^>]+>", "", m.group(1)) == "🇹🇼 台股總覽"
    assert "../tw/#${tw}" in html   # 自選股卡片：台股個股的名稱連到台股總覽的詳細資料


# ---------------------------------------------------------------- 審查修正（第二輪）
def _node(tmp_path, names, body, extra=""):
    """把頁面裡的幾個函式拿出來，和 body 一起用 node 執行，回傳 body 印出的 JSON。"""
    node = shutil.which("node")
    if not node:
        pytest.skip("沒有 node")
    html = _html(PAGE)
    js = tmp_path / "t.js"
    js.write_text(extra + "\n".join(_js(html, n) for n in names) + "\n" + body, encoding="utf-8")
    r = subprocess.run([node, str(js)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


DATES = ("WD", "p2", "parseD", "wdOf", "dShort", "mmdd", "isNum")


def test_institutional_amounts_show_only_the_market_that_has_them(tmp_path):
    """上櫃的法人買賣金額暫不提供（institutional.otc 是 null）：只列上市，不要一整欄「還沒公布」
    （審查：頁首說上櫃暫不提供，下面卻叫人等）；合計就是上市的數字。"""
    both = re.search(r"^const both = .*$", _html(PAGE), re.M).group(0)
    one, two, half = _node(tmp_path, ("isNum", "INST_KINDS", "instView"), """
const T = { foreign: -7.5e10, trust: 3e9, dealer: -1.8e10, total: -9.0e10 };
const O = { foreign: 1e9, trust: 2e8, dealer: -5e7, total: 1.15e9 };
console.log(JSON.stringify([instView({ date: "2026-10-08", tse: T, otc: null }),
  instView({ date: "2026-10-08", tse: T, otc: O }), instView({ date: "2026-10-08", tse: null, otc: null })]));
""", extra=both + "\n")
    assert one["title"] == "三大法人買賣超（上市）" and one["one"] == "上市"
    assert [r["total"] for r in one["rows"]] == [-7.5e10, 3e9, -1.8e10, -9.0e10]
    assert all(r["sides"] is None for r in one["rows"])          # 沒有上櫃那一欄
    assert two["title"] == "三大法人買賣超（上市＋上櫃）" and two["one"] is None
    assert two["rows"][0]["total"] == -7.4e10 and two["rows"][0]["sides"] == [["上市", True, -7.5e10], ["上櫃", True, 1e9]]
    assert half["rows"][0]["sides"] == [["上市", False, None], ["上櫃", False, None]]     # 都沒有才是「還沒公布」
    html = _html(PAGE)
    assert "instView(ins)" in html and "上櫃 ${side(io" not in html


SUMMARY = """
let summary = { date: "2026-10-08", chips_date: "2026-10-08", margin_date: "2026-10-07",
  markets: { tse: { date: "2026-10-08", chips_date: "2026-10-08", margin_date: "2026-10-07" },
             otc: { date: "2026-10-07", chips_date: "2026-10-07", margin_date: "2026-10-07" } } };
const MKT = { tse: "上市", otc: "上櫃" };
"""


def test_otc_lagging_shows_its_own_dates(tmp_path):
    """上櫃比上市晚：個股詳細的「收盤」日期、籌碼分頁的資料日期用那個市場自己的（審查：舊價格標成 10/08 收盤）。"""
    out = _node(tmp_path, DATES + ("mDate", "DATE_NOTES", "whenText", "dataDateNote"), SUMMARY + """
const night = { date: "2026-10-08", hm: "22:00" };
const old = { date: "2026-10-08" };    // 舊的 summary 沒有各市場的日期
const r = [whenText({ market: "otc" }), whenText({ market: "tse" }),
  dataDateNote("inst", "otc", night).join(""), dataDateNote("inst", "tse", night).join(""),
  dataDateNote("inst", "all", night).join(""), mDate("otc", "margin_date")];
summary = { ...summary, markets: { tse: {}, otc: {} } };
r.push(whenText({ market: "otc" }), dataDateNote("inst", "otc", night).join(""));
console.log(JSON.stringify(r));
""")
    assert out[0] == '<span class="when lag">10/07（三）收盤（上櫃 10/08 的還沒抓到）</span>'
    assert out[1] == '<span class="when">10/08（四）收盤</span>'
    assert out[2] == "三大法人資料日期 10/07（三）（10/08 的還沒抓到）"
    assert out[3] == "三大法人資料日期 10/08（四）"
    assert out[4] == "三大法人資料日期 上市 10/08、上櫃 10/07（10/08 的還沒抓到）"
    assert out[5] == "2026-10-07"
    assert out[6:] == ['<span class="when">10/08（四）收盤</span>', "三大法人資料日期 10/08（四）"]


def test_chips_note_names_the_missing_day(tmp_path):
    """籌碼（基本面）資料比收盤日舊：收盤當天、還沒到公布時間才說「今天的還沒公布」；週末、晚上或抓不到時說哪一天的還沒抓到
    （審查：週六看也說「今天的還沒公布」）。"""
    out = _node(tmp_path, DATES + ("mDate", "DATE_NOTES", "dataDateNote"), SUMMARY + """
summary.chips_date = summary.markets.tse.chips_date = summary.markets.otc.chips_date = "2026-10-07";
summary.valuation_date = summary.markets.tse.valuation_date = "2026-10-08";
summary.markets.otc.valuation_date = "2026-10-07";
const at = (date, hm) => ({ date, hm });
console.log(JSON.stringify([dataDateNote("inst", "all", at("2026-10-08", "15:00")),
  dataDateNote("inst", "all", at("2026-10-08", "16:30")), dataDateNote("inst", "all", at("2026-10-10", "12:00")),
  dataDateNote("margin", "all", at("2026-10-08", "20:59")), dataDateNote("margin", "tse", at("2026-10-09", "08:00")),
  dataDateNote("valuation", "otc", at("2026-10-08", "14:30")), dataDateNote("valuation", "tse", at("2026-10-08", "14:30"))]));
""")
    assert out == [["三大法人資料日期 10/07（三）", "（今天的還沒公布，每天約 16:00 後）"],
                   ["三大法人資料日期 10/07（三）", "（10/08 的還沒抓到）"],
                   ["三大法人資料日期 10/07（三）", "（10/08 的還沒抓到）"],
                   ["融資融券資料日期 10/07（三）", "（今天的還沒公布，每天約 21:00 後）"],
                   ["融資融券資料日期 10/07（三）", "（10/08 的還沒抓到）"],
                   ["本益比、殖利率、淨值比資料日期 10/07（三）", "（今天的還沒公布，每天約 15:00 後）"],
                   ["本益比、殖利率、淨值比資料日期 10/08（四）"]]


def test_hist_cache_key_follows_hist_version(tmp_path):
    """歷史檔的版本看 summary.hist_version：日期都沒變、歷史檔內容變了（補了歷史、上櫃晚到）也要重新下載
    （審查：K 線一直停在舊的）。舊的 summary 沒有 hist_version 時，天數或註記變了也算。"""
    a, b, c, d, e = _node(tmp_path, ("isNum", "hashStr", "histKeyOf"), """
const s = { date: "2026-10-08", chips_date: "2026-10-08", margin_date: "2026-10-08", history_days: 1, notes: ["上櫃行情是 10/07 的"] };
console.log(JSON.stringify([histKeyOf({ ...s, hist_version: "aaa" }), histKeyOf({ ...s, hist_version: "bbb" }),
  histKeyOf(s), histKeyOf({ ...s, history_days: 80 }), histKeyOf({ ...s, notes: [] })]));
""")
    assert a == "aaa" and b == "bbb"
    assert len({c, d, e}) == 3


def test_margin_table_fits_small_phones():
    """360px 手機：融資融券表（6 欄）的格子留白縮小，券資比不會被切掉（審查：表格 348px 放在 336px 的框裡）。"""
    html = _html(PAGE)
    m = re.search(r"@media \(max-width: 400px\) \{(.*?)\n\}", html, re.S)
    assert m and ".t.mini td, .t.mini th { padding: 5px 3px; }" in m.group(1)
