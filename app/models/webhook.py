from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime
from app.database import Base


class ProcessedWebhookDelivery(Base):
    __tablename__ = "processed_webhook_deliveries"

    delivery_id = Column(String(128), primary_key=True)
    source = Column(String(32), nullable=False, index=True)  # "github", "paypal"
    event_type = Column(String(64), nullable=True)
    payload_hash = Column(String(64), nullable=True)
    processed_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
