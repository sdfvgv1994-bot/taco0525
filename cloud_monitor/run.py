"""雲端看盤：GitHub Actions 每 15 分鐘執行一次（和全球看板同一個排程）。

    python cloud_monitor/run.py --state _state --out _site/monitor

- 設定來自 cloud_monitor/config.toml（在 GitHub 網頁就能改）
- 帳戶、提醒紀錄、股名、資產走勢存在 --state 資料夾（雲端是 monitor-data 分支），每次執行後存回去
- 只在該檔所屬市場的交易時間內、而且已經有今天的 K 棒時才判斷訊號、自動交易；
  休市時（含國定假日）只更新報價顯示
- 歷史資料用 Yahoo（有今天的延遲價格）；台股另外試證交所即時報價
"""
from __future__ import annotations

import argparse
import difflib
import json
import math
import os
import re
import shutil
import sys
import tomllib
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from cloud_monitor import notify  # noqa: E402
from common import strategies as strat  # noqa: E402
from common.market import parse_symbol, uses_twse  # noqa: E402
from stock_monitor.engine import Monitor  # noqa: E402
from stock_monitor.signals import strategy_params as effective_params  # noqa: E402
from stock_monitor.storage import DEFAULT_SETTINGS, Store  # noqa: E402
from world_market.data import yahoo_code  # noqa: E402
from world_market.markets import ALL_ITEMS, EXCHANGES, market_status  # noqa: E402

TPE = ZoneInfo("Asia/Taipei")
HERE = Path(__file__).resolve().parent
CONFIG = HERE / "config.toml"
PAGE = HERE / "index.html"
EDIT_URL = "https://github.com/sdfvgv1994-bot/taco0525/edit/main/cloud_monitor/config.toml"
EQUITY_MAX = 5000
HISTORY_BARS = 240   # 雲端抓 1 年的日 K（約 240 個交易日），均線和策略的天數不能超過
NOTIFY_DEFAULT = {"enabled": True, "trades": True, "signals": "primary", "price_alerts": True}
NUMBER_SETTINGS = {"auto_buy_amount": (1, 1_000_000_000), "stop_loss_pct": (0, 100),
                   "short_ma": (1, HISTORY_BARS - 1), "long_ma": (2, HISTORY_BARS)}
# 各段落可以寫的設定（"" 是檔案最上面、還沒有任何 [段落] 標題的地方）
SECTION_KEYS = {
    "": ["watchlist", "settings", "limits", "strategy_params", "notify"],
    "settings": ["strategies", "auto_trade", *NUMBER_SETTINGS],
    "notify": ["enabled", "trades", "signals", "price_alerts"],
}
WINDOW_PARAMS = {"short", "long", "n", "fast", "slow", "signal", "entry", "exit"}
LEVEL_PARAMS = {"low", "high"}
# 手機輸入法常打出來的全形符號、彎引號：自動當成半形
FULLWIDTH = {"“": '"', "”": '"', "„": '"', "＂": '"', "‘": "'", "’": "'", "＇": "'",
             "，": ",", "＝": "=", "　": " ", "［": "[", "］": "]", "｛": "{", "｝": "}",
             "．": ".", "－": "-", "＋": "+", "％": "%", "＃": "#",
             **{chr(0xFF10 + i): str(i) for i in range(10)}}
TOML_HINTS = [
    ("Invalid value", "值看不懂：文字要用半形雙引號 \" 括起來，數字後面不能加單位"),
    ("Unclosed array", "清單 [ ] 沒有正確結束：項目之間用半形逗號 , 分開，最後要有 ]"),
    ("Unclosed inline table", "{ } 沒有正確結束"),
    ("Expected '=' after a key", "設定名稱後面要接半形等號 ="),
    ("Expected newline or end of document", "一行只能寫一個設定，值的後面不能再接其他文字"),
    ("Invalid initial character for a key part", "有看不懂的字（{ } 的最後不能多一個逗號）"),
    ("Cannot overwrite a value", "同一個設定寫了兩次"),
    ("Cannot declare", "同一個 [段落] 寫了兩次"),
    ("Expected ']' at the end of a table declaration", "[段落] 的標題少了 ]"),
    ("Illegal character", "引號沒有成對"),
    ("Unterminated string", "引號沒有成對"),
    ("Invalid statement", "這一行看不懂"),
]
# Yahoo 代號的後綴 → 交易所（決定什麼時候算交易時間）
SUFFIX_EXCHANGE = {
    ".T": "JP", ".HK": "HK", ".KS": "KR", ".KQ": "KR", ".SS": "CN", ".SZ": "CN", ".SI": "SG",
    ".NS": "IN", ".BO": "IN", ".AX": "AU", ".L": "UK", ".DE": "EU", ".F": "EU", ".PA": "EU",
    ".AS": "EU", ".MI": "EU", ".MC": "EU", ".SW": "EU", ".BR": "EU", ".VI": "EU",
    ".TO": "CA", ".V": "CA", ".SA": "BR",
}


