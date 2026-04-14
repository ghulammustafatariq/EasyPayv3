"""
app/schemas/admin.py — EasyPay v3.0 Admin Schemas

Rules enforced:
  Rule 15: ALL admin routes require BOTH valid admin JWT AND X-Admin-Key header
           (enforced in dependencies.get_current_admin, not in these schemas).
  Rule 16: Every admin action includes a 'reason' field — validated non-empty here.
  Rule 18: Admin self-action guard lives in route handlers (AdminActionBase
           does NOT enforce it — that requires the current user context).
  Rule 19: Reversal is atomic; AdminReversalRequest carries the reason logged
           to admin_actions table before the reversal transaction runs.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


# ── Shared validator  ─────────────────────────────────────────────────────────

def _require_reason(v: str) -> str:
    """Rule 16: reason must not be blank."""
    if not v or not v.strip():
        raise ValueError("A reason is required for all admin actions (ADMIN_REASON_REQUIRED).")
    return v.strip()


# ══════════════════════════════════════════════════════════════════════════════
# USER MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

class AdminUserListResponse(BaseModel):
    """
    Minimal user record for the admin user list table.
    CRITICAL (Rule 2): password_hash / pin_hash / cnic_encrypted NEVER included.
    """

    id: uuid.UUID
    phone_number: str
    email: str
    full_name: str
    is_verified: bool
    is_active: bool
    is_locked: bool
    is_superuser: bool
    account_type: str
    verification_tier: int
    risk_score: int
    is_flagged: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class AdminUserDetailResponse(BaseModel):
    """
    Full admin view of a single user — includes KYC and fraud columns.
    CRITICAL (Rule 2): password_hash / pin_hash / cnic_encrypted still absent.
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
    # KYC
    cnic_verified: bool
    biometric_verified: bool
    fingerprint_verified: bool
    nadra_verified: bool
    cnic_front_url: Optional[str] = None
    cnic_back_url: Optional[str] = None
    liveness_selfie_url: Optional[str] = None
    # Business
    account_type: str
    business_status: Optional[str] = None
    verification_tier: int
    daily_limit_override: Optional[Decimal] = None
    # Fraud
    risk_score: int
    is_flagged: bool
    flag_reason: Optional[str] = None
    # Timestamps
    created_at: datetime
    last_login_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class AdminBlockRequest(BaseModel):
    """Block or unblock a user account. Rule 16: reason required."""

    reason: str = Field(..., min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, v: str) -> str:
        return _require_reason(v)


class AdminTierOverrideRequest(BaseModel):
    """Override a user's verification tier. Rule 16: reason required."""

    tier: int = Field(..., ge=0, le=4, description="New verification tier (0–4)")
    reason: str = Field(..., min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, v: str) -> str:
        return _require_reason(v)


class AdminKYCActionRequest(BaseModel):
    """Approve or reject a KYC submission. Rule 16: reason always required."""

    reason: str = Field(..., min_length=1, max_length=500)
    rejection_reasons: List[str] = Field(
        default_factory=list,
        description="Per-document rejection notes (populated on rejection only)",
    )

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, v: str) -> str:
        return _require_reason(v)


# ══════════════════════════════════════════════════════════════════════════════
# TRANSACTION MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

class AdminTransactionListResponse(BaseModel):
    """Transaction record as shown in the admin panel."""

    id: uuid.UUID
    reference_number: str
    sender_id: Optional[uuid.UUID] = None
    recipient_id: Optional[uuid.UUID] = None
    amount: Decimal
    fee: Decimal
    type: str
    status: str
    description: Optional[str] = None
    is_flagged: bool
    flag_reason: Optional[str] = None
    flagged_by: Optional[uuid.UUID] = None
    flagged_at: Optional[datetime] = None
    reversed_by: Optional[uuid.UUID] = None
    reversal_reason: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class AdminFlagTransactionRequest(BaseModel):
    """Manually flag a transaction for review. Rule 16: reason required."""

    reason: str = Field(..., min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, v: str) -> str:
        return _require_reason(v)


class AdminReversalRequest(BaseModel):
    """
    Request a transaction reversal.
    Rule 19: Reversal is ATOMIC — full credit back to sender or nothing.
    The service must use async with db.begin() + with_for_update().
    Rule 16: reason required and is logged to admin_actions.
    """

    reason: str = Field(..., min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, v: str) -> str:
        return _require_reason(v)


