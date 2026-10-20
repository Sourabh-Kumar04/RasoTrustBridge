import hmac
import hashlib
import uuid
from datetime import datetime, timezone, timedelta
import pytest
from app.config import get_settings
from app.models.user import User
from app.models.contract import Contract
from app.models.milestone import Milestone
from app.models.ledger import LedgerEntry
from app.models.idempotency import IdempotencyRecord
from app.models.payment import DisputeRecord
from app.models.enums import ContractStatus, MilestoneStatus, FXStrategy, DisputeStatus, LedgerEntryType, ActorRole
from app.services.auth_service import hash_password, create_access_token
from app.services.contract_service import contract_service
from app.services.paypal_client import paypal_client
from app.services.reputation_service import reputation_service
from app.schemas.contract import ContractCreateSchema, MilestoneCreateSchema, AcceptanceCriterionSchema

settings = get_settings()


def make_user(db_session, role="client", prefix="u"):
    uid = f"usr_{prefix}_{uuid.uuid4().hex[:8]}"
    email = f"{prefix}_{uuid.uuid4().hex[:6]}@trustbridge.network"
    user = User(
        id=uid,
        email=email,
        hashed_password=hash_password("DevSecretPassword123!"),
        role=role,
        display_name=f"User {prefix}",
        trust_score=50.0,
    )
    db_session.add(user)
    db_session.commit()
    token = create_access_token({
        "sub": user.id,
        "email": user.email,
        "role": user.role,
        "display_name": user.display_name,
    })
    return user, token


# --------------------------------------------------------------------------
# 1. SEC-01: Forged PayPal webhook rejected (401)
# --------------------------------------------------------------------------
def test_forged_paypal_webhook_rejected_401(client):
    # Forged signature must be rejected with 401
    payload = {
        "id": f"WH-EVENT-{uuid.uuid4().hex[:8]}",
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "resource": {"id": "CAP-FORGED-001"},
    }
    forged_headers = {
        "paypal-transmission-id": f"trans-{uuid.uuid4().hex[:8]}",
        "paypal-transmission-sig": "sha256=invalid_forged_hex_signature_99999",
        "paypal-transmission-time": datetime.now(timezone.utc).isoformat(),
        "Content-Type": "application/json",
    }
    res = client.post("/api/v1/paypal/webhook", json=payload, headers=forged_headers)
    assert res.status_code == 401
    assert "Invalid or forged PayPal webhook signature" in res.json()["detail"]

    # Valid signature must be accepted with 200
    secret = settings.PAYPAL_WEBHOOK_SECRET or "trustbridge_paypal_webhook_secret_dev"
    webhook_id = settings.PAYPAL_WEBHOOK_ID or "WH-TRUSTBRIDGE-DEV-001"
    valid_sig = hmac.new(secret.encode("utf-8"), webhook_id.encode("utf-8"), hashlib.sha256).hexdigest()

    valid_headers = {
        "paypal-transmission-id": f"trans-{uuid.uuid4().hex[:8]}",
        "paypal-transmission-sig": valid_sig,
        "paypal-transmission-time": datetime.now(timezone.utc).isoformat(),
        "Content-Type": "application/json",
    }
    res_valid = client.post("/api/v1/paypal/webhook", json=payload, headers=valid_headers)
    assert res_valid.status_code == 200
    assert res_valid.json()["status"] in ("received", "processed")


# --------------------------------------------------------------------------
# 2. SEC-02: Unauthenticated order creation and capture rejected (401)
# --------------------------------------------------------------------------
def test_unauthenticated_order_creation_rejected_401(unauth_client):
    res_create = unauth_client.post(
        "/api/v1/paypal/order/create",
        json={"contract_id": "ct_nonexistent", "amount": 100.0},
    )
    assert res_create.status_code == 401

    res_capture = unauth_client.post(
        "/api/v1/paypal/order/capture",
        json={"order_id": "ORDER-UNAUTH-TEST"},
    )
    assert res_capture.status_code == 401


