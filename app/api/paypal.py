from fastapi import APIRouter, HTTPException, Request, Response
from app.schemas.paypal import (
    CreateOrderRequest,
    CreateOrderResponse,
    CaptureOrderRequest,
    CaptureOrderResponse,
)
from app.services.paypal_client import paypal_client

router = APIRouter(prefix="/paypal", tags=["PayPal Sandbox Gateway"])


@router.post("/order/create", response_model=CreateOrderResponse)
async def create_paypal_order(
    req: CreateOrderRequest,
):
    """
    Creates a PayPal Order to hold funds for a contract in escrow.
    Returns approval URL where client authorizes the transaction.
    """
    try:
        res = await paypal_client.create_order(
            contract_id=req.contract_id,
            amount=req.amount,
            currency=req.currency,
            return_url=req.return_url or "https://trustbridge.network/checkout/success",
            cancel_url=req.cancel_url or "https://trustbridge.network/checkout/cancel",
        )
        return CreateOrderResponse(
            order_id=res["order_id"],
            status=res["status"],
            approve_url=res.get("approve_url"),
            contract_id=req.contract_id,
            amount=req.amount,
            currency=req.currency,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create PayPal order: {str(e)}")


@router.post("/order/capture", response_model=CaptureOrderResponse)
async def capture_paypal_order(
    req: CaptureOrderRequest,
):
    """
    Captures an authorized PayPal order, transferring funds into escrow holding.
    """
    try:
        res = await paypal_client.capture_order(order_id=req.order_id)
        return CaptureOrderResponse(
            order_id=res["order_id"],
            capture_id=res["capture_id"],
            status=res["status"],
            amount_captured=res.get("amount_captured", 0.0),
            currency=res.get("currency", "USD"),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to capture PayPal order: {str(e)}")


@router.post("/webhook")
async def paypal_webhook(request: Request):
    """
    Listens for PayPal sandbox webhook events (e.g. PAYMENT.CAPTURE.COMPLETED, CHECKOUT.ORDER.APPROVED).
    """
    try:
        payload = await request.json()
        event_type = payload.get("event_type")
        resource_id = payload.get("resource", {}).get("id")
        return {
            "status": "received",
            "event_type": event_type,
            "resource_id": resource_id,
        }
    except Exception:
        return Response(status_code=200)
