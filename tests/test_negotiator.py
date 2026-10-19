import pytest
from app.models.enums import FXStrategy, ContractStatus
from app.schemas.negotiation import NegotiationRequestSchema
from app.services.negotiator_service import negotiator_service


def test_negotiate_api_endpoint_success(client):
    payload = {
        "buyer_brief": "Need API built for international remittance. Budget $3000 USD, timeline 21 days.",
        "seller_brief": "Can build API with PayPal integration and FastAPI. Ask $4000 USD, timeline 28 days.",
        "client_name": "Sarah Jenkins",
        "client_email": "sarah@acme.com",
        "client_country": "US",
        "developer_name": "Arjun Patel",
        "developer_email": "arjun@devstudio.in",
        "developer_country": "IN",
        "base_currency": "USD",
        "payout_currency": "INR",
        "max_rounds": 3,
        "auto_create_contract": True,
        "force_rule_based": True,
    }

    res = client.post("/api/v1/negotiate", json=payload)
    assert res.status_code == 200
    data = res.json()

    assert data["status"] == "agreed"
    assert data["total_rounds"] == 3
    assert len(data["rounds"]) == 3
    assert len(data["transcript"]) == 6  # 3 rounds * 2 agents

    # Check transcript details
    for msg in data["transcript"]:
        assert msg["sender"] in ("buyer_agent", "seller_agent")
        assert len(msg["reasoning"]) > 10
        assert len(msg["message"]) > 10
        assert msg["offer"]["price"] > 0
        assert msg["offer"]["timeline_days"] > 0

    # Check price convergence
    r1 = data["rounds"][0]
    r3 = data["rounds"][2]
    assert r1["price_gap"] > r3["price_gap"]
    assert r3["price_gap"] <= 1.0

    # Check agreed terms
    agreed = data["agreed_terms"]
    assert agreed is not None
    assert 3000.0 <= agreed["final_price"] <= 4000.0
    assert len(agreed["milestones"]) == 3
    assert agreed["fx_strategy"] == "LOCKED_AT_SIGNING"

    # Check auto-created contract
    assert data["contract_id"] is not None
    assert data["contract"] is not None
    assert data["contract"]["status"] == "DRAFT"
    assert data["contract"]["total_amount"] == agreed["final_price"]

    # Verify contract exists in database via contracts endpoint
    c_res = client.get(f"/api/v1/contracts/{data['contract_id']}")
    assert c_res.status_code == 200
    assert c_res.json()["title"] == agreed["title"]

    # Verify negotiation can be retrieved by ID
    neg_id = data["id"]
    get_res = client.get(f"/api/v1/negotiate/{neg_id}")
    assert get_res.status_code == 200
    assert get_res.json()["id"] == neg_id

    # Verify list negotiations
    list_res = client.get("/api/v1/negotiate")
    assert list_res.status_code == 200
    assert any(n["id"] == neg_id for n in list_res.json())


def test_negotiate_without_contract_creation(client):
    payload = {
        "buyer_brief": "Build a React Native mobile prototype for delivery tracking. Budget $1500.",
        "seller_brief": "Senior mobile engineer. Ask $2200 for full iOS and Android build.",
        "max_rounds": 2,
        "auto_create_contract": False,
        "force_rule_based": True,
    }

    res = client.post("/api/v1/negotiate", json=payload)
    assert res.status_code == 200
    data = res.json()

    assert data["status"] == "agreed"
    assert data["total_rounds"] == 2
    assert data["contract_id"] is None
    assert data["contract"] is None
    assert data["agreed_terms"] is not None
    assert 1500.0 <= data["agreed_terms"]["final_price"] <= 2200.0


@pytest.mark.asyncio
async def test_negotiator_service_unit():
    req = NegotiationRequestSchema(
        buyer_brief="Need microservice architecture. Budget $5,000, 30 days.",
        seller_brief="Enterprise architect. Ask $6,500, 40 days.",
        force_rule_based=True,
        auto_create_contract=False,
    )

    p_buyer = negotiator_service._extract_price(req.buyer_brief, 1000.0)
    p_seller = negotiator_service._extract_price(req.seller_brief, 1000.0)
    assert p_buyer == 5000.0
    assert p_seller == 6500.0

    t_buyer = negotiator_service._extract_timeline(req.buyer_brief, 14)
    t_seller = negotiator_service._extract_timeline(req.seller_brief, 14)
    assert t_buyer == 30
    assert t_seller == 40
