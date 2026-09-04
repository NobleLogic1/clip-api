import logging
import os
import secrets
import sqlite3
import threading
from functools import wraps
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple

from ..config import DB_PATH, TIER_LIMITS, VALID_TIERS

logger = logging.getLogger(__name__)

FREE_KEY_IP_LIMIT = 3
VERIFY_TOKEN_TTL_HOURS = 1
_DB_CONN: Optional[sqlite3.Connection] = None
_DB_CONN_PATH: Optional[str] = None
_DB_LOCK = threading.RLock()
_SCHEMA_INITIALIZED = False


def _with_db_lock(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        with _DB_LOCK:
            return func(*args, **kwargs)

    return wrapper


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
    global _DB_CONN, _DB_CONN_PATH, _SCHEMA_INITIALIZED
    target = _resolve_db_path()
    directory = os.path.dirname(target) or "."
    os.makedirs(directory, exist_ok=True)
    with _DB_LOCK:
        if _DB_CONN is not None and _DB_CONN_PATH == target:
            return _DB_CONN
        if _DB_CONN is not None:
            try:
                _DB_CONN.close()
            except sqlite3.Error:
                pass
        conn = sqlite3.connect(target, timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode=WAL")
        _DB_CONN = conn
        _DB_CONN_PATH = target
        _SCHEMA_INITIALIZED = False
        return conn


def close_db() -> None:
    global _DB_CONN, _DB_CONN_PATH, _SCHEMA_INITIALIZED
    with _DB_LOCK:
        if _DB_CONN is not None:
            try:
                _DB_CONN.close()
            except sqlite3.Error:
                pass
        _DB_CONN = None
        _DB_CONN_PATH = None
        _SCHEMA_INITIALIZED = False


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
    global _SCHEMA_INITIALIZED
    if _SCHEMA_INITIALIZED:
        return
    with _DB_LOCK:
        if _SCHEMA_INITIALIZED:
            return
        _ensure_schema_locked(conn)
        _SCHEMA_INITIALIZED = True


def _ensure_schema_locked(conn: sqlite3.Connection) -> None:
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
                created_at TEXT NOT NULL,
                signup_ip TEXT
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
            CREATE TABLE IF NOT EXISTS email_verifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                token TEXT UNIQUE NOT NULL,
                client_ip TEXT,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                used INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        conn.commit()
    except sqlite3.Error as exc:
        logger.exception("Database schema initialization failed: %s", exc)
        raise

    columns = {}
    for table in ("customers", "api_keys", "usage", "email_verifications"):
        try:
            columns[table] = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
        except sqlite3.Error:
            columns[table] = []
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
        if "signup_ip" not in columns["customers"]:
            conn.execute("ALTER TABLE customers ADD COLUMN signup_ip TEXT")
        if "revoked" not in columns["api_keys"]:
            conn.execute("ALTER TABLE api_keys ADD COLUMN revoked INTEGER DEFAULT 0")
        if "call_count" not in columns["usage"]:
            conn.execute("ALTER TABLE usage ADD COLUMN call_count INTEGER DEFAULT 0")
        _safe_commit(conn)
    except sqlite3.OperationalError as exc:
        logger.warning("Schema migration skipped: %s", exc)


@_with_db_lock
def init_db() -> None:
    try:
        conn = _get_db()
        _ensure_schema(conn)
    except sqlite3.Error as exc:
        logger.exception("Database initialization failed: %s", exc)


def generate_api_key() -> str:
    return "clip_" + secrets.token_urlsafe(32)


@_with_db_lock
def get_existing_free_key(email: str) -> Optional[dict]:
    """Return existing free key + usage for email, or None."""
    email = email.strip().lower()
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    free_limit = TIER_LIMITS.get("free", 9_000)
    conn = None
    try:
        conn = _get_db()
        _ensure_schema(conn)
        existing = conn.execute(
            """
            SELECT c.id, ak.key
            FROM customers c
            JOIN api_keys ak ON ak.customer_id = c.id
            WHERE c.email = ? AND c.tier = 'free' AND c.status = 'active' AND ak.revoked = 0
            ORDER BY c.id DESC LIMIT 1
            """,
            (email,),
        ).fetchone()
        if not existing:
            return None
        usage_row = conn.execute(
            "SELECT call_count FROM usage WHERE api_key = ? AND month = ?",
            (existing["key"], month),
        ).fetchone()
        used = usage_row["call_count"] if usage_row else 0
        return {
            "customer_id": existing["id"],
            "api_key": existing["key"],
            "calls_used": used,
            "calls_limit": free_limit,
            "calls_remaining": max(0, free_limit - used),
        }
    except sqlite3.Error as exc:
        logger.exception("Existing free key lookup failed: %s", exc)
        return None


@_with_db_lock
def create_verification_token(email: str, client_ip: Optional[str] = None) -> str:
    """
    Create a one-time verification token for free-key signup.
    Does NOT issue the API key yet — that happens on verify.
    """
    email = email.strip().lower()
    if not email or "@" not in email:
        raise ValueError("A valid email is required")

    now = datetime.now(timezone.utc)
    token = secrets.token_urlsafe(32)
    expires = (now + timedelta(hours=VERIFY_TOKEN_TTL_HOURS)).isoformat()

    conn = None
    try:
        conn = _get_db()
        _ensure_schema(conn)

        # IP rate limit on verification *requests* (not only completed keys)
        if client_ip:
            cutoff = (now - timedelta(hours=24)).isoformat()
            ip_count_row = conn.execute(
                """
                SELECT COUNT(*) AS cnt FROM email_verifications
                WHERE client_ip = ? AND created_at >= ?
                """,
                (client_ip, cutoff),
            ).fetchone()
            if ip_count_row and ip_count_row["cnt"] >= FREE_KEY_IP_LIMIT * 2:
                raise ValueError(
                    f"Too many free key requests from this network. "
                    f"Please try again later or upgrade to a paid plan."
                )

        # Invalidate prior unused tokens for this email
        conn.execute(
            "UPDATE email_verifications SET used = 1 WHERE email = ? AND used = 0",
            (email,),
        )
        conn.execute(
            """
            INSERT INTO email_verifications (email, token, client_ip, created_at, expires_at, used)
            VALUES (?, ?, ?, ?, ?, 0)
            """,
            (email, token, client_ip, now.isoformat(), expires),
        )
        if not _safe_commit(conn):
            raise RuntimeError("Failed to store verification token")
        return token
    except sqlite3.Error as exc:
        logger.exception("Verification token creation failed: %s", exc)
        raise


@_with_db_lock
def verify_and_create_free_key(token: str) -> Tuple[str, dict]:
    """
    Consume a verification token and issue (or reuse) a free API key.
    Returns (api_key, meta).
    """
    if not token or len(token) < 16:
        raise ValueError("Invalid verification token")

    now = datetime.now(timezone.utc)
    conn = None
    try:
        conn = _get_db()
        _ensure_schema(conn)

        row = conn.execute(
            "SELECT * FROM email_verifications WHERE token = ?",
            (token,),
        ).fetchone()
        if not row:
            raise ValueError("Invalid or unknown verification link")
        if row["used"]:
            raise ValueError("This verification link has already been used")
        if row["expires_at"] < now.isoformat():
            raise ValueError("This verification link has expired. Please request a new free key.")

        email = row["email"]
        client_ip = row["client_ip"]

        # Mark token used first (prevents double-click races)
        conn.execute("UPDATE email_verifications SET used = 1 WHERE id = ?", (row["id"],))
        if not _safe_commit(conn):
            raise RuntimeError("Failed to mark verification token used")

        # Issue key via existing create path
        customer_id, api_key, meta = create_free_key(email, client_ip=client_ip)
        return api_key, {**meta, "email": email}
    except sqlite3.Error as exc:
        logger.exception("Verify free key failed: %s", exc)
        raise


@_with_db_lock
def create_free_key(email: str, client_ip: Optional[str] = None) -> Tuple[int, str, dict]:
    """Create or return a Free-tier API key (called after email verification)."""
    if not email or "@" not in email:
        raise ValueError("A valid email is required to create a free key")

    email = email.strip().lower()
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    month = now.strftime("%Y-%m")
    free_limit = TIER_LIMITS.get("free", 9_000)

    conn = None
    try:
        conn = _get_db()
        _ensure_schema(conn)

        existing = conn.execute(
            """
            SELECT c.id, ak.key
            FROM customers c
            JOIN api_keys ak ON ak.customer_id = c.id
            WHERE c.email = ? AND c.tier = 'free' AND c.status = 'active' AND ak.revoked = 0
            ORDER BY c.id DESC LIMIT 1
            """,
            (email,),
        ).fetchone()

        if existing:
            usage_row = conn.execute(
                "SELECT call_count FROM usage WHERE api_key = ? AND month = ?",
                (existing["key"], month),
            ).fetchone()
            used = usage_row["call_count"] if usage_row else 0
            remaining = max(0, free_limit - used)
            return existing["id"], existing["key"], {
                "reused": True,
                "calls_used": used,
                "calls_limit": free_limit,
                "calls_remaining": remaining,
            }

        if client_ip:
            cutoff = (now - timedelta(hours=24)).isoformat()
            ip_count_row = conn.execute(
                """
                SELECT COUNT(*) AS cnt FROM customers
                WHERE tier = 'free' AND signup_ip = ? AND created_at >= ?
                """,
                (client_ip, cutoff),
            ).fetchone()
            if ip_count_row and ip_count_row["cnt"] >= FREE_KEY_IP_LIMIT:
                raise ValueError(
                    f"Too many free keys created from this network. "
                    f"Limit is {FREE_KEY_IP_LIMIT} new free keys per 24 hours. "
                    f"Upgrade to a paid plan for more capacity."
                )

        synthetic_id = f"free_{secrets.token_urlsafe(16)}"
        cursor = conn.execute(
            """
            INSERT INTO customers
                (stripe_customer_id, stripe_subscription_id, email, tier, status, created_at, signup_ip)
            VALUES (?, NULL, ?, 'free', 'active', ?, ?)
            """,
            (synthetic_id, email, now_iso, client_ip),
        )
        customer_id = cursor.lastrowid
        api_key = generate_api_key()
        conn.execute(
            "INSERT INTO api_keys (key, customer_id, created_at, revoked) VALUES (?, ?, ?, 0)",
            (api_key, customer_id, now_iso),
        )
        if not _safe_commit(conn):
            raise RuntimeError("Failed to commit free key creation")

        logger.info(
            "Free key created",
            extra={"event": "free_key.created", "context": {"customer_id": customer_id, "email": email, "ip": client_ip}},
        )
        return customer_id, api_key, {
            "reused": False,
            "calls_used": 0,
            "calls_limit": free_limit,
            "calls_remaining": free_limit,
        }
    except sqlite3.Error as exc:
        logger.exception("Free key creation failed: %s", exc)
        raise


@_with_db_lock
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
                SET stripe_subscription_id = ?, email = ?, tier = ?, status = 'active'
                WHERE id = ?
                """,
                (subscription_id, email or "unknown", normalized_tier, existing["id"]),
            )
            customer_id = existing["id"]
            existing_key = conn.execute(
                "SELECT key FROM api_keys WHERE customer_id = ? AND revoked = 0 ORDER BY id DESC LIMIT 1",
                (customer_id,),
            ).fetchone()
            if existing_key:
                api_key = existing_key["key"]
            else:
                api_key = generate_api_key()
                conn.execute(
                    "INSERT INTO api_keys (key, customer_id, created_at, revoked) VALUES (?, ?, ?, 0)",
                    (api_key, customer_id, now),
                )
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
        return customer_id, api_key
    except sqlite3.Error as exc:
        logger.exception("Customer creation failed: %s", exc)
        raise


@_with_db_lock
def validate_api_key(token_value: str) -> Optional[dict]:
    if not token_value or not isinstance(token_value, str):
        return None
    if not token_value.startswith("clip_") or len(token_value) < 20:
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
        if not row or row["revoked"] or row["status"] != "active":
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


@_with_db_lock
def check_and_increment_usage(usage_key: str, tier: str) -> Tuple[bool, int, int]:
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    normalized_tier = (tier or "developer").lower()
    limit = TIER_LIMITS.get(normalized_tier, 500_000)

    if not usage_key or not isinstance(usage_key, str):
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


@_with_db_lock
def update_customer_status(stripe_customer_id: str, status: str) -> bool:
    if not stripe_customer_id:
        return False
    conn = None
    try:
        conn = _get_db()
        _ensure_schema(conn)
        conn.execute(
            "UPDATE customers SET status = ? WHERE stripe_customer_id = ?",
            (status, stripe_customer_id),
        )
        return _safe_commit(conn)
    except sqlite3.Error as exc:
        logger.exception("Customer status update failed: %s", exc)
        return False


@_with_db_lock
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
            (token_value, month),
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
