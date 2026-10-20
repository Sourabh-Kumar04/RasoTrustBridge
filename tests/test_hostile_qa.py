import json
import uuid
from datetime import datetime, timedelta, timezone
import pytest

from app.models.enums import ContractStatus, MilestoneStatus, FXStrategy, ActorRole, DisputeStatus, LedgerEntryType
from app.models.contract import Contract
from app.models.milestone import Milestone
from app.models.payment import Stake, AuditLog, DisputeRecord
from app.models.ledger import LedgerEntry
from app.models.user import User
from app.schemas.contract import ContractCreateSchema, MilestoneCreateSchema, AcceptanceCriterionSchema, PostStakeRequest
from app.schemas.milestone import MilestoneSubmitRequest, MilestoneReleaseRequest, MilestoneDisputeRequest, MilestoneVerifyRequest
from app.schemas.negotiation import NegotiationRequestSchema
from app.services.contract_service import contract_service
from app.services.dispute_service import dispute_service
from app.services.negotiator_service import negotiator_service
from app.services.stake_service import stake_service
from app.services.reputation_service import reputation_service
from tests.test_github_webhooks import sign_payload


# ==============================================================================
# 1. NEGOTIATOR HOSTILE ATTACKS
# ==============================================================================

@pytest.mark.asyncio
async def test_attack_negotiator_zero_budget_rejected_no_deal(db_session):
    """
    Hostile Attack: Attempt to exploit the negotiator by proposing $0 budget.
    Previously, 0 budget caused division by zero or was treated as 0% gap, forcing consensus at $2,500.
    Fix: Negotiator detects non-positive budget and terminates immediately with NO_DEAL.
    """
    req = NegotiationRequestSchema(
        buyer_brief="Build full production backend. Budget $0 USD, 14 days.",
        seller_brief="Senior engineer. Ask $5,000 USD, 14 days.",
        client_trust_score=50.0,
        developer_trust_score=50.0,
        quick_mode=True,
        force_rule_based=True,
        auto_create_contract=False,
    )
    res = await negotiator_service.run_negotiation(db_session, req)
    assert res.status == "no_deal"
    assert res.agreed_terms is None
    assert "greater than zero" in res.termination_reason.lower()


@pytest.mark.asyncio
async def test_attack_negotiator_negative_budget_rejected(db_session):
    """
    Hostile Attack: Propose negative budget to invert economic polarity.
    Fix: Terminated with NO_DEAL.
    """
    req = NegotiationRequestSchema(
        buyer_brief="Build payment service. Budget -$1,000 USD, 14 days.",
        seller_brief="Developer. Ask $3,000 USD, 14 days.",
        client_trust_score=50.0,
        developer_trust_score=50.0,
        quick_mode=True,
        force_rule_based=True,
        auto_create_contract=False,
    )
    res = await negotiator_service.run_negotiation(db_session, req)
    assert res.status == "no_deal"
    assert res.agreed_terms is None
    assert "greater than zero" in res.termination_reason.lower()


@pytest.mark.asyncio
async def test_attack_negotiator_absurd_timeline_zero_days(db_session):
    """
    Hostile Attack: Impossible timeline of 0 days.
    Fix: Terminated with NO_DEAL.
    """
    req = NegotiationRequestSchema(
        buyer_brief="Build app. Budget $3,000 USD, 0 days delivery.",
        seller_brief="Developer. Ask $3,500 USD, 14 days delivery.",
        client_trust_score=50.0,
        developer_trust_score=50.0,
        quick_mode=True,
        force_rule_based=True,
        auto_create_contract=False,
    )
    res = await negotiator_service.run_negotiation(db_session, req)
    assert res.status == "no_deal"
    assert res.agreed_terms is None
    assert "timeline" in res.termination_reason.lower()


@pytest.mark.asyncio
async def test_attack_negotiator_absurd_timeline_100_years(db_session):
    """
    Hostile Attack: Absurd timeline exceeding 10 years (e.g. 50,000 days).
    Fix: Terminated with NO_DEAL.
    """
    req = NegotiationRequestSchema(
        buyer_brief="Build app. Budget $3,000 USD, 50000 days.",
        seller_brief="Developer. Ask $3,500 USD, 14 days.",
        client_trust_score=50.0,
        developer_trust_score=50.0,
        quick_mode=True,
        force_rule_based=True,
        auto_create_contract=False,
    )
    res = await negotiator_service.run_negotiation(db_session, req)
    assert res.status == "no_deal"
    assert res.agreed_terms is None
    assert "timeline" in res.termination_reason.lower()


