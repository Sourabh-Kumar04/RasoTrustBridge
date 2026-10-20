import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, Float, Integer
from sqlalchemy.orm import relationship
from app.database import Base


def generate_user_id() -> str:
    return f"usr_{uuid.uuid4().hex[:12]}"


class User(Base):
    __tablename__ = "users"

    id = Column(String(32), primary_key=True, default=generate_user_id)
    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    role = Column(String(32), nullable=False, default="client")  # "client", "freelancer", or "admin"
    display_name = Column(String(128), nullable=False)
    trust_score = Column(Float, nullable=False, default=30.0)  # 0 to 100, default 30 (new accounts)
    completed_deals = Column(Integer, nullable=False, default=0)
    disputed_deals = Column(Integer, nullable=False, default=0)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    client_contracts = relationship(
        "Contract",
        foreign_keys="Contract.client_id",
        back_populates="client_user",
    )
    freelancer_contracts = relationship(
        "Contract",
        foreign_keys="Contract.freelancer_id",
        back_populates="freelancer_user",
    )
    notifications = relationship(
        "Notification",
        back_populates="user",
        cascade="all, delete-orphan",
        order_by="desc(Notification.created_at)",
    )
