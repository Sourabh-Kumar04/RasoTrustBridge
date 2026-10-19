from datetime import datetime
from typing import Optional, Dict
from pydantic import BaseModel, Field
from app.models.enums import FXStrategy


class FXQuoteRequest(BaseModel):
    base_currency: str = Field(default="USD", max_length=8)
    target_currency: str = Field(default="INR", max_length=8)
    amount: float = Field(default=1.0, gt=0)


class FXQuoteResponse(BaseModel):
    base_currency: str
    target_currency: str
    rate: float
    converted_amount: float
    fetched_at: datetime
    provider: str
    cached: bool = False


class FXComparisonRequest(BaseModel):
    base_currency: str = Field(default="USD", max_length=8)
    target_currency: str = Field(default="INR", max_length=8)
    amount: float = Field(..., gt=0, description="Amount in base currency")
    locked_rate: float = Field(..., gt=0, description="Contract locked rate at signing")
    fx_strategy: FXStrategy = Field(default=FXStrategy.LOCKED_AT_SIGNING)
    spot_rate: Optional[float] = Field(None, gt=0, description="Optional manual spot rate override")


class FXComparisonResponse(BaseModel):
    base_currency: str
    target_currency: str
    amount: float
    locked_rate: float
    spot_rate: float
    fx_strategy: FXStrategy
    rate_change_percent: float
    locked_payout_amount: float
    spot_payout_amount: float
    effective_payout_amount: float
    payout_difference_target: float
    risk_bearer: str
    analysis: str