# ==============================================================================
# 2. ESCROW & AUTHORIZATION PRIVILEGE ESCALATION ATTACKS
# ==============================================================================

@pytest.fixture
def auth_parties(unauth_client):
    """Helper fixture providing separate client and freelancer authenticated tokens and users."""
    res_client = unauth_client.post(
        "/api/v1/auth/register",
        json={
            "email": "sarah.client@buyer.org",
            "password": "Password123!",
            "role": "client",
            "display_name": "Sarah Client",
        },
    )
    client_token = res_client.json()["access_token"]
    client_id = res_client.json()["user"]["id"]

    res_dev = unauth_client.post(
        "/api/v1/auth/register",
        json={
            "email": "carlos.freelancer@dev.org",
            "password": "Password123!",
            "role": "freelancer",
            "display_name": "Carlos Dev",
        },
    )
    dev_token = res_dev.json()["access_token"]
    dev_id = res_dev.json()["user"]["id"]

    return {
        "client_token": client_token,
        "client_id": client_id,
        "client_email": "sarah.client@buyer.org",
        "dev_token": dev_token,
        "dev_id": dev_id,
        "dev_email": "carlos.freelancer@dev.org",
    }


def test_attack_freelancer_privilege_escalation_cannot_release_funds(unauth_client, auth_parties):
    """
    Hostile Attack: Freelancer calls /milestones/{id}/release to release escrow funds to themselves.
    Previously, check_contract_access allowed anyone on the contract (client or freelancer).
    Fix: release_milestone strictly enforces that only the contract client (or admin) can release funds.
    """
    c_token = auth_parties["client_token"]
    d_token = auth_parties["dev_token"]

    # 1. Client creates contract
    c_res = unauth_client.post(
        "/api/v1/contracts",
        json={
            "title": "Escrow Protection Contract",
            "brief": "Secure payments",
            "client_name": "Sarah Client",
            "client_email": auth_parties["client_email"],
            "developer_name": "Carlos Dev",
            "developer_email": auth_parties["dev_email"],
            "base_currency": "USD",
            "payout_currency": "EUR",
            "total_amount": 500.0,
            "milestones": [
                {
                    "title": "M1 Architecture",
                    "description": "Scaffold",
                    "amount": 500.0,
                    "order_index": 1,
                }
            ],
        },
        headers={"Authorization": f"Bearer {c_token}"},
    )
    assert c_res.status_code == 201
    contract_id = c_res.json()["id"]
    milestone_id = c_res.json()["milestones"][0]["id"]

    # 2. Client funds contract
    fund_res = unauth_client.post(
        f"/api/v1/contracts/{contract_id}/fund",
        json={"paypal_order_id": "ORDER-PRIV-TEST"},
        headers={"Authorization": f"Bearer {c_token}"},
    )
    assert fund_res.status_code == 200

    # 3. Developer submits milestone
    sub_res = unauth_client.post(
        f"/api/v1/milestones/{milestone_id}/submit",
        json={"evidence_url": "https://github.com/Sourabh-Kumar04/RasoTrustBridge/pull/1"},
        headers={"Authorization": f"Bearer {d_token}"},
    )
    assert sub_res.status_code == 200

    # 4. Hostile Attack: Freelancer attempts to call release on their own milestone -> 403 Forbidden!
    hack_res = unauth_client.post(
        f"/api/v1/milestones/{milestone_id}/release",
        json={"release_note": "Self payout hack!"},
        headers={"Authorization": f"Bearer {d_token}"},
    )
    assert hack_res.status_code == 403
    assert "only the client can release milestone funds" in hack_res.json()["detail"].lower()


def test_attack_freelancer_cannot_override_verification_score(unauth_client, auth_parties):
    """
    Hostile Attack: Freelancer calls /milestones/{id}/verify with override_score=1.0 to self-approve.
    Fix: Only client can provide override_score; returns 403 for freelancer.
    """
    c_token = auth_parties["client_token"]
    d_token = auth_parties["dev_token"]

    c_res = unauth_client.post(
        "/api/v1/contracts",
        json={
            "title": "Verification Override Security Test",
            "brief": "Secure payments",
            "client_name": "Sarah Client",
            "client_email": auth_parties["client_email"],
            "developer_name": "Carlos Dev",
            "developer_email": auth_parties["dev_email"],
            "base_currency": "USD",
            "payout_currency": "EUR",
            "total_amount": 500.0,
            "milestones": [{"title": "M1", "description": "Test", "amount": 500.0, "order_index": 1}],
        },
        headers={"Authorization": f"Bearer {c_token}"},
    )
    m_id = c_res.json()["milestones"][0]["id"]

    hack_res = unauth_client.post(
        f"/api/v1/milestones/{m_id}/verify",
        json={"override_score": 1.0, "notes": "I approve myself!"},
        headers={"Authorization": f"Bearer {d_token}"},
    )
    assert hack_res.status_code == 403
    assert "only the client can provide a manual verification override score" in hack_res.json()["detail"].lower()


