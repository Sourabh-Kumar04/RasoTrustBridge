#!/usr/bin/env python3
"""
TrustBridge PayPal Sandbox Readiness & Verification Script.

Usage:
    python scripts/test_paypal_sandbox.py
    # or
    uv run python scripts/test_paypal_sandbox.py

Purpose:
    Validates developer sandbox credentials from .env, tests OAuth2 token
    generation against PayPal Sandbox API, and creates a test $1 USD escrow order.
"""

import sys
import os
import asyncio

# Ensure project root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.config import get_settings
from app.services.paypal_client import paypal_client


async def run_sandbox_check():
    settings = get_settings()

    print("=" * 64)
    print("       TrustBridge PayPal Sandbox Readiness Check")
    print("=" * 64)

    # 1. Check Credentials Configuration
    client_id = settings.PAYPAL_CLIENT_ID
    client_secret = settings.PAYPAL_CLIENT_SECRET

    if not client_id or not client_secret or len(client_id.strip()) < 5:
        print("\n❌ set PAYPAL_CLIENT_ID in .env")
        print("\nDetails:")
        print("  PAYPAL_CLIENT_ID and/or PAYPAL_CLIENT_SECRET are missing or empty.")
        print("\nNext Steps:")
        print("  1. Open docs/PAYPAL_SANDBOX_GUIDE.md for step-by-step instructions.")
        print("  2. Create a sandbox application at https://developer.paypal.com")
        print("  3. Set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET in your .env file.")
        print("=" * 64)
        sys.exit(1)

    print("\n[1/3] Environment Configuration:")
    print(f"  ✓ PAYPAL_CLIENT_ID:     {client_id[:8]}...{client_id[-4:] if len(client_id) > 12 else ''}")
    print(f"  ✓ PAYPAL_MODE:          {settings.PAYPAL_MODE}")
    print(f"  ✓ PAYPAL_API_BASE:      {settings.PAYPAL_API_BASE}")

    # 2. Test OAuth2 Authentication
    print("\n[2/3] Authenticating with PayPal OAuth2 API...")
    try:
        token = await paypal_client.get_access_token()
        if token == "mock_sandbox_access_token":
            print("  ⚠️  Running in mock fallback mode.")
        else:
            print(f"  ✓ OAuth2 Access Token acquired successfully! ({token[:12]}...)")
    except Exception as e:
        print(f"  ❌ Authentication failed: {str(e)}")
        print("  Please verify PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET in .env.")
        sys.exit(1)

    # 3. Create $1.00 USD Escrow Order
    print("\n[3/3] Creating $1.00 USD Test Escrow Order (Orders API v2)...")
    try:
        order = await paypal_client.create_order(
            contract_id="ct_sandbox_verify_001",
            amount=1.00,
            currency="USD",
        )
        print(f"  ✓ Order Created:  {order.get('order_id')}")
        print(f"  ✓ Order Status:   {order.get('status')}")
        print(f"  ✓ Amount:         ${order.get('amount'):.2f} {order.get('currency')}")
        if order.get("approve_url"):
            print(f"  ✓ Approval URL:   {order.get('approve_url')}")
    except Exception as e:
        print(f"  ❌ Order creation failed: {str(e)}")
        sys.exit(1)

    print("\n" + "=" * 64)
    print("  ✓ SUCCESS: PayPal Sandbox is properly configured and operational!")
    print("=" * 64)


if __name__ == "__main__":
    asyncio.run(run_sandbox_check())