# --------------------------------------------------------------------------
# 3. SEC-03: Dispute initiator spoofing blocked
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_dispute_initiator_spoofing_blocked(unauth_client, db_session):
    client_user, c_token = make_user(db_session, role="client", prefix="client_sec3")
    freelancer_user, f_token = make_user(db_session, role="freelancer", prefix="free_sec3")

    contract_schema = ContractCreateSchema(
        title="Anti-Spoofing Dispute Contract",
        brief="Testing SEC-03 initiator verification",
        client_name=client_user.display_name,
        client_email=client_user.email,
        developer_name=freelancer_user.display_name,
        developer_email=freelancer_user.email,
        client_id=client_user.id,
        freelancer_id=freelancer_user.id,
        base_currency="USD",
        payout_currency="USD",
        total_amount=500.0,
        milestones=[
            MilestoneCreateSchema(
                title="Milestone 1",
                description="Delivery",
                amount=500.0,
                order_index=1,
                acceptance_criteria=[AcceptanceCriterionSchema(description="Working API", verification_method="manual")],
            )
        ],
    )
    contract = await contract_service.create_contract(db_session, contract_schema)
    await contract_service.fund_contract(db_session, contract.id, paypal_order_id="ORD-SEC03", paypal_capture_id="CAP-SEC03")
    milestone_id = contract.milestones[0].id

    # Freelancer attempts to spoof initiator as CLIENT -> 403 Forbidden
    spoof_res = unauth_client.post(
        f"/api/v1/milestones/{milestone_id}/dispute",
        json={
            "initiator_role": "CLIENT",
            "initiator_email": client_user.email,
            "reason": "Hostile takeover attempt",
            "description": "Freelancer trying to pose as client",
        },
        headers={"Authorization": f"Bearer {f_token}"},
    )
    assert spoof_res.status_code == 403

    # Legitimate dispute filed by freelancer is correctly recorded as DEVELOPER
    legit_res = unauth_client.post(
        f"/api/v1/milestones/{milestone_id}/dispute",
        json={
            "initiator_role": "DEVELOPER",
            "initiator_email": freelancer_user.email,
            "reason": "Legitimate freelancer grievance",
            "description": "Client requested unpaid scope expansion",
        },
        headers={"Authorization": f"Bearer {f_token}"},
    )
    assert legit_res.status_code == 200
    disp_data = legit_res.json()
    assert disp_data["initiator_role"] == "DEVELOPER"
    assert disp_data["initiator_email"] == freelancer_user.email


# --------------------------------------------------------------------------
# 4. SEC-04: Contract creation with mismatched client_id -> 403
# --------------------------------------------------------------------------
def test_contract_creation_with_mismatched_client_id_403(unauth_client, db_session):
    alice, alice_token = make_user(db_session, role="client", prefix="alice_sec4")
    bob, _ = make_user(db_session, role="client", prefix="bob_sec4")

    # Alice attempts to create contract setting client_id to Bob's ID -> 403 Forbidden
    mismatched_payload = {
        "title": "Unauthorized Impersonation Contract",
        "brief": "Alice tries to bind Bob without consent",
        "client_name": bob.display_name,
        "client_email": bob.email,
        "client_id": bob.id,
        "developer_name": "Dave Developer",
        "developer_email": "dave@example.com",
        "base_currency": "USD",
        "payout_currency": "USD",
        "total_amount": 1000.0,
        "milestones": [
            {
                "title": "M1",
                "description": "Test",
                "amount": 1000.0,
                "order_index": 1,
                "acceptance_criteria": [{"description": "done", "verification_method": "manual"}],
            }
        ],
    }
    res = unauth_client.post(
        "/api/v1/contracts",
        json=mismatched_payload,
        headers={"Authorization": f"Bearer {alice_token}"},
    )
    assert res.status_code == 403
    assert "client_id must match authenticated user ID" in res.json()["detail"]


