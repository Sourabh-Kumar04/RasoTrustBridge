from fastapi import APIRouter, HTTPException, Request, Response, Depends, Header
from sqlalchemy.orm import Session
from typing import Optional
from app.database import get_db
from app.schemas.paypal import (
    CreateOrderRequest,
    CreateOrderResponse,
    CaptureOrderRequest,
    CaptureOrderResponse,
)
from app.services.paypal_client import paypal_client
from app.services.webhook_dedup_service import webhook_dedup_service

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
async def paypal_webhook(
    request: Request,
    db: Session = Depends(get_db),
    paypal_transmission_id: Optional[str] = Header(None, alias="paypal-transmission-id"),
):
    """
    Listens for PayPal sandbox webhook events (e.g. PAYMENT.CAPTURE.COMPLETED, CHECKOUT.ORDER.APPROVED).
    Deduplicates events by transmission/event ID with 200 response.
    """
    try:
        raw_body = await request.body()
        payload = {}
        if raw_body:
            import json
            try:
                payload = json.loads(raw_body.decode("utf-8"))
            except Exception:
                pass

        delivery_id = paypal_transmission_id or payload.get("id")
        event_type = payload.get("event_type")
        resource_id = payload.get("resource", {}).get("id")

        if delivery_id:
            is_new = webhook_dedup_service.check_and_record(
                db=db,
                delivery_id=delivery_id,
                source="paypal",
                event_type=event_type,
                payload_bytes=raw_body,
            )
            if not is_new:
                return {
                    "status": "duplicate",
                    "delivery_id": delivery_id,
                    "transmission_id": delivery_id,
                    "message": f"PayPal webhook delivery '{delivery_id}' already processed. Deduplicated.",
                }

        return {
            "status": "received",
            "event_type": event_type,
            "resource_id": resource_id,
            "delivery_id": delivery_id,
        }
    except Exception:
        return Response(status_code=200)
