"""雲端看盤的設定檔（config.toml）：讀取、修正手機常見的打字錯誤、檢查每個設定。

從 cloud_monitor/run.py 拆出來的積木；run.py 仍然可以用 run.load_config、run.ConfigError。
只依賴 common/、stock_monitor/ 的策略與預設值，不連網、不碰帳戶資料，可以單獨測試。
"""

from __future__ import annotations

import difflib
import re
import tomllib
from pathlib import Path

import numpy as np
import pandas as pd

from common import strategies as strat
from cloud_monitor.exchange import exchange_of
from common.market import parse_symbol
from stock_monitor.signals import strategy_params as effective_params
from stock_monitor.storage import DEFAULT_SETTINGS

HISTORY_BARS = 240   # 雲端抓 1 年的日 K（約 240 個交易日），均線和策略的天數不能超過
NOTIFY_DEFAULT = {"enabled": True, "trades": True, "signals": "primary", "price_alerts": True}
NUMBER_SETTINGS = {"auto_buy_amount": (1, 1_000_000_000), "stop_loss_pct": (0, 100),
                   "trail_stop_pct": (0, 100),
                   "short_ma": (1, HISTORY_BARS - 1), "long_ma": (2, HISTORY_BARS)}
# 各段落可以寫的設定（"" 是檔案最上面、還沒有任何 [段落] 標題的地方）
SECTION_KEYS = {
    "": ["watchlist", "settings", "limits", "strategy_params", "notify"],
    "settings": ["strategies", "auto_trade", *NUMBER_SETTINGS],
    "notify": ["enabled", "trades", "signals", "price_alerts"],
}
LIMIT_KEYS = ["lower", "upper", "support"]   # [limits] 每檔可以寫的提醒
WINDOW_PARAMS = {"short", "long", "n", "fast", "slow", "signal", "entry", "exit", "swing"}
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
    return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": 1000.0})


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
            if k not in LIMIT_KEYS:
                close = difflib.get_close_matches(str(k).lower(), LIMIT_KEYS, n=1, cutoff=0.5)
                warnings.append(f"「{code}」的提醒只能寫 {'、'.join(LIMIT_KEYS)}，看不懂「{k}」" +
                                (f"，是不是要寫「{close[0]}」？" if close else ""))
        lo, hi = v.get("lower"), v.get("upper")
        bad = [x for x in (lo, hi) if x is not None and (isinstance(x, bool) or not isinstance(x, (int, float)))]
        if bad or (lo is not None and hi is not None and lo >= hi):
            warnings.append(f"「{code}」的上下限要是數字，而且下限要小於上限")
            continue
        sup = v.get("support", [])
        if not isinstance(sup, list):
            sup = [sup]
        if any(isinstance(x, bool) or not isinstance(x, (int, float)) or x <= 0 for x in sup):
            warnings.append(f"「{code}」的支撐線要寫成數字清單，例：support = [1900, 1850]，先不使用")
            sup = []
        if lo is None and hi is None and not sup:
            continue
        if c not in watchlist:
            what = "上下限" if lo is not None or hi is not None else "支撐線"
            warnings.append(f"「{code}」有設{what}，但不在自選股裡，所以不會提醒")
        limits[c] = {"lower": lo, "upper": hi}
        if sup:
            limits[c]["support"] = sorted({float(x) for x in sup}, reverse=True)

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
