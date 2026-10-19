import pytest
from app.models.enums import ContractStatus, MilestoneStatus, FXStrategy, VerificationType
from app.schemas.contract import ContractCreateSchema, MilestoneCreateSchema


def test_auth_registration_and_login_flow(unauth_client):
    # 1. Register a new client user
    reg_payload = {
        "email": "sarah.client@example.com",
        "password": "SecurePassword123!",
        "role": "client",
        "display_name": "Sarah Connor",
    }
    reg_res = unauth_client.post("/api/v1/auth/register", json=reg_payload)
    assert reg_res.status_code == 201
    reg_data = reg_res.json()
    assert "access_token" in reg_data
    assert "refresh_token" in reg_data
    assert reg_data["user"]["email"] == "sarah.client@example.com"
    assert reg_data["user"]["role"] == "client"
    assert reg_data["user"]["display_name"] == "Sarah Connor"

    # 2. Duplicate registration with same email should be rejected
    dup_res = unauth_client.post("/api/v1/auth/register", json=reg_payload)
    assert dup_res.status_code == 400
    assert "already exists" in dup_res.json()["detail"].lower()

    # 3. Login with correct credentials
    login_payload = {
        "email": "sarah.client@example.com",
        "password": "SecurePassword123!",
    }
    login_res = unauth_client.post("/api/v1/auth/login", json=login_payload)
    assert login_res.status_code == 200
    login_data = login_res.json()
    access_token = login_data["access_token"]
    refresh_token = login_data["refresh_token"]

    # 4. Login with invalid password fails
    bad_login = unauth_client.post(
        "/api/v1/auth/login",
        json={"email": "sarah.client@example.com", "password": "WrongPassword!"},
    )
    assert bad_login.status_code == 401

    # 5. Login with non-existent user fails
    missing_user = unauth_client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@nowhere.com", "password": "Password123!"},
    )
    assert missing_user.status_code == 401

    # 6. Verify /api/v1/auth/me with valid Bearer token
    me_res = unauth_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert me_res.status_code == 200
    me_data = me_res.json()
    assert me_data["email"] == "sarah.client@example.com"
    assert me_data["role"] == "client"

    # 7. Refresh token flow
    refresh_res = unauth_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert refresh_res.status_code == 200
    new_tokens = refresh_res.json()
    assert "access_token" in new_tokens
    assert new_tokens["access_token"] != access_token

    # 8. Refresh with invalid token fails
    bad_refresh = unauth_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": "malformed.jwt.token"},
    )
    assert bad_refresh.status_code == 401


def test_unauthorized_access_blocked(unauth_client):
    # Missing Authorization header -> 401
    res1 = unauth_client.get("/api/v1/contracts")
    assert res1.status_code == 401

    res2 = unauth_client.get("/api/v1/contracts/ct_nonexistent")
    assert res2.status_code == 401

    res3 = unauth_client.get("/api/v1/notifications")
    assert res3.status_code == 401

    res4 = unauth_client.get("/api/v1/auth/me")
    assert res4.status_code == 401

    # Invalid / garbage Bearer token -> 401
    res5 = unauth_client.get(
        "/api/v1/contracts",
        headers={"Authorization": "Bearer bad.token.here"},
    )
    assert res5.status_code == 401


def test_cross_user_isolation(unauth_client):
    # 1. Register Client Alice
    res_a = unauth_client.post(
        "/api/v1/auth/register",
        json={
            "email": "alice@client-corp.com",
            "password": "Password123!",
            "role": "client",
            "display_name": "Alice Anderson",
        },
    )
    assert res_a.status_code == 201
    token_a = res_a.json()["access_token"]
    user_a_id = res_a.json()["user"]["id"]

    # 2. Register Client Bob
    res_b = unauth_client.post(
        "/api/v1/auth/register",
        json={
            "email": "bob@another-biz.com",
            "password": "Password123!",
            "role": "client",
            "display_name": "Bob Builder",
        },
    )
    assert res_b.status_code == 201
    token_b = res_b.json()["access_token"]

    # 3. Register Freelancer Charlie
    res_c = unauth_client.post(
        "/api/v1/auth/register",
        json={
            "email": "charlie@freelance.dev",
            "password": "Password123!",
            "role": "freelancer",
            "display_name": "Charlie Coder",
        },
    )
    assert res_c.status_code == 201
    token_c = res_c.json()["access_token"]

    # 4. Alice creates Contract A (assigning Charlie as developer)
    contract_a_payload = {
        "title": "Alice's Mobile App",
        "brief": "Mobile app MVP",
        "client_name": "Alice Anderson",
        "client_email": "alice@client-corp.com",
        "developer_name": "Charlie Coder",
        "developer_email": "charlie@freelance.dev",
        "base_currency": "USD",
        "payout_currency": "EUR",
        "total_amount": 1000.0,
        "milestones": [
            {
                "title": "M1: Wireframes",
                "description": "Figma files",
                "amount": 1000.0,
                "order_index": 1,
            }
        ],
    }
    c_a_res = unauth_client.post(
        "/api/v1/contracts",
        json=contract_a_payload,
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert c_a_res.status_code == 201
    contract_a = c_a_res.json()
    contract_a_id = contract_a["id"]
    milestone_a_id = contract_a["milestones"][0]["id"]
    assert contract_a["client_id"] == user_a_id

    # 5. Bob attempts to view Contract A -> 403 Forbidden!
    bob_view = unauth_client.get(
        f"/api/v1/contracts/{contract_a_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert bob_view.status_code == 403
    assert "forbidden" in bob_view.json()["detail"].lower()

    # 6. Bob lists contracts -> Contract A is NOT in Bob's list
    bob_list = unauth_client.get(
        "/api/v1/contracts",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert bob_list.status_code == 200
    bob_contract_ids = [c["id"] for c in bob_list.json()]
    assert contract_a_id not in bob_contract_ids

    # 7. Alice lists contracts -> Contract A IS in Alice's list
    alice_list = unauth_client.get(
        "/api/v1/contracts",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert alice_list.status_code == 200
    alice_contract_ids = [c["id"] for c in alice_list.json()]
    assert contract_a_id in alice_contract_ids

    # 8. Charlie (the developer on Contract A) CAN view Contract A
    charlie_view = unauth_client.get(
        f"/api/v1/contracts/{contract_a_id}",
        headers={"Authorization": f"Bearer {token_c}"},
    )
    assert charlie_view.status_code == 200
    assert charlie_view.json()["id"] == contract_a_id

    # 9. Bob attempts to submit evidence on Alice's milestone -> 403 Forbidden!
    bob_submit = unauth_client.post(
        f"/api/v1/milestones/{milestone_a_id}/submit",
        json={"evidence_url": "https://github.com/evil/repo"},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert bob_submit.status_code == 403

    # 10. Bob attempts to fund Alice's contract -> 403 Forbidden!
    bob_fund = unauth_client.post(
        f"/api/v1/contracts/{contract_a_id}/fund",
        json={"paypal_order_id": "ORDER-HACK-123"},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert bob_fund.status_code == 403
