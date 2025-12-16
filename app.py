import os
import streamlit as st
import pandas as pd
import altair as alt
from datetime import date, datetime, timedelta

# DB (SQLAlchemy — works with Postgres or SQLite)
from sqlalchemy import create_engine, text, MetaData, Table, Column, Integer, String, Float, Text, Boolean, DateTime, func, bindparam

st.set_page_config(page_title="Ashvin Expense Tracker (Hosted)", page_icon="📱", layout="wide")

# ---- Security: Passcode gate (set APP_PASSCODE in secrets or env) ----
PASS = os.getenv("APP_PASSCODE", "") or st.secrets.get("APP_PASSCODE", "")
if PASS:
    if "AUTH_OK" not in st.session_state:
        st.session_state["AUTH_OK"] = False
    if not st.session_state["AUTH_OK"]:
        st.title("🔒 Protected")
        pwd = st.text_input("Enter passcode", type="password")
        if st.button("Unlock"):
            if pwd == PASS:
                st.session_state["AUTH_OK"] = True
                st.experimental_rerun()
            else:
                st.error("Incorrect passcode")
        st.stop()

# ---- DB Setup (DATABASE_URL for Postgres; else SQLite local) ----
DB_URL = os.getenv("DATABASE_URL", "")
if not DB_URL:
    DB_PATH = os.path.join(os.path.dirname(__file__), "expenses.db")
    DB_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(DB_URL, future=True)

metadata = MetaData()

# Tables (DB-agnostic)
# Keep date as ISO string (YYYY-MM-DD) for simple cross-DB filtering by string range
from sqlalchemy import Table
transactions = Table(
    "transactions", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("date", String, nullable=False),
    Column("account", String),
    Column("merchant", String),
    Column("category", String),
    Column("type", String, nullable=False),  # Expense | Income | Transfer
    Column("method", String),
    Column("amount", Float, nullable=False),
    Column("notes", Text),
    Column("created_at", DateTime, server_default=func.now()),
)

budgets = Table(
    "budgets", metadata,
    Column("category", String, primary_key=True),
    Column("planned", Float, nullable=False),
)

subscriptions = Table(
    "subscriptions", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String, nullable=False),
    Column("amount", Float, nullable=False),
    Column("billing_day", Integer, nullable=False),
    Column("account", String),
    Column("category", String, default="Subscriptions"),
    Column("notes", Text),
    Column("active", Boolean),
)

metadata.create_all(engine)

ACCOUNTS = ["Barclays","Lloyds","Revolut","Cash"]
METHODS = ["Card","Direct Debit","Transfer","Cash"]
TYPES = ["Expense","Income","Transfer"]
CATEGORIES = [
    "Rent","Groceries","Travel","Subscriptions","Debt Payments",
    "Dining & Coffee","Shopping","Health","Education","Utilities","Transfers","Other"
]
DEFAULT_BUDGETS = {
    "Rent": 600,
    "Groceries": 200,
    "Travel": 150,
    "Subscriptions": 165,  # Galaxy AI 15 + Lenses 20 + Insurance 80 + Other 50
    "Debt Payments": 0,
    "Dining & Coffee": 100,
    "Shopping": 50,
    "Health": 0,
    "Education": 0,
    "Utilities": 0,
    "Transfers": 0,
    "Other": 0
}

# Seed budgets if empty
with engine.begin() as conn:
    n = conn.execute(text("SELECT COUNT(*) FROM budgets")).scalar()
    if not n:
        for cat, val in DEFAULT_BUDGETS.items():
            conn.execute(text("INSERT INTO budgets (category, planned) VALUES (:c, :p)"), {"c": cat, "p": float(val)})

# ----- Helpers -----
def to_month_str(d: date):
    return d.strftime("%Y-%m")

def month_range(ym: str):
    y, m = ym.split("-")
    y, m = int(y), int(m)
    first = date(y, m, 1)
    nxt = date(y+1, 1, 1) if m == 12 else date(y, m+1, 1)
    last = nxt - timedelta(days=1)
    return first, last

