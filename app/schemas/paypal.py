from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field


class CreateOrderRequest(BaseModel):
    contract_id: str
    amount: float
    currency: str = "USD"
    return_url: Optional[str] = "https://trustbridge.network/checkout/success"
    cancel_url: Optional[str] = "https://trustbridge.network/checkout/cancel"


class CreateOrderResponse(BaseModel):
    order_id: str
    status: str
    approve_url: Optional[str] = None
    contract_id: str
    amount: float
    currency: str


class CaptureOrderRequest(BaseModel):
    order_id: str
    contract_id: Optional[str] = None


class CaptureOrderResponse(BaseModel):
    order_id: str
    capture_id: str
    status: str
    amount_captured: float
    currency: str


class PayoutItemRequest(BaseModel):
    recipient_email: str
    amount: float
    currency: str = "USD"
    note: str
    sender_item_id: str


class PayoutBatchResponse(BaseModel):
    payout_batch_id: str
    batch_status: str
    sender_batch_header: Dict[str, Any]
    items: List[Dict[str, Any]] = Field(default_factory=list)


class PayPalWebhookEvent(BaseModel):
    id: str
    event_type: str
    resource_type: str
    resource: Dict[str, Any]
    create_time: str
