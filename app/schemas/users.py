"""
app/schemas/users.py — EasyPay v3.0 User Schemas

Rule 2: NEVER include password_hash, pin_hash, or cnic_encrypted.
All response schemas use model_config = {"from_attributes": True} for ORM compatibility.
"""
import uuid
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, EmailStr, Field


# ══════════════════════════════════════════════════════════════════════════════
# RESPONSE SCHEMAS
# ══════════════════════════════════════════════════════════════════════════════

class UserResponse(BaseModel):
    """
    Full public user profile.

    CRITICAL (Rule 2): password_hash, pin_hash, cnic_encrypted are
    INTENTIONALLY ABSENT. Never add them here.
    """

    id: uuid.UUID
    phone_number: str
    email: str
    full_name: str
    is_verified: bool
    is_active: bool
    is_locked: bool
    is_superuser: bool
    login_attempts: int
    biometric_enabled: bool
    profile_photo_url: Optional[str] = None
    account_type: str
    verification_tier: int
    # KYC status fields (safe to expose — booleans only, no raw data)
    cnic_verified: bool
    biometric_verified: bool
    fingerprint_verified: bool
    nadra_verified: bool
    business_status: Optional[str] = None
    # Fraud / risk (safe meta — no PII)
    risk_score: int
    is_flagged: bool
    has_pin: bool = False

    model_config = {"from_attributes": True}


class UserUpdateRequest(BaseModel):
    """Fields that a user is allowed to update themselves."""

    email: Optional[EmailStr] = Field(None, examples=["newemail@example.com"])
    full_name: Optional[str] = Field(None, min_length=2, max_length=100, examples=["Ali Khan"])
    profile_photo_url: Optional[str] = Field(None, examples=["https://res.cloudinary.com/..."])


class UserSearchResponse(BaseModel):
    """
    Minimal user info returned by recipient-search endpoint.
    Phone number is masked to protect privacy:  e.g. "+92300****567"
    """

    id: uuid.UUID
    full_name: str
    phone_number: str  # already masked at the service layer
    profile_photo_url: Optional[str] = None

    model_config = {"from_attributes": True}


class VerificationStatusResponse(BaseModel):
    """
    Current KYC/verification state and the resulting daily transaction limits.
    Returned by GET /users/verification-status.
    """

    verification_tier: int = Field(..., ge=0, le=4)
    is_verified: bool = False
    cnic_verified: bool
    biometric_verified: bool
    fingerprint_verified: bool
    nadra_verified: bool
    business_status: Optional[str] = None
    daily_limit: Decimal = Field(..., description="PKR daily send limit for current tier")
    daily_spent: Decimal = Field(..., description="PKR spent today (resets at midnight PKT)")

    model_config = {"from_attributes": True}
