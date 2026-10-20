import uuid
from datetime import datetime, timezone, timedelta
import pytest
from fastapi.testclient import TestClient

from app.models.user import User
from app.models.contract import Contract
from app.models.milestone import Milestone
from app.models.payment import Stake, ClientDeposit, DisputeRecord
from app.models.ledger import LedgerEntry
from app.models.enums import ContractStatus, MilestoneStatus, ActorRole, DisputeStatus, LedgerEntryType
from app.schemas.contract import ContractCreateSchema, MilestoneCreateSchema
from app.schemas.milestone import MilestoneSubmitRequest, MilestoneReleaseRequest, MilestoneDisputeRequest
from app.services.contract_service import contract_service
from app.services.stake_service import stake_service
from app.services.dispute_service import dispute_service
from app.services.reputation_service import reputation_service
from app.services.auth_service import hash_password, create_access_token


@pytest.mark.asyncio
async def test_new_account_zero_deals_on_500_contract_requires_10_percent_stake(db_session):
    """
    Harmer's Model 1:
    New accounts (< 7 days OR 0 completed deals) require 10% stake regardless of contract value.
    On a $500 contract, stake is 10% ($50).
    Verifies applied rate is computed from reputation and stored on Stake.
    """
    new_freelancer = User(
        id=f"usr_new_{uuid.uuid4().hex[:6]}",
        email=f"new_freelancer_{uuid.uuid4().hex[:6]}@example.com",
        hashed_password=hash_password("Pass123!"),
        role="freelancer",
        display_name="New Freelancer",
        trust_score=30.0,
        completed_deals=0,
        disputes_lost=0,
        created_at=datetime.now(timezone.utc),  # Brand new (< 7 days)
    )
    db_session.add(new_freelancer)
    db_session.commit()

    schema = ContractCreateSchema(
        title="Small Contract",
        brief="Test 10% rate for new freelancer on $500 deal",
        client_name="Alice Client",
        client_email=f"client_{uuid.uuid4().hex[:6]}@example.com",
        developer_name="New Freelancer",
        developer_email=new_freelancer.email,
        freelancer_id=new_freelancer.id,
        base_currency="USD",
        payout_currency="EUR",
        total_amount=500.0,
        milestones=[MilestoneCreateSchema(title="M1", description="Part 1", amount=500.0, order_index=1)],
    )
    contract = await contract_service.create_contract(db_session, schema)
    await contract_service.fund_contract(db_session, contract.id, "ORD-500", "CAP-500")

    # Rate computed from reputation must be 10.0%
    req_pct = reputation_service.calculate_graduated_stake_percentage(new_freelancer)
    assert req_pct == 10.0

    # Post stake
    stake = stake_service.post_stake(db=db_session, contract=contract, freelancer=new_freelancer)
    assert stake.status == "HELD"
    assert stake.stake_percentage == 10.0
    assert stake.amount == 50.0  # 10% of $500


@pytest.mark.asyncio
async def test_proven_freelancer_25_deals_requires_2_percent_stake(db_session):
    """
    Harmer's Model 1:
    Proven freelancer (20+ completed deals, dispute-loss rate < 5%) requires 2% stake.
    On a $1,000 contract, stake is 2% ($20).
    """
    proven_dev = User(
        id=f"usr_proven_{uuid.uuid4().hex[:6]}",
        email=f"proven_dev_{uuid.uuid4().hex[:6]}@example.com",
        hashed_password=hash_password("Pass123!"),
        role="freelancer",
        display_name="Veteran Freelancer",
        trust_score=95.0,
        completed_deals=25,
        disputes_lost=0,
        created_at=datetime.now(timezone.utc) - timedelta(days=60),  # Mature account
    )
    db_session.add(proven_dev)
    db_session.commit()

    schema = ContractCreateSchema(
        title="Proven Freelancer Contract",
        brief="Test 2% rate for proven freelancer",
        client_name="Bob Client",
        client_email=f"client_{uuid.uuid4().hex[:6]}@example.com",
        developer_name="Veteran Freelancer",
        developer_email=proven_dev.email,
        freelancer_id=proven_dev.id,
        base_currency="USD",
        payout_currency="EUR",
        total_amount=1000.0,
        milestones=[MilestoneCreateSchema(title="M1", description="Part 1", amount=1000.0, order_index=1)],
    )
    contract = await contract_service.create_contract(db_session, schema)
    await contract_service.fund_contract(db_session, contract.id, "ORD-PROVEN", "CAP-PROVEN")

    req_pct = reputation_service.calculate_graduated_stake_percentage(proven_dev)
    assert req_pct == 2.0

    stake = stake_service.post_stake(db=db_session, contract=contract, freelancer=proven_dev)
    assert stake.status == "HELD"
    assert stake.stake_percentage == 2.0
    assert stake.amount == 20.0  # 2% of $1,000


