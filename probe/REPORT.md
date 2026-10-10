# TW endpoint probe report

Probe run 2026-10-10 (Sat). Last trading day 2026-10-08; 2026-10-09 holiday. Total HTTP requests: 16. Blocked hosts: `{"openapi.twse.com.tw": "A1: HTTP 200 html", "www.twse.com.tw": "B3: HTTP 307 html"}`

Raw bodies: `probe/raw/<ID>.<kind>.gz`. Script: `probe/tw_probe.py`.

## Summary / findings (read first)

**Succeeded (14):** B1, B2, C1–C5, D1–D7. **Failed (10):** A1 (blocked page), A2–A4 (skipped: host blocked), B3 (307 + blocked page), B4–B8 (skipped: host blocked).

**TWSE blocking**
- `openapi.twse.com.tw` answered the very first request (A1) with HTTP **200** `text/html` "因為安全性考量…FOR SECURITY REASONS, THIS PAGE CAN NOT BE ACCESSED" (800 bytes). Status 200, so detect the block by content-type/body, not status code.
- `www.twse.com.tw` served B1 and B2 fine, then B3 (T86) got HTTP **307** with the same HTML body and **no Location header**. Requests were ≥6.5 s apart, so the trigger may be the IP/egress range rather than rate. No further TWSE requests were made, so B4–B8 (including the B7 holiday "no data" shape) are **not captured**; that needs another machine/IP.

**TWSE rwd MI_INDEX (B1/B2)**
- Top level: `stat` "OK" (upper-case), `date` "YYYYMMDD", `params`, `type`. No top-level `title`; titles sit on each table and use ROC dates ("115年10月08日 …").
- `tables[8]` is the per-security quote table (1380 rows on 10/08: 1094 four-digit codes, 240 codes starting "00" (ETFs), plus others like 00625K, 2-letter suffixes, TDRs, preferred shares). `tables[9]` is `{}`. Find the table by its title (contains "每日收盤行情"), not by index.
- Up/down sign is **HTML** in column 9 "漲跌(+/-)": `<p style= color:red>+</p>`, `<p style= color:green>-</p>`, `<p> </p>` (flat), `<p>X</p>` (no comparison price, e.g. ex-rights). Column 10 漲跌價差 is unsigned. Index tables use `<p style ='color:green'>-</p>` (different spacing/quotes), so strip tags and don't match exact strings.
- Numbers use thousands commas ("23,145,193"). Untraded rows have `"--"` for open/high/low/close (16 rows on 10/08). Units per `hints`: 單位：元、股 (shares, not 千股, in this table), but `notes` say 交易單位皆為千股 for the trading unit.
- Old dates work: B2 (2026-06-01) returned the same structure (1362 rows), so backfill by date is viable when not blocked.
- 2330 on 10/08: close 2,550.00, -35.00, vol 23,145,193 shares, PE 29.56.

