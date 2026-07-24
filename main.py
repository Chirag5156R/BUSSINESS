"""
Ledgerly API — FastAPI backend for the billing / sales / stock / customers app.

This replaces app1.py (Streamlit). Same SQLite schema and business logic,
now exposed as a JSON REST API that any frontend (the included index.html,
or something else) can call.

Run with:
    pip install -r requirements.txt
    export GROQ_API_KEY=gsk_...          # get one free at https://console.groq.com
    uvicorn main:app --reload --port 8000
"""

import os
import sqlite3
import datetime
from contextlib import contextmanager
from typing import Optional, List

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

app = FastAPI()

# --- ADD CORS SETUP HERE ---
origins = [
    "http://localhost:3000",      # Your local frontend development URL (e.g., React)
    "http://127.0.0.1:5500",      # Your local HTML/Live Server URL
    "hhttps://bussiness-fblq.onrender.com"  
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,        # Allows these specific sites to talk to your backend
    allow_credentials=True,
    allow_methods=["*"],          # Allows all HTTP methods (GET, POST, etc.)
    allow_headers=["*"],          # Allows all headers
)

# ===========================================================================
# SECTION 1: DATABASE LAYER (same schema as the original Streamlit app)
# ===========================================================================

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "expense.db")


@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                mobile TEXT UNIQUE,
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                image_path TEXT,
                cost_price REAL NOT NULL,
                sell_price REAL NOT NULL,
                stock_qty INTEGER NOT NULL DEFAULT 0,
                reorder_level INTEGER NOT NULL DEFAULT 5,
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER,
                bill_date TEXT DEFAULT (datetime('now', 'localtime')),
                total_amount REAL NOT NULL DEFAULT 0,
                paid_amount REAL NOT NULL DEFAULT 0,
                balance_due REAL NOT NULL DEFAULT 0,
                payment_mode TEXT,
                FOREIGN KEY (customer_id) REFERENCES customers(id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bill_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bill_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL,
                price_each REAL NOT NULL,
                cost_each REAL NOT NULL,
                FOREIGN KEY (bill_id) REFERENCES bills(id),
                FOREIGN KEY (product_id) REFERENCES products(id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bill_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                mode TEXT NOT NULL,
                payment_date TEXT DEFAULT (datetime('now', 'localtime')),
                FOREIGN KEY (bill_id) REFERENCES bills(id)
            )
        """)
        conn.commit()


def row_to_dict(row) -> dict:
    return dict(row) if row is not None else None


def rows_to_list(rows) -> List[dict]:
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------

def db_add_customer(name, mobile):
    with get_connection() as conn:
        try:
            cur = conn.execute("INSERT INTO customers (name, mobile) VALUES (?, ?)", (name, mobile))
            conn.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None


def db_get_all_customers():
    with get_connection() as conn:
        return rows_to_list(conn.execute("SELECT * FROM customers ORDER BY name").fetchall())


def db_get_outstanding_balances():
    with get_connection() as conn:
        return rows_to_list(conn.execute("""
            SELECT customers.id, customers.name, customers.mobile,
                   SUM(bills.balance_due) AS total_due,
                   COUNT(bills.id) AS bill_count
            FROM bills JOIN customers ON bills.customer_id = customers.id
            WHERE bills.balance_due > 0
            GROUP BY customers.id
            ORDER BY total_due DESC
        """).fetchall())


def db_get_customer_payment_history():
    with get_connection() as conn:
        return rows_to_list(conn.execute("""
            SELECT customers.id, customers.name, customers.mobile,
                   COUNT(bills.id) AS total_bills,
                   SUM(bills.total_amount) AS total_billed,
                   SUM(bills.paid_amount) AS total_paid,
                   SUM(bills.balance_due) AS total_due,
                   SUM(CASE WHEN bills.balance_due > 0 THEN 1 ELSE 0 END) AS unpaid_bill_count
            FROM customers LEFT JOIN bills ON customers.id = bills.customer_id
            GROUP BY customers.id
            HAVING total_bills > 0
            ORDER BY total_due DESC
        """).fetchall())


# ---------------------------------------------------------------------------
# Products / stock
# ---------------------------------------------------------------------------

def db_add_product(name, cost_price, sell_price, stock_qty, reorder_level=5):
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO products (name, cost_price, sell_price, stock_qty, reorder_level)
               VALUES (?, ?, ?, ?, ?)""",
            (name, cost_price, sell_price, stock_qty, reorder_level),
        )
        conn.commit()
        return cur.lastrowid


