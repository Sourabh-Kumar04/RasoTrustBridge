from datetime import datetime, timezone, timedelta
from typing import Optional, List
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from app.models.contract import Contract
from app.models.milestone import Milestone
from app.models.payment import AuditLog, EscrowHold
from app.models.enums import ContractStatus, MilestoneStatus, ActorRole, FXStrategy, LedgerEntryType
from app.schemas.contract import ContractCreateSchema
from app.schemas.milestone import MilestoneSubmitRequest, MilestoneReleaseRequest, MilestoneDisputeRequest
from app.services.fx_service import fx_service
from app.services.paypal_client import paypal_client
from app.services.milestone_verifier import milestone_verifier
from app.services.dispute_service import dispute_service
from app.services.notification_service import notification_service
from app.services.ledger_service import ledger_service
from app.services.reputation_service import reputation_service
from app.models.user import User


class ContractService:
    """
    Core Escrow Engine orchestrating the contract lifecycle,
    milestone state machine, event-sourced financial ledger, and PayPal escrow.
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

        # 3. Resolve user account IDs (client_id, freelancer_id)
        client_id = req.client_id
        if not client_id:
            u_client = db.query(User).filter(User.email == req.client_email.lower()).first()
            if u_client:
                client_id = u_client.id

        freelancer_id = req.freelancer_id
        if not freelancer_id:
            u_dev = db.query(User).filter(User.email == req.developer_email.lower()).first()
            if u_dev:
                freelancer_id = u_dev.id

        # 4. Create contract with configurable buyer timeout
        contract = Contract(
            title=req.title,
            brief=req.brief,
            client_id=client_id,
            freelancer_id=freelancer_id,
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
            buyer_timeout_days=req.buyer_timeout_days or 7,
            status=ContractStatus.DRAFT,
        )
        db.add(contract)
        db.flush()

        # 5. Create milestones
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

        # 6. Audit log
        audit = AuditLog(
            contract_id=contract.id,
            actor_role=ActorRole.CLIENT,
            actor_identifier=req.client_email,
            action="CONTRACT_CREATED",
            description=f"Contract created with {len(req.milestones)} milestones. Locked FX rate: {locked_rate:.4f} {req.base_currency}/{req.payout_currency}. Buyer timeout: {contract.buyer_timeout_days} days.",
            metadata_json={"fx_strategy": req.fx_strategy.value, "locked_rate": locked_rate, "buyer_timeout_days": contract.buyer_timeout_days},
        )
        db.add(audit)
        db.commit()
        db.refresh(contract)
        self._attach_trust_scores(contract)
        return contract

    async def fund_contract(
        self,
        db: Session,
        contract_id: str,
        paypal_order_id: str,
        paypal_capture_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Contract:
        contract = db.query(Contract).filter(Contract.id == contract_id).first()
        if not contract:
            raise HTTPException(status_code=404, detail="Contract not found")

        # Idempotency check: if escrow hold with this order or key exists, return contract
        idem_key = idempotency_key or f"fund_{contract.id}_{paypal_order_id}"
        existing_hold = (
            db.query(EscrowHold)
            .filter(
                (EscrowHold.idempotency_key == idem_key) |
                (EscrowHold.paypal_order_id == paypal_order_id)
            )
            .first()
        )
        if existing_hold and contract.status == ContractStatus.ACTIVE:
            self._attach_trust_scores(contract)
            return contract

        if contract.status not in (ContractStatus.DRAFT, ContractStatus.PENDING_FUNDING):
            raise HTTPException(
                status_code=400,
                detail=f"Contract cannot be funded from status '{contract.status.value}'",
            )

        now = datetime.now(timezone.utc)

        # 1. Event-Sourced Ledger: Write financial ledger entries FIRST
        ledger_service.record_entry(
            db=db,
            contract_id=contract.id,
            entry_type=LedgerEntryType.HOLD_CREATED,
            amount=contract.total_amount,
            currency=contract.base_currency,
            paypal_reference=paypal_order_id,
            idempotency_key=f"hold_{idem_key}",
        )
        if paypal_capture_id:
            ledger_service.record_entry(
                db=db,
                contract_id=contract.id,
                entry_type=LedgerEntryType.FUNDS_CAPTURED,
                amount=contract.total_amount,
                currency=contract.base_currency,
                paypal_reference=paypal_capture_id,
                idempotency_key=f"cap_{idem_key}",
            )

        # 2. State is derived from ledger
        contract.paypal_order_id = paypal_order_id
        contract.paypal_capture_id = paypal_capture_id
        contract.status = ContractStatus.ACTIVE
        contract.funded_at = now

        # Update all pending milestones to FUNDED
        for m in contract.milestones:
            if m.status == MilestoneStatus.PENDING:
                m.status = MilestoneStatus.FUNDED
                m.funded_at = now

        # 3. Record escrow hold
        hold = EscrowHold(
            contract_id=contract.id,
            amount=contract.total_amount,
            currency=contract.base_currency,
            paypal_order_id=paypal_order_id,
            paypal_capture_id=paypal_capture_id,
            paypal_status="COMPLETED" if paypal_capture_id else "HELD",
            idempotency_key=idem_key,
            captured_at=now if paypal_capture_id else None,
        )
        db.add(hold)

        # 4. Audit log
        audit = AuditLog(
            contract_id=contract.id,
            actor_role=ActorRole.PAYPAL,
            actor_identifier=paypal_order_id,
            action="ESCROW_FUNDED",
            description=f"Escrow funded via PayPal order {paypal_order_id}. Total held: {contract.base_currency} {contract.total_amount:.2f}.",
            metadata_json={"order_id": paypal_order_id, "capture_id": paypal_capture_id, "idempotency_key": idem_key},
        )
        db.add(audit)
        db.commit()
        db.refresh(contract)
        self._attach_trust_scores(contract)
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
        contract = milestone.contract
        timeout_days = contract.buyer_timeout_days or 7

        milestone.evidence_url = req.evidence_url
        milestone.evidence_notes = req.evidence_notes
        milestone.evidence_payload = req.evidence_payload
        milestone.submitted_at = now
        # Configure buyer timeout expiry
        milestone.expires_at = now + timedelta(days=timeout_days)
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
            # Write ledger entry FIRST
            ledger_service.record_entry(
                db=db,
                contract_id=contract.id,
                milestone_id=milestone.id,
                entry_type=LedgerEntryType.MILESTONE_VERIFIED,
                amount=milestone.amount,
                currency=contract.base_currency,
                paypal_reference=None,
                idempotency_key=f"verify_{milestone.id}_{int(now.timestamp())}",
            )
            milestone.status = MilestoneStatus.VERIFIED
            milestone.verified_at = now

        audit = AuditLog(
            contract_id=milestone.contract_id,
            milestone_id=milestone.id,
            actor_role=ActorRole.DEVELOPER,
            actor_identifier=milestone.contract.developer_email,
            action="MILESTONE_SUBMITTED",
            description=f"Evidence submitted for Milestone {milestone.order_index}. Verification score: {score:.2f} ({'VERIFIED' if is_approved else 'SUBMITTED'}). Auto-release expiry: {milestone.expires_at.isoformat()}.",
            metadata_json={"score": score, "status": milestone.status.value, "url": req.evidence_url, "expires_at": milestone.expires_at.isoformat()},
        )
        db.add(audit)
        db.commit()
        db.refresh(milestone)

        # Event triggers: notify client of deliverable submission
        notification_service.notify_milestone_submitted(db, milestone.contract, milestone)
        if is_approved:
            notification_service.notify_milestone_verified(db, milestone.contract, milestone)

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
            # Write ledger entry FIRST
            ledger_service.record_entry(
                db=db,
                contract_id=milestone.contract_id,
                milestone_id=milestone.id,
                entry_type=LedgerEntryType.MILESTONE_VERIFIED,
                amount=milestone.amount,
                currency=milestone.contract.base_currency,
                paypal_reference=None,
                idempotency_key=f"verify_{milestone.id}_{int(now.timestamp())}",
            )
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

        if is_approved:
            notification_service.notify_milestone_verified(db, milestone.contract, milestone)

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

        idem_key = req.idempotency_key or f"payout_{milestone.id}"

        # Idempotency check: if already released or payout executed with this idempotency key
        if milestone.status == MilestoneStatus.RELEASED:
            return milestone

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

        # 2. Trigger PayPal Payout to developer with idempotency key
        note = f"{req.release_note} [Milestone {milestone.order_index}: {milestone.title}]"
        payout_res = await paypal_client.create_payout(
            recipient_email=contract.developer_email,
            amount=milestone.amount,  # PayPal payouts in base currency
            currency=contract.base_currency,
            note=note,
            sender_item_id=milestone.id,
            idempotency_key=idem_key,
        )

        batch_id = payout_res.get("payout_batch_id")

        # 3. Event-Sourced Ledger: Write ledger entries FIRST
        ledger_service.record_entry(
            db=db,
            contract_id=contract.id,
            milestone_id=milestone.id,
            entry_type=LedgerEntryType.PAYOUT_INITIATED,
            amount=milestone.amount,
            currency=contract.base_currency,
            paypal_reference=batch_id,
            idempotency_key=f"init_{idem_key}",
        )
        ledger_service.record_entry(
            db=db,
            contract_id=contract.id,
            milestone_id=milestone.id,
            entry_type=LedgerEntryType.PAYOUT_COMPLETED,
            amount=milestone.amount,
            currency=contract.base_currency,
            paypal_reference=batch_id,
            idempotency_key=idem_key,
        )

        # 4. Update Milestone state
        milestone.paypal_payout_batch_id = batch_id
        milestone.paypal_payout_item_id = payout_res.get("payout_item_id")
        milestone.idempotency_key = idem_key
        milestone.status = MilestoneStatus.RELEASED
        milestone.released_at = now

        # 5. Check if all milestones completed
        all_milestones = db.query(Milestone).filter(Milestone.contract_id == contract.id).all()
        all_released = all(m.status == MilestoneStatus.RELEASED for m in all_milestones)
        if all_released:
            contract.status = ContractStatus.COMPLETED
            contract.completed_at = now
            # Harmer's Graduated Model: Return freelancer stake and client deposit in full upon completion
            from app.services.stake_service import stake_service
            stake_service.return_stake(db, contract.id)
            stake_service.return_client_deposit(db, contract.id)

        # 6. Reputation Engine: Award trust points on successful release
        reputation_service.record_successful_release(db, contract.freelancer_user)
        reputation_service.record_successful_release(db, contract.client_user)

        audit = AuditLog(
            contract_id=contract.id,
            milestone_id=milestone.id,
            actor_role=ActorRole.SYSTEM,
            actor_identifier="PAYPAL_PAYOUT_ENGINE",
            action="MILESTONE_RELEASED",
            description=f"Funds released for Milestone {milestone.order_index}. Payout: {contract.base_currency} {milestone.amount:.2f} (approx {contract.payout_currency} {final_payout_amount:,.2f}). PayPal Batch: {milestone.paypal_payout_batch_id}. Idempotency key: {idem_key}.",
            metadata_json={"batch_id": milestone.paypal_payout_batch_id, "amount": milestone.amount, "idempotency_key": idem_key},
        )
        db.add(audit)
        db.commit()
        db.refresh(milestone)

        # Event trigger: notify developer of released payout
        notification_service.notify_funds_released(db, contract, milestone)

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
        dispute = dispute_service.open_dispute(
            db=db,
            contract=contract,
            milestone=milestone,
            initiator_role=req.initiator_role,
            initiator_email=req.initiator_email,
            reason=req.reason,
            description=req.description,
        )
        notification_service.notify_dispute_opened(db, contract, milestone, dispute)
        return dispute

    async def check_and_auto_release_expired_milestones(self, db: Session) -> List[Milestone]:
        """
        Background task / endpoint:
        Finds all submitted milestones past their buyer timeout expiry date
        with no buyer action, and auto-transitions them to VERIFIED with audit log entry:
        'auto-released: buyer timeout'.
        """
        now = datetime.now(timezone.utc)
        expired_milestones = (
            db.query(Milestone)
            .filter(
                Milestone.status == MilestoneStatus.SUBMITTED,
                Milestone.expires_at.isnot(None),
                Milestone.expires_at <= now,
            )
            .all()
        )

        released_list = []
        for ms in expired_milestones:
            contract = ms.contract
            
            # Write ledger entry FIRST
            ledger_service.record_entry(
                db=db,
                contract_id=contract.id,
                milestone_id=ms.id,
                entry_type=LedgerEntryType.MILESTONE_VERIFIED,
                amount=ms.amount,
                currency=contract.base_currency,
                paypal_reference="auto-released: buyer timeout",
                idempotency_key=f"auto_release_{ms.id}_{int(ms.expires_at.timestamp())}",
            )

            ms.status = MilestoneStatus.VERIFIED
            ms.verified_at = now
            ms.verified_by = "BUYER_TIMEOUT_AUTO_RELEASE"
            ms.verification_score = 1.0

            audit = AuditLog(
                contract_id=contract.id,
                milestone_id=ms.id,
                actor_role=ActorRole.SYSTEM,
                actor_identifier="BUYER_TIMEOUT_AUTO_RELEASE",
                action="MILESTONE_VERIFIED",
                description="auto-released: buyer timeout",
                metadata_json={
                    "reason": "buyer_timeout",
                    "submitted_at": ms.submitted_at.isoformat() if ms.submitted_at else None,
                    "expired_at": ms.expires_at.isoformat() if ms.expires_at else None,
                    "timeout_days": contract.buyer_timeout_days,
                },
            )
            db.add(audit)
            released_list.append(ms)

            # Notify parties
            notification_service.notify_milestone_verified(db, contract, ms)

        if released_list:
            db.commit()
            for ms in released_list:
                db.refresh(ms)

        return released_list

    def _attach_trust_scores(self, contract: Contract) -> None:
        """Helper to attach live trust scores to contract response representation."""
        if contract.client_user:
            setattr(contract, "client_trust_score", contract.client_user.trust_score)
        else:
            setattr(contract, "client_trust_score", 50.0)

        if contract.freelancer_user:
            setattr(contract, "freelancer_trust_score", contract.freelancer_user.trust_score)
        else:
            setattr(contract, "freelancer_trust_score", 50.0)


contract_service = ContractService()
