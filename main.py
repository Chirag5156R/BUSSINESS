"""
Ledgerly API — FastAPI backend for the billing / sales / stock / customers app.

Multi-tenant: every business owner registers/logs in, and every row of data
(customers, products, bills, payments) is scoped to their account. Postgres
database (works on serverless hosts like Vercel, unlike local SQLite), full
logical isolation via user_id + auth tokens.

Run with:
    pip install -r requirements.txt
    export DATABASE_URL=postgres://...   # e.g. from Neon / Vercel Postgres / Supabase
    export GROQ_API_KEY=gsk_...          # get one free at https://console.groq.com
    uvicorn main:app --reload --port 8000
"""
import psycopg2
import psycopg2.extras

import os
import json
import datetime
import hashlib
import secrets
from contextlib import contextmanager
from typing import Optional, List

import requests
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

# ===========================================================================
# SECTION 1: DATABASE LAYER
# ===========================================================================
DATABASE_URL = os.environ["DATABASE_URL"]
SESSION_LIFETIME_DAYS = 30


@contextmanager
def get_connection():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                business_name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                created_at TIMESTAMP DEFAULT NOW(),
                expires_at TIMESTAMP NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS customers (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                name TEXT NOT NULL,
                mobile TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(user_id, mobile)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                name TEXT NOT NULL,
                image_path TEXT,
                cost_price REAL NOT NULL,
                sell_price REAL NOT NULL,
                stock_qty INTEGER NOT NULL DEFAULT 0,
                reorder_level INTEGER NOT NULL DEFAULT 5,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bills (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                customer_id INTEGER REFERENCES customers(id),
                bill_date TIMESTAMP DEFAULT NOW(),
                total_amount REAL NOT NULL DEFAULT 0,
                paid_amount REAL NOT NULL DEFAULT 0,
                balance_due REAL NOT NULL DEFAULT 0,
                payment_mode TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bill_items (
                id SERIAL PRIMARY KEY,
                bill_id INTEGER NOT NULL REFERENCES bills(id),
                product_id INTEGER NOT NULL REFERENCES products(id),
                quantity INTEGER NOT NULL,
                price_each REAL NOT NULL,
                cost_each REAL NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id SERIAL PRIMARY KEY,
                bill_id INTEGER NOT NULL REFERENCES bills(id),
                amount REAL NOT NULL,
                mode TEXT NOT NULL,
                payment_date TIMESTAMP DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS daily_briefs (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                brief_date DATE NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(user_id, brief_date)
            )
        """)
        conn.commit()


def row_to_dict(row) -> dict:
    return dict(row) if row is not None else None


def rows_to_list(rows) -> List[dict]:
    return [dict(r) for r in rows]


def with_image_data(product: Optional[dict]) -> Optional[dict]:
    """The frontend refers to a product's image as `image_data` (it may be a data: URL).
    The DB column is `image_path` -- alias it here so the API shape matches the UI."""
    if product is None:
        return None
    product = dict(product)
    product["image_data"] = product.pop("image_path", None)
    return product


def products_with_image_data(products: List[dict]) -> List[dict]:
    return [with_image_data(p) for p in products]


# ---------------------------------------------------------------------------
# Auth / users
# ---------------------------------------------------------------------------

def hash_password(password: str, salt: Optional[str] = None) -> tuple:
    if salt is None:
        salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000)
    return digest.hex(), salt


def db_create_user(business_name, email, password):
    password_hash, salt = hash_password(password)
    with get_connection() as conn:
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO users (business_name, email, password_hash, password_salt) VALUES (%s, %s, %s, %s) RETURNING id",
                (business_name, email.lower(), password_hash, salt),
            )
            new_id = cur.fetchone()["id"]
            conn.commit()
            return new_id
        except psycopg2.IntegrityError:
            conn.rollback()
            return None


def db_get_user_by_email(email):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE email = %s", (email.lower(),))
        return row_to_dict(cur.fetchone())


def db_verify_login(email, password):
    user = db_get_user_by_email(email)
    if user is None:
        return None
    check_hash, _ = hash_password(password, user["password_salt"])
    if not secrets.compare_digest(check_hash, user["password_hash"]):
        return None
    return user


def db_create_session(user_id):
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.datetime.now() + datetime.timedelta(days=SESSION_LIFETIME_DAYS)).isoformat()
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO sessions (token, user_id, expires_at) VALUES (%s, %s, %s)",
            (token, user_id, expires_at),
        )
        conn.commit()
    return token


def db_get_session_user(token):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT users.id, users.business_name, users.email, sessions.expires_at
            FROM sessions JOIN users ON sessions.user_id = users.id
            WHERE sessions.token = %s
        """, (token,))
        row = cur.fetchone()
        if row is None:
            return None
        row = dict(row)
        if datetime.datetime.fromisoformat(str(row["expires_at"])) < datetime.datetime.now():
            cur.execute("DELETE FROM sessions WHERE token = %s", (token,))
            conn.commit()
            return None
        return row


def db_delete_session(token):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM sessions WHERE token = %s", (token,))
        conn.commit()


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------

def db_add_customer(user_id, name, mobile):
    with get_connection() as conn:
        try:
            cur = conn.cursor()
            cur.execute("INSERT INTO customers (user_id, name, mobile) VALUES (%s, %s, %s) RETURNING id",
                        (user_id, name, mobile))
            new_id = cur.fetchone()["id"]
            conn.commit()
            return new_id
        except psycopg2.IntegrityError:
            conn.rollback()
            return None


def db_get_all_customers(user_id):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM customers WHERE user_id = %s ORDER BY name", (user_id,))
        return rows_to_list(cur.fetchall())


def db_get_outstanding_balances(user_id):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT customers.id, customers.name, customers.mobile,
                   SUM(bills.balance_due) AS total_due,
                   COUNT(bills.id) AS bill_count
            FROM bills JOIN customers ON bills.customer_id = customers.id
            WHERE bills.balance_due > 0 AND bills.user_id = %s
            GROUP BY customers.id
            ORDER BY total_due DESC
        """, (user_id,))
        return rows_to_list(cur.fetchall())


def db_get_customer_payment_history(user_id):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT customers.id, customers.name, customers.mobile,
                   COUNT(bills.id) AS total_bills,
                   SUM(bills.total_amount) AS total_billed,
                   SUM(bills.paid_amount) AS total_paid,
                   SUM(bills.balance_due) AS total_due,
                   SUM(CASE WHEN bills.balance_due > 0 THEN 1 ELSE 0 END) AS unpaid_bill_count
            FROM customers LEFT JOIN bills ON customers.id = bills.customer_id
            WHERE customers.user_id = %s
            GROUP BY customers.id
            HAVING COUNT(bills.id) > 0
            ORDER BY total_due DESC
        """, (user_id,))
        return rows_to_list(cur.fetchall())


# ---------------------------------------------------------------------------
# Products / stock
# ---------------------------------------------------------------------------

def db_add_product(user_id, name, cost_price, sell_price, stock_qty, reorder_level=5, image_path=None):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO products (user_id, name, cost_price, sell_price, stock_qty, reorder_level, image_path)
               VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (user_id, name, cost_price, sell_price, stock_qty, reorder_level, image_path),
        )
        new_id = cur.fetchone()["id"]
        conn.commit()
        return new_id