def fetch_months():
    df = pd.read_sql(text("SELECT date FROM transactions ORDER BY date"), engine)
    if df.empty:
        return [to_month_str(date.today())]
    months = sorted({str(x)[:7] for x in df["date"].astype(str)})
    return months or [to_month_str(date.today())]

def read_transactions(ym: str):
    start, end = month_range(ym)
    q = text("SELECT * FROM transactions WHERE date >= :s AND date <= :e ORDER BY date DESC, id DESC")
    df = pd.read_sql(q, engine, params={"s": str(start), "e": str(end)})
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"]).dt.date
    return df

def insert_transaction(d, account, merchant, category, ttype, method, amount, notes):
    with engine.begin() as conn:
        conn.execute(
            transactions.insert().values(
                date=str(d), account=account, merchant=merchant, category=category,
                type=ttype, method=method, amount=float(amount), notes=notes
            )
        )

def delete_transactions(ids):
    if not ids:
        return
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM transactions WHERE id = ANY(:ids)"), {"ids": ids}) if engine.url.get_backend_name() in ["postgresql"] else \
        conn.execute(text("DELETE FROM transactions WHERE id IN :ids").bindparams(bindparam("ids", expanding=True)), {"ids": ids})

def read_budgets():
    return pd.read_sql(text("SELECT * FROM budgets"), engine)

def upsert_budget(cat, planned):
    backend = engine.url.get_backend_name()
    with engine.begin() as conn:
        if backend in ["postgresql", "sqlite"]:
            conn.execute(text("INSERT INTO budgets (category, planned) VALUES (:c,:p) ON CONFLICT(category) DO UPDATE SET planned = excluded.planned"),
                         {"c": cat, "p": float(planned)})
        else:
            # Fallback: try update then insert if no row
            n = conn.execute(text("UPDATE budgets SET planned=:p WHERE category=:c"), {"c":cat,"p":float(planned)}).rowcount
            if n == 0:
                conn.execute(text("INSERT INTO budgets (category, planned) VALUES (:c,:p)"), {"c":cat,"p":float(planned)})

def read_subs(active_only=True):
    q = "SELECT * FROM subscriptions" + (" WHERE active=1" if active_only else "")
    return pd.read_sql(text(q), engine)

def add_sub(name, amount, billing_day, account, category, notes, active=True):
    with engine.begin() as conn:
        conn.execute(subscriptions.insert().values(
            name=name, amount=float(amount), billing_day=int(billing_day),
            account=account, category=category, notes=notes, active=bool(active)
        ))

def post_due_subs(ym: str):
    start, end = month_range(ym)
    y, m = int(ym[:4]), int(ym[5:7])
    df = read_subs(active_only=True)
    if df.empty:
        return 0
    posted = 0
    for _, r in df.iterrows():
        day = min(int(r["billing_day"]), end.day)
        d = date(y, m, day)
        insert_transaction(d, r.get("account","Barclays"), r["name"], r.get("category","Subscriptions"),
                           "Expense", "Direct Debit", r["amount"], r.get("notes","Auto-posted subscription"))
        posted += 1
    return posted

def import_csv(file, ym_target=None):
    df = pd.read_csv(file)
    # Expected columns: date, account, merchant, category, type, method, amount, [notes]
    cols = {c.lower().strip(): c for c in df.columns}
    req = ["date","account","merchant","category","type","method","amount"]
    for r in req:
        if r not in cols:
            raise ValueError(f"Missing column '{r}' in CSV.")
    for _, r in df.iterrows():
        d = pd.to_datetime(r[cols["date"]], errors="coerce")
        if pd.isna(d):
            continue
        if ym_target:
            y, m = int(ym_target[:4]), int(ym_target[5:7])
            d = d.to_pydatetime().date().replace(year=y, month=m)
        else:
            d = d.date()
        insert_transaction(
            d,
            str(r[cols["account"]]) if not pd.isna(r[cols["account"]]) else "",
            str(r[cols["merchant"]]) if not pd.isna(r[cols["merchant"]]) else "",
            str(r[cols["category"]]) if not pd.isna(r[cols["category"]]) else "Other",
            str(r[cols["type"]]) if not pd.isna(r[cols["type"]]) else "Expense",
            str(r[cols["method"]]) if not pd.isna(r[cols["method"]]) else "Card",
            float(r[cols["amount"]]),
            str(r[cols["notes"]]) if "notes" in cols and not pd.isna(r[cols["notes"]]) else ""
        )

