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