# --------------------------------------------------------------------------
# 5. SEC-05: Cross-user dispute dossier access -> 403 (IDOR defense)
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cross_user_dispute_dossier_access_403(unauth_client, db_session):
    alice, a_token = make_user(db_session, role="client", prefix="alice_sec5")
    bob, b_token = make_user(db_session, role="freelancer", prefix="bob_sec5")
    mallory, m_token = make_user(db_session, role="client", prefix="mallory_sec5")

    contract_schema = ContractCreateSchema(
        title="Confidential Escrow Contract",
        brief="Contains proprietary trade secret evidence",
        client_name=alice.display_name,
        client_email=alice.email,
        developer_name=bob.display_name,
        developer_email=bob.email,
        client_id=alice.id,
        freelancer_id=bob.id,
        base_currency="USD",
        payout_currency="USD",
        total_amount=1200.0,
        milestones=[
            MilestoneCreateSchema(
                title="Proprietary Code",
                description="Delivery",
                amount=1200.0,
                order_index=1,
                acceptance_criteria=[AcceptanceCriterionSchema(description="Secret algorithm", verification_method="manual")],
            )
        ],
    )
    contract = await contract_service.create_contract(db_session, contract_schema)
    await contract_service.fund_contract(db_session, contract.id, paypal_order_id="ORD-SEC05", paypal_capture_id="CAP-SEC05")
    milestone_id = contract.milestones[0].id

    # Alice opens dispute
    disp_res = unauth_client.post(
        f"/api/v1/milestones/{milestone_id}/dispute",
        json={
            "initiator_role": "CLIENT",
            "initiator_email": alice.email,
            "reason": "Confidential breach",
            "description": "Sensitive dispute description",
        },
        headers={"Authorization": f"Bearer {a_token}"},
    ).json()
    dispute_id = disp_res["id"]

    # Mallory (external party) attempts to query dispute record -> 403 Forbidden!
    idor_res = unauth_client.get(
        f"/api/v1/disputes/{dispute_id}",
        headers={"Authorization": f"Bearer {m_token}"},
    )
    assert idor_res.status_code == 403
    assert "Forbidden" in idor_res.json()["detail"]

    # Alice and Bob (contract parties) have legitimate access -> 200 OK
    alice_res = unauth_client.get(
        f"/api/v1/disputes/{dispute_id}",
        headers={"Authorization": f"Bearer {a_token}"},
    )
    assert alice_res.status_code == 200

    bob_res = unauth_client.get(
        f"/api/v1/disputes/{dispute_id}",
        headers={"Authorization": f"Bearer {b_token}"},
    )
    assert bob_res.status_code == 200


# --------------------------------------------------------------------------
# 6. SEC-06: Idempotency survives process restart (simulated via DB record)
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_idempotency_survives_process_restart_via_db(db_session):
    idem_key = f"payout_test_survive_{uuid.uuid4().hex[:10]}"

    # First execution generates new payout batch
    payout1 = await paypal_client.create_payout(
        recipient_email="freelancer@test.org",
        amount=250.0,
        currency="USD",
        idempotency_key=idem_key,
        db=db_session,
    )
    assert payout1["payout_batch_id"] is not None
    assert payout1.get("idempotency_replayed") is not True

    # Verify record was persisted in database
    db_rec = db_session.query(IdempotencyRecord).filter(IdempotencyRecord.key == idem_key).first()
    assert db_rec is not None
    assert db_rec.response_hash is not None

    # Simulate process crash / worker reboot by wiping in-memory cache
    paypal_client._idempotency_store.clear()
    assert idem_key not in paypal_client._idempotency_store

    # Replay call with same idempotency key must recover response from DB
    payout2 = await paypal_client.create_payout(
        recipient_email="freelancer@test.org",
        amount=250.0,
        currency="USD",
        idempotency_key=idem_key,
        db=db_session,
    )
    assert payout2["payout_batch_id"] == payout1["payout_batch_id"]
    assert payout2.get("idempotency_replayed") is True


