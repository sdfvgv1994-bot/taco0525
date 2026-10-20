"""
自動看盤 手機版（網頁 APP）——只負責網頁與 API，看盤規則全在 engine.py（和文字版、雲端看盤同一套）。
在電腦上執行這個檔案，手機用瀏覽器打開畫面上的網址就能使用：
    在家（同一個 Wi-Fi）：用「在家」網址
    在外面（行動網路）：電腦和手機都裝 Tailscale 並登入同一個帳號，用「在外面」網址
看盤、提醒、自動模擬交易、通知都在電腦上跑，手機只是看與操作的畫面。
電腦關機也想看：用雲端看盤 https://sdfvgv1994-bot.github.io/taco0525/monitor/（另一份帳戶）

執行：python stock_monitor/web_app.py          （Windows 可以雙擊 stock_monitor\啟動手機版.bat）
      python stock_monitor/web_app.py --demo   （示範資料，不連網）

注意：
    和桌面版、文字版共用 stock_monitor/data 的存檔，一次只開一個
    網址裡有一組密碼（key），只給自己的手機用，不要分享
    通知的 Token 只能在電腦本機的瀏覽器設定，而且只寫入、不會傳回畫面
※ 學習用程式，模擬下單不會真的下單，非投資建議。
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if __name__ == "__main__":  # 先自動更新，再匯入 pandas 等套件（Windows 更新套件時檔案才不會被占用）
    from common.updater import check_and_update
    check_and_update()

from stock_monitor import web_api as API  # noqa: E402  網址 → 看盤服務的操作（積木）
from stock_monitor.import_old import auto_import  # noqa: E402  搬舊版存檔
from stock_monitor.live import LiveMonitor  # noqa: E402  看盤服務（和桌面版共用）

HERE = Path(__file__).resolve().parent
WEB_DIR = HERE / "web"
DATA_DIR = HERE / "data"
PORT = 8618


# ====================== 網頁伺服器 ======================

def load_key(store):
    """手機網址用的密碼，第一次執行時隨機產生並存起來（data/web_config.json）。"""
    data = store.load("web_config.json", {})
    if isinstance(data, dict) and data.get("key"):
        return data["key"]
    key = secrets.token_urlsafe(9)
    store.save("web_config.json", {"key": key})
    return key


def lan_ip():
    """找出這台電腦在 Wi-Fi 上的 IP（不會真的送出封包）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


TAILSCALE_NET = ipaddress.ip_network("100.64.0.0/10")   # Tailscale 分配給裝置的位址範圍
_ts_cache = [0.0, None]


