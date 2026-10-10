# TW endpoint probe report (GitHub Actions runner)

Probe run 2026-10-10 (Sat). Last trading day 2026-10-08; 2026-10-09 holiday. Total HTTP requests: 18. Blocked hosts: `{}`

Raw bodies: `probe/raw/<ID>.<kind>.gz`. Script: `probe/tw_probe.py`.

## G01

- URL: `https://www.twse.com.tw/rwd/zh/fund/T86?date=20261008&selectType=ALLBUT0999&response=json`
- HTTP 200; content-type `application/json;charset=UTF-8`; size 188581 bytes; body kind: json
- top-level keys: `['stat', 'date', 'title', 'hints', 'fields', 'data', 'selectType', 'notes', 'total']`
- stat: `"OK"`
- date: `"20261008"`
- title: `"115年10月08日 三大法人買賣超日報"`

### data
- rows: 1325
- fields: `["證券代號", "證券名稱", "外陸資買進股數(不含外資自營商)", "外陸資賣出股數(不含外資自營商)", "外陸資買賣超股數(不含外資自營商)", "外資自營商買進股數", "外資自營商賣出股數", "外資自營商買賣超股數", "投信買進股數", "投信賣出股數", "投信買賣超股數", "自營商買賣超股數", "自營商買進股數(自行買賣)", "自營商賣出股數(自行買賣)", "自營商買賣超股數(自行買賣)", "自營商買進股數(避險)", "自營商賣出股數(避險)", "自營商買賣超股數(避險)", "三大法人買賣超股數"]`
- 1325 rows; 4-digit numeric codes: 1076; codes starting '00' (ETF-ish): 237; other (warrants/ETNs/etc, non-4-digit): 249
- row[0]: `["00632R", "元大台灣50反1   ", "603,000", "719,000", "-116,000", "0", "0", "0", "0", "0", "0", "44,564,373", "0", "2,000", "-2,000", "46,491,795", "1,925,422", "44,566,373", "44,448,373"]`
- row[1]: `["00403A", "主動統一升級50  ", "53,408,427", "23,319,510", "30,088,917", "0", "0", "0", "0", "0", "0", "9,081,338", "279,000", "0", "279,000", "24,484,583", "15,682,245", "8,802,338", "39,170,255"]`
- row for 2330: `["2330", "台積電          ", "7,131,686", "19,434,673", "-12,302,987", "0", "0", "0", "829,300", "36,437", "792,863", "509,848", "248,600", "7,500", "241,100", "334,747", "65,999", "268,748", "-11,000,276"]`
- non-list values: `{"stat": "OK", "date": "20261008", "title": "115年10月08日 三大法人買賣超日報", "hints": "單位：股", "selectType": "ALLBUT0999", "total": 1325}`

## G02

- URL: `https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date=20261008&selectType=ALL&response=json`
- HTTP 200; content-type `application/json;charset=UTF-8`; size 128929 bytes; body kind: json
- top-level keys: `['stat', 'date', 'tables']`
- stat: `"OK"`
- date: `"20261008"`

### G02 tables[0] — '115年10月08日 信用交易統計'
- table keys: `['title', 'fields', 'data']`
- rows: 3
- fields: `["項目", "買進", "賣出", "現金(券)償還", "前日餘額", "今日餘額"]`
- 3 rows; 4-digit numeric codes: 0; codes starting '00' (ETF-ish): 0; other (warrants/ETNs/etc, non-4-digit): 3
- row[0]: `["融資(交易單位)", "385,423", "305,441", "31,506", "9,358,244", "9,406,720"]`
- row[1]: `["融券(交易單位)", "17,203", "16,677", "2,629", "215,104", "211,949"]`
- row for 2330: not present