class ConfigError(Exception):
    pass


# ---------------------------------------------------------------- 設定檔
def normalize_text(text: str) -> tuple[str, list]:
    """把註解以外的全形符號、彎引號換成半形，拿掉數字後面的 %。回傳 (新內容, 警告)。"""
    out_lines, warnings = [], []
    for no, line in enumerate(text.split("\n"), 1):
        out, quote, fixed, i = [], None, [], 0
        while i < len(line):
            ch = line[i]
            if quote:                                   # 字串裡面
                if FULLWIDTH.get(ch) == quote:          # 用彎引號開頭的字串，也用彎引號結束
                    fixed.append(ch)
                    ch = quote
                out.append(ch)
                if ch == "\\" and quote == '"' and i + 1 < len(line):
                    out.append(line[i + 1])
                    i += 1
                elif ch == quote:
                    quote = None
                i += 1
                continue
            if ch == "#":                               # 註解：原樣保留
                out.append(line[i:])
                break
            mapped = FULLWIDTH.get(ch, ch)
            if mapped != ch:
                fixed.append(ch)
            if mapped == "%" and "".join(out).rstrip()[-1:].isdigit():
                if ch == "%":
                    fixed.append(ch)
                i += 1                                  # 停損 8% → 8
                continue
            out.append(mapped)
            if mapped in "\"'":
                quote = mapped
            i += 1
        if fixed:
            shown = " ".join(dict.fromkeys("全形空白" if c == "　" else c for c in fixed))
            warnings.append(f"第 {no} 行有全形符號、彎引號或 %（{shown}），已自動當成半形處理，"
                            f"建議改成半形")
        out_lines.append("".join(out))
    return "\n".join(out_lines), warnings


def explain_toml_error(e: tomllib.TOMLDecodeError, text: str) -> str:
    msg = str(e)
    m = re.search(r"at line (\d+)", msg)
    where = ""
    if m:
        no = int(m.group(1))
        lines = text.split("\n")
        snippet = lines[no - 1].strip() if 0 < no <= len(lines) else ""
        where = f"第 {no} 行" + (f"「{snippet[:60]}」" if snippet else "")
    hint = next((h for k, h in TOML_HINTS if msg.startswith(k)), "")
    return f"設定檔格式錯誤：{where}{'，' if where and hint else ''}{hint}（{msg}）"


def unknown_key(key: str, section: str) -> str:
    """看不懂的設定名稱：放錯段落就說該放哪，拼錯就猜一個。"""
    for sec, keys in SECTION_KEYS.items():
        if sec != section and key in keys:
            if sec == "":
                return (f"「{key}」要寫在檔案最上面（任何 [段落] 標題之前），"
                        f"寫在 [{section}] 後面的設定都會算在 [{section}] 裡")
            here = f"[{section}] 裡" if section else "檔案最上面"
            return (f"「{key}」寫在{here}了，要搬到 [{sec}] 底下"
                    f"（寫在某個 [段落] 標題後面的設定都會算在那一段）")
    close = difflib.get_close_matches(str(key).lower(), SECTION_KEYS.get(section, []), n=1, cutoff=0.6)
    where = f"[{section}] 裡" if section else ""
    return f"{where}看不懂設定「{key}」" + (f"，是不是要寫「{close[0]}」？" if close else "，先略過")


def _looks_like_auto_trade(key) -> bool:
    return bool(difflib.get_close_matches(str(key).lower(), ["auto_trade"], cutoff=0.7))