# ══════════════════════════════════════════════════════════════════════════════
# BUSINESS VERIFICATION
# ══════════════════════════════════════════════════════════════════════════════

class AdminBusinessActionRequest(BaseModel):
    """
    Approve or reject a business profile submission.
    Rule 14: Backend must reject if ai_confidence_score < 0.85 for auto-approve.
    Rule 16: reason required.
    """

    reason: str = Field(..., min_length=1, max_length=500)
    rejection_reasons: List[str] = Field(
        default_factory=list,
        description="Specific document issues (populated for rejections)",
    )

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, v: str) -> str:
        return _require_reason(v)


# ══════════════════════════════════════════════════════════════════════════════
# FRAUD FLAGS
# ══════════════════════════════════════════════════════════════════════════════

class FraudFlagResponse(BaseModel):
    """A fraud flag record from the fraud_flags table."""

    id: uuid.UUID
    user_id: uuid.UUID
    transaction_id: Optional[uuid.UUID] = None
    rule_triggered: str
    severity: str = Field(..., description="Low | Medium | High | Critical")
    details: dict = Field(default_factory=dict)
    status: str = Field(..., description="active | resolved | escalated")
    resolved_by: Optional[uuid.UUID] = None
    resolved_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class FraudResolveRequest(BaseModel):
    """Resolve or escalate a fraud flag. Rule 16: notes serve as the reason."""

    notes: str = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="Resolution notes — required for audit trail (Rule 16)",
    )

    @field_validator("notes")
    @classmethod
    def validate_notes(cls, v: str) -> str:
        return _require_reason(v)


# ══════════════════════════════════════════════════════════════════════════════
# DASHBOARD & ANALYTICS
# ══════════════════════════════════════════════════════════════════════════════

class AdminDashboardStats(BaseModel):
    """Aggregate stats for the admin dashboard home page."""

    total_users: int = Field(..., ge=0)
    active_today: int = Field(..., ge=0, description="Users who logged in today")
    new_today: int = Field(..., ge=0, description="Accounts created today")
    total_transactions_today: int = Field(..., ge=0)
    transaction_volume_today: Decimal = Field(
        ..., ge=0, description="PKR total transaction volume today"
    )
    pending_kyc_reviews: int = Field(..., ge=0)
    active_fraud_alerts: int = Field(..., ge=0, description="Fraud flags with status=active")
    total_system_balance: Decimal = Field(
        ..., ge=0, description="Sum of all wallet balances in PKR"
    )

    model_config = {"from_attributes": True}


class AdminChartDataPoint(BaseModel):
    """A single data point in the transaction volume chart."""

    day: date = Field(..., description="Calendar date (YYYY-MM-DD)")
    transaction_count: int = Field(..., ge=0)
    volume: Decimal = Field(..., ge=0, description="PKR volume on this date")

    model_config = {"from_attributes": True}


class AdminChartData(BaseModel):
    """
    Time-series chart data for the admin analytics view.
    items is a list of AdminChartDataPoint ordered by date ASC.
    """

    items: List[AdminChartDataPoint] = Field(default_factory=list)

    model_config = {"from_attributes": True}


# ══════════════════════════════════════════════════════════════════════════════
# BROADCAST / ANNOUNCEMENTS
# ══════════════════════════════════════════════════════════════════════════════

class BroadcastRequest(BaseModel):
    """
    Send a system announcement to a segment of users.
    FCM push is sent to every matched user (Rule 11).
    """

    title: str = Field(..., min_length=1, max_length=100, examples=["System Maintenance"])
    body: str = Field(..., min_length=1, examples=["EasyPay will be offline 2AM–3AM PKT."])
    segment: str = Field(
        ...,
        description="all | tier1_only | tier3_plus | business_only",
        examples=["all"],
    )

    @field_validator("segment")
    @classmethod
    def validate_segment(cls, v: str) -> str:
        allowed = {"all", "tier1_only", "tier3_plus", "business_only"}
        if v not in allowed:
            raise ValueError(f"segment must be one of: {', '.join(sorted(allowed))}")
        return v