### G02 tables[1] — '115年10月08日 融資融券彙總 (全部)'
- table keys: `['title', 'fields', 'data', 'notes', 'groups']`
- notes: `["備註欄係表明成交日次一營業日股票融資融券狀況", "符號說明： O：停止融資, X：停止融券, @：融資分配, %：融券分配, !：停止買賣 ", "限額：<br>　融資餘額達該種股票上市股份25%時，暫停融資買進。融券餘額達該種股票上市股份25%時，暫停融券賣出。", "融資、融券餘額達限額之八成時，次一營業日進行額度分配。", "當日如有臨時新增之變更交易有價證券標的，網頁右上角會呈現點選框，屆時請點選明細查詢，另外，歷史資料也可點選此 <a href='/zh/trading/bfihbu.html' target='_blank'>連結</a> 查詢。當日臨時新增之變更交易有價證券，若該證券原得為融資融券交易，將被暫停融資融券。"]`
- groups: `[{"title": "股票", "span": 2}, {"title": "融資", "span": 6}, {"title": "融券", "span": 6}, {"title": "", "span": 1}, {"title": "", "span": 1}]`
- rows: 1298
- fields: `["代號", "名稱", "買進", "賣出", "現金償還", "前日餘額", "今日餘額", "次一營業日限額", "買進", "賣出", "現券償還", "前日餘額", "今日餘額", "次一營業日限額", "資券互抵", "註記"]`
- 1298 rows; 4-digit numeric codes: 1067; codes starting '00' (ETF-ish): 235; other (warrants/ETNs/etc, non-4-digit): 231
- row[0]: `["00400A", "主動國泰動能高息", "512", "563", "0", "12,434", "12,383", "474,285", "0", "2", "0", "0", "2", "474,285", "12", " "]`
- row[1]: `["00401A", "主動摩根台灣鑫收", "108", "35", "1", "1,848", "1,920", "64,848", "2", "0", "0", "2", "0", "64,848", "0", "X "]`
- row for 2330: `["2330", "台積電", "1,699", "361", "30", "30,278", "31,586", "6,483,092", "13", "8", "0", "50", "45", "6,483,092", "4", " "]`
- non-list values: `{"stat": "OK", "date": "20261008"}`

## G03

- URL: `https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d?date=20261008&selectType=ALL&response=json`
- HTTP 200; content-type `application/json;charset=UTF-8`; size 66387 bytes; body kind: json
- top-level keys: `['stat', 'date', 'title', 'fields', 'data', 'selectType', 'total']`
- stat: `"OK"`
- date: `"20261008"`
- title: `"115年10月08日 個股日本益比、殖利率及股價淨值比"`

### data
- rows: 1082
- fields: `["證券代號", "證券名稱", "收盤價", "殖利率(%)", "股利年度", "本益比", "股價淨值比", "財報年/季"]`
- 1082 rows; 4-digit numeric codes: 1082; codes starting '00' (ETF-ish): 0; other (warrants/ETNs/etc, non-4-digit): 0
- row[0]: `["1101", "台泥", "25.70", "3.11", 114, "-", "0.83", "115/2"]`
- row[1]: `["1102", "亞泥", "36.35", "6.33", 114, "10.04", "0.70", "115/2"]`
- row for 2330: `["2330", "台積電", "2,550.00", "0.86", 114, "29.56", "10.28", "115/2"]`
- non-list values: `{"stat": "OK", "date": "20261008", "title": "115年10月08日 個股日本益比、殖利率及股價淨值比", "selectType": "ALL", "total": 1082}`

## G04

- URL: `https://www.twse.com.tw/rwd/zh/fund/BFI82U?type=day&dayDate=20261008&response=json`
- HTTP 200; content-type `application/json;charset=UTF-8`; size 1569 bytes; body kind: json
- top-level keys: `['stat', 'date', 'title', 'fields', 'data', 'params', 'notes', 'hints']`
- stat: `"OK"`
- date: `"20261008"`
- title: `"115年10月08日 三大法人買賣金額統計表"`

### data
- rows: 6
- fields: `["單位名稱", "買進金額", "賣出金額", "買賣差額"]`
- 6 rows; 4-digit numeric codes: 0; codes starting '00' (ETF-ish): 0; other (warrants/ETNs/etc, non-4-digit): 6
- row[0]: `["自營商(自行買賣)", "6,972,303,793", "9,104,089,748", "-2,131,785,955"]`
- row[1]: `["自營商(避險)", "23,310,089,237", "39,909,564,886", "-16,599,475,649"]`
- row for 2330: not present
- non-list values: `{"stat": "OK", "date": "20261008", "title": "115年10月08日 三大法人買賣金額統計表", "params": {"type": "day", "dayDate": "20261008", "response": "json", "controller": "fund", "action": "BFI82U", "lang": "zh", "monthDate": null, "weekDate": null}, "hints": "單位：元"}`

## G05

