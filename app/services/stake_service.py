import logging
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Tuple
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.contract import Contract
from app.models.payment import Stake, AuditLog
from app.models.enums import ActorRole, LedgerEntryType, ContractStatus
from app.models.user import User
from app.services.ledger_service import ledger_service
from app.services.reputation_service import reputation_service
from app.services.paypal_client import paypal_client

logger = logging.getLogger("trustbridge.stake")


class StakeService:
    """
    Security Deposit & Staking Service with Economic Skin.
    Manages freelancer collateral held alongside client escrow.
    """

    def __init__(self):
        self.settings = get_settings()

    def get_stake_by_contract_id(self, db: Session, contract_id: str) -> Optional[Stake]:
        return db.query(Stake).filter(Stake.contract_id == contract_id).first()

    def post_stake(
        self,
        db: Session,
        contract: Contract,
        freelancer: Optional[User],
        paypal_order_id: Optional[str] = None,
        paypal_capture_id: Optional[str] = None,
        offered_stake_percentage: Optional[float] = None,
        offered_stake_amount: Optional[float] = None,
        idempotency_key: Optional[str] = None,
    ) -> Stake:
        """
        Posts freelancer security deposit / stake when accepting contract.
        Validates new-account friction rules:
        - If contract >= $1,000 and freelancer is new (<7d) with 0 prior deals, requires 10% stake.
        - Otherwise defaults to 5% stake.
        """
        # Idempotency / duplicate check
        existing_stake = self.get_stake_by_contract_id(db, contract.id)
        if existing_stake:
            return existing_stake

        is_eligible, req_pct, error_msg = reputation_service.validate_freelancer_for_contract(
            freelancer=freelancer,
            contract_amount=contract.total_amount,
            offered_stake_percentage=offered_stake_percentage,
        )

        if not is_eligible:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=error_msg or "Freelancer ineligible without required security deposit.",
            )

        stake_pct = offered_stake_percentage if (offered_stake_percentage and offered_stake_percentage >= req_pct) else req_pct
        calculated_amount = round(contract.total_amount * (stake_pct / 100.0), 2)
        stake_amount = offered_stake_amount if offered_stake_amount is not None else calculated_amount

        if stake_amount < calculated_amount:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Stake amount ({stake_amount:.2f}) is lower than required {stake_pct:.1f}% ({calculated_amount:.2f}).",
            )

        order_id = paypal_order_id or f"ORDER-STK-{uuid.uuid4().hex[:8].upper()}"
        capture_id = paypal_capture_id or f"CAP-STK-{uuid.uuid4().hex[:8].upper()}"
        idem_key = idempotency_key or f"stake_{contract.id}_{order_id}"

        # 1. Event-Sourced Ledger entry FIRST
        ledger_service.record_entry(
            db=db,
            contract_id=contract.id,
            milestone_id=None,
            entry_type=LedgerEntryType.STAKE_POSTED,
            amount=stake_amount,
            currency=contract.base_currency,
            paypal_reference=order_id,
            idempotency_key=f"post_{idem_key}",
        )

        now = datetime.now(timezone.utc)
        stake = Stake(
            contract_id=contract.id,
            freelancer_id=freelancer.id if freelancer else contract.freelancer_id,
            amount=stake_amount,
            currency=contract.base_currency,
            stake_percentage=stake_pct,
            status="HELD",
            paypal_order_id=order_id,
            paypal_capture_id=capture_id,
            created_at=now,
        )
        db.add(stake)

        # Audit log
        dev_email = freelancer.email if freelancer else contract.developer_email
        audit = AuditLog(
            contract_id=contract.id,
            actor_role=ActorRole.DEVELOPER,
            actor_identifier=dev_email,
            action="STAKE_POSTED",
            description=(
                f"Freelancer security deposit posted: {contract.base_currency} {stake_amount:.2f} "
                f"({stake_pct:.1f}% of {contract.total_amount:.2f}). Held via PayPal alongside client escrow."
            ),
            metadata_json={
                "stake_amount": stake_amount,
                "stake_percentage": stake_pct,
                "paypal_order_id": order_id,
                "paypal_capture_id": capture_id,
            },
        )
        db.add(audit)
        db.commit()
        db.refresh(stake)
        return stake

    def return_stake(
        self,
        db: Session,
        contract_id: str,
        dispute_id: Optional[str] = None,
    ) -> Optional[Stake]:
        """
        Returns freelancer stake in full upon successful contract release or winning a dispute.
        """
        stake = self.get_stake_by_contract_id(db, contract_id)
        if not stake or stake.status != "HELD":
            return stake

        now = datetime.now(timezone.utc)
        contract = stake.contract

        # 1. Event-sourced ledger entry FIRST
        ref = f"dispute_return_{dispute_id}" if dispute_id else f"release_return_{contract_id}"
        ledger_service.record_entry(
            db=db,
            contract_id=contract.id,
            milestone_id=None,
            entry_type=LedgerEntryType.STAKE_RETURNED,
            amount=stake.amount,
            currency=stake.currency,
            paypal_reference=ref,
            idempotency_key=f"return_{stake.id}_{int(now.timestamp())}",
        )

        stake.status = "RETURNED"
        stake.returned_amount = stake.amount
        stake.settled_at = now

        audit = AuditLog(
            contract_id=contract.id,
            actor_role=ActorRole.SYSTEM,
            actor_identifier="STAKE_ENGINE",
            action="STAKE_RETURNED",
            description=f"Security deposit of {stake.currency} {stake.amount:.2f} returned in full to freelancer {contract.developer_email}.",
            metadata_json={"returned_amount": stake.amount, "reason": "DISPUTE_WON" if dispute_id else "SUCCESSFUL_RELEASE"},
        )
        db.add(audit)
        db.commit()
        db.refresh(stake)
        return stake

    def slash_stake(
        self,
        db: Session,
        contract_id: str,
        slash_percentage: Optional[float] = None,
        dispute_id: Optional[str] = None,
    ) -> Optional[Stake]:
        """
        Slashes freelancer stake when client refund dispute is resolved against freelancer.
        """
        stake = self.get_stake_by_contract_id(db, contract_id)
        if not stake or stake.status != "HELD":
            return stake

        pct = slash_percentage if slash_percentage is not None else self.settings.DEFAULT_SLASH_PERCENTAGE
        slashed_amount = round(stake.amount * (pct / 100.0), 2)
        refund_amount = round(stake.amount - slashed_amount, 2)
        now = datetime.now(timezone.utc)
        contract = stake.contract

        # 1. Event-sourced ledger entry FIRST
        ref = f"dispute_slash_{dispute_id}" if dispute_id else f"slash_{contract_id}"
        ledger_service.record_entry(
            db=db,
            contract_id=contract.id,
            milestone_id=None,
            entry_type=LedgerEntryType.STAKE_SLASHED,
            amount=slashed_amount,
            currency=stake.currency,
            paypal_reference=ref,
            idempotency_key=f"slash_{stake.id}_{int(now.timestamp())}",
        )

        if refund_amount > 0:
            ledger_service.record_entry(
                db=db,
                contract_id=contract.id,
                milestone_id=None,
                entry_type=LedgerEntryType.STAKE_RETURNED,
                amount=refund_amount,
                currency=stake.currency,
                paypal_reference=f"{ref}_partial_return",
                idempotency_key=f"part_return_{stake.id}_{int(now.timestamp())}",
            )

        stake.status = "SLASHED"
        stake.slashed_amount = slashed_amount
        stake.returned_amount = refund_amount
        stake.settled_at = now

        audit = AuditLog(
            contract_id=contract.id,
            actor_role=ActorRole.SYSTEM,
            actor_identifier="STAKE_ENGINE",
            action="STAKE_SLASHED",
            description=(
                f"Security deposit slashed by {pct:.1f}% ({stake.currency} {slashed_amount:.2f} slashed"
                f"{f', {refund_amount:.2f} returned' if refund_amount > 0 else ''}) due to dispute lost by freelancer."
            ),
            metadata_json={"slashed_amount": slashed_amount, "returned_amount": refund_amount, "dispute_id": dispute_id},
        )
        db.add(audit)
        db.commit()
        db.refresh(stake)
        return stake


stake_service = StakeService()
