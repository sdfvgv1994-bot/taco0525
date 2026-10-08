# 均線交叉回測工具

```bash
python stock_backtest/backtest.py 2330                          # MA5/MA20、近 2 年
python stock_backtest/backtest.py 2330 --short 10 --long 60 --period 5y
python stock_backtest/backtest.py AAPL --stop-loss 8 --trailing 12
python stock_backtest/backtest.py                               # 互動模式，一題一題問
python stock_backtest/backtest.py 2330 --demo                   # 示範資料，不連網
```

## 規則
- 黃金交叉時全部資金買進，死亡交叉時全部賣出（以收盤價成交）
- **固定停損** `--stop-loss X`：收盤價比買價跌超過 X% 就賣
- **移動停損** `--trailing X`：收盤價從買進後的最高點回落超過 X% 就賣
- 兩種停損可以同時開；停損出場後要等下一次黃金交叉才會再買
- 成本：手續費 0.1425%（買賣都收），賣出台股收證交稅 0.3%
- 會和「第一天全部買進、一路持有」比較總報酬、年化報酬、最大回撤

## 輸出
- 終端機：績效對照表＋每筆交易明細
- 圖表：`stock_backtest/output/代號_MA短_長.png`
  - 綠▲ 買進、紅▼ 死亡交叉賣出、**紫▼ 固定停損**、**青▼ 移動停損**、灰▼ 期末未平倉
  - 下半部是策略 vs 買進持有的資產曲線
