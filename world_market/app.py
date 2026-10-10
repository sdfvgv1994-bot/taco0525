"""全球股市看板。

執行：python world_market/app.py            會自動開啟瀏覽器 http://127.0.0.1:8050
      python world_market/app.py --demo     示範資料（不連網）
      python world_market/app.py --text     不開網頁，直接在終端機印出總覽
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import unicodedata
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world_market.data import OVERVIEW_TTL, DataService  # noqa: E402

STATIC = Path(__file__).parent / "static"


def make_handler(service: DataService):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # 不要每個請求都印一行
            pass

        def _send(self, code: int, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def do_GET(self):
            url = urlparse(self.path)
            q = parse_qs(url.query)
            try:
                if url.path in ("/", "/index.html"):
                    self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
                elif url.path == "/api/overview":
                    self._json(200, service.overview(force=q.get("force") == ["1"]))
                elif url.path == "/api/history":
                    sym = q.get("symbol", [""])[0]
                    period = q.get("period", ["1y"])[0]
                    self._json(200, service.history(sym, period))
                else:
                    self._json(404, {"error": "找不到這個頁面"})
            except (KeyError, ValueError) as e:
                self._json(400, {"error": str(e.args[0] if e.args else e)})
            except Exception as e:  # noqa: BLE001 - 回傳錯誤給網頁顯示，伺服器不中斷
                self._json(500, {"error": str(e)})

    return Handler


def background_refresh(service: DataService, stop: threading.Event):
    """背景定時更新，網頁打開時資料已經準備好。"""
    while not stop.is_set():
        try:
            service.overview(force=True)
        except Exception as e:  # noqa: BLE001
            print(f"⚠ 更新失敗：{e}", file=sys.stderr)
        stop.wait(OVERVIEW_TTL)


def pad(text: str, width: int) -> str:
    w = sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)
    return text + " " * max(0, width - w)


def print_text(service: DataService):
    ov = service.overview()
    print(f"\n全球股市總覽　更新時間 {ov['updated']}" + ("（示範資料）" if ov["demo"] else ""))
    print("  ".join(f"{c['city']} {c['time']}{'●' if c['status'] == 'open' else '○'}"
                    for c in ov["clocks"]) + "　（● 交易中）")
    for g in ov["groups"]:
        print(f"\n【{g['name']}】")
        for it in g["items"]:
            name = pad(it["name"], 20)
            if "error" in it:
                print(f"  {name}⚠ {it['error']}")
                continue
            d = it["decimals"]
            pct = it.get("change_pct")
            arrow = "▲" if pct and pct > 0 else "▼" if pct and pct < 0 else " "
            pct_s = f"{arrow}{pct:+6.2f}%" if pct is not None else ""
            dot = "●" if it["status"] == "open" else "○"
            print(f"  {name}{it['price']:>14,.{d}f}  {pct_s:<10} {dot}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="全球股市看板")
    ap.add_argument("--demo", action="store_true", help="示範資料（不連網）")
    ap.add_argument("--text", action="store_true", help="在終端機印出總覽就結束")
    ap.add_argument("--port", type=int, default=8050)
    ap.add_argument("--no-browser", action="store_true", help="不要自動開瀏覽器")
    args = ap.parse_args(argv)

    service = DataService(demo=args.demo)
    if args.text:
        print_text(service)
        return

    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(service))
    stop = threading.Event()
    threading.Thread(target=background_refresh, args=(service, stop), daemon=True).start()
    url = f"http://127.0.0.1:{args.port}"
    print(f"全球股市看板已啟動：{url}（按 Ctrl+C 結束）")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已結束")
    finally:
        stop.set()
        server.server_close()


if __name__ == "__main__":
    from common.updater import check_and_update
    check_and_update()
    main()