def db_update_stock(user_id, product_id, qty_change):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE products SET stock_qty = stock_qty + %s WHERE id = %s AND user_id = %s",
            (qty_change, product_id, user_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise KeyError("product not found")


def db_update_product(user_id, product_id, name, cost_price, sell_price, stock_qty, reorder_level, image_path=None):
    with get_connection() as conn:
        cur = conn.cursor()
        if image_path is not None:
            cur.execute(
                """UPDATE products SET name=%s, cost_price=%s, sell_price=%s, stock_qty=%s,
                       reorder_level=%s, image_path=%s WHERE id=%s AND user_id=%s""",
                (name, cost_price, sell_price, stock_qty, reorder_level, image_path, product_id, user_id),
            )
        else:
            cur.execute(
                """UPDATE products SET name=%s, cost_price=%s, sell_price=%s, stock_qty=%s,
                       reorder_level=%s WHERE id=%s AND user_id=%s""",
                (name, cost_price, sell_price, stock_qty, reorder_level, product_id, user_id),
            )
        if cur.rowcount == 0:
            raise KeyError("product not found")
        conn.commit()
        cur.execute("SELECT * FROM products WHERE id=%s AND user_id=%s", (product_id, user_id))
        return row_to_dict(cur.fetchone())


def db_delete_product(user_id, product_id):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM products WHERE id = %s AND user_id = %s", (product_id, user_id))
        owned = cur.fetchone()
        if owned is None:
            raise KeyError("product not found")
        cur.execute("SELECT COUNT(*) AS count FROM bill_items WHERE product_id = %s", (product_id,))
        in_use = cur.fetchone()["count"]
        if in_use:
            raise ValueError("This product appears on existing bills and can't be deleted.")
        cur.execute("DELETE FROM products WHERE id = %s AND user_id = %s", (product_id, user_id))
        conn.commit()


def db_get_all_products(user_id):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM products WHERE user_id = %s ORDER BY name", (user_id,))
        return rows_to_list(cur.fetchall())


def db_get_low_stock_products(user_id):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM products WHERE user_id = %s AND stock_qty <= reorder_level ORDER BY stock_qty",
            (user_id,),
        )
        return rows_to_list(cur.fetchall())


# ---------------------------------------------------------------------------
# Billing
# ---------------------------------------------------------------------------

