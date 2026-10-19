from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from app.models.ledger import LedgerEntry
from app.models.enums import LedgerEntryType, ContractStatus, MilestoneStatus


class LedgerService:
    """
    Event-Sourced Financial Ledger.
    Provides immutable, append-only records of all financial events.
    Every money movement writes a ledger entry FIRST, and application state
    is derived from the ledger history.
    """

    def record_entry(
        self,
        db: Session,
        contract_id: str,
        entry_type: LedgerEntryType,
        amount: float,
        currency: str = "USD",
        milestone_id: Optional[str] = None,
        paypal_reference: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> LedgerEntry:
        # Idempotency check: if entry with idempotency_key already exists, return it
        if idempotency_key:
            existing = (
                db.query(LedgerEntry)
                .filter(LedgerEntry.idempotency_key == idempotency_key)
                .first()
            )
            if existing:
                return existing

        entry = LedgerEntry(
            contract_id=contract_id,
            milestone_id=milestone_id,
            entry_type=entry_type,
            amount=amount,
            currency=currency,
            paypal_reference=paypal_reference,
            idempotency_key=idempotency_key,
            created_at=datetime.now(timezone.utc),
        )
        db.add(entry)
        db.flush()
        return entry

    def get_entries(
        self,
        db: Session,
        contract_id: Optional[str] = None,
        milestone_id: Optional[str] = None,
    ) -> List[LedgerEntry]:
        query = db.query(LedgerEntry)
        if contract_id:
            query = query.filter(LedgerEntry.contract_id == contract_id)
        if milestone_id:
            query = query.filter(LedgerEntry.milestone_id == milestone_id)
        return query.order_by(LedgerEntry.created_at.asc()).all()

    def derive_state_from_ledger(self, db: Session, contract_id: str) -> Dict[str, Any]:
        """
        Derives balance and lifecycle state from immutable ledger events.
        """
        entries = self.get_entries(db, contract_id=contract_id)
        
        total_held = sum(e.amount for e in entries if e.entry_type == LedgerEntryType.HOLD_CREATED)
        total_captured = sum(e.amount for e in entries if e.entry_type == LedgerEntryType.FUNDS_CAPTURED)
        total_paid = sum(e.amount for e in entries if e.entry_type == LedgerEntryType.PAYOUT_COMPLETED)
        total_refunded = sum(e.amount for e in entries if e.entry_type == LedgerEntryType.REFUND_ISSUED)
        
        # Net balance currently in escrow custody
        current_escrow_balance = round(total_captured - total_paid - total_refunded, 2)

        # Milestone states derived from ledger
        milestone_states: Dict[str, MilestoneStatus] = {}
        for e in entries:
            if not e.milestone_id:
                continue
            ms_id = e.milestone_id
            if e.entry_type in (LedgerEntryType.PAYOUT_COMPLETED, LedgerEntryType.PAYOUT_INITIATED):
                milestone_states[ms_id] = MilestoneStatus.RELEASED
            elif e.entry_type == LedgerEntryType.MILESTONE_VERIFIED and milestone_states.get(ms_id) != MilestoneStatus.RELEASED:
                milestone_states[ms_id] = MilestoneStatus.VERIFIED
            elif e.entry_type == LedgerEntryType.REFUND_ISSUED:
                milestone_states[ms_id] = MilestoneStatus.CANCELLED if hasattr(MilestoneStatus, "CANCELLED") else MilestoneStatus.PENDING

        # Contract status derived from ledger
        if total_captured > 0 and current_escrow_balance <= 0.01 and total_paid > 0:
            derived_contract_status = ContractStatus.COMPLETED
        elif total_captured > 0 or total_held > 0:
            derived_contract_status = ContractStatus.ACTIVE
        else:
            derived_contract_status = ContractStatus.DRAFT

        return {
            "contract_id": contract_id,
            "total_held": total_held,
            "total_captured": total_captured,
            "total_paid": total_paid,
            "total_refunded": total_refunded,
            "current_escrow_balance": current_escrow_balance,
            "derived_contract_status": derived_contract_status,
            "milestone_states": milestone_states,
            "entry_count": len(entries),
        }


ledger_service = LedgerService()
