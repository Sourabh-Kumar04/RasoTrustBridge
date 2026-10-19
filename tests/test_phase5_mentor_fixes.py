import os
import json
import uuid
from datetime import datetime, timedelta
import pytest
from app.models.enums import ContractStatus, MilestoneStatus, FXStrategy, LedgerEntryType, DisputeStatus
from app.models.ledger import LedgerEntry
from app.models.milestone import Milestone
from app.models.contract import Contract
from app.models.payment import AuditLog
from app.models.user import User
from app.schemas.contract import ContractCreateSchema, MilestoneCreateSchema, AcceptanceCriterionSchema
from app.schemas.negotiation import NegotiationRequestSchema
from app.services.contract_service import contract_service
from app.services.paypal_client import paypal_client
from app.services.reputation_service import reputation_service
from app.services.negotiator_service import negotiator_service
from app.services.ai_provider import get_ai_provider, OllamaProvider, OpenAIProvider
from tests.test_github_webhooks import sign_payload


@pytest.mark.asyncio
async def test_github_webhook_deduplication(client, db_session):
    """
    Mentor Critique 2: The first outage will be a webhook that fires twice.
    Verifies that GitHub webhook events are deduplicated by X-GitHub-Delivery header.
    The second identical delivery returns HTTP 200 with duplicate status, preventing double-processing.
    """
    delivery_id = f"gh-deliv-{uuid.uuid4().hex[:12]}"
    payload = {
        "zen": "Encourage flow.",
        "hook_id": 99991,
        "hook": {"type": "Repository"},
    }
    raw, sig = sign_payload(payload)

    headers = {
        "X-GitHub-Delivery": delivery_id,
        "X-GitHub-Event": "ping",
        "X-Hub-Signature-256": sig,
        "Content-Type": "application/json",
    }

    # First delivery: processed successfully
    res1 = client.post("/api/v1/webhooks/github", data=raw, headers=headers)
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["status"] in ("ok", "processed", "pong")

    # Second delivery (replay with same X-GitHub-Delivery): rejected as duplicate with 200
    res2 = client.post("/api/v1/webhooks/github", data=raw, headers=headers)
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["status"] == "duplicate"
    assert data2["delivery_id"] == delivery_id


@pytest.mark.asyncio
async def test_paypal_webhook_deduplication(client, db_session):
    """
    Mentor Critique 2: PayPal webhook deduplication via transmission ID.
    Duplicate webhooks must return 200 with duplicate status rather than error or double-payout.
    """
    transmission_id = f"pp-trans-{uuid.uuid4().hex[:12]}"
    event_id = f"WH-EVENT-{uuid.uuid4().hex[:8]}"

    payload = {
        "id": event_id,
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "resource": {"id": "CAP-TEST-1234", "status": "COMPLETED"},
    }

    headers = {
        "paypal-transmission-id": transmission_id,
        "paypal-transmission-time": datetime.utcnow().isoformat() + "Z",
        "Content-Type": "application/json",
    }

    # First delivery
    res1 = client.post("/api/v1/paypal/webhook", json=payload, headers=headers)
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["status"] in ("received", "processed")

    # Second delivery
    res2 = client.post("/api/v1/paypal/webhook", json=payload, headers=headers)
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["status"] == "duplicate"
    assert data2["transmission_id"] == transmission_id


@pytest.mark.asyncio
async def test_payout_idempotency_key_prevents_duplicate_execution():
    """
    Mentor Critique 2: PayPal API calls must generate and enforce idempotency keys.
    Executing create_payout twice with same idempotency key must only execute once.
    """
    idemp_key = f"idemp-test-payout-{uuid.uuid4().hex[:10]}"

    payout1 = await paypal_client.create_payout(
        recipient_email="freelancer@international.dev",
        amount=750.0,
        currency="USD",
        note="Phase 5 Milestone Release",
        sender_item_id="ms_phase5_idem",
        idempotency_key=idemp_key,
    )
    assert payout1["payout_batch_id"] is not None
    assert payout1.get("idempotency_replayed") is not True

    # Call again with the exact same idempotency key
    payout2 = await paypal_client.create_payout(
        recipient_email="freelancer@international.dev",
        amount=750.0,
        currency="USD",
        note="Phase 5 Milestone Release Duplicate Attempt",
        sender_item_id="ms_phase5_idem",
        idempotency_key=idemp_key,
    )
    # Must return cached result with idempotency_replayed=True
    assert payout2["payout_batch_id"] == payout1["payout_batch_id"]
    assert payout2.get("idempotency_replayed") is True