def db_update_stock(product_id, qty_change):
    with get_connection() as conn:
        cur = conn.execute("UPDATE products SET stock_qty = stock_qty + ? WHERE id = ?", (qty_change, product_id))
        conn.commit()
        if cur.rowcount == 0:
            raise KeyError("product not found")


def db_get_all_products():
    with get_connection() as conn:
        return rows_to_list(conn.execute("SELECT * FROM products ORDER BY name").fetchall())


def db_get_low_stock_products():
    with get_connection() as conn:
        return rows_to_list(conn.execute(
            "SELECT * FROM products WHERE stock_qty <= reorder_level ORDER BY stock_qty"
        ).fetchall())


# ---------------------------------------------------------------------------
# Billing
# ---------------------------------------------------------------------------

def db_create_bill(customer_id, items, paid_amount, payment_mode):
    with get_connection() as conn:
        total_amount = sum(i["quantity"] * i["price_each"] for i in items)
        balance_due = round(total_amount - paid_amount, 2)

        cur = conn.execute(
            """INSERT INTO bills (customer_id, total_amount, paid_amount, balance_due, payment_mode)
               VALUES (?, ?, ?, ?, ?)""",
            (customer_id, total_amount, paid_amount, balance_due, payment_mode),
        )
        bill_id = cur.lastrowid

        for item in items:
            prod = conn.execute("SELECT stock_qty FROM products WHERE id = ?", (item["product_id"],)).fetchone()
            if prod is None:
                raise ValueError(f"Product {item['product_id']} not found")
            if item["quantity"] > prod["stock_qty"]:
                raise ValueError(f"Not enough stock for product {item['product_id']}")

            conn.execute(
                """INSERT INTO bill_items (bill_id, product_id, quantity, price_each, cost_each)
                   VALUES (?, ?, ?, ?, ?)""",
                (bill_id, item["product_id"], item["quantity"], item["price_each"], item["cost_each"]),
            )
            conn.execute("UPDATE products SET stock_qty = stock_qty - ? WHERE id = ?",
                         (item["quantity"], item["product_id"]))

        if paid_amount > 0:
            conn.execute("INSERT INTO payments (bill_id, amount, mode) VALUES (?, ?, ?)",
                         (bill_id, paid_amount, payment_mode))

        conn.commit()
        return bill_id


def db_add_payment(bill_id, amount, mode):
    with get_connection() as conn:
        bill = conn.execute("SELECT balance_due FROM bills WHERE id = ?", (bill_id,)).fetchone()
        if bill is None:
            raise KeyError("bill not found")
        conn.execute("INSERT INTO payments (bill_id, amount, mode) VALUES (?, ?, ?)", (bill_id, amount, mode))
        conn.execute(
            "UPDATE bills SET paid_amount = paid_amount + ?, balance_due = balance_due - ? WHERE id = ?",
            (amount, amount, bill_id),
        )
        conn.commit()


def db_get_bills(start_date=None, end_date=None, customer_id=None):
    with get_connection() as conn:
        query = """
            SELECT bills.*, customers.name AS customer_name, customers.mobile AS customer_mobile
            FROM bills LEFT JOIN customers ON bills.customer_id = customers.id
            WHERE 1=1
        """
        params = []
        if start_date:
            query += " AND date(bill_date) >= date(?)"
            params.append(start_date)
        if end_date:
            query += " AND date(bill_date) <= date(?)"
            params.append(end_date)
        if customer_id:
            query += " AND bills.customer_id = ?"
            params.append(customer_id)
        query += " ORDER BY bills.bill_date DESC"
        return rows_to_list(conn.execute(query, params).fetchall())


