import time
import uuid
from typing import Dict, Any, Optional
import httpx
from app.config import get_settings


class PayPalClient:
    """
    PayPal REST API Sandbox client supporting Orders API (holding funds)
    and Payouts API (releasing funds to international freelancers).
    Gracefully falls back to deterministic sandbox simulation when credentials
    are not configured in the environment.
    """

    def __init__(self):
        self.settings = get_settings()
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0

    @property
    def is_configured(self) -> bool:
        return bool(
            self.settings.PAYPAL_CLIENT_ID and 
            self.settings.PAYPAL_CLIENT_SECRET and
            len(self.settings.PAYPAL_CLIENT_ID.strip()) > 5
        )

    async def get_access_token(self) -> str:
        """Fetch or return cached OAuth2 access token."""
        now = time.time()
        if self._access_token and now < self._token_expires_at - 60:
            return self._access_token

        if not self.is_configured:
            return "mock_sandbox_access_token"

        url = f"{self.settings.PAYPAL_API_BASE}/v1/oauth2/token"
        auth = (self.settings.PAYPAL_CLIENT_ID, self.settings.PAYPAL_CLIENT_SECRET)
        data = {"grant_type": "client_credentials"}
        headers = {"Accept": "application/json", "Accept-Language": "en_US"}

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, data=data, auth=auth, headers=headers)
            response.raise_for_status()
            res_data = response.json()
            self._access_token = res_data["access_token"]
            expires_in = res_data.get("expires_in", 3600)
            self._token_expires_at = now + expires_in
            return self._access_token

    async def create_order(
        self,
        contract_id: str,
        amount: float,
        currency: str = "USD",
        return_url: str = "https://trustbridge.network/checkout/success",
        cancel_url: str = "https://trustbridge.network/checkout/cancel",
    ) -> Dict[str, Any]:
        """
        Creates a PayPal Order to hold / authorize funds in escrow.
        Uses POST /v2/checkout/orders with intent=CAPTURE.
        """
        if not self.is_configured:
            order_id = f"ORDER-SANDBOX-{uuid.uuid4().hex[:10].upper()}"
            return {
                "order_id": order_id,
                "status": "CREATED",
                "approve_url": f"https://www.sandbox.paypal.com/checkoutnow?token={order_id}",
                "amount": amount,
                "currency": currency,
                "mock": True,
            }

        token = await self.get_access_token()
        url = f"{self.settings.PAYPAL_API_BASE}/v2/checkout/orders"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }
        payload = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "reference_id": contract_id,
                    "description": f"TrustBridge Escrow Hold for Contract {contract_id}",
                    "custom_id": contract_id,
                    "amount": {
                        "currency_code": currency.upper(),
                        "value": f"{amount:.2f}",
                    },
                }
            ],
            "application_context": {
                "brand_name": "TrustBridge Escrow",
                "landing_page": "NO_PREFERENCE",
                "user_action": "PAY_NOW",
                "return_url": return_url,
                "cancel_url": cancel_url,
            },
        }

        async with httpx.AsyncClient(timeout=12.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            approve_url = None
            for link in data.get("links", []):
                if link.get("rel") == "approve":
                    approve_url = link.get("href")
                    break

            return {
                "order_id": data.get("id"),
                "status": data.get("status", "CREATED"),
                "approve_url": approve_url,
                "amount": amount,
                "currency": currency,
                "mock": False,
            }

    async def capture_order(self, order_id: str) -> Dict[str, Any]:
        """
        Captures previously approved order into escrow balance.
        Uses POST /v2/checkout/orders/{id}/capture.
        """
        if not self.is_configured or order_id.startswith("ORDER-SANDBOX-"):
            capture_id = f"CAP-SANDBOX-{uuid.uuid4().hex[:10].upper()}"
            return {
                "order_id": order_id,
                "capture_id": capture_id,
                "status": "COMPLETED",
                "amount_captured": 0.0,
                "mock": True,
            }

        token = await self.get_access_token()
        url = f"{self.settings.PAYPAL_API_BASE}/v2/checkout/orders/{order_id}/capture"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }

        async with httpx.AsyncClient(timeout=12.0) as client:
            response = await client.post(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            capture_id = None
            amount_captured = 0.0
            currency = "USD"
            try:
                purchase_units = data.get("purchase_units", [])
                if purchase_units:
                    captures = purchase_units[0].get("payments", {}).get("captures", [])
                    if captures:
                        capture_id = captures[0].get("id")
                        amount_captured = float(captures[0].get("amount", {}).get("value", 0.0))
                        currency = captures[0].get("amount", {}).get("currency_code", "USD")
            except Exception:
                pass

            return {
                "order_id": data.get("id", order_id),
                "capture_id": capture_id or f"CAP-{data.get('id', 'UNKNOWN')}",
                "status": data.get("status", "COMPLETED"),
                "amount_captured": amount_captured,
                "currency": currency,
                "mock": False,
            }

    async def create_payout(
        self,
        recipient_email: str,
        amount: float,
        currency: str = "USD",
        note: str = "TrustBridge Milestone Release",
        sender_item_id: str = "",
    ) -> Dict[str, Any]:
        """
        Releases funds to international freelancer via PayPal Payouts API.
        Uses POST /v1/payments/payouts.
        """
        if not self.is_configured:
            batch_id = f"PAYOUT-SANDBOX-BATCH-{uuid.uuid4().hex[:8].upper()}"
            item_id = f"PAYOUT-ITEM-{uuid.uuid4().hex[:8].upper()}"
            return {
                "payout_batch_id": batch_id,
                "payout_item_id": item_id,
                "status": "SUCCESS",
                "amount": amount,
                "currency": currency,
                "recipient_email": recipient_email,
                "mock": True,
            }

        token = await self.get_access_token()
        url = f"{self.settings.PAYPAL_API_BASE}/v1/payments/payouts"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }
        batch_id = f"batch_{uuid.uuid4().hex[:12]}"
        payload = {
            "sender_batch_header": {
                "sender_batch_id": batch_id,
                "email_subject": "TrustBridge Milestone Released",
                "email_message": note,
            },
            "items": [
                {
                    "recipient_type": "EMAIL",
                    "amount": {
                        "value": f"{amount:.2f}",
                        "currency": currency.upper(),
                    },
                    "note": note,
                    "sender_item_id": sender_item_id or f"item_{uuid.uuid4().hex[:8]}",
                    "receiver": recipient_email,
                }
            ],
        }

        async with httpx.AsyncClient(timeout=12.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            batch_header = data.get("batch_header", {})
            return {
                "payout_batch_id": batch_header.get("payout_batch_id", batch_id),
                "payout_item_id": sender_item_id,
                "status": batch_header.get("batch_status", "PENDING"),
                "amount": amount,
                "currency": currency,
                "recipient_email": recipient_email,
                "mock": False,
            }


paypal_client = PayPalClient()
