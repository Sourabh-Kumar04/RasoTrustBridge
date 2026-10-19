import pytest
from app.models.enums import ContractStatus, MilestoneStatus, FXStrategy, ActorRole, DisputeStatus
from app.schemas.contract import ContractCreateSchema, MilestoneCreateSchema, AcceptanceCriterionSchema
from app.schemas.milestone import MilestoneSubmitRequest, MilestoneReleaseRequest, MilestoneDisputeRequest
from app.services.contract_service import contract_service
from app.services.dispute_service import dispute_service


@pytest.fixture
async def active_contract_with_milestones(db_session):
    schema = ContractCreateSchema(
        title="Full Lifecycle Test Contract",
        brief="Test complete milestone lifecycle",
        client_name="Test Client",
        client_email="client@example.com",
        developer_name="Test Developer",
        developer_email="dev@example.com",
        base_currency="USD",
        payout_currency="INR",
        total_amount=1000.0,
        fx_strategy=FXStrategy.LOCKED_AT_SIGNING,
        locked_fx_rate=84.00,
        milestones=[
            MilestoneCreateSchema(
                title="Milestone A",
                description="Initial deliverable",
                amount=400.0,
                order_index=1,
                acceptance_criteria=[
                    AcceptanceCriterionSchema(description="Code passes all tests", verification_method="automated_test")
                ],
            ),
            MilestoneCreateSchema(
                title="Milestone B",
                description="Final deliverable",
                amount=600.0,
                order_index=2,
                acceptance_criteria=[
                    AcceptanceCriterionSchema(description="Production deployment verified", verification_method="manual_review")
                ],
            ),
        ],
    )
    contract = await contract_service.create_contract(db_session, schema)
    return await contract_service.fund_contract(
        db_session,
        contract.id,
        paypal_order_id="ORDER-LIFECYCLE-1",
        paypal_capture_id="CAP-LIFECYCLE-1",
    )


@pytest.mark.asyncio
async def test_complete_milestone_lifecycle(db_session, active_contract_with_milestones):
    contract = active_contract_with_milestones
    m1 = contract.milestones[0]
    m2 = contract.milestones[1]

    # 1. Check funded state
    assert m1.status == MilestoneStatus.FUNDED
    assert m2.status == MilestoneStatus.FUNDED

    # 2. Submit Milestone A with GitHub PR & passing tests
    submit_req = MilestoneSubmitRequest(
        evidence_url="https://github.com/Sourabh-Kumar04/TrustBridge/pull/1",
        evidence_notes="Implemented authentication endpoints and added unit tests",
        evidence_payload={
            "commit_sha": "a1b2c3d4e5f6789012345678901234567890abcd",
            "tests_passed": 15,
            "tests_total": 15,
        },
    )
    submitted_m1 = await contract_service.submit_milestone(db_session, m1.id, submit_req)

    # Verification score should be high (>= 0.80) -> transitions to VERIFIED
    assert submitted_m1.status == MilestoneStatus.VERIFIED
    assert submitted_m1.verification_score >= 0.85
    assert submitted_m1.verified_at is not None
    assert submitted_m1.verification_log["is_approved"] is True

    # 3. Release Milestone A -> Triggers PayPal Payout release
    release_req = MilestoneReleaseRequest(release_note="Milestone 1 approved and released")
    released_m1 = await contract_service.release_milestone(db_session, m1.id, release_req)

    assert released_m1.status == MilestoneStatus.RELEASED
    assert released_m1.released_at is not None
    assert released_m1.paypal_payout_batch_id is not None
    # Contract should still be ACTIVE because M2 is pending
    db_session.refresh(contract)
    assert contract.status == ContractStatus.ACTIVE

    # 4. Submit and Release Milestone B
    await contract_service.submit_milestone(
        db_session,
        m2.id,
        MilestoneSubmitRequest(
            evidence_url="https://github.com/Sourabh-Kumar04/TrustBridge/pull/2",
            evidence_notes="Final release",
            evidence_payload={"tests_passed": 20, "tests_total": 20},
        ),
    )
    await contract_service.release_milestone(
        db_session,
        m2.id,
        MilestoneReleaseRequest(release_note="Final milestone approved"),
    )

    # 5. Since all milestones released, Contract should be COMPLETED!
    db_session.refresh(contract)
    assert contract.status == ContractStatus.COMPLETED
    assert contract.completed_at is not None


@pytest.mark.asyncio
async def test_milestone_dispute_and_evidence_bundle(db_session, active_contract_with_milestones):
    contract = active_contract_with_milestones
    m1 = contract.milestones[0]

    # Developer submits work
    await contract_service.submit_milestone(
        db_session,
        m1.id,
        MilestoneSubmitRequest(
            evidence_url="https://github.com/Sourabh-Kumar04/TrustBridge/pull/99",
            evidence_notes="Partial code submitted",
        ),
    )

    # Client raises dispute
    dispute_req = MilestoneDisputeRequest(
        initiator_role=ActorRole.CLIENT,
        initiator_email="client@example.com",
        reason="Deliverable does not meet documented acceptance criteria",
        description="The submitted PR is missing required test coverage and JWT validation logic.",
    )
    dispute = await contract_service.dispute_milestone(db_session, m1.id, dispute_req)

    # Check that milestone and contract are frozen in DISPUTED
    db_session.refresh(m1)
    db_session.refresh(contract)
    assert m1.status == MilestoneStatus.DISPUTED
    assert contract.status == ContractStatus.DISPUTED
    assert dispute.status == DisputeStatus.OPEN

    # Check evidence bundle generation for PayPal dispute resolution
    bundle = dispute.evidence_bundle
    assert bundle is not None
    assert bundle["protocol_version"] == "TrustBridge-Evidence-v1"
    assert bundle["contract"]["id"] == contract.id
    assert bundle["disputed_milestone"]["id"] == m1.id
    assert bundle["developer_submission"]["evidence_url"] == "https://github.com/Sourabh-Kumar04/TrustBridge/pull/99"
    assert bundle["dispute_claim"]["reason"] == "Deliverable does not meet documented acceptance criteria"

    # Resolve dispute with developer payout
    resolved = dispute_service.resolve_dispute(
        db=db_session,
        dispute=dispute,
        resolution_status=DisputeStatus.RESOLVED_DEVELOPER_PAYOUT,
        resolution_notes="Mediation complete: developer provided supplementary fixes.",
    )
    assert resolved.status == DisputeStatus.RESOLVED_DEVELOPER_PAYOUT
    db_session.refresh(m1)
    assert m1.status == MilestoneStatus.RELEASED
