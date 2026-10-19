import pytest
from app.models.enums import FXStrategy
from app.services.fx_service import fx_service


@pytest.mark.asyncio
async def test_get_rate_usd_inr():
    rate = await fx_service.get_rate("USD", "INR")
    assert rate > 50.0  # Real rate is ~84.50


@pytest.mark.asyncio
async def test_fx_strategy_locked_at_signing():
    res = await fx_service.compare_locked_vs_spot(
        base_currency="USD",
        target_currency="INR",
        amount=1000.0,
        locked_rate=84.00,
        fx_strategy=FXStrategy.LOCKED_AT_SIGNING,
        spot_rate=86.00,  # Rupee depreciated to 86
    )

    # Freelancer receives guaranteed locked rate
    assert res.effective_payout_amount == 84000.0
    assert res.locked_payout_amount == 84000.0
    assert res.spot_payout_amount == 86000.0
    assert "guaranteed" in res.analysis.lower()


@pytest.mark.asyncio
async def test_fx_strategy_spot_at_release():
    res = await fx_service.compare_locked_vs_spot(
        base_currency="USD",
        target_currency="INR",
        amount=1000.0,
        locked_rate=84.00,
        fx_strategy=FXStrategy.SPOT_AT_RELEASE,
        spot_rate=86.00,
    )

    # Freelancer receives current spot rate
    assert res.effective_payout_amount == 86000.0
    assert "spot payout" in res.analysis.lower()


@pytest.mark.asyncio
async def test_fx_strategy_split_risk_within_collar():
    # 84.00 to 85.00 is ~1.19% change (within +/-2% collar)
    res = await fx_service.compare_locked_vs_spot(
        base_currency="USD",
        target_currency="INR",
        amount=1000.0,
        locked_rate=84.00,
        fx_strategy=FXStrategy.SPLIT_RISK,
        spot_rate=85.00,
    )

    # Within collar, locked rate applies
    assert res.effective_payout_amount == 84000.0
    assert "within the ±2.0% collar band" in res.analysis


@pytest.mark.asyncio
async def test_fx_strategy_split_risk_outside_collar():
    # 84.00 to 90.00 is +7.14% change (exceeds +/-2% collar)
    # Delta = +6.00. 50/50 split -> effective rate = 84.00 + 3.00 = 87.00
    res = await fx_service.compare_locked_vs_spot(
        base_currency="USD",
        target_currency="INR",
        amount=1000.0,
        locked_rate=84.00,
        fx_strategy=FXStrategy.SPLIT_RISK,
        spot_rate=90.00,
    )

    assert res.effective_payout_amount == 87000.0
    assert "exceeds the 2% collar band" in res.analysis
    assert "50/50" in res.risk_bearer
