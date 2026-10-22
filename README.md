# taco0525：自己的股市工具箱

看盤、模擬交易、回測、全球與台股行情，一個人就能用的股市工具。
有網頁版（手機打開就能看，電腦關機也照常運作），也有電腦上的桌面版。

> ⚠️ 這裡的交易都是**模擬帳戶**（虛擬的 100 萬），不會動到真的錢；程式給的訊號和資料只是參考，**不是投資建議**。

## 🌐 打開就能用的網頁

| 網頁 | 能做什麼 |
| --- | --- |
| [📈 雲端看盤](https://sdfvgv1994-bot.github.io/taco0525/monitor/) | 自選股報價、買賣訊號、模擬帳戶（含配息配股）、手機推播；手機上就能加減自選股、手動下單，也能裝成手機 App |
| [🇹🇼 台股總覽](https://sdfvgv1994-bot.github.io/taco0525/tw/) | 全部上市櫃股票與 ETF：行情、排行、產業、三大法人、融資融券；個股頁有日／週／月 K、KD、MACD、近 6 年配息配股 |
| [🌏 全球看板](https://sdfvgv1994-bot.github.io/taco0525/) | 各國股市指數、匯率、商品、加密貨幣，現在哪些市場開盤 |

三個網頁都由 GitHub 的雲端機器**每 15 分鐘自動更新**（實際間隔常會晚一點），不用開電腦。

## 🧰 每個工具

### 雲端看盤 [`cloud_monitor/`](cloud_monitor/)
- 每次更新：抓自選股報價 → 判斷策略訊號 → 照設定自動模擬交易 → 做成網頁
- **模擬帳戶像真的證券戶**：手續費、證交稅；除權息日照持股拿股利（現金股利約一個月後入帳、股票股利加股數、
  單次滿 2 萬扣 2.11% 二代健保、匯費 10 元、美股預扣 30%）
- **手機推播**（ntfy）：成交、買賣訊號、突破上下限、停損、除息與股利入帳
- **在手機上改設定**：加減自選股、設上下限與支撐線、手動下單
- 詳細說明：[cloud_monitor/README](cloud_monitor/README.md)

### 自動看盤系統 [`stock_monitor/`](stock_monitor/)
電腦上的看盤：桌面視窗、手機網頁（同一個 Wi-Fi 用手機看電腦）、文字版三種畫面共用一個引擎，
預設和雲端看盤用**同一個模擬帳戶**。每分鐘看報價、提醒，也能畫趨勢線、水平線。
雙擊 `stock_monitor\啟動桌面版.bat` 或 `啟動手機版.bat`。→ [說明](stock_monitor/README.md)

### 策略回測 [`stock_backtest/`](stock_backtest/)
8 種策略（均線、MACD、RSI、布林、SMC、訂單流…）拿歷史資料試跑，可以一次比較、加固定或移動停損。
`python stock_backtest/backtest.py 2330 --compare` → [說明](stock_backtest/README.md)

### 台股總覽 [`tw_market/`](tw_market/)、全球看板 [`world_market/`](world_market/)
網頁的產生程式，平常由雲端自動執行；也可以在自己電腦上跑。→ [台股總覽說明](tw_market/README.md)、[全球看板說明](world_market/README.md)

小遊戲（貪食蛇、萬花筒、俄羅斯方塊、網頁版方塊對戰）在另一個專案 [taco-game](https://github.com/sdfvgv1994-bot/taco-game)。

## 🚀 第一次在電腦上用

1. 安裝 [Python 3.12](https://www.python.org/downloads/) 和 [Git](https://git-scm.com/download/win)
2. 下載這個專案：`git clone https://github.com/sdfvgv1994-bot/taco0525.git`
3. 安裝需要的套件（只要做一次，指令在下面「安裝」）
4. 雙擊 `stock_monitor\啟動桌面版.bat`

之後每次啟動都會**自動更新到最新版**（見下面「自動更新」）。

## 🧱 程式架構（積木）

每個工具都拆成一塊一塊的小檔案（「積木」），一塊只做一件事，改一塊不會動到別塊：

| 積木 | 做什麼 |
| --- | --- |
| `common/` | 大家共用：代號辨識、資料下載（證交所、Yahoo）、證交所回應解析、交易成本、技術指標、交易策略、自動更新 |
| `cloud_monitor/` | `config`（設定檔檢查）、`exchange`（交易所判斷）、`dividends`（配息配股）、`notify`（手機推播）、`run`（主流程） |
| `stock_monitor/` | `engine`（看盤引擎）、`account`（模擬帳戶）、`live_*`（通知、雲端帳戶、畫面操作）、`dialogs`（設定視窗）、`web_api`（手機版網址） |
| `tw_market/` | `sources`（資料來源）、`update`（排程抓資料）、`exright`（除權除息）、`store`（快取）、`build`（產生網頁） |

修改前後都會跑完整測試（目前約 490 個），結果一樣才合併。

## 安裝

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS / Linux: source .venv/bin/activate
pip install -r requirements.txt
```

## 自動更新
每個程式啟動時都會先檢查 GitHub 上有沒有新版，有的話自動更新、套件有變就自動安裝，再用新版重新啟動。
- 只有 GitHub 上有新版時才會更新；本機改過的程式碼會被新版覆蓋，但會先備份到 `.cache/backup/`
  的 .patch 檔和 git 的 `refs/backup/…`（沒推上去的提交則留在 `backup/…` 分支）
- 新版需要的套件裝不起來時，會先退回原本的版本，下次啟動再試
- 模擬帳戶、自選股、提醒紀錄、快取等資料不在 git 裡，不會被動到
- 沒網路時照常用目前版本；不想更新可以加 `--no-update`，或設環境變數 `TACO_NO_UPDATE=1`

## 資料來源
- **台股（上市、上櫃）和加權指數**：預設從**台灣證券交易所 / 櫃買中心**的公開 API 抓
  - 歷史日 K 存在 `.cache/twse/`，過去的月份不會重抓；當月資料 15 分鐘更新一次
  - 證交所限制連線頻率（大約 5 秒 3 次，超過會被封鎖 20 分鐘以上），所以每次連線間隔 3 秒。
    第一次抓 2 年資料約需 75 秒，之後幾乎是瞬間
  - 這個間隔是**所有程式共用**的：同時開回測、看盤、看板，或連續執行好幾次，也不會疊加成連發
  - 萬一還是被封鎖，程式會記下來，5 分鐘內不再連證交所（自動改用 Yahoo），避免越試封越久；
    即時報價被擋通常很快恢復，只暫停 1 分鐘
  - 即時報價或股名有問題時，可以執行診斷（只會送出幾個請求），把輸出貼給 Claude：
    ```bash
    python -m common.twse 2330 6488
    ```
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
