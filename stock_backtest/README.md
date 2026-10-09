# 策略回測工具

```bash
python stock_backtest/backtest.py 2330                          # 均線交叉 MA5/MA20、近 2 年
python stock_backtest/backtest.py 2330 -s macd                  # 換成 MACD
python stock_backtest/backtest.py 2330 -s rsi -p low=25 -p high=75
python stock_backtest/backtest.py 2330 --compare                # 6 種策略一起比，排名次
python stock_backtest/backtest.py --list                        # 列出所有策略與預設參數
python stock_backtest/backtest.py AAPL -s kd --stop-loss 8 --trailing 12
python stock_backtest/backtest.py                               # 互動模式，一題一題問
python stock_backtest/backtest.py 2330 --demo                   # 示範資料，不連網
```

## 策略（`-s 代號`，`-p 名稱=值` 調參數）

| 代號 | 策略 | 買進 | 賣出 | 預設參數 |
| --- | --- | --- | --- | --- |
| `ma` | 均線交叉 | 短均線往上穿過長均線 | 往下穿過 | short=5 long=20（也可用 `--short` `--long`） |
| `macd` | MACD | DIF 往上穿過訊號線 | 往下穿過 | fast=12 slow=26 signal=9 |
| `rsi` | RSI 超買超賣 | RSI 從 low 以下回升 | RSI 衝上 high | n=14 low=30 high=70 |
| `kd` | KD 指標 | D < low 時 K 往上穿過 D | D > high 時 K 往下穿過 D | n=9 low=30 high=70 |
| `bbands` | 布林通道 | 收盤跌破下軌後收回 | 收盤突破上軌 | n=20 k=2 |
| `breakout` | 突破新高（海龜） | 收盤創 entry 日新高 | 收盤跌破 exit 日新低 | entry=20 exit=10 |

- 趨勢型：`ma`、`macd`、`breakout`；抄底 / 反轉型：`rsi`、`kd`、`bbands`。
  反轉型策略遇到長期下跌容易被套，建議搭配停損。
- KD 用台灣常見算法（RSV 9 日、1/3 平滑）。低高檔預設 30/70，想更嚴格可用 `-p low=20 -p high=80`。

## 共同規則
- 出現買進訊號時全部資金買進，出現賣出訊號時全部賣出（以收盤價成交）
- **固定停損** `--stop-loss X`：收盤價比買價跌超過 X% 就賣
- **移動停損** `--trailing X`：收盤價從買進後的最高點回落超過 X% 就賣
- 停損出場後要等下一次買進訊號才會再買
- 成本：手續費 0.1425%（買賣都收），賣出台股收證交稅 0.3%
- 會和「第一天全部買進、一路持有」比較總報酬、年化報酬、最大回撤、持股時間

## 輸出
- 終端機：績效對照表＋每筆交易明細（`--compare` 則是所有策略的排名表）
- 圖表存在 `stock_backtest/output/`
  - 單一策略：價格與指標線、買賣點（綠▲ 買進、紅▼ 訊號賣出、**紫▼ 固定停損**、**青▼ 移動停損**）、
    MACD / RSI / KD 會另外畫在副圖，最下面是資產曲線
  - `--compare`：所有策略的資產曲線疊在一起，和買進持有比較