def db_get_bill_items(bill_id):
    with get_connection() as conn:
        return rows_to_list(conn.execute(
            """SELECT bill_items.*, products.name AS product_name
               FROM bill_items JOIN products ON bill_items.product_id = products.id
               WHERE bill_id = ?""",
            (bill_id,),
        ).fetchall())


# ---------------------------------------------------------------------------
# Sales / dashboard analytics
# ---------------------------------------------------------------------------

def db_get_sales_summary(start_date, end_date):
    with get_connection() as conn:
        row = conn.execute("""
            SELECT COALESCE(SUM(total_amount), 0) AS revenue,
                   COALESCE(SUM(paid_amount), 0) AS collected,
                   COALESCE(SUM(balance_due), 0) AS outstanding,
                   COUNT(*) AS bill_count
            FROM bills
            WHERE date(bill_date) BETWEEN date(?) AND date(?)
        """, (start_date, end_date)).fetchone()
        return dict(row)


def db_get_profit_loss(start_date, end_date):
    with get_connection() as conn:
        row = conn.execute("""
            SELECT
                COALESCE(SUM(bill_items.quantity * bill_items.price_each), 0) AS revenue,
                COALESCE(SUM(bill_items.quantity * bill_items.cost_each), 0) AS cost
            FROM bill_items JOIN bills ON bill_items.bill_id = bills.id
            WHERE date(bills.bill_date) BETWEEN date(?) AND date(?)
        """, (start_date, end_date)).fetchone()
        revenue = row["revenue"] or 0
        cost = row["cost"] or 0
        return {"revenue": revenue, "cost": cost, "profit": revenue - cost}


def db_get_product_sales_ranking(start_date, end_date):
    with get_connection() as conn:
        return rows_to_list(conn.execute("""
            SELECT products.id, products.name,
                   COALESCE(SUM(bill_items.quantity), 0) AS units_sold,
                   COALESCE(SUM(bill_items.quantity * bill_items.price_each), 0) AS revenue
            FROM products
            LEFT JOIN bill_items ON products.id = bill_items.product_id
            LEFT JOIN bills ON bill_items.bill_id = bills.id
                AND date(bills.bill_date) BETWEEN date(?) AND date(?)
            GROUP BY products.id
            ORDER BY units_sold DESC
        """, (start_date, end_date)).fetchall())


def db_get_home_summary():
    with get_connection() as conn:
        total_customers = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        total_products = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        total_bills = conn.execute("SELECT COUNT(*) FROM bills").fetchone()[0]
        total_due = conn.execute("SELECT COALESCE(SUM(balance_due),0) FROM bills").fetchone()[0]
        return {
            "total_customers": total_customers,
            "total_products": total_products,
            "total_bills": total_bills,
            "total_due": total_due,
        }


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


def ai_coach_reply(message: str, history: list):
    """Freeform AI Coach chat, grounded in the shop's live numbers."""
    today = datetime.date.today()
    month_start = today.replace(day=1)
    revenue, cost, profit = db_get_profit_loss(str(month_start), str(today)).values()
    home = db_get_home_summary()

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


# ---------- Pydantic models ----------

class CustomerCreate(BaseModel):
    name: str
    mobile: str


class ProductCreate(BaseModel):
    name: str
    cost_price: float = Field(ge=0)
    sell_price: float = Field(ge=0)
    stock_qty: int = Field(ge=0, default=0)
    reorder_level: int = Field(ge=0, default=5)


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


# ---------- Home / summary ----------

@app.get("/api/summary")
def get_summary():
    return db_get_home_summary()


# ---------- Customers ----------

@app.get("/api/customers")
def list_customers():
    return db_get_all_customers()