@pytest.mark.asyncio
async def test_graduated_reputation_tiers(db_session):
    """
    Verifies all 4 Harmer graduated tiers:
    - New (< 7 days OR 0 completed deals): 10%
    - Emerging (1-4 completed deals, no lost disputes): 7%
    - Established (5-19 completed deals, dispute-loss rate < 10%): 4%
    - Proven (20+ completed deals, dispute-loss rate < 5%): 2%
    - High dispute-loss rate fallback: 10%
    """
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=30)

    # 1. New account by age (< 7 days), even with prior completed deals
    u_new_age = User(email="new_age@t.co", hashed_password="x", display_name="New", completed_deals=5, created_at=now)
    assert reputation_service.calculate_graduated_stake_percentage(u_new_age) == 10.0

    # 2. New account by 0 completed deals (mature age)
    u_zero_deals = User(email="zero@t.co", hashed_password="x", display_name="Zero", completed_deals=0, created_at=old)
    assert reputation_service.calculate_graduated_stake_percentage(u_zero_deals) == 10.0

    # 3. Emerging: 1-4 deals, 0 lost disputes
    u_emerging = User(email="emerging@t.co", hashed_password="x", display_name="Emerging", completed_deals=3, disputes_lost=0, created_at=old)
    assert reputation_service.calculate_graduated_stake_percentage(u_emerging) == 7.0

    # 4. Established: 5-19 deals, loss rate < 10%
    u_est = User(email="est@t.co", hashed_password="x", display_name="Est", completed_deals=10, disputes_lost=0, created_at=old)
    assert reputation_service.calculate_graduated_stake_percentage(u_est) == 4.0

    # 5. Proven: 20+ deals, loss rate < 5%
    u_prov = User(email="prov@t.co", hashed_password="x", display_name="Prov", completed_deals=25, disputes_lost=0, created_at=old)
    assert reputation_service.calculate_graduated_stake_percentage(u_prov) == 2.0

    # 6. Poor reputation fallback: 15 deals, but 5 losses (5/20 = 25% >= 10%)
    u_bad = User(email="bad@t.co", hashed_password="x", display_name="Bad", completed_deals=15, disputes_lost=5, created_at=old)
    assert reputation_service.calculate_graduated_stake_percentage(u_bad) == 10.0


@pytest.mark.asyncio
async def test_symmetric_client_deposit_posted_at_acceptance(db_session):
    """
    Harmer's Model 2:
    Client posts a matching good-faith deposit: 50% of the freelancer's stake amount.
    On a $1,000 contract with a 10% freelancer stake = $100, client deposits $50.
    Ledger entry: CLIENT_DEPOSIT_POSTED.
    """
    freelancer = User(
        id=f"usr_fl_{uuid.uuid4().hex[:6]}",
        email=f"fl_{uuid.uuid4().hex[:6]}@example.com",
        hashed_password=hash_password("Pass123!"),
        role="freelancer",
        display_name="Dev",
        completed_deals=0,
    )
    client_user = User(
        id=f"usr_cl_{uuid.uuid4().hex[:6]}",
        email=f"cl_{uuid.uuid4().hex[:6]}@example.com",
        hashed_password=hash_password("Pass123!"),
        role="client",
        display_name="Client",
    )
    db_session.add_all([freelancer, client_user])
    db_session.commit()

    schema = ContractCreateSchema(
        title="Symmetric Deposit Test Contract",
        brief="Test client deposit posting",
        client_name="Client",
        client_email=client_user.email,
        developer_name="Dev",
        developer_email=freelancer.email,
        freelancer_id=freelancer.id,
        base_currency="USD",
        payout_currency="EUR",
        total_amount=1000.0,
        milestones=[MilestoneCreateSchema(title="M1", description="D1", amount=1000.0, order_index=1)],
    )
    contract = await contract_service.create_contract(db_session, schema)
    contract.client_id = client_user.id
    db_session.commit()

    # Freelancer posts stake (10% of $1000 = $100)
    stake = stake_service.post_stake(db=db_session, contract=contract, freelancer=freelancer)
    assert stake.amount == 100.0

    # Symmetric client deposit must be 50% of freelancer stake = $50
    db_session.refresh(contract)
    assert contract.client_deposit is not None
    assert contract.client_deposit.amount == 50.0
    assert contract.client_deposit.status == "HELD"
    assert contract.client_deposit.party == "client"

    # Verify ledger entry CLIENT_DEPOSIT_POSTED
    deposit_entry = db_session.query(LedgerEntry).filter(
        LedgerEntry.contract_id == contract.id,
        LedgerEntry.entry_type == LedgerEntryType.CLIENT_DEPOSIT_POSTED,
    ).first()
    assert deposit_entry is not None
    assert deposit_entry.amount == 50.0


