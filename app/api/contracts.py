from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.contract import Contract
from app.schemas.contract import (
    ContractCreateSchema,
    ContractResponseSchema,
)
from app.services.contract_service import contract_service
from pydantic import BaseModel

router = APIRouter(prefix="/contracts", tags=["Contracts"])


class FundContractPayload(BaseModel):
    paypal_order_id: str
    paypal_capture_id: Optional[str] = None


@router.post("", response_model=ContractResponseSchema, status_code=201)
async def create_contract(
    req: ContractCreateSchema,
    db: Session = Depends(get_db),
):
    """
    Creates a new freelance escrow contract with structured milestones,
    parties, currency pairs, and FX risk strategy.
    """
    return await contract_service.create_contract(db, req)


@router.get("", response_model=List[ContractResponseSchema])
def list_contracts(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List all freelance contracts ordered by creation date."""
    return db.query(Contract).order_by(Contract.created_at.desc()).offset(skip).limit(limit).all()


@router.get("/{contract_id}", response_model=ContractResponseSchema)
def get_contract(
    contract_id: str,
    db: Session = Depends(get_db),
):
    """Retrieve contract details, milestone states, and immutable audit trail."""
    contract = db.query(Contract).filter(Contract.id == contract_id).first()
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    return contract


@router.post("/{contract_id}/fund", response_model=ContractResponseSchema)
async def fund_contract(
    contract_id: str,
    payload: FundContractPayload,
    db: Session = Depends(get_db),
):
    """
    Funds the contract escrow using an authorized or captured PayPal Order ID.
    Transitions contract to ACTIVE and milestones to FUNDED.
    """
    return await contract_service.fund_contract(
        db=db,
        contract_id=contract_id,
        paypal_order_id=payload.paypal_order_id,
        paypal_capture_id=payload.paypal_capture_id,
    )
