from datetime import datetime
from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field, EmailStr
from app.models.enums import FXStrategy
from app.schemas.contract import MilestoneCreateSchema, ContractResponseSchema


class AgentOfferSchema(BaseModel):
    price: float = Field(..., gt=0, description="Proposed total price in base currency")
    timeline_days: int = Field(..., gt=0, description="Proposed completion timeline in days")
    fx_strategy: FXStrategy = Field(default=FXStrategy.LOCKED_AT_SIGNING)
    milestones: List[Dict[str, Any]] = Field(default_factory=list, description="Structured milestone proposals")
    summary: str = Field(..., description="High-level summary of the offer")


class NegotiationMessageSchema(BaseModel):
    round_number: int
    sender: str = Field(..., description="'buyer_agent' or 'seller_agent'")
    sender_label: str = Field(..., description="Human-readable agent title")
    reasoning: str = Field(..., description="Internal chain-of-thought strategy")
    message: str = Field(..., description="Conversational dialogue sent to the counterparty agent")
    offer: AgentOfferSchema
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class NegotiationRoundSchema(BaseModel):
    round_number: int
    buyer_turn: NegotiationMessageSchema
    seller_turn: NegotiationMessageSchema
    price_gap: float
    status: str = Field(default="negotiating", description="'negotiating', 'concession_made', 'agreed'")


class AgreedTermsSchema(BaseModel):
    title: str
    final_price: float
    timeline_days: int
    fx_strategy: FXStrategy
    base_currency: str = "USD"
    payout_currency: str = "INR"
    milestones: List[MilestoneCreateSchema]
    concession_summary: str


class NegotiationRequestSchema(BaseModel):
    buyer_brief: str = Field(
        ...,
        description="Buyer project requirements, scope, and initial budget expectation",
        examples=["Need high-performance payment API built with FastAPI and PostgreSQL. Budget $3,000 USD."],
    )
    seller_brief: str = Field(
        ...,
        description="Seller capabilities, deliverables, and ask price",
        examples=["Can build high-performance payment API with complete test suite and PayPal integration. Rate $4,000 USD."],
    )
    client_name: str = Field(default="Sarah Jenkins", max_length=128)
    client_email: EmailStr = Field(default="sarah@acme.com")
    client_country: str = Field(default="US", max_length=8)
    developer_name: str = Field(default="Arjun Patel", max_length=128)
    developer_email: EmailStr = Field(default="arjun@devstudio.in")
    developer_country: str = Field(default="IN", max_length=8)
    base_currency: str = Field(default="USD", max_length=8)
    payout_currency: str = Field(default="INR", max_length=8)
    max_rounds: int = Field(default=3, ge=1, le=5, description="Maximum negotiation rounds before convergence")
    quick_mode: bool = Field(default=True, description="Quick mode: term suggestion + one-round adjustment (default: True)")
    client_trust_score: Optional[float] = Field(default=None, description="Buyer trust score (0-100)")
    developer_trust_score: Optional[float] = Field(default=None, description="Seller trust score (0-100)")
    auto_create_contract: bool = Field(default=True, description="Automatically create TrustBridge escrow contract upon agreement")
    force_rule_based: bool = Field(default=False, description="Bypass local Ollama and use deterministic negotiation engine")


class NegotiationResponseSchema(BaseModel):
    id: str
    status: str = Field(..., description="'agreed', 'no_deal', 'in_progress', or 'failed'")
    total_rounds: int
    transcript: List[NegotiationMessageSchema]
    rounds: List[NegotiationRoundSchema]
    agreed_terms: Optional[AgreedTermsSchema] = None
    termination_reason: Optional[str] = None
    contract_id: Optional[str] = None
    contract: Optional[ContractResponseSchema] = None
    model_used: str = Field(..., description="Engine used: 'llama3.2:3b', 'gpt-4o', or 'rule_based_fallback'")
    created_at: datetime = Field(default_factory=datetime.utcnow)
