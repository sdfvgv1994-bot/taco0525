# 自動看盤系統＋模擬下單

```bash
python stock_monitor/monitor.py          # 真實資料（Yahoo Finance）
python stock_monitor/monitor.py --demo   # 示範資料，不連網
```

## 功能
- **自選股監控**：台股只打數字（自動判斷上市 / 上櫃）、美股、指數（加權、SPX…）、加密貨幣（BTC…）
- **技術指標**：短 / 長均線（預設 MA5 / MA20）、RSI
- **提醒**（同一個提醒一天只響一次，會發出嗶聲）
  - 🟢 黃金交叉、🔴 死亡交叉
  - ⬆️ 突破上限、⬇️ 跌破下限（每檔可自訂）
  - 🛑 停損：持股虧損超過設定 %（預設 8%）
- **模擬下單**：虛擬帳戶起始 100 萬
  - 手動買賣；也可開啟自動交易：黃金交叉時按設定金額買進，死亡交叉或停損時全部賣出
  - 手續費 0.1425%（最低 20 元），賣出台股收證交稅 0.3%
  - 現金不足、賣超都會被拒絕；有持股的股票不能從自選股刪除
- **存檔**：`stock_monitor/data/` 底下的 `watchlist.json`、`settings.json`、`account.json`、`alerts.json`，以及 `monitor.log`