- URL: `https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date=20261009&type=ALLBUT0999&response=json`
- HTTP 200; content-type `application/json;charset=UTF-8`; size 71 bytes; body kind: json
- top-level keys: `['stat', 'type']`
- stat: `"很抱歉，沒有符合條件的資料!"`
- non-list values: `{"stat": "很抱歉，沒有符合條件的資料!", "type": "ALLBUT0999"}`

## G06

- URL: `https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?date=20261001&response=json`
- HTTP 200; content-type `application/json;charset=UTF-8`; size 1038 bytes; body kind: json
- top-level keys: `['stat', 'date', 'title', 'hints', 'fields', 'data', 'notes']`
- stat: `"OK"`
- date: `"20261001"`
- title: `"115年10月市場成交資訊"`

### data
- rows: 6
- fields: `["日期", "成交股數", "成交金額", "成交筆數", "發行量加權股價指數", "漲跌點數"]`
- 6 rows; 4-digit numeric codes: 0; codes starting '00' (ETF-ish): 0; other (warrants/ETNs/etc, non-4-digit): 6
- row[0]: `["115/10/01", "10,691,867,330", "874,046,252,416", "4,715,278", "48,353.49", "413.36"]`
- row[1]: `["115/10/02", "11,017,717,304", "938,222,196,327", "4,612,433", "48,475.74", "122.25"]`
- row for 2330: not present
- non-list values: `{"stat": "OK", "date": "20261001", "title": "115年10月市場成交資訊", "hints": "單位：元、股"}`

## G07

- URL: `https://www.twse.com.tw/rwd/zh/fund/T86?date=20261009&selectType=ALLBUT0999&response=json`
- HTTP 200; content-type `application/json;charset=UTF-8`; size 61 bytes; body kind: json
- top-level keys: `['stat', 'total']`
- stat: `"很抱歉，沒有符合條件的資料!"`
- non-list values: `{"stat": "很抱歉，沒有符合條件的資料!", "total": 0}`

## G08

- URL: `https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date=20261008&type=ALLBUT0999&response=json`
- HTTP 200; content-type `application/json;charset=UTF-8`; size 247290 bytes; body kind: json
- top-level keys: `['tables', 'type', 'params', 'stat', 'date']`
- stat: `"OK"`
- date: `"20261008"`

### G08 tables[0] — '115年10月08日 價格指數(臺灣證券交易所)'
- table keys: `['title', 'fields', 'data', 'hints']`
- rows: 56
- fields: `["指數", "收盤指數", "漲跌(+/-)", "漲跌點數", "漲跌百分比(%)", "特殊處理註記"]`
- 56 rows; 4-digit numeric codes: 0; codes starting '00' (ETF-ish): 0; other (warrants/ETNs/etc, non-4-digit): 56
- row[0]: `["寶島股價指數", "54,683.46", "<p style ='color:green'>-</p>", "542.01", "-0.98", ""]`
- row[1]: `["發行量加權股價指數", "49,313.44", "<p style ='color:green'>-</p>", "492.93", "-0.99", ""]`
- row for 2330: not present

### G08 tables[1] — '價格指數(跨市場)'
- table keys: `['title', 'fields', 'data']`
- rows: 46
- fields: `["指數", "收盤指數", "漲跌(+/-)", "漲跌點數", "漲跌百分比(%)", "特殊處理註記"]`
- 46 rows; 4-digit numeric codes: 0; codes starting '00' (ETF-ish): 0; other (warrants/ETNs/etc, non-4-digit): 46
- row[0]: `["臺灣生技指數", "4,154.07", "<p style ='color:red'>+</p>", "10.49", "0.25", ""]`
- row[1]: `["臺灣中小型公司治理指數", "19,483.50", "<p style ='color:green'>-</p>", "36.71", "-0.19", ""]`
- row for 2330: not present

### G08 tables[2] — '價格指數(臺灣指數公司)'
- table keys: `['title', 'fields', 'data']`
- rows: 35
- fields: `["指數", "收盤指數", "漲跌(+/-)", "漲跌點數", "漲跌百分比(%)", "特殊處理註記"]`
- 35 rows; 4-digit numeric codes: 0; codes starting '00' (ETF-ish): 0; other (warrants/ETNs/etc, non-4-digit): 35
- row[0]: `["金融類日報酬兩倍指數", "124,506.69", "<p style ='color:green'>-</p>", "4,341.46", "-3.37", ""]`
- row[1]: `["金融類日報酬反向一倍指數", "1,949.64", "<p style ='color:red'>+</p>", "32.30", "1.68", ""]`
- row for 2330: not present

