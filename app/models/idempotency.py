import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Text, DateTime
from app.database import Base


def generate_idempotency_id() -> str:
    return f"idem_{uuid.uuid4().hex[:12]}"


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"

    id = Column(String(32), primary_key=True, default=generate_idempotency_id)
    key = Column(String(255), unique=True, nullable=False, index=True)
    response_hash = Column(String(128), nullable=True)
    response_json = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    expires_at = Column(DateTime(timezone=True), nullable=True)
