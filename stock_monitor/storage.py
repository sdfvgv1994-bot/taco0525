"""JSON 存檔與日誌。所有檔案都放在 data 資料夾。"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

DEFAULT_SETTINGS = {
    "strategies": ["ma"],       # 要判斷的策略；第一個是自動交易用的主策略
    "strategy_params": {},      # 例 {"rsi": {"low": 25}}，沒寫的用預設值
    "short_ma": 5,              # 均線策略與報價表的短 / 長均線
    "long_ma": 20,
    "interval": 60,             # 自動更新間隔（秒）
    "stop_loss_pct": 8.0,       # 持股虧損超過幾 % 觸發停損
    "auto_trade": False,        # 是否自動模擬交易
    "auto_buy_amount": 100000,  # 主策略出現買進訊號時，每次買進的金額
    "demo": False,              # 示範資料（不連網）
}
INITIAL_CASH = 1_000_000


class Store:
    def __init__(self, data_dir: str | Path):
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        return self.dir / name

    def load(self, name: str, default):
        p = self._path(name)
        if not p.exists():
            return default
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # 檔案壞掉就備份起來，從預設值重來
            p.rename(p.with_suffix(p.suffix + ".broken"))
            return default

    def save(self, name: str, data) -> None:
        p = self._path(name)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)  # 先寫暫存再取代，避免寫到一半當掉毀檔

    def log(self, message: str, when: datetime | None = None) -> str:
        when = when or datetime.now()
        line = f"[{when:%Y-%m-%d %H:%M:%S}] {message}"
        with self._path("monitor.log").open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        return line

    # 常用的幾個檔案
    def settings(self) -> dict:
        return {**DEFAULT_SETTINGS, **self.load("settings.json", {})}

    def save_settings(self, s: dict) -> None:
        self.save("settings.json", s)

    def watchlist(self) -> dict:
        return self.load("watchlist.json", {})

    def save_watchlist(self, w: dict) -> None:
        self.save("watchlist.json", w)
