import pytest
from app.services.paypal_client import paypal_client


@pytest.mark.asyncio
async def test_paypal_create_order_sandbox():
    order = await paypal_client.create_order(
        contract_id="ct_test_12345",
        amount=500.0,
        currency="USD",
    )
    assert order["order_id"] is not None
    assert order["status"] in ("CREATED", "SAVED")
    assert "approve_url" in order
    assert order["amount"] == 500.0
    assert order["currency"] == "USD"


@pytest.mark.asyncio
async def test_paypal_capture_order_sandbox():
    capture = await paypal_client.capture_order(order_id="ORDER-SANDBOX-TEST99")
    assert capture["order_id"] == "ORDER-SANDBOX-TEST99"
    assert capture["capture_id"] is not None
    assert capture["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_paypal_create_payout_sandbox():
    payout = await paypal_client.create_payout(
        recipient_email="freelancer@bangalore.io",
        amount=400.0,
        currency="USD",
        note="Milestone 1 Release",
        sender_item_id="ms_test_01",
    )
    assert payout["payout_batch_id"] is not None
    assert payout["status"] in ("SUCCESS", "PENDING")
    assert payout["recipient_email"] == "freelancer@bangalore.io"
