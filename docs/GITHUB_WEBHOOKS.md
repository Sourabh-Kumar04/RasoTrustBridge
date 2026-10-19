# TrustBridge — GitHub Webhook Integration & Verification Guide

This guide documents the automated delivery verification system in TrustBridge. When international developers complete code deliverables and merge GitHub Pull Requests, TrustBridge automatically receives the webhook, verifies its authenticity, grades the delivery using the AI verification engine, and transitions the milestone from `FUNDED` → `SUBMITTED` → `VERIFIED`.

---

## 1. Webhook Endpoint URL

Configure the following URL in your GitHub repository:

```
https://<YOUR_DOMAIN_OR_NGROK>/api/v1/webhooks/github
```

- **Content type**: `application/json`
- **Secret**: The shared secret configured in your TrustBridge `.env` (`GITHUB_WEBHOOK_SECRET`)
- **SSL verification**: Enable SSL verification (recommended for production)

For local development or hackathon testing with local services, use a tunnel like `ngrok` or `cloudflared`:
```bash
ngrok http 8000
# Webhook URL: https://xyz-1234.ngrok-free.app/api/v1/webhooks/github
```

---

## 2. Generating a Secure Webhook Secret

Generate a cryptographically secure random secret using OpenSSL or Python:

```bash
# Using OpenSSL:
openssl rand -hex 32

# Or using Python:
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Copy the generated secret and add it to your `.env` file:

```env
GITHUB_WEBHOOK_SECRET=7f8b910111213141516171819202122232425262728293031323334353637383
```

*(In local development environments without an explicit `.env` setting, TrustBridge defaults to `trustbridge_github_webhook_secret_dev`)*

---

## 3. Subscribed GitHub Events

In GitHub repository **Settings** → **Webhooks** → **Which events would you like to trigger this webhook?**, select:
- **Let me select individual events**:
  - `Pull requests` (Essential: Triggers milestone verification when PR is merged)
  - `Pushes` (Optional / Recommended: Tracks commit SHAs for progress & proof)

---

## 4. How Webhook Linking Works

TrustBridge links incoming webhooks to contracts and milestones automatically using intelligent heuristic parsing:

### Method A: Explicit Milestone ID (Highest Priority)
Include the milestone ID (`ms_...`) in the PR title, branch name, or commit message:
- PR Title: `feat: User Authentication System [ms_123456789abc]`
- Branch Name: `feature/ms_123456789abc`

### Method B: Contract ID + Milestone Number
Include the contract ID (`ct_...`) along with the milestone index:
- PR Title: `feat: Milestone 1 Database Setup [ct_abcdef123456]`
- Branch Name: `feat/ct_abcdef123456-milestone-1`
- PR Body: `Delivers milestone 2 for contract ct_abcdef123456`

### Method C: Earliest Active Milestone
If only the contract ID is referenced, TrustBridge resolves to the earliest currently `FUNDED` milestone in that contract.

---

## 5. Automated AI Verification Pipeline

When a GitHub PR is merged:
1. **HMAC Signature Check**: Validates `X-Hub-Signature-256` using the shared secret.
2. **Event Filtering**: Confirms `action == "closed"` and `merged == True`.
3. **Evidence Extraction**: Pulls PR URL, merge commit SHA, author, and timestamp into `evidence_payload`.
4. **AI Criteria Evaluation**:
   - URL validation (20% weight): Verifies legitimate GitHub PR link.
   - Test & Commit metrics (40% weight): Validates 40-character git commit hash and automated test results.
   - Acceptance criteria checklist (40% weight): Checks delivery specifications.
5. **State Transition**:
   - If score $\ge 0.80$, milestone status updates from `FUNDED` → `SUBMITTED` → `VERIFIED`.
   - Immutable audit log entry `GITHUB_PR_MERGED_VERIFIED` is written to the database.
   - Client can now release funds with 1 click, triggering instant PayPal Payout.

---

## 6. Testing Webhooks Locally via curl

You can test the webhook endpoint directly with curl using Python to sign the payload:

```bash
# 1. Start server
uv run uvicorn app.main:app --port 8000

# 2. In another terminal, generate signature and send test ping
SECRET="trustbridge_github_webhook_secret_dev"
PAYLOAD='{"zen":"Keep it logically awesome.","hook_id":12345}'
SIG="sha256=$(echo -n "$PAYLOAD" | openssl dgst -sha256 -hmac "$SECRET" | sed 's/^.* //')"

curl -X POST http://127.0.0.1:8000/api/v1/webhooks/github \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Event: ping" \
  -H "X-Hub-Signature-256: $SIG" \
  -d "$PAYLOAD"
```

Expected Response:
```json
{"status":"pong","zen":"Keep it logically awesome.","hook_id":12345,"delivery_id":null}
```
