"""
app/services/notification_service.py — EasyPay v3.0 Notification Service

B13 spec:
  create_notification(db, user_id, title, body, type, data={}):
    1. Always save to notifications table first (DB write never skipped).
    2. If user.fcm_token exists → call fcm_service.send_push_notification().
       FCM failure is silently logged — NEVER blocks the DB save or caller.

Critical Rules:
  Point 1 — SQLAlchemy 2.0: await db.execute(select(...)) — never .query()
  B13     — DB insert always completes regardless of FCM outcome.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import Notification, User
from app.services import fcm_service

logger = logging.getLogger(__name__)


async def create_notification(
    db: AsyncSession,
    user_id: str | uuid.UUID,
    title: str,
    body: str,
    type: str = "system",
    data: dict[str, Any] | None = None,
) -> Notification:
    """
    Persist a Notification row and optionally send an FCM push.

    Steps (always in this order):
      1. Insert Notification record → commit.
      2. Fetch user.fcm_token.
      3. If token exists → fire-and-forget FCM push (failure is non-fatal).

    Args:
        db:      Active AsyncSession.
        user_id: UUID of the target user.
        title:   Notification title (max 100 chars — matches DB column).
        body:    Notification body text.
        type:    One of: transaction | security | system | ai_insight | admin
        data:    Optional metadata dict stored in JSONB column.

    Returns:
        The persisted Notification ORM instance.
    """
    uid = uuid.UUID(str(user_id)) if not isinstance(user_id, uuid.UUID) else user_id

    notif = Notification(
        user_id=uid,
        title=title[:100],   # guard against overly long titles
        body=body,
        type=type,
        data=data or {},
    )
    db.add(notif)
    await db.commit()
    await db.refresh(notif)

    # ── FCM push (non-fatal) ──────────────────────────────────────────────────
    try:
        result = await db.execute(select(User).where(User.id == uid))
        user = result.scalar_one_or_none()
        if user and user.fcm_token:
            await fcm_service.send_push_notification(
                device_token=user.fcm_token,
                title=title,
                body=body,
                data=data,
            )
    except Exception as exc:
        # FCM issues must never propagate — log and continue
        logger.error("Notification FCM dispatch error (non-fatal) for user %s: %s", uid, exc)

    return notif


async def get_user_notifications(
    db: AsyncSession,
    user_id: str | uuid.UUID,
    unread_only: bool = False,
    page: int = 1,
    per_page: int = 20,
) -> tuple[list[Notification], int]:
    """
    Return paginated notifications for a user, newest first.

    Returns:
        (items, total_count) tuple.
    """
    from sqlalchemy import func, desc

    uid = uuid.UUID(str(user_id)) if not isinstance(user_id, uuid.UUID) else user_id
    per_page = min(per_page, 100)  # safety cap
    offset = (page - 1) * per_page

    base_query = select(Notification).where(Notification.user_id == uid)
    if unread_only:
        base_query = base_query.where(Notification.is_read == False)  # noqa: E712

    # Total count
    count_result = await db.execute(
        select(func.count()).select_from(base_query.subquery())
    )
    total = count_result.scalar_one()

    # Paginated items
    items_result = await db.execute(
        base_query.order_by(desc(Notification.created_at))
        .offset(offset)
        .limit(per_page)
    )
    items = list(items_result.scalars().all())

    return items, total


async def mark_notification_read(
    db: AsyncSession,
    notification_id: str | uuid.UUID,
    user_id: str | uuid.UUID,
) -> Notification | None:
    """Mark a single notification as read. Returns None if not found or not owned."""
    nid = uuid.UUID(str(notification_id)) if not isinstance(notification_id, uuid.UUID) else notification_id
    uid = uuid.UUID(str(user_id)) if not isinstance(user_id, uuid.UUID) else user_id

    result = await db.execute(
        select(Notification).where(
            Notification.id == nid,
            Notification.user_id == uid,
        )
    )
    notif = result.scalar_one_or_none()
    if notif is None:
        return None

    notif.is_read = True
    await db.commit()
    await db.refresh(notif)
    return notif


async def mark_all_read(
    db: AsyncSession,
    user_id: str | uuid.UUID,
) -> int:
    """Mark all unread notifications as read. Returns count of updated rows."""
    from sqlalchemy import update

    uid = uuid.UUID(str(user_id)) if not isinstance(user_id, uuid.UUID) else user_id

    result = await db.execute(
        update(Notification)
        .where(Notification.user_id == uid, Notification.is_read == False)  # noqa: E712
        .values(is_read=True)
    )
    await db.commit()
    return result.rowcount


async def get_unread_count(
    db: AsyncSession,
    user_id: str | uuid.UUID,
) -> int:
    """Return the count of unread notifications for a user."""
    from sqlalchemy import func

    uid = uuid.UUID(str(user_id)) if not isinstance(user_id, uuid.UUID) else user_id

    result = await db.execute(
        select(func.count()).where(
            Notification.user_id == uid,
            Notification.is_read == False,  # noqa: E712
        )
    )
    return result.scalar_one()


async def delete_notification(
    db: AsyncSession,
    notification_id: str | uuid.UUID,
    user_id: str | uuid.UUID,
) -> bool:
    """Delete a notification. Returns True if deleted, False if not found/not owned."""
    nid = uuid.UUID(str(notification_id)) if not isinstance(notification_id, uuid.UUID) else notification_id
    uid = uuid.UUID(str(user_id)) if not isinstance(user_id, uuid.UUID) else user_id

    result = await db.execute(
        select(Notification).where(
            Notification.id == nid,
            Notification.user_id == uid,
        )
    )
    notif = result.scalar_one_or_none()
    if notif is None:
        return False

    await db.delete(notif)
    await db.commit()
    return True
