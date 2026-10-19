import json
from fastapi import APIRouter, Depends, HTTPException, Request, Header, status
from sqlalchemy.orm import Session
from typing import Optional

from app.database import get_db
from app.services.github_verifier import github_verifier_service

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


@router.post("/github")
async def github_webhook_endpoint(
    request: Request,
    db: Session = Depends(get_db),
    x_hub_signature_256: Optional[str] = Header(None, alias="X-Hub-Signature-256"),
    x_github_event: Optional[str] = Header(None, alias="X-GitHub-Event"),
    x_github_delivery: Optional[str] = Header(None, alias="X-GitHub-Delivery"),
):
    """
    Receives and processes GitHub webhook events with HMAC-SHA256 signature verification.
    Supported events:
    - ping: Webhook handshake confirmation
    - pull_request: Closed/merged PR triggers automated AI milestone delivery verification
    - push: Tracks commit hashes and delivery proof against active contract milestones
    """
    raw_body = await request.body()

    # 1. Cryptographic HMAC-SHA256 Signature Verification
    if not github_verifier_service.verify_signature(raw_body, x_hub_signature_256):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Hub-Signature-256 webhook signature",
        )

    # 2. Parse payload
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Malformed JSON payload: {str(e)}",
        )

    event_type = (x_github_event or "unknown").lower()

    # 3. Dispatch by event type
    if event_type == "ping":
        return {
            "status": "pong",
            "zen": payload.get("zen"),
            "hook_id": payload.get("hook_id"),
            "delivery_id": x_github_delivery,
        }

    elif event_type == "pull_request":
        return await github_verifier_service.handle_pull_request_event(db, payload)

    elif event_type == "push":
        return await github_verifier_service.handle_push_event(db, payload)

    else:
        return {
            "status": "ignored",
            "event": event_type,
            "delivery_id": x_github_delivery,
            "message": f"Webhook event '{event_type}' is received but not handled.",
        }