# ----- UI -----
st.title("📱 Ashvin Expense Tracker — Hosted")

# Sidebar
months = fetch_months()
default_month = to_month_str(date.today())
all_months = sorted(set(months + [default_month]))
month_choice = st.sidebar.selectbox("Month", options=all_months, index=all_months.index(default_month))
st.sidebar.caption("Tip: Switch months to review history.")

with st.sidebar.expander("Budgets (quick edit)"):
    bdf = read_budgets().set_index("category").reindex(CATEGORIES).fillna(0)
    for cat in CATEGORIES:
        val = float(bdf.loc[cat, "planned"]) if cat in bdf.index else 0.0
        new_val = st.number_input(f"{cat}", min_value=0.0, value=val, step=5.0, key=f"bud_{cat}")
        if new_val != val:
            upsert_budget(cat, new_val)
    st.success("Budgets saved")

tab1, tab2, tab3, tab4 = st.tabs(["➕ Add Transaction", "🔁 Subscriptions", "📊 Dashboard", "🗂 Data"])

with tab1:
    st.subheader("Add a transaction")
    with st.form("add_tx"):
        c1, c2, c3 = st.columns(3)
        d = c1.date_input("Date", value=date.today())
        account = c2.selectbox("Account", ACCOUNTS, index=0)
        method = c3.selectbox("Payment Method", METHODS, index=0)
        merchant = st.text_input("Merchant / Payee")
        c4, c5, c6 = st.columns(3)
        category = c4.selectbox("Category", CATEGORIES, index=CATEGORIES.index("Groceries") if "Groceries" in CATEGORIES else 0)
        ttype = c5.selectbox("Type", TYPES, index=0)
        amount = c6.number_input("Amount (£)", min_value=0.0, step=0.50, value=0.0, format="%.2f")
        notes = st.text_area("Notes", height=60)
        submitted = st.form_submit_button("Add")
        if submitted:
            if amount <= 0:
                st.error("Amount must be greater than 0.")
            else:
                insert_transaction(d, account, merchant, category, ttype, method, amount, notes)
                st.success("Transaction added.")

    st.markdown("---")
    st.subheader("Import CSV")
    st.caption("CSV columns: date, account, merchant, category, type, method, amount, [notes]")
    up = st.file_uploader("Upload CSV", type=["csv"])
    if up is not None:
        force_month = st.checkbox("Force transactions into selected month", value=False)
        try:
            import_csv(up, ym_target=month_choice if force_month else None)
            st.success("CSV imported successfully.")
        except Exception as e:
            st.error(f"Import failed: {e}")

with tab2:
    st.subheader("Manage subscriptions")
    subs = read_subs(active_only=False)
    if subs.empty:
        st.info("No subscriptions yet. Add one below.")
    else:
        st.dataframe(subs)

    st.markdown("### Add subscription")
    with st.form("add_sub"):
        c1, c2, c3 = st.columns(3)
        s_name = c1.text_input("Name", value="")
        s_amount = c2.number_input("Amount (£)", min_value=0.0, step=1.0, value=0.0)
        s_day = c3.number_input("Billing day (1-28/31)", min_value=1, max_value=31, value=1, step=1)
        c4, c5 = st.columns(2)
        s_account = c4.selectbox("Account", ACCOUNTS, index=0)
        s_cat = c5.selectbox("Category", CATEGORIES, index=CATEGORIES.index("Subscriptions") if "Subscriptions" in CATEGORIES else 0)
        s_notes = st.text_input("Notes", value="")
        s_active = st.checkbox("Active", value=True)
        add_submitted = st.form_submit_button("Add subscription")
        if add_submitted:
            if s_name.strip() == "" or s_amount <= 0:
                st.error("Please enter a valid name and amount.")
            else:
                add_sub(s_name.strip(), s_amount, s_day, s_account, s_cat, s_notes, s_active)
                st.success("Subscription added.")

    st.markdown("---")
    if st.button(f"Post all active subscriptions for {month_choice}"):
        n = post_due_subs(month_choice)
        st.success(f"Posted {n} subscription transactions into {month_choice}.")

