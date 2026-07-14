import secrets
import sqlite3
import os
from datetime import datetime, timezone
from typing import Optional, Tuple

DB_PATH = os.getenv("DB_PATH", "/data/clip_api.db")

TIER_LIMITS = {
    "developer":    500_000,
    "professional": 2_500_000,
    "enterprise":   10_000_000,
}

def _get_db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = _get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS customers (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            stripe_customer_id   TEXT UNIQUE NOT NULL,
            stripe_subscription_id TEXT,
            email                TEXT NOT NULL,
            tier                 TEXT NOT NULL DEFAULT 'developer',
            status               TEXT NOT NULL DEFAULT 'active',
            created_at           TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS api_keys (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            key         TEXT UNIQUE NOT NULL,
            customer_id INTEGER NOT NULL,
            created_at  TEXT NOT NULL,
            revoked     INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (customer_id) REFERENCES customers(id)
        );
        CREATE TABLE IF NOT EXISTS usage (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            api_key     TEXT NOT NULL,
            month       TEXT NOT NULL,
            call_count  INTEGER NOT NULL DEFAULT 0,
            UNIQUE(api_key, month)
        );
    """)
    conn.commit()
    conn.close()

def generate_api_key() -> str:
    return "clip_" + secrets.token_urlsafe(32)

def create_customer(stripe_customer_id: str, email: str,
                    tier: str, subscription_id: str) -> Tuple[int, str]:
    conn = _get_db()
    now = datetime.now(timezone.utc).isoformat()
    cursor = conn.execute(
        """INSERT OR REPLACE INTO customers
           (stripe_customer_id, stripe_subscription_id, email, tier, status, created_at)
           VALUES (?, ?, ?, ?, 'active', ?)""",
        (stripe_customer_id, subscription_id, email, tier, now)
    )
    customer_id = cursor.lastrowid
    api_key = generate_api_key()
    conn.execute(
        "INSERT INTO api_keys (key, customer_id, created_at) VALUES (?, ?, ?)",
        (api_key, customer_id, now)
    )
    conn.commit()
    conn.close()
    return customer_id, api_key

def validate_api_key(api_key: str) -> Optional[dict]:
    conn = _get_db()
    row = conn.execute("""
        SELECT ak.key, c.tier, c.status, c.email, c.stripe_customer_id
        FROM api_keys ak
        JOIN customers c ON ak.customer_id = c.id
        WHERE ak.key = ? AND ak.revoked = 0 AND c.status = 'active'
    """, (api_key,)).fetchone()
    conn.close()
    return dict(row) if row else None

def check_and_increment_usage(api_key: str, tier: str) -> Tuple[bool, int, int]:
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    limit = TIER_LIMITS.get(tier, 500_000)
    conn = _get_db()
    conn.execute(
        "INSERT OR IGNORE INTO usage (api_key, month, call_count) VALUES (?, ?, 0)",
        (api_key, month)
    )
    row = conn.execute(
        "SELECT call_count FROM usage WHERE api_key = ? AND month = ?",
        (api_key, month)
    ).fetchone()
    current = row["call_count"] if row else 0
    if current >= limit:
        conn.close()
        return False, current, limit
    conn.execute(
        "UPDATE usage SET call_count = call_count + 1 WHERE api_key = ? AND month = ?",
        (api_key, month)
    )
    conn.commit()
    conn.close()
    return True, current + 1, limit

def update_customer_status(stripe_customer_id: str, status: str):
    conn = _get_db()
    conn.execute(
        "UPDATE customers SET status = ? WHERE stripe_customer_id = ?",
        (status, stripe_customer_id)
    )
    conn.commit()
    conn.close()

def get_usage_stats(api_key: str) -> dict:
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    conn = _get_db()
    customer = conn.execute("""
        SELECT c.tier, c.email, c.status
        FROM api_keys ak JOIN customers c ON ak.customer_id = c.id
        WHERE ak.key = ?
    """, (api_key,)).fetchone()
    usage = conn.execute(
        "SELECT call_count FROM usage WHERE api_key = ? AND month = ?",
        (api_key, month)
    ).fetchone()
    conn.close()
    if not customer:
        return {}
    limit = TIER_LIMITS.get(customer["tier"], 500_000)
    current = usage["call_count"] if usage else 0
    return {
        "tier": customer["tier"],
        "status": customer["status"],
        "month": month,
        "calls_used": current,
        "calls_limit": limit,
        "calls_remaining": max(0, limit - current),
    }