def db_create_bill(user_id, customer_id, items, paid_amount, payment_mode):
    with get_connection() as conn:
        cur = conn.cursor()
        if customer_id is not None:
            cur.execute(
                "SELECT id FROM customers WHERE id = %s AND user_id = %s", (customer_id, user_id)
            )
            if cur.fetchone() is None:
                raise ValueError("Customer not found")

        total_amount = sum(i["quantity"] * i["price_each"] for i in items)
        balance_due = round(total_amount - paid_amount, 2)

        cur.execute(
            """INSERT INTO bills (user_id, customer_id, total_amount, paid_amount, balance_due, payment_mode)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
            (user_id, customer_id, total_amount, paid_amount, balance_due, payment_mode),
        )
        bill_id = cur.fetchone()["id"]

        for item in items:
            cur.execute(
                "SELECT stock_qty FROM products WHERE id = %s AND user_id = %s",
                (item["product_id"], user_id),
            )
            prod = cur.fetchone()
            if prod is None:
                raise ValueError(f"Product {item['product_id']} not found")
            if item["quantity"] > prod["stock_qty"]:
                raise ValueError(f"Not enough stock for product {item['product_id']}")

            cur.execute(
                """INSERT INTO bill_items (bill_id, product_id, quantity, price_each, cost_each)
                   VALUES (%s, %s, %s, %s, %s)""",
                (bill_id, item["product_id"], item["quantity"], item["price_each"], item["cost_each"]),
            )
            cur.execute("UPDATE products SET stock_qty = stock_qty - %s WHERE id = %s",
                        (item["quantity"], item["product_id"]))

        if paid_amount > 0:
            cur.execute("INSERT INTO payments (bill_id, amount, mode) VALUES (%s, %s, %s)",
                        (bill_id, paid_amount, payment_mode))

        conn.commit()
        return bill_id


def db_add_payment(user_id, bill_id, amount, mode):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT balance_due FROM bills WHERE id = %s AND user_id = %s", (bill_id, user_id)
        )
        if cur.fetchone() is None:
            raise KeyError("bill not found")
        cur.execute("INSERT INTO payments (bill_id, amount, mode) VALUES (%s, %s, %s)", (bill_id, amount, mode))
        cur.execute(
            "UPDATE bills SET paid_amount = paid_amount + %s, balance_due = balance_due - %s WHERE id = %s",
            (amount, amount, bill_id),
        )
        conn.commit()


def db_get_bills(user_id, start_date=None, end_date=None, customer_id=None):
    with get_connection() as conn:
        cur = conn.cursor()
        query = """
            SELECT bills.*, customers.name AS customer_name, customers.mobile AS customer_mobile
            FROM bills LEFT JOIN customers ON bills.customer_id = customers.id
            WHERE bills.user_id = %s
        """
        params = [user_id]
        if start_date:
            query += " AND bill_date::date >= %s::date"
            params.append(start_date)
        if end_date:
            query += " AND bill_date::date <= %s::date"
            params.append(end_date)
        if customer_id:
            query += " AND bills.customer_id = %s"
            params.append(customer_id)
        query += " ORDER BY bills.bill_date DESC"
        cur.execute(query, params)
        return rows_to_list(cur.fetchall())


def db_get_bill_items(user_id, bill_id):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM bills WHERE id = %s AND user_id = %s", (bill_id, user_id))
        if cur.fetchone() is None:
            raise KeyError("bill not found")
        cur.execute(
            """SELECT bill_items.*, products.name AS product_name
               FROM bill_items JOIN products ON bill_items.product_id = products.id
               WHERE bill_id = %s""",
            (bill_id,),
        )
        return rows_to_list(cur.fetchall())


# ---------------------------------------------------------------------------
# Sales / dashboard analytics
# ---------------------------------------------------------------------------

def db_get_sales_summary(user_id, start_date, end_date):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT COALESCE(SUM(total_amount), 0) AS revenue,
                   COALESCE(SUM(paid_amount), 0) AS collected,
                   COALESCE(SUM(balance_due), 0) AS outstanding,
                   COUNT(*) AS bill_count
            FROM bills
            WHERE user_id = %s AND bill_date::date BETWEEN %s::date AND %s::date
        """, (user_id, start_date, end_date))
        return dict(cur.fetchone())


def db_get_profit_loss(user_id, start_date, end_date):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                COALESCE(SUM(bill_items.quantity * bill_items.price_each), 0) AS revenue,
                COALESCE(SUM(bill_items.quantity * bill_items.cost_each), 0) AS cost
            FROM bill_items JOIN bills ON bill_items.bill_id = bills.id
            WHERE bills.user_id = %s AND bills.bill_date::date BETWEEN %s::date AND %s::date
        """, (user_id, start_date, end_date))
        row = cur.fetchone()
        revenue = row["revenue"] or 0
        cost = row["cost"] or 0
        return {"revenue": revenue, "cost": cost, "profit": revenue - cost}


def db_get_product_sales_ranking(user_id, start_date, end_date):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT products.id, products.name,
                   COALESCE(SUM(bill_items.quantity), 0) AS units_sold,
                   COALESCE(SUM(bill_items.quantity * bill_items.price_each), 0) AS revenue
            FROM products
            LEFT JOIN bill_items ON products.id = bill_items.product_id
            LEFT JOIN bills ON bill_items.bill_id = bills.id
                AND bills.bill_date::date BETWEEN %s::date AND %s::date
            WHERE products.user_id = %s
            GROUP BY products.id
            ORDER BY units_sold DESC
        """, (start_date, end_date, user_id))
        return rows_to_list(cur.fetchall())


def db_get_home_summary(user_id):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS count FROM customers WHERE user_id = %s", (user_id,))
        total_customers = cur.fetchone()["count"]
        cur.execute("SELECT COUNT(*) AS count FROM products WHERE user_id = %s", (user_id,))
        total_products = cur.fetchone()["count"]
        cur.execute("SELECT COUNT(*) AS count FROM bills WHERE user_id = %s", (user_id,))
        total_bills = cur.fetchone()["count"]
        cur.execute("SELECT COALESCE(SUM(balance_due),0) AS total FROM bills WHERE user_id = %s", (user_id,))
        total_due = cur.fetchone()["total"]
        return {
            "total_customers": total_customers,
            "total_products": total_products,
            "total_bills": total_bills,
            "total_due": total_due,
        }


# ---------------------------------------------------------------------------
# Daily AI brief (cached once per user per calendar day)
# ---------------------------------------------------------------------------

def db_get_daily_brief(user_id, brief_date):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT content, created_at FROM daily_briefs WHERE user_id = %s AND brief_date = %s",
            (user_id, brief_date),
        )
        return row_to_dict(cur.fetchone())


