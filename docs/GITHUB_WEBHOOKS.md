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

## 5. Automated AI Verification Pipeline & Anti-Gaming Rules

### 5.1 Verification Security Rules (Polu's Critique Remediation)
To eliminate fraudulent payouts via empty PRs, unreviewed deliveries, and self-merges, TrustBridge enforces cryptographic and heuristic anti-gaming verification gates on every merged PR:

1. **Reviewer Approval Required**:
   - A merged PR only qualifies for automatic milestone verification if it includes at least one approving review (`state == "APPROVED"`) from a reviewer **other than the author**.
   - If the PR is merged without third-party reviewer approval or only contains self-approvals, it is flagged and routed to `PENDING_REVIEW`.

2. **Commit-Hash Anchoring & Full Diff Stat Tracking**:
   - TrustBridge anchors the exact `merge_commit_sha` along with the full git diff stat (`changed_files`, `additions`, `deletions`, `total_lines_changed`) into the immutable milestone `evidence_payload`.
   - Subsequent audit logs link directly to the anchored commit SHA to prevent SHA reassignment or post-merge tampering.

3. **Insignificant Code Changes Rejection (Empty PR Guard)**:
   - PRs with zero or trivial changes (< 5 lines changed across < 2 files, or 0 files/lines modified) are automatically rejected from auto-verification.
   - The milestone is transitioned to `PENDING_REVIEW` with an anti-gaming audit flag, requiring manual client inspection before release.

4. **Self-Merge Detection**:
   - If `merger == author` and no other external approving reviewers are present in the review payload, TrustBridge automatically flags the delivery.
   - The milestone is halted in `PENDING_REVIEW` rather than auto-advancing to `VERIFIED`.

### 5.2 Verification Workflow
When a GitHub PR is merged:
1. **HMAC Signature Check**: Validates `X-Hub-Signature-256` using the shared secret.
2. **Deduplication Check**: Ensures delivery is fresh via `X-GitHub-Delivery` or payload SHA256 digest.
3. **Anti-Gaming Evaluation**: Assesses reviewer approvals, self-merge status, and code diff volume. If flagged, transitions milestone to `PENDING_REVIEW`.
4. **AI Criteria Evaluation**:
   - URL validation (20% weight): Verifies legitimate GitHub PR link.
   - Test & Commit metrics (40% weight): Validates 40-character git commit hash and automated test results.
   - Acceptance criteria checklist (40% weight): Checks delivery specifications.
5. **State Transition**:
   - If anti-gaming checks pass and AI score $\ge 0.80$, milestone status updates from `FUNDED` → `SUBMITTED` → `VERIFIED`.
   - If anti-gaming flags trigger, milestone transitions to `PENDING_REVIEW` with full evidence payload.
   - Client can release verified funds or manually review pending submissions.

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
