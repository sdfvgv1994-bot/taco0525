"""財經新聞：從 Google 新聞的 RSS 抓中文財經新聞（會彙整鉅亨網、經濟日報、工商時報、中央社等）。"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote

GOOGLE_NEWS = "https://news.google.com/rss/search?q={q}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
CATEGORIES = [
    ("tw", "台股", "台股 OR 加權指數 OR 櫃買 when:1d"),
    ("us", "美股", "美股 OR 道瓊 OR 那斯達克 OR 費半 when:1d"),
    ("world", "國際", "國際股市 OR 全球股市 OR 日股 OR 港股 OR 歐股 when:1d"),
    ("crypto", "加密貨幣", "比特幣 OR 以太幣 OR 加密貨幣 when:1d"),
]
MAX_ITEMS = 20
TIMEOUT = 15


def feed_url(query: str) -> str:
    return GOOGLE_NEWS.format(q=quote(query))


def parse_rss(xml_text: str | bytes) -> list[dict]:
    """RSS 2.0 → [{"title", "link", "source", "published_utc"}]，新的在前、標題重複的只留一則。"""
    root = ET.fromstring(xml_text)
    items, seen = [], set()
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        if not title or not link.startswith(("http://", "https://")):
            continue
        source = (it.findtext("source") or "").strip()
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3].strip()   # Google 新聞的標題後面會接「 - 來源」
        key = re.sub(r"\s+", "", title)
        if key in seen:
            continue
        seen.add(key)
        published = None
        try:
            dt = parsedate_to_datetime(it.findtext("pubDate") or "")
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            published = dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (TypeError, ValueError):
            pass
        items.append({"title": title, "link": link, "source": source, "published_utc": published})
    items.sort(key=lambda x: x["published_utc"] or "", reverse=True)
    return items


def fetch_news(get=None, log=print) -> dict:
    """抓所有分類；某一類抓不到時只記錄錯誤，不影響其他類。"""
    if get is None:
        import requests

        def get(url):
            r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": "Mozilla/5.0 (taco0525)"})
            r.raise_for_status()
            return r.content
    cats = []
    for key, name, query in CATEGORIES:
        try:
            items = parse_rss(get(feed_url(query)))[:MAX_ITEMS]
            cats.append({"key": key, "name": name, "items": items})
        except Exception as e:  # noqa: BLE001 - 一類失敗不影響其他類
            log(f"  ⚠ {name}新聞抓取失敗：{e}")
            cats.append({"key": key, "name": name, "items": [], "error": str(e)})
    return {"updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "categories": cats}


def demo_news() -> dict:
    """示範模式用的假新聞（清楚標示是示範）。"""
    now = datetime.now(timezone.utc)
    cats = []
    for i, (key, name, _) in enumerate(CATEGORIES):
        items = [{"title": f"【示範】{name}新聞標題 {j + 1}：這裡會顯示真實的財經新聞",
                  "link": "https://news.google.com/", "source": "示範資料",
                  "published_utc": (now - timedelta(minutes=37 * j + 11 * i)).strftime("%Y-%m-%dT%H:%M:%SZ")}
                 for j in range(6)]
        cats.append({"key": key, "name": name, "items": items})
    return {"updated_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "categories": cats}


def merge_with_previous(new: dict, previous: dict | None) -> dict:
    """某一類這次抓不到時，沿用上一次成功的新聞（標記 stale）。"""
    if not previous:
        return new
    prev = {c["key"]: c for c in previous.get("categories", [])}
    for c in new["categories"]:
        if not c["items"] and prev.get(c["key"], {}).get("items"):
            c["items"] = prev[c["key"]]["items"]
            c["stale"] = True
    return new