def _stray_keys(raw: dict) -> list:
    """各段落裡看不懂的設定名稱（用來找打錯字或放錯段落的 auto_trade）。"""
    out = []
    for key, v in raw.items():
        if key not in SECTION_KEYS[""]:
            out.append(key)
            if isinstance(v, dict):          # 例如 [Settings]：裡面的設定也算
                out += list(v)
    for sec in ("settings", "notify"):
        if isinstance(raw.get(sec), dict):
            out += [k for k in raw[sec] if k not in SECTION_KEYS[sec]]
    if isinstance(raw.get("strategy_params"), dict):
        out += [k for k in raw["strategy_params"] if str(k).lower() not in strat.STRATEGIES]
    if isinstance(raw.get("limits"), dict):
        out += [k for k, v in raw["limits"].items() if not isinstance(v, dict)]
    return out


def _synthetic(n: int) -> pd.DataFrame:
    """檢查策略參數用的假日 K（有漲有跌）。"""
    x = np.arange(n, dtype=float)
    c = pd.Series(100 + 10 * np.sin(x / 7) + x * 0.05)
    return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": 0.0})


def check_strategy(key: str, params: dict, settings: dict) -> str | None:
    """參數不能用時回傳原因（例如快線比慢線長、天數超過雲端有的資料）。"""
    st = strat.get(key)
    eff = effective_params({**DEFAULT_SETTINGS, **settings, "strategy_params": {key: params}}, key)
    p = {**st.defaults, **eff}
    for k, v in p.items():
        if k in WINDOW_PARAMS and not 1 <= v <= HISTORY_BARS:
            return f"{k} 要在 1～{HISTORY_BARS} 之間"
        if k in LEVEL_PARAMS and not 0 < v < 100:
            return f"{k} 要在 0～100 之間"
        if k == "k" and not v > 0:
            return "k 要大於 0"
    need = int(st.warmup(p)) + 2
    if need > HISTORY_BARS:
        return f"需要 {need} 天的資料，雲端只有大約 {HISTORY_BARS} 天"
    try:
        st.run(_synthetic(need + 20), **eff)
    except Exception as e:  # noqa: BLE001 - 任何錯誤都代表這組參數不能用
        return str(e) or type(e).__name__
    return None


def load_config(path: Path) -> tuple[dict, list]:
    """回傳 (設定, 警告)。格式錯到無法使用時丟 ConfigError。

    設定 = {"watchlist": [...], "limits": {...}, "settings": {...}, "strategy_params": {...},
            "notify": {...}}
    """
    try:
        # utf-8-sig：Windows 記事本存檔時開頭會多一個 BOM
        text = Path(path).read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise ConfigError("找不到設定檔 cloud_monitor/config.toml") from None
    except UnicodeDecodeError:
        raise ConfigError("設定檔不是 UTF-8 編碼，請用 GitHub 網頁編輯，或另存成 UTF-8") from None
    text, warnings = normalize_text(text)
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(explain_toml_error(e, text)) from None
    try:
        return _validate(raw, warnings)
    except ConfigError:
        raise
    except Exception as e:  # noqa: BLE001 - 沒想到的寫法也不要讓整個看盤掛掉
        raise ConfigError(f"設定檔內容看不懂：{e}") from None