@app.post("/api/customers", status_code=201)
def create_customer(payload: CustomerCreate):
    cid = db_add_customer(payload.name.strip(), payload.mobile.strip())
    if cid is None:
        raise HTTPException(status_code=409, detail="A customer with this mobile number already exists.")
    return {"id": cid, "name": payload.name, "mobile": payload.mobile}


@app.get("/api/customers/outstanding")
def outstanding_balances():
    return db_get_outstanding_balances()


@app.get("/api/customers/payment-history")
def payment_history():
    return db_get_customer_payment_history()


# ---------- Products / stock ----------

@app.get("/api/products")
def list_products():
    return db_get_all_products()


@app.get("/api/products/low-stock")
def low_stock_products():
    return db_get_low_stock_products()


@app.post("/api/products", status_code=201)
def create_product(payload: ProductCreate):
    if payload.sell_price < payload.cost_price:
        pid = db_add_product(payload.name.strip(), payload.cost_price, payload.sell_price,
                             payload.stock_qty, payload.reorder_level)
        return {"id": pid, "warning": "Selling below cost.", **payload.dict()}
    pid = db_add_product(payload.name.strip(), payload.cost_price, payload.sell_price,
                         payload.stock_qty, payload.reorder_level)
    return {"id": pid, **payload.dict()}


@app.post("/api/products/{product_id}/restock")
def restock_product(product_id: int, payload: RestockRequest):
    try:
        db_update_stock(product_id, payload.qty)
    except KeyError:
        raise HTTPException(status_code=404, detail="Product not found")
    return {"ok": True}


# ---------- Billing ----------

@app.get("/api/bills")
def list_bills(start_date: Optional[str] = None, end_date: Optional[str] = None,
               customer_id: Optional[int] = None):
    return db_get_bills(start_date, end_date, customer_id)


@app.post("/api/bills", status_code=201)
def create_bill(payload: BillCreate):
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
        bill_id = db_create_bill(payload.customer_id, items, payload.paid_amount, payload.payment_mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"id": bill_id, "total_amount": total, "balance_due": balance_due}


@app.get("/api/bills/{bill_id}/items")
def bill_items(bill_id: int):
    return db_get_bill_items(bill_id)


@app.post("/api/bills/{bill_id}/payments", status_code=201)
def record_payment(bill_id: int, payload: PaymentCreate):
    try:
        db_add_payment(bill_id, payload.amount, payload.mode)
    except KeyError:
        raise HTTPException(status_code=404, detail="Bill not found")
    return {"ok": True}


# ---------- Sales / dashboard ----------

@app.get("/api/sales/summary")
def sales_summary(start_date: str, end_date: str, customer_id: Optional[int] = None):
    return db_get_sales_summary(start_date, end_date)


@app.get("/api/dashboard/profit-loss")
def profit_loss(start_date: str, end_date: str):
    return db_get_profit_loss(start_date, end_date)


@app.get("/api/dashboard/product-ranking")
def product_ranking(start_date: str, end_date: str):
    return db_get_product_sales_ranking(start_date, end_date)


# ---------- AI (Groq) ----------

@app.post("/api/ai/sales-insights")
def ai_sales_insights(start_date: str, end_date: str):
    revenue, cost, profit = db_get_profit_loss(start_date, end_date).values()
    ranking = db_get_product_sales_ranking(start_date, end_date)
    top = [p for p in ranking if p["units_sold"] > 0][:5]
    slow = [p for p in ranking if p["units_sold"] == 0]
    return {"insight": analyze_sales_and_dashboard(revenue, cost, profit, top, slow)}


@app.post("/api/ai/stock-insights")
def ai_stock_insights():
    low = db_get_low_stock_products()
    all_products = db_get_all_products()
    return {"insight": analyze_stock(low, all_products)}


@app.post("/api/ai/payment-behavior")
def ai_payment_behavior():
    history = db_get_customer_payment_history()
    return {"insight": analyze_customer_payment_behavior(history)}


@app.post("/api/ai/coach")
def ai_coach(payload: CoachMessage):
    return {"text": ai_coach_reply(payload.message, payload.history)}
