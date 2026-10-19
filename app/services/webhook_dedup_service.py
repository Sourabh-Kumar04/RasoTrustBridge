from datetime import datetime, timezone
import hashlib
from typing import Optional
from sqlalchemy.orm import Session
from app.models.webhook import ProcessedWebhookDelivery


class WebhookDedupService:
    """
    Deduplicates incoming webhooks (GitHub, PayPal) by transmission / delivery ID.
    Rejects duplicates with HTTP 200 to prevent duplicate execution while satisfying webhook providers.
    """

    def is_duplicate(self, db: Session, delivery_id: str) -> bool:
        if not delivery_id:
            return False
        return db.query(ProcessedWebhookDelivery).filter(
            ProcessedWebhookDelivery.delivery_id == delivery_id
        ).first() is not None

    def record_delivery(
        self,
        db: Session,
        delivery_id: str,
        source: str,
        event_type: Optional[str] = None,
        payload_bytes: Optional[bytes] = None,
    ) -> ProcessedWebhookDelivery:
        payload_hash = None
        if payload_bytes:
            payload_hash = hashlib.sha256(payload_bytes).hexdigest()

        record = ProcessedWebhookDelivery(
            delivery_id=delivery_id,
            source=source,
            event_type=event_type,
            payload_hash=payload_hash,
            processed_at=datetime.now(timezone.utc),
        )
        db.add(record)
        db.commit()
        return record

    def check_and_record(
        self,
        db: Session,
        delivery_id: str,
        source: str,
        event_type: Optional[str] = None,
        payload_bytes: Optional[bytes] = None,
    ) -> bool:
        """
        Returns True if this is a fresh delivery that has been successfully recorded.
        Returns False if this delivery_id has already been processed (duplicate).
        """
        if not delivery_id:
            return True

        if self.is_duplicate(db, delivery_id):
            return False

        try:
            self.record_delivery(db, delivery_id, source, event_type, payload_bytes)
            return True
        except Exception:
            db.rollback()
            # If race condition or duplicate key error occurs
            return False


webhook_dedup_service = WebhookDedupService()