### G08 tables[3] — '報酬指數(臺灣證券交易所)'
- table keys: `['title', 'fields', 'data']`
- rows: 47
- fields: `["報酬指數", "收盤指數", "漲跌(+/-)", "漲跌點數", "漲跌百分比(%)", "特殊處理註記"]`
- 47 rows; 4-digit numeric codes: 0; codes starting '00' (ETF-ish): 0; other (warrants/ETNs/etc, non-4-digit): 47
- row[0]: `["寶島股價報酬指數", "85,073.41", "<p style ='color:green'>-</p>", "843.22", "-0.98", ""]`
- row[1]: `["發行量加權股價報酬指數", "114,031.08", "<p style ='color:green'>-</p>", "1,139.84", "-0.99", ""]`
- row for 2330: not present

### G08 tables[4] — '報酬指數(跨市場)'
- table keys: `['title', 'fields', 'data']`
- rows: 47
- fields: `["報酬指數", "收盤指數", "漲跌(+/-)", "漲跌點數", "漲跌百分比(%)", "特殊處理註記"]`
- 47 rows; 4-digit numeric codes: 0; codes starting '00' (ETF-ish): 0; other (warrants/ETNs/etc, non-4-digit): 47
- row[0]: `["臺灣生技報酬指數", "4,885.11", "<p style ='color:red'>+</p>", "12.34", "0.25", ""]`
- row[1]: `["臺灣中小型公司治理報酬指數", "29,152.46", "<p style ='color:green'>-</p>", "54.94", "-0.19", ""]`
- row for 2330: not present

### G08 tables[5] — '報酬指數(臺灣指數公司)'
- table keys: `['title', 'fields', 'data']`
- rows: 34
- fields: `["報酬指數", "收盤指數", "漲跌(+/-)", "漲跌點數", "漲跌百分比(%)", "特殊處理註記"]`
- 34 rows; 4-digit numeric codes: 0; codes starting '00' (ETF-ish): 0; other (warrants/ETNs/etc, non-4-digit): 34
- row[0]: `["漲升股利150報酬指數", "36,708.78", "<p style ='color:green'>-</p>", "213.20", "-0.58", ""]`
- row[1]: `["藍籌30報酬指數", "42,299.19", "<p style ='color:green'>-</p>", "548.18", "-1.28", ""]`
- row for 2330: not present

### G08 tables[6] — '115年10月08日 大盤統計資訊'
- table keys: `['title', 'fields', 'data', 'hints']`
- rows: 17
- fields: `["成交統計", "成交金額(元)", "成交股數(股)", "成交筆數"]`
- 17 rows; 4-digit numeric codes: 0; codes starting '00' (ETF-ish): 0; other (warrants/ETNs/etc, non-4-digit): 17
- row[0]: `["1.一般股票", "854,684,336,071", "4,336,915,500", "3,880,638"]`
- row[1]: `["2.台灣存託憑證", "208,616,424", "26,849,455", "6,423"]`
- row for 2330: not present

### G08 tables[7] — '漲跌證券數合計'
- table keys: `['title', 'fields', 'data', 'notes']`
- notes: `["\"漲跌價差\"為當日收盤價與前一日收盤價比較。", "\"無比價\"含前一日無收盤價、當日除權、除息、新上市、恢復交易者。", "外幣成交值係以本公司當日下午3時30分公告匯率換算後加入成交金額。公告匯率請參考本公司首頁>產品與服務>交易系統>雙幣ETF專區>代號對應及每日公告匯率。"]`
- rows: 5
- fields: `["類型", "整體市場", "股票"]`
- 5 rows; 4-digit numeric codes: 0; codes starting '00' (ETF-ish): 0; other (warrants/ETNs/etc, non-4-digit): 5
- row[0]: `["上漲(漲停)", "5,285(78)", "425(14)"]`
- row[1]: `["下跌(跌停)", "9,634(83)", "540(2)"]`
- row for 2330: not present

