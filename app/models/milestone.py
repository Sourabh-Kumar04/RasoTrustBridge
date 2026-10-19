import json
import uuid
from datetime import datetime, timezone
from typing import Any, List, Optional
from sqlalchemy import Column, String, Float, Text, Integer, DateTime, ForeignKey, Enum as SQLEnum
from sqlalchemy.orm import relationship
from app.database import Base
from app.models.enums import MilestoneStatus, VerificationType


def generate_milestone_id() -> str:
    return f"ms_{uuid.uuid4().hex[:12]}"


class Milestone(Base):
    __tablename__ = "milestones"

    id = Column(String(32), primary_key=True, default=generate_milestone_id)
    contract_id = Column(String(32), ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False)
    order_index = Column(Integer, nullable=False, default=1)
    
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=False)
    
    # Financials
    amount = Column(Float, nullable=False)  # in base_currency (e.g. USD)
    payout_amount = Column(Float, nullable=True)  # in payout_currency (e.g. INR)
    
    # Lifecycle Status
    status = Column(
        SQLEnum(MilestoneStatus),
        nullable=False,
        default=MilestoneStatus.PENDING,
    )
    
    # Acceptance & Verification Rules
    verification_type = Column(
        SQLEnum(VerificationType),
        nullable=False,
        default=VerificationType.CODE_DELIVERY,
    )
    _acceptance_criteria = Column("acceptance_criteria", Text, nullable=False, default="[]")
    
    # Submission & Evidence
    evidence_url = Column(String(512), nullable=True)
    evidence_notes = Column(Text, nullable=True)
    _evidence_payload = Column("evidence_payload", Text, nullable=True)
    
    # Verification Result
    verification_score = Column(Float, nullable=True)
    _verification_log = Column("verification_log", Text, nullable=True)
    verified_by = Column(String(64), nullable=True)
    
    # PayPal Escrow and Payout Tracking
    paypal_capture_id = Column(String(128), nullable=True)
    paypal_payout_batch_id = Column(String(128), nullable=True)
    paypal_payout_item_id = Column(String(128), nullable=True)
    
    # Lifecycle Timestamps
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    funded_at = Column(DateTime(timezone=True), nullable=True)
    submitted_at = Column(DateTime(timezone=True), nullable=True)
    verified_at = Column(DateTime(timezone=True), nullable=True)
    released_at = Column(DateTime(timezone=True), nullable=True)
    disputed_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    contract = relationship("Contract", back_populates="milestones")
    dispute = relationship("DisputeRecord", back_populates="milestone", uselist=False)

    # JSON property helpers
    @property
    def acceptance_criteria(self) -> List[Any]:
        if not self._acceptance_criteria:
            return []
        try:
            return json.loads(self._acceptance_criteria)
        except Exception:
            return []

    @acceptance_criteria.setter
    def acceptance_criteria(self, value: List[Any]):
        self._acceptance_criteria = json.dumps(value)

    @property
    def evidence_payload(self) -> Optional[dict]:
        if not self._evidence_payload:
            return None
        try:
            return json.loads(self._evidence_payload)
        except Exception:
            return None

    @evidence_payload.setter
    def evidence_payload(self, value: Optional[dict]):
        self._evidence_payload = json.dumps(value) if value is not None else None

    @property
    def verification_log(self) -> Optional[dict]:
        if not self._verification_log:
            return None
        try:
            return json.loads(self._verification_log)
        except Exception:
            return None

    @verification_log.setter
    def verification_log(self, value: Optional[dict]):
        self._verification_log = json.dumps(value) if value is not None else None
