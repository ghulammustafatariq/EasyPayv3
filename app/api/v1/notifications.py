"""
app/api/v1/notifications.py — EasyPay v3.0 Notification Endpoints

Routes:
  GET    /notifications                  → 200  paginated list
  PATCH  /notifications/{id}/read        → 200  mark single read
  POST   /notifications/mark-all-read    → 200  mark all read
  GET    /notifications/unread-count     → 200  count badge
  DELETE /notifications/{id}             → 200  delete single

All routes require JWT (get_current_verified_user).
"""
import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_verified_user, get_db
from app.core.exceptions import EasyPayException
from app.models.database import User
from app.schemas.base import success_response
from app.services import notification_service

router = APIRouter(prefix="/notifications", tags=["Notifications"])


# ══════════════════════════════════════════════════════════════════════════════
# GET /notifications
# ══════════════════════════════════════════════════════════════════════════════

@router.get("", status_code=status.HTTP_200_OK)
async def list_notifications(
    unread_only: bool = Query(False, description="Return only unread notifications"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """Return paginated notifications for the authenticated user, newest first."""
    items, total = await notification_service.get_user_notifications(
        db, current_user.id, unread_only=unread_only, page=page, per_page=per_page
    )

    notifications_data = [
        {
            "id": str(n.id),
            "title": n.title,
            "body": n.body,
            "type": n.type,
            "is_read": n.is_read,
            "data": n.data,
            "created_at": n.created_at.isoformat() if n.created_at else None,
        }
        for n in items
    ]

    return success_response(
        message="Notifications retrieved successfully.",
        data={
            "notifications": notifications_data,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "pages": (total + per_page - 1) // per_page if per_page else 1,
            },
        },
    )


# ══════════════════════════════════════════════════════════════════════════════
# GET /notifications/unread-count
# Must be defined BEFORE /{id}/read to avoid FastAPI treating "unread-count" as an id
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/unread-count", status_code=status.HTTP_200_OK)
async def get_unread_count(
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the number of unread notifications (for app badge display)."""
    count = await notification_service.get_unread_count(db, current_user.id)
    return success_response(message="Unread count retrieved.", data={"unread_count": count})


# ══════════════════════════════════════════════════════════════════════════════
# POST /notifications/mark-all-read
# Must be defined BEFORE /{id}/read to avoid route conflict
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/mark-all-read", status_code=status.HTTP_200_OK)
async def mark_all_read(
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark all unread notifications as read."""
    updated = await notification_service.mark_all_read(db, current_user.id)
    return success_response(
        message=f"{updated} notification(s) marked as read.",
        data={"updated_count": updated},
    )


# ══════════════════════════════════════════════════════════════════════════════
# PATCH /notifications/{id}/read
# ══════════════════════════════════════════════════════════════════════════════

@router.patch("/{notification_id}/read", status_code=status.HTTP_200_OK)
async def mark_notification_read(
    notification_id: uuid.UUID,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark a single notification as read."""
    notif = await notification_service.mark_notification_read(
        db, notification_id, current_user.id
    )
    if notif is None:
        raise EasyPayException(
            detail="Notification not found or does not belong to your account.",
            error_code="NOTIFICATION_NOT_FOUND",
        )
    return success_response(
        message="Notification marked as read.",
        data={
            "id": str(notif.id),
            "is_read": notif.is_read,
        },
    )


# ══════════════════════════════════════════════════════════════════════════════
# DELETE /notifications/{id}
# ══════════════════════════════════════════════════════════════════════════════

@router.delete("/{notification_id}", status_code=status.HTTP_200_OK)
async def delete_notification(
    notification_id: uuid.UUID,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a single notification."""
    deleted = await notification_service.delete_notification(
        db, notification_id, current_user.id
    )
    if not deleted:
        raise EasyPayException(
            detail="Notification not found or does not belong to your account.",
            error_code="NOTIFICATION_NOT_FOUND",
        )
    return success_response(
        message="Notification deleted successfully.",
        data={"id": str(notification_id)},
    )
