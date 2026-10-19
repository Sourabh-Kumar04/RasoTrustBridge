from datetime import datetime, timezone
from typing import Optional, List
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from app.models.contract import Contract
from app.models.milestone import Milestone
from app.models.payment import AuditLog, EscrowHold
from app.models.enums import ContractStatus, MilestoneStatus, ActorRole, FXStrategy
from app.schemas.contract import ContractCreateSchema
from app.schemas.milestone import MilestoneSubmitRequest, MilestoneReleaseRequest, MilestoneDisputeRequest
from app.services.fx_service import fx_service
from app.services.paypal_client import paypal_client
from app.services.milestone_verifier import milestone_verifier
from app.services.dispute_service import dispute_service


class ContractService:
    """
    Core Escrow Engine orchestrating the contract lifecycle,
    milestone state machine, and PayPal escrow hold & release.
    """

    async def create_contract(self, db: Session, req: ContractCreateSchema) -> Contract:
        # 1. Fetch current exchange rate if locked rate not provided
        locked_rate = req.locked_fx_rate
        if not locked_rate or locked_rate <= 0:
            locked_rate = await fx_service.get_rate(req.base_currency, req.payout_currency)

        # 2. Validate milestone amounts
        milestone_sum = sum(m.amount for m in req.milestones)
        if req.milestones and abs(milestone_sum - req.total_amount) > 0.05:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Milestone amounts sum ({milestone_sum:.2f}) does not match contract total ({req.total_amount:.2f})",
            )

        # 3. Create contract
        contract = Contract(
            title=req.title,
            brief=req.brief,
            client_name=req.client_name,
            client_email=req.client_email,
            client_country=req.client_country,
            developer_name=req.developer_name,
            developer_email=req.developer_email,
            developer_country=req.developer_country,
            base_currency=req.base_currency.upper(),
            payout_currency=req.payout_currency.upper(),
            total_amount=req.total_amount,
            fx_strategy=req.fx_strategy,
            locked_fx_rate=locked_rate,
            status=ContractStatus.DRAFT,
        )
        db.add(contract)
        db.flush()

        # 4. Create milestones
        for idx, m_in in enumerate(req.milestones, start=1):
            payout_amt = round(m_in.amount * locked_rate, 2)
            milestone = Milestone(
                contract_id=contract.id,
                order_index=m_in.order_index or idx,
                title=m_in.title,
                description=m_in.description,
                amount=m_in.amount,
                payout_amount=payout_amt,
                status=MilestoneStatus.PENDING,
                verification_type=m_in.verification_type,
                acceptance_criteria=[c.model_dump() for c in m_in.acceptance_criteria],
            )
            db.add(milestone)

        # 5. Audit log
        audit = AuditLog(
            contract_id=contract.id,
            actor_role=ActorRole.CLIENT,
            actor_identifier=req.client_email,
            action="CONTRACT_CREATED",
            description=f"Contract created with {len(req.milestones)} milestones. Locked FX rate: {locked_rate:.4f} {req.base_currency}/{req.payout_currency}.",
            metadata_json={"fx_strategy": req.fx_strategy.value, "locked_rate": locked_rate},
        )
        db.add(audit)
        db.commit()
        db.refresh(contract)
        return contract

    async def fund_contract(
        self,
        db: Session,
        contract_id: str,
        paypal_order_id: str,
        paypal_capture_id: Optional[str] = None,
    ) -> Contract:
        contract = db.query(Contract).filter(Contract.id == contract_id).first()
        if not contract:
            raise HTTPException(status_code=404, detail="Contract not found")

        if contract.status not in (ContractStatus.DRAFT, ContractStatus.PENDING_FUNDING):
            raise HTTPException(
                status_code=400,
                detail=f"Contract cannot be funded from status '{contract.status.value}'",
            )

        now = datetime.now(timezone.utc)
        contract.paypal_order_id = paypal_order_id
        contract.paypal_capture_id = paypal_capture_id
        contract.status = ContractStatus.ACTIVE
        contract.funded_at = now

        # Update all pending milestones to FUNDED
        for m in contract.milestones:
            if m.status == MilestoneStatus.PENDING:
                m.status = MilestoneStatus.FUNDED
                m.funded_at = now

        # Record escrow hold
        hold = EscrowHold(
            contract_id=contract.id,
            amount=contract.total_amount,
            currency=contract.base_currency,
            paypal_order_id=paypal_order_id,
            paypal_capture_id=paypal_capture_id,
            paypal_status="COMPLETED" if paypal_capture_id else "HELD",
            captured_at=now if paypal_capture_id else None,
        )
        db.add(hold)

        # Audit log
        audit = AuditLog(
            contract_id=contract.id,
            actor_role=ActorRole.PAYPAL,
            actor_identifier=paypal_order_id,
            action="ESCROW_FUNDED",
            description=f"Escrow funded via PayPal order {paypal_order_id}. Total held: {contract.base_currency} {contract.total_amount:.2f}.",
            metadata_json={"order_id": paypal_order_id, "capture_id": paypal_capture_id},
        )
        db.add(audit)
        db.commit()
        db.refresh(contract)
        return contract

    async def submit_milestone(
        self,
        db: Session,
        milestone_id: str,
        req: MilestoneSubmitRequest,
    ) -> Milestone:
        milestone = db.query(Milestone).filter(Milestone.id == milestone_id).first()
        if not milestone:
            raise HTTPException(status_code=404, detail="Milestone not found")

        if milestone.status not in (MilestoneStatus.FUNDED, MilestoneStatus.SUBMITTED):
            raise HTTPException(
                status_code=400,
                detail=f"Milestone cannot be submitted in status '{milestone.status.value}'. Must be FUNDED.",
            )

        now = datetime.now(timezone.utc)
        milestone.evidence_url = req.evidence_url
        milestone.evidence_notes = req.evidence_notes
        milestone.evidence_payload = req.evidence_payload
        milestone.submitted_at = now
        milestone.status = MilestoneStatus.SUBMITTED

        # Automatically execute programmable verification
        score, log, is_approved = milestone_verifier.verify_submission(
            milestone=milestone,
            evidence_url=req.evidence_url,
            evidence_notes=req.evidence_notes or "",
            evidence_payload=req.evidence_payload,
        )
        milestone.verification_score = score
        milestone.verification_log = log
        milestone.verified_by = "AI_ENGINE_V1"

        if is_approved:
            milestone.status = MilestoneStatus.VERIFIED
            milestone.verified_at = now

        audit = AuditLog(
            contract_id=milestone.contract_id,
            milestone_id=milestone.id,
            actor_role=ActorRole.DEVELOPER,
            actor_identifier=milestone.contract.developer_email,
            action="MILESTONE_SUBMITTED",
            description=f"Evidence submitted for Milestone {milestone.order_index}. Verification score: {score:.2f} ({'VERIFIED' if is_approved else 'SUBMITTED'}).",
            metadata_json={"score": score, "status": milestone.status.value, "url": req.evidence_url},
        )
        db.add(audit)
        db.commit()
        db.refresh(milestone)
        return milestone

    async def verify_milestone(
        self,
        db: Session,
        milestone_id: str,
        override_score: Optional[float] = None,
        notes: Optional[str] = None,
    ) -> Milestone:
        milestone = db.query(Milestone).filter(Milestone.id == milestone_id).first()
        if not milestone:
            raise HTTPException(status_code=404, detail="Milestone not found")

        if milestone.status not in (MilestoneStatus.SUBMITTED, MilestoneStatus.VERIFIED):
            raise HTTPException(
                status_code=400,
                detail=f"Milestone in status '{milestone.status.value}' cannot be verified.",
            )

        now = datetime.now(timezone.utc)
        score, log, is_approved = milestone_verifier.verify_submission(
            milestone=milestone,
            evidence_url=milestone.evidence_url or "",
            evidence_notes=notes or milestone.evidence_notes or "",
            evidence_payload=milestone.evidence_payload,
            override_score=override_score,
        )

        milestone.verification_score = score
        milestone.verification_log = log
        milestone.verified_by = "CLIENT_VERIFIER" if override_score is not None else "AI_ENGINE_V1"

        if is_approved:
            milestone.status = MilestoneStatus.VERIFIED
            milestone.verified_at = now

        audit = AuditLog(
            contract_id=milestone.contract_id,
            milestone_id=milestone.id,
            actor_role=ActorRole.CLIENT if override_score is not None else ActorRole.AI_AGENT,
            actor_identifier=milestone.contract.client_email if override_score is not None else "AI_ENGINE",
            action="MILESTONE_VERIFIED",
            description=f"Milestone {milestone.order_index} verification evaluated. Score: {score:.2f}. Status: {milestone.status.value}.",
            metadata_json={"score": score, "override": override_score is not None},
        )
        db.add(audit)
        db.commit()
        db.refresh(milestone)
        return milestone

    async def release_milestone(
        self,
        db: Session,
        milestone_id: str,
        req: MilestoneReleaseRequest,
    ) -> Milestone:
        milestone = db.query(Milestone).filter(Milestone.id == milestone_id).first()
        if not milestone:
            raise HTTPException(status_code=404, detail="Milestone not found")

        if milestone.status != MilestoneStatus.VERIFIED:
            raise HTTPException(
                status_code=400,
                detail=f"Milestone must be in VERIFIED status to release funds (current: {milestone.status.value})",
            )

        contract = milestone.contract
        now = datetime.now(timezone.utc)

        # 1. Calculate final payout based on FX strategy
        payout_rate = req.override_payout_rate
        if not payout_rate or payout_rate <= 0:
            comparison = await fx_service.compare_locked_vs_spot(
                base_currency=contract.base_currency,
                target_currency=contract.payout_currency,
                amount=milestone.amount,
                locked_rate=contract.locked_fx_rate or 1.0,
                fx_strategy=contract.fx_strategy,
            )
            final_payout_amount = comparison.effective_payout_amount
        else:
            final_payout_amount = round(milestone.amount * payout_rate, 2)

        milestone.payout_amount = final_payout_amount

        # 2. Trigger PayPal Payout to developer
        note = f"{req.release_note} [Milestone {milestone.order_index}: {milestone.title}]"
        payout_res = await paypal_client.create_payout(
            recipient_email=contract.developer_email,
            amount=milestone.amount,  # PayPal payouts in base currency
            currency=contract.base_currency,
            note=note,
            sender_item_id=milestone.id,
        )

        milestone.paypal_payout_batch_id = payout_res.get("payout_batch_id")
        milestone.paypal_payout_item_id = payout_res.get("payout_item_id")
        milestone.status = MilestoneStatus.RELEASED
        milestone.released_at = now

        # 3. Check if all milestones completed
        all_milestones = db.query(Milestone).filter(Milestone.contract_id == contract.id).all()
        all_released = all(m.status == MilestoneStatus.RELEASED for m in all_milestones)
        if all_released:
            contract.status = ContractStatus.COMPLETED
            contract.completed_at = now

        audit = AuditLog(
            contract_id=contract.id,
            milestone_id=milestone.id,
            actor_role=ActorRole.SYSTEM,
            actor_identifier="PAYPAL_PAYOUT_ENGINE",
            action="MILESTONE_RELEASED",
            description=f"Funds released for Milestone {milestone.order_index}. Payout: {contract.base_currency} {milestone.amount:.2f} (approx {contract.payout_currency} {final_payout_amount:,.2f}). PayPal Batch: {milestone.paypal_payout_batch_id}.",
            metadata_json={"batch_id": milestone.paypal_payout_batch_id, "amount": milestone.amount},
        )
        db.add(audit)
        db.commit()
        db.refresh(milestone)
        return milestone

    async def dispute_milestone(
        self,
        db: Session,
        milestone_id: str,
        req: MilestoneDisputeRequest,
    ):
        milestone = db.query(Milestone).filter(Milestone.id == milestone_id).first()
        if not milestone:
            raise HTTPException(status_code=404, detail="Milestone not found")

        contract = milestone.contract
        return dispute_service.open_dispute(
            db=db,
            contract=contract,
            milestone=milestone,
            initiator_role=req.initiator_role,
            initiator_email=req.initiator_email,
            reason=req.reason,
            description=req.description,
        )


contract_service = ContractService()
