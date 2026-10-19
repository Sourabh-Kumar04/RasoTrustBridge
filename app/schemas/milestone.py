from datetime import datetime
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field
from app.models.enums import ActorRole, DisputeStatus


class MilestoneSubmitRequest(BaseModel):
    evidence_url: str = Field(..., description="Link to PR, git repository commit, or test run report")
    evidence_notes: Optional[str] = Field(None, description="Developer notes regarding submission")
    evidence_payload: Optional[Dict[str, Any]] = Field(
        None,
        description="Structured verification parameters, e.g. {'commit_sha': 'abc', 'tests_passed': 10, 'tests_total': 10}",
    )


class MilestoneVerifyRequest(BaseModel):
    override_score: Optional[float] = Field(None, ge=0.0, le=1.0, description="Optional manual score override")
    notes: Optional[str] = Field(None, description="Verification remarks")


class MilestoneReleaseRequest(BaseModel):
    release_note: Optional[str] = Field("Milestone approved and funds released via PayPal Payouts", description="Release remarks")
    override_payout_rate: Optional[float] = Field(None, description="Optional manual FX rate override for release calculation")


class MilestoneDisputeRequest(BaseModel):
    initiator_role: ActorRole = Field(..., description="CLIENT or DEVELOPER")
    initiator_email: str
    reason: str = Field(..., max_length=255)
    description: str = Field(..., description="Detailed dispute explanation for mediation")


class DisputeResponseSchema(BaseModel):
    id: str
    contract_id: str
    milestone_id: str
    initiator_role: ActorRole
    initiator_email: str
    reason: str
    description: str
    evidence_bundle: Optional[Dict[str, Any]] = None
    status: DisputeStatus
    resolution_notes: Optional[str] = None
    created_at: datetime
    resolved_at: Optional[datetime] = None

    class Config:
        from_attributes = True
