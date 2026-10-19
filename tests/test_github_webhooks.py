import json
import hmac
import hashlib
import pytest
from app.config import get_settings
from app.models.enums import ContractStatus, MilestoneStatus, FXStrategy, ActorRole
from app.schemas.contract import ContractCreateSchema, MilestoneCreateSchema, AcceptanceCriterionSchema
from app.services.contract_service import contract_service
from app.models.milestone import Milestone
from app.models.payment import AuditLog

settings = get_settings()
TEST_SECRET = settings.GITHUB_WEBHOOK_SECRET or "trustbridge_github_webhook_secret_dev"


def sign_payload(payload_dict: dict, secret: str = TEST_SECRET) -> tuple[bytes, str]:
    raw = json.dumps(payload_dict).encode("utf-8")
    sig = "sha256=" + hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    return raw, sig


@pytest.fixture
async def funded_contract(db_session):
    schema = ContractCreateSchema(
        title="GitHub Webhook Verification Contract",
        brief="Testing automated delivery verification",
        client_name="Sarah Jenkins",
        client_email="sarah@client.org",
        developer_name="Carlos Mendez",
        developer_email="carlos@freelance.dev",
        base_currency="USD",
        payout_currency="EUR",
        total_amount=1500.0,
        fx_strategy=FXStrategy.LOCKED_AT_SIGNING,
        locked_fx_rate=0.92,
        milestones=[
            MilestoneCreateSchema(
                title="API Authentication & Core Models",
                description="Deliver JWT auth and database models with unit tests",
                amount=500.0,
                order_index=1,
                acceptance_criteria=[
                    AcceptanceCriterionSchema(description="Code passes all automated unit tests", verification_method="automated_test"),
                    AcceptanceCriterionSchema(description="PR code review approved", verification_method="github_pr"),
                ],
            ),
            MilestoneCreateSchema(
                title="Stripe and PayPal Webhook Engine",
                description="Payment gateways integration",
                amount=1000.0,
                order_index=2,
                acceptance_criteria=[
                    AcceptanceCriterionSchema(description="Webhook signature verification working", verification_method="automated_test"),
                ],
            ),
        ],
    )
    contract = await contract_service.create_contract(db_session, schema)
    return await contract_service.fund_contract(
        db_session,
        contract.id,
        paypal_order_id="ORDER-GH-TEST-001",
        paypal_capture_id="CAP-GH-TEST-001",
    )


def test_github_webhook_signature_rejection(client):
    payload = {"zen": "Keep it logically awesome."}
    raw = json.dumps(payload).encode("utf-8")

    # 1. Missing header
    res1 = client.post(
        "/api/v1/webhooks/github",
        content=raw,
        headers={"Content-Type": "application/json", "X-GitHub-Event": "ping"},
    )
    assert res1.status_code == 401
    assert "Invalid or missing" in res1.json()["detail"]

    # 2. Tampered / wrong signature
    res2 = client.post(
        "/api/v1/webhooks/github",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "ping",
            "X-Hub-Signature-256": "sha256=bad000000000000000000000000000000000000000000000000000000000dead",
        },
    )
    assert res2.status_code == 401


