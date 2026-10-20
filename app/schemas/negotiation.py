from datetime import datetime
from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field, EmailStr
from app.models.enums import FXStrategy
from app.schemas.contract import MilestoneCreateSchema, ContractResponseSchema


class AgentOfferSchema(BaseModel):
    price: float = Field(..., ge=0, description="Proposed total price in base currency")
    timeline_days: int = Field(..., ge=0, description="Proposed completion timeline in days")
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


class NegotiationBoundsSchema(BaseModel):
    buyer_max_budget: float = Field(..., description="Buyer hard cap")
    seller_min_price: float = Field(..., description="Seller hard floor")
    max_over_budget_pct: float = Field(default=0.0, description="Max permitted stretch above budget (%)")
    effective_buyer_cap: float = Field(..., description="Calculated hard ceiling: buyer_max_budget * (1 + max_over_budget_pct/100)")
    walk_away_gap_pct: float = Field(default=40.0, description="Tolerance gap threshold for NO_DEAL (%)")
    max_rounds: int = Field(default=5, description="Max negotiation rounds")


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
    buyer_max_budget: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="Buyer hard cap. The buyer agent may NEVER agree to a price above this.",
    )
    seller_min_price: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="Seller hard floor. The seller agent may NEVER agree below this.",
    )
    max_over_budget_pct: float = Field(
        default=0.0,
        ge=0.0,
        description="The only permitted stretch above budget (%), default 0.0 (zero stretch).",
    )
    walk_away_gap_pct: float = Field(
        default=40.0,
        ge=0.0,
        description="User-set gap threshold (%) for NO_DEAL (default 40.0).",
    )
    max_rounds: int = Field(
        default=5,
        ge=1,
        le=10,
        description="User-set round limit before convergence or walk-away (default: 5).",
    )
    quick_mode: bool = Field(default=True, description="Quick mode: term suggestion + one-round adjustment (default: True)")
    client_trust_score: Optional[float] = Field(default=None, description="Buyer trust score (0-100)")
    developer_trust_score: Optional[float] = Field(default=None, description="Seller trust score (0-100)")
    auto_create_contract: bool = Field(default=True, description="Automatically create TrustBridge escrow contract upon agreement")
    force_rule_based: bool = Field(default=False, description="Bypass local Ollama and use deterministic negotiation engine")


# Alias for explicit task naming compatibility
NegotiateRequest = NegotiationRequestSchema


class NegotiationResponseSchema(BaseModel):
    id: str
    status: str = Field(..., description="'agreed', 'no_deal', 'in_progress', or 'failed'")
    total_rounds: int
    transcript: List[NegotiationMessageSchema]
    rounds: List[NegotiationRoundSchema]
    agreed_terms: Optional[AgreedTermsSchema] = None
    termination_reason: Optional[str] = None
    bounds: Optional[NegotiationBoundsSchema] = Field(default=None, description="Active negotiation bounds enforced on agents")
    contract_id: Optional[str] = None
    contract: Optional[ContractResponseSchema] = None
    model_used: str = Field(..., description="Engine used: 'llama3.2:3b', 'gpt-4o', or 'rule_based_fallback'")
    created_at: datetime = Field(default_factory=datetime.utcnow)


NegotiateResponse = NegotiationResponseSchema
