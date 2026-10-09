# taco0525

個人小專案合集。

| 資料夾 | 內容 | 執行 |
| --- | --- | --- |
| [`stock_monitor/`](stock_monitor/) | 自動看盤系統＋模擬下單 | `python stock_monitor/monitor.py` |
| [`stock_backtest/`](stock_backtest/) | 策略回測：6 種策略、可一次比較（含固定 / 移動停損） | `python stock_backtest/backtest.py 2330 --compare` |
| [`snake/`](snake/) | 貪食蛇 | `python snake/snake.py` |
| [`kaleidoscope/`](kaleidoscope/) | 萬花筒畫板 | `python kaleidoscope/kaleidoscope.py` |

`common/` 是兩個股票工具共用的模組：代號辨識、資料下載、交易成本、技術指標、交易策略。

## 安裝

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS / Linux: source .venv/bin/activate
pip install -r requirements.txt
```

## 沒有網路時
兩個股票工具都可以加 `--demo` 參數，改用離線產生的示範資料（每個代號的走勢固定、可重現），方便測試功能。

## 測試

```bash
python -m pytest tests
```
