# מנתח דוחות כספיים

כלי לאנליסטים ישראלים: מקלידים סימול של חברה אמריקאית, ומקבלים בעברית עמוד סיכום של הדוח הרבעוני או השנתי (10-Q / 10-K) עם יחסים, מה השתנה, תמחור, חדשות ומקור לכל מספר.

פרויקט גמר, קורס מפתחי AI, האוניברסיטה העברית.

## מבנה

- האפיון המלא: `spec.md`
- שרת (Python + Flask): `backend/`
- ממשק (HTML + CSS): `frontend/`
- מסכי העיצוב מ-Stitch: `design/stitch/`
- בדיקות: `tests/`

## הפעלה

העתיקו את `.env.example` ל-`.env` ומלאו מפתחות.

הפעלת השרת המקומי:

1. התקינו תלויות: `pip install -r requirements.txt`
2. ב-`.env` הגדירו `SEC_EDGAR_USER_AGENT` עם שם וכתובת אימייל (SEC דורש זאת).
3. הריצו מתיקיית הפרויקט: `python -m backend.app`
4. פתחו בדפדפן את http://127.0.0.1:5000 והקלידו סימול, למשל NVDA.

תשובות SEC נשמרות מקומית ב-`.cache/sec` (אפשר לשנות עם `SEC_CACHE_DIR`), כך שפתיחה חוזרת של אותה חברה מהירה.

## בדיקות

1. כל הבדיקות הרגילות, בלי רשת: `python -m pytest -q`
2. בדיקות דפדפן אמיתי דורשות `pip install -r requirements-dev.txt` והתקנה של Chromium, ובלעדיהן הן מדלגות.
3. בדיקות חיות מול SEC, דורשות `SEC_EDGAR_USER_AGENT`: `python -m pytest -q -m live`
4. צילומי מסך של הדפים אל `docs/screenshots/`: `python scripts/screenshots.py`