def db_save_daily_brief(user_id, brief_date, content):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO daily_briefs (user_id, brief_date, content) VALUES (%s, %s, %s)
            ON CONFLICT (user_id, brief_date) DO UPDATE SET content = EXCLUDED.content,
                created_at = NOW()
        """, (user_id, brief_date, content))
        conn.commit()


# ---------------------------------------------------------------------------
# Demo account (admin@gmail.com / admin1234)
#
# IMPORTANT: the demo account never touches Postgres. It is not a row in the
# `users` table and it creates no rows in `customers`/`products`/`bills`/etc.
# All of its data comes from the read-only demo_data.json file next to this
# script, and is served through the demo_* functions below. This keeps the
# real database — and any real users' data in it — completely untouched.
# ---------------------------------------------------------------------------

DEMO_USER_ID = -1  # sentinel id, never a real row in the users table
DEMO_EMAIL = "admin@gmail.com"
DEMO_PASSWORD = "admin1234"
DEMO_DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_data.json")

# Demo login sessions live only in memory (never in the `sessions` table, which
# has a foreign key to `users` and isn't meant to hold a fake user id).
DEMO_SESSIONS: dict = {}          # token -> expiry datetime

_demo_data_cache = None


def load_demo_data() -> dict:
    global _demo_data_cache
    if _demo_data_cache is None:
        with open(DEMO_DATA_PATH, "r", encoding="utf-8") as f:
            _demo_data_cache = json.load(f)
    return _demo_data_cache


def is_demo(user_id: int) -> bool:
    return user_id == DEMO_USER_ID


def _demo_resolved_bills() -> List[dict]:
    """Turns each demo bill's `days_ago` into a real date relative to *right now*,
    so the demo always looks like a live shop with recent activity — no matter
    when someone opens it."""
    data = load_demo_data()
    now = datetime.datetime.now()
    customers_by_id = {c["id"]: c for c in data["customers"]}
    resolved = []
    for b in data["bills"]:
        bill_dt = now - datetime.timedelta(days=b["days_ago"])
        cust = customers_by_id.get(b["customer_id"]) if b["customer_id"] is not None else None
        resolved.append({
            **b,
            "bill_date": bill_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "customer_name": cust["name"] if cust else None,
            "customer_mobile": cust["mobile"] if cust else None,
        })
    return resolved


def demo_get_all_customers() -> List[dict]:
    return sorted([dict(c) for c in load_demo_data()["customers"]], key=lambda c: c["name"])


def demo_get_all_products() -> List[dict]:
    return sorted([dict(p) for p in load_demo_data()["products"]], key=lambda p: p["name"])


def demo_get_low_stock_products() -> List[dict]:
    low = [p for p in demo_get_all_products() if p["stock_qty"] <= p["reorder_level"]]
    return sorted(low, key=lambda p: p["stock_qty"])


def demo_get_outstanding_balances() -> List[dict]:
    totals = {}
    for b in _demo_resolved_bills():
        if b["customer_id"] is not None and b["balance_due"] > 0:
            cid = b["customer_id"]
            row = totals.setdefault(cid, {"id": cid, "name": b["customer_name"],
                                           "mobile": b["customer_mobile"], "total_due": 0.0, "bill_count": 0})
            row["total_due"] = round(row["total_due"] + b["balance_due"], 2)
            row["bill_count"] += 1
    return sorted(totals.values(), key=lambda o: -o["total_due"])


def demo_get_customer_payment_history() -> List[dict]:
    stats = {
        c["id"]: {"id": c["id"], "name": c["name"], "mobile": c["mobile"], "total_bills": 0,
                  "total_billed": 0.0, "total_paid": 0.0, "total_due": 0.0, "unpaid_bill_count": 0}
        for c in load_demo_data()["customers"]
    }
    for b in _demo_resolved_bills():
        if b["customer_id"] is None:
            continue
        s = stats[b["customer_id"]]
        s["total_bills"] += 1
        s["total_billed"] = round(s["total_billed"] + b["total_amount"], 2)
        s["total_paid"] = round(s["total_paid"] + b["paid_amount"], 2)
        s["total_due"] = round(s["total_due"] + b["balance_due"], 2)
        if b["balance_due"] > 0:
            s["unpaid_bill_count"] += 1
    return sorted([s for s in stats.values() if s["total_bills"] > 0], key=lambda s: -s["total_due"])


def demo_get_bills(start_date=None, end_date=None, customer_id=None) -> List[dict]:
    def in_range(b):
        d = b["bill_date"][:10]
        if start_date and d < start_date:
            return False
        if end_date and d > end_date:
            return False
        if customer_id and b["customer_id"] != customer_id:
            return False
        return True

    bills = [b for b in _demo_resolved_bills() if in_range(b)]
    bills.sort(key=lambda b: b["bill_date"], reverse=True)
    return [{k: v for k, v in b.items() if k not in ("items", "days_ago")} for b in bills]


def demo_get_bill_items(bill_id: int) -> List[dict]:
    data = load_demo_data()
    products_by_id = {p["id"]: p for p in data["products"]}
    for b in data["bills"]:
        if b["id"] == bill_id:
            return [{**i, "product_name": products_by_id.get(i["product_id"], {}).get("name", "Unknown")}
                    for i in b["items"]]
    raise KeyError("bill not found")


def demo_get_sales_summary(start_date: str, end_date: str) -> dict:
    bills = demo_get_bills(start_date, end_date)
    return {
        "revenue": round(sum(b["total_amount"] for b in bills), 2),
        "collected": round(sum(b["paid_amount"] for b in bills), 2),
        "outstanding": round(sum(b["balance_due"] for b in bills), 2),
        "bill_count": len(bills),
    }


def demo_get_profit_loss(start_date: str, end_date: str) -> dict:
    now = datetime.datetime.now()
    revenue = cost = 0.0
    for b in load_demo_data()["bills"]:
        d = (now - datetime.timedelta(days=b["days_ago"])).strftime("%Y-%m-%d")
        if d < start_date or d > end_date:
            continue
        for i in b["items"]:
            revenue += i["quantity"] * i["price_each"]
            cost += i["quantity"] * i["cost_each"]
    return {"revenue": round(revenue, 2), "cost": round(cost, 2), "profit": round(revenue - cost, 2)}


def demo_get_product_sales_ranking(start_date: str, end_date: str) -> List[dict]:
    now = datetime.datetime.now()
    data = load_demo_data()
    sold = {p["id"]: {"id": p["id"], "name": p["name"], "units_sold": 0, "revenue": 0.0} for p in data["products"]}
    for b in data["bills"]:
        d = (now - datetime.timedelta(days=b["days_ago"])).strftime("%Y-%m-%d")
        if d < start_date or d > end_date:
            continue
        for i in b["items"]:
            row = sold[i["product_id"]]
            row["units_sold"] += i["quantity"]
            row["revenue"] = round(row["revenue"] + i["quantity"] * i["price_each"], 2)
    return sorted(sold.values(), key=lambda p: -p["units_sold"])


def demo_get_home_summary() -> dict:
    data = load_demo_data()
    return {
        "total_customers": len(data["customers"]),
        "total_products": len(data["products"]),
        "total_bills": len(data["bills"]),
        "total_due": round(sum(b["balance_due"] for b in data["bills"]), 2),
    }


# ---------------------------------------------------------------------------
# Thin dispatchers: real users hit Postgres as before, the demo user reads the
# JSON file. Every endpoint and AI helper below should go through these
# instead of calling db_* / demo_* directly, so nothing has to branch twice.
# ---------------------------------------------------------------------------

def get_all_customers(user_id):
    return demo_get_all_customers() if is_demo(user_id) else db_get_all_customers(user_id)


def get_outstanding_balances(user_id):
    return demo_get_outstanding_balances() if is_demo(user_id) else db_get_outstanding_balances(user_id)


def get_customer_payment_history(user_id):
    return demo_get_customer_payment_history() if is_demo(user_id) else db_get_customer_payment_history(user_id)


def get_all_products(user_id):
    return demo_get_all_products() if is_demo(user_id) else products_with_image_data(db_get_all_products(user_id))


def get_low_stock_products(user_id):
    return demo_get_low_stock_products() if is_demo(user_id) else products_with_image_data(db_get_low_stock_products(user_id))


def get_bills(user_id, start_date=None, end_date=None, customer_id=None):
    if is_demo(user_id):
        return demo_get_bills(start_date, end_date, customer_id)
    return db_get_bills(user_id, start_date, end_date, customer_id)


def get_bill_items(user_id, bill_id):
    return demo_get_bill_items(bill_id) if is_demo(user_id) else db_get_bill_items(user_id, bill_id)


def get_sales_summary(user_id, start_date, end_date):
    return demo_get_sales_summary(start_date, end_date) if is_demo(user_id) else db_get_sales_summary(user_id, start_date, end_date)


def get_profit_loss(user_id, start_date, end_date):
    return demo_get_profit_loss(start_date, end_date) if is_demo(user_id) else db_get_profit_loss(user_id, start_date, end_date)


def get_product_sales_ranking(user_id, start_date, end_date):
    return demo_get_product_sales_ranking(start_date, end_date) if is_demo(user_id) else db_get_product_sales_ranking(user_id, start_date, end_date)


def get_home_summary(user_id):
    return demo_get_home_summary() if is_demo(user_id) else db_get_home_summary(user_id)


def block_if_demo(user_id):
    """Mutating endpoints call this first: the demo account is shared and read-only,
    so writes are rejected with a friendly message instead of touching anything."""
    if is_demo(user_id):
        raise HTTPException(
            status_code=403,
            detail="This is a shared read-only demo account. Register your own free account to add data.",
        )


# ===========================================================================
# SECTION 2: AI HELPER — Groq API calls
# ===========================================================================

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"


def get_api_key() -> str:
    return os.environ.get("GROQ_API_KEY", "")


def call_groq(system_prompt: str, user_prompt: str, max_tokens: int = 600) -> str:
    api_key = get_api_key()
    if not api_key:
        return ("⚠️ No Groq API key configured. Set the GROQ_API_KEY environment "
                "variable on the server (get a free key at https://console.groq.com).")

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.4,
    }
    try:
        resp = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except requests.exceptions.RequestException as e:
        return f"⚠️ Could not reach Groq API: {e}"
    except (KeyError, IndexError):
        return "⚠️ Unexpected response from Groq API."


def analyze_customer_payment_behavior(customer_rows):
    if not customer_rows:
        return "Not enough billing history yet to analyze customer payment behavior."
    lines = [
        f"- {c['name']} ({c['mobile']}): billed {c['total_billed']:.2f}, paid {c['total_paid']:.2f}, "
        f"outstanding {c['total_due']:.2f}, {c['unpaid_bill_count']}/{c['total_bills']} bills with a balance due."
        for c in customer_rows
    ]
    system_prompt = ("You are a financial analyst helping a small shop owner understand which customers "
                      "pay reliably and which don't. Be concise and practical.")
    user_prompt = (
        "Here is each registered customer's billing history:\n\n" + "\n".join(lines) +
        "\n\nGive a short analysis: rank customers roughly into reliable payers vs risky/late payers, "
        "flag anyone who owes a large amount relative to what they've paid, and suggest 2-3 practical "
        "actions the shop owner could take (e.g. credit limits, reminders)."
    )
    return call_groq(system_prompt, user_prompt)


def analyze_sales_and_dashboard(revenue, cost, profit, top_products, slow_products):
    system_prompt = ("You are a retail business consultant. Be concise, use bullet points, "
                      "and give concrete, actionable suggestions.")
    top_str = ", ".join(f"{p['name']} ({p['units_sold']} units)" for p in top_products) or "none"
    slow_str = ", ".join(f"{p['name']} ({p['units_sold']} units)" for p in slow_products) or "none"
    user_prompt = (
        f"Revenue: {revenue:.2f}, Cost of goods sold: {cost:.2f}, Profit: {profit:.2f}.\n"
        f"Best selling products: {top_str}.\nSlow / not-selling products: {slow_str}.\n\n"
        "Give: (1) a one-line verdict on overall performance (profit or loss and why), "
        "(2) why the best sellers might be doing well, (3) 2-3 concrete ideas to improve sales "
        "of the slow-moving products or whether to stop stocking them."
    )
    return call_groq(system_prompt, user_prompt)


def analyze_stock(low_stock_products, all_products):
    system_prompt = "You are an inventory management assistant for a small retail shop. Be concise and practical."
    low_str = ", ".join(f"{p['name']} (stock {p['stock_qty']}, reorder level {p['reorder_level']})"
                        for p in low_stock_products) or "none"
    overstock_str = ", ".join(f"{p['name']} (stock {p['stock_qty']})"
                              for p in all_products if p["stock_qty"] > (p["reorder_level"] * 5)) or "none"
    user_prompt = (
        f"Products at or below their reorder level (need attention): {low_str}.\n"
        f"Products with very high stock relative to their reorder level (possible overstock): {overstock_str}.\n\n"
        "Give a short, prioritized list of what should be reordered soon, and what should NOT be reordered / "
        "should be discounted to clear (overstock). Keep it actionable."
    )
    return call_groq(system_prompt, user_prompt)


def generate_daily_brief(user_id: int) -> str:
    """One short, digestible AI brief for the day: today's sales so far, how the week is
    trending, what needs reordering, and which customers to chase for payment."""
    today = datetime.date.today()
    week_start = today - datetime.timedelta(days=6)

    today_stats = get_profit_loss(user_id, str(today), str(today))
    week_stats = get_profit_loss(user_id, str(week_start), str(today))
    low_stock = get_low_stock_products(user_id)
    outstanding = get_outstanding_balances(user_id)

    total_due = sum(o["total_due"] or 0 for o in outstanding)
    top_due = sorted(outstanding, key=lambda o: -(o["total_due"] or 0))[:3]

    low_str = ", ".join(
        f"{p['name']} (stock {p['stock_qty']}, reorder level {p['reorder_level']})" for p in low_stock
    ) or "none — stock levels look healthy"
    due_str = ", ".join(f"{o['name']} owes {o['total_due']:.2f}" for o in top_due) or "none"

    system_prompt = (
        "You are Ledgerly's AI assistant writing a short daily brief for a small shop owner, "
        "delivered first thing in the morning. Be warm, direct, and practical. Reply with 4-6 short "
        "bullet points covering: today's sales so far, how the week is trending, any urgent stock to "
        "reorder, and any customer payments worth chasing. End with one short, motivating line."
    )
    user_prompt = (
        f"Today's revenue so far: ₹{today_stats['revenue']:.2f}, profit: ₹{today_stats['profit']:.2f}.\n"
        f"Last 7 days revenue: ₹{week_stats['revenue']:.2f}, profit: ₹{week_stats['profit']:.2f}.\n"
        f"Products at or below reorder level: {low_str}.\n"
        f"Total outstanding balance owed by customers: ₹{total_due:.2f}. Customers who owe the most: {due_str}.\n\n"
        "Write today's brief."
    )
    return call_groq(system_prompt, user_prompt, max_tokens=400)


def ai_coach_reply(user_id: int, message: str, history: list):
    """Freeform AI Coach chat, grounded in the shop's live numbers."""
    today = datetime.date.today()
    month_start = today.replace(day=1)
    revenue, cost, profit = get_profit_loss(user_id, str(month_start), str(today)).values()
    home = get_home_summary(user_id)

    system_prompt = (
        "You are Ledgerly's AI Financial Coach for a small retail shop owner. You have their live "
        "numbers below. Be warm, direct, and practical — like a smart friend who's good with money. "
        "Keep replies short (3-6 sentences) unless asked for detail.\n\n"
        f"This month so far — revenue: ₹{revenue:.2f}, cost of goods sold: ₹{cost:.2f}, "
        f"profit: ₹{profit:.2f}. Registered customers: {home['total_customers']}. "
        f"Products in catalog: {home['total_products']}. Total outstanding balance owed by customers: "
        f"₹{home['total_due']:.2f}."
    )

    messages = [{"role": "system", "content": system_prompt}]
    for h in history[-8:]:
        role = "user" if h.get("role") == "user" else "assistant"
        messages.append({"role": role, "content": h.get("content", "")})
    messages.append({"role": "user", "content": message})

    api_key = get_api_key()
    if not api_key:
        return ("⚠️ No Groq API key configured. Set the GROQ_API_KEY environment variable on the "
                "server (get a free key at https://console.groq.com).")
    try:
        resp = requests.post(
            GROQ_API_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": GROQ_MODEL, "messages": messages, "max_tokens": 500, "temperature": 0.5},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except requests.exceptions.RequestException as e:
        return f"⚠️ Could not reach Groq API: {e}"
    except (KeyError, IndexError):
        return "⚠️ Unexpected response from Groq API."


# ===========================================================================
# SECTION 3: FASTAPI APP + ROUTES
# ===========================================================================

app = FastAPI(title="Ledgerly API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten this to your real frontend origin in production
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    init_db()
    load_demo_data()  # fail fast at boot if demo_data.json is missing/invalid


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LANDING_HTML_PATH = os.path.join(BASE_DIR, "index.html")   # marketing / startup page
APP_HTML_PATH = os.path.join(BASE_DIR, "app.html")          # the actual Ledgerly app


@app.get("/")
def serve_landing():
    return FileResponse(LANDING_HTML_PATH)


@app.get("/app")
def serve_app():
    return FileResponse(APP_HTML_PATH)


# ---------- Auth dependency ----------

def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not logged in.")
    token = authorization[len("Bearer "):].strip()

    expiry = DEMO_SESSIONS.get(token)
    if expiry is not None:
        if expiry < datetime.datetime.now():
            DEMO_SESSIONS.pop(token, None)
        else:
            demo = load_demo_data()
            return {"id": DEMO_USER_ID, "business_name": demo["business_name"], "email": DEMO_EMAIL}

    user = db_get_session_user(token)
    if user is None:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")
    return user


def uid(user: dict = Depends(get_current_user)) -> int:
    return user["id"]


# ---------- Pydantic models ----------

class RegisterRequest(BaseModel):
    business_name: str
    email: str
    password: str = Field(min_length=6)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v):
        v = v.strip()
        if "@" not in v or "." not in v.split("@")[-1] or len(v) < 5:
            raise ValueError("Enter a valid email address.")
        return v


class LoginRequest(BaseModel):
    email: str
    password: str


class CustomerCreate(BaseModel):
    name: str
    mobile: str


class ProductCreate(BaseModel):
    name: str
    cost_price: float = Field(ge=0)
    sell_price: float = Field(ge=0)
    stock_qty: int = Field(ge=0, default=0)
    reorder_level: int = Field(ge=0, default=5)
    image_data: Optional[str] = None


class ProductUpdate(BaseModel):
    name: str
    cost_price: float = Field(ge=0)
    sell_price: float = Field(ge=0)
    stock_qty: int = Field(ge=0)
    reorder_level: int = Field(ge=0)
    image_data: Optional[str] = None


class RestockRequest(BaseModel):
    qty: int = Field(gt=0)


class BillItem(BaseModel):
    product_id: int
    quantity: int = Field(gt=0)
    price_each: float
    cost_each: float


class BillCreate(BaseModel):
    customer_id: Optional[int] = None
    items: List[BillItem]
    paid_amount: float = Field(ge=0)
    payment_mode: str = "Cash"


class PaymentCreate(BaseModel):
    amount: float = Field(gt=0)
    mode: str = "Cash"


class CoachMessage(BaseModel):
    message: str
    history: List[dict] = []


# ---------- Auth ----------

@app.post("/api/auth/register", status_code=201)
def register(payload: RegisterRequest):
    if str(payload.email).strip().lower() == DEMO_EMAIL:
        raise HTTPException(status_code=409, detail="This email is reserved for the demo account.")
    user_id = db_create_user(payload.business_name.strip(), str(payload.email), payload.password)
    if user_id is None:
        raise HTTPException(status_code=409, detail="An account with this email already exists.")
    token = db_create_session(user_id)
    return {"token": token, "business_name": payload.business_name, "email": payload.email}


@app.post("/api/auth/login")
def login(payload: LoginRequest):
    if str(payload.email).strip().lower() == DEMO_EMAIL and payload.password == DEMO_PASSWORD:
        # Demo sessions are purely in-memory — no row is ever written to Postgres.
        token = secrets.token_urlsafe(32)
        DEMO_SESSIONS[token] = datetime.datetime.now() + datetime.timedelta(days=SESSION_LIFETIME_DAYS)
        demo = load_demo_data()
        return {"token": token, "business_name": demo["business_name"], "email": DEMO_EMAIL}

    user = db_verify_login(str(payload.email), payload.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Incorrect email or password.")
    token = db_create_session(user["id"])
    return {"token": token, "business_name": user["business_name"], "email": user["email"]}


@app.post("/api/auth/logout")
def logout(authorization: Optional[str] = Header(None)):
    if authorization and authorization.startswith("Bearer "):
        token = authorization[len("Bearer "):].strip()
        DEMO_SESSIONS.pop(token, None)
        db_delete_session(token)
    return {"ok": True}


@app.get("/api/auth/me")
def me(user: dict = Depends(get_current_user)):
    return {"business_name": user["business_name"], "email": user["email"]}


# ---------- Home / summary ----------

@app.get("/api/summary")
def get_summary(user_id: int = Depends(uid)):
    return get_home_summary(user_id)


# ---------- Customers ----------

@app.get("/api/customers")
def list_customers(user_id: int = Depends(uid)):
    return get_all_customers(user_id)


@app.post("/api/customers", status_code=201)
def create_customer(payload: CustomerCreate, user_id: int = Depends(uid)):
    block_if_demo(user_id)
    cid = db_add_customer(user_id, payload.name.strip(), payload.mobile.strip())
    if cid is None:
        raise HTTPException(status_code=409, detail="A customer with this mobile number already exists.")
    return {"id": cid, "name": payload.name, "mobile": payload.mobile}


@app.get("/api/customers/outstanding")
def outstanding_balances(user_id: int = Depends(uid)):
    return get_outstanding_balances(user_id)


@app.get("/api/customers/payment-history")
def payment_history(user_id: int = Depends(uid)):
    return get_customer_payment_history(user_id)


# ---------- Products / stock ----------

@app.get("/api/products")
def list_products(user_id: int = Depends(uid)):
    return get_all_products(user_id)


@app.get("/api/products/low-stock")
def low_stock_products(user_id: int = Depends(uid)):
    return get_low_stock_products(user_id)


@app.post("/api/products", status_code=201)
def create_product(payload: ProductCreate, user_id: int = Depends(uid)):
    block_if_demo(user_id)
    pid = db_add_product(user_id, payload.name.strip(), payload.cost_price, payload.sell_price,
                         payload.stock_qty, payload.reorder_level, payload.image_data)
    warning = payload.sell_price < payload.cost_price
    result = {"id": pid, **payload.dict()}
    if warning:
        result["warning"] = "Selling below cost."
    return result


@app.put("/api/products/{product_id}")
def update_product(product_id: int, payload: ProductUpdate, user_id: int = Depends(uid)):
    block_if_demo(user_id)
    try:
        updated = db_update_product(user_id, product_id, payload.name.strip(), payload.cost_price,
                                    payload.sell_price, payload.stock_qty, payload.reorder_level,
                                    payload.image_data)
    except KeyError:
        raise HTTPException(status_code=404, detail="Product not found")
    return with_image_data(updated)


@app.delete("/api/products/{product_id}")
def delete_product(product_id: int, user_id: int = Depends(uid)):
    block_if_demo(user_id)
    try:
        db_delete_product(user_id, product_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Product not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@app.post("/api/products/{product_id}/restock")
def restock_product(product_id: int, payload: RestockRequest, user_id: int = Depends(uid)):
    block_if_demo(user_id)
    try:
        db_update_stock(user_id, product_id, payload.qty)
    except KeyError:
        raise HTTPException(status_code=404, detail="Product not found")
    return {"ok": True}


# ---------- Billing ----------

@app.get("/api/bills")
def list_bills(start_date: Optional[str] = None, end_date: Optional[str] = None,
               customer_id: Optional[int] = None, user_id: int = Depends(uid)):
    return get_bills(user_id, start_date, end_date, customer_id)


@app.post("/api/bills", status_code=201)
def create_bill(payload: BillCreate, user_id: int = Depends(uid)):
    block_if_demo(user_id)
    items = [i.dict() for i in payload.items]
    total = sum(i["quantity"] * i["price_each"] for i in items)
    balance_due = round(total - payload.paid_amount, 2)

    if payload.customer_id is None and balance_due > 0:
        raise HTTPException(
            status_code=400,
            detail="A balance due can't be tracked for a walk-in customer with no record. "
                   "Register the customer first, or collect full payment.",
        )
    try:
        bill_id = db_create_bill(user_id, payload.customer_id, items, payload.paid_amount, payload.payment_mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"id": bill_id, "total_amount": total, "balance_due": balance_due}


@app.get("/api/bills/{bill_id}/items")
def bill_items(bill_id: int, user_id: int = Depends(uid)):
    try:
        return get_bill_items(user_id, bill_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Bill not found")


@app.post("/api/bills/{bill_id}/payments", status_code=201)
def record_payment(bill_id: int, payload: PaymentCreate, user_id: int = Depends(uid)):
    block_if_demo(user_id)
    try:
        db_add_payment(user_id, bill_id, payload.amount, payload.mode)
    except KeyError:
        raise HTTPException(status_code=404, detail="Bill not found")
    return {"ok": True}


# ---------- Sales / dashboard ----------

@app.get("/api/sales/summary")
def sales_summary(start_date: str, end_date: str, customer_id: Optional[int] = None,
                   user_id: int = Depends(uid)):
    return get_sales_summary(user_id, start_date, end_date)


@app.get("/api/dashboard/profit-loss")
def profit_loss(start_date: str, end_date: str, user_id: int = Depends(uid)):
    return get_profit_loss(user_id, start_date, end_date)


@app.get("/api/dashboard/product-ranking")
def product_ranking(start_date: str, end_date: str, user_id: int = Depends(uid)):
    return get_product_sales_ranking(user_id, start_date, end_date)


# ---------- AI (Groq) ----------

@app.post("/api/ai/sales-insights")
def ai_sales_insights(start_date: str, end_date: str, user_id: int = Depends(uid)):
    revenue, cost, profit = get_profit_loss(user_id, start_date, end_date).values()
    ranking = get_product_sales_ranking(user_id, start_date, end_date)
    top = [p for p in ranking if p["units_sold"] > 0][:5]
    slow = [p for p in ranking if p["units_sold"] == 0]
    return {"insight": analyze_sales_and_dashboard(revenue, cost, profit, top, slow)}


@app.post("/api/ai/stock-insights")
def ai_stock_insights(user_id: int = Depends(uid)):
    low = get_low_stock_products(user_id)
    all_products = get_all_products(user_id)
    return {"insight": analyze_stock(low, all_products)}


@app.post("/api/ai/payment-behavior")
def ai_payment_behavior(user_id: int = Depends(uid)):
    history = get_customer_payment_history(user_id)
    return {"insight": analyze_customer_payment_behavior(history)}


@app.post("/api/ai/coach")
def ai_coach(payload: CoachMessage, user_id: int = Depends(uid)):
    return {"text": ai_coach_reply(user_id, payload.message, payload.history)}


@app.get("/api/ai/daily-brief")
def ai_daily_brief(refresh: bool = False, user_id: int = Depends(uid)):
    """Returns today's AI brief, generated once per day and cached — pass ?refresh=true
    to force a fresh one (e.g. after new sales come in). The demo account always gets a
    freshly generated brief since there's nowhere to cache it against a real user row."""
    today = str(datetime.date.today())
    if is_demo(user_id):
        brief = generate_daily_brief(user_id)
        return {"date": today, "insight": brief, "generated_at": None, "cached": False}

    if not refresh:
        cached = db_get_daily_brief(user_id, today)
        if cached:
            return {"date": today, "insight": cached["content"], "generated_at": cached["created_at"], "cached": True}
    brief = generate_daily_brief(user_id)
    db_save_daily_brief(user_id, today, brief)
    return {"date": today, "insight": brief, "generated_at": None, "cached": False}
