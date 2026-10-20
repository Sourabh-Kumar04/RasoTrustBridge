import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Float, DateTime, ForeignKey, Enum as SQLEnum, event
from sqlalchemy.orm import relationship
from app.database import Base
from app.models.enums import LedgerEntryType


def generate_ledger_id() -> str:
    return f"ledg_{uuid.uuid4().hex[:12]}"


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"

    id = Column(String(32), primary_key=True, default=generate_ledger_id)
    contract_id = Column(String(32), ForeignKey("contracts.id", ondelete="RESTRICT"), nullable=False, index=True)
    milestone_id = Column(String(32), nullable=True, index=True)
    entry_type = Column(SQLEnum(LedgerEntryType), nullable=False, index=True)
    amount = Column(Float, nullable=False)
    currency = Column(String(8), nullable=False, default="USD")
    paypal_reference = Column(String(128), nullable=True, index=True)
    idempotency_key = Column(String(128), nullable=True, index=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    contract = relationship("Contract", back_populates="ledger_entries")


# Enforce append-only semantics on LedgerEntry
@event.listens_for(LedgerEntry, "before_update")
def receive_before_update(mapper, connection, target):
    raise ValueError("LedgerEntry is immutable: updates are prohibited on event-sourced financial records.")


@event.listens_for(LedgerEntry, "before_delete")
def receive_before_delete(mapper, connection, target):
    raise ValueError("LedgerEntry is immutable: deletes are prohibited on event-sourced financial records.")
