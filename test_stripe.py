#!/usr/bin/env python3
"""
Test script for Stripe integration.
Run this to verify Stripe configuration is working.
"""

import os
import sys
import requests
from clip_api.config import (
    STRIPE_SECRET_KEY,
    STRIPE_WEBHOOK_SECRET,
    STRIPE_PRICE_STARTER,
    STRIPE_PRICE_PRO,
    STRIPE_PRICE_ENTERPRISE,
    BASE_URL,
)

def check_config():
    """Check if Stripe configuration is complete."""
    print("\n=== Stripe Configuration Check ===\n")
    
    checks = {
        "STRIPE_SECRET_KEY": STRIPE_SECRET_KEY,
        "STRIPE_WEBHOOK_SECRET": STRIPE_WEBHOOK_SECRET,
        "STRIPE_PRICE_STARTER": STRIPE_PRICE_STARTER,
        "STRIPE_PRICE_PRO": STRIPE_PRICE_PRO,
        "STRIPE_PRICE_ENTERPRISE": STRIPE_PRICE_ENTERPRISE,
        "BASE_URL": BASE_URL,
    }
    
    all_set = True
    for key, value in checks.items():
        status = "✓" if value else "✗"
        print(f"{status} {key}: {'SET' if value else 'MISSING'}")
        if not value and key.startswith("STRIPE_"):
            all_set = False
    
    print()
    return all_set


def test_checkout(base_url: str):
    """Test the checkout endpoint."""
    print("=== Testing Checkout Endpoint ===\n")
    
    url = f"{base_url}/billing/checkout"
    payload = {
        "tier": "professional",
        "email": "test@example.com"
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        print(f"POST {url}")
        print(f"Status: {response.status_code}")
        print(f"Response: {response.json()}\n")
        return response.status_code < 400
    except Exception as e:
        print(f"Error: {e}\n")
        return False


def test_portal(base_url: str, api_key: str = None):
    """Test the billing portal endpoint."""
    print("=== Testing Billing Portal Endpoint ===\n")
    
    if not api_key:
        print("Skipping portal test (no API key provided)")
        print("Tip: Run with an actual API key to test portal access\n")
        return None
    
    url = f"{base_url}/billing/portal"
    payload = {"api_key": api_key}
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        print(f"POST {url}")
        print(f"Status: {response.status_code}")
        print(f"Response: {response.json()}\n")
        return response.status_code < 400
    except Exception as e:
        print(f"Error: {e}\n")
        return False


def test_usage(base_url: str, api_key: str = None):
    """Test the usage stats endpoint."""
    print("=== Testing Usage Stats Endpoint ===\n")
    
    if not api_key:
        print("Skipping usage test (no API key provided)")
        print("Tip: Run with an actual API key to test usage stats\n")
        return None
    
    url = f"{base_url}/billing/usage"
    headers = {"Authorization": f"Bearer {api_key}"}
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        print(f"GET {url}")
        print(f"Status: {response.status_code}")
        print(f"Response: {response.json()}\n")
        return response.status_code < 400
    except Exception as e:
        print(f"Error: {e}\n")
        return False


if __name__ == "__main__":
    api_key = sys.argv[1] if len(sys.argv) > 1 else None
    base_url = os.environ.get("TEST_BASE_URL", BASE_URL).rstrip("/")
    
    print(f"Testing Stripe integration against: {base_url}\n")
    
    # Check configuration
    config_ok = check_config()
    
    if not config_ok:
        print("⚠️  Stripe configuration is incomplete!")
        print("Please set these environment variables in Railway:")
        print("  - STRIPE_SECRET_KEY")
        print("  - STRIPE_WEBHOOK_SECRET")
        print("  - STRIPE_PRICE_STARTER")
        print("  - STRIPE_PRICE_PRO")
        print("  - STRIPE_PRICE_ENTERPRISE")
        sys.exit(1)
    
    print("✓ Stripe configuration is complete!\n")
    
    # Test endpoints (requires running service)
    if base_url.startswith("http"):
        checkout_ok = test_checkout(base_url)
        portal_ok = test_portal(base_url, api_key)
        usage_ok = test_usage(base_url, api_key)
        
        if not checkout_ok:
            print("⚠️  Checkout endpoint failed")
            sys.exit(1)
        
        print("✓ All accessible endpoints passed!")
    else:
        print("Skipping endpoint tests (no valid BASE_URL)")

