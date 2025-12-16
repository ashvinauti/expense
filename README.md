# Ashvin Expense Tracker — Hosted

Mobile-friendly personal expense tracker with passcode login, budgets, subscriptions, dashboard, and CSV import/export.

## Deploy on Streamlit Community Cloud (easiest)
1. Create a new GitHub repo and add these files (from this ZIP).
2. Go to https://share.streamlit.io → "New app" → connect your repo and pick `app.py`.
3. In "Secrets", set:
   ```
   APP_PASSCODE = "your-strong-passcode"
   # Optional Postgres (recommended for multi-device sync)
   # DATABASE_URL = "postgresql+psycopg2://USER:PASSWORD@HOST:PORT/DBNAME"
   ```
4. Deploy. Open the URL on your iPhone and "Add to Home Screen" (Safari Share).

## Deploy on Render (free tier)
1. Push to GitHub.
2. Create a new Web Service on https://render.com
3. Environment: Python • Build command: `pip install -r requirements.txt`
4. Start command:
   ```
   streamlit run app.py --server.address 0.0.0.0 --server.port $PORT
   ```
5. Add environment variables:
   - `APP_PASSCODE` (required)
   - `DATABASE_URL` (optional Postgres, recommended)
6. (If using SQLite) Add a Persistent Disk and point to the app folder to keep `expenses.db`.

## Local dev
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Notes
- Default local DB: SQLite file `expenses.db`
- For multi-device sync, use `DATABASE_URL` Postgres string.
- All categories/budgets preloaded; subscriptions can be auto-posted into any month.

Security:
- This app includes a simple passcode screen.
- For additional protection, deploy behind your host's auth or use Streamlit secrets for per-user keys.