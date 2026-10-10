"""Probe Taiwan exchange public data endpoints (TWSE / TPEx) politely.

Strictly sequential, >= 6 s between requests, <= 45 requests total.
Raw bodies go to probe/raw/<ID>.<ext>.gz, metadata to probe/raw/meta.json.

Usage:
    python probe/tw_probe.py fetch            # fetch all ENDPOINTS in order
    python probe/tw_probe.py fetch ID=URL ... # extra (discovery) fetches
    python probe/tw_probe.py report           # write probe/REPORT.md
"""
import gzip
import json
import os
import sys
import time
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "raw")
META = os.path.join(RAW, "meta.json")
UA = "Mozilla/5.0 (taco0525 probe)"
MIN_GAP = 6.5
MAX_REQ = 45

TW = "https://www.twse.com.tw/rwd/zh"
TP = "https://www.tpex.org.tw"
ENDPOINTS = [
    ("A1", "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"),
    ("A2", "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"),
    ("A3", "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"),
    ("A4", "https://openapi.twse.com.tw/v1/exchangeReport/MI_INDEX"),
    ("B1", f"{TW}/afterTrading/MI_INDEX?date=20261008&type=ALLBUT0999&response=json"),
    ("B2", f"{TW}/afterTrading/MI_INDEX?date=20260601&type=ALLBUT0999&response=json"),
    ("B3", f"{TW}/fund/T86?date=20261008&selectType=ALLBUT0999&response=json"),
    ("B4", f"{TW}/marginTrading/MI_MARGN?date=20261008&selectType=ALL&response=json"),
    ("B5", f"{TW}/afterTrading/BWIBBU_d?date=20261008&selectType=ALL&response=json"),
    ("B6", f"{TW}/fund/BFI82U?type=day&dayDate=20261008&response=json"),
    ("B7", f"{TW}/afterTrading/MI_INDEX?date=20261009&type=ALLBUT0999&response=json"),
    ("B8", f"{TW}/afterTrading/FMTQIK?date=20261001&response=json"),
    ("C1", f"{TP}/openapi/v1/tpex_mainboard_daily_close_quotes"),
    ("C2", f"{TP}/openapi/v1/tpex_mainboard_peratio_analysis"),
    ("C3", f"{TP}/openapi/v1/tpex_3insti_daily_trading"),
    ("C4", f"{TP}/openapi/v1/tpex_mainboard_margin_balance"),
    ("C5", f"{TP}/openapi/v1/mopsfin_t187ap03_O"),
    ("D1", f"{TP}/www/zh-tw/afterTrading/dailyQuotes?date=2026%2F10%2F08&id=&response=json"),
    ("D2", f"{TP}/www/zh-tw/afterTrading/dailyQuotes?date=2026%2F06%2F01&id=&response=json"),
    ("D3", f"{TP}/www/zh-tw/insti/dailyTrade?type=Daily&sect=EW&date=2026%2F10%2F08&response=json"),
    ("D4", f"{TP}/www/zh-tw/margin/balance?date=2026%2F10%2F08&id=&response=json"),
    ("D5", f"{TP}/www/zh-tw/afterTrading/peQryDate?date=2026%2F10%2F08&response=json"),
    ("D6", f"{TP}/www/zh-tw/indexInfo/inx?date=2026%2F10%2F08&response=json"),
    ("D7", f"{TP}/web/stock/aftertrading/daily_close_quotes/stk_quote_result.php?l=zh-tw&d=115/10/08&o=json"),
]

BLOCK_MARKERS = ("CANNOT BE ACCESSED", "FOR SECURITY REASONS")


def load_meta():
    if os.path.exists(META):
        with open(META, encoding="utf-8") as f:
            return json.load(f)
    return {"requests": 0, "last_ts": 0, "blocked_hosts": {}, "results": {}}


def save_meta(meta):
    with open(META, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)


def classify(ctype, body):
    head = body[:200].lstrip()
    if head[:1] in (b"{", b"["):
        return "json"
    if b"<html" in body[:2000].lower() or "html" in (ctype or ""):
        return "html"
    return "txt"


