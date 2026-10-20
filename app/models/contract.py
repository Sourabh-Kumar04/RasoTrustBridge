import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Float, Text, DateTime, Integer, ForeignKey, Enum as SQLEnum
from sqlalchemy.orm import relationship
from app.database import Base
from app.models.enums import ContractStatus, FXStrategy


def generate_contract_id() -> str:
    return f"ct_{uuid.uuid4().hex[:12]}"


class Contract(Base):
    __tablename__ = "contracts"

    id = Column(String(32), primary_key=True, default=generate_contract_id)
    title = Column(String(255), nullable=False)
    brief = Column(Text, nullable=False)
    
    # Linked User Accounts
    client_id = Column(String(32), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    freelancer_id = Column(String(32), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    # Parties
    client_name = Column(String(128), nullable=False)
    client_email = Column(String(255), nullable=False)
    client_country = Column(String(8), nullable=False, default="US")
    
    developer_name = Column(String(128), nullable=False)
    developer_email = Column(String(255), nullable=False)  # PayPal receiver address
    developer_country = Column(String(8), nullable=False, default="IN")
    
    # Currency and Financials
    base_currency = Column(String(8), nullable=False, default="USD")
    payout_currency = Column(String(8), nullable=False, default="INR")
    total_amount = Column(Float, nullable=False)
    
    # FX Risk Strategy
    fx_strategy = Column(
        SQLEnum(FXStrategy),
        nullable=False,
        default=FXStrategy.LOCKED_AT_SIGNING,
    )
    locked_fx_rate = Column(Float, nullable=True)  # Snapshot rate at contract signing/funding
    
    # State
    status = Column(
        SQLEnum(ContractStatus),
        nullable=False,
        default=ContractStatus.DRAFT,
    )
    
    # PayPal Escrow References
    paypal_order_id = Column(String(128), nullable=True)
    paypal_capture_id = Column(String(128), nullable=True)
    buyer_timeout_days = Column(Integer, nullable=False, default=7)
    stake_slash_percentage = Column(Float, nullable=False, default=50.0)
    
    # Timestamps
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    funded_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    client_user = relationship(
        "User",
        foreign_keys=[client_id],
        back_populates="client_contracts",
    )
    freelancer_user = relationship(
        "User",
        foreign_keys=[freelancer_id],
        back_populates="freelancer_contracts",
    )
    milestones = relationship(
        "Milestone",
        back_populates="contract",
        cascade="all, delete-orphan",
        order_by="Milestone.order_index",
    )
    audit_logs = relationship(
        "AuditLog",
        back_populates="contract",
        cascade="all, delete-orphan",
        order_by="AuditLog.timestamp",
    )
    disputes = relationship(
        "DisputeRecord",
        back_populates="contract",
        cascade="all, delete-orphan",
    )
    ledger_entries = relationship(
        "LedgerEntry",
        back_populates="contract",
        cascade="save-update, merge",
        order_by="LedgerEntry.created_at",
    )
    stake = relationship(
        "Stake",
        back_populates="contract",
        uselist=False,
        cascade="all, delete-orphan",
    )
    client_deposit = relationship(
        "ClientDeposit",
        back_populates="contract",
        uselist=False,
        cascade="all, delete-orphan",
    )