### G08 tables[8] — '115年10月08日 每日收盤行情(全部(不含權證、牛熊證、可展延牛熊證))'
- table keys: `['title', 'fields', 'data', 'hints', 'groups', 'notes']`
- notes: `["漲跌(+/-)欄位符號說明:+/-/X表示漲/跌/不比價。", "當證券代號為認購(售)權證及認股權憑證時本益比欄位置為結算價；但如為以外國證券或指數為標的之認購(售)權證及履約方式採歐式者，該欄位為空白。", "除境外指數股票型基金及外國股票第二上市外，餘交易單位皆為千股。", "本統計資訊含一般、零股、盤後定價、鉅額交易，不含拍賣、標購。"]`
- groups: `[{"start": 0, "span": 11, "title": "(元,股)"}, {"start": 11, "span": 5, "title": "(元,交易單位)"}]`
- rows: 1380
- fields: `["證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額", "開盤價", "最高價", "最低價", "收盤價", "漲跌(+/-)", "漲跌價差", "最後揭示買價", "最後揭示買量", "最後揭示賣價", "最後揭示賣量", "本益比"]`
- 1380 rows; 4-digit numeric codes: 1094; codes starting '00' (ETF-ish): 240; other (warrants/ETNs/etc, non-4-digit): 286
- row[0]: `["00400A", "主動國泰動能高息", "28,722,889", "10,751", "462,794,154", "16.12", "16.22", "15.98", "16.15", "<p>X</p>", "0.00", "16.15", "818", "16.16", "1,169", "0.00"]`
- row[1]: `["00401A", "主動摩根台灣鑫收", "5,380,337", "1,615", "78,208,949", "14.57", "14.57", "14.47", "14.53", "<p style= color:green>-</p>", "0.09", "14.52", "274", "14.53", "255", "0.00"]`
- row for 2330: `["2330", "台積電", "23,145,193", "141,976", "59,167,379,273", "2,550.00", "2,575.00", "2,550.00", "2,550.00", "<p style= color:green>-</p>", "35.00", "2,550.00", "69", "2,555.00", "140", "29.56"]`

### G08 tables[9] — None
- table keys: `[]`
- rows: 0
- non-list values: `{"type": "ALLBUT0999", "params": {"date": "20261008", "type": "ALLBUT0999", "response": "json", "controller": "afterTrading", "action": "MI_INDEX", "lang": "zh"}, "stat": "OK", "date": "20261008"}`

## G09

- URL: `https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL`
- HTTP 200; content-type `application/json`; size 319289 bytes; body kind: json
- list length: 1380
- keys of item[0]: `["Date", "Code", "Name", "TradeVolume", "TradeValue", "OpeningPrice", "HighestPrice", "LowestPrice", "ClosingPrice", "Change", "Transaction"]`
- rows: 1380
- 1380 rows; 4-digit numeric codes: 1094; codes starting '00' (ETF-ish): 240; other (warrants/ETNs/etc, non-4-digit): 286
- row[0]: `{"Date": "1151008", "Code": "00400A", "Name": "主動國泰動能高息", "TradeVolume": "28722889", "TradeValue": "462794154", "OpeningPrice": "16.12", "HighestPrice": "16.22", "LowestPrice": "15.98", "ClosingPrice": "16.15", "Change": "0.0000", "Transaction": "10751"}`
- row[1]: `{"Date": "1151008", "Code": "00401A", "Name": "主動摩根台灣鑫收", "TradeVolume": "5380337", "TradeValue": "78208949", "OpeningPrice": "14.57", "HighestPrice": "14.57", "LowestPrice": "14.47", "ClosingPrice": "14.53", "Change": "-0.0900", "Transaction": "1615"}`
- row for 2330: `{"Date": "1151008", "Code": "2330", "Name": "台積電", "TradeVolume": "23145193", "TradeValue": "59167379273", "OpeningPrice": "2550.00", "HighestPrice": "2575.00", "LowestPrice": "2550.00", "ClosingPrice": "2550.00", "Change": "-35.0000", "Transaction": "141976"}`

## G10

- URL: `https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL`
- HTTP 200; content-type `application/json`; size 116588 bytes; body kind: json
- list length: 1082
- keys of item[0]: `["Date", "Code", "Name", "PEratio", "DividendYield", "PBratio"]`
- rows: 1082
- 1082 rows; 4-digit numeric codes: 1082; codes starting '00' (ETF-ish): 0; other (warrants/ETNs/etc, non-4-digit): 0
- row[0]: `{"Date": "1151008", "Code": "1101", "Name": "台泥", "PEratio": "", "DividendYield": "3.11", "PBratio": "0.83"}`
- row[1]: `{"Date": "1151008", "Code": "1102", "Name": "亞泥", "PEratio": "10.04", "DividendYield": "6.33", "PBratio": "0.70"}`
- row for 2330: `{"Date": "1151008", "Code": "2330", "Name": "台積電", "PEratio": "29.56", "DividendYield": "0.86", "PBratio": "10.28"}`