def _validate(raw: dict, warnings: list) -> tuple[dict, list]:
    for key in raw:
        if key not in SECTION_KEYS[""]:
            warnings.append(unknown_key(key, ""))

    if "watchlist" not in raw:
        warnings.append('沒有設定自選股：檔案最上面要有一行 watchlist = ["2330", "AAPL"]')
    wl = raw.get("watchlist", [])
    if not isinstance(wl, list) or not all(isinstance(x, (str, int)) for x in wl):
        raise ConfigError('watchlist 要寫成清單，例如 watchlist = ["2330", "AAPL"]')
    watchlist = []
    for x in wl:
        code = str(x).strip().upper()
        try:
            parse_symbol(code)
        except ValueError as e:
            warnings.append(f"自選股「{x}」看不懂，先略過（{e}）")
            continue
        if code not in watchlist:
            watchlist.append(code)
            if exchange_of(code) is None:
                warnings.append(f"「{code}」不知道是哪個市場、什麼時候交易，只顯示報價，"
                                f"不判斷訊號也不自動交易")
    if "watchlist" in raw and not watchlist:
        warnings.append("自選股是空的")

    s = raw.get("settings", {})
    if not isinstance(s, dict):
        raise ConfigError("[settings] 的格式不對")
    for key in s:
        if key not in SECTION_KEYS["settings"]:
            warnings.append(unknown_key(key, "settings"))
    settings = {}
    keys = s.get("strategies", ["ma"])
    if isinstance(keys, str):
        keys = [keys]
    good = []
    for k in keys if isinstance(keys, list) else []:
        try:
            good.append(strat.get(str(k)).key)
        except ValueError:
            warnings.append(f"沒有「{k}」這個策略，可用：{', '.join(strat.STRATEGIES)}")
    settings["strategies"] = list(dict.fromkeys(good)) or ["ma"]
    if "auto_trade" in s:
        if not isinstance(s["auto_trade"], bool):
            warnings.append("auto_trade 只能填 true 或 false，先維持關閉")
            settings["auto_trade"] = False
        else:
            settings["auto_trade"] = s["auto_trade"]
    elif any(_looks_like_auto_trade(k) for k in _stray_keys(raw)):
        settings["auto_trade"] = False   # auto_trade 打錯字或放錯段落：為了安全先關閉
        warnings.append("auto_trade 好像打錯字或放錯段落了，為了安全先關閉自動交易")
    for key, (lo, hi) in NUMBER_SETTINGS.items():
        if key not in s:
            continue
        v = s[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
            warnings.append(f"{key} 要填 {lo:,}～{hi:,} 的數字，先用預設值")
            continue
        settings[key] = int(v) if key.endswith("_ma") else float(v)
    if settings.get("short_ma", DEFAULT_SETTINGS["short_ma"]) >= settings.get("long_ma", DEFAULT_SETTINGS["long_ma"]):
        warnings.append("short_ma 要比 long_ma 小，先用 5 / 20")
        settings["short_ma"], settings["long_ma"] = 5, 20

    limits = {}
    lim_raw = raw.get("limits", {})
    if not isinstance(lim_raw, dict):
        warnings.append('[limits] 的格式不對，例："2330" = { lower = 900, upper = 1200 }')
        lim_raw = {}
    for code, v in lim_raw.items():
        c = str(code).strip().upper()
        if any(code in keys for keys in SECTION_KEYS.values()):
            warnings.append(unknown_key(code, "limits"))
            continue
        if not isinstance(v, dict):
            warnings.append(f"「{code}」的上下限格式不對，例：\"2330\" = {{ lower = 900, upper = 1200 }}")
            continue
        for k in v:
            if k not in ("lower", "upper"):
                close = difflib.get_close_matches(str(k).lower(), ["lower", "upper"], n=1, cutoff=0.5)
                warnings.append(f"「{code}」的上下限只能寫 lower、upper，看不懂「{k}」" +
                                (f"，是不是要寫「{close[0]}」？" if close else ""))
        lo, hi = v.get("lower"), v.get("upper")
        bad = [x for x in (lo, hi) if x is not None and (isinstance(x, bool) or not isinstance(x, (int, float)))]
        if bad or (lo is not None and hi is not None and lo >= hi):
            warnings.append(f"「{code}」的上下限要是數字，而且下限要小於上限")
            continue
        if lo is None and hi is None:
            continue
        if c not in watchlist:
            warnings.append(f"「{code}」有設上下限，但不在自選股裡，所以不會提醒")
        limits[c] = {"lower": lo, "upper": hi}

    params = {}
    p_raw = raw.get("strategy_params", {})
    if not isinstance(p_raw, dict):
        warnings.append("[strategy_params] 的格式不對，例：rsi = { low = 25, high = 75 }")
        p_raw = {}
    for key, p in p_raw.items():
        try:
            st = strat.get(str(key))
        except ValueError:
            if any(key in keys for keys in SECTION_KEYS.values()):
                warnings.append(unknown_key(key, "strategy_params"))
            else:
                warnings.append(f"strategy_params 裡沒有「{key}」這個策略，可用：{', '.join(strat.STRATEGIES)}")
            continue
        if not isinstance(p, dict):
            warnings.append(f"{key} 的參數格式不對，例：rsi = {{ low = 25, high = 75 }}")
            continue
        unknown = [k for k in p if k not in st.defaults]
        if unknown:
            warnings.append(f"{st.name} 沒有參數 {', '.join(unknown)}（可用：{', '.join(st.defaults)}）")
        nums = {}
        for k, v in p.items():
            if k not in st.defaults:
                continue
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                warnings.append(f"{st.name} 的 {k} 要填數字（不用加引號），先用預設值")
                continue
            nums[k] = float(v)
        problem = check_strategy(st.key, nums, settings)
        if problem:
            warnings.append(f"{st.name} 的參數不能用（{problem}），先用預設值")
            continue
        params[st.key] = nums
    for key in settings["strategies"]:   # 沒自訂參數的策略也檢查（均線會沿用 short_ma / long_ma）
        if key not in params:
            problem = check_strategy(key, {}, settings)
            if problem:
                warnings.append(f"{strat.get(key).name} 沒辦法判斷（{problem}）")

    n = raw.get("notify", {})
    notify_cfg = dict(NOTIFY_DEFAULT)
    if not isinstance(n, dict):
        warnings.append("[notify] 的格式不對，先用預設值")
        n = {}
    for key in n:
        if key not in SECTION_KEYS["notify"]:
            warnings.append(unknown_key(key, "notify"))
    for key in ("enabled", "trades", "price_alerts"):
        if key in n:
            if isinstance(n[key], bool):
                notify_cfg[key] = n[key]
            else:
                warnings.append(f"notify 的 {key} 只能填 true 或 false")
    if "signals" in n:
        if n["signals"] in ("primary", "all", "none"):
            notify_cfg["signals"] = n["signals"]
        else:
            warnings.append('notify 的 signals 只能填 "primary"、"all" 或 "none"')
    return {"watchlist": watchlist, "limits": limits, "settings": settings,
            "strategy_params": params, "notify": notify_cfg}, warnings


# ---------------------------------------------------------------- 交易時間
def exchange_of(name: str) -> str | None:
    """這檔在哪個交易所交易（EXCHANGES 的 key）；不知道時回傳 None。"""
    sym = parse_symbol(name)
    code = sym.candidates[0]
    if uses_twse(sym) or code in ("^TWOII",):
        return "TW"
    if sym.kind == "crypto":
        return "CRYPTO"
    for it in ALL_ITEMS.values():
        if code in (it.symbol, yahoo_code(it)):
            return it.exchange
    if code.endswith(("=X", "=F")):
        return "FX"
    if "." in code:
        return SUFFIX_EXCHANGE.get(code[code.rfind("."):])
    if re.fullmatch(r"[A-Z]{1,5}(-[A-Z]{1,2})?", code):   # 美股代號，例如 AAPL、BRK-B
        return "US"
    return None


def is_active(name: str, now: datetime) -> bool:
    """這檔現在是不是交易時間（now 是台北時間、不帶時區）。"""
    ex = exchange_of(name)
    return ex is not None and market_status(ex, now.replace(tzinfo=TPE)) == "open"


def session_date(name: str, now: datetime) -> str | None:
    """這檔所屬市場「今天」的日期（當地時間）。最後一根 K 棒比這個舊，就代表休市或資料還沒更新。"""
    ex = exchange_of(name)
    if ex is None:
        return None
    return now.replace(tzinfo=TPE).astimezone(ZoneInfo(EXCHANGES[ex].tz)).strftime("%Y-%m-%d")


# ---------------------------------------------------------------- 執行
def prefetch_names(m, now: datetime, log=print) -> None:
    """還沒有股名的台股，向證交所 / 櫃買中心查一次當月資料，從標題取出股名（之後存在 names.json）。
    雲端機器常拿不到即時報價，這樣股名才不會空白。"""
    from common import twse
    cl = twse.client()
    for code in m.watchlist:
        sym = parse_symbol(code)
        if not sym.is_tw_stock or code in m.names:
            continue
        base = sym.candidates[0].split(".")[0]
        try:
            name = cl.cached_name(base)
            if not name:
                if cl.stock_month(base, now.year, now.month).empty and base not in cl.names:
                    cl.tpex_month(base, now.year, now.month)
                name = cl.names.get(base)
        except Exception as e:  # noqa: BLE001 - 查不到股名不影響看盤
            log(f"  ⚠ {code} 股名查詢失敗：{e}")
            continue
        if name:
            m.names[code] = name


def finite(x):
    """NaN / 無限大換成 None：瀏覽器的 JSON 不接受 NaN，一個 NaN 就會讓整頁打不開。"""
    if isinstance(x, float) and not math.isfinite(x):
        return None
    if isinstance(x, dict):
        return {k: finite(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [finite(v) for v in x]
    return x


def run(state_dir: Path, out_dir: Path, config_path: Path = CONFIG, now: datetime | None = None,
        monitor_kwargs: dict | None = None, log=print, fetch_names: bool = True) -> dict:
    now = now or datetime.now(TPE).replace(tzinfo=None)
    store = Store(state_dir)
    config_errors = []
    try:
        cfg, warnings = load_config(config_path)
        store.save("last_good_config.json", cfg)
    except ConfigError as e:
        warnings = []
        last = store.load("last_good_config.json", None)
        if last:
            config_errors.append(f"{e}；先沿用上一次正確的設定")
            cfg = last
        else:
            config_errors.append(f"{e}；還沒有上一次正確的設定可以沿用，這次先只更新報價、不自動交易")
            cfg = {"watchlist": list(store.watchlist()), "limits": {},
                   "settings": {"auto_trade": False}, "strategy_params": {}}
    config_errors += warnings

    settings = {**DEFAULT_SETTINGS, "auto_trade": True, **cfg["settings"],
                "strategy_params": cfg["strategy_params"],
                "source": "yahoo", "realtime": True, "demo": False}
    store.save_settings(settings)
    store.save_watchlist({c: cfg["limits"].get(c, {"lower": None, "upper": None})
                          for c in cfg["watchlist"]})

    m = Monitor(store, is_active=is_active, session_date=session_date, **(monitor_kwargs or {}))
    held_only = [c for c in m.account.positions if c not in m.watchlist]
    for c in held_only:   # 從自選股刪掉但還有持股：繼續追蹤，停損和賣出訊號才會照常運作
        m.watchlist[c] = {"lower": None, "upper": None}
    if fetch_names:
        prefetch_names(m, now, log)
    res = m.refresh(now)

    # 這次抓不到報價的持股用上一次的價格計算（不然會被當成成本價，資產走勢出現假的跳動）
    stamp = f"{now:%Y-%m-%d %H:%M}"
    known = store.load("prices.json", {})
    known.update({c: {"price": p, "time": stamp} for c, p in m.last_prices.items()})
    known = {c: v for c, v in known.items() if c in m.watchlist}
    store.save("prices.json", known)
    prices = {c: v["price"] for c, v in known.items()}
    missing = [c for c in m.account.positions if c not in m.last_prices]
    total = m.account.total_assets(prices)
    updated_utc = now.replace(tzinfo=TPE).astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
    equity = store.load("equity.json", [])
    if missing:
        res.notes.append(f"持股 {'、'.join(missing)} 這次抓不到報價，先用上一次抓到的價格計算，"
                         f"資產走勢這次不記錄")
    else:
        equity.append([updated_utc, round(total, 2)])
        del equity[:-EQUITY_MAX]
        store.save("equity.json", equity)

    notify_cfg = {**NOTIFY_DEFAULT, **cfg.get("notify", {})}
    notify_status = push_notifications(store, m, res, notify_cfg, now, log)

    data = page_data(m, res, now, updated_utc, total, equity, config_errors, held_only,
                     prices, {c: v["time"] for c, v in known.items()})
    data["settings"]["notify"] = {**notify_cfg, "status": notify_status}
    data = finite(data)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "data.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":"),
                                              allow_nan=False), encoding="utf-8")
    shutil.copyfile(PAGE, out / "index.html")
    log(f"雲端看盤：{len(res.snapshots)}/{len(m.watchlist)} 檔有報價，新提醒 {len(res.alerts)} 則，"
        f"自動成交 {len(res.trades)} 筆，總資產 {total:,.0f}")
    for c, e in res.errors.items():
        log(f"  ⚠ {c}：{e}")
    for e in config_errors:
        log(f"  ⚠ 設定：{e}")
    for n in res.notes:
        log(f"  ・{n}")
    return data


def push_notifications(store, m, res, cfg: dict, now: datetime, log=print) -> str:
    """把這次新的成交和提醒合併成一則手機通知。回傳顯示在網頁上的狀態。"""
    topic = (os.environ.get("NTFY_TOPIC") or "").strip()
    if not cfg["enabled"]:
        return "關閉"
    if not topic:
        return "還沒設定（GitHub 的 NTFY_TOPIC）"
    status = "已開啟"
    # 第一次看到這個頻道：送一則測試通知，確認手機收得到（只存雜湊，不存頻道名稱）
    fp = notify.topic_fingerprint(topic)
    state = store.load("notify.json", {})
    try:
        if state.get("fingerprint") != fp:
            notify.send({"title": "雲端看盤：手機推播設定成功", "tags": ["white_check_mark"],
                         "message": "之後有自動成交、買賣訊號、突破上下限或停損時會通知你。",
                         "click": notify.MONITOR_URL})
            store.save("notify.json", {"fingerprint": fp})
            log("  已送出推播測試通知")
        trades = res.trades if cfg["trades"] else []
        alerts = notify.pick_alerts(res.alerts, m.settings["strategies"][0], cfg)
        payload = notify.build_message(trades, alerts, m.names, f"{now:%m/%d %H:%M}")
        if payload:
            notify.send(payload)
            log(f"  已推播：{payload['title']}")
    except Exception as e:  # noqa: BLE001 - 推播失敗不影響看盤
        log(f"  ⚠ 推播失敗：{e}")
        status = f"已開啟，但這次推播失敗（{e}）"
    return status


def page_data(m, res, now, updated_utc, total, equity, config_errors, held_only,
              prices: dict, price_time: dict) -> dict:
    a = m.account
    positions = []
    for code, p in a.positions.items():
        px = prices.get(code, p["avg_cost"])
        positions.append({"code": code, "name": m.names.get(code, ""), "shares": p["shares"],
                          "avg_cost": round(p["avg_cost"], 4), "price": px,
                          "pnl": round((px - p["avg_cost"]) * p["shares"], 2),
                          "pnl_pct": round((px / p["avg_cost"] - 1) * 100, 2),
                          # 這次抓不到報價時，價格是哪個時間抓到的
                          "price_time": None if code in m.last_prices else price_time.get(code)})
    watch = []
    for code, limits in m.watchlist.items():
        row = {"code": code, "name": m.names.get(code, ""), "exchange": exchange_of(code),
               "limits": limits, "held": code in a.positions, "held_only": code in held_only}
        snap = res.snapshots.get(code)
        if snap is None:
            row["error"] = res.errors.get(code, "查不到資料")
        else:
            trend = None
            if math.isfinite(snap.short_ma) and math.isfinite(snap.long_ma):
                trend = "多" if snap.short_ma > snap.long_ma else "空"
            row.update(price=snap.price, change_pct=round(snap.change_pct, 2),
                       short_ma=round(snap.short_ma, 4), long_ma=round(snap.long_ma, 4),
                       rsi=round(snap.rsi, 1), trend=trend, bar_date=snap.date,
                       stale=code in res.stale,
                       live=code in res.live, tw=uses_twse(parse_symbol(code)),
                       signals=[{"strategy": k, "name": strat.get(k).name, "dir": v,
                                 "reason": snap.reasons[k][0 if v == 1 else 1]}
                                for k, v in snap.signals.items() if v])
        watch.append(row)
    s = m.settings
    return {
        "updated": f"{now:%Y-%m-%d %H:%M:%S}", "updated_utc": updated_utc,
        "edit_url": EDIT_URL, "config_errors": config_errors, "notes": res.notes,
        "settings": {"strategies": [{"key": k, "name": strat.get(k).name} for k in s["strategies"]],
                     "auto_trade": s["auto_trade"], "auto_buy_amount": s["auto_buy_amount"],
                     "stop_loss_pct": s["stop_loss_pct"], "short_ma": s["short_ma"],
                     "long_ma": s["long_ma"]},
        "account": {"initial": a.initial, "cash": round(a.cash, 2),
                    "market_value": round(a.market_value(prices), 2), "total": round(total, 2),
                    "return_pct": round(a.return_pct(prices), 3)},
        "positions": positions, "watch": watch,
        "alerts": list(reversed(m.alert_state.get("history", [])[-50:])),
        "new_alerts": len(res.alerts),
        "trades": list(reversed(a.trades[-50:])),
        "equity": equity[-2000:],
        "exchanges": {k: {"name": e.name, "tz": e.tz, "always": e.always,
                          "sessions": [[f"{x:%H:%M}", f"{y:%H:%M}"] for x, y in e.sessions]}
                      for k, e in EXCHANGES.items()},
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="雲端看盤（給 GitHub Actions 執行）")
    ap.add_argument("--state", required=True, help="帳戶等資料存放的資料夾")
    ap.add_argument("--out", required=True, help="網頁輸出資料夾")
    ap.add_argument("--config", default=str(CONFIG))
    args = ap.parse_args(argv)
    run(Path(args.state), Path(args.out), Path(args.config))


if __name__ == "__main__":
    main()
