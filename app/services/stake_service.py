import logging
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Tuple
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.contract import Contract
from app.models.payment import Stake, ClientDeposit, AuditLog
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

    def get_client_deposit_by_contract_id(self, db: Session, contract_id: str) -> Optional[ClientDeposit]:
        return db.query(ClientDeposit).filter(ClientDeposit.contract_id == contract_id).first()

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
        Posts freelancer security deposit / stake when accepting contract using Graduated Stake Model:
        - New accounts (< 7 days OR 0 completed deals): 10% of contract value
        - Emerging (1-4 completed deals, no lost disputes): 7%
        - Established (5-19 completed deals, dispute-loss rate < 10%): 4%
        - Proven (20+ completed deals, dispute-loss rate < 5%): 2%
        
        Also posts the symmetric client deposit: 50% of the freelancer's stake amount.
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

        # 1. Event-Sourced Ledger entry FIRST for freelancer stake
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
            party="freelancer",
            status="HELD",
            paypal_order_id=order_id,
            paypal_capture_id=capture_id,
            created_at=now,
        )
        db.add(stake)

        # Audit log for freelancer stake
        dev_email = freelancer.email if freelancer else contract.developer_email
        audit = AuditLog(
            contract_id=contract.id,
            actor_role=ActorRole.DEVELOPER,
            actor_identifier=dev_email,
            action="STAKE_POSTED",
            description=(
                f"Freelancer security deposit posted: {contract.base_currency} {stake_amount:.2f} "
                f"({stake_pct:.1f}% of {contract.total_amount:.2f}). Applied graduated rate."
            ),
            metadata_json={
                "stake_amount": stake_amount,
                "stake_percentage": stake_pct,
                "paypal_order_id": order_id,
                "paypal_capture_id": capture_id,
            },
        )
        db.add(audit)

        # 2. Symmetric Client Deposit (50% of freelancer stake)
        existing_deposit = self.get_client_deposit_by_contract_id(db, contract.id)
        if not existing_deposit:
            client_deposit_amount = round(stake_amount * 0.5, 2)
            cld_order_id = f"ORDER-CLD-{uuid.uuid4().hex[:8].upper()}"
            cld_capture_id = f"CAP-CLD-{uuid.uuid4().hex[:8].upper()}"

            ledger_service.record_entry(
                db=db,
                contract_id=contract.id,
                milestone_id=None,
                entry_type=LedgerEntryType.CLIENT_DEPOSIT_POSTED,
                amount=client_deposit_amount,
                currency=contract.base_currency,
                paypal_reference=cld_order_id,
                idempotency_key=f"cld_post_{contract.id}_{cld_order_id}",
            )

            client_deposit = ClientDeposit(
                contract_id=contract.id,
                client_id=contract.client_id,
                amount=client_deposit_amount,
                currency=contract.base_currency,
                party="client",
                status="HELD",
                paypal_order_id=cld_order_id,
                paypal_capture_id=cld_capture_id,
                created_at=now,
            )
            db.add(client_deposit)

            client_audit = AuditLog(
                contract_id=contract.id,
                actor_role=ActorRole.CLIENT,
                actor_identifier=contract.client_email,
                action="CLIENT_DEPOSIT_POSTED",
                description=(
                    f"Symmetric client good-faith deposit posted: {contract.base_currency} {client_deposit_amount:.2f} "
                    f"(50% of freelancer stake {stake_amount:.2f}). Held alongside contract escrow."
                ),
                metadata_json={
                    "client_deposit_amount": client_deposit_amount,
                    "freelancer_stake_amount": stake_amount,
                    "paypal_order_id": cld_order_id,
                },
            )
            db.add(client_audit)

        db.commit()
        db.refresh(stake)
        return stake

    def post_client_deposit(
        self,
        db: Session,
        contract: Contract,
        amount: Optional[float] = None,
        paypal_order_id: Optional[str] = None,
        paypal_capture_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> ClientDeposit:
        """Explicitly posts or updates client good-faith deposit."""
        existing = self.get_client_deposit_by_contract_id(db, contract.id)
        if existing:
            return existing

        stake = self.get_stake_by_contract_id(db, contract.id)
        expected_amount = round(stake.amount * 0.5, 2) if stake else round(contract.total_amount * 0.05, 2)
        deposit_amount = amount if amount is not None else expected_amount

        order_id = paypal_order_id or f"ORDER-CLD-{uuid.uuid4().hex[:8].upper()}"
        capture_id = paypal_capture_id or f"CAP-CLD-{uuid.uuid4().hex[:8].upper()}"
        idem_key = idempotency_key or f"cld_{contract.id}_{order_id}"
        now = datetime.now(timezone.utc)

        ledger_service.record_entry(
            db=db,
            contract_id=contract.id,
            milestone_id=None,
            entry_type=LedgerEntryType.CLIENT_DEPOSIT_POSTED,
            amount=deposit_amount,
            currency=contract.base_currency,
            paypal_reference=order_id,
            idempotency_key=f"post_{idem_key}",
        )

        client_deposit = ClientDeposit(
            contract_id=contract.id,
            client_id=contract.client_id,
            amount=deposit_amount,
            currency=contract.base_currency,
            party="client",
            status="HELD",
            paypal_order_id=order_id,
            paypal_capture_id=capture_id,
            created_at=now,
        )
        db.add(client_deposit)

        audit = AuditLog(
            contract_id=contract.id,
            actor_role=ActorRole.CLIENT,
            actor_identifier=contract.client_email,
            action="CLIENT_DEPOSIT_POSTED",
            description=f"Symmetric client good-faith deposit posted: {contract.base_currency} {deposit_amount:.2f}.",
            metadata_json={"amount": deposit_amount, "paypal_order_id": order_id},
        )
        db.add(audit)
        db.commit()
        db.refresh(client_deposit)
        return client_deposit

    def return_client_deposit(
        self,
        db: Session,
        contract_id: str,
        dispute_id: Optional[str] = None,
    ) -> Optional[ClientDeposit]:
        """
        Returns client deposit in full when contract completes normally,
        or when client wins a dispute (arbiter rules for client).
        """
        deposit = self.get_client_deposit_by_contract_id(db, contract_id)
        if not deposit or deposit.status != "HELD":
            return deposit

        now = datetime.now(timezone.utc)
        contract = deposit.contract
        ref = f"dispute_return_{dispute_id}" if dispute_id else f"release_return_{contract_id}"

        ledger_service.record_entry(
            db=db,
            contract_id=contract.id,
            milestone_id=None,
            entry_type=LedgerEntryType.CLIENT_DEPOSIT_RETURNED,
            amount=deposit.amount,
            currency=deposit.currency,
            paypal_reference=ref,
            idempotency_key=f"cld_return_{deposit.id}_{int(now.timestamp())}",
        )

        deposit.status = "RETURNED"
        deposit.returned_amount = deposit.amount
        deposit.settled_at = now

        audit = AuditLog(
            contract_id=contract.id,
            actor_role=ActorRole.SYSTEM,
            actor_identifier="STAKE_ENGINE",
            action="CLIENT_DEPOSIT_RETURNED",
            description=f"Client deposit of {deposit.currency} {deposit.amount:.2f} returned in full to client {contract.client_email}.",
            metadata_json={"returned_amount": deposit.amount, "reason": "DISPUTE_WON" if dispute_id else "NORMAL_COMPLETION"},
        )
        db.add(audit)
        db.commit()
        db.refresh(deposit)
        return deposit

    def forfeit_client_deposit(
        self,
        db: Session,
        contract_id: str,
        dispute_id: Optional[str] = None,
    ) -> Optional[ClientDeposit]:
        """
        Forfeits client deposit to freelancer when client loses dispute (frivolous dispute penalty).
        """
        deposit = self.get_client_deposit_by_contract_id(db, contract_id)
        if not deposit or deposit.status != "HELD":
            return deposit

        now = datetime.now(timezone.utc)
        contract = deposit.contract
        ref = f"dispute_forfeit_{dispute_id}" if dispute_id else f"forfeit_{contract_id}"

        ledger_service.record_entry(
            db=db,
            contract_id=contract.id,
            milestone_id=None,
            entry_type=LedgerEntryType.CLIENT_DEPOSIT_FORFEITED,
            amount=deposit.amount,
            currency=deposit.currency,
            paypal_reference=ref,
            idempotency_key=f"cld_forfeit_{deposit.id}_{int(now.timestamp())}",
        )

        deposit.status = "FORFEITED"
        deposit.forfeited_amount = deposit.amount
        deposit.settled_at = now

        audit = AuditLog(
            contract_id=contract.id,
            actor_role=ActorRole.SYSTEM,
            actor_identifier="STAKE_ENGINE",
            action="CLIENT_DEPOSIT_FORFEITED",
            description=(
                f"Client deposit of {deposit.currency} {deposit.amount:.2f} forfeited to freelancer {contract.developer_email} "
                f"as compensation for frivolous dispute."
            ),
            metadata_json={"forfeited_amount": deposit.amount, "dispute_id": dispute_id, "recipient": contract.developer_email},
        )
        db.add(audit)
        db.commit()
        db.refresh(deposit)
        return deposit

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
        PARTIAL SLASH ONLY (Graduated Model):
        - Slashes 50% by default (configurable per contract, 25-75% range).
        - Full slash (100%+) is NEVER permitted: raises ValueError.
        - Unslashed remainder is always returned to the freelancer.
        - Slashed funds go directly to the wronged party (client), not to the platform.
        """
        stake = self.get_stake_by_contract_id(db, contract_id)
        if not stake or stake.status != "HELD":
            return stake

        contract = stake.contract
        contract_pct = getattr(contract, "stake_slash_percentage", None)
        pct = slash_percentage if slash_percentage is not None else (contract_pct if contract_pct is not None else self.settings.DEFAULT_SLASH_PERCENTAGE)

        # Enforce no full slash
        if pct >= 100.0:
            raise ValueError(f"Full slash is never permitted; slash percentage must be less than 100% (provided: {pct:.1f}%).")

        slashed_amount = round(stake.amount * (pct / 100.0), 2)
        refund_amount = round(stake.amount - slashed_amount, 2)
        now = datetime.now(timezone.utc)

        # 1. Slashed funds go to wronged client
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

        # 2. Unslashed remainder returned to freelancer
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
                f"Partial security deposit slash of {pct:.1f}%: {stake.currency} {slashed_amount:.2f} awarded to client "
                f"{contract.client_email}; unslashed remainder {stake.currency} {refund_amount:.2f} returned to freelancer."
            ),
            metadata_json={
                "slashed_amount": slashed_amount,
                "returned_amount": refund_amount,
                "slash_percentage": pct,
                "dispute_id": dispute_id,
                "wronged_party_recipient": contract.client_email,
            },
        )
        db.add(audit)
        db.commit()
        db.refresh(stake)
        return stake


stake_service = StakeService()
