def test_root_and_health(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.json()["service"] == "TrustBridge"

    r_health = client.get("/health")
    assert r_health.status_code == 200
    assert r_health.json()["status"] == "healthy"


def test_ai_generate_contract_endpoint(client):
    payload = {
        "brief": "Build a secure payment gateway integration with webhooks and refund handling in FastAPI.",
        "client_name": "Stripe Alum",
        "developer_name": "Bangalore Lead",
        "budget": 2000.0,
    }
    res = client.post("/api/v1/ai/generate-contract", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["suggested_total_amount"] == 2000.0
    assert len(data["milestones"]) >= 3


def test_contract_crud_and_funding_flow(client):
    # 1. Create contract
    contract_data = {
        "title": "API Engine Contract",
        "brief": "Full API backend development",
        "client_name": "New York Client",
        "client_email": "client@ny.com",
        "client_country": "US",
        "developer_name": "Bangalore Dev",
        "developer_email": "dev@bangalore.in",
        "developer_country": "IN",
        "base_currency": "USD",
        "payout_currency": "INR",
        "total_amount": 1000.0,
        "fx_strategy": "LOCKED_AT_SIGNING",
        "locked_fx_rate": 84.50,
        "milestones": [
            {
                "title": "Milestone 1",
                "description": "Backend foundation",
                "amount": 1000.0,
                "order_index": 1,
                "verification_type": "CODE_DELIVERY",
                "acceptance_criteria": [
                    {"description": "Unit tests pass", "verification_method": "automated_test"}
                ],
            }
        ],
    }

    res = client.post("/api/v1/contracts", json=contract_data)
    assert res.status_code == 201
    contract = res.json()
    c_id = contract["id"]
    m_id = contract["milestones"][0]["id"]
    assert contract["status"] == "DRAFT"

    # 2. Get contract
    get_res = client.get(f"/api/v1/contracts/{c_id}")
    assert get_res.status_code == 200
    assert get_res.json()["id"] == c_id

    # 3. Fund contract
    fund_res = client.post(
        f"/api/v1/contracts/{c_id}/fund",
        json={"paypal_order_id": "ORDER-API-TEST-123"},
    )
    assert fund_res.status_code == 200
    assert fund_res.json()["status"] == "ACTIVE"

    # 4. Submit milestone
    submit_res = client.post(
        f"/api/v1/milestones/{m_id}/submit",
        json={
            "evidence_url": "https://github.com/Sourabh-Kumar04/TrustBridge/pull/1",
            "evidence_payload": {"tests_passed": 10, "tests_total": 10},
        },
    )
    assert submit_res.status_code == 200
    assert submit_res.json()["status"] == "VERIFIED"

    # 5. Release milestone
    release_res = client.post(
        f"/api/v1/milestones/{m_id}/release",
        json={"release_note": "Great work approved!"},
    )
    assert release_res.status_code == 200
    assert release_res.json()["status"] == "RELEASED"

    # 6. Verify contract is now COMPLETED
    c_after = client.get(f"/api/v1/contracts/{c_id}").json()
    assert c_after["status"] == "COMPLETED"


def test_fx_endpoints(client):
    quote_res = client.get("/api/v1/fx/quote?base=USD&target=INR&amount=500")
    assert quote_res.status_code == 200
    data = quote_res.json()
    assert data["converted_amount"] > 10000

    compare_res = client.post(
        "/api/v1/fx/compare",
        json={
            "base_currency": "USD",
            "target_currency": "INR",
            "amount": 1000.0,
            "locked_rate": 84.00,
            "fx_strategy": "SPLIT_RISK",
            "spot_rate": 89.00,
        },
    )
    assert compare_res.status_code == 200
    compare_data = compare_res.json()
    assert compare_data["effective_payout_amount"] > 84000.0


def test_paypal_endpoints(client):
    create_res = client.post(
        "/api/v1/paypal/order/create",
        json={"contract_id": "ct_123", "amount": 250.0},
    )
    assert create_res.status_code == 200
    order_id = create_res.json()["order_id"]
    assert order_id is not None

    capture_res = client.post(
        "/api/v1/paypal/order/capture",
        json={"order_id": order_id},
    )
    assert capture_res.status_code == 200
    assert capture_res.json()["status"] == "COMPLETED"

    webhook_res = client.post(
        "/api/v1/paypal/webhook",
        json={"event_type": "CHECKOUT.ORDER.APPROVED", "resource": {"id": order_id}},
    )
    assert webhook_res.status_code == 200