def test_attack_client_cannot_submit_deliverable_for_freelancer(unauth_client, auth_parties):
    """
    Hostile Attack: Client calls submit on milestone pretending to be the developer.
    Fix: submit_milestone strictly enforces that only the assigned freelancer can submit deliverables.
    """
    c_token = auth_parties["client_token"]

    c_res = unauth_client.post(
        "/api/v1/contracts",
        json={
            "title": "Deliverable Impersonation Test",
            "brief": "Secure payments",
            "client_name": "Sarah Client",
            "client_email": auth_parties["client_email"],
            "developer_name": "Carlos Dev",
            "developer_email": auth_parties["dev_email"],
            "base_currency": "USD",
            "payout_currency": "EUR",
            "total_amount": 500.0,
            "milestones": [{"title": "M1", "description": "Test", "amount": 500.0, "order_index": 1}],
        },
        headers={"Authorization": f"Bearer {c_token}"},
    )
    m_id = c_res.json()["milestones"][0]["id"]

    hack_res = unauth_client.post(
        f"/api/v1/milestones/{m_id}/submit",
        json={"evidence_url": "https://github.com/imposter/repo"},
        headers={"Authorization": f"Bearer {c_token}"},
    )
    assert hack_res.status_code == 403
    assert "only the assigned developer/freelancer can submit milestone deliverables" in hack_res.json()["detail"].lower()


def test_attack_freelancer_cannot_fund_contract(unauth_client, auth_parties):
    """
    Hostile Attack: Freelancer calls /contracts/{id}/fund.
    Fix: Only the client can fund the escrow.
    """
    c_token = auth_parties["client_token"]
    d_token = auth_parties["dev_token"]

    c_res = unauth_client.post(
        "/api/v1/contracts",
        json={
            "title": "Unauthorized Funding Test",
            "brief": "Secure payments",
            "client_name": "Sarah Client",
            "client_email": auth_parties["client_email"],
            "developer_name": "Carlos Dev",
            "developer_email": auth_parties["dev_email"],
            "base_currency": "USD",
            "payout_currency": "EUR",
            "total_amount": 500.0,
            "milestones": [{"title": "M1", "description": "Test", "amount": 500.0, "order_index": 1}],
        },
        headers={"Authorization": f"Bearer {c_token}"},
    )
    c_id = c_res.json()["id"]

    hack_res = unauth_client.post(
        f"/api/v1/contracts/{c_id}/fund",
        json={"paypal_order_id": "ORDER-HACK"},
        headers={"Authorization": f"Bearer {d_token}"},
    )
    assert hack_res.status_code == 403
    assert "only the client can fund the escrow" in hack_res.json()["detail"].lower()


# ==============================================================================
# 3. DISPUTE ABUSE ATTACKS
# ==============================================================================

@pytest.mark.asyncio
async def test_attack_cannot_dispute_already_released_milestone(db_session):
    """
    Hostile Attack: Attacker tries to open a dispute on an already RELEASED milestone.
    Previously, open_dispute had no check on milestone.status, allowing attacker to dispute post-payout.
    Fix: open_dispute rejects dispute on RELEASED milestone with 400 Bad Request.
    """
    schema = ContractCreateSchema(
        title="Dispute Abuse Protection Contract",
        brief="Test dispute guard",
        client_name="Buyer",
        client_email="buyer@test.com",
        developer_name="Dev",
        developer_email="dev@test.com",
        base_currency="USD",
        payout_currency="EUR",
        total_amount=500.0,
        milestones=[MilestoneCreateSchema(title="M1", description="Test", amount=500.0, order_index=1)],
    )
    contract = await contract_service.create_contract(db_session, schema)
    await contract_service.fund_contract(db_session, contract.id, "ORDER-DISP-001", "CAP-DISP-001")
    m = contract.milestones[0]
    await contract_service.submit_milestone(db_session, m.id, MilestoneSubmitRequest(evidence_url="https://github.com/test/repo"))
    await contract_service.release_milestone(db_session, m.id, MilestoneReleaseRequest(release_note="Paid"))
    assert m.status == MilestoneStatus.RELEASED

    with pytest.raises(Exception) as excinfo:
        dispute_service.open_dispute(
            db=db_session,
            contract=contract,
            milestone=m,
            initiator_role=ActorRole.CLIENT,
            initiator_email="buyer@test.com",
            reason="I want my money back after payout",
            description="Abusive dispute attempt",
        )
    assert "already released" in str(excinfo.value.detail).lower()


