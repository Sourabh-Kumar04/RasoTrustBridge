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
from typing import List
from app.services.contract_service import contract_service
from app.api.deps import get_current_user
from app.api.contracts import check_contract_access, is_client, is_freelancer
from app.models.enums import ActorRole

router = APIRouter(prefix="/milestones", tags=["Milestones"])


@router.post("/check-expiry", response_model=List[MilestoneResponseSchema])
async def check_milestones_expiry(
    db: Session = Depends(get_db),
):
    """
    Background worker / maintenance endpoint:
    Scans submitted milestones past their buyer timeout expiry date with no buyer action,
    auto-transitioning them to VERIFIED status with audit log 'auto-released: buyer timeout'.
    """
    return await contract_service.check_and_auto_release_expired_milestones(db)



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
    if not is_freelancer(milestone.contract, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Only the assigned developer/freelancer can submit milestone deliverables.",
        )
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
    if req.override_score is not None and not is_client(milestone.contract, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Only the client can provide a manual verification override score.",
        )
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
    if not is_client(milestone.contract, current_user):
        # SEC-07: Allow freelancer to execute release if client timed out and milestone was auto-verified
        if not (milestone.verified_by == "BUYER_TIMEOUT_AUTO_RELEASE" and is_freelancer(milestone.contract, current_user)):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: Only the client can release milestone funds.",
            )
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
    Derives dispute initiator role and email exclusively from authenticated current_user (SEC-03).
    """
    milestone = db.query(Milestone).filter(Milestone.id == milestone_id).first()
    if not milestone:
        raise HTTPException(status_code=404, detail="Milestone not found")
    contract = milestone.contract
    check_contract_access(contract, current_user)

    user_is_client = is_client(contract, current_user)
    user_is_freelancer = is_freelancer(contract, current_user)

    if current_user.role in ("admin", "system"):
        actual_role = req.initiator_role or ActorRole.CLIENT
        actual_email = req.initiator_email or current_user.email
    elif user_is_client and not user_is_freelancer:
        actual_role = ActorRole.CLIENT
        actual_email = current_user.email
        if req.initiator_role and req.initiator_role != ActorRole.CLIENT:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: Cannot initiate dispute as freelancer when authenticated as client.",
            )
    elif user_is_freelancer and not user_is_client:
        actual_role = ActorRole.DEVELOPER
        actual_email = current_user.email
        if req.initiator_role and req.initiator_role != ActorRole.DEVELOPER:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: Cannot initiate dispute as client when authenticated as freelancer.",
            )
    else:
        actual_role = ActorRole.DEVELOPER if current_user.role == "freelancer" else ActorRole.CLIENT
        actual_email = current_user.email

    if current_user.role not in ("admin", "system") and req.initiator_email:
        if current_user.email and req.initiator_email.strip().lower() != current_user.email.strip().lower():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: Cannot spoof dispute initiator email.",
            )

    req.initiator_role = actual_role
    req.initiator_email = actual_email

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