def test_github_webhook_ping_handshake(client):
    payload = {"zen": "Non-blocking is better than blocking.", "hook_id": 98765432}
    raw, sig = sign_payload(payload)

    res = client.post(
        "/api/v1/webhooks/github",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "ping",
            "X-Hub-Signature-256": sig,
            "X-GitHub-Delivery": "delivery-ping-uuid-1234",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "pong"
    assert data["hook_id"] == 98765432
    assert data["zen"] == "Non-blocking is better than blocking."


@pytest.mark.asyncio
async def test_github_webhook_push_tracks_commits(client, db_session, funded_contract):
    contract = funded_contract
    m1 = contract.milestones[0]

    payload = {
        "ref": f"refs/heads/feature/{m1.id}",
        "after": "c0ffee1234567890abcdef1234567890abcdef12",
        "repository": {"full_name": "Sourabh-Kumar04/RasoTrustBridge"},
        "commits": [
            {
                "id": "c0ffee1234567890abcdef1234567890abcdef12",
                "message": f"feat: implement auth tokens for [{m1.id}]",
                "timestamp": "2026-10-19T12:00:00Z",
                "url": "https://github.com/Sourabh-Kumar04/RasoTrustBridge/commit/c0ffee",
                "author": {"name": "Carlos Mendez", "email": "carlos@freelance.dev"},
            }
        ],
    }
    raw, sig = sign_payload(payload)

    res = client.post(
        "/api/v1/webhooks/github",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "push",
            "X-Hub-Signature-256": sig,
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "tracked"
    assert data["milestone_id"] == m1.id
    assert data["commits_tracked"] == 1
    assert data["latest_commit_sha"] == "c0ffee1234567890abcdef1234567890abcdef12"

    # Verify milestone evidence payload in database
    db_session.refresh(m1)
    assert m1.evidence_payload is not None
    assert len(m1.evidence_payload["tracked_commits"]) == 1
    assert m1.evidence_payload["latest_commit_sha"] == "c0ffee1234567890abcdef1234567890abcdef12"

    # Verify audit log recorded
    logs = db_session.query(AuditLog).filter(
        AuditLog.contract_id == contract.id,
        AuditLog.action == "GITHUB_COMMITS_PUSHED",
    ).all()
    assert len(logs) >= 1


@pytest.mark.asyncio
async def test_github_webhook_pr_unmerged_ignored(client, db_session, funded_contract):
    contract = funded_contract
    m1 = contract.milestones[0]

    # PR closed but not merged
    payload = {
        "action": "closed",
        "pull_request": {
            "number": 14,
            "title": f"WIP: Abandoned attempt for {m1.id}",
            "merged": False,
            "html_url": "https://github.com/Sourabh-Kumar04/RasoTrustBridge/pull/14",
            "head": {"ref": f"feat/{m1.id}", "sha": "1111222233334444555566667777888899990000"},
        },
    }
    raw, sig = sign_payload(payload)

    res = client.post(
        "/api/v1/webhooks/github",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": sig,
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ignored"

    # Milestone should still be FUNDED
    db_session.refresh(m1)
    assert m1.status == MilestoneStatus.FUNDED


@pytest.mark.asyncio
async def test_github_webhook_pr_merged_triggers_verification(client, db_session, funded_contract):
    contract = funded_contract
    m1 = contract.milestones[0]
    assert m1.status == MilestoneStatus.FUNDED

    merge_commit_sha = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
    payload = {
        "action": "closed",
        "repository": {"full_name": "Sourabh-Kumar04/RasoTrustBridge"},
        "pull_request": {
            "number": 42,
            "title": f"feat: Complete authentication & database models [{m1.id}]",
            "body": f"Resolves deliverables for milestone {m1.id}. Passes all tests.",
            "merged": True,
            "merged_at": "2026-10-19T14:30:00Z",
            "merged_by": {"login": "carlos-mendez"},
            "merge_commit_sha": merge_commit_sha,
            "html_url": "https://github.com/Sourabh-Kumar04/RasoTrustBridge/pull/42",
            "head": {
                "ref": "feature/auth-models",
                "sha": merge_commit_sha,
            },
        },
    }
    raw, sig = sign_payload(payload)

    res = client.post(
        "/api/v1/webhooks/github",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": sig,
        },
    )
    assert res.status_code == 200
    data = res.json()

    # Core assertion: PR merge triggered AI verification and verified milestone
    assert data["event"] == "pull_request"
    assert data["status"] == "verified"
    assert data["milestone_id"] == m1.id
    assert data["milestone_status"] == "VERIFIED"
    assert data["is_approved"] is True
    assert data["verification_score"] >= 0.80

    # Refresh DB session and verify milestone state in database
    db_session.refresh(m1)
    assert m1.status == MilestoneStatus.VERIFIED
    assert m1.verification_score >= 0.80
    assert m1.verified_at is not None
    assert m1.verified_by == "AI_ENGINE_V1"
    assert m1.evidence_url == "https://github.com/Sourabh-Kumar04/RasoTrustBridge/pull/42"
    assert m1.evidence_payload["commit_sha"] == merge_commit_sha

    # Verify audit trail contains the webhook-specific log entry
    logs = db_session.query(AuditLog).filter(
        AuditLog.contract_id == contract.id,
        AuditLog.milestone_id == m1.id,
        AuditLog.action == "GITHUB_PR_MERGED_VERIFIED",
    ).all()
    assert len(logs) == 1
    assert logs[0].actor_role == ActorRole.SYSTEM
    assert logs[0].metadata_json["is_approved"] is True


@pytest.mark.asyncio
async def test_github_webhook_pr_merged_with_contract_and_order_index(client, db_session, funded_contract):
    """
    Tests finding linked milestone when PR references contract ID and milestone index
    e.g. branch 'feature/ct_12345/milestone-2'
    """
    contract = funded_contract
    m2 = contract.milestones[1]  # Milestone order index 2
    assert m2.status == MilestoneStatus.FUNDED

    merge_sha = "8f3b2c1d0e9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c"
    payload = {
        "action": "closed",
        "repository": {"full_name": "Sourabh-Kumar04/RasoTrustBridge"},
        "pull_request": {
            "number": 43,
            "title": f"feat: Implement Stripe and PayPal gateway [{contract.id} Milestone 2]",
            "body": "Completes milestone 2 payment webhooks.",
            "merged": True,
            "merged_at": "2026-10-19T16:00:00Z",
            "merged_by": {"login": "sarah-jenkins"},
            "merge_commit_sha": merge_sha,
            "html_url": "https://github.com/Sourabh-Kumar04/RasoTrustBridge/pull/43",
            "head": {
                "ref": f"feat/{contract.id}-m2",
                "sha": merge_sha,
            },
        },
    }
    raw, sig = sign_payload(payload)

    res = client.post(
        "/api/v1/webhooks/github",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": sig,
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "verified"
    assert data["milestone_id"] == m2.id
    assert data["milestone_status"] == "VERIFIED"

    db_session.refresh(m2)
    assert m2.status == MilestoneStatus.VERIFIED
