from fastapi import APIRouter, Query
from app.schemas.fx import (
    FXQuoteResponse,
    FXComparisonRequest,
    FXComparisonResponse,
)
from app.services.fx_service import fx_service

router = APIRouter(prefix="/fx", tags=["FX Risk Engine"])


@router.get("/quote", response_model=FXQuoteResponse)
async def get_fx_quote(
    base: str = Query("USD", max_length=8),
    target: str = Query("INR", max_length=8),
    amount: float = Query(1.0, gt=0),
):
    """
    Fetches real-time or cached exchange rate quote between international currency pairs.
    """
    return await fx_service.get_quote(
        base_currency=base,
        target_currency=target,
        amount=amount,
    )


@router.post("/compare", response_model=FXComparisonResponse)
async def compare_fx_strategies(
    req: FXComparisonRequest,
):
    """
    Simulates and compares financial payouts under the 3 FX risk strategies:
    1. LOCKED_AT_SIGNING (guaranteed rate to freelancer; client absorbs delta)
    2. SPOT_AT_RELEASE (market rate at milestone completion; freelancer absorbs delta)
    3. SPLIT_RISK (±2% collar band; variance outside band is split 50/50)
    """
    return await fx_service.compare_locked_vs_spot(
        base_currency=req.base_currency,
        target_currency=req.target_currency,
        amount=req.amount,
        locked_rate=req.locked_rate,
        fx_strategy=req.fx_strategy,
        spot_rate=req.spot_rate,
    )
