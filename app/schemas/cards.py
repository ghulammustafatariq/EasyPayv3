"""
app/schemas/cards.py — EasyPay v3.0 Card System Schemas (B24)

All money fields use Decimal — NEVER float.
CardDetailsResponse is SENSITIVE: returned once at issue (virtual) or
on explicit GET /details call (requires PIN).  CVV shown once, then "•••".
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import UUID4, BaseModel, field_validator


# ── Request schemas ──────────────────────────────────────────────────────────

class CardIssueRequest(BaseModel):
    card_type: str                          # "virtual" or "physical"
    delivery_address: Optional[str] = None  # required if physical
    card_holder_name: Optional[str] = None  # defaults to user.full_name.upper()


class UpdateLimitsRequest(BaseModel):
    daily_limit: Optional[Decimal] = None
    monthly_limit: Optional[Decimal] = None


class UpdateSettingsRequest(BaseModel):
    is_online_enabled: Optional[bool] = None
    is_contactless_enabled: Optional[bool] = None


class BlockCardRequest(BaseModel):
    reason: str                             # mandatory


class ReplaceCardRequest(BaseModel):
    reason: str                             # "lost", "stolen", "damaged"
    delivery_address: Optional[str] = None  # for physical replacement


# ── Response schemas ─────────────────────────────────────────────────────────

class CardResponse(BaseModel):
    id: UUID4
    card_number_masked: str                 # "4276 **** **** 1234"
    card_holder_name: str
    expiry_month: int
    expiry_year: str                        # "29" (2-digit)
    card_type: str
    status: str
    is_frozen: bool
    is_online_enabled: bool
    is_contactless_enabled: bool
    daily_limit: Decimal
    monthly_limit: Decimal
    daily_spent: Decimal
    monthly_spent: Decimal
    delivery_status: Optional[str] = None
    delivery_tracking_id: Optional[str] = None
    estimated_delivery_at: Optional[datetime] = None
    issued_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("expiry_year", mode="before")
    @classmethod
    def coerce_expiry_year(cls, v: object) -> str:
        """DB stores expiry_year as int (e.g. 2029); convert to 2-digit string."""
        return str(v)[-2:]


class CardDetailsResponse(BaseModel):
    """
    SENSITIVE — only returned once from issue (virtual) or GET /details (PIN required).
    After first GET /details call, cvv_encrypted is deleted from DB.
    CVV displayed as '•••' on any subsequent /details call.
    """
    card_number: str                        # full formatted "4276 XXXX XXXX XXXX"
    cvv: str                                # "XXX" or "•••" after first reveal
    expiry_month: int
    expiry_year: str                        # "29"
    card_holder_name: str

    @field_validator("expiry_year", mode="before")
    @classmethod
    def coerce_expiry_year(cls, v: object) -> str:
        return str(v)[-2:]


class CardIssueResponse(BaseModel):
    card: CardResponse
    details: Optional[CardDetailsResponse] = None   # virtual only (one-time)
    message: str


class CardTransactionResponse(BaseModel):
    id: UUID4
    amount: Decimal
    merchant_name: Optional[str] = None
    description: Optional[str] = None
    status: str
    type: str
    created_at: datetime

    model_config = {"from_attributes": True}