def test_ledger_append_only_immutability(db_session):
    """
    Mentor Critique 3: Event-sourced ledger must be strictly append-only.
    Updates and deletions on LedgerEntry records must be rejected by SQLAlchemy event listeners.
    """
    # Create valid parent contract for foreign key
    contract = Contract(
        id=f"ct_led_{uuid.uuid4().hex[:8]}",
        title="Ledger Immutability Test",
        brief="Ledger immutability test brief",
        client_name="Alice",
        client_email="alice@test.org",
        developer_name="Bob",
        developer_email="bob@test.org",
        total_amount=1200.0,
        base_currency="USD",
        payout_currency="EUR",
        status=ContractStatus.ACTIVE,
    )
    db_session.add(contract)
    db_session.commit()

    entry = LedgerEntry(
        id=f"led_test_{uuid.uuid4().hex[:8]}",
        contract_id=contract.id,
        milestone_id=None,
        entry_type=LedgerEntryType.FUNDS_CAPTURED,
        amount=1200.0,
        currency="USD",
        paypal_reference="CAP-MOCK-IMMUTABLE",
        idempotency_key=f"idemp_led_{uuid.uuid4().hex[:8]}",
    )
    db_session.add(entry)
    db_session.commit()

    # Attempting to UPDATE an existing ledger entry must fail
    entry.amount = 9999.0
    with pytest.raises(ValueError, match="strictly append-only|immutable"):
        db_session.commit()

    db_session.rollback()

    # Attempting to DELETE an existing ledger entry must fail
    reloaded = db_session.query(LedgerEntry).filter(LedgerEntry.id == entry.id).first()
    assert reloaded is not None
    db_session.delete(reloaded)
    with pytest.raises(ValueError, match="cannot be deleted|immutable"):
        db_session.commit()

    db_session.rollback()


@pytest.mark.asyncio
async def test_buyer_timeout_auto_release(client, db_session):
    """
    Mentor Critique 4: Buyer timeout + auto-release when client ghosts.
    Milestones past expires_at automatically transition to VERIFIED with audit log entry.
    """
    contract_schema = ContractCreateSchema(
        title="Buyer Timeout Escrow Contract",
        brief="Testing automated milestone expiry and release",
        client_name="Silent Buyer",
        client_email="silent_buyer@test.corp",
        developer_name="Active Dev",
        developer_email="active_dev@freelance.org",
        base_currency="USD",
        payout_currency="EUR",
        total_amount=1000.0,
        buyer_timeout_days=5,
        fx_strategy=FXStrategy.LOCKED_AT_SIGNING,
        locked_fx_rate=0.92,
        milestones=[
            MilestoneCreateSchema(
                title="Frontend Prototype",
                description="Deliver responsive UI",
                amount=1000.0,
                order_index=1,
                acceptance_criteria=[
                    AcceptanceCriterionSchema(description="Code builds without errors", verification_method="automated_test")
                ],
            )
        ],
    )
    contract = await contract_service.create_contract(db_session, contract_schema)
    assert contract.buyer_timeout_days == 5

    await contract_service.fund_contract(
        db_session,
        contract.id,
        paypal_order_id="ORDER-TIMEOUT-TEST",
        paypal_capture_id="CAP-TIMEOUT-TEST",
    )

    milestone = contract.milestones[0]
    # Freelancer submits work
    milestone.status = MilestoneStatus.SUBMITTED
    # Simulate timeout: set expires_at 2 days in the past
    milestone.expires_at = datetime.utcnow() - timedelta(days=2)
    db_session.commit()

    # Trigger buyer timeout expiry check endpoint
    res = client.post("/api/v1/milestones/check-expiry")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert any(m["id"] == milestone.id for m in data)

    # Reload milestone and verify state
    db_session.expire_all()
    updated_ms = db_session.query(Milestone).filter(Milestone.id == milestone.id).first()
    assert updated_ms.status == MilestoneStatus.VERIFIED

    # Check that ledger entry was written for the auto-release
    ledger_entries = db_session.query(LedgerEntry).filter(
        LedgerEntry.contract_id == contract.id,
        LedgerEntry.entry_type == LedgerEntryType.MILESTONE_VERIFIED,
    ).all()
    assert len(ledger_entries) >= 1

    # Check that audit log has the auto-released entry
    logs = db_session.query(AuditLog).filter(
        AuditLog.contract_id == contract.id,
    ).all()
    assert any("auto-released: buyer timeout" in (l.description or "") for l in logs)


