from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.user import User
from app.schemas.notification import (
    NotificationResponseSchema,
    NotificationListResponse,
)
from app.services.notification_service import notification_service
from app.api.deps import get_current_user

router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.get("", response_model=NotificationListResponse)
def list_notifications(
    unread_only: bool = Query(False, description="Filter for unread notifications only"),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Retrieves in-app notifications for the authenticated user, ordered from newest to oldest.
    Includes count of total unread notifications.
    """
    items = notification_service.get_user_notifications(
        db=db,
        user_id=current_user.id,
        unread_only=unread_only,
        limit=limit,
    )
    unread_count = notification_service.get_unread_count(db=db, user_id=current_user.id)
    return NotificationListResponse(
        notifications=[NotificationResponseSchema.model_validate(n) for n in items],
        unread_count=unread_count,
    )


@router.post("/{notification_id}/read", response_model=NotificationResponseSchema)
def mark_notification_read(
    notification_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Marks a specific notification as read.
    """
    notif = notification_service.mark_as_read(
        db=db,
        notification_id=notification_id,
        user_id=current_user.id,
    )
    if not notif:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found",
        )
    return NotificationResponseSchema.model_validate(notif)


@router.post("/read-all", response_model=dict)
def mark_all_notifications_read(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Marks all unread notifications for current user as read.
    """
    count = notification_service.mark_all_as_read(db=db, user_id=current_user.id)
    return {"message": "All notifications marked as read", "updated_count": count}
