from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.schemas.negotiation import (
    NegotiationRequestSchema,
    NegotiationResponseSchema,
)
from app.services.negotiator_service import negotiator_service

router = APIRouter(prefix="/negotiate", tags=["Agent Negotiator"])


@router.post("", response_model=NegotiationResponseSchema)
async def negotiate_contract_terms(
    req: NegotiationRequestSchema,
    db: Session = Depends(get_db),
):
    """
    Autonomous Agent-to-Agent Negotiation:
    Runs multi-round strategic negotiation between Buyer Agent and Seller Agent
    over price, milestones, timeline, and FX risk strategy.
    
    Uses local Ollama (llama3.2:3b) with rule-based fallback.
    Upon consensus, automatically creates a TrustBridge escrow contract.
    """
    return await negotiator_service.run_negotiation(db, req)


@router.get("", response_model=List[NegotiationResponseSchema])
def list_negotiations():
    """List all executed agent negotiations."""
    return negotiator_service.list_negotiations()


@router.get("/{negotiation_id}", response_model=NegotiationResponseSchema)
def get_negotiation(negotiation_id: str):
    """Retrieve full transcript and agreed terms of a specific agent negotiation."""
    result = negotiator_service.get_negotiation(negotiation_id)
    if not result:
        raise HTTPException(status_code=404, detail="Negotiation session not found")
    return result