def test_reputation_trust_score_engine(db_session):
    """
    Mentor Critique 5: Trust score (0-100, default 50) updates on completion, dispute, and resolution.
    """
    # Create test user
    user = User(
        id=f"usr_rep_{uuid.uuid4().hex[:6]}",
        email=f"rep_{uuid.uuid4().hex[:6]}@trustbridge.network",
        hashed_password="hashed_test_password_123",
        role="developer",
        display_name="Reputable Dev",
        trust_score=50.0,
        completed_deals=0,
        disputed_deals=0,
    )
    db_session.add(user)
    db_session.commit()

    # 1. Milestone completed (+5 points)
    reputation_service.record_successful_release(db_session, user)
    assert user.trust_score == 55.0
    assert user.completed_deals == 1

    # 2. Dispute opened (-10 points)
    reputation_service.record_dispute_opened(db_session, client=None, freelancer=user)
    assert user.trust_score == 45.0
    assert user.disputed_deals == 1

    # 3. Dispute resolved in favor of user (+5 points)
    reputation_service.record_dispute_resolution(db_session, client=None, freelancer=user, resolution_status=DisputeStatus.RESOLVED_DEVELOPER_PAYOUT)
    assert user.trust_score == 50.0

    # 4. Dispute resolved against user (-5 points)
    reputation_service.record_dispute_resolution(db_session, client=None, freelancer=user, resolution_status=DisputeStatus.RESOLVED_CLIENT_REFUND)
    assert user.trust_score == 45.0

    # 5. Boundary tests: capped at 100, floored at 0
    score_max = reputation_service.clamp_score(150.0)
    assert score_max == 100.0
    score_min = reputation_service.clamp_score(-20.0)
    assert score_min == 0.0


@pytest.mark.asyncio
async def test_negotiator_walk_away_on_irreconcilable_gap(db_session):
    """
    Mentor Critique 6: Negotiator walk-away when price gap > 40% (Maex/Yubi critique).
    When buyer budget is $1,000 and seller ask is $2,500 (150% gap), agents terminate with NO_DEAL.
    """
    req = NegotiationRequestSchema(
        buyer_brief="Build MVP. Budget $1,000 USD, 14 days.",
        seller_brief="Senior architect. Rate $2,500 USD, 28 days.",
        quick_mode=True,
        force_rule_based=True,
        auto_create_contract=False,
    )

    response = await negotiator_service.run_negotiation(db_session, req)
    assert response.status == "no_deal"
    assert response.agreed_terms is None
    assert response.termination_reason is not None
    assert "exceeds 40%" in response.termination_reason.lower()


@pytest.mark.asyncio
async def test_negotiator_quick_mode_default(client):
    """
    Mentor Critique 6: Quick Mode is default (single-round term suggestion + adjustment).
    """
    payload = {
        "buyer_brief": "Build a landing page. Budget $2000 USD, 10 days.",
        "seller_brief": "Web designer. Ask $2400 USD, 14 days.",
        "force_rule_based": True,
        "auto_create_contract": False,
    }

    res = client.post("/api/v1/negotiate", json=payload)
    assert res.status_code == 200
    data = res.json()
    # Default is Quick Mode: exactly 1 round
    assert data["total_rounds"] == 1
    assert data["status"] == "agreed"
    assert data["agreed_terms"]["final_price"] == 2200.0


def test_ai_provider_swapping():
    """
    Mentor Critique 8: AI Provider swappable (Ollama default, OpenAI when OPENAI_API_KEY set).
    """
    orig_env = os.environ.get("OPENAI_API_KEY")

    try:
        # Without OPENAI_API_KEY -> OllamaProvider
        if "OPENAI_API_KEY" in os.environ:
            del os.environ["OPENAI_API_KEY"]
        provider = get_ai_provider()
        assert isinstance(provider, OllamaProvider)
        assert provider.provider_name.startswith("ollama")

        # With OPENAI_API_KEY set -> OpenAIProvider
        os.environ["OPENAI_API_KEY"] = "sk-test-mock-key-for-phase5"
        provider_openai = get_ai_provider()
        assert isinstance(provider_openai, OpenAIProvider)
        assert provider_openai.provider_name.startswith("openai")

    finally:
        if orig_env is not None:
            os.environ["OPENAI_API_KEY"] = orig_env
        elif "OPENAI_API_KEY" in os.environ:
            del os.environ["OPENAI_API_KEY"]
