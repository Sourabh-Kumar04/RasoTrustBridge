from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import or_
from pydantic import BaseModel

from app.database import get_db
from app.models.contract import Contract
from app.models.user import User
from app.schemas.contract import (
    ContractCreateSchema,
    ContractResponseSchema,
    LedgerEntryResponseSchema,
    PostStakeRequest,
)
from app.services.contract_service import contract_service
from app.services.ledger_service import ledger_service
from app.services.stake_service import stake_service
from app.api.deps import get_current_user

router = APIRouter(prefix="/contracts", tags=["Contracts"])


class FundContractPayload(BaseModel):
    paypal_order_id: str
    paypal_capture_id: Optional[str] = None
    idempotency_key: Optional[str] = None


def is_client(contract: Contract, user: User) -> bool:
    if user.role in ("admin", "system"):
        return True
    if contract.client_id and contract.client_id == user.id:
        return True
    if user.email and contract.client_email and contract.client_email.lower() == user.email.lower():
        return True
    return False


def is_freelancer(contract: Contract, user: User) -> bool:
    if user.role in ("admin", "system"):
        return True
    if contract.freelancer_id and contract.freelancer_id == user.id:
        return True
    if user.email and contract.developer_email and contract.developer_email.lower() == user.email.lower():
        return True
    return False


def check_contract_access(contract: Contract, user: User) -> None:
    """
    Enforces user isolation: users can only access their own contracts
    (as client or freelancer). Admin and system roles have full access.
    """
    if user.role in ("admin", "system"):
        return

    # Check matching user IDs
    if contract.client_id and contract.client_id == user.id:
        return
    if contract.freelancer_id and contract.freelancer_id == user.id:
        return

    # Fallback: check matching verified emails
    if user.email:
        u_email = user.email.lower()
        if contract.client_email and contract.client_email.lower() == u_email:
            return
        if contract.developer_email and contract.developer_email.lower() == u_email:
            return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Forbidden: You do not have permission to access this contract.",
    )


@router.post("", response_model=ContractResponseSchema, status_code=201)
async def create_contract(
    req: ContractCreateSchema,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Creates a new freelance escrow contract with structured milestones,
    parties, currency pairs, and FX risk strategy.
    Links the contract to the authenticated user's ID.
    """
    # Ownership verification: client_id must match current_user.id (or user must have admin role) (SEC-04)
    if current_user.role not in ("admin", "system"):
        if req.client_id and req.client_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: client_id must match authenticated user ID.",
            )
        if current_user.role == "client":
            req.client_id = current_user.id
            if req.client_email and current_user.email and req.client_email.strip().lower() != current_user.email.strip().lower():
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Forbidden: client_email must match authenticated user email.",
                )
            if not req.client_email:
                req.client_email = current_user.email
        elif current_user.role == "freelancer":
            if req.freelancer_id and req.freelancer_id != current_user.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Forbidden: freelancer_id must match authenticated user ID.",
                )
            req.freelancer_id = current_user.id
            if not req.developer_email and current_user.email:
                req.developer_email = current_user.email
    else:
        # Admin / system user can specify or default
        if current_user.role == "client" and not req.client_id:
            req.client_id = current_user.id
        elif current_user.role == "freelancer" and not req.freelancer_id:
            req.freelancer_id = current_user.id

    return await contract_service.create_contract(db, req)


@router.get("", response_model=List[ContractResponseSchema])
def list_contracts(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    List freelance contracts.
    Users only see contracts where they are client or freelancer.
    Admin/system users see all contracts.
    """
    query = db.query(Contract)
    if current_user.role not in ("admin", "system"):
        u_email = current_user.email.lower() if current_user.email else ""
        query = query.filter(
            or_(
                Contract.client_id == current_user.id,
                Contract.freelancer_id == current_user.id,
                Contract.client_email.ilike(u_email),
                Contract.developer_email.ilike(u_email),
            )
        )
    contracts = query.order_by(Contract.created_at.desc()).offset(skip).limit(limit).all()
    for c in contracts:
        contract_service._attach_trust_scores(c)
    return contracts


@router.get("/{contract_id}", response_model=ContractResponseSchema)
def get_contract(
    contract_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Retrieve contract details, milestone states, and immutable audit trail.
    Enforces authorization check.
    """
    contract = db.query(Contract).filter(Contract.id == contract_id).first()
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")

    check_contract_access(contract, current_user)
    contract_service._attach_trust_scores(contract)
    return contract


@router.post("/{contract_id}/fund", response_model=ContractResponseSchema)
async def fund_contract(
    contract_id: str,
    payload: FundContractPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Funds the contract escrow using an authorized or captured PayPal Order ID.
    Transitions contract to ACTIVE and milestones to FUNDED.
    """
    contract = db.query(Contract).filter(Contract.id == contract_id).first()
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")

    check_contract_access(contract, current_user)
    if not is_client(contract, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Only the client can fund the escrow.",
        )

    return await contract_service.fund_contract(
        db=db,
        contract_id=contract_id,
        paypal_order_id=payload.paypal_order_id,
        paypal_capture_id=payload.paypal_capture_id,
        idempotency_key=payload.idempotency_key,
    )


@router.post("/{contract_id}/stake", response_model=ContractResponseSchema)
async def post_contract_stake(
    contract_id: str,
    payload: PostStakeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Freelancer posts security deposit stake when accepting a contract.
    Enforces new-account friction rules (10% stake or >= 1 completed deal for contracts >= $1,000).
    """
    contract = db.query(Contract).filter(Contract.id == contract_id).first()
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")

    check_contract_access(contract, current_user)
    if not is_freelancer(contract, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Only the assigned developer/freelancer can post the security deposit stake.",
        )

    stake_service.post_stake(
        db=db,
        contract=contract,
        freelancer=current_user,
        paypal_order_id=payload.paypal_order_id,
        paypal_capture_id=payload.paypal_capture_id,
        offered_stake_percentage=payload.stake_percentage,
        offered_stake_amount=payload.stake_amount,
        idempotency_key=payload.idempotency_key,
    )
    contract_service._attach_trust_scores(contract)
    db.refresh(contract)
    return contract


@router.get("/{contract_id}/ledger", response_model=List[LedgerEntryResponseSchema])
def get_contract_ledger(
    contract_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Retrieves the immutable, append-only event-sourced financial ledger entries for this contract.
    """
    contract = db.query(Contract).filter(Contract.id == contract_id).first()
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")

    check_contract_access(contract, current_user)
    return ledger_service.get_entries(db, contract_id=contract_id)

