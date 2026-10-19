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
)
from app.services.contract_service import contract_service
from app.services.ledger_service import ledger_service
from app.api.deps import get_current_user

router = APIRouter(prefix="/contracts", tags=["Contracts"])


class FundContractPayload(BaseModel):
    paypal_order_id: str
    paypal_capture_id: Optional[str] = None
    idempotency_key: Optional[str] = None


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
    # Link current user ID if not explicitly specified
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

    return await contract_service.fund_contract(
        db=db,
        contract_id=contract_id,
        paypal_order_id=payload.paypal_order_id,
        paypal_capture_id=payload.paypal_capture_id,
        idempotency_key=payload.idempotency_key,
    )


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

