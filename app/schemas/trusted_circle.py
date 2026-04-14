from pydantic import BaseModel
from decimal import Decimal
from typing import Optional
from uuid import UUID
from datetime import datetime


class TrustedCircleSettingsRequest(BaseModel):
    is_enabled: bool
    require_pin_for_non_circle: bool = True
    notify_on_non_circle: bool = True
    max_non_circle_amount: Optional[Decimal] = None  # None = block all non-circle


class AddContactRequest(BaseModel):
    phone_number: str  # search contact by phone


class ContactResponse(BaseModel):
    contact_id: UUID
    full_name: str
    phone_number: str
    verification_tier: int
    added_at: datetime

    model_config = {"from_attributes": True}


class TrustedCircleSettingsResponse(BaseModel):
    is_enabled: bool
    require_pin_for_non_circle: bool
    notify_on_non_circle: bool
    max_non_circle_amount: Optional[Decimal] = None
    contacts: list[ContactResponse]

    model_config = {"from_attributes": True}


class NonCircleCheckResponse(BaseModel):
    is_in_circle: bool
    circle_enabled: bool
    requires_extra_confirmation: bool
    max_allowed_amount: Optional[Decimal] = None
