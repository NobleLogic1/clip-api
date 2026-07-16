import logging
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from typing import Optional, Tuple

from ..config import DB_PATH, TIER_LIMITS, VALID_TIERS

logger = logging.getLogger(__name__)


def _resolve_db_path() -> str:
    target = DB_PATH
    if target.startswith("/data"):
        directory = os.path.dirname(target) or "."
        if not os.path.isdir(directory):
            try:
                os.makedirs(directory, exist_ok=True)
            except OSError:
                target = os.path.join("/tmp", os.path.basename(target))
        elif not os.access(directory, os.W_OK):
            target = os.path.join("/tmp", os.path.basename(target))

    if not target:
        target = os.path.join("/tmp", "clip_api.db")

    return target


def _get_db() -> sqlite3.Connection:
    target = _resolve_db_path()
    directory = os.path.dirname(target) or "."
    os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(target, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _safe_commit(conn: sqlite3.Connection) -> bool:
    try:
        conn.commit()
        return True
    except sqlite3.Error as exc:
        logger.exception("Database commit failed: %s", exc)
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        return False


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create tables and add missing columns defensively."""
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stripe_customer_id TEXT UNIQUE,
                stripe_subscription_id TEXT,
                email TEXT,
                tier TEXT NOT NULL DEFAULT 'developer',
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS api_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT UNIQUE NOT NULL,
                customer_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                revoked INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (customer_id) REFERENCES customers(id)
            );
            CREATE TABLE IF NOT EXISTS usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                api_key TEXT NOT NULL,
                month TEXT NOT NULL,
                call_count INTEGER NOT NULL DEFAULT 0,
                UNIQUE(api_key, month)
            );
            """
        )
        conn.commit()
    except sqlite3.Error as exc:
        logger.exception("Database schema initialization failed: %s", exc)
        raise

    columns = {}
    for table in ("customers", "api_keys", "usage"):
        columns[table] = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    try:
        if "stripe_customer_id" not in columns["customers"]:
            conn.execute("ALTER TABLE customers ADD COLUMN stripe_customer_id TEXT")
        if "stripe_subscription_id" not in columns["customers"]:
            conn.execute("ALTER TABLE customers ADD COLUMN stripe_subscription_id TEXT")
        if "email" not in columns["customers"]:
            conn.execute("ALTER TABLE customers ADD COLUMN email TEXT")
        if "tier" not in columns["customers"]:
            conn.execute("ALTER TABLE customers ADD COLUMN tier TEXT DEFAULT 'developer'")
        if "status" not in columns["customers"]:
            conn.execute("ALTER TABLE customers ADD COLUMN status TEXT DEFAULT 'active'")
        if "created_at" not in columns["customers"]:
            conn.execute("ALTER TABLE customers ADD COLUMN created_at TEXT")
        if "revoked" not in columns["api_keys"]:
            conn.execute("ALTER TABLE api_keys ADD COLUMN revoked INTEGER DEFAULT 0")
        if "call_count" not in columns["usage"]:
            conn.execute("ALTER TABLE usage ADD COLUMN call_count INTEGER DEFAULT 0")
        _safe_commit(conn)
    except sqlite3.OperationalError as exc:
        logger.warning("Schema migration skipped: %s", exc)


def init_db() -> None:
    conn = None
    try:
        conn = _get_db()
        _ensure_schema(conn)
    except sqlite3.Error as exc:
        logger.exception("Database initialization failed: %s", exc)
    finally:
        if conn is not None:
            conn.close()


def generate_api_key() -> str:
    return "clip_" + secrets.token_urlsafe(32)