def tailscale_info():
    """偵測這台電腦的 Tailscale 狀態。回傳 {"status": ok / not_installed / not_running, "ip", "dns"}。
    30 秒內重複呼叫直接用上次的結果（伺服器開著時才裝 Tailscale 也會自動偵測到）。"""
    if time.time() - _ts_cache[0] < 30 and _ts_cache[1]:
        return _ts_cache[1]
    info = {"status": "not_installed", "ip": "", "dns": ""}
    exe = shutil.which("tailscale")
    default = r"C:\Program Files\Tailscale\tailscale.exe"
    if not exe and os.path.exists(default):
        exe = default
    if exe:
        info["status"] = "not_running"
        try:
            out = subprocess.run([exe, "status", "--json"], capture_output=True, timeout=5,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            data = json.loads(out.stdout.decode("utf-8", "replace") or "{}")
            me = data.get("Self") or {}
            ipv4 = [ip for ip in me.get("TailscaleIPs") or [] if ":" not in ip]
            if data.get("BackendState") == "Running" and ipv4:
                info.update(status="ok", ip=ipv4[0], dns=(me.get("DNSName") or "").rstrip("."))
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    if info["status"] != "ok":   # 沒有指令可用時，直接找網卡上的 100.x 位址
        try:
            for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                ip = item[4][0]
                if ipaddress.ip_address(ip) in TAILSCALE_NET:
                    info.update(status="ok", ip=ip)
                    break
        except OSError:
            pass
    _ts_cache[:] = [time.time(), info]
    return info


def access_urls(key):
    ts = tailscale_info()
    return {
        "home": "http://%s:%d/?key=%s" % (lan_ip(), PORT, key),
        "remote": "http://%s:%d/?key=%s" % (ts["ip"], PORT, key) if ts["status"] == "ok" else "",
        "tailscale": ts["status"],
    }


def make_handler(engine, key):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):   # 不要在視窗洗版
            pass

        def is_local(self):
            """請求是不是從這台電腦自己發出的（不是手機）。經過代理轉發的一律不算本機。"""
            if any(h in self.headers for h in ("X-Forwarded-For", "Forwarded", "Cf-Connecting-Ip", "X-Real-Ip")):
                return False
            try:
                return ipaddress.ip_address(self.client_address[0]).is_loopback
            except ValueError:
                return False

        def authorized(self):
            query = parse_qs(urlparse(self.path).query)
            if secrets.compare_digest(query.get("key", [""])[0], key):
                return True
            jar = cookies.SimpleCookie(self.headers.get("Cookie", ""))
            return "key" in jar and secrets.compare_digest(jar["key"].value, key)

        def send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def send_json(self, obj, code=200):
            self.send(code, json.dumps(obj, ensure_ascii=False))

        def do_GET(self):
            path = urlparse(self.path).path
            if path in ("/manifest.json", "/icon.svg"):   # 加到主畫面用，不含資料
                name = path.lstrip("/")
                ctype = "application/manifest+json" if name.endswith("json") else "image/svg+xml"
                with open(WEB_DIR / name, "rb") as f:
                    return self.send(200, f.read(), ctype)
            if not self.authorized():
                return self.send(403, "<h2 style='font-family:sans-serif'>請用電腦上顯示的網址（含 key）開啟</h2>",
                                 "text/html; charset=utf-8")
            if path == "/":
                with open(WEB_DIR / "index.html", "rb") as f:
                    page = f.read()
                cookie = "key=%s; Path=/; Max-Age=31536000; SameSite=Strict; HttpOnly" % key
                return self.send(200, page, "text/html; charset=utf-8", {"Set-Cookie": cookie})
            if path.startswith("/api/"):
                code, obj = API.get(engine, path, parse_qs(urlparse(self.path).query),
                                    key=key, is_local=self.is_local(), urls=access_urls)
                return self.send_json(obj, code)
            self.send(404, "not found", "text/plain")

        def do_POST(self):
            if not self.authorized():
                return self.send_json({"error": "未授權"}, 403)
            path = urlparse(self.path).path
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}") if length else {}
            except ValueError:
                return self.send_json({"error": "格式錯誤"}, 400)
            code, obj = API.post(engine, path, body, is_local=self.is_local())
            self.send_json(obj, code)

    return Handler


def main(argv=None):
    ap = argparse.ArgumentParser(description="自動看盤 手機版")
    ap.add_argument("--demo", action="store_true", help="使用示範資料（不連網）")
    ap.add_argument("--source", choices=["auto", "twse", "yahoo"], default="auto",
                    help="資料來源：auto（台股用證交所，失敗改 Yahoo）/ twse / yahoo")
    ap.add_argument("--data", default=str(DATA_DIR), help="存檔資料夾")
    ap.add_argument("--no-browser", action="store_true", help="不要自動打開電腦的瀏覽器")
    ap.add_argument("--no-update", action="store_true", help="不要檢查 GitHub 新版")
    args = ap.parse_args(argv)

    moved = auto_import(args.data)
    engine = LiveMonitor(args.data, source="手機版", demo=args.demo, data_source=args.source,
                        cloud=False if args.demo else None)
    for line in moved:
        engine.log(line, "trade")
    key = load_key(engine.store)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), make_handler(engine, key))
    threading.Thread(target=engine.run_forever, kwargs={"respect_auto": False, "news_digest": True},
                     daemon=True).start()

    urls = access_urls(key)
    print("=" * 64)
    print(" 自動看盤 手機版 已啟動" + ("（示範資料）" if args.demo else ""))
    for line in moved:
        print(" " + line)
    print(" 在家（同一個 Wi-Fi）：" + urls["home"])
    if urls["remote"]:
        print(" 在外面（Tailscale）：  " + urls["remote"])
    else:
        print(" 在外面：電腦和手機裝 Tailscale 並登入同一個帳號後，重新整理電腦網頁就會出現網址")
    print(" 電腦打開： http://localhost:%d/?key=%s" % (PORT, key))
    print(" 電腦網頁「設定」分頁有 QR code，手機掃描即可")
    print(" 電腦關機也想看：雲端看盤 https://sdfvgv1994-bot.github.io/taco0525/monitor/")
    print(" 關閉這個視窗就會停止")
    print("=" * 64)
    if not args.no_browser:
        webbrowser.open("http://localhost:%d/?key=%s" % (PORT, key))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
