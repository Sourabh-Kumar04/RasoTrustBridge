from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from app.models.enums import FXStrategy, VerificationType


class AIBriefRequestSchema(BaseModel):
    brief: str = Field(..., min_length=10, description="Plain-English project brief or requirements")
    client_name: Optional[str] = Field("Acme Global Client", description="Client name")
    client_email: Optional[str] = Field("client@acme.corp", description="Client PayPal account email")
    client_country: Optional[str] = Field("US", description="Client country ISO code")
    developer_name: Optional[str] = Field("Bangalore Dev Studio", description="Developer name")
    developer_email: Optional[str] = Field("developer@bangalore.io", description="Developer PayPal payout email")
    developer_country: Optional[str] = Field("IN", description="Developer country ISO code")
    budget: Optional[float] = Field(None, gt=0, description="Optional target budget in base currency")
    base_currency: str = Field("USD", max_length=8)
    payout_currency: str = Field("INR", max_length=8)
    fx_strategy: Optional[FXStrategy] = Field(None, description="Optional forced FX strategy")


class AIMilestoneSuggestionSchema(BaseModel):
    order_index: int
    title: str
    description: str
    amount_percentage: float = Field(..., description="Percentage of total contract (e.g. 0.30 for 30%)")
    estimated_amount: float = Field(..., description="Estimated amount in base currency")
    verification_type: VerificationType
    acceptance_criteria: List[Dict[str, Any]] = Field(default_factory=list)


class AIContractResponseSchema(BaseModel):
    title: str
    summary: str
    suggested_total_amount: float
    base_currency: str
    payout_currency: str
    recommended_fx_strategy: FXStrategy
    fx_strategy_rationale: str
    milestones: List[AIMilestoneSuggestionSchema]
    dispute_protocol_summary: str
    generator_source: str
