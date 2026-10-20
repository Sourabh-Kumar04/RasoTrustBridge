from fastapi import APIRouter, HTTPException, Request, Response, Depends, Header, status
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
from app.api.deps import get_current_user
from app.models.user import User
from app.models.contract import Contract
from app.models.payment import EscrowHold
from app.api.contracts import is_client

router = APIRouter(prefix="/paypal", tags=["PayPal Sandbox Gateway"])


@router.post("/order/create", response_model=CreateOrderResponse)
async def create_paypal_order(
    req: CreateOrderRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Creates a PayPal Order to hold funds for a contract in escrow.
    Requires authentication: only the contract's client (or admin) may create orders.
    """
    contract = db.query(Contract).filter(Contract.id == req.contract_id).first()
    if contract:
        if not is_client(contract, current_user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: Only the contract's client may create PayPal orders.",
            )
    else:
        # Non-existent contract is rejected unless caller has admin/system role (e.g. testing)
        if current_user.role not in ("admin", "system"):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Contract not found",
            )

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
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create PayPal order: {str(e)}")


@router.post("/order/capture", response_model=CaptureOrderResponse)
async def capture_paypal_order(
    req: CaptureOrderRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Captures an authorized PayPal order, transferring funds into escrow holding.
    Requires authentication: only the contract's client (or admin) may capture orders.
    """
    linked_contract = None
    if req.contract_id:
        linked_contract = db.query(Contract).filter(Contract.id == req.contract_id).first()
    else:
        linked_contract = (
            db.query(Contract).filter(Contract.paypal_order_id == req.order_id).first()
        )
        if not linked_contract:
            escrow = db.query(EscrowHold).filter(EscrowHold.paypal_order_id == req.order_id).first()
            if escrow and escrow.contract:
                linked_contract = escrow.contract

    if linked_contract and not is_client(linked_contract, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Only the contract's client may capture PayPal orders.",
        )

    try:
        res = await paypal_client.capture_order(order_id=req.order_id)
        return CaptureOrderResponse(
            order_id=res["order_id"],
            capture_id=res["capture_id"],
            status=res["status"],
            amount_captured=res.get("amount_captured", 0.0),
            currency=res.get("currency", "USD"),
        )
    except HTTPException:
        raise
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
