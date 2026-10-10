"""台股總覽網頁（tw_market/static/index.html）與其他頁面連過去的連結。"""
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