## G11

- URL: `https://openapi.twse.com.tw/v1/opendata/t187ap03_L`
- HTTP 200; content-type `application/json`; size 1327578 bytes; body kind: json
- list length: 1095
- keys of item[0]: `["出表日期", "公司代號", "公司名稱", "公司簡稱", "外國企業註冊地國", "產業別", "住址", "營利事業統一編號", "董事長", "總經理", "發言人", "發言人職稱", "代理發言人", "總機電話", "成立日期", "上市日期", "普通股每股面額", "實收資本額", "私募股數", "特別股", "編制財務報表類型", "股票過戶機構", "過戶電話", "過戶地址", "簽證會計師事務所", "簽證會計師1", "簽證會計師2", "英文簡稱", "英文通訊地址", "傳真機號碼", "電子郵件信箱", "網址", "已發行普通股數或TDR原股發行股數"]`
- rows: 1095
- 1095 rows; 4-digit numeric codes: 1089; codes starting '00' (ETF-ish): 0; other (warrants/ETNs/etc, non-4-digit): 6
- row[0]: `{"出表日期": "1151009", "公司代號": "1101", "公司名稱": "臺灣水泥股份有限公司", "公司簡稱": "台泥", "外國企業註冊地國": "－ ", "產業別": "01", "住址": "台北市中山北路2段113號", "營利事業統一編號": "11913502", "董事長": "張安平", "總經理": "程耀輝", "發言人": "葉毓君", "發言人職稱": "永續長", "代理發言人": "賴家柔", "總機電話": "(02)2531-7099", "成立日期": "19501229", "上市日期": "19620209", "普通股每股面額": "新台幣                 10.0000元", "實收資本額": "77231817420", "私募股數": "0", "特別股": "200000000", "編制財務報表類型": "1", "股票過戶機構": "中國信託商業銀行代理部", "過戶電話": "66365566", "過戶地址": "台北市重慶南路一段83號5樓", "簽證會計師事務所": "勤業眾信聯合會計師事務所", "簽證會計師1": "翁雅玲", "簽證會計師2": "邵志明", "英文簡稱": "TCC", "英文通訊地址": "No.113, Sec.2, Zhongshan N. Rd.,Taipei City 104,Taiwan (R.O.C.)", "傳真機號碼": "(02)2531-6529", "電子郵件信箱": "finance@taiwancement.com", "網址": "https://www.tccgroupholdings.com/tw/", "已發行普通股數或TDR原股發行股數": "7523181742"}`
- row[1]: `{"出表日期": "1151009", "公司代號": "1102", "公司名稱": "亞洲水泥股份有限公司", "公司簡稱": "亞泥", "外國企業註冊地國": "－ ", "產業別": "01", "住址": "台北市大安區敦化南路2段207號30、31樓", "營利事業統一編號": "03244509", "董事長": "徐旭東", "總經理": "李坤炎", "發言人": "王照宇", "發言人職稱": "協理", "代理發言人": "楊淯玲", "總機電話": "02-2733-8000", "成立日期": "19570321", "上市日期": "19620608", "普通股每股面額": "新台幣                 10.0000元", "實收資本額": "35465628810", "私募股數": "0", "特別股": "0", "編制財務報表類型": "1", "股票過戶機構": "亞東證券股份有限公司", "過戶電話": "02-7753-1699", "過戶地址": "新北市板橋區新站路16號13樓", "簽證會計師事務所": "勤業眾信聯合會計師事務所", "簽證會計師1": "劉紋伶", "簽證會計師2": "陳培德", "英文簡稱": "ACC", "英文通訊地址": "30-31F., No.207, Sec. 2, Dunhua S. Rd., Da' an Dist., Taipei City 106, TaiwanTAIPEI,TAIWAN,R.O.C", "傳真機號碼": "02-2736-6263", "電子郵件信箱": "service@acc.com.tw", "網址": "www.acc.com.tw", "已發行普通股數或TDR原股發行股數": "3546562881"}`
- row for 2330: `{"出表日期": "1151009", "公司代號": "2330", "公司名稱": "台灣積體電路製造股份有限公司", "公司簡稱": "台積電", "外國企業註冊地國": "－ ", "產業別": "24", "住址": "新竹科學園區力行六路8號", "營利事業統一編號": "22099131", "董事長": "魏哲家", "總經理": "總裁: 魏哲家", "發言人": "黃仁昭", "發言人職稱": "資深副總經理暨財務長", "代理發言人": "高孟華", "總機電話": "03-5636688", "成立日期": "19870221", "上市日期": "19940905", "普通股每股面額": "新台幣                 10.0000元", "實收資本額": "259323700670", "私募股數": "0", "特別股": "0", "編制財務報表類型": "1", "股票過戶機構": "中國信託商業銀行 代理部", "過戶電話": "02-6636-5566", "過戶地址": "台北市重慶南路一段83號5樓", "簽證會計師事務所": "勤業眾信聯合會計師事務所", "簽證會計師1": "吳世宗", "簽證會計師2": "陳彥君", "英文簡稱": "TSMC", "英文通訊地址": "No. 8, Li-Hsin Rd. 6, Hsinchu Science Park,Hsin-Chu 300096, Taiwan, R.O.C.", "傳真機號碼": "03-5797337", "電子郵件信箱": "invest@tsmc.com", "網址": "https://www.tsmc.com", "已發行普通股數或TDR原股發行股數": "25932370067"}`