def fetch_one(meta, pid, url):
    import requests

    host = urlparse(url).hostname
    if host in meta["blocked_hosts"]:
        meta["results"][pid] = {"url": url, "skipped": f"host blocked: {meta['blocked_hosts'][host]}"}
        print(pid, "SKIP (host blocked)")
        return
    if meta["requests"] >= MAX_REQ:
        meta["results"][pid] = {"url": url, "skipped": "request budget exhausted"}
        print(pid, "SKIP (budget)")
        return
    wait = meta["last_ts"] + MIN_GAP - time.time()
    if wait > 0:
        time.sleep(wait)
    meta["requests"] += 1
    rec = {"url": url}
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=20, allow_redirects=False)
    except Exception as e:  # noqa: BLE001
        meta["last_ts"] = time.time()
        rec["error"] = f"{type(e).__name__}: {e}"[:500]
        if "ProxyError" in rec["error"] or "403" in rec["error"]:
            meta["blocked_hosts"][host] = f"{pid}: {rec['error'][:120]}"
        meta["results"][pid] = rec
        print(pid, "ERROR", rec["error"][:200])
        return
    meta["last_ts"] = time.time()
    body = r.content
    ctype = r.headers.get("Content-Type", "")
    kind = classify(ctype, body)
    rec.update(status=r.status_code, content_type=ctype, size=len(body), kind=kind,
               location=r.headers.get("Location"))
    fname = f"{pid}.{kind}.gz"
    with gzip.open(os.path.join(RAW, fname), "wb") as f:
        f.write(body)
    rec["file"] = fname
    upper = body[:5000].decode("utf-8", "replace").upper()
    if r.status_code in (301, 302, 303, 307, 308, 403, 429) or any(m in upper for m in BLOCK_MARKERS):
        meta["blocked_hosts"][host] = f"{pid}: HTTP {r.status_code} {kind}"
        rec["blocked_host"] = True
    meta["results"][pid] = rec
    print(pid, r.status_code, ctype, len(body), kind, "BLOCK" if rec.get("blocked_host") else "")


def cmd_fetch(args):
    os.makedirs(RAW, exist_ok=True)
    meta = load_meta()
    todo = ENDPOINTS if not args else [tuple(a.split("=", 1)) for a in args]
    for pid, url in todo:
        fetch_one(meta, pid, url)
        save_meta(meta)
    print("total requests:", meta["requests"], "blocked:", meta["blocked_hosts"])


# ---------------------------------------------------------------- report
def jdump(x, limit=1500):
    s = json.dumps(x, ensure_ascii=False)
    return s if len(s) <= limit else s[:limit] + " …(truncated)"


def find_stock(rows, code):
    out = []
    for row in rows:
        if isinstance(row, dict):
            vals = list(row.values())
        elif isinstance(row, list):
            vals = row
        else:
            continue
        if any(isinstance(v, str) and v.strip() == code for v in vals[:3]):
            out.append(row)
    return out


def code_of(row):
    if isinstance(row, dict):
        for k in ("Code", "SecuritiesCompanyCode", "公司代號", "SecuritiesCode", "CompanyCode", "股票代號"):
            if k in row:
                return str(row[k]).strip()
        v = next(iter(row.values()), "")
        return str(v).strip()
    if isinstance(row, list) and row:
        return str(row[0]).strip()
    return ""


def code_stats(rows):
    codes = [code_of(r) for r in rows]
    four = sum(1 for c in codes if len(c) == 4 and c.isdigit())
    etf = sum(1 for c in codes if c.startswith("00"))
    other = len(codes) - four
    return f"{len(codes)} rows; 4-digit numeric codes: {four}; codes starting '00' (ETF-ish): {etf}; other (warrants/ETNs/etc, non-4-digit): {other}"


