"""
財經新聞積木：抓各家公開的 RSS / 新聞列表，只取標題、來源、時間與連結（不轉載內文）。
給手機版 web_app.py 的「新聞」分頁與每日新聞推播使用。

來源：鉅亨網、Yahoo 股市、經濟日報；商業周刊、今周刊、財訊沒有公開 RSS，改用 Google 新聞搜尋並只保留該媒體的新聞。
※ 新聞版權屬於各媒體，點連結到原網站閱讀。
"""

import datetime as dt
import email.utils
import re
import threading
import time
import xml.etree.ElementTree as ET
from urllib.parse import quote, unquote

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/128.0 Safari/537.36"}
CNYES_API = "https://api.cnyes.com/media/api/v1/newslist/category/%s?limit=30"
GOOGLE_NEWS = "https://news.google.com/rss/search?q=%s&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
REGIONS = {"tw": "台股", "intl": "國際", "mag": "財經雜誌"}
PER_SOURCE = 30   # 每個來源最多取幾則，避免量大的來源把其他來源擠掉

# 雜誌網站什麼都有（政治、生活），加上財經關鍵字讓 Google 優先給財經文章
FIN_WORDS = "(股 OR 投資 OR 經濟 OR 理財 OR 產業 OR 營收 OR 財報 OR 央行 OR 美股 OR 台股 OR ETF OR 半導體)"


# Google 的關鍵字比對很寬鬆，雜誌標題要再含有下面任一個財經字詞才保留（擋掉政治、生活新聞）
FIN_TITLE = re.compile(r"股|投資|經濟|理財|產業|營收|財報|央行|美股|台股|ETF|半導體|晶片|AI|利率|降息|升息|通膨|"
                       r"債|匯|油價|柴油|關稅|基金|保險|銀行|金控|法說|獲利|市場|出口|景氣|供應鏈|電動車|房市|房價|"
                       r"退休|薪|存股|配息|股息|殖利率|外資|聯準會|Fed|企業|公司|品牌|商機|創業|併購|IPO|上市|"
                       r"賺|錢|億|兆|萬元|補助|消費|零售|科技|鴻海|台積|輝達|蘋果|特斯拉", re.I)


def google(site):
    # 要用 www.，不然商周只會找到旗下的健康、教育網站
    return GOOGLE_NEWS % quote("site:%s %s when:3d" % (site, FIN_WORDS))


SOURCES = [
    # (代號, 顯示名稱, 分類, 抓法, 網址或參數, Google 新聞要保留的來源名稱（完全相同才算）)
    ("cnyes_tw", "鉅亨網", "tw", "cnyes", "tw_stock", None),
    ("cnyes_intl", "鉅亨網", "intl", "cnyes", "wd_stock", None),
    ("yahoo_tw", "Yahoo股市", "tw", "rss", "https://tw.stock.yahoo.com/rss?category=tw-market", None),
    ("yahoo_intl", "Yahoo股市", "intl", "rss", "https://tw.stock.yahoo.com/rss?category=intl-markets", None),
    ("udn", "經濟日報", "tw", "rss", "https://money.udn.com/rssfeed/news/1001/5591?ch=money", None),
    ("bw", "商業周刊", "mag", "rss", google("www.businessweekly.com.tw"), ("商業周刊", "商周")),
    ("bt", "今周刊", "mag", "rss", google("www.businesstoday.com.tw"), ("今周刊",)),
    ("wealth", "財訊", "mag", "rss", google("www.wealth.com.tw"), ("財訊",)),   # 不含「財訊商城」
]


def clean_title(title):
    """去掉 Google 新聞標題的媒體名、商周的「商周-」前綴與「第2030期」期數。"""
    title = re.sub(r"\s+-\s+[^-]+$", "", title)            # 「… - 商業周刊」
    title = re.sub(r"^商周-", "", title)
    title = re.sub(r"\s*-\s*第\d+期$", "", title)
    return title.strip()

_cache = {"time": 0.0, "data": None}
_lock = threading.Lock()


def parse_time(text):
    """RSS 的各種時間格式 -> epoch 秒；看不懂就回傳 0。"""
    if not text:
        return 0
    text = text.strip()
    try:
        return email.utils.parsedate_to_datetime(text).timestamp()   # Sat, 10 Oct 2026 07:20:00 GMT
    except (TypeError, ValueError):
        pass
    try:
        d = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))  # 2026-10-10T15:12:23
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone(dt.timedelta(hours=8)))   # 沒寫時區的當成台灣時間
        return d.timestamp()
    except ValueError:
        return 0


def fetch_cnyes(category):
    r = requests.get(CNYES_API % category, headers=UA, timeout=15)
    r.raise_for_status()
    items = (r.json().get("items") or {}).get("data") or []
    return [{"title": it["title"], "link": "https://news.cnyes.com/news/id/%s" % it["newsId"],
             "time": float(it.get("publishAt") or 0)} for it in items if it.get("title")]