## G12

- URL: `https://isin.twse.com.tw/isin/C_public.jsp?strMode=2`
- HTTP 200; content-type `text/html;charset=MS950`; size 9027968 bytes; body kind: html
- body head: `'\n\n\n\n\n\n\n      <link rel="stylesheet" href="http://isin.twse.com.tw/isin/style1.css" type="text/css">\n      <body><table  align=center><h2><strong><font class=\'h1\'>����W���Ҩ����Ҩ���Ѹ��X�@����</font></strong></h2><h2><strong><font class=\'h1\'><center>�̪��s���:2026/10/10  </center> </font></strong></h2><h2><font color=\'red\'><center>���P��H�������i����</center></font></h2></table><TABLE class=\'h4\' align'`

## G13

- URL: `https://isin.twse.com.tw/isin/C_public.jsp?strMode=4`
- HTTP 200; content-type `text/html;charset=MS950`; size 3018993 bytes; body kind: html
- body head: `'\n\n\n\n\n\n\n      <link rel="stylesheet" href="http://isin.twse.com.tw/isin/style1.css" type="text/css">\n      <body><table  align=center><h2><strong><font class=\'h1\'>����W�d�Ҩ����Ҩ���Ѹ��X�@����</font></strong></h2><h2><strong><font class=\'h1\'><center>�̪��s���:2026/10/10  </center> </font></strong></h2><h2><font color=\'red\'><center>���P��H�������i����</center></font></h2></table><TABLE class=\'h4\' align'`

## G14

- URL: `https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade?type=Daily&sect=EW&date=2026%2F10%2F09&response=json`
- HTTP 200; content-type `application/json;charset=UTF-8`; size 641 bytes; body kind: json
- top-level keys: `['columnNum', 'tables', 'template', 'date', 'stat']`
- stat: `"ok"`
- date: `"20261009"`

### G14 tables[0] — '三大法人買賣明細資訊'
- table keys: `['title', 'date', 'fields', 'data', 'notes', 'totalCount', 'summary', 'columnNum', 'subtitle']`
- date: `"115/10/09"`
- rows: 0
- fields: `["代號", "名稱", "買進股數", "賣出股數", "買賣超股數", "買進股數", "賣出股數", "買賣超股數", "買進股數", "賣出股數", "買賣超股數", "買進股數", "賣出股數", "買賣超股數", "買進股數", "賣出股數", "買賣超股數", "買進股數", "賣出股數", "買賣超股數", "買進股數", "賣出股數", "買賣超股數", "三大法人買賣超股數合計"]`

### G14 tables[1] — None
- table keys: `[]`
- rows: 0
- non-list values: `{"columnNum": 25, "template": "/template/insti/dailyTrade", "date": "20261009", "stat": "ok"}`

## G15

- URL: `https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date=2026%2F10%2F09&id=&response=json`
- HTTP 200; content-type `application/json;charset=UTF-8`; size 1630 bytes; body kind: json
- top-level keys: `['date', 'tables', 'flagField', 'stat']`
- stat: `"ok"`
- date: `"20261009"`

