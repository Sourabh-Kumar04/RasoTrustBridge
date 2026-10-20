from datetime import datetime
from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field, EmailStr
from app.models.enums import ContractStatus, MilestoneStatus, FXStrategy, VerificationType, ActorRole


class AcceptanceCriterionSchema(BaseModel):
    id: Optional[str] = None
    description: str = Field(..., description="Actionable, testable deliverable condition")
    verification_method: str = Field(
        default="automated_check",
        description="Method to verify: automated_test, github_pr, manual_review, api_response",
    )
    required: bool = True


class MilestoneCreateSchema(BaseModel):
    title: str = Field(..., max_length=255)
    description: str
    amount: float = Field(..., gt=0, description="Amount in contract base currency (e.g. USD)")
    order_index: int = Field(default=1, ge=1)
    verification_type: VerificationType = VerificationType.CODE_DELIVERY
    acceptance_criteria: List[AcceptanceCriterionSchema] = Field(default_factory=list)


class MilestoneResponseSchema(BaseModel):
    id: str
    contract_id: str
    order_index: int
    title: str
    description: str
    amount: float
    payout_amount: Optional[float] = None
    status: MilestoneStatus
    verification_type: VerificationType
    acceptance_criteria: List[Dict[str, Any]] = Field(default_factory=list)
    evidence_url: Optional[str] = None
    evidence_notes: Optional[str] = None
    evidence_payload: Optional[Dict[str, Any]] = None
    verification_score: Optional[float] = None
    verification_log: Optional[Dict[str, Any]] = None
    verified_by: Optional[str] = None
    paypal_capture_id: Optional[str] = None
    paypal_payout_batch_id: Optional[str] = None
    paypal_payout_item_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    created_at: datetime
    funded_at: Optional[datetime] = None
    submitted_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    verified_at: Optional[datetime] = None
    released_at: Optional[datetime] = None
    disputed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ContractCreateSchema(BaseModel):
    title: str = Field(..., max_length=255)
    brief: str = Field(..., description="Plain-English project scope and requirements")
    client_name: str = Field(..., max_length=128)
    client_email: EmailStr
    client_country: str = Field(default="US", max_length=8)
    client_id: Optional[str] = None
    developer_name: str = Field(..., max_length=128)
    developer_email: EmailStr
    developer_country: str = Field(default="IN", max_length=8)
    freelancer_id: Optional[str] = None
    base_currency: str = Field(default="USD", max_length=8)
    payout_currency: str = Field(default="INR", max_length=8)
    total_amount: float = Field(..., gt=0)
    fx_strategy: FXStrategy = FXStrategy.LOCKED_AT_SIGNING
    locked_fx_rate: Optional[float] = None
    buyer_timeout_days: int = Field(default=7, ge=1, le=90, description="Auto-release timeout in days if buyer does not review submitted deliverable")
    milestones: List[MilestoneCreateSchema] = Field(default_factory=list)


class AuditLogSchema(BaseModel):
    id: str
    actor_role: ActorRole
    actor_identifier: str
    action: str
    description: str
    metadata_json: Optional[Dict[str, Any]] = None
    timestamp: datetime

    class Config:
        from_attributes = True


class LedgerEntryResponseSchema(BaseModel):
    id: str
    contract_id: str
    milestone_id: Optional[str] = None
    entry_type: str
    amount: float
    currency: str
    paypal_reference: Optional[str] = None
    idempotency_key: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class StakeResponseSchema(BaseModel):
    id: str
    contract_id: str
    freelancer_id: Optional[str] = None
    amount: float
    currency: str
    stake_percentage: float
    status: str
    paypal_order_id: Optional[str] = None
    paypal_capture_id: Optional[str] = None
    slashed_amount: float = 0.0
    returned_amount: float = 0.0
    created_at: datetime
    settled_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class PostStakeRequest(BaseModel):
    paypal_order_id: Optional[str] = None
    paypal_capture_id: Optional[str] = None
    stake_percentage: Optional[float] = None
    stake_amount: Optional[float] = None
    idempotency_key: Optional[str] = None


class ContractResponseSchema(BaseModel):
    id: str
    title: str
    brief: str
    client_id: Optional[str] = None
    freelancer_id: Optional[str] = None
    client_name: str
    client_email: str
    client_country: str
    developer_name: str
    developer_email: str
    developer_country: str
    base_currency: str
    payout_currency: str
    total_amount: float
    fx_strategy: FXStrategy
    locked_fx_rate: Optional[float] = None
    status: ContractStatus
    paypal_order_id: Optional[str] = None
    paypal_capture_id: Optional[str] = None
    buyer_timeout_days: int = 7
    client_trust_score: Optional[float] = None
    freelancer_trust_score: Optional[float] = None
    created_at: datetime
    updated_at: datetime
    funded_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    stake: Optional[StakeResponseSchema] = None
    milestones: List[MilestoneResponseSchema] = Field(default_factory=list)
    audit_logs: List[AuditLogSchema] = Field(default_factory=list)

    class Config:
        from_attributes = True
