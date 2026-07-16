import os

# Database
DB_PATH = os.environ.get("CLIP_DB_PATH") or os.environ.get("DB_PATH") or "/data/clip_api.db"

# Image processing
MAX_IMAGE_BYTES = int(os.environ.get("MAX_IMAGE_BYTES", 5_000_000))
IMAGE_TIMEOUT = int(os.environ.get("IMAGE_TIMEOUT", 10))

# Model
CLIP_MODEL_ID = os.environ.get("CLIP_MODEL_ID", "openai/clip-vit-base-patch32")
MODEL_VARIANT = "clip-maintained-2026"

# Stripe Configuration
# Maps to Railway environment variables (can use either naming convention)
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

# Price IDs: Accept either STARTER/PRO or DEVELOPER/PROFESSIONAL naming
STRIPE_PRICE_STARTER = (
    os.environ.get("STRIPE_PRICE_STARTER") or
    os.environ.get("STRIPE_PRICE_DEVELOPER") or
    ""
)
STRIPE_PRICE_PRO = (
    os.environ.get("STRIPE_PRICE_PRO") or
    os.environ.get("STRIPE_PRICE_PROFESSIONAL") or
    ""
)
STRIPE_PRICE_ENTERPRISE = os.environ.get("STRIPE_PRICE_ENTERPRISE", "")

# API Tier Limits (calls per month)
TIER_LIMITS = {
    "developer": 500_000,
    "professional": 2_500_000,
    "enterprise": 10_000_000,
}
VALID_TIERS = tuple(TIER_LIMITS.keys())

# Service Configuration
BASE_URL = os.environ.get("BASE_URL", "https://clip-api-production.up.railway.app")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
LOG_FILE_PATH = os.environ.get("LOG_FILE_PATH", "/tmp/clip_api.log")
LOG_MAX_BYTES = int(os.environ.get("LOG_MAX_BYTES", 5_242_880))
LOG_BACKUP_COUNT = int(os.environ.get("LOG_BACKUP_COUNT", 3))
RATE_LIMIT_REQUESTS_PER_MINUTE = int(os.environ.get("RATE_LIMIT_REQUESTS_PER_MINUTE", 60))
RATE_LIMIT_ENABLED = os.environ.get("RATE_LIMIT_ENABLED", "true").lower() == "true"


def validate_startup_config() -> dict:
    """Return startup diagnostics and any missing deployment settings."""
    missing = []
    if not STRIPE_SECRET_KEY:
        missing.append("STRIPE_SECRET_KEY")
    if not STRIPE_WEBHOOK_SECRET:
        missing.append("STRIPE_WEBHOOK_SECRET")
    if not STRIPE_PRICE_STARTER:
        missing.append("STRIPE_PRICE_STARTER/DEVELOPER")
    if not STRIPE_PRICE_PRO:
        missing.append("STRIPE_PRICE_PRO/PROFESSIONAL")
    if not STRIPE_PRICE_ENTERPRISE:
        missing.append("STRIPE_PRICE_ENTERPRISE")
    if not BASE_URL:
        missing.append("BASE_URL")

    return {
        "ok": not missing,
        "missing": missing,
        "stripe_configured": all(
            [STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, STRIPE_PRICE_STARTER, STRIPE_PRICE_PRO, STRIPE_PRICE_ENTERPRISE]
        ),
        "db_path": DB_PATH,
        "log_file_path": LOG_FILE_PATH,
    }