### G15 tables[0] — '上櫃股票行情'
- table keys: `['title', 'subtitle', 'date', 'listedCompanies', 'totalTradingAmount', 'totalTradingShares', 'totalTranscations', 'totalCount', 'fields', 'data', 'notes']`
- date: `"115/10/09"`
- rows: 0
- fields: `["代號", "名稱", "收盤", "漲跌", "開盤", "最高", "最低", "均價", "成交股數", "成交金額(元)", "成交筆數", "最後買價", "最後買量(張數)", "最後賣價", "最後賣量(張數)", "發行股數", "次日 參考價", "次日 漲停價", "次日 跌停價"]`

### G15 tables[1] — '管理股票'
- table keys: `['title', 'subtitle', 'totalCount', 'fields', 'data', 'notes']`
- notes: `["認購(售)權證標的物若當日遇除權息時，該權證行使比例之異動資料尚未更新，故次日參考價資料係以異動前之行使比例計算僅供參考, 請直接於次一營業日查詢本中心網站-->衍生性金融商品 -->認購(售)權證 -->權證資訊-->權證收盤行情下之參考價資料", "--- : 個股當日無交易時，以此符號表示開市價、最高價、最低價、收盤價、漲跌價", "ETF證券代號第六碼為K、C者，表示該ETF以外幣交易，其一交易單位(張)應為100受益權或其整倍數，請詳其公開說明書。"]`
- rows: 0
- fields: `["代號", "名稱", "收盤", "漲跌", "開盤", "最高", "最低", "均價", "成交股數", "成交金額(元)", "成交筆數", "最後買價", "最後買量(張數)", "最後賣價", "最後賣量(張數)", "發行股數", "次日 參考價", "次日 漲停價", "次日 跌停價"]`
- non-list values: `{"date": "20261009", "flagField": "張數", "stat": "ok"}`

## G16

- URL: `https://www.tpex.org.tw/www/zh-tw/margin/balance?date=2026%2F10%2F09&id=&response=json`
- HTTP 200; content-type `application/json;charset=UTF-8`; size 419 bytes; body kind: json
- top-level keys: `['date', 'tables', 'stat']`
- stat: `"ok"`
- date: `"20261009"`

### G16 tables[0] — '上櫃股票融資融券餘額'
- table keys: `['title', 'date', 'totalCount', 'fields', 'data', 'summary', 'notes']`
- date: `"115/10/09"`
- rows: 0
- fields: `["代號", "名稱", "前資餘額(張)", "資買", "資賣", "現償", "資餘額", "資屬證金", "資使用率(%)", "資限額", "前券餘額(張)", "券賣", "券買", "券償", "券餘額", "券屬證金", "券使用率(%)", "券限額", "資券相抵(張)", "備註"]`
- non-list values: `{"date": "20261009", "stat": "ok"}`

## G17

- URL: `https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date=20261009&selectType=ALL&response=json`
- HTTP 200; content-type `application/json;charset=UTF-8`; size 50 bytes; body kind: json
- top-level keys: `['stat']`
- stat: `"很抱歉，沒有符合條件的資料"`
- non-list values: `{"stat": "很抱歉，沒有符合條件的資料"}`

## G18

- URL: `https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d?date=20260601&selectType=ALL&response=json`
- HTTP 200; content-type `application/json;charset=UTF-8`; size 66038 bytes; body kind: json
- top-level keys: `['stat', 'date', 'title', 'fields', 'data', 'selectType', 'total']`
- stat: `"OK"`
- date: `"20260601"`
- title: `"115年06月01日 個股日本益比、殖利率及股價淨值比"`

### data
- rows: 1078
- fields: `["證券代號", "證券名稱", "收盤價", "殖利率(%)", "股利年度", "本益比", "股價淨值比", "財報年/季"]`
- 1078 rows; 4-digit numeric codes: 1078; codes starting '00' (ETF-ish): 0; other (warrants/ETNs/etc, non-4-digit): 0
- row[0]: `["1101", "台泥", "24.55", "3.26", 114, "-", "0.78", "115/1"]`
- row[1]: `["1102", "亞泥", "33.80", "6.80", 114, "11.34", "0.68", "115/1"]`
- row for 2330: `["2330", "台積電", "2,355.00", "0.93", 114, "31.66", "10.37", "115/1"]`
- non-list values: `{"stat": "OK", "date": "20260601", "title": "115年06月01日 個股日本益比、殖利率及股價淨值比", "selectType": "ALL", "total": 1078}`

