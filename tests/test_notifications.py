import pytest
from app.models.enums import ActorRole
from app.services.notification_service import notification_service
from app.models.user import User
from app.services.auth_service import hash_password


def test_notification_service_unit(db_session):
    # 1. Create test user
    user = User(
        email="notif.user@example.com",
        hashed_password=hash_password("Pass123!"),
        role="client",
        display_name="Notification Tester",
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    # 2. Test send_email interface (stub log driver)
    sent = notification_service.send_email(
        to_email=user.email,
        subject="Test Notification",
        body_text="This is a test notification message.",
    )
    assert sent is True

    # 3. Create notification
    notif1 = notification_service.create_notification(
        db=db_session,
        user_id=user.id,
        notif_type="MILESTONE_SUBMITTED",
        title="Deliverable Submitted",
        body="Milestone 1 has been submitted.",
    )
    assert notif1.id.startswith("notif_")
    assert notif1.read is False

    notif2 = notification_service.create_notification(
        db=db_session,
        user_id=user.id,
        notif_type="FUNDS_RELEASED",
        title="Funds Released",
        body="Funds of $500 released.",
    )

    # 4. Check unread count
    unread = notification_service.get_unread_count(db_session, user.id)
    assert unread == 2

    # 5. Mark notif1 as read
    updated = notification_service.mark_as_read(db_session, notif1.id, user.id)
    assert updated.read is True
    assert notification_service.get_unread_count(db_session, user.id) == 1

    # 6. Mark all as read
    count = notification_service.mark_all_as_read(db_session, user.id)
    assert count == 1
    assert notification_service.get_unread_count(db_session, user.id) == 0


def test_notification_event_triggers_and_endpoints(unauth_client):
    # 1. Register Client Dave
    res_dave = unauth_client.post(
        "/api/v1/auth/register",
        json={
            "email": "dave.client@notif-corp.com",
            "password": "Password123!",
            "role": "client",
            "display_name": "Dave Client",
        },
    )
    assert res_dave.status_code == 201
    dave_token = res_dave.json()["access_token"]
    dave_id = res_dave.json()["user"]["id"]

    # 2. Register Freelancer Eva
    res_eva = unauth_client.post(
        "/api/v1/auth/register",
        json={
            "email": "eva.dev@notif-dev.com",
            "password": "Password123!",
            "role": "freelancer",
            "display_name": "Eva Engineer",
        },
    )
    assert res_eva.status_code == 201
    eva_token = res_eva.json()["access_token"]
    eva_id = res_eva.json()["user"]["id"]

    # 3. Dave creates contract with Eva
    contract_payload = {
        "title": "Payment SDK Integration",
        "brief": "FastAPI PayPal integration with notifications",
        "client_name": "Dave Client",
        "client_email": "dave.client@notif-corp.com",
        "developer_name": "Eva Engineer",
        "developer_email": "eva.dev@notif-dev.com",
        "base_currency": "USD",
        "payout_currency": "EUR",
        "total_amount": 1000.0,
        "milestones": [
            {
                "title": "Milestone 1: Webhook Handling",
                "description": "HMAC-SHA256 signature verification",
                "amount": 1000.0,
                "order_index": 1,
            }
        ],
    }
    c_res = unauth_client.post(
        "/api/v1/contracts",
        json=contract_payload,
        headers={"Authorization": f"Bearer {dave_token}"},
    )
    assert c_res.status_code == 201
    contract = c_res.json()
    contract_id = contract["id"]
    milestone_id = contract["milestones"][0]["id"]

    # 4. Dave funds contract
    fund_res = unauth_client.post(
        f"/api/v1/contracts/{contract_id}/fund",
        json={"paypal_order_id": "ORDER-NOTIF-TEST-001"},
        headers={"Authorization": f"Bearer {dave_token}"},
    )
    assert fund_res.status_code == 200

    # 5. Eva submits milestone -> triggers notify_milestone_submitted to Dave!
    submit_res = unauth_client.post(
        f"/api/v1/milestones/{milestone_id}/submit",
        json={
            "evidence_url": "https://github.com/Sourabh-Kumar04/TrustBridge/pull/42",
            "evidence_notes": "Implemented webhooks with 100% test coverage",
            "evidence_payload": {"tests_passed": 12, "tests_total": 12},
        },
        headers={"Authorization": f"Bearer {eva_token}"},
    )
    assert submit_res.status_code == 200

    # Check Dave's notifications -> should have MILESTONE_SUBMITTED
    dave_notifs = unauth_client.get(
        "/api/v1/notifications",
        headers={"Authorization": f"Bearer {dave_token}"},
    )
    assert dave_notifs.status_code == 200
    dave_data = dave_notifs.json()
    assert dave_data["unread_count"] >= 1
    submitted_notif = next((n for n in dave_data["notifications"] if n["type"] == "MILESTONE_SUBMITTED"), None)
    assert submitted_notif is not None
    assert "Milestone Submitted" in submitted_notif["title"]

    # 6. Eva's notifications -> should have MILESTONE_VERIFIED (since submission score >= 0.80)
    eva_notifs = unauth_client.get(
        "/api/v1/notifications",
        headers={"Authorization": f"Bearer {eva_token}"},
    )
    assert eva_notifs.status_code == 200
    eva_data = eva_notifs.json()
    assert eva_data["unread_count"] >= 1
    verified_notif = next((n for n in eva_data["notifications"] if n["type"] == "MILESTONE_VERIFIED"), None)
    assert verified_notif is not None

    # 7. Dave releases milestone -> triggers notify_funds_released to Eva!
    rel_res = unauth_client.post(
        f"/api/v1/milestones/{milestone_id}/release",
        json={"release_note": "Great work approved!"},
        headers={"Authorization": f"Bearer {dave_token}"},
    )
    assert rel_res.status_code == 200

    # Eva checks notifications again -> should have FUNDS_RELEASED
    eva_notifs2 = unauth_client.get(
        "/api/v1/notifications",
        headers={"Authorization": f"Bearer {eva_token}"},
    )
    assert eva_notifs2.status_code == 200
    released_notif = next((n for n in eva_notifs2.json()["notifications"] if n["type"] == "FUNDS_RELEASED"), None)
    assert released_notif is not None
    assert "Funds Released" in released_notif["title"]

    # 8. Mark notification as read endpoint
    read_res = unauth_client.post(
        f"/api/v1/notifications/{released_notif['id']}/read",
        headers={"Authorization": f"Bearer {eva_token}"},
    )
    assert read_res.status_code == 200
    assert read_res.json()["read"] is True

    # 9. Dave tries to mark Eva's notification as read -> 404 Not Found
    forbidden_read = unauth_client.post(
        f"/api/v1/notifications/{released_notif['id']}/read",
        headers={"Authorization": f"Bearer {dave_token}"},
    )
    assert forbidden_read.status_code == 404

    # 10. Mark all as read endpoint
    read_all_res = unauth_client.post(
        "/api/v1/notifications/read-all",
        headers={"Authorization": f"Bearer {eva_token}"},
    )
    assert read_all_res.status_code == 200
    assert read_all_res.json()["message"] == "All notifications marked as read"

    # Verify unread count is now 0 for Eva
    eva_final = unauth_client.get(
        "/api/v1/notifications",
        headers={"Authorization": f"Bearer {eva_token}"},
    )
    assert eva_final.json()["unread_count"] == 0
