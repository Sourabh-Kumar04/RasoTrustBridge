import json
import hmac
import hashlib
import pytest
from app.config import get_settings
from app.models.enums import ContractStatus, MilestoneStatus
from app.models.payment import EscrowHold, AuditLog
from app.models.milestone import Milestone
from app.models.contract import Contract

settings = get_settings()
WEBHOOK_SECRET = settings.GITHUB_WEBHOOK_SECRET or "trustbridge_github_webhook_secret_dev"


def sign_body(payload: dict, secret: str = WEBHOOK_SECRET) -> tuple[bytes, str]:
    raw = json.dumps(payload).encode("utf-8")
    sig = "sha256=" + hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    return raw, sig


def test_full_autonomous_lifecycle_e2e(client, db_session):
    """
    End-to-End Integration Flow Test:
    1. Negotiate (Autonomous agent-to-agent negotiation creates escrow contract)
    2. Fund (PayPal sandbox escrow hold authorization & capture)
    3. Push (GitHub push webhook tracks commit SHA delivery proof)
    4. Merge (GitHub PR merge webhook triggers AI verification -> VERIFIED)
    5. Release (PayPal payout releases funds to freelancer)
    6. Completion (All milestones released -> contract automatically COMPLETED)
    """

    # --------------------------------------------------------------------------
    # Step 1: Agent Negotiation & Autonomous Contract Creation
    # --------------------------------------------------------------------------
    neg_payload = {
        "buyer_brief": "Need scalable payment orchestration backend with PayPal. Budget $3,500 USD, 21 days.",
        "seller_brief": "Senior backend architect. Propose $4,500 USD, 28 days with full test suite.",
        "client_name": "Marcus Aurelius",
        "client_email": "marcus@rome.enterprise",
        "client_country": "US",
        "developer_name": "Ravi Shankar",
        "developer_email": "ravi@freelance.in",
        "developer_country": "IN",
        "base_currency": "USD",
        "payout_currency": "INR",
        "max_rounds": 3,
        "auto_create_contract": True,
        "force_rule_based": True,
    }

    neg_res = client.post("/api/v1/negotiate", json=neg_payload)
    assert neg_res.status_code == 200
    neg_data = neg_res.json()

    assert neg_data["status"] == "agreed"
    assert neg_data["contract_id"] is not None
    contract_id = neg_data["contract_id"]

    # Verify initial contract state in database
    contract = db_session.query(Contract).filter(Contract.id == contract_id).first()
    assert contract is not None
    assert contract.status == ContractStatus.DRAFT
    assert len(contract.milestones) == 3
    for m in contract.milestones:
        assert m.status == MilestoneStatus.PENDING

    m1_id = contract.milestones[0].id
    m2_id = contract.milestones[1].id
    m3_id = contract.milestones[2].id

    # --------------------------------------------------------------------------
    # Step 2: Client Funds Escrow via PayPal
    # --------------------------------------------------------------------------
    # 2a. Create PayPal Order
    order_res = client.post("/api/v1/paypal/order/create", json={
        "contract_id": contract_id,
        "amount": contract.total_amount,
        "currency": "USD",
    })
    assert order_res.status_code == 200
    order_data = order_res.json()
    paypal_order_id = order_data["order_id"]

    # 2b. Capture PayPal Order
    capture_res = client.post("/api/v1/paypal/order/capture", json={
        "order_id": paypal_order_id,
    })
    assert capture_res.status_code == 200
    capture_data = capture_res.json()
    paypal_capture_id = capture_data["capture_id"]

    # 2c. Confirm Escrow Funding
    fund_res = client.post(f"/api/v1/contracts/{contract_id}/fund", json={
        "paypal_order_id": paypal_order_id,
        "paypal_capture_id": paypal_capture_id,
    })
    assert fund_res.status_code == 200

    db_session.refresh(contract)
    assert contract.status == ContractStatus.ACTIVE
    assert contract.funded_at is not None
    for m in contract.milestones:
        db_session.refresh(m)
        assert m.status == MilestoneStatus.FUNDED

    # Verify EscrowHold recorded
    hold = db_session.query(EscrowHold).filter(EscrowHold.contract_id == contract_id).first()
    assert hold is not None
    assert hold.paypal_order_id == paypal_order_id

    # --------------------------------------------------------------------------
    # Step 3: Developer Pushes Work (Track Commits via GitHub Push Webhook)
    # --------------------------------------------------------------------------
    push_payload = {
        "ref": f"refs/heads/feature/{m1_id}",
        "after": "1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b",
        "repository": {"full_name": "Sourabh-Kumar04/RasoTrustBridge"},
        "commits": [
            {
                "id": "1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b",
                "message": f"feat: foundational API architecture deliverable [{m1_id}]",
                "timestamp": "2026-10-19T13:00:00Z",
                "url": "https://github.com/Sourabh-Kumar04/RasoTrustBridge/commit/1a2b3c",
                "author": {"name": "Ravi Shankar", "email": "ravi@freelance.in"},
            }
        ],
    }
    raw_push, push_sig = sign_body(push_payload)

    push_res = client.post(
        "/api/v1/webhooks/github",
        content=raw_push,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "push",
            "X-Hub-Signature-256": push_sig,
        },
    )
    assert push_res.status_code == 200
    assert push_res.json()["status"] == "tracked"

    # --------------------------------------------------------------------------
    # Step 4: GitHub PR Merged (Triggers AI Delivery Verification)
    # --------------------------------------------------------------------------
    pr_merge_sha = "f0e1d2c3b4a5968778695a4b3c2d1e0f9a8b7c6d"
    pr_payload = {
        "action": "closed",
        "repository": {"full_name": "Sourabh-Kumar04/RasoTrustBridge"},
        "pull_request": {
            "number": 101,
            "title": f"feat(m1): Complete foundational API architecture [{m1_id}]",
            "body": f"Closes milestone deliverable {m1_id} for contract {contract_id}. Passes unit tests.",
            "merged": True,
            "merged_at": "2026-10-19T15:00:00Z",
            "merged_by": {"login": "marcus-client"},
            "merge_commit_sha": pr_merge_sha,
            "html_url": "https://github.com/Sourabh-Kumar04/RasoTrustBridge/pull/101",
            "head": {
                "ref": f"feature/{m1_id}",
                "sha": pr_merge_sha,
            },
        },
    }
    raw_pr, pr_sig = sign_body(pr_payload)

    pr_res = client.post(
        "/api/v1/webhooks/github",
        content=raw_pr,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": pr_sig,
        },
    )
    assert pr_res.status_code == 200
    pr_data = pr_res.json()

    assert pr_data["status"] == "verified"
    assert pr_data["milestone_id"] == m1_id
    assert pr_data["milestone_status"] == "VERIFIED"
    assert pr_data["is_approved"] is True
    assert pr_data["verification_score"] >= 0.80

    # --------------------------------------------------------------------------
    # Step 5: Release Milestone 1 (PayPal Payout Triggered)
    # --------------------------------------------------------------------------
    rel1_res = client.post(f"/api/v1/milestones/{m1_id}/release", json={
        "release_note": "Milestone 1 verified via GitHub PR merge. Funds approved.",
    })
    assert rel1_res.status_code == 200
    rel1_data = rel1_res.json()
    assert rel1_data["status"] == "RELEASED"
    assert rel1_data["paypal_payout_batch_id"] is not None

    # Contract should still be ACTIVE because M2 and M3 remain
    db_session.refresh(contract)
    assert contract.status == ContractStatus.ACTIVE

    # --------------------------------------------------------------------------
    # Step 6: Complete Remaining Milestones -> Contract Transitions to COMPLETED
    # --------------------------------------------------------------------------
    # Complete Milestone 2 via PR webhook
    pr2_payload = {
        "action": "closed",
        "repository": {"full_name": "Sourabh-Kumar04/RasoTrustBridge"},
        "pull_request": {
            "number": 102,
            "title": f"feat(m2): Core feature deliverable [{m2_id}]",
            "merged": True,
            "merge_commit_sha": "222233334444555566667777888899990000aaaa",
            "html_url": "https://github.com/Sourabh-Kumar04/RasoTrustBridge/pull/102",
            "head": {"ref": f"feat/{m2_id}", "sha": "222233334444555566667777888899990000aaaa"},
        },
    }
    raw_pr2, pr2_sig = sign_body(pr2_payload)
    res_pr2 = client.post("/api/v1/webhooks/github", content=raw_pr2, headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": pr2_sig})
    assert res_pr2.status_code == 200
    assert res_pr2.json()["status"] == "verified"

    # Release Milestone 2
    res_rel2 = client.post(f"/api/v1/milestones/{m2_id}/release", json={"release_note": "Milestone 2 released"})
    assert res_rel2.status_code == 200
    assert res_rel2.json()["status"] == "RELEASED"

    # Complete Milestone 3 via PR webhook
    pr3_payload = {
        "action": "closed",
        "repository": {"full_name": "Sourabh-Kumar04/RasoTrustBridge"},
        "pull_request": {
            "number": 103,
            "title": f"feat(m3): Polish and deployment [{m3_id}]",
            "merged": True,
            "merge_commit_sha": "33334444555566667777888899990000aaaabbbb",
            "html_url": "https://github.com/Sourabh-Kumar04/RasoTrustBridge/pull/103",
            "head": {"ref": f"feat/{m3_id}", "sha": "33334444555566667777888899990000aaaabbbb"},
        },
    }
    raw_pr3, pr3_sig = sign_body(pr3_payload)
    res_pr3 = client.post("/api/v1/webhooks/github", content=raw_pr3, headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": pr3_sig})
    assert res_pr3.status_code == 200
    assert res_pr3.json()["status"] == "verified"

    # Release Milestone 3
    res_rel3 = client.post(f"/api/v1/milestones/{m3_id}/release", json={"release_note": "Final milestone released"})
    assert res_rel3.status_code == 200
    assert res_rel3.json()["status"] == "RELEASED"

    # --------------------------------------------------------------------------
    # Step 7: Final Contract State & Audit Log Assertions
    # --------------------------------------------------------------------------
    db_session.refresh(contract)
    assert contract.status == ContractStatus.COMPLETED
    assert contract.completed_at is not None

    # Inspect complete audit trail for the contract
    audits = db_session.query(AuditLog).filter(AuditLog.contract_id == contract_id).all()
    audit_actions = [a.action for a in audits]

    assert "CONTRACT_CREATED" in audit_actions
    assert "ESCROW_FUNDED" in audit_actions
    assert "GITHUB_COMMITS_PUSHED" in audit_actions
    assert "GITHUB_PR_MERGED_VERIFIED" in audit_actions
    assert "MILESTONE_RELEASED" in audit_actions

    print("\n✅ End-to-End Lifecycle Verified: Negotiate -> Escrow -> Push -> Webhook Verify -> Release -> Complete")