# --------------------------------------------------------------------------
# 7. SEC-07: Buyer timeout triggers payout and writes ledger entries
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_buyer_timeout_triggers_payout_and_ledger(client, db_session):
    contract_schema = ContractCreateSchema(
        title="Buyer Timeout Payout Contract",
        brief="Testing automated payout execution on expiry",
        client_name="Silent Client",
        client_email="silent@enterprise.org",
        developer_name="Active Coder",
        developer_email="coder@freelance.org",
        base_currency="USD",
        payout_currency="USD",
        total_amount=800.0,
        buyer_timeout_days=3,
        milestones=[
            MilestoneCreateSchema(
                title="Delivery 1",
                description="Completed feature",
                amount=800.0,
                order_index=1,
                acceptance_criteria=[AcceptanceCriterionSchema(description="Tested", verification_method="automated_test")],
            )
        ],
    )
    contract = await contract_service.create_contract(db_session, contract_schema)
    await contract_service.fund_contract(db_session, contract.id, paypal_order_id="ORD-TO-01", paypal_capture_id="CAP-TO-01")

    milestone = contract.milestones[0]
    milestone.status = MilestoneStatus.SUBMITTED
    milestone.expires_at = datetime.now(timezone.utc) - timedelta(days=2)
    db_session.commit()

    # Trigger maintenance expiry scan
    res = client.post("/api/v1/milestones/check-expiry")
    assert res.status_code == 200

    db_session.expire_all()
    updated_ms = db_session.query(Milestone).filter(Milestone.id == milestone.id).first()
    assert updated_ms.status == MilestoneStatus.VERIFIED
    assert updated_ms.verified_by == "BUYER_TIMEOUT_AUTO_RELEASE"

    # Crucial SEC-07 verification: funds must NOT be trapped; payout batch ID must be populated!
    assert updated_ms.paypal_payout_batch_id is not None
    assert updated_ms.paypal_payout_batch_id.startswith("PAYOUT-SANDBOX-BATCH-")

    # Ledger must contain both PAYOUT_INITIATED and PAYOUT_COMPLETED records
    payout_entries = db_session.query(LedgerEntry).filter(
        LedgerEntry.contract_id == contract.id,
        LedgerEntry.entry_type.in_([LedgerEntryType.PAYOUT_INITIATED, LedgerEntryType.PAYOUT_COMPLETED]),
    ).all()
    assert len(payout_entries) == 2


# --------------------------------------------------------------------------
# 8. SEC-08: Dispute opened does not penalize reputation until lost
# --------------------------------------------------------------------------
def test_dispute_opened_does_not_penalize_reputation_until_lost(db_session):
    client_user, _ = make_user(db_session, role="client", prefix="c_rep")
    dev_user, _ = make_user(db_session, role="freelancer", prefix="f_rep")

    initial_client_score = client_user.trust_score
    initial_dev_score = dev_user.trust_score
    assert initial_client_score == 50.0
    assert initial_dev_score == 50.0

    # 1. Opening a dispute must NOT deduct reputation points (no griefing attack)
    reputation_service.record_dispute_opened(db_session, client=client_user, freelancer=dev_user)
    assert client_user.trust_score == initial_client_score
    assert dev_user.trust_score == initial_dev_score
    assert client_user.disputed_deals == 1
    assert dev_user.disputed_deals == 1

    # 2. Resolving in developer's favor:
    # Developer wins: +5.0 points
    # Client loses: strictly penalized with -10.0 points
    reputation_service.record_dispute_resolution(
        db_session,
        client=client_user,
        freelancer=dev_user,
        resolution_status=DisputeStatus.RESOLVED_DEVELOPER_PAYOUT,
    )
    assert dev_user.trust_score == 55.0  # 50.0 + 5.0
    assert client_user.trust_score == 40.0  # 50.0 - 10.0 (penalty only applied when lost!)
