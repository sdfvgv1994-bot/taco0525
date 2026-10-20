# 小遊戲

和股票程式（`stock_monitor/`、`tw_market/` 等）分開放的小遊戲，需要 `pygame-ce`（`pip install -r requirements.txt` 已經包含）。

| 資料夾 | 內容 | 執行 |
| --- | --- | --- |
| [`snake/`](snake/) | 貪食蛇 | `python games/snake/snake.py` |
| [`kaleidoscope/`](kaleidoscope/) | 萬花筒畫板 | `python games/kaleidoscope/kaleidoscope.py` |

以前放在專案最外層的 `snake/`、`kaleidoscope/`。自動更新到這一版後，第一次打開遊戲會把
最高分（`highscore.json`）和存好的畫（`drawings/`）搬到新的資料夾。
