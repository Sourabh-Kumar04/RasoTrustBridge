from datetime import datetime
from typing import List
from pydantic import BaseModel, ConfigDict


class NotificationResponseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    type: str
    title: str
    body: str
    read: bool
    created_at: datetime


class NotificationListResponse(BaseModel):
    notifications: List[NotificationResponseSchema]
    unread_count: int
