import pytest
from fastapi import HTTPException
from app.models.enums import ContractStatus, MilestoneStatus, FXStrategy, VerificationType
from app.schemas.contract import ContractCreateSchema, MilestoneCreateSchema, AcceptanceCriterionSchema
from app.services.contract_service import contract_service
from app.models.contract import Contract


@pytest.mark.asyncio
async def test_create_contract_success(db_session):
    schema = ContractCreateSchema(
        title="Fullstack Microservices Development",
        brief="Develop 3 microservices with FastAPI and PostgreSQL",
        client_name="Alice Morgan",
        client_email="alice@acme-global.com",
        client_country="US",
        developer_name="Rahul Sharma",
        developer_email="rahul@bangalore-devs.in",
        developer_country="IN",
        base_currency="USD",
        payout_currency="INR",
        total_amount=1500.0,
        fx_strategy=FXStrategy.LOCKED_AT_SIGNING,
        locked_fx_rate=84.50,
        milestones=[
            MilestoneCreateSchema(
                title="Phase 1: DB Schema and Auth",
                description="User auth and DB setup",
                amount=500.0,
                order_index=1,
                verification_type=VerificationType.CODE_DELIVERY,
                acceptance_criteria=[
                    AcceptanceCriterionSchema(
                        description="Passing unit tests for JWT auth",
                        verification_method="automated_test",
                    )
                ],
            ),
            MilestoneCreateSchema(
                title="Phase 2: Core Microservices",
                description="Business logic endpoints",
                amount=1000.0,
                order_index=2,
                verification_type=VerificationType.CODE_DELIVERY,
                acceptance_criteria=[
                    AcceptanceCriterionSchema(
                        description="GitHub PR merged into main branch",
                        verification_method="github_pr",
                    )
                ],
            ),
        ],
    )

    contract = await contract_service.create_contract(db_session, schema)

    assert contract.id.startswith("ct_")
    assert contract.status == ContractStatus.DRAFT
    assert contract.total_amount == 1500.0
    assert contract.locked_fx_rate == 84.50
    assert len(contract.milestones) == 2
    assert contract.milestones[0].amount == 500.0
    assert contract.milestones[0].payout_amount == 42250.0  # 500 * 84.50
    assert contract.milestones[0].status == MilestoneStatus.PENDING
    assert len(contract.audit_logs) == 1
    assert contract.audit_logs[0].action == "CONTRACT_CREATED"


@pytest.mark.asyncio
async def test_create_contract_amount_mismatch_fails(db_session):
    schema = ContractCreateSchema(
        title="Mismatch Test",
        brief="Testing milestone sum validation",
        client_name="Bob Client",
        client_email="bob@example.com",
        developer_name="Dave Dev",
        developer_email="dave@example.com",
        total_amount=1000.0,
        milestones=[
            MilestoneCreateSchema(
                title="M1",
                description="desc",
                amount=400.0,
            ),
            MilestoneCreateSchema(
                title="M2",
                description="desc",
                amount=400.0,  # 400 + 400 = 800 != 1000
            ),
        ],
    )

    with pytest.raises(HTTPException) as exc:
        await contract_service.create_contract(db_session, schema)

    assert exc.value.status_code == 400
    assert "does not match contract total" in exc.value.detail


@pytest.mark.asyncio
async def test_fund_contract(db_session):
    schema = ContractCreateSchema(
        title="Escrow Funding Test",
        brief="Test escrow state transitions",
        client_name="Carol",
        client_email="carol@client.com",
        developer_name="Deepak",
        developer_email="deepak@dev.com",
        total_amount=800.0,
        milestones=[
            MilestoneCreateSchema(
                title="Milestone 1",
                description="Deliverable 1",
                amount=800.0,
            )
        ],
    )

    contract = await contract_service.create_contract(db_session, schema)
    assert contract.status == ContractStatus.DRAFT

    # Fund the contract
    funded_contract = await contract_service.fund_contract(
        db_session,
        contract_id=contract.id,
        paypal_order_id="ORDER-SANDBOX-TEST-12345",
        paypal_capture_id="CAP-SANDBOX-TEST-67890",
    )

    assert funded_contract.status == ContractStatus.ACTIVE
    assert funded_contract.paypal_order_id == "ORDER-SANDBOX-TEST-12345"
    assert funded_contract.paypal_capture_id == "CAP-SANDBOX-TEST-67890"
    assert funded_contract.milestones[0].status == MilestoneStatus.FUNDED
    assert funded_contract.funded_at is not None