@pytest.mark.asyncio
async def test_frivolous_dispute_client_loses_deposit_forfeited_to_freelancer(db_session):
    """
    Harmer's Model 2:
    If the client opens a dispute and LOSES (arbiter rules for freelancer),
    the client's deposit is FORFEITED to the freelancer as compensation for the frivolous dispute.
    Freelancer's stake is returned in full.
    Ledger entries: CLIENT_DEPOSIT_FORFEITED, STAKE_RETURNED.
    """
    freelancer = User(
        id=f"usr_fl_{uuid.uuid4().hex[:6]}",
        email=f"fl_{uuid.uuid4().hex[:6]}@example.com",
        hashed_password=hash_password("Pass123!"),
        role="freelancer",
        display_name="Dev",
        completed_deals=0,
    )
    client_user = User(
        id=f"usr_cl_{uuid.uuid4().hex[:6]}",
        email=f"cl_{uuid.uuid4().hex[:6]}@example.com",
        hashed_password=hash_password("Pass123!"),
        role="client",
        display_name="Client",
    )
    db_session.add_all([freelancer, client_user])
    db_session.commit()

    schema = ContractCreateSchema(
        title="Frivolous Dispute Contract",
        brief="Test frivolous dispute forfeit flow",
        client_name="Client",
        client_email=client_user.email,
        developer_name="Dev",
        developer_email=freelancer.email,
        freelancer_id=freelancer.id,
        base_currency="USD",
        payout_currency="EUR",
        total_amount=1000.0,
        milestones=[MilestoneCreateSchema(title="M1", description="D1", amount=1000.0, order_index=1)],
    )
    contract = await contract_service.create_contract(db_session, schema)
    contract.client_id = client_user.id
    await contract_service.fund_contract(db_session, contract.id, "ORD-FRIV", "CAP-FRIV")

    # Freelancer posts stake ($100), client deposit is posted ($50)
    stake = stake_service.post_stake(db=db_session, contract=contract, freelancer=freelancer)
    assert stake.amount == 100.0
    assert contract.client_deposit.amount == 50.0

    # Client opens frivolous dispute
    m1 = contract.milestones[0]
    dispute = dispute_service.open_dispute(
        db=db_session,
        contract=contract,
        milestone=m1,
        initiator_role=ActorRole.CLIENT,
        initiator_email=client_user.email,
        reason="Client claims unfair work without basis",
        description="Frivolous claim",
    )

    # Arbiter resolves dispute in favor of freelancer (client loses)
    dispute_service.resolve_dispute(
        db=db_session,
        dispute=dispute,
        resolution_status=DisputeStatus.RESOLVED_DEVELOPER_PAYOUT,
        resolution_notes="Work meets all acceptance criteria. Frivolous dispute by buyer.",
        decided_by="arbiter",
    )

    db_session.refresh(contract)
    db_session.refresh(stake)

    # 1. Freelancer stake returned in full
    assert stake.status == "RETURNED"
    assert stake.returned_amount == 100.0

    # 2. Client deposit forfeited to freelancer
    assert contract.client_deposit.status == "FORFEITED"
    assert contract.client_deposit.forfeited_amount == 50.0

    # Verify ledger entries
    forfeit_entry = db_session.query(LedgerEntry).filter(
        LedgerEntry.contract_id == contract.id,
        LedgerEntry.entry_type == LedgerEntryType.CLIENT_DEPOSIT_FORFEITED,
    ).first()
    assert forfeit_entry is not None
    assert forfeit_entry.amount == 50.0