@pytest.mark.asyncio
async def test_attack_duplicate_dispute_spamming_blocked(db_session):
    """
    Hostile Attack: Repeatedly open disputes on the same milestone to repeatedly penalize trust score.
    Fix: open_dispute checks for existing open dispute and rejects with 400 Bad Request.
    """
    schema = ContractCreateSchema(
        title="Duplicate Dispute Spam Protection",
        brief="Test duplicate dispute guard",
        client_name="Buyer",
        client_email="buyer2@test.com",
        developer_name="Dev",
        developer_email="dev2@test.com",
        base_currency="USD",
        payout_currency="EUR",
        total_amount=500.0,
        milestones=[MilestoneCreateSchema(title="M1", description="Test", amount=500.0, order_index=1)],
    )
    contract = await contract_service.create_contract(db_session, schema)
    await contract_service.fund_contract(db_session, contract.id, "ORDER-DISP-002", "CAP-DISP-002")
    m = contract.milestones[0]

    # Open first dispute
    dispute_service.open_dispute(
        db=db_session,
        contract=contract,
        milestone=m,
        initiator_role=ActorRole.CLIENT,
        initiator_email="buyer2@test.com",
        reason="Work not as agreed",
        description="First dispute",
    )
    assert m.status == MilestoneStatus.DISPUTED

    # Hostile Attack: open second dispute on same milestone
    with pytest.raises(Exception) as excinfo:
        dispute_service.open_dispute(
            db=db_session,
            contract=contract,
            milestone=m,
            initiator_role=ActorRole.CLIENT,
            initiator_email="buyer2@test.com",
            reason="Spam dispute",
            description="Second dispute attempt",
        )
    assert "already has an active open dispute" in str(excinfo.value.detail).lower()


# ==============================================================================
# 4. WEBHOOK HOSTILE ATTACKS & MALFORMED PAYLOAD RESILIENCE
# ==============================================================================

def test_attack_webhook_malformed_json_syntax(client):
    """
    Hostile Attack: Attacker sends malformed non-JSON body.
    Fix: Returns 400 Bad Request, does not crash.
    """
    raw = b"{ malformed: json, syntax: error "
    secret = "trustbridge_github_webhook_secret_dev"
    import hmac, hashlib
    sig = "sha256=" + hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()

    res = client.post(
        "/api/v1/webhooks/github",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": sig,
        },
    )
    assert res.status_code == 400
    assert "malformed json" in res.json()["detail"].lower()


def test_attack_webhook_non_dict_root_element(client):
    """
    Hostile Attack: Attacker sends valid JSON that is a list or scalar, not an object.
    Fix: Validated and returns 400 Bad Request.
    """
    raw, sig = sign_payload([1, 2, 3, "evil_array"])
    res = client.post(
        "/api/v1/webhooks/github",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": sig,
        },
    )
    assert res.status_code == 400
    assert "root element must be an object" in res.json()["detail"].lower()


def test_attack_webhook_null_pull_request_handled_safely(client):
    """
    Hostile Attack: Attacker sends {"action": "closed", "pull_request": null}.
    Previously, pr.get('merged') crashed with 500 AttributeError: 'NoneType' object has no attribute 'get'.
    Fix: Safely handled, returns status 'malformed', HTTP 200 without 500 crash.
    """
    raw, sig = sign_payload({"action": "closed", "pull_request": None})
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
    assert data["status"] == "malformed"


def test_attack_webhook_null_commits_push_handled_safely(client):
    """
    Hostile Attack: Attacker sends {"ref": "refs/heads/main", "commits": null}.
    Previously, iteration over None crashed with 500 TypeError.
    Fix: Safely handled, returns status 'unmatched', HTTP 200 without 500 crash.
    """
    raw, sig = sign_payload({"ref": "refs/heads/main", "commits": None, "after": None})
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
    assert data["status"] == "unmatched"


def test_attack_webhook_replay_without_delivery_header_deduplicated(client):
    """
    Hostile Attack: Replay attack with missing X-GitHub-Delivery header.
    Fix: Dedup service hashes payload bytes and rejects replayed payload with status 'duplicate'.
    """
    payload = {"zen": "Replay attack resilience test.", "hook_id": 771122}
    raw, sig = sign_payload(payload)

    # 1. First send
    res1 = client.post(
        "/api/v1/webhooks/github",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "ping",
            "X-Hub-Signature-256": sig,
        },
    )
    assert res1.status_code == 200
    assert res1.json()["status"] == "pong"

    # 2. Replay identical payload without delivery header
    res2 = client.post(
        "/api/v1/webhooks/github",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "ping",
            "X-Hub-Signature-256": sig,
        },
    )
    assert res2.status_code == 200
    assert res2.json()["status"] == "duplicate"


