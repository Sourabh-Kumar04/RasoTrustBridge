#!/usr/bin/env bash
set -e

PORT=8989
BASE_URL="http://127.0.0.1:${PORT}"
SECRET="trustbridge_github_webhook_secret_dev"

echo "=================================================================="
echo "    Simulating GitHub PR Merge Webhook with curl against Server"
echo "=================================================================="

# 1. Start server in background
echo "Starting TrustBridge server on port ${PORT}..."
uv run uvicorn app.main:app --port ${PORT} --host 127.0.0.1 &
SERVER_PID=$!

# Trap to ensure server is killed when script exits
trap "kill -9 $SERVER_PID 2>/dev/null || true" EXIT

# Wait for server to become responsive
echo "Waiting for server to be ready..."
for i in {1..30}; do
    if curl -s "${BASE_URL}/health" >/dev/null 2>&1; then
        echo "Server is UP at ${BASE_URL} (PID: $SERVER_PID)"
        break
    fi
    sleep 0.5
done

# 2. Create a contract
echo -e "\n1. Creating test contract..."
CREATE_RES=$(curl -s -X POST "${BASE_URL}/api/v1/contracts" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Curl Webhook Verification Contract",
    "brief": "Testing live curl webhook execution",
    "client_name": "Sarah Connor",
    "client_email": "sarah@resistance.org",
    "developer_name": "John Connor",
    "developer_email": "john@resistance.org",
    "base_currency": "USD",
    "payout_currency": "EUR",
    "total_amount": 1000.0,
    "milestones": [
      {
        "title": "Deploy Defense Grid Auth",
        "description": "API security layer",
        "amount": 1000.0,
        "order_index": 1,
        "acceptance_criteria": [
          {"description": "Automated tests passing", "verification_method": "automated_test"}
        ]
      }
    ]
  }')

CONTRACT_ID=$(echo "$CREATE_RES" | python3 -c "import sys, json; print(json.load(sys.stdin)['id'])")
MILESTONE_ID=$(echo "$CREATE_RES" | python3 -c "import sys, json; print(json.load(sys.stdin)['milestones'][0]['id'])")
echo "   Contract ID:  $CONTRACT_ID"
echo "   Milestone ID: $MILESTONE_ID"

# 3. Fund the contract
echo -e "\n2. Funding contract escrow..."
FUND_RES=$(curl -s -X POST "${BASE_URL}/api/v1/contracts/${CONTRACT_ID}/fund" \
  -H "Content-Type: application/json" \
  -d '{
    "paypal_order_id": "ORDER-CURL-SIM-001",
    "paypal_capture_id": "CAP-CURL-SIM-001"
  }')

M_STATUS=$(curl -s "${BASE_URL}/api/v1/milestones/${MILESTONE_ID}" | python3 -c "import sys, json; print(json.load(sys.stdin)['status'])")
echo "   Milestone status after funding: $M_STATUS"
if [ "$M_STATUS" != "FUNDED" ]; then
    echo "❌ Expected milestone status to be FUNDED, got $M_STATUS"
    exit 1
fi

# 4. Prepare GitHub PR Merge Webhook payload & HMAC-SHA256 signature
echo -e "\n3. Simulating GitHub PR Merge Webhook..."
PAYLOAD=$(cat <<EOF
{
  "action": "closed",
  "repository": {
    "full_name": "Sourabh-Kumar04/RasoTrustBridge"
  },
  "pull_request": {
    "number": 999,
    "title": "feat: Defense Grid Auth Deliverable [${MILESTONE_ID}]",
    "body": "Resolves deliverable for milestone ${MILESTONE_ID}.",
    "merged": true,
    "merged_at": "2026-10-19T18:00:00Z",
    "merged_by": {
      "login": "john-connor"
    },
    "merge_commit_sha": "e9b5c2a104f38d7e6c5b4a3f2e1d0c9b8a7f6e5d",
    "html_url": "https://github.com/Sourabh-Kumar04/RasoTrustBridge/pull/999",
    "head": {
      "ref": "feature/defense-grid-auth",
      "sha": "e9b5c2a104f38d7e6c5b4a3f2e1d0c9b8a7f6e5d"
    }
  }
}
EOF
)

# Compute HMAC signature using python
SIGNATURE=$(python3 -c "
import hmac, hashlib
secret = b'$SECRET'
body = '''$PAYLOAD'''.encode('utf-8')
sig = 'sha256=' + hmac.new(secret, body, hashlib.sha256).hexdigest()
print(sig)
")

echo "   Computed HMAC Signature: $SIGNATURE"

# 5. Send POST to webhook endpoint via curl
echo "   Sending curl request to /api/v1/webhooks/github..."
WEBHOOK_RES=$(curl -s -X POST "${BASE_URL}/api/v1/webhooks/github" \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Event: pull_request" \
  -H "X-Hub-Signature-256: ${SIGNATURE}" \
  -d "$PAYLOAD")

echo "   Webhook Response: $WEBHOOK_RES"

# 6. Verify Milestone state transition
echo -e "\n4. Verifying milestone transition via GET /api/v1/milestones/${MILESTONE_ID}..."
VERIFIED_DATA=$(curl -s "${BASE_URL}/api/v1/milestones/${MILESTONE_ID}")
FINAL_STATUS=$(echo "$VERIFIED_DATA" | python3 -c "import sys, json; print(json.load(sys.stdin)['status'])")
SCORE=$(echo "$VERIFIED_DATA" | python3 -c "import sys, json; print(json.load(sys.stdin).get('verification_score'))")

echo "   Final Milestone Status: $FINAL_STATUS"
echo "   AI Verification Score:  $SCORE"

if [ "$FINAL_STATUS" = "VERIFIED" ]; then
    echo -e "\n=================================================================="
    echo "  ✓ SUCCESS: Milestone successfully verified via GitHub PR curl webhook!"
    echo "=================================================================="
    exit 0
else
    echo -e "\n❌ FAILURE: Expected status VERIFIED, got $FINAL_STATUS"
    exit 1
fi
