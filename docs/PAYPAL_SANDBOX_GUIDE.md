# TrustBridge — PayPal Sandbox Setup Guide

This guide is for **Amit** (and any developer configuring TrustBridge sandbox escrow).
TrustBridge operates as a programmable trust layer:
1. **Hold Funds**: Uses PayPal **Orders API v2** (`intent=CAPTURE`) to lock client funds in escrow.
2. **Release Funds**: Uses PayPal **Payouts API v1** (`POST /v1/payments/payouts`) to release funds to the international freelancer's PayPal email upon verified milestone completion.

---

## Quick Setup (5 Minutes)

### Step 1: Sign in to PayPal Developer
1. Go to [https://developer.paypal.com](https://developer.paypal.com).
2. Click **Log In to Dashboard** (use your standard personal or business PayPal login).

### Step 2: Navigate to Apps & Credentials
1. In the left navigation, click **Apps & Credentials**.
2. Make sure the toggle at the top is set to **Sandbox** (do NOT use Live for testing).
3. Click the blue **Create App** button.
   - **App Name**: `TrustBridge-Escrow-Sandbox`
   - **App Type**: Select **Merchant** (Default).
4. Click **Create App**.

### Step 3: Copy Your Sandbox Credentials
Once the app is created, PayPal displays your credentials:
1. Copy the **Client ID** (a long string like `AWe...`).
2. Click **Show** under **Secret** and copy the **Client Secret**.

*(Optional: Scroll down to App Settings on that page and confirm that **Payouts** is checked. If it is unchecked, toggle it on so releases can be tested.)*

### Step 4: Check Sandbox Accounts
1. In the left navigation under **Sandbox**, click **Accounts**.
2. You will see two pre-generated test accounts:
   - A **Business (Merchant)** account (receives payments / holds escrow).
   - A **Personal (Buyer)** account (used by clients to pay / approve orders).
3. Click the `...` menu next to either account to view their test login email and system-generated password.

### Step 5: Configure `.env` in TrustBridge
In `/mnt/d/Sourabh_Workspace/03_Vibe_Hackathon/01_Active/trustbridge`, create or edit your `.env` file:

```env
# PayPal REST API Sandbox Credentials
PAYPAL_CLIENT_ID=your_actual_sandbox_client_id_here
PAYPAL_CLIENT_SECRET=your_actual_sandbox_client_secret_here
PAYPAL_MODE=sandbox
PAYPAL_API_BASE=https://api-m.sandbox.paypal.com
```

> **Note**: TrustBridge automatically uses safe deterministic mock simulation when credentials are empty, so tests always pass locally even without API keys. Adding credentials enables real live sandbox calls.

### Step 6: Run the Verification Script
Run the automated sandbox readiness check:

```bash
uv run python scripts/test_paypal_sandbox.py
```
*(or `python scripts/test_paypal_sandbox.py` if your virtual environment is active)*

### Expected Output

```
============================================================
       TrustBridge PayPal Sandbox Readiness Check
============================================================
[1/4] Checking environment configuration...
  ✓ PAYPAL_CLIENT_ID detected
  ✓ PAYPAL_CLIENT_SECRET detected
  ✓ PAYPAL_API_BASE: https://api-m.sandbox.paypal.com

[2/4] Authenticating with PayPal OAuth2 API...
  ✓ OAuth2 Token acquired: A21AA... (expires in 32400s)

[3/4] Creating test $1.00 USD Escrow Order (Orders API v2)...
  ✓ Order Created: 8RF98471...
  ✓ Status: CREATED
  ✓ Approve URL: https://www.sandbox.paypal.com/checkoutnow?token=...

[4/4] Verifying Payouts API readiness...
  ✓ Payouts endpoint reachable

============================================================
  ✓ PayPal Sandbox is 100% READY for TrustBridge!
============================================================
```

If credentials are not yet set, the script will exit gracefully and remind you to set `PAYPAL_CLIENT_ID` in `.env`.

---

## Architecture Reference

| Action | API Endpoint | PayPal Intent | TrustBridge Service |
|---|---|---|---|
| **Hold / Escrow** | `POST /v2/checkout/orders` | `CAPTURE` | `PayPalClient.create_order()` |
| **Capture Funds** | `POST /v2/checkout/orders/{id}/capture` | `CAPTURE` | `PayPalClient.capture_order()` |
| **Release Funds** | `POST /v1/payments/payouts` | Payout Batch | `PayPalClient.create_payout()` |
| **Dispute Hold** | Read-only Evidence Bundle | Evidence Pack | `DisputeService.open_dispute()` |
