from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.milestone import Milestone
from app.models.payment import DisputeRecord
from app.models.user import User
from app.schemas.contract import MilestoneResponseSchema
from app.schemas.milestone import (
    MilestoneSubmitRequest,
    MilestoneVerifyRequest,
    MilestoneReleaseRequest,
    MilestoneDisputeRequest,
    DisputeResponseSchema,
)
from app.services.contract_service import contract_service
from app.api.deps import get_current_user
from app.api.contracts import check_contract_access

router = APIRouter(prefix="/milestones", tags=["Milestones"])


@router.get("/{milestone_id}", response_model=MilestoneResponseSchema)
def get_milestone(
    milestone_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    milestone = db.query(Milestone).filter(Milestone.id == milestone_id).first()
    if not milestone:
        raise HTTPException(status_code=404, detail="Milestone not found")
    check_contract_access(milestone.contract, current_user)
    return milestone


@router.post("/{milestone_id}/submit", response_model=MilestoneResponseSchema)
async def submit_milestone(
    milestone_id: str,
    req: MilestoneSubmitRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Developer submits deliverable evidence (PR URL, commit SHA, test metrics).
    Triggers automated AI criteria verification. If score >= 0.80, status becomes VERIFIED.
    """
    milestone = db.query(Milestone).filter(Milestone.id == milestone_id).first()
    if not milestone:
        raise HTTPException(status_code=404, detail="Milestone not found")
    check_contract_access(milestone.contract, current_user)
    return await contract_service.submit_milestone(db, milestone_id, req)


@router.post("/{milestone_id}/verify", response_model=MilestoneResponseSchema)
async def verify_milestone(
    milestone_id: str,
    req: MilestoneVerifyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Runs or overrides verification evaluation on submitted milestone.
    """
    milestone = db.query(Milestone).filter(Milestone.id == milestone_id).first()
    if not milestone:
        raise HTTPException(status_code=404, detail="Milestone not found")
    check_contract_access(milestone.contract, current_user)
    return await contract_service.verify_milestone(
        db,
        milestone_id,
        override_score=req.override_score,
        notes=req.notes,
    )


@router.post("/{milestone_id}/release", response_model=MilestoneResponseSchema)
async def release_milestone(
    milestone_id: str,
    req: MilestoneReleaseRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Approves milestone release and triggers PayPal Payout to developer.
    Calculates final FX rate according to contract strategy (Locked vs Spot).
    """
    milestone = db.query(Milestone).filter(Milestone.id == milestone_id).first()
    if not milestone:
        raise HTTPException(status_code=404, detail="Milestone not found")
    check_contract_access(milestone.contract, current_user)
    return await contract_service.release_milestone(db, milestone_id, req)


@router.post("/{milestone_id}/dispute", response_model=DisputeResponseSchema)
async def dispute_milestone(
    milestone_id: str,
    req: MilestoneDisputeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Initiates dispute protocol. Milestone is frozen in DISPUTED state,
    and a PayPal-ready immutable evidence pack is automatically assembled.
    """
    milestone = db.query(Milestone).filter(Milestone.id == milestone_id).first()
    if not milestone:
        raise HTTPException(status_code=404, detail="Milestone not found")
    check_contract_access(milestone.contract, current_user)
    return await contract_service.dispute_milestone(db, milestone_id, req)


@router.get("/{milestone_id}/dispute", response_model=DisputeResponseSchema)
def get_milestone_dispute(
    milestone_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    milestone = db.query(Milestone).filter(Milestone.id == milestone_id).first()
    if not milestone:
        raise HTTPException(status_code=404, detail="Milestone not found")
    check_contract_access(milestone.contract, current_user)

    dispute = db.query(DisputeRecord).filter(DisputeRecord.milestone_id == milestone_id).first()
    if not dispute:
        raise HTTPException(status_code=404, detail="No dispute found for this milestone")
    return dispute
