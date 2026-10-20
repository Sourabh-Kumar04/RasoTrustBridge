import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from sqlalchemy import Column, String, Float, Text, DateTime, ForeignKey, Enum as SQLEnum
from sqlalchemy.orm import relationship
from app.database import Base
from app.models.enums import ActorRole, DisputeStatus


def generate_audit_id() -> str:
    return f"aud_{uuid.uuid4().hex[:12]}"


def generate_dispute_id() -> str:
    return f"disp_{uuid.uuid4().hex[:12]}"


def generate_hold_id() -> str:
    return f"hld_{uuid.uuid4().hex[:12]}"


def generate_stake_id() -> str:
    return f"stk_{uuid.uuid4().hex[:12]}"


def generate_client_deposit_id() -> str:
    return f"cld_{uuid.uuid4().hex[:12]}"


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(String(32), primary_key=True, default=generate_audit_id)
    contract_id = Column(String(32), ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False)
    milestone_id = Column(String(32), nullable=True)
    
    actor_role = Column(SQLEnum(ActorRole), nullable=False)
    actor_identifier = Column(String(255), nullable=False)
    action = Column(String(64), nullable=False)
    description = Column(Text, nullable=False)
    
    _metadata = Column("metadata_json", Text, nullable=True)
    timestamp = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    contract = relationship("Contract", back_populates="audit_logs")

    @property
    def metadata_json(self) -> Optional[dict]:
        if not self._metadata:
            return None
        try:
            return json.loads(self._metadata)
        except Exception:
            return None

    @metadata_json.setter
    def metadata_json(self, value: Optional[dict]):
        self._metadata = json.dumps(value) if value is not None else None


class DisputeRecord(Base):
    __tablename__ = "disputes"

    id = Column(String(32), primary_key=True, default=generate_dispute_id)
    contract_id = Column(String(32), ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False)
    milestone_id = Column(String(32), ForeignKey("milestones.id", ondelete="CASCADE"), nullable=False, unique=True)
    
    initiator_role = Column(SQLEnum(ActorRole), nullable=False)
    initiator_email = Column(String(255), nullable=False)
    reason = Column(String(255), nullable=False)
    description = Column(Text, nullable=False)
    
    # Packaged dispute reduction evidence bundle
    _evidence_bundle = Column("evidence_bundle", Text, nullable=True)
    status = Column(SQLEnum(DisputeStatus), nullable=False, default=DisputeStatus.OPEN)
    decided_by = Column(String(32), nullable=True, default="arbiter")  # 'client_accept', 'arbiter', 'auto_timeout'
    resolution_notes = Column(Text, nullable=True)
    
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    contract = relationship("Contract", back_populates="disputes")
    milestone = relationship("Milestone", back_populates="dispute")

    @property
    def evidence_bundle(self) -> Optional[dict]:
        if not self._evidence_bundle:
            return None
        try:
            return json.loads(self._evidence_bundle)
        except Exception:
            return None

    @evidence_bundle.setter
    def evidence_bundle(self, value: Optional[dict]):
        self._evidence_bundle = json.dumps(value) if value is not None else None


class EscrowHold(Base):
    __tablename__ = "escrow_holds"

    id = Column(String(32), primary_key=True, default=generate_hold_id)
    contract_id = Column(String(32), ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False)
    milestone_id = Column(String(32), nullable=True)
    
    amount = Column(Float, nullable=False)
    currency = Column(String(8), nullable=False, default="USD")
    
    paypal_order_id = Column(String(128), nullable=False)
    paypal_capture_id = Column(String(128), nullable=True)
    paypal_status = Column(String(64), nullable=False)  # CREATED, APPROVED, COMPLETED
    idempotency_key = Column(String(128), unique=True, index=True, nullable=True)
    
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    captured_at = Column(DateTime(timezone=True), nullable=True)


class Stake(Base):
    """
    Security Deposit / Stake Mechanism for Reputation with Economic Skin.
    Held via PayPal alongside client escrow; returned on successful delivery or dispute victory,
    slashed on lost dispute. Imposes economic friction against Sybil / scam reruns.
    """
    __tablename__ = "stakes"

    id = Column(String(32), primary_key=True, default=generate_stake_id)
    contract_id = Column(String(32), ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    freelancer_id = Column(String(32), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    amount = Column(Float, nullable=False)
    currency = Column(String(8), nullable=False, default="USD")
    stake_percentage = Column(Float, nullable=False, default=5.0)
    party = Column(String(32), nullable=False, default="freelancer")
    status = Column(String(32), nullable=False, default="HELD")  # HELD, RETURNED, SLASHED

    paypal_order_id = Column(String(128), nullable=True)
    paypal_capture_id = Column(String(128), nullable=True)

    slashed_amount = Column(Float, nullable=False, default=0.0)
    returned_amount = Column(Float, nullable=False, default=0.0)

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    settled_at = Column(DateTime(timezone=True), nullable=True)

    contract = relationship("Contract", back_populates="stake")
    freelancer = relationship("User", foreign_keys=[freelancer_id])


class ClientDeposit(Base):
    """
    Symmetric Client Good-Faith Deposit (Harmer's Graduated Model).
    Client posts a matching good-faith deposit: 50% of the freelancer's stake amount.
    - Returned in full when the contract completes normally.
    - Returned in full if client opens a dispute and WINS (arbiter rules for client).
    - Forfeited to freelancer as compensation if client opens a frivolous dispute and LOSES.
    """
    __tablename__ = "client_deposits"

    id = Column(String(32), primary_key=True, default=generate_client_deposit_id)
    contract_id = Column(String(32), ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    client_id = Column(String(32), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    amount = Column(Float, nullable=False)
    currency = Column(String(8), nullable=False, default="USD")
    party = Column(String(32), nullable=False, default="client")
    status = Column(String(32), nullable=False, default="HELD")  # HELD, RETURNED, FORFEITED

    paypal_order_id = Column(String(128), nullable=True)
    paypal_capture_id = Column(String(128), nullable=True)

    forfeited_amount = Column(Float, nullable=False, default=0.0)
    returned_amount = Column(Float, nullable=False, default=0.0)

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    settled_at = Column(DateTime(timezone=True), nullable=True)

    contract = relationship("Contract", back_populates="client_deposit")
    client = relationship("User", foreign_keys=[client_id])
