import logging
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.payment import DisputeRecord
from app.models.enums import DisputeStatus
from app.schemas.milestone import DisputeResponseSchema
from app.api.deps import get_current_user, require_admin_user
from app.api.contracts import check_contract_access
from app.services.dispute_service import dispute_service

logger = logging.getLogger("trustbridge.disputes")

router = APIRouter(prefix="/disputes", tags=["disputes"])


class ArbiterResolveRequest(BaseModel):
    outcome: Literal["freelancer", "client"]
    arbiter_note: str = Field(..., min_length=1, description="Arbiter rationale for decision")


@router.post("/{id}/resolve", response_model=DisputeResponseSchema)
async def resolve_dispute_as_arbiter(
    id: str,
    payload: ArbiterResolveRequest,
    db: Session = Depends(get_db),
    admin_user: User = Depends(require_admin_user),
):
    """
    Neutral Arbiter Dispute Resolution (Harmer's Model).
    
    Arbiter resolves a contested milestone dispute between client and freelancer.
    Restricted to admin role.
    
    PRODUCTION NOTE:
    In production this would be handled by a decentralized peer jury (e.g. Kleros-style),
    a multi-sig escalation council, or licensed professional arbiters.
    Currently, platform administrators act as neutral arbiters.

    Resolution Flows:
    - outcome == 'freelancer':
      Arbiter rules for freelancer (client filed frivolous dispute).
      * Milestone funds released to freelancer.
      * Freelancer stake returned in full.
      * Client good-faith deposit is FORFEITED to the freelancer as compensation.
    - outcome == 'client':
      Arbiter rules for client (freelancer non-performance).
      * Milestone refunded to client.
      * Client good-faith deposit returned in full.
      * Freelancer stake is PARTIALLY SLASHED (50% default, 25-75% range),
        with slashed funds awarded to the client and remainder returned to the freelancer.
    """
    dispute = db.query(DisputeRecord).filter(DisputeRecord.id == id).first()
    if not dispute:
        raise HTTPException(status_code=404, detail="Dispute not found")

    if dispute.status != DisputeStatus.OPEN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot resolve dispute in status {dispute.status.value}. Dispute must be OPEN.",
        )

    if payload.outcome == "freelancer":
        resolution_status = DisputeStatus.RESOLVED_DEVELOPER_PAYOUT
    elif payload.outcome == "client":
        resolution_status = DisputeStatus.RESOLVED_CLIENT_REFUND
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid outcome. Must be 'freelancer' or 'client'.",
        )

    resolved_dispute = dispute_service.resolve_dispute(
        db=db,
        dispute=dispute,
        resolution_status=resolution_status,
        resolution_notes=payload.arbiter_note,
        resolved_by=f"ARBITER:{admin_user.email}",
        decided_by="arbiter",
    )
    return resolved_dispute


@router.get("/{id}", response_model=DisputeResponseSchema)
def get_dispute(
    id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Retrieves dispute record by dispute ID with contract access authorization (SEC-05)."""
    dispute = db.query(DisputeRecord).filter(DisputeRecord.id == id).first()
    if not dispute:
        raise HTTPException(status_code=404, detail="Dispute not found")
    check_contract_access(dispute.contract, current_user)
    return dispute
