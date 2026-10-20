import pytest
from app.models.enums import FXStrategy
from app.schemas.negotiation import NegotiationRequestSchema, NegotiateRequest
from app.services.negotiator_service import negotiator_service


@pytest.mark.asyncio
async def test_buyer_never_exceeds_hard_cap_quick_mode(db_session):
    """
    Requirement 4.1: Buyer agent NEVER exceeds hard cap even when seller ask is just above it
    (deal fails NO_DEAL instead).
    """
    # Buyer budget $2,000, seller ask $2,050 (just $50 above budget).
    # With hard cap at $2,000, seller compromise at $2,025 exceeds cap -> NO_DEAL.
    req = NegotiationRequestSchema(
        buyer_brief="Build MVP backend with FastAPI. Budget $2,000 USD, 14 days.",
        seller_brief="Senior engineer. Ask $2,050 USD, 14 days.",
        buyer_max_budget=2000.0,
        max_over_budget_pct=0.0,
        force_rule_based=True,
        auto_create_contract=False,
    )

    response = await negotiator_service.run_negotiation(db_session, req)
    assert response.status == "no_deal"
    assert response.agreed_terms is None
    assert response.termination_reason is not None
    assert "seller price" in response.termination_reason.lower()
    assert "exceeds buyer hard cap" in response.termination_reason.lower()
    assert response.bounds is not None
    assert response.bounds.buyer_max_budget == 2000.0
    assert response.bounds.effective_buyer_cap == 2000.0


@pytest.mark.asyncio
async def test_buyer_never_exceeds_hard_cap_multiround(db_session):
    """
    Buyer agent NEVER exceeds hard cap in multi-round deliberation mode.
    """
    req = NegotiationRequestSchema(
        buyer_brief="Build payment service. Budget $3,000 USD, 21 days.",
        seller_brief="Enterprise architect. Ask $3,100 USD, 28 days.",
        buyer_max_budget=3000.0,
        seller_min_price=3050.0,
        max_rounds=3,
        max_over_budget_pct=0.0,
        force_rule_based=True,
        auto_create_contract=False,
    )

    response = await negotiator_service.run_negotiation(db_session, req)
    assert response.status == "no_deal"
    assert response.agreed_terms is None
    assert response.termination_reason is not None
    assert "seller price" in response.termination_reason.lower()
    assert "exceeds buyer hard cap" in response.termination_reason.lower()


@pytest.mark.asyncio
async def test_max_over_budget_pct_stretch_permitted_and_bounded(db_session):
    """
    Requirement 4.2: With max_over_budget_pct=10, buyer can stretch to exactly cap*1.10 and no further.
    """
    # Buyer budget $2,000, max_over_budget_pct=10.0 -> effective cap = $2,200.0.
    # Seller ask $2,400 -> compromise = (2000 + 2400)/2 = $2,200.0.
    # Exactly hits cap*1.10 -> agreed!
    req_pass = NegotiationRequestSchema(
        buyer_brief="Build dashboard. Budget $2,000 USD, 14 days.",
        seller_brief="Fullstack dev. Ask $2,400 USD, 14 days.",
        buyer_max_budget=2000.0,
        max_over_budget_pct=10.0,
        force_rule_based=True,
        auto_create_contract=False,
    )

    res_pass = await negotiator_service.run_negotiation(db_session, req_pass)
    assert res_pass.status == "agreed"
    assert res_pass.agreed_terms is not None
    assert res_pass.agreed_terms.final_price == 2200.0
    assert res_pass.bounds.effective_buyer_cap == 2200.0

    # Counter-test: Seller ask $2,500 -> compromise = $2,250.0 > $2,200.0 -> fails NO_DEAL!
    req_fail = NegotiationRequestSchema(
        buyer_brief="Build dashboard. Budget $2,000 USD, 14 days.",
        seller_brief="Fullstack dev. Ask $2,500 USD, 14 days.",
        buyer_max_budget=2000.0,
        max_over_budget_pct=10.0,
        force_rule_based=True,
        auto_create_contract=False,
    )

    res_fail = await negotiator_service.run_negotiation(db_session, req_fail)
    assert res_fail.status == "no_deal"
    assert res_fail.agreed_terms is None
    assert "seller price 2250.0 exceeds buyer hard cap 2200.0" in res_fail.termination_reason


