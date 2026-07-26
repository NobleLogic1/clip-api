"""Email delivery via Resend (https://resend.com)."""

import logging
from typing import Optional

import requests

from ..config import BASE_URL, EMAIL_FROM, RESEND_API_KEY

logger = logging.getLogger(__name__)


def send_free_key_verification(email: str, token: str) -> bool:
    """
    Send a magic-link email so the user can activate their free API key.
    Returns True if the email was accepted by Resend, False otherwise.
    """
    verify_url = f"{BASE_URL.rstrip('/')}/billing/verify-free?token={token}"

    if not RESEND_API_KEY:
        logger.warning(
            "RESEND_API_KEY not set — verification email not sent. URL: %s", verify_url
        )
        return False

    payload = {
        "from": EMAIL_FROM,
        "to": [email],
        "subject": "Your NobleLogic CLIP API free key — confirm your email",
        "html": f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 520px; margin: 0 auto; padding: 32px 24px; color: #111;">
          <h2 style="margin: 0 0 16px;">Confirm your email</h2>
          <p style="line-height: 1.6; color: #444;">
            Click the button below to activate your <strong>free CLIP API key</strong>
            (9,000 calls/month). This link expires in 1 hour.
          </p>
          <p style="margin: 28px 0;">
            <a href="{verify_url}"
               style="display: inline-block; background: #6366f1; color: #fff; text-decoration: none;
                      padding: 12px 24px; border-radius: 8px; font-weight: 600;">
              Activate free API key
            </a>
          </p>
          <p style="font-size: 13px; color: #888; line-height: 1.5;">
            If you did not request this, you can ignore this email.<br/>
            Or open this link directly:<br/>
            <a href="{verify_url}" style="color: #6366f1; word-break: break-all;">{verify_url}</a>
          </p>
          <hr style="border: none; border-top: 1px solid #eee; margin: 28px 0;"/>
          <p style="font-size: 12px; color: #aaa;">NobleLogic LLC — Fort Pierce, FL</p>
        </div>
        """,
    }

    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=15,
        )
        if resp.status_code in (200, 201):
            logger.info("Verification email sent to %s", email)
            return True
        logger.error(
            "Resend failed (%s): %s", resp.status_code, resp.text[:300]
        )
        return False
    except Exception as exc:
        logger.exception("Failed to send verification email: %s", exc)
        return False


def verification_url(token: str) -> str:
    return f"{BASE_URL.rstrip('/')}/billing/verify-free?token={token}"