# ==============================================================================
# 5. GITHUB VERIFICATION HARDENING (POLU'S CRITIQUE: ANTI-GAMING)
# ==============================================================================

@pytest.fixture
async def funded_github_contract(db_session):
    schema = ContractCreateSchema(
        title="Anti-Gaming Verification Contract",
        brief="Testing self-merge and empty PR detection",
        client_name="Reviewer Lead",
        client_email="reviewer@lead.org",
        developer_name="Carlos Dev",
        developer_email="carlos@dev.org",
        base_currency="USD",
        payout_currency="EUR",
        total_amount=1000.0,
        milestones=[
            MilestoneCreateSchema(
                title="M1 Delivery",
                description="Deliver auth logic",
                amount=1000.0,
                order_index=1,
            )
        ],
    )
    contract = await contract_service.create_contract(db_session, schema)
    return await contract_service.fund_contract(db_session, contract.id, "ORDER-GH-HARDEN-1", "CAP-GH-HARDEN-1")


@pytest.mark.asyncio
async def test_anti_gaming_self_merge_flagged_pending_review(client, db_session, funded_github_contract):
    """
    Polu's Critique: 'empty PR, self-merged' passes today.
    Self-Merge Rule: if merger == author and no other reviewers, require manual review (milestone -> PENDING_REVIEW).
    """
    contract = funded_github_contract
    m1 = contract.milestones[0]

    payload = {
        "action": "closed",
        "repository": {"full_name": "Sourabh-Kumar04/RasoTrustBridge"},
        "pull_request": {
            "number": 51,
            "title": f"feat: Self merged PR [{m1.id}]",
            "body": f"Delivers {m1.id}",
            "merged": True,
            "merged_at": "2026-10-20T10:00:00Z",
            "user": {"login": "carlos-mendez"},       # Author
            "merged_by": {"login": "carlos-mendez"},   # Self-merger!
            "merge_commit_sha": "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678",
            "html_url": "https://github.com/Sourabh-Kumar04/RasoTrustBridge/pull/51",
            "changed_files": 3,
            "additions": 45,
            "deletions": 5,
        },
        "reviews": [],  # No external reviewers!
    }
    raw, sig = sign_payload(payload)

    res = client.post(
        "/api/v1/webhooks/github",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": sig,
            "X-GitHub-Delivery": f"gh-deliv-{uuid.uuid4().hex[:8]}",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "pending_review"
    assert data["milestone_status"] == "PENDING_REVIEW"
    assert data["is_approved"] is False
    assert any("self-merge detected" in f.lower() for f in data["flags"])

    # Verify DB milestone state
    db_session.refresh(m1)
    assert m1.status == MilestoneStatus.PENDING_REVIEW
    assert m1.verification_score == 0.50
    assert m1.evidence_payload["diff_stat"]["total_lines_changed"] == 50


@pytest.mark.asyncio
async def test_anti_gaming_empty_pr_insignificant_changes_flagged(client, db_session, funded_github_contract):
    """
    Polu's Critique: Reject PRs with zero meaningful changes (< 5 lines changed across < 2 files = PENDING_REVIEW).
    """
    contract = funded_github_contract
    m1 = contract.milestones[0]

    payload = {
        "action": "closed",
        "repository": {"full_name": "Sourabh-Kumar04/RasoTrustBridge"},
        "pull_request": {
            "number": 52,
            "title": f"fix: update typo [{m1.id}]",
            "body": f"Delivers {m1.id}",
            "merged": True,
            "merged_at": "2026-10-20T10:15:00Z",
            "user": {"login": "carlos-mendez"},
            "merged_by": {"login": "sarah-reviewer"},  # Not self-merge
            "merge_commit_sha": "f1e2d3c4b5a60718293a4b5c6d7e8f9012345678",
            "html_url": "https://github.com/Sourabh-Kumar04/RasoTrustBridge/pull/52",
            "changed_files": 1,  # Only 1 file!
            "additions": 2,      # Only 2 lines!
            "deletions": 1,
        },
        "reviews": [
            {"user": {"login": "sarah-reviewer"}, "state": "APPROVED"}
        ],
    }
    raw, sig = sign_payload(payload)

    res = client.post(
        "/api/v1/webhooks/github",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": sig,
            "X-GitHub-Delivery": f"gh-deliv-{uuid.uuid4().hex[:8]}",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "pending_review"
    assert data["milestone_status"] == "PENDING_REVIEW"
    assert any("insignificant changes" in f.lower() for f in data["flags"])

    db_session.refresh(m1)
    assert m1.status == MilestoneStatus.PENDING_REVIEW


@pytest.mark.asyncio
async def test_anti_gaming_author_only_review_rejected(client, db_session, funded_github_contract):
    """
    Polu's Critique: Reviewer approval required from someone OTHER than author.
    If review is by the author themselves, flag for manual review.
    """
    contract = funded_github_contract
    m1 = contract.milestones[0]

    payload = {
        "action": "closed",
        "repository": {"full_name": "Sourabh-Kumar04/RasoTrustBridge"},
        "pull_request": {
            "number": 53,
            "title": f"feat: Author self-reviewed PR [{m1.id}]",
            "body": f"Delivers {m1.id}",
            "merged": True,
            "merged_at": "2026-10-20T10:30:00Z",
            "user": {"login": "carlos-mendez"},
            "merged_by": {"login": "external-merger"},
            "merge_commit_sha": "d4c3b2a1e5f60718293a4b5c6d7e8f9012345678",
            "html_url": "https://github.com/Sourabh-Kumar04/RasoTrustBridge/pull/53",
            "changed_files": 4,
            "additions": 100,
            "deletions": 10,
        },
        "reviews": [
            {"user": {"login": "carlos-mendez"}, "state": "APPROVED"}  # Author reviewed themselves!
        ],
    }
    raw, sig = sign_payload(payload)

    res = client.post(
        "/api/v1/webhooks/github",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": sig,
            "X-GitHub-Delivery": f"gh-deliv-{uuid.uuid4().hex[:8]}",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "pending_review"
    assert data["milestone_status"] == "PENDING_REVIEW"
    assert any("someone other than the author" in f.lower() for f in data["flags"])


@pytest.mark.asyncio
async def test_anti_gaming_valid_peer_reviewed_pr_verified_and_anchored(client, db_session, funded_github_contract):
    """
    Valid PR with:
    - Author != merger (or external peer approval)
    - Review from third-party reviewer (approved)
    - Meaningful diff stat (4 files, 110 lines changed)
    -> Transitions to VERIFIED, anchors merge commit SHA + diff stat.
    """
    contract = funded_github_contract
    m1 = contract.milestones[0]
    merge_sha = "1234567890abcdef1234567890abcdef12345678"

    payload = {
        "action": "closed",
        "repository": {"full_name": "Sourabh-Kumar04/RasoTrustBridge"},
        "pull_request": {
            "number": 54,
            "title": f"feat: Peer-reviewed deliverable [{m1.id}]",
            "body": f"Delivers {m1.id} with complete unit tests",
            "merged": True,
            "merged_at": "2026-10-20T10:45:00Z",
            "user": {"login": "carlos-mendez"},
            "merged_by": {"login": "sarah-jenkins"},
            "merge_commit_sha": merge_sha,
            "html_url": "https://github.com/Sourabh-Kumar04/RasoTrustBridge/pull/54",
            "changed_files": 4,
            "additions": 95,
            "deletions": 15,
        },
        "reviews": [
            {"user": {"login": "sarah-jenkins"}, "state": "APPROVED"}
        ],
    }
    raw, sig = sign_payload(payload)

    res = client.post(
        "/api/v1/webhooks/github",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": sig,
            "X-GitHub-Delivery": f"gh-deliv-{uuid.uuid4().hex[:8]}",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "verified"
    assert data["milestone_status"] == "VERIFIED"
    assert data["is_approved"] is True

    # Check commit-hash anchoring and diff stat in evidence
    db_session.refresh(m1)
    assert m1.status == MilestoneStatus.VERIFIED
    assert m1.evidence_payload["merge_commit_sha"] == merge_sha
    assert m1.evidence_payload["diff_stat"]["changed_files"] == 4
    assert m1.evidence_payload["diff_stat"]["total_lines_changed"] == 110
    assert m1.evidence_payload["approving_reviewers"] == ["sarah-jenkins"]


# ==============================================================================
# 6. REPUTATION WITH ECONOMIC SKIN (STAKING & NEW-ACCOUNT FRICTION)
# ==============================================================================

def test_new_account_friction_trust_score_starts_at_30(unauth_client):
    """
    Harmer's Critique: Bad actor's cost to create new account is zero.
    New-Account Friction: Trust score starts at 30 (not 50) for accounts < 7 days old.
    """
    reg_res = unauth_client.post(
        "/api/v1/auth/register",
        json={
            "email": f"sybil_{uuid.uuid4().hex[:6]}@scam.org",
            "password": "Password123!",
            "role": "freelancer",
            "display_name": "New Actor",
        },
    )
    assert reg_res.status_code == 201
    user_data = reg_res.json()["user"]
    assert user_data["trust_score"] == 30.0


def test_high_value_contract_requires_10_percent_stake_for_new_freelancer(unauth_client):
    """
    Harmer's Critique: Contracts >= $1,000 require freelancer to have completed >= 1 prior deal OR post 10% stake.
    Posting only 5% on a $1,500 contract without prior deals fails with HTTP 400.
    Posting 10% succeeds.
    """
    # 1. Register Client and new Freelancer
    c_res = unauth_client.post(
        "/api/v1/auth/register",
        json={"email": f"client_{uuid.uuid4().hex[:6]}@co.org", "password": "Pass123!", "role": "client", "display_name": "Client"},
    )
    c_token = c_res.json()["access_token"]
    c_email = c_res.json()["user"]["email"]

    d_res = unauth_client.post(
        "/api/v1/auth/register",
        json={"email": f"new_dev_{uuid.uuid4().hex[:6]}@co.org", "password": "Pass123!", "role": "freelancer", "display_name": "New Dev"},
    )
    d_token = d_res.json()["access_token"]
    d_email = d_res.json()["user"]["email"]

    # 2. Client creates high-value contract ($1,500 >= $1,000 threshold)
    c_contract = unauth_client.post(
        "/api/v1/contracts",
        json={
            "title": "High Value Contract",
            "brief": "Enterprise architecture",
            "client_name": "Client",
            "client_email": c_email,
            "developer_name": "New Dev",
            "developer_email": d_email,
            "base_currency": "USD",
            "payout_currency": "EUR",
            "total_amount": 1500.0,
            "milestones": [{"title": "M1", "description": "Core", "amount": 1500.0, "order_index": 1}],
        },
        headers={"Authorization": f"Bearer {c_token}"},
    ).json()
    contract_id = c_contract["id"]

    # 3. New Freelancer attempts to post only default 5% stake ($75) -> Fails with 400!
    bad_stake_res = unauth_client.post(
        f"/api/v1/contracts/{contract_id}/stake",
        json={"stake_percentage": 5.0, "stake_amount": 75.0},
        headers={"Authorization": f"Bearer {d_token}"},
    )
    assert bad_stake_res.status_code == 400
    assert "must post a 10.0% security deposit stake" in bad_stake_res.json()["detail"].lower()

    # 4. New Freelancer posts required 10% stake ($150) -> Succeeds!
    good_stake_res = unauth_client.post(
        f"/api/v1/contracts/{contract_id}/stake",
        json={"stake_percentage": 10.0, "stake_amount": 150.0},
        headers={"Authorization": f"Bearer {d_token}"},
    )
    assert good_stake_res.status_code == 200
    contract_data = good_stake_res.json()
    assert contract_data["stake"] is not None
    assert contract_data["stake"]["amount"] == 150.0
    assert contract_data["stake"]["stake_percentage"] == 10.0
    assert contract_data["stake"]["status"] == "HELD"


@pytest.mark.asyncio
async def test_stake_posted_and_returned_in_full_on_success(db_session):
    """
    Staking Lifecycle: Successful release -> stake returned in full.
    Verifies Stake model, ledger entries (STAKE_POSTED, STAKE_RETURNED), and state transitions.
    """
    user_dev = User(
        id=f"usr_dev_{uuid.uuid4().hex[:6]}",
        email=f"stake_dev_{uuid.uuid4().hex[:6]}@test.com",
        hashed_password="hashed_pass_123",
        role="freelancer",
        display_name="Dev",
        trust_score=50.0,
        completed_deals=2,  # Has completed prior deals
    )
    db_session.add(user_dev)
    db_session.commit()

    schema = ContractCreateSchema(
        title="Staking Success Test Contract",
        brief="Test stake return on completion",
        client_name="Buyer",
        client_email="buyer_stake@test.com",
        developer_name="Dev",
        developer_email=user_dev.email,
        freelancer_id=user_dev.id,
        base_currency="USD",
        payout_currency="EUR",
        total_amount=1000.0,
        milestones=[MilestoneCreateSchema(title="M1", description="Test", amount=1000.0, order_index=1)],
    )
    contract = await contract_service.create_contract(db_session, schema)
    await contract_service.fund_contract(db_session, contract.id, "ORDER-STAKE-001", "CAP-STAKE-001")

    # 1. Freelancer posts 5% stake ($50)
    stk = stake_service.post_stake(
        db=db_session,
        contract=contract,
        freelancer=user_dev,
        paypal_order_id="ORDER-STK-POSTED",
        paypal_capture_id="CAP-STK-POSTED",
    )
    assert stk.status == "HELD"
    assert stk.amount == 50.0

    # Verify ledger has STAKE_POSTED entry
    entries = db_session.query(LedgerEntry).filter(
        LedgerEntry.contract_id == contract.id,
        LedgerEntry.entry_type == LedgerEntryType.STAKE_POSTED,
    ).all()
    assert len(entries) == 1
    assert entries[0].amount == 50.0

    # 2. Freelancer submits and client releases milestone
    m1 = contract.milestones[0]
    await contract_service.submit_milestone(db_session, m1.id, MilestoneSubmitRequest(evidence_url="https://github.com/test/repo"))
    await contract_service.release_milestone(db_session, m1.id, MilestoneReleaseRequest(release_note="Full delivery accepted"))

    # 3. Assert contract COMPLETED and stake RETURNED in full
    db_session.refresh(contract)
    db_session.refresh(stk)
    assert contract.status == ContractStatus.COMPLETED
    assert stk.status == "RETURNED"
    assert stk.returned_amount == 50.0

    # Verify ledger has STAKE_RETURNED entry
    ret_entries = db_session.query(LedgerEntry).filter(
        LedgerEntry.contract_id == contract.id,
        LedgerEntry.entry_type == LedgerEntryType.STAKE_RETURNED,
    ).all()
    assert len(ret_entries) == 1
    assert ret_entries[0].amount == 50.0


@pytest.mark.asyncio
async def test_stake_slashed_on_lost_dispute(db_session):
    """
    Staking Lifecycle: Dispute lost by freelancer (client refund) -> stake slashed.
    Verifies Stake model, ledger entries (STAKE_SLASHED), and status.
    """
    user_dev = User(
        id=f"usr_slash_{uuid.uuid4().hex[:6]}",
        email=f"slash_dev_{uuid.uuid4().hex[:6]}@test.com",
        hashed_password="hashed_pass_123",
        role="freelancer",
        display_name="Dev",
        trust_score=50.0,
        completed_deals=1,
    )
    db_session.add(user_dev)
    db_session.commit()

    schema = ContractCreateSchema(
        title="Staking Slash Test Contract",
        brief="Test stake slash on lost dispute",
        client_name="Buyer",
        client_email="buyer_slash@test.com",
        developer_name="Dev",
        developer_email=user_dev.email,
        freelancer_id=user_dev.id,
        base_currency="USD",
        payout_currency="EUR",
        total_amount=1000.0,
        milestones=[MilestoneCreateSchema(title="M1", description="Test", amount=1000.0, order_index=1)],
    )
    contract = await contract_service.create_contract(db_session, schema)
    await contract_service.fund_contract(db_session, contract.id, "ORDER-STAKE-002", "CAP-STAKE-002")

    # 1. Freelancer posts 5% stake ($50)
    stk = stake_service.post_stake(db=db_session, contract=contract, freelancer=user_dev)
    assert stk.status == "HELD"
    assert stk.amount == 50.0

    # 2. Client opens dispute
    m1 = contract.milestones[0]
    dispute = dispute_service.open_dispute(
        db=db_session,
        contract=contract,
        milestone=m1,
        initiator_role=ActorRole.CLIENT,
        initiator_email="buyer_slash@test.com",
        reason="Developer disappeared without delivering",
        description="Ghosted project",
    )

    # 3. Dispute resolved in favor of client (CLIENT_REFUND) -> Freelancer lost!
    dispute_service.resolve_dispute(
        db=db_session,
        dispute=dispute,
        resolution_status=DisputeStatus.RESOLVED_CLIENT_REFUND,
        resolution_notes="Full client refund justified. Freelancer non-performance.",
    )

    # 4. Stake must be SLASHED
    db_session.refresh(stk)
    assert stk.status == "SLASHED"
    assert stk.slashed_amount == 50.0

    # Verify ledger has STAKE_SLASHED entry
    slash_entries = db_session.query(LedgerEntry).filter(
        LedgerEntry.contract_id == contract.id,
        LedgerEntry.entry_type == LedgerEntryType.STAKE_SLASHED,
    ).all()
    assert len(slash_entries) == 1
    assert slash_entries[0].amount == 50.0
