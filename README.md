# taco0525

個人小專案合集。

| 資料夾 | 內容 | 執行 |
| --- | --- | --- |
| [`stock_monitor/`](stock_monitor/) | 自動看盤系統＋模擬下單 | `python stock_monitor/monitor.py` |
| [`stock_backtest/`](stock_backtest/) | 策略回測：6 種策略、可一次比較（含固定 / 移動停損） | `python stock_backtest/backtest.py 2330 --compare` |
| [`world_market/`](world_market/) | 全球股市看板（網頁） | `python world_market/app.py` |
| [`snake/`](snake/) | 貪食蛇 | `python snake/snake.py` |
| [`kaleidoscope/`](kaleidoscope/) | 萬花筒畫板 | `python kaleidoscope/kaleidoscope.py` |

`common/` 是兩個股票工具共用的模組：代號辨識、資料下載、交易成本、技術指標、交易策略。

## 安裝

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS / Linux: source .venv/bin/activate
pip install -r requirements.txt
```

## 資料來源
- **台股（上市、上櫃）和加權指數**：預設從**台灣證券交易所 / 櫃買中心**的公開 API 抓
  - 歷史日 K 存在 `.cache/twse/`，過去的月份不會重抓；當月資料 15 分鐘更新一次
  - 證交所限制連線頻率（大約 5 秒 3 次，超過會被封鎖 20 分鐘以上），所以每次連線間隔 3 秒。
    第一次抓 2 年資料約需 75 秒，之後幾乎是瞬間
  - 這個間隔是**所有程式共用**的：同時開回測、看盤、看板，或連續執行好幾次，也不會疊加成連發
  - 萬一還是被封鎖，程式會記下來，5 分鐘內不再連證交所（自動改用 Yahoo），避免越試封越久
  - 看盤系統的台股即時價格用證交所即時報價（mis.twse.com.tw），自選股一次查完，也會顯示中文股名
  - 證交所抓不到時會自動改用 Yahoo Finance，並顯示提示
- **美股、國際指數、加密貨幣**：Yahoo Finance
- 想指定來源可以加 `--source twse`（只用證交所）或 `--source yahoo`

## 沒有網路時
兩個股票工具都可以加 `--demo` 參數，改用離線產生的示範資料（每個代號的走勢固定、可重現），方便測試功能。

## 測試

```bash
python -m pytest tests
```
