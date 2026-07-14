import os

DB_PATH = os.environ.get("CLIP_DB_PATH", "clip_maintained.db")
MAX_IMAGE_BYTES = int(os.environ.get("MAX_IMAGE_BYTES", 5_000_000))
IMAGE_TIMEOUT = int(os.environ.get("IMAGE_TIMEOUT", 10))
MODEL_ID = os.environ.get("CLIP_MODEL_ID", "openai/clip-vit-base-patch32")

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_STARTER = os.environ.get("STRIPE_PRICE_STARTER", "")
STRIPE_PRICE_PRO = os.environ.get("STRIPE_PRICE_PRO", "")
STRIPE_PRICE_ENTERPRISE = os.environ.get("STRIPE_PRICE_ENTERPRISE", "")

TIER_LIMITS = {
    "free": 100,
    "starter": 5000,
    "pro": 50000,
    "enterprise": 10**18,
}