def fetch_rss(url, keep_sources=None):
    r = requests.get(url, headers=UA, timeout=15)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    out = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        if keep_sources is not None:   # Google 新聞：只留指定媒體（名稱完全相同），並整理標題
            src = it.find("source")
            src_name = ((src.text or "") if src is not None else "").strip()
            if src_name not in keep_sources:
                continue
            title = clean_title(title)
            if title.startswith("http") or "測試" in title:   # 沒有標題、測試文章
                continue
            if not FIN_TITLE.search(title):                    # 不是財經新聞
                continue
        if title and link:
            out.append({"title": title, "link": link, "time": parse_time(it.findtext("pubDate"))})
    return out


def normalize(title):
    return re.sub(r"[\s　「」『』【】《》()（）!！?？,，.。:：、\-—|｜]", "", title).lower()


def fetch_all(max_age_days=3):
    """抓全部來源，去除重複標題，新到舊排序。回傳 (新聞, 各來源狀態)。"""
    items, status, seen = [], {}, set()
    cutoff = time.time() - max_age_days * 86400
    for sid, name, region, kind, arg, keep in SOURCES:
        try:
            got = fetch_cnyes(arg) if kind == "cnyes" else fetch_rss(arg, keep)
            got = sorted(got, key=lambda x: x["time"], reverse=True)[:PER_SOURCE]
            status[sid] = {"name": name, "region": region, "ok": True, "count": len(got)}
        except (requests.RequestException, ET.ParseError, ValueError, KeyError) as exc:
            status[sid] = {"name": name, "region": region, "ok": False, "error": exc.__class__.__name__}
            continue
        for it in got:
            key = normalize(it["title"])
            if key in seen or (it["time"] and it["time"] < cutoff):
                continue
            seen.add(key)
            items.append(dict(it, source=name, region=region))
    items.sort(key=lambda x: x["time"], reverse=True)
    return items, status


def get_news(ttl=900, force=False):
    """15 分鐘內用快取，避免一直打新聞網站。"""
    with _lock:
        if not force and _cache["data"] and time.time() - _cache["time"] < ttl:
            return _cache["data"]
    items, status = fetch_all()
    data = {"items": items, "status": status, "updated": time.time()}
    with _lock:
        _cache.update(time=time.time(), data=data)
    return data


def tag_watchlist(items, watch):
    """watch: [(ticker, 中文名)]。標題提到自選股（名稱或代號）就加上 tags。"""
    out = []
    for it in items:
        tags = [t for t, name in watch
                if (name and len(name) >= 2 and name in it["title"]) or (t.isdigit() and re.search(r"(?<!\d)%s(?!\d)" % t, it["title"]))]
        out.append(dict(it, tags=tags))
    return out


def digest_text(items, watch, per_region=5, now=None):
    """組出每日新聞推播的文字：自選股相關優先，其他每類取幾則，只放標題（自選股相關附連結）。"""
    now = now or dt.datetime.now()
    tagged = tag_watchlist(items, watch)
    names = dict(watch)
    lines = ["📰 每日財經新聞 %s（%s）" % (now.strftime("%m/%d"), "一二三四五六日"[now.weekday()])]
    # 自選股相關：每檔最多 2 則，避免熱門股（例如台積電）把其他自選股的新聞擠掉
    mine, per_stock = [], {}
    for it in tagged:
        if it["tags"] and len(mine) < 6 and any(per_stock.get(t, 0) < 2 for t in it["tags"]):
            mine.append(it)
            for t in it["tags"]:
                per_stock[t] = per_stock.get(t, 0) + 1
    if mine:
        lines.append("\n⭐ 自選股相關")
        for it in mine:
            who = "、".join(names.get(t) or t for t in it["tags"])
            # Yahoo 的網址把中文編碼成 %E5%8F… 很長，解回中文短很多，手機一樣點得開
            lines.append("• [%s] %s（%s）\n  %s" % (who, it["title"], it["source"], unquote(it["link"])))
    used = {id(it) for it in mine}
    for region, label in REGIONS.items():
        picked = [it for it in tagged if it["region"] == region and id(it) not in used][:per_region]
        if picked:
            lines.append("\n%s %s" % ({"tw": "🇹🇼", "intl": "🌐", "mag": "📖"}[region], label))
            lines.extend("• %s（%s）" % (it["title"], it["source"]) for it in picked)
    if len(lines) == 1:
        lines.append("今天抓不到新聞，請稍後在 App 的「新聞」分頁查看")
    else:
        lines.append("\n完整新聞請看 App 的「新聞」分頁")
    return "\n".join(lines)
