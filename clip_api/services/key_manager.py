import os
import sqlite3
import hashlib
import threading
from datetime import datetime
from typing import Optional, Tuple

DB_PATH = os.environ.get("CLIP_DB_PATH", "clip_maintained.db")

TIER_LIMITS = {
    "free": 100,
    "starter": 5000,
    "pro": 50000,
    "enterprise": 10**18,
}

DB_LOCK = threading.Lock()


class KeyManager:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.db = self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                api_key TEXT PRIMARY KEY,
                email TEXT,
                tier TEXT,
                stripe_customer_id TEXT,
                stripe_subscription_id TEXT,
                created_utc TEXT,
                requests_today INTEGER DEFAULT 0,
                total_requests INTEGER DEFAULT 0,
                last_reset_date TEXT
            )
        """)
        conn.commit()
        return conn

    def _utc_today(self) -> str:
        return datetime.utcnow().date().isoformat()

    def create_api_key(self, email: str, tier: str = "free",
                       stripe_cid: str = None, stripe_sid: str = None) -> str:
        api_key = f"clip_{hashlib.md5(email.encode()).hexdigest()[:16]}"
        now = datetime.utcnow().isoformat()
        today = self._utc_today()
        with DB_LOCK:
            with self.db:
                self.db.execute("""
                    INSERT OR REPLACE INTO users
                    (api_key, email, tier, stripe_customer_id,
                     stripe_subscription_id, created_utc, requests_today,
                     total_requests, last_reset_date)
                    VALUES (?, ?, ?, ?, ?, ?,
                        COALESCE((SELECT requests_today FROM users WHERE api_key=?),0),
                        COALESCE((SELECT total_requests FROM users WHERE api_key=?),0),
                        ?)
                """, (api_key, email, tier, stripe_cid, stripe_sid,
                      now, api_key, api_key, today))
        return api_key

    def validate_key(self, api_key: str) -> Optional[dict]:
        today = self._utc_today()
        with DB_LOCK:
            self.db.execute(
                "UPDATE users SET requests_today=0, last_reset_date=? "
                "WHERE api_key=? AND last_reset_date!=?",
                (today, api_key, today)
            )
            cur = self.db.execute(
                "SELECT api_key,email,tier,requests_today,total_requests "
                "FROM users WHERE api_key=?", (api_key,)
            )
            row = cur.fetchone()
        if not row:
            return None
        return {
            "api_key": row[0], "email": row[1], "tier": row[2],
            "requests_today": row[3], "total_requests": row[4],
        }

    def check_rate_limit(self, api_key: str) -> Tuple[bool, str]:
        user = self.validate_key(api_key)
        if not user:
            return False, "Invalid API key"
        limit = TIER_LIMITS.get(user["tier"], TIER_LIMITS["free"])
        if user["requests_today"] >= limit:
            return False, f"Rate limit exceeded. Upgrade to increase limits."
        return True, "OK"

    def increment_usage(self, api_key: str, n: int = 1):
        with DB_LOCK:
            with self.db:
                self.db.execute(
                    "UPDATE users SET requests_today=requests_today+?, "
                    "total_requests=total_requests+? WHERE api_key=?",
                    (n, n, api_key)
                )

    def remaining_requests(self, api_key: str) -> int:
        user = self.validate_key(api_key)
        if not user:
            return 0
        limit = TIER_LIMITS.get(user["tier"], 0)
        return limit - user["requests_today"]


key_manager = KeyManager()