@pytest.mark.asyncio
async def test_lost_dispute_exactly_50_percent_slashed_remainder_returned_never_100(db_session):
    """
    Harmer's Model 3:
    When freelancer loses dispute:
    - Exactly 50% of the staked amount is slashed by default.
    - Slashed funds go to the wronged client, not the platform.
    - The unslashed remainder is ALWAYS returned to the freelancer.
    - Full slash (100%+) is NEVER permitted: raises ValueError.
    - Client deposit is returned in full when client wins dispute.
    """
    freelancer = User(
        id=f"usr_fl_{uuid.uuid4().hex[:6]}",
        email=f"fl_{uuid.uuid4().hex[:6]}@example.com",
        hashed_password=hash_password("Pass123!"),
        role="freelancer",
        display_name="Dev",
        completed_deals=0,
    )
    client_user = User(
        id=f"usr_cl_{uuid.uuid4().hex[:6]}",
        email=f"cl_{uuid.uuid4().hex[:6]}@example.com",
        hashed_password=hash_password("Pass123!"),
        role="client",
        display_name="Client",
    )
    db_session.add_all([freelancer, client_user])
    db_session.commit()

    schema = ContractCreateSchema(
        title="Partial Slash Test Contract",
        brief="Test 50% partial slash on lost dispute",
        client_name="Client",
        client_email=client_user.email,
        developer_name="Dev",
        developer_email=freelancer.email,
        freelancer_id=freelancer.id,
        base_currency="USD",
        payout_currency="EUR",
        total_amount=1000.0,
        milestones=[MilestoneCreateSchema(title="M1", description="D1", amount=1000.0, order_index=1)],
    )
    contract = await contract_service.create_contract(db_session, schema)
    contract.client_id = client_user.id
    await contract_service.fund_contract(db_session, contract.id, "ORD-SLASH", "CAP-SLASH")

    # Freelancer posts 10% stake ($100), client deposit is $50
    stake = stake_service.post_stake(db=db_session, contract=contract, freelancer=freelancer)
    assert stake.amount == 100.0
    assert contract.client_deposit.amount == 50.0

    # 1. Enforce code prevents full slash (100%+)
    with pytest.raises(ValueError, match="Full slash is never permitted"):
        stake_service.slash_stake(db=db_session, contract_id=contract.id, slash_percentage=100.0)

    with pytest.raises(ValueError, match="Full slash is never permitted"):
        stake_service.slash_stake(db=db_session, contract_id=contract.id, slash_percentage=105.0)

    # 2. Client opens dispute and freelancer loses
    m1 = contract.milestones[0]
    dispute = dispute_service.open_dispute(
        db=db_session,
        contract=contract,
        milestone=m1,
        initiator_role=ActorRole.CLIENT,
        initiator_email=client_user.email,
        reason="Freelancer did not submit code",
        description="Missing delivery",
    )

    dispute_service.resolve_dispute(
        db=db_session,
        dispute=dispute,
        resolution_status=DisputeStatus.RESOLVED_CLIENT_REFUND,
        resolution_notes="Dispute resolved in client favor. Freelancer failed to deliver.",
        decided_by="arbiter",
    )

    db_session.refresh(stake)
    db_session.refresh(contract)

    # Exactly 50% slashed ($50)
    assert stake.status == "SLASHED"
    assert stake.slashed_amount == 50.0
    # Unslashed remainder returned to freelancer ($50)
    assert stake.returned_amount == 50.0

    # Client deposit returned in full ($50)
    assert contract.client_deposit.status == "RETURNED"
    assert contract.client_deposit.returned_amount == 50.0

    # Verify ledger entries
    slash_entry = db_session.query(LedgerEntry).filter(
        LedgerEntry.contract_id == contract.id,
        LedgerEntry.entry_type == LedgerEntryType.STAKE_SLASHED,
    ).first()
    assert slash_entry is not None
    assert slash_entry.amount == 50.0

    # Check unslashed remainder return entry
    return_entry = db_session.query(LedgerEntry).filter(
        LedgerEntry.contract_id == contract.id,
        LedgerEntry.entry_type == LedgerEntryType.STAKE_RETURNED,
    ).first()
    assert return_entry is not None
    assert return_entry.amount == 50.0