**TPEx openapi (C1–C5)**: all JSON lists, latest day only (no date parameter). `Date` is ROC "1151008" (C5's is "1151010", the fetch date).
- C1 includes warrants: 12221 rows, only 886 four-digit codes. Change looks like "-85.00 " (trailing space), "+12.00", "0.00 ", or "--- " when untraded; Close is " ---" when untraded.
- C3 key names are messy: leading space in `" Foreign Investors … -Total Sell"`, `"Dealers -TotalSell"`, `"ForeignInvestorsInclude MainlandAreaInvestors-Difference"`. It has only 20 keys while D3 has 24 data columns, so the dealer 自營/避險 split is partly lost. Prefer D3.
- C2: 885 rows, all four-digit codes. C4: 919 rows (includes 117 ETFs). C5: 893 company profiles.

**TPEx www by date (D1–D7)**: all worked first try, so no discovery requests were needed.
- Shape: `{"date":"YYYYMMDD","tables":[{"title","date":"115/10/08","totalCount","fields","data",...}],"stat":"ok"}`. `stat` is lower-case "ok", and table dates are ROC.
- D1 (dailyQuotes) contains the same data as C1 (12221 rows including warrants). Volume column 成交股數 is in shares; 最後買量/賣量 are in 張 (`flagField` "張數"). tables[1] '管理股票' is empty.
- D2 (2026-06-01) works, so backfill is possible.
- D3 dailyTrade: 24 columns per row: code, name, 外資(不含自營) buy/sell/net, 外資自營 b/s/n, 外資合計 b/s/n, 投信 b/s/n, 自營(自行) b/s/n, 自營(避險) b/s/n, 自營合計 b/s/n, 三大法人合計. Field names repeat ("買進股數" ×7), so parse by position. Units are shares.
- D4 margin: units are 張. `summary` holds totals rows (合計(張), 融資金(仟元)).
- D5 peQryDate: PE is "N/A" for 212 rows; names are space-padded ("環球晶          "); 股利年度 is an int (114); 財報年/季 looks like "115Q2".
- D6 inx is a **month query**: date=2026/10/08 returned the whole month to date (6 rows, 2026/10/01–10/08; dates are Gregorian "2026/10/01" here). For a month or more of history, fetch one request per month.
- D7 (old stk_quote_result.php) returned byte-identical JSON to D1 (it is now an alias of the new API).

## A1

- URL: `https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL`
- HTTP 200; content-type `text/html; charset=UTF-8`; size 800 bytes; body kind: html
- body head: `'<html>\n<head>\n<meta http-equiv="Content-Type" content="text/html; charset=utf-8">\n</head>\n<body> \n因為安全性考量，您所執行的頁面無法呈現。<BR>\nFOR SECURITY REASONS, THIS PAGE CAN NOT BE ACCESSED.<BR>\n<BR>\n您可以<A HREF="javascript:history.back()">回上一頁</A>繼續其他動作，或撥打以下電話通報問題：(02)8101-5840, (02)8101-5834。<BR>\nYOU COULD GO <A HREF="javascript:history.back()">BACK</A> TO PREVIOUS PAGE OR CALL US FOR FURTHER HELP：(02)8101-584'`

## A2

- URL: `https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL`
- **SKIPPED**: host blocked: A1: HTTP 200 html

## A3

- URL: `https://openapi.twse.com.tw/v1/opendata/t187ap03_L`
- **SKIPPED**: host blocked: A1: HTTP 200 html

## A4

- URL: `https://openapi.twse.com.tw/v1/exchangeReport/MI_INDEX`
- **SKIPPED**: host blocked: A1: HTTP 200 html

## B1

- URL: `https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date=20261008&type=ALLBUT0999&response=json`
- HTTP 200; content-type `application/json;charset=UTF-8`; size 247290 bytes; body kind: json
- top-level keys: `['tables', 'type', 'params', 'stat', 'date']`
- stat: `"OK"`
- date: `"20261008"`

### B1 tables[0] — '115年10月08日 價格指數(臺灣證券交易所)'
- table keys: `['title', 'fields', 'data', 'hints']`
- rows: 56
- fields: `["指數", "收盤指數", "漲跌(+/-)", "漲跌點數", "漲跌百分比(%)", "特殊處理註記"]`
- 56 rows; 4-digit numeric codes: 0; codes starting '00' (ETF-ish): 0; other (warrants/ETNs/etc, non-4-digit): 56
- row[0]: `["寶島股價指數", "54,683.46", "<p style ='color:green'>-</p>", "542.01", "-0.98", ""]`
- row[1]: `["發行量加權股價指數", "49,313.44", "<p style ='color:green'>-</p>", "492.93", "-0.99", ""]`
- row for 2330: not present

### B1 tables[1] — '價格指數(跨市場)'
- table keys: `['title', 'fields', 'data']`
- rows: 46
- fields: `["指數", "收盤指數", "漲跌(+/-)", "漲跌點數", "漲跌百分比(%)", "特殊處理註記"]`
- 46 rows; 4-digit numeric codes: 0; codes starting '00' (ETF-ish): 0; other (warrants/ETNs/etc, non-4-digit): 46
- row[0]: `["臺灣生技指數", "4,154.07", "<p style ='color:red'>+</p>", "10.49", "0.25", ""]`
- row[1]: `["臺灣中小型公司治理指數", "19,483.50", "<p style ='color:green'>-</p>", "36.71", "-0.19", ""]`
- row for 2330: not present

### B1 tables[2] — '價格指數(臺灣指數公司)'
- table keys: `['title', 'fields', 'data']`
- rows: 35
- fields: `["指數", "收盤指數", "漲跌(+/-)", "漲跌點數", "漲跌百分比(%)", "特殊處理註記"]`
- 35 rows; 4-digit numeric codes: 0; codes starting '00' (ETF-ish): 0; other (warrants/ETNs/etc, non-4-digit): 35
- row[0]: `["金融類日報酬兩倍指數", "124,506.69", "<p style ='color:green'>-</p>", "4,341.46", "-3.37", ""]`
- row[1]: `["金融類日報酬反向一倍指數", "1,949.64", "<p style ='color:red'>+</p>", "32.30", "1.68", ""]`
- row for 2330: not present

### B1 tables[3] — '報酬指數(臺灣證券交易所)'
- table keys: `['title', 'fields', 'data']`
- rows: 47
- fields: `["報酬指數", "收盤指數", "漲跌(+/-)", "漲跌點數", "漲跌百分比(%)", "特殊處理註記"]`
- 47 rows; 4-digit numeric codes: 0; codes starting '00' (ETF-ish): 0; other (warrants/ETNs/etc, non-4-digit): 47
- row[0]: `["寶島股價報酬指數", "85,073.41", "<p style ='color:green'>-</p>", "843.22", "-0.98", ""]`
- row[1]: `["發行量加權股價報酬指數", "114,031.08", "<p style ='color:green'>-</p>", "1,139.84", "-0.99", ""]`
- row for 2330: not present

### B1 tables[4] — '報酬指數(跨市場)'
- table keys: `['title', 'fields', 'data']`
- rows: 47
- fields: `["報酬指數", "收盤指數", "漲跌(+/-)", "漲跌點數", "漲跌百分比(%)", "特殊處理註記"]`
- 47 rows; 4-digit numeric codes: 0; codes starting '00' (ETF-ish): 0; other (warrants/ETNs/etc, non-4-digit): 47
- row[0]: `["臺灣生技報酬指數", "4,885.11", "<p style ='color:red'>+</p>", "12.34", "0.25", ""]`
- row[1]: `["臺灣中小型公司治理報酬指數", "29,152.46", "<p style ='color:green'>-</p>", "54.94", "-0.19", ""]`
- row for 2330: not present

### B1 tables[5] — '報酬指數(臺灣指數公司)'
- table keys: `['title', 'fields', 'data']`
- rows: 34
- fields: `["報酬指數", "收盤指數", "漲跌(+/-)", "漲跌點數", "漲跌百分比(%)", "特殊處理註記"]`
- 34 rows; 4-digit numeric codes: 0; codes starting '00' (ETF-ish): 0; other (warrants/ETNs/etc, non-4-digit): 34
- row[0]: `["漲升股利150報酬指數", "36,708.78", "<p style ='color:green'>-</p>", "213.20", "-0.58", ""]`
- row[1]: `["藍籌30報酬指數", "42,299.19", "<p style ='color:green'>-</p>", "548.18", "-1.28", ""]`
- row for 2330: not present

### B1 tables[6] — '115年10月08日 大盤統計資訊'
- table keys: `['title', 'fields', 'data', 'hints']`
- rows: 17
- fields: `["成交統計", "成交金額(元)", "成交股數(股)", "成交筆數"]`
- 17 rows; 4-digit numeric codes: 0; codes starting '00' (ETF-ish): 0; other (warrants/ETNs/etc, non-4-digit): 17
- row[0]: `["1.一般股票", "854,684,336,071", "4,336,915,500", "3,880,638"]`
- row[1]: `["2.台灣存託憑證", "208,616,424", "26,849,455", "6,423"]`
- row for 2330: not present

### B1 tables[7] — '漲跌證券數合計'
- table keys: `['title', 'fields', 'data', 'notes']`
- notes: `["\"漲跌價差\"為當日收盤價與前一日收盤價比較。", "\"無比價\"含前一日無收盤價、當日除權、除息、新上市、恢復交易者。", "外幣成交值係以本公司當日下午3時30分公告匯率換算後加入成交金額。公告匯率請參考本公司首頁>產品與服務>交易系統>雙幣ETF專區>代號對應及每日公告匯率。"]`
- rows: 5
- fields: `["類型", "整體市場", "股票"]`
- 5 rows; 4-digit numeric codes: 0; codes starting '00' (ETF-ish): 0; other (warrants/ETNs/etc, non-4-digit): 5
- row[0]: `["上漲(漲停)", "5,285(78)", "425(14)"]`
- row[1]: `["下跌(跌停)", "9,634(83)", "540(2)"]`
- row for 2330: not present

### B1 tables[8] — '115年10月08日 每日收盤行情(全部(不含權證、牛熊證、可展延牛熊證))'
- table keys: `['title', 'fields', 'data', 'hints', 'groups', 'notes']`
- notes: `["漲跌(+/-)欄位符號說明:+/-/X表示漲/跌/不比價。", "當證券代號為認購(售)權證及認股權憑證時本益比欄位置為結算價；但如為以外國證券或指數為標的之認購(售)權證及履約方式採歐式者，該欄位為空白。", "除境外指數股票型基金及外國股票第二上市外，餘交易單位皆為千股。", "本統計資訊含一般、零股、盤後定價、鉅額交易，不含拍賣、標購。"]`
- groups: `[{"start": 0, "span": 11, "title": "(元,股)"}, {"start": 11, "span": 5, "title": "(元,交易單位)"}]`
- rows: 1380
- fields: `["證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額", "開盤價", "最高價", "最低價", "收盤價", "漲跌(+/-)", "漲跌價差", "最後揭示買價", "最後揭示買量", "最後揭示賣價", "最後揭示賣量", "本益比"]`
- 1380 rows; 4-digit numeric codes: 1094; codes starting '00' (ETF-ish): 240; other (warrants/ETNs/etc, non-4-digit): 286
- row[0]: `["00400A", "主動國泰動能高息", "28,722,889", "10,751", "462,794,154", "16.12", "16.22", "15.98", "16.15", "<p>X</p>", "0.00", "16.15", "818", "16.16", "1,169", "0.00"]`
- row[1]: `["00401A", "主動摩根台灣鑫收", "5,380,337", "1,615", "78,208,949", "14.57", "14.57", "14.47", "14.53", "<p style= color:green>-</p>", "0.09", "14.52", "274", "14.53", "255", "0.00"]`
- row for 2330: `["2330", "台積電", "23,145,193", "141,976", "59,167,379,273", "2,550.00", "2,575.00", "2,550.00", "2,550.00", "<p style= color:green>-</p>", "35.00", "2,550.00", "69", "2,555.00", "140", "29.56"]`

### B1 tables[9] — None
- table keys: `[]`
- rows: 0
- non-list values: `{"type": "ALLBUT0999", "params": {"date": "20261008", "type": "ALLBUT0999", "response": "json", "controller": "afterTrading", "action": "MI_INDEX", "lang": "zh"}, "stat": "OK", "date": "20261008"}`

## B2

- URL: `https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date=20260601&type=ALLBUT0999&response=json`
- HTTP 200; content-type `application/json;charset=UTF-8`; size 246925 bytes; body kind: json
- top-level keys: `['tables', 'type', 'params', 'stat', 'date']`
- stat: `"OK"`
- date: `"20260601"`

### B2 tables[0] — '115年06月01日 價格指數(臺灣證券交易所)'
- table keys: `['title', 'fields', 'data', 'hints']`
- rows: 56
- fields: `["指數", "收盤指數", "漲跌(+/-)", "漲跌點數", "漲跌百分比(%)", "特殊處理註記"]`
- 56 rows; 4-digit numeric codes: 0; codes starting '00' (ETF-ish): 0; other (warrants/ETNs/etc, non-4-digit): 56
- row[0]: `["寶島股價指數", "50,741.92", "<p style ='color:red'>+</p>", "646.14", "1.29", ""]`
- row[1]: `["發行量加權股價指數", "45,337.91", "<p style ='color:red'>+</p>", "604.97", "1.35", ""]`
- row for 2330: not present

### B2 tables[1] — '價格指數(跨市場)'
- table keys: `['title', 'fields', 'data']`
- rows: 48
- fields: `["指數", "收盤指數", "漲跌(+/-)", "漲跌點數", "漲跌百分比(%)", "特殊處理註記"]`
- 48 rows; 4-digit numeric codes: 0; codes starting '00' (ETF-ish): 0; other (warrants/ETNs/etc, non-4-digit): 48
- row[0]: `["臺灣生技指數", "3,626.77", "<p style ='color:red'>+</p>", "42.28", "1.18", ""]`
- row[1]: `["臺灣中小型公司治理指數", "21,656.73", "<p style ='color:red'>+</p>", "293.49", "1.37", ""]`
- row for 2330: not present

### B2 tables[2] — '價格指數(臺灣指數公司)'
- table keys: `['title', 'fields', 'data']`
- rows: 34
- fields: `["指數", "收盤指數", "漲跌(+/-)", "漲跌點數", "漲跌百分比(%)", "特殊處理註記"]`
- 34 rows; 4-digit numeric codes: 0; codes starting '00' (ETF-ish): 0; other (warrants/ETNs/etc, non-4-digit): 34
- row[0]: `["金融類日報酬兩倍指數", "74,334.64", "<p style ='color:red'>+</p>", "2,700.03", "3.77", ""]`
- row[1]: `["金融類日報酬反向一倍指數", "2,604.68", "<p style ='color:green'>-</p>", "50.03", "-1.88", ""]`
- row for 2330: not present

### B2 tables[3] — '報酬指數(臺灣證券交易所)'
- table keys: `['title', 'fields', 'data']`
- rows: 47
- fields: `["報酬指數", "收盤指數", "漲跌(+/-)", "漲跌點數", "漲跌百分比(%)", "特殊處理註記"]`
- 47 rows; 4-digit numeric codes: 0; codes starting '00' (ETF-ish): 0; other (warrants/ETNs/etc, non-4-digit): 47
- row[0]: `["寶島股價報酬指數", "77,880.32", "<p style ='color:red'>+</p>", "993.03", "1.29", ""]`
- row[1]: `["發行量加權股價報酬指數", "103,434.65", "<p style ='color:red'>+</p>", "1,381.66", "1.35", ""]`
- row for 2330: not present

### B2 tables[4] — '報酬指數(跨市場)'
- table keys: `['title', 'fields', 'data']`
- rows: 49
- fields: `["報酬指數", "收盤指數", "漲跌(+/-)", "漲跌點數", "漲跌百分比(%)", "特殊處理註記"]`
- 49 rows; 4-digit numeric codes: 0; codes starting '00' (ETF-ish): 0; other (warrants/ETNs/etc, non-4-digit): 49
- row[0]: `["臺灣生技報酬指數", "4,222.91", "<p style ='color:red'>+</p>", "49.23", "1.18", ""]`
- row[1]: `["臺灣中小型公司治理報酬指數", "31,444.69", "<p style ='color:red'>+</p>", "426.13", "1.37", ""]`
- row for 2330: not present

### B2 tables[5] — '報酬指數(臺灣指數公司)'
- table keys: `['title', 'fields', 'data']`
- rows: 33
- fields: `["報酬指數", "收盤指數", "漲跌(+/-)", "漲跌點數", "漲跌百分比(%)", "特殊處理註記"]`
- 33 rows; 4-digit numeric codes: 0; codes starting '00' (ETF-ish): 0; other (warrants/ETNs/etc, non-4-digit): 33
- row[0]: `["漲升股利150報酬指數", "33,942.11", "<p style ='color:red'>+</p>", "691.38", "2.08", ""]`
- row[1]: `["藍籌30報酬指數", "39,470.15", "<p style ='color:red'>+</p>", "768.51", "1.99", ""]`
- row for 2330: not present

### B2 tables[6] — '115年06月01日 大盤統計資訊'
- table keys: `['title', 'fields', 'data', 'hints']`
- rows: 17
- fields: `["成交統計", "成交金額(元)", "成交股數(股)", "成交筆數"]`
- 17 rows; 4-digit numeric codes: 0; codes starting '00' (ETF-ish): 0; other (warrants/ETNs/etc, non-4-digit): 17
- row[0]: `["1.一般股票", "1,499,740,827,345", "10,103,730,124", "6,381,763"]`
- row[1]: `["2.台灣存託憑證", "1,883,395,612", "199,667,945", "37,328"]`
- row for 2330: not present

### B2 tables[7] — '漲跌證券數合計'
- table keys: `['title', 'fields', 'data', 'notes']`
- notes: `["\"漲跌價差\"為當日收盤價與前一日收盤價比較。", "\"無比價\"含前一日無收盤價、當日除權、除息、新上市、恢復交易者。", "外幣成交值係以本公司當日下午3時30分公告匯率換算後加入成交金額。公告匯率請參考本公司首頁>產品與服務>交易系統>雙幣ETF專區>代號對應及每日公告匯率。"]`
- rows: 5
- fields: `["類型", "整體市場", "股票"]`
- 5 rows; 4-digit numeric codes: 0; codes starting '00' (ETF-ish): 0; other (warrants/ETNs/etc, non-4-digit): 5
- row[0]: `["上漲(漲停)", "9,668(623)", "767(66)"]`
- row[1]: `["下跌(跌停)", "4,055(59)", "253(1)"]`
- row for 2330: not present

### B2 tables[8] — '115年06月01日 每日收盤行情(全部(不含權證、牛熊證、可展延牛熊證))'
- table keys: `['title', 'fields', 'data', 'hints', 'groups', 'notes']`
- notes: `["漲跌(+/-)欄位符號說明:+/-/X表示漲/跌/不比價。", "當證券代號為認購(售)權證及認股權憑證時本益比欄位置為結算價；但如為以外國證券或指數為標的之認購(售)權證及履約方式採歐式者，該欄位為空白。", "除境外指數股票型基金及外國股票第二上市外，餘交易單位皆為千股。", "本統計資訊含一般、零股、盤後定價、鉅額交易，不含拍賣、標購。"]`
- groups: `[{"start": 0, "span": 11, "title": "(元,股)"}, {"start": 11, "span": 5, "title": "(元,交易單位)"}]`
- rows: 1362
- fields: `["證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額", "開盤價", "最高價", "最低價", "收盤價", "漲跌(+/-)", "漲跌價差", "最後揭示買價", "最後揭示買量", "最後揭示賣價", "最後揭示賣量", "本益比"]`
- 1362 rows; 4-digit numeric codes: 1090; codes starting '00' (ETF-ish): 225; other (warrants/ETNs/etc, non-4-digit): 272
- row[0]: `["00400A", "主動國泰動能高息", "59,165,872", "8,756", "886,604,300", "14.93", "15.11", "14.85", "14.95", "<p style= color:red>+</p>", "0.12", "14.94", "62", "14.95", "447", "0.00"]`
- row[1]: `["00401A", "主動摩根台灣鑫收", "5,925,398", "1,141", "81,774,248", "13.77", "13.89", "13.73", "13.74", "<p style= color:red>+</p>", "0.07", "13.73", "40", "13.74", "54", "0.00"]`
- row for 2330: `["2330", "台積電", "60,942,792", "136,367", "144,105,259,583", "2,355.00", "2,415.00", "2,350.00", "2,355.00", "<p> </p>", "0.00", "2,355.00", "240", "2,360.00", "117", "31.66"]`

### B2 tables[9] — None
- table keys: `[]`
- rows: 0
- non-list values: `{"type": "ALLBUT0999", "params": {"date": "20260601", "type": "ALLBUT0999", "response": "json", "controller": "afterTrading", "action": "MI_INDEX", "lang": "zh"}, "stat": "OK", "date": "20260601"}`

## B3

- URL: `https://www.twse.com.tw/rwd/zh/fund/T86?date=20261008&selectType=ALLBUT0999&response=json`
- HTTP 307; content-type `text/html; charset=UTF-8`; size 800 bytes; body kind: html
- body head: `'<html>\n<head>\n<meta http-equiv="Content-Type" content="text/html; charset=utf-8">\n</head>\n<body> \n因為安全性考量，您所執行的頁面無法呈現。<BR>\nFOR SECURITY REASONS, THIS PAGE CAN NOT BE ACCESSED.<BR>\n<BR>\n您可以<A HREF="javascript:history.back()">回上一頁</A>繼續其他動作，或撥打以下電話通報問題：(02)8101-5840, (02)8101-5834。<BR>\nYOU COULD GO <A HREF="javascript:history.back()">BACK</A> TO PREVIOUS PAGE OR CALL US FOR FURTHER HELP：(02)8101-584'`

## B4

- URL: `https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date=20261008&selectType=ALL&response=json`
- **SKIPPED**: host blocked: B3: HTTP 307 html

## B5

- URL: `https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d?date=20261008&selectType=ALL&response=json`
- **SKIPPED**: host blocked: B3: HTTP 307 html

## B6

- URL: `https://www.twse.com.tw/rwd/zh/fund/BFI82U?type=day&dayDate=20261008&response=json`
- **SKIPPED**: host blocked: B3: HTTP 307 html

## B7

- URL: `https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date=20261009&type=ALLBUT0999&response=json`
- **SKIPPED**: host blocked: B3: HTTP 307 html

## B8

- URL: `https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?date=20261001&response=json`
- **SKIPPED**: host blocked: B3: HTTP 307 html

## C1

- URL: `https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes`
- HTTP 200; content-type `application/json`; size 4782391 bytes; body kind: json
- list length: 12221
- keys of item[0]: `["Date", "SecuritiesCompanyCode", "CompanyName", "Close", "Change", "Open", "High", "Low", "Average", "TradingShares", "TransactionAmount", "TransactionNumber", "LatestBidPrice", "LatesAskPrice", "Capitals", "NextReferencePrice", "NextLimitUp", "NextLimitDown"]`
- rows: 12221
- 12221 rows; 4-digit numeric codes: 886; codes starting '00' (ETF-ish): 118; other (warrants/ETNs/etc, non-4-digit): 11335
- row[0]: `{"Date": "1151008", "SecuritiesCompanyCode": "00411A", "CompanyName": "主動統一前沿科技", "Close": "11.13", "Change": "-0.10 ", "Open": "11.06", "High": "11.16", "Low": "11.06", "Average": "11.12", "TradingShares": "15878753", "TransactionAmount": "176540418", "TransactionNumber": "2826", "LatestBidPrice": "11.12", "LatesAskPrice": "11.13", "Capitals": "419576000", "NextReferencePrice": "11.13", "NextLimitUp": "9999.95", "NextLimitDown": "0.01"}`
- row[1]: `{"Date": "1151008", "SecuritiesCompanyCode": "006201", "CompanyName": "元大富櫃50", "Close": "47.82", "Change": "-0.66 ", "Open": "48.38", "High": "48.38", "Low": "47.00", "Average": "47.77", "TradingShares": "243420", "TransactionAmount": "11629139", "TransactionNumber": "277", "LatestBidPrice": "47.80", "LatesAskPrice": "47.82", "Capitals": "22946000", "NextReferencePrice": "47.82", "NextLimitUp": "52.60", "NextLimitDown": "43.04"}`
- row for 6488: `{"Date": "1151008", "SecuritiesCompanyCode": "6488", "CompanyName": "環球晶", "Close": "1130.00", "Change": "-85.00 ", "Open": "1180.00", "High": "1195.00", "Low": "1095.00", "Average": "1128.62", "TradingShares": "20608056", "TransactionAmount": "23258740205", "TransactionNumber": "49278", "LatestBidPrice": "1130.00", "LatesAskPrice": "1135.00", "Capitals": "528113725", "NextReferencePrice": "1130.00", "NextLimitUp": "1240.00", "NextLimitDown": "1020.00"}`

## C2

- URL: `https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis`
- HTTP 200; content-type `application/json`; size 155978 bytes; body kind: json
- list length: 885
- keys of item[0]: `["Date", "SecuritiesCompanyCode", "CompanyName", "PriceEarningRatio", "DividendPerShare", "YieldRatio", "PriceBookRatio"]`
- rows: 885
- 885 rows; 4-digit numeric codes: 885; codes starting '00' (ETF-ish): 0; other (warrants/ETNs/etc, non-4-digit): 0
- row[0]: `{"Date": "1151008", "SecuritiesCompanyCode": "1240", "CompanyName": "茂生農經", "PriceEarningRatio": "10.00", "DividendPerShare": "0.50000000", "YieldRatio": "0.93", "PriceBookRatio": "1.59"}`
- row[1]: `{"Date": "1151008", "SecuritiesCompanyCode": "1259", "CompanyName": "安心", "PriceEarningRatio": "18.14", "DividendPerShare": "1.20000000", "YieldRatio": "1.87", "PriceBookRatio": "0.74"}`
- row for 6488: `{"Date": "1151008", "SecuritiesCompanyCode": "6488", "CompanyName": "環球晶", "PriceEarningRatio": "54.85", "DividendPerShare": "7.70000000", "YieldRatio": "0.68", "PriceBookRatio": "5.59"}`

## C3

- URL: `https://www.tpex.org.tw/openapi/v1/tpex_3insti_daily_trading`
- HTTP 200; content-type `application/json`; size 844241 bytes; body kind: json
- list length: 892
- keys of item[0]: `["Date", "SecuritiesCompanyCode", "CompanyName", "Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Total Buy", " Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Total Sell", "Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Difference", "Foreign Dealers-Total Buy", "Foreign Dealers-TotalSell", "ForeignDealers-Difference", "ForeignInvestorsIncludeMainlandAreaInvestors-TotalBuy", "ForeignInvestorsIncludeMainlandAreaInvestors-TotalSell", "ForeignInvestorsInclude MainlandAreaInvestors-Difference", "SecuritiesInvestmentTrustCompanies-TotalBuy", "SecuritiesInvestmentTrustCompanies-TotalSell", "SecuritiesInvestmentTrustCompanies-Difference", "Dealers-TotalBuy", "Dealers-TotalSell", "Dealers-Difference", "Dealers -TotalSell", "TotalDifference"]`
- rows: 892
- 892 rows; 4-digit numeric codes: 770; codes starting '00' (ETF-ish): 118; other (warrants/ETNs/etc, non-4-digit): 122
- row[0]: `{"Date": "1151008", "SecuritiesCompanyCode": "00411A", "CompanyName": "主動統一前沿科技", "Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Total Buy": "1304000", " Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Total Sell": "1619500", "Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Difference": "-315500", "Foreign Dealers-Total Buy": "0", "Foreign Dealers-TotalSell": "0", "ForeignDealers-Difference": "0", "ForeignInvestorsIncludeMainlandAreaInvestors-TotalBuy": "1304000", "ForeignInvestorsIncludeMainlandAreaInvestors-TotalSell": "1619500", "ForeignInvestorsInclude MainlandAreaInvestors-Difference": "-315500", "SecuritiesInvestmentTrustCompanies-TotalBuy": "0", "SecuritiesInvestmentTrustCompanies-TotalSell": "0", "SecuritiesInvestmentTrustCompanies-Difference": "0", "Dealers-TotalBuy": "2851583", "Dealers-TotalSell": "6046351", "Dealers-Difference": "-3194768", "Dealers -TotalSell": "6046351", "TotalDifference": "-3510268"}`
- row[1]: `{"Date": "1151008", "SecuritiesCompanyCode": "00679B", "CompanyName": "元大美債20年", "Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Total Buy": "4603133", " Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Total Sell": "347000", "Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Difference": "4256133", "Foreign Dealers-Total Buy": "0", "Foreign Dealers-TotalSell": "0", "ForeignDealers-Difference": "0", "ForeignInvestorsIncludeMainlandAreaInvestors-TotalBuy": "4603133", "ForeignInvestorsIncludeMainlandAreaInvestors-TotalSell": "347000", "ForeignInvestorsInclude MainlandAreaInvestors-Difference": "4256133", "SecuritiesInvestmentTrustCompanies-TotalBuy": "0", "SecuritiesInvestmentTrustCompanies-TotalSell": "0", "SecuritiesInvestmentTrustCompanies-Difference": "0", "Dealers-TotalBuy": "3624000", "Dealers-TotalSell": "6598243", "Dealers-Difference": "-2974243", "Dealers -TotalSell": "6598243", "TotalDifference": "1281890"}`
- row for 6488: `{"Date": "1151008", "SecuritiesCompanyCode": "6488", "CompanyName": "環球晶", "Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Total Buy": "6053408", " Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Total Sell": "7354566", "Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Difference": "-1301158", "Foreign Dealers-Total Buy": "0", "Foreign Dealers-TotalSell": "0", "ForeignDealers-Difference": "0", "ForeignInvestorsIncludeMainlandAreaInvestors-TotalBuy": "6053408", "ForeignInvestorsIncludeMainlandAreaInvestors-TotalSell": "7354566", "ForeignInvestorsInclude MainlandAreaInvestors-Difference": "-1301158", "SecuritiesInvestmentTrustCompanies-TotalBuy": "51100", "SecuritiesInvestmentTrustCompanies-TotalSell": "1040000", "SecuritiesInvestmentTrustCompanies-Difference": "-988900", "Dealers-TotalBuy": "902542", "Dealers-TotalSell": "898292", "Dealers-Difference": "4250", "Dealers -TotalSell": "789597", "TotalDifference": "-2285808"}`

## C4

- URL: `https://www.tpex.org.tw/openapi/v1/tpex_mainboard_margin_balance`
- HTTP 200; content-type `application/json`; size 550896 bytes; body kind: json
- list length: 919
- keys of item[0]: `["Date", "SecuritiesCompanyCode", "CompanyName", "MarginPurchaseBalancePreviousDay", "MarginPurchase", "MarginSales", "CashRedemption", "MarginPurchaseBalance", "MarginPurchaseBalanceBelongSecuritiesFinanceEnterprise", "MarginPurchaseUtilizationRate", "MarginPurchaseQuota", "ShortSaleBalancePreviousDay", "ShortSale", "ShortConvering", "StockRedemption", "ShortSaleBalance", "ShortSaleBalanceBelongSecuritiesFinanceEnterprise", "ShortSaleUtilizationRate", "ShortSaleQuota", "Offsetting", "Note"]`
- rows: 919
- 919 rows; 4-digit numeric codes: 802; codes starting '00' (ETF-ish): 117; other (warrants/ETNs/etc, non-4-digit): 117
- row[0]: `{"Date": "1151008", "SecuritiesCompanyCode": "00411A", "CompanyName": "主動統一前沿科技", "MarginPurchaseBalancePreviousDay": "10662", "MarginPurchase": "382", "MarginSales": "684", "CashRedemption": "0", "MarginPurchaseBalance": "10360", "MarginPurchaseBalanceBelongSecuritiesFinanceEnterprise": "43", "MarginPurchaseUtilizationRate": "9.87", "MarginPurchaseQuota": "104894", "ShortSaleBalancePreviousDay": "12", "ShortSale": "0", "ShortConvering": "0", "StockRedemption": "0", "ShortSaleBalance": "12", "ShortSaleBalanceBelongSecuritiesFinanceEnterprise": "0", "ShortSaleUtilizationRate": "0.01", "ShortSaleQuota": "104894", "Offsetting": "0", "Note": ""}`
- row[1]: `{"Date": "1151008", "SecuritiesCompanyCode": "00679B", "CompanyName": "元大美債20年", "MarginPurchaseBalancePreviousDay": "4618", "MarginPurchase": "25", "MarginSales": "184", "CashRedemption": "19", "MarginPurchaseBalance": "4440", "MarginPurchaseBalanceBelongSecuritiesFinanceEnterprise": "137", "MarginPurchaseUtilizationRate": "0.28", "MarginPurchaseQuota": "1560548", "ShortSaleBalancePreviousDay": "5", "ShortSale": "0", "ShortConvering": "0", "StockRedemption": "0", "ShortSaleBalance": "5", "ShortSaleBalanceBelongSecuritiesFinanceEnterprise": "0", "ShortSaleUtilizationRate": "0.0", "ShortSaleQuota": "1560548", "Offsetting": "1", "Note": ""}`
- row for 6488: `{"Date": "1151008", "SecuritiesCompanyCode": "6488", "CompanyName": "環球晶", "MarginPurchaseBalancePreviousDay": "17969", "MarginPurchase": "1520", "MarginSales": "2452", "CashRedemption": "1", "MarginPurchaseBalance": "17036", "MarginPurchaseBalanceBelongSecuritiesFinanceEnterprise": "242", "MarginPurchaseUtilizationRate": "12.9", "MarginPurchaseQuota": "132028", "ShortSaleBalancePreviousDay": "565", "ShortSale": "6", "ShortConvering": "71", "StockRedemption": "0", "ShortSaleBalance": "500", "ShortSaleBalanceBelongSecuritiesFinanceEnterprise": "0", "ShortSaleUtilizationRate": "0.37", "ShortSaleQuota": "132028", "Offsetting": "33", "Note": ""}`

## C5

- URL: `https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O`
- HTTP 200; content-type `application/json`; size 1074408 bytes; body kind: json
- list length: 893
- keys of item[0]: `["Date", "SecuritiesCompanyCode", "CompanyName", "CompanyAbbreviation", "Registration", "SecuritiesIndustryCode", "Address", "UnifiedBusinessNo.", "Chairman", "GeneralManager", "Spokesman", "TitleOfSpokesman", "DeputySpokesperson", "Telephone", "DateOfIncorporation", "DateOfListing", "ParValueOfCommonStock", "Paidin.Capital.NTDollars", "PrivateStock.shares", "PreferredStock.shares", "PreparationOfFinancialReportType", "StockTransferAgent", "StockTransferAgentTelephone", "StockTransferAgentAddress", "AccountingFirm", "CPA.CharteredPublicAccountant.First", "CPA.CharteredPublicAccountant.Second", "Symbol", "Fax", "EmailAddress", "WebAddress", "IssueShares"]`
- rows: 893
- 893 rows; 4-digit numeric codes: 893; codes starting '00' (ETF-ish): 0; other (warrants/ETNs/etc, non-4-digit): 0
- row[0]: `{"Date": "1151010", "SecuritiesCompanyCode": "1240", "CompanyName": "茂生農經股份有限公司", "CompanyAbbreviation": "茂生農經", "Registration": "－ ", "SecuritiesIndustryCode": "33", "Address": "2F.,No.30,Sec. 1,Heping W.Rd.,Zhongzheng Dist.,Taipei City 100028TAIPEI,TAIWAN(R.O.C)", "UnifiedBusinessNo.": "18795706", "Chairman": "吳清德", "GeneralManager": "吳清德", "Spokesman": "林信鴻", "TitleOfSpokesman": "公司治理主管", "DeputySpokesperson": "邱紹齊", "Telephone": "02-23671162", "DateOfIncorporation": "19670218", "DateOfListing": "20180808", "ParValueOfCommonStock": "新台幣                 10.0000元", "Paidin.Capital.NTDollars": "464439920", "PrivateStock.shares": "0", "PreferredStock.shares": "0", "PreparationOfFinancialReportType": "1", "StockTransferAgent": "元大證券股份有限公司股務代理部", "StockTransferAgentTelephone": "02-2586-5859", "StockTransferAgentAddress": "106045台北市大安區敦化南路二段67號地下1樓", "AccountingFirm": "勤業眾信聯合會計師事務所", "CPA.CharteredPublicAccountant.First": "陳重成", "CPA.CharteredPublicAccountant.Second": "洪偉倫", "Symbol": "MORNSUN　", "Fax": "02-23640694", "EmailAddress": "bedford@morn-sun.com.tw", "WebAddress": "https://www.morn-sun.com.tw/　", "IssueShares": "46443992"}`
- row[1]: `{"Date": "1151010", "SecuritiesCompanyCode": "1259", "CompanyName": "安心食品服務股份有限公司", "CompanyAbbreviation": "安心", "Registration": "－ ", "SecuritiesIndustryCode": "16", "Address": "16 F.-3, No. 66, Jingmao 2nd Rd., Nangang Dist., Taipei CityTaipei City 115605, Taiwan (R.O.C.)", "UnifiedBusinessNo.": "23928945", "Chairman": "黃茂雄", "GeneralManager": "謝靜慧", "Spokesman": "高順興", "TitleOfSpokesman": "副董事長", "DeputySpokesperson": "卓世賢", "Telephone": "(02)2567-5001", "DateOfIncorporation": "19901123", "DateOfListing": "20111215", "ParValueOfCommonStock": "新台幣                 10.0000元", "Paidin.Capital.NTDollars": "323895000", "PrivateStock.shares": "0", "PreferredStock.shares": "0", "PreparationOfFinancialReportType": "1", "StockTransferAgent": "群益金鼎證券(股)公司股務代理部", "StockTransferAgentTelephone": "(02)27023999", "StockTransferAgentAddress": "(106)台北市大安區敦化南路二段97號B2", "AccountingFirm": "資誠聯合會計師事務所", "CPA.CharteredPublicAccountant.First": "徐明釧", "CPA.CharteredPublicAccountant.Second": "陳怡婷", "Symbol": "AN-SHIN", "Fax": "(02)2567-5002", "EmailAddress": "ir@mos.com.tw", "WebAddress": "http://www.mos.com.tw", "IssueShares": "32389500"}`
- row for 6488: `{"Date": "1151010", "SecuritiesCompanyCode": "6488", "CompanyName": "環球晶圓股份有限公司", "CompanyAbbreviation": "環球晶", "Registration": "－ ", "SecuritiesIndustryCode": "24", "Address": "No. 8. Industrial East Road 2, Hsinchu Science Park, Taiwan, R.O.C.Taiwan R.O.C", "UnifiedBusinessNo.": "28113286", "Chairman": "徐秀蘭", "GeneralManager": "Mark England", "Spokesman": "彭欣瑜", "TitleOfSpokesman": "董事長特別助理", "DeputySpokesperson": "黃琮芸/陳保全", "Telephone": "03-5772255", "DateOfIncorporation": "20111018", "DateOfListing": "20150925", "ParValueOfCommonStock": "新台幣                 10.0000元", "Paidin.Capital.NTDollars": "4781137250", "PrivateStock.shares": "0", "PreferredStock.shares": "0", "PreparationOfFinancialReportType": "1", "StockTransferAgent": "元大證券(股)公司股務代理部", "StockTransferAgentTelephone": "02-2586-5859", "StockTransferAgentAddress": "台北市大安區敦化南路二段67號地下一樓", "AccountingFirm": "安侯建業聯合會計師事務所", "CPA.CharteredPublicAccountant.First": "吳俊源", "CPA.CharteredPublicAccountant.Second": "黃泳華", "Symbol": "GWC", "Fax": "03-5781706", "EmailAddress": "GWCIR@sas-globalwafers.com", "WebAddress": "http://www.sas-globalwafers.com", "IssueShares": "478113725"}`

## D1

- URL: `https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date=2026%2F10%2F08&id=&response=json`
- HTTP 200; content-type `application/json;charset=UTF-8`; size 1841812 bytes; body kind: json
- top-level keys: `['date', 'tables', 'flagField', 'stat']`
- stat: `"ok"`
- date: `"20261008"`

### D1 tables[0] — '上櫃股票行情'
- table keys: `['title', 'subtitle', 'date', 'listedCompanies', 'totalTradingAmount', 'totalTradingShares', 'totalTranscations', 'totalCount', 'fields', 'data', 'notes']`
- date: `"115/10/08"`
- totalCount: `12221`
- rows: 12221
- fields: `["代號", "名稱", "收盤", "漲跌", "開盤", "最高", "最低", "均價", "成交股數", "成交金額(元)", "成交筆數", "最後買價", "最後買量(張數)", "最後賣價", "最後賣量(張數)", "發行股數", "次日 參考價", "次日 漲停價", "次日 跌停價"]`
- 12221 rows; 4-digit numeric codes: 886; codes starting '00' (ETF-ish): 118; other (warrants/ETNs/etc, non-4-digit): 11335
- row[0]: `["00411A", "主動統一前沿科技", "11.13", "-0.10 ", "11.06", "11.16", "11.06", "11.12", "15,878,753", "176,540,418", "2,826", "11.12", "1549", "11.13", "25", "419,576,000", "11.13", "9999.95", "0.01"]`
- row[1]: `["006201", "元大富櫃50", "47.82", "-0.66 ", "48.38", "48.38", "47.00", "47.77", "243,420", "11,629,139", "277", "47.80", "3", "47.82", "1", "22,946,000", "47.82", "52.60", "43.04"]`
- row for 6488: `["6488", "環球晶", "1130.00", "-85.00 ", "1180.00", "1195.00", "1095.00", "1128.62", "20,608,056", "23,258,740,205", "49,278", "1130.00", "21", "1135.00", "44", "528,113,725", "1130.00", "1240.00", "1020.00"]`

### D1 tables[1] — '管理股票'
- table keys: `['title', 'subtitle', 'totalCount', 'fields', 'data', 'notes']`
- notes: `["認購(售)權證標的物若當日遇除權息時，該權證行使比例之異動資料尚未更新，故次日參考價資料係以異動前之行使比例計算僅供參考, 請直接於次一營業日查詢本中心網站-->衍生性金融商品 -->認購(售)權證 -->權證資訊-->權證收盤行情下之參考價資料", "--- : 個股當日無交易時，以此符號表示開市價、最高價、最低價、收盤價、漲跌價", "ETF證券代號第六碼為K、C者，表示該ETF以外幣交易，其一交易單位(張)應為100受益權或其整倍數，請詳其公開說明書。"]`
- rows: 0
- fields: `["代號", "名稱", "收盤", "漲跌", "開盤", "最高", "最低", "均價", "成交股數", "成交金額(元)", "成交筆數", "最後買價", "最後買量(張數)", "最後賣價", "最後賣量(張數)", "發行股數", "次日 參考價", "次日 漲停價", "次日 跌停價"]`
- non-list values: `{"date": "20261008", "flagField": "張數", "stat": "ok"}`

## D2

- URL: `https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date=2026%2F06%2F01&id=&response=json`
- HTTP 200; content-type `application/json;charset=UTF-8`; size 1577157 bytes; body kind: json
- top-level keys: `['date', 'tables', 'flagField', 'stat']`
- stat: `"ok"`
- date: `"20260601"`

### D2 tables[0] — '上櫃股票行情'
- table keys: `['title', 'subtitle', 'date', 'listedCompanies', 'totalTradingAmount', 'totalTradingShares', 'totalTranscations', 'totalCount', 'fields', 'data', 'notes']`
- date: `"115/06/01"`
- totalCount: `10402`
- rows: 10402
- fields: `["代號", "名稱", "收盤", "漲跌", "開盤", "最高", "最低", "均價", "成交股數", "成交金額(元)", "成交筆數", "最後買價", "最後買量(張數)", "最後賣價", "最後賣量(張數)", "發行股數", "次日 參考價", "次日 漲停價", "次日 跌停價"]`
- 10402 rows; 4-digit numeric codes: 887; codes starting '00' (ETF-ish): 113; other (warrants/ETNs/etc, non-4-digit): 9515
- row[0]: `["006201", "元大富櫃50", "50.35", "+0.05", "50.35", "50.95", "50.15", "50.52", "327,478", "16,542,810", "358", "50.35", "3", "50.45", "5", "19,446,000", "50.35", "55.35", "45.32"]`
- row[1]: `["00679B", "元大美債20年", "26.33", "-0.13 ", "26.40", "26.40", "26.29", "26.33", "50,051,385", "1,318,051,152", "6,016", "26.32", "2045", "26.33", "3156", "7,184,192,000", "26.33", "9999.95", "0.01"]`
- row for 6488: `["6488", "環球晶", "950.00", "-65.00 ", "1015.00", "1040.00", "936.00", "973.38", "4,452,238", "4,333,717,873", "6,880", "949.00", "1", "950.00", "572", "478,113,725", "950.00", "1045.00", "855.00"]`

### D2 tables[1] — '管理股票'
- table keys: `['title', 'subtitle', 'totalCount', 'fields', 'data', 'notes']`
- notes: `["認購(售)權證標的物若當日遇除權息時，該權證行使比例之異動資料尚未更新，故次日參考價資料係以異動前之行使比例計算僅供參考, 請直接於次一營業日查詢本中心網站-->衍生性金融商品 -->認購(售)權證 -->權證資訊-->權證收盤行情下之參考價資料", "--- : 個股當日無交易時，以此符號表示開市價、最高價、最低價、收盤價、漲跌價", "ETF證券代號第六碼為K、C者，表示該ETF以外幣交易，其一交易單位(張)應為100受益權或其整倍數，請詳其公開說明書。"]`
- rows: 0
- fields: `["代號", "名稱", "收盤", "漲跌", "開盤", "最高", "最低", "均價", "成交股數", "成交金額(元)", "成交筆數", "最後買價", "最後買量(張數)", "最後賣價", "最後賣量(張數)", "發行股數", "次日 參考價", "次日 漲停價", "次日 跌停價"]`
- non-list values: `{"date": "20260601", "flagField": "張數", "stat": "ok"}`

## D3

- URL: `https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade?type=Daily&sect=EW&date=2026%2F10%2F08&response=json`
- HTTP 200; content-type `application/json;charset=UTF-8`; size 143385 bytes; body kind: json
- top-level keys: `['columnNum', 'tables', 'template', 'date', 'stat']`
- stat: `"ok"`
- date: `"20261008"`

### D3 tables[0] — '三大法人買賣明細資訊'
- table keys: `['title', 'date', 'fields', 'data', 'notes', 'totalCount', 'summary', 'columnNum', 'subtitle']`
- date: `"115/10/08"`
- totalCount: `892`
- rows: 892
- fields: `["代號", "名稱", "買進股數", "賣出股數", "買賣超股數", "買進股數", "賣出股數", "買賣超股數", "買進股數", "賣出股數", "買賣超股數", "買進股數", "賣出股數", "買賣超股數", "買進股數", "賣出股數", "買賣超股數", "買進股數", "賣出股數", "買賣超股數", "買進股數", "賣出股數", "買賣超股數", "三大法人買賣超股數合計"]`
- 892 rows; 4-digit numeric codes: 770; codes starting '00' (ETF-ish): 118; other (warrants/ETNs/etc, non-4-digit): 122
- row[0]: `["00411A", "主動統一前沿科技", "1,304,000", "1,619,500", "-315,500", "0", "0", "0", "1,304,000", "1,619,500", "-315,500", "0", "0", "0", "0", "0", "0", "2,851,583", "6,046,351", "-3,194,768", "2,851,583", "6,046,351", "-3,194,768", "-3,510,268"]`
- row[1]: `["006201", "元大富櫃50", "33,104", "14,000", "19,104", "0", "0", "0", "33,104", "14,000", "19,104", "0", "22,000", "-22,000", "0", "0", "0", "74,354", "84,025", "-9,671", "74,354", "84,025", "-9,671", "-12,567"]`
- row for 6488: `["6488", "環球晶", "6,053,408", "7,354,566", "-1,301,158", "0", "0", "0", "6,053,408", "7,354,566", "-1,301,158", "51,100", "1,040,000", "-988,900", "169,000", "108,695", "60,305", "733,542", "789,597", "-56,055", "902,542", "898,292", "4,250", "-2,285,808"]`

### D3 tables[1] — None
- table keys: `[]`
- rows: 0
- non-list values: `{"columnNum": 25, "template": "/template/insti/dailyTrade", "date": "20261008", "stat": "ok"}`

## D4

- URL: `https://www.tpex.org.tw/www/zh-tw/margin/balance?date=2026%2F10%2F08&id=&response=json`
- HTTP 200; content-type `application/json;charset=UTF-8`; size 107561 bytes; body kind: json
- top-level keys: `['date', 'tables', 'stat']`
- stat: `"ok"`
- date: `"20261008"`

### D4 tables[0] — '上櫃股票融資融券餘額'
- table keys: `['title', 'date', 'totalCount', 'fields', 'data', 'summary', 'notes']`
- date: `"115/10/08"`
- totalCount: `919`
- summary: `[["", "合計(張)", "2,428,639", "80,787", "87,922", "1,940", "2,419,564", "", "", "", "40,410", "5,065", "4,589", "479", "40,407", "", "", "", "", ""], ["", "融資金(仟元)", "228,958,200", "10,034,169", "10,971,829", "148,363", "227,872,177", "", "", "", "", "", "", "", "", "", "", "", "", ""]]`
- rows: 919
- fields: `["代號", "名稱", "前資餘額(張)", "資買", "資賣", "現償", "資餘額", "資屬證金", "資使用率(%)", "資限額", "前券餘額(張)", "券賣", "券買", "券償", "券餘額", "券屬證金", "券使用率(%)", "券限額", "資券相抵(張)", "備註"]`
- 919 rows; 4-digit numeric codes: 802; codes starting '00' (ETF-ish): 117; other (warrants/ETNs/etc, non-4-digit): 117
- row[0]: `["00411A", "主動統一前沿科技", "10,662", "382", "684", "0", "10,360", "43", "9.87", "104,894", "12", "0", "0", "0", "12", "0", "0.01", "104,894", "0", ""]`
- row[1]: `["00679B", "元大美債20年", "4,618", "25", "184", "19", "4,440", "137", "0.28", "1,560,548", "5", "0", "0", "0", "5", "0", "0.0", "1,560,548", "1", ""]`
- row for 6488: `["6488", "環球晶", "17,969", "1,520", "2,452", "1", "17,036", "242", "12.9", "132,028", "565", "6", "71", "0", "500", "0", "0.37", "132,028", "33", ""]`
- non-list values: `{"date": "20261008", "stat": "ok"}`

## D5

- URL: `https://www.tpex.org.tw/www/zh-tw/afterTrading/peQryDate?date=2026%2F10%2F08&response=json`
- HTTP 200; content-type `application/json;charset=UTF-8`; size 69968 bytes; body kind: json
- top-level keys: `['tables', 'date', 'stat']`
- stat: `"ok"`
- date: `"20261008"`

### D5 tables[0] — ''
- table keys: `['title', 'date', 'totalCount', 'fields', 'data', 'summary', 'notes', 'stkCategory']`
- date: `"115/10/08"`
- totalCount: `885`
- notes: `["股票名稱有附註「*」者，表該股票為無面額或面額非屬10元，其本益比、殖利率及股價淨值比之數值自面額變更換發新股即配合調整計算。", "本益比＝收盤價／每股稅後純益，其中每股稅後純益＝該公司最近４季稅後純益／發行股數，當每股稅後純益為 0 或負數時，則不計算本益比。", "殖利率＝每股股利／收盤價 * 100%，其中每股股利為該公司每股配發之現金股利＋盈餘轉增資股票股利(本表之殖利率係採屬前一年度股利計算之)。", "本表之每股股利數值係以該公司於公開資訊觀測站公告之當年度股利分派數值為準，若遇有股本變動或面額變更情事，本表之每股股利數值不另行調整，另本網頁所載內容如與該公司正式公告內容有別，悉以公告內容為準。", "股價淨值比＝收盤價／每股淨值。", "有關上櫃公司之普通股股利分派頻率請至<a href=\"https://mops.twse.com.tw/mops/#/web/t05sb12\" target=\"_blank\">公開資訊觀測站</a>查詢。", "世紀*(5314) 於115年8月14日除權，配股率為每仟股無償配發3,157.02936096股。本益比之計算係以財報公告之每股盈餘 (EPS) 為基礎，投資人參考本益比 (P/E ratio) 時，請注意除權交易日與財務資料更新時間之差異，並應一併考量除權後股本變動對本益比之影響。", "本網頁僅供參考，任何 …(truncated)`
- rows: 885
- fields: `["股票代號", "公司名稱", "本益比", "每股股利", "股利年度", "殖利率(%)", "股價淨值比", "財報年/季"]`
- 885 rows; 4-digit numeric codes: 885; codes starting '00' (ETF-ish): 0; other (warrants/ETNs/etc, non-4-digit): 0
- row[0]: `["1240", "茂生農經        ", "10.00", "0.50000000", 114, "0.93", "1.59", "115Q2"]`
- row[1]: `["1259", "安心            ", "18.14", "1.20000000", 114, "1.87", "0.74", "115Q2"]`
- row for 6488: `["6488", "環球晶          ", "54.85", "7.70000000", 114, "0.68", "5.59", "115Q2"]`
- non-list values: `{"date": "20261008", "stat": "ok"}`

## D6

- URL: `https://www.tpex.org.tw/www/zh-tw/indexInfo/inx?date=2026%2F10%2F08&response=json`
- HTTP 200; content-type `application/json;charset=UTF-8`; size 559 bytes; body kind: json
- top-level keys: `['date', 'tables', 'stat']`
- stat: `"ok"`
- date: `"20261008"`

### D6 tables[0] — '櫃買指數(月查詢)'
- table keys: `['title', 'date', 'totalCount', 'fields', 'data', 'summary', 'notes']`
- date: `"115/10"`
- totalCount: `6`
- rows: 6
- fields: `["日期", "開市", "最高", "最低", "收市", "漲/跌"]`
- 6 rows; 4-digit numeric codes: 0; codes starting '00' (ETF-ish): 0; other (warrants/ETNs/etc, non-4-digit): 6
- row[0]: `["2026/10/01", "417.48", "421.21", "417.09", "418.82", "1.75"]`
- row[1]: `["2026/10/02", "419.94", "426.93", "419.94", "426.93", "8.11"]`
- row for 6488: not present
- non-list values: `{"date": "20261008", "stat": "ok"}`

## D7

- URL: `https://www.tpex.org.tw/web/stock/aftertrading/daily_close_quotes/stk_quote_result.php?l=zh-tw&d=115/10/08&o=json`
- HTTP 200; content-type `application/json;charset=UTF-8`; size 1841812 bytes; body kind: json
- top-level keys: `['date', 'tables', 'flagField', 'stat']`
- stat: `"ok"`
- date: `"20261008"`

### D7 tables[0] — '上櫃股票行情'
- table keys: `['title', 'subtitle', 'date', 'listedCompanies', 'totalTradingAmount', 'totalTradingShares', 'totalTranscations', 'totalCount', 'fields', 'data', 'notes']`
- date: `"115/10/08"`
- totalCount: `12221`
- rows: 12221
- fields: `["代號", "名稱", "收盤", "漲跌", "開盤", "最高", "最低", "均價", "成交股數", "成交金額(元)", "成交筆數", "最後買價", "最後買量(張數)", "最後賣價", "最後賣量(張數)", "發行股數", "次日 參考價", "次日 漲停價", "次日 跌停價"]`
- 12221 rows; 4-digit numeric codes: 886; codes starting '00' (ETF-ish): 118; other (warrants/ETNs/etc, non-4-digit): 11335
- row[0]: `["00411A", "主動統一前沿科技", "11.13", "-0.10 ", "11.06", "11.16", "11.06", "11.12", "15,878,753", "176,540,418", "2,826", "11.12", "1549", "11.13", "25", "419,576,000", "11.13", "9999.95", "0.01"]`
- row[1]: `["006201", "元大富櫃50", "47.82", "-0.66 ", "48.38", "48.38", "47.00", "47.77", "243,420", "11,629,139", "277", "47.80", "3", "47.82", "1", "22,946,000", "47.82", "52.60", "43.04"]`
- row for 6488: `["6488", "環球晶", "1130.00", "-85.00 ", "1180.00", "1195.00", "1095.00", "1128.62", "20,608,056", "23,258,740,205", "49,278", "1130.00", "21", "1135.00", "44", "528,113,725", "1130.00", "1240.00", "1020.00"]`

### D7 tables[1] — '管理股票'
- table keys: `['title', 'subtitle', 'totalCount', 'fields', 'data', 'notes']`
- notes: `["認購(售)權證標的物若當日遇除權息時，該權證行使比例之異動資料尚未更新，故次日參考價資料係以異動前之行使比例計算僅供參考, 請直接於次一營業日查詢本中心網站-->衍生性金融商品 -->認購(售)權證 -->權證資訊-->權證收盤行情下之參考價資料", "--- : 個股當日無交易時，以此符號表示開市價、最高價、最低價、收盤價、漲跌價", "ETF證券代號第六碼為K、C者，表示該ETF以外幣交易，其一交易單位(張)應為100受益權或其整倍數，請詳其公開說明書。"]`
- rows: 0
- fields: `["代號", "名稱", "收盤", "漲跌", "開盤", "最高", "最低", "均價", "成交股數", "成交金額(元)", "成交筆數", "最後買價", "最後買量(張數)", "最後賣價", "最後賣量(張數)", "發行股數", "次日 參考價", "次日 漲停價", "次日 跌停價"]`
- non-list values: `{"date": "20261008", "flagField": "張數", "stat": "ok"}`