def create_customer(stripe_customer_id: str, email: str, tier: str, subscription_id: str) -> Tuple[int, str]:
    if not stripe_customer_id:
        logger.warning("Skipping customer creation because stripe_customer_id is missing")
        return 0, ""

    normalized_tier = (tier or "developer").lower()
    if normalized_tier not in VALID_TIERS:
        logger.warning("Unknown tier '%s' received; defaulting to developer", tier)
        normalized_tier = "developer"

    conn = None
    try:
        conn = _get_db()
        _ensure_schema(conn)
        now = datetime.now(timezone.utc).isoformat()
        existing = conn.execute(
            "SELECT id FROM customers WHERE stripe_customer_id = ?",
            (stripe_customer_id,),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE customers
                SET stripe_subscription_id = ?, email = ?, tier = ?, status = 'active', created_at = ?
                WHERE id = ?
                """,
                (subscription_id, email or "unknown", normalized_tier, now, existing["id"]),
            )
            customer_id = existing["id"]
        else:
            cursor = conn.execute(
                """
                INSERT INTO customers (stripe_customer_id, stripe_subscription_id, email, tier, status, created_at)
                VALUES (?, ?, ?, ?, 'active', ?)
                """,
                (stripe_customer_id, subscription_id, email or "unknown", normalized_tier, now),
            )
            customer_id = cursor.lastrowid

        api_key = generate_api_key()
        conn.execute(
            "INSERT INTO api_keys (key, customer_id, created_at, revoked) VALUES (?, ?, ?, 0)",
            (api_key, customer_id, now),
        )
        if not _safe_commit(conn):
            raise RuntimeError("Failed to commit customer creation")
        logger.info(
            "Customer created or updated",
            extra={"event": "customer.created", "context": {"customer_id": customer_id, "stripe_customer_id": stripe_customer_id, "tier": normalized_tier}},
        )
        return customer_id, api_key
    except sqlite3.Error as exc:
        logger.exception("Customer creation failed: %s", exc)
        raise
    finally:
        if conn is not None:
            conn.close()


def validate_api_key(token_value: str) -> Optional[dict]:
    if not token_value or not isinstance(token_value, str):
        logger.warning("Rejected missing API key")
        return None
    if not token_value.startswith("clip_") or len(token_value) < 20:
        logger.warning("Rejected malformed API key", extra={"event": "auth.invalid_format", "context": {"reason": "malformed"}})
        return None

    conn = None
    try:
        conn = _get_db()
        _ensure_schema(conn)
        row = conn.execute(
            """
            SELECT ak.key, ak.revoked, c.tier, c.status, c.email, c.stripe_customer_id
            FROM api_keys ak
            JOIN customers c ON ak.customer_id = c.id
            WHERE ak.key = ?
            """,
            (token_value,),
        ).fetchone()
        if not row:
            logger.warning("API key not found", extra={"event": "auth.invalid_key", "context": {"reason": "missing"}})
            return None
        if row["revoked"]:
            logger.warning("Rejected revoked API key", extra={"event": "auth.revoked", "context": {"reason": "revoked"}})
            return None
        if row["status"] != "active":
            logger.warning("Rejected inactive customer for API key", extra={"event": "auth.inactive_customer", "context": {"status": row["status"]}})
            return None
        return {
            "key": row["key"],
            "tier": row["tier"],
            "status": row["status"],
            "email": row["email"],
            "stripe_customer_id": row["stripe_customer_id"],
        }
    except sqlite3.Error as exc:
        logger.exception("API key validation failed: %s", exc)
        return None
    finally:
        if conn is not None:
            conn.close()


def check_and_increment_usage(usage_key: str, tier: str) -> Tuple[bool, int, int]:
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    normalized_tier = (tier or "developer").lower()
    limit = TIER_LIMITS.get(normalized_tier, 500_000)

    if not token_value or not isinstance(token_value, str):
        logger.warning("Usage increment skipped because API key is missing")
        return False, 0, limit

    conn = None
    try:
        conn = _get_db()
        _ensure_schema(conn)
        conn.execute(
            "INSERT OR IGNORE INTO usage (api_key, month, call_count) VALUES (?, ?, 0)",
            (usage_key, month),
        )
        row = conn.execute(
            "SELECT call_count FROM usage WHERE api_key = ? AND month = ?",
            (usage_key, month),
        ).fetchone()
        current = row["call_count"] if row else 0
        if current >= limit:
            logger.warning(
                "Usage limit reached",
                extra={"event": "usage.limit_reached", "context": {"tier": normalized_tier, "month": month, "current": current, "limit": limit}},
            )
            return False, current, limit

        conn.execute(
            "UPDATE usage SET call_count = call_count + 1 WHERE api_key = ? AND month = ?",
            (usage_key, month),
        )
        if not _safe_commit(conn):
            raise RuntimeError("Failed to commit usage update")
        return True, current + 1, limit
    except sqlite3.Error as exc:
        logger.exception("Usage update failed: %s", exc)
        return False, 0, limit
    finally:
        if conn is not None:
            conn.close()


def update_customer_status(stripe_customer_id: str, status: str) -> bool:
    if not stripe_customer_id:
        logger.warning("Skipping customer status update because stripe_customer_id is missing")
        return False

    conn = None
    try:
        conn = _get_db()
        _ensure_schema(conn)
        conn.execute(
            "UPDATE customers SET status = ? WHERE stripe_customer_id = ?",
            (status, stripe_customer_id),
        )
        if not _safe_commit(conn):
            return False
        return True
    except sqlite3.Error as exc:
        logger.exception("Customer status update failed: %s", exc)
        return False
    finally:
        if conn is not None:
            conn.close()


def get_usage_stats(token_value: str) -> dict:
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    conn = None
    try:
        conn = _get_db()
        _ensure_schema(conn)
        customer = conn.execute(
            """
            SELECT c.tier, c.email, c.status
            FROM api_keys ak JOIN customers c ON ak.customer_id = c.id
            WHERE ak.key = ?
            """,
            (token_value,),
        ).fetchone()
        usage = conn.execute(
            "SELECT call_count FROM usage WHERE api_key = ? AND month = ?",
            (usage_key, month),
        ).fetchone()
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
    except sqlite3.Error as exc:
        logger.exception("Usage stats lookup failed: %s", exc)
        return {}
    finally:
        if conn is not None:
            conn.close()