def test_arbiter_endpoint_permissions_and_resolution_flows(unauth_client, db_session):
    """
    Harmer's Model 4:
    Arbiter endpoint POST /api/v1/disputes/{id}/resolve:
    - Non-admin (freelancer, client, unauthenticated) gets 403 (or 401).
    - Admin resolution triggers correct slash / forfeit flows.
    """
    # 1. Create client, freelancer, and admin users
    c_res = unauth_client.post(
        "/api/v1/auth/register",
        json={"email": f"client_{uuid.uuid4().hex[:6]}@co.org", "password": "Pass123!", "role": "client", "display_name": "Client"},
    )
    c_token = c_res.json()["access_token"]
    c_email = c_res.json()["user"]["email"]

    d_res = unauth_client.post(
        "/api/v1/auth/register",
        json={"email": f"dev_{uuid.uuid4().hex[:6]}@co.org", "password": "Pass123!", "role": "freelancer", "display_name": "Dev"},
    )
    d_token = d_res.json()["access_token"]
    d_email = d_res.json()["user"]["email"]

    admin = User(
        id=f"usr_adm_{uuid.uuid4().hex[:6]}",
        email=f"admin_{uuid.uuid4().hex[:6]}@trustbridge.network",
        hashed_password=hash_password("AdminPass123!"),
        role="admin",
        display_name="Neutral Arbiter",
    )
    db_session.add(admin)
    db_session.commit()
    admin_token = create_access_token({"sub": admin.id, "email": admin.email, "role": "admin"})

    # 2. Client creates and funds contract
    contract_res = unauth_client.post(
        "/api/v1/contracts",
        json={
            "title": "Arbitration Test Contract",
            "brief": "Testing arbiter authority",
            "client_name": "Client",
            "client_email": c_email,
            "developer_name": "Dev",
            "developer_email": d_email,
            "base_currency": "USD",
            "payout_currency": "EUR",
            "total_amount": 1000.0,
            "milestones": [{"title": "M1", "description": "Delivery", "amount": 1000.0, "order_index": 1}],
        },
        headers={"Authorization": f"Bearer {c_token}"},
    ).json()
    contract_id = contract_res["id"]
    milestone_id = contract_res["milestones"][0]["id"]

    # Fund contract
    unauth_client.post(
        f"/api/v1/contracts/{contract_id}/fund",
        json={"paypal_order_id": "ORD-ARB-1", "paypal_capture_id": "CAP-ARB-1"},
        headers={"Authorization": f"Bearer {c_token}"},
    )

    # Freelancer posts stake ($100), symmetric client deposit ($50) automatically held
    unauth_client.post(
        f"/api/v1/contracts/{contract_id}/stake",
        json={"stake_percentage": 10.0, "stake_amount": 100.0},
        headers={"Authorization": f"Bearer {d_token}"},
    )

    # Client opens dispute
    disp_res = unauth_client.post(
        f"/api/v1/milestones/{milestone_id}/dispute",
        json={
            "initiator_role": "CLIENT",
            "initiator_email": c_email,
            "reason": "Delivery issue",
            "description": "Unsatisfied with milestone output",
        },
        headers={"Authorization": f"Bearer {c_token}"},
    ).json()
    dispute_id = disp_res["id"]

    # 3. Non-admin calls arbiter endpoint -> 403 Forbidden!
    client_resolve_attempt = unauth_client.post(
        f"/api/v1/disputes/{dispute_id}/resolve",
        json={"outcome": "client", "arbiter_note": "Client trying to arbitrate themselves"},
        headers={"Authorization": f"Bearer {c_token}"},
    )
    assert client_resolve_attempt.status_code == 403

    freelancer_resolve_attempt = unauth_client.post(
        f"/api/v1/disputes/{dispute_id}/resolve",
        json={"outcome": "freelancer", "arbiter_note": "Freelancer trying to arbitrate themselves"},
        headers={"Authorization": f"Bearer {d_token}"},
    )
    assert freelancer_resolve_attempt.status_code == 403

    # 4. Admin resolves dispute with outcome 'freelancer' (rules for freelancer)
    admin_resolve_res = unauth_client.post(
        f"/api/v1/disputes/{dispute_id}/resolve",
        json={"outcome": "freelancer", "arbiter_note": "Neutral arbiter review: Freelancer delivered per specs."},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert admin_resolve_res.status_code == 200
    res_data = admin_resolve_res.json()
    assert res_data["status"] == "RESOLVED_DEVELOPER_PAYOUT"
    assert res_data["decided_by"] == "arbiter"

    # Verify contract stake & client deposit states via API
    c_check = unauth_client.get(
        f"/api/v1/contracts/{contract_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    ).json()
    assert c_check["stake"]["status"] == "RETURNED"
    assert c_check["stake"]["returned_amount"] == 100.0
    assert c_check["client_deposit"]["status"] == "FORFEITED"
    assert c_check["client_deposit"]["forfeited_amount"] == 50.0
