# תוכנית: שלב 13, משימות 1 ו-2 (משיכה מ-EDGAR ויחסים)
תכנון: Opus 5.5. ביצוע: Sonnet 5.5. ענף: feature/edgar-ratios.

## מטרה
מסימול מניה אמריקאית: למצוא את הדוח התקופתי האחרון (10-Q או 10-K) ואת הקודם לו, לשלוף מספרים מ-XBRL company facts, ולחשב בפייתון את היחסים מהאפיון. לכל יחס: ערך, נוסחה ומקור (תגית XBRL, דוח, תקופה, קישור). נתון חסר = "לא דווח", אף פעם לא ניחוש.

## מקורות (נבדקו חי ב-26.10.2026)
1. סימול ל-CIK: https://www.sec.gov/files/company_tickers.json
2. רשימת דוחות: https://data.sec.gov/submissions/CIK##########.json (שדות filings.recent: form, filingDate, reportDate, accessionNumber, primaryDocument)
3. מספרים: https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json (facts.us-gaap.<tag>.units.<unit>[] עם start, end, val, accn, form, filed)
4. קישור לדוח: https://www.sec.gov/Archives/edgar/data/{cik}/{accn בלי מקפים}/{primaryDocument}
5. כל בקשה: User-Agent מ-SEC_EDGAR_USER_AGENT (שם ומייל), עד 10 בקשות בשנייה.

## בחירת עובדות לדוח
1. דוח = 10-Q או 10-K (לא תיקונים /A בגרסה הזו). האחרון והקודם לפי filingDate.
2. עובדה שייכת לדוח אם accn שלה זהה למספר הדוח.
3. סעיף תוצאות (הכנסות, רווח): end = reportDate, ואורך תקופה 80-100 יום ל-10-Q או 350-380 יום ל-10-K (כדי לא לקחת נתון מצטבר של חצי שנה).
4. סעיף מאזן: end = reportDate, בלי start.
5. השוואה לשנה קודמת: עובדה באותו דוח, אותו אורך תקופה, end בטווח 350-380 יום לפני reportDate.

## תגיות, לפי סדר עדיפות
1. הכנסות: Revenues, RevenueFromContractWithCustomerExcludingAssessedTax, RevenueFromContractWithCustomerIncludingAssessedTax, SalesRevenueNet, RevenuesNetOfInterestExpense (בנקים).
2. רווח גולמי: GrossProfit. אם חסר: הכנסות פחות CostOfRevenue או CostOfGoodsAndServicesSold (שתי התגיות מופיעות במקור).
3. רווח תפעולי: OperatingIncomeLoss.
4. רווח נקי: NetIncomeLoss, ואז ProfitLoss.
5. הון עצמי: StockholdersEquity, ואז StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest.
6. נכסים: Assets. נכסים שוטפים: AssetsCurrent. התחייבויות שוטפות: LiabilitiesCurrent.
7. חוב: (א) LongTermDebt ועוד ShortTermBorrowings אם דווח. (ב) LongTermDebtNoncurrent ועוד LongTermDebtCurrent או DebtCurrent, ועוד ShortTermBorrowings אם דווח. אם אין אף אחד: לא דווח.

## יחסים
1. צמיחת הכנסות לעומת שנה קודמת (YoY), ולעומת הדוח הקודם (רק אם שני הדוחות באותו אורך תקופה).
2. שיעור רווח גולמי, תפעולי ונקי = רווח חלקי הכנסות.
3. תשואה להון = רווח נקי של התקופה חלקי הון בסוף התקופה (לא מחושב לשנה; אורך התקופה מוחזר).
4. יחס שוטף = נכסים שוטפים חלקי התחייבויות שוטפות.
5. חוב להון = חוב חלקי הון. הון למאזן = הון חלקי נכסים.
6. מכנה אפס או הון שלילי: "לא ניתן לחישוב" עם סיבה, לא מספר.

## מבנה קוד
1. backend/config.py: טעינת .env, בדיקה שיש User-Agent עם מייל.
2. backend/sec_client.py: לקוח HTTP עם הגבלת קצב, timeout, ניסיון חוזר ל-429/5xx, ולידציה לסימול, הצעת סימולים קרובים.
3. backend/filings.py: מציאת הדוח האחרון והקודם, שליפת עובדות לדוח.
4. backend/ratios.py: חישוב יחסים טהור (בלי רשת), כל תוצאה עם מקור.
5. backend/app.py: Flask, נתיבים /api/health, /api/filings/<ticker>, /api/ratios/<ticker>. שגיאות JSON בעברית.
6. tests/: בדיקות יחידה על קובץ נתונים מקוצר של NVDA, מקרי קצה, בדיקות Flask, ובדיקה חיה (live) מול הדוח של NVDA.

## בדיקת מחירים היסטוריים
1. Finnhub stock/candle: לבדוק אם זמין בתוכנית החינמית (תיעוד ובקשה חיה כשיש מפתח).
2. חלופות חינמיות בלי מפתח, לגיבוי. ממצאים: docs/price-history-check.md.