with tab3:
    st.subheader(f"Dashboard — {month_choice}")
    df = read_transactions(month_choice)
    if df.empty:
        st.info("No data for this month yet. Add transactions in the first tab.")
    else:
        # Summary
        total_income = df.loc[df["type"]=="Income","amount"].sum()
        total_expense = df.loc[df["type"]=="Expense","amount"].sum()
        net = (total_income - total_expense)

        c1, c2, c3 = st.columns(3)
        c1.metric("Total Income", f"£{total_income:,.2f}")
        c2.metric("Total Expenses", f"£{total_expense:,.2f}")
        c3.metric("Net Cash Flow", f"£{net:,.2f}")

        # Category chart
        by_cat = df[df["type"]=="Expense"].groupby("category")["amount"].sum().reset_index()
        by_cat = by_cat.sort_values("amount", ascending=False)
        budgets = read_budgets()
        by_cat = by_cat.merge(budgets, on="category", how="left").rename(columns={"planned":"budget"})
        by_cat["variance"] = by_cat["budget"].fillna(0) - by_cat["amount"]

        st.markdown("#### Spending by category")
        st.dataframe(by_cat.rename(columns={"amount":"spent"}))

        chart = alt.Chart(by_cat).mark_bar().encode(
            x=alt.X('amount:Q', title='Spent (£)'),
            y=alt.Y('category:N', sort='-x', title='Category'),
            color=alt.Color('category:N', legend=None)
        ).properties(height=350)
        st.altair_chart(chart, use_container_width=True)

        # 6-month trend (build from all data in DB)
        all_df = pd.read_sql(text("SELECT date, type, amount FROM transactions"), engine)
        if not all_df.empty:
            all_df["ym"] = pd.to_datetime(all_df["date"]).dt.strftime("%Y-%m")
            hist = all_df.groupby(["ym","type"])["amount"].sum().reset_index()
            pvt = hist.pivot(index="ym", columns="type", values="amount").fillna(0)
            pvt["Net"] = pvt.get("Income",0) - pvt.get("Expense",0)
            pvt = pvt.reset_index().sort_values("ym")
            tr = pvt.tail(6)
            trm = tr.melt("ym", var_name="Metric", value_name="Amount")
            line = alt.Chart(trm).mark_line(point=True).encode(
                x=alt.X("ym:N", title="Month"),
                y=alt.Y("Amount:Q", title="£"),
                color="Metric:N"
            ).properties(height=300)
            st.markdown("#### 6‑month trend")
            st.altair_chart(line, use_container_width=True)

with tab4:
    st.subheader(f"Transactions — {month_choice}")
    df = read_transactions(month_choice)
    if df.empty:
        st.info("No transactions yet.")
    else:
        st.dataframe(df)
        st.download_button("Export CSV", data=df.to_csv(index=False), file_name=f"transactions_{month_choice}.csv", mime="text/csv")
        ids = st.multiselect("Select rows to delete (by id)", options=df["id"].tolist())
        if st.button("Delete selected"):
            delete_transactions(ids)
            st.success("Deleted. Reload to see updates.")

st.caption("Hosted version • Passcode via APP_PASSCODE • Optional Postgres via DATABASE_URL • Built for Ashvin.")