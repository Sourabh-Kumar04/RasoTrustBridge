import pytest
from app.models.enums import FXStrategy
from app.schemas.ai import AIBriefRequestSchema
from app.services.ai_contract_builder import ai_contract_builder


@pytest.mark.asyncio
async def test_ai_builder_rule_based_fallback():
    brief_text = (
        "Build a high-performance REST API with FastAPI, JWT authentication, "
        "and PostgreSQL database for an e-commerce platform. Total budget is $1,500 USD."
    )
    req = AIBriefRequestSchema(
        brief=brief_text,
        client_name="Sarah Jenkins",
        client_country="US",
        developer_name="Arjun Patel",
        developer_country="IN",
        base_currency="USD",
        payout_currency="INR",
    )

    contract = await ai_contract_builder.generate_contract(req)

    assert contract.suggested_total_amount == 1500.0
    assert contract.base_currency == "USD"
    assert contract.payout_currency == "INR"
    assert len(contract.milestones) >= 3
    
    # Check that percentages sum to 1.0 (100%)
    total_pct = sum(m.amount_percentage for m in contract.milestones)
    assert abs(total_pct - 1.0) < 0.01

    # Check milestone estimated amounts sum to total budget
    total_m_amt = sum(m.estimated_amount for m in contract.milestones)
    assert abs(total_m_amt - 1500.0) < 0.05

    # Check that each milestone contains structured acceptance criteria
    for m in contract.milestones:
        assert len(m.acceptance_criteria) > 0
        for crit in m.acceptance_criteria:
            assert "description" in crit
            assert "verification_method" in crit

    assert contract.recommended_fx_strategy == FXStrategy.LOCKED_AT_SIGNING
    assert "PayPal" in contract.dispute_protocol_summary

    # Explicitly test deterministic rule-based template engine
    contract_rule = await ai_contract_builder.generate_contract(req, force_rule_based=True)
    assert contract_rule.generator_source == "rule_based_deterministic_engine"
    assert contract_rule.suggested_total_amount == 1500.0
    assert len(contract_rule.milestones) == 4


@pytest.mark.asyncio
async def test_ai_builder_custom_budget_and_strategy():
    req = AIBriefRequestSchema(
        brief="Create custom React dashboard with TailwindCSS and ChartJS",
        budget=2400.0,
        fx_strategy=FXStrategy.SPLIT_RISK,
        base_currency="USD",
        payout_currency="EUR",
    )
    contract = await ai_contract_builder.generate_contract(req)

    assert contract.suggested_total_amount == 2400.0
    assert contract.payout_currency == "EUR"
    assert contract.recommended_fx_strategy == FXStrategy.SPLIT_RISK
