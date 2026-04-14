"""
app/schemas/notifications.py — EasyPay v3.0 Notification Schemas

Rule 11: An FCM push is ALWAYS sent alongside every in-app notification.
         FCM delivery is handled at the service layer; these schemas represent
         the persisted notification records returned by the API.
"""
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class NotificationResponse(BaseModel):
    """
    A single notification record from the notifications table.
    type is one of: transaction | security | system | ai_insight | admin
    """

    id: uuid.UUID
    title: str
    body: str
    type: str = Field(
        ...,
        description="transaction | security | system | ai_insight | admin",
    )
    is_read: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class UnreadCountResponse(BaseModel):
    """Lightweight response for the unread-count badge endpoint."""

    count: int = Field(..., ge=0, description="Number of unread notifications")

    model_config = {"from_attributes": True}
