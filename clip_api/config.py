import os

# Database
DB_PATH = os.environ.get("CLIP_DB_PATH", "/data/clip_api.db")

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

# Service Configuration
BASE_URL = os.environ.get("BASE_URL", "https://clip-api-production.up.railway.app")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

