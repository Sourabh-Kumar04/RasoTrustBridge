import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Float, Text, DateTime, Enum as SQLEnum
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