def describe_table(lines, rows, fields, code):
    lines.append(f"- rows: {len(rows)}")
    if fields is not None:
        lines.append(f"- fields: `{jdump(fields, 3000)}`")
    if rows:
        lines.append(f"- {code_stats(rows)}")
        for i, r in enumerate(rows[:2]):
            lines.append(f"- row[{i}]: `{jdump(r)}`")
        hits = find_stock(rows, code)
        lines.append(f"- row for {code}: " + ("`" + jdump(hits[0]) + "`" if hits else "not present"))


def cmd_report():
    meta = load_meta()
    L = ["# TW endpoint probe report", "",
         f"Probe run 2026-10-10 (Sat). Last trading day 2026-10-08; 2026-10-09 holiday. "
         f"Total HTTP requests: {meta['requests']}. Blocked hosts: `{jdump(meta['blocked_hosts'])}`", "",
         "Raw bodies: `probe/raw/<ID>.<kind>.gz`. Script: `probe/tw_probe.py`.", ""]
    for pid in sorted(meta["results"], key=lambda p: (p[0], len(p), p)):
        rec = meta["results"][pid]
        code = "6488" if "tpex" in rec["url"] else "2330"
        L += [f"## {pid}", "", f"- URL: `{rec['url']}`"]
        for k in ("skipped", "error"):
            if k in rec:
                L += [f"- **{k.upper()}**: {rec[k]}", ""]
        if "status" not in rec:
            continue
        L.append(f"- HTTP {rec['status']}; content-type `{rec['content_type']}`; size {rec['size']} bytes; body kind: {rec['kind']}")
        if rec.get("location"):
            L.append(f"- Location: `{rec['location']}`")
        with gzip.open(os.path.join(RAW, rec["file"]), "rb") as f:
            body = f.read()
        if rec["kind"] != "json":
            txt = body.decode("utf-8", "replace")
            L.append(f"- body head: `{txt[:400]!r}`")
            L.append("")
            continue
        try:
            data = json.loads(body.decode("utf-8-sig"))
        except Exception as e:  # noqa: BLE001
            L += [f"- JSON parse error: {e}", ""]
            continue
        if isinstance(data, list):
            L.append(f"- list length: {len(data)}")
            if data and isinstance(data[0], dict):
                L.append(f"- keys of item[0]: `{jdump(list(data[0].keys()), 3000)}`")
            describe_table(L, data, None, code)
        elif isinstance(data, dict):
            L.append(f"- top-level keys: `{list(data.keys())}`")
            for k in ("stat", "date", "title", "reportDate", "reportTitle", "iTotalRecords", "totalCount"):
                if k in data:
                    L.append(f"- {k}: `{jdump(data[k], 300)}`")
            if isinstance(data.get("tables"), list):
                for i, t in enumerate(data["tables"]):
                    if not isinstance(t, dict):
                        continue
                    L.append(f"\n### {pid} tables[{i}] — {t.get('title')!r}")
                    L.append(f"- table keys: `{list(t.keys())}`")
                    for k in ("date", "totalCount", "summary", "notes", "groups"):
                        if k in t and t[k]:
                            L.append(f"- {k}: `{jdump(t[k], 600)}`")
                    describe_table(L, t.get("data") or [], t.get("fields"), code)
            if "data" in data and isinstance(data["data"], list):
                L.append("\n### data")
                describe_table(L, data["data"], data.get("fields"), code)
            for k, v in data.items():
                if k.startswith("data") and k != "data" and isinstance(v, list):
                    L.append(f"\n### {k}")
                    fk = "fields" + k[4:]
                    describe_table(L, v, data.get(fk), code)
            if "aaData" in data:
                L.append("\n### aaData")
                describe_table(L, data["aaData"], None, code)
            rest = {k: v for k, v in data.items() if not isinstance(v, list)}
            L.append(f"- non-list values: `{jdump(rest, 800)}`")
        L.append("")
    with open(os.path.join(HERE, "REPORT.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("wrote REPORT.md")


if __name__ == "__main__":
    if sys.argv[1:2] == ["fetch"]:
        cmd_fetch(sys.argv[2:])
    elif sys.argv[1:2] == ["report"]:
        cmd_report()
    else:
        print(__doc__)