@pytest.mark.asyncio
async def test_custom_walk_away_gap_pct_triggers_no_deal(db_session):
    """
    Requirement 4.3: Custom walk_away_gap_pct=10 triggers NO_DEAL on a 15% gap
    (old default 40 would have continued).
    """
    # Buyer budget $2,000, seller ask $2,300 -> gap is (2300 - 2000)/2000 = 15.0%.
    # With walk_away_gap_pct=10.0:
    req_tight = NegotiationRequestSchema(
        buyer_brief="Microservice API. Budget $2,000 USD, 14 days.",
        seller_brief="Senior backend dev. Ask $2,300 USD, 14 days.",
        walk_away_gap_pct=10.0,
        force_rule_based=True,
        auto_create_contract=False,
    )

    res_tight = await negotiator_service.run_negotiation(db_session, req_tight)
    assert res_tight.status == "no_deal"
    assert res_tight.agreed_terms is None
    assert res_tight.termination_reason is not None
    assert "exceeds 10%" in res_tight.termination_reason.lower()

    # Contrast with default walk_away_gap_pct=40.0: gap 15% <= 40% -> succeeds
    req_default = NegotiationRequestSchema(
        buyer_brief="Microservice API. Budget $2,000 USD, 14 days.",
        seller_brief="Senior backend dev. Ask $2,300 USD, 14 days.",
        walk_away_gap_pct=40.0,
        force_rule_based=True,
        auto_create_contract=False,
    )

    res_default = await negotiator_service.run_negotiation(db_session, req_default)
    assert res_default.status == "agreed"
    assert res_default.agreed_terms is not None
    assert res_default.agreed_terms.final_price == 2150.0


@pytest.mark.asyncio
async def test_seller_floor_respected_never_below_min_price(db_session):
    """
    Requirement 4.4: Seller floor respected: agreed price never below seller_min_price.
    """
    # Buyer budget $3,000, seller ask $4,000.
    # Normal compromise midpoint is $3,500.
    # But seller sets seller_min_price = $3,750.
    req = NegotiationRequestSchema(
        buyer_brief="Data pipeline. Budget $3,000 USD, 21 days.",
        seller_brief="Data engineer. Ask $4,000 USD, 28 days.",
        buyer_max_budget=4000.0,
        seller_min_price=3750.0,
        force_rule_based=True,
        auto_create_contract=False,
    )

    response = await negotiator_service.run_negotiation(db_session, req)
    assert response.status == "agreed"
    assert response.agreed_terms is not None
    assert response.agreed_terms.final_price >= 3750.0
    assert response.agreed_terms.final_price == 3750.0
    assert response.bounds.seller_min_price == 3750.0


def test_api_negotiate_with_bounds(client):
    """
    Tests the POST /api/v1/negotiate endpoint with explicit bound parameters.
    """
    payload = {
        "buyer_brief": "Fintech payment engine. Budget $2,500 USD, 14 days.",
        "seller_brief": "Fintech architect. Ask $3,200 USD, 21 days.",
        "buyer_max_budget": 2800.0,
        "seller_min_price": 2700.0,
        "max_over_budget_pct": 5.0,
        "walk_away_gap_pct": 35.0,
        "max_rounds": 5,
        "force_rule_based": True,
        "auto_create_contract": False,
    }

    res = client.post("/api/v1/negotiate", json=payload)
    assert res.status_code == 200
    data = res.json()

    assert "bounds" in data
    bounds = data["bounds"]
    assert bounds["buyer_max_budget"] == 2800.0
    assert bounds["seller_min_price"] == 2700.0
    assert bounds["max_over_budget_pct"] == 5.0
    assert bounds["effective_buyer_cap"] == 2940.0
    assert bounds["walk_away_gap_pct"] == 35.0
    assert bounds["max_rounds"] == 5
