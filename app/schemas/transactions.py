"""
app/schemas/transactions.py — EasyPay v3.0 Transaction Schemas

Rule 5: send-money operations use async with db.begin() + with_for_update().
        These schemas are the boundary layer — amount > 0 enforced here.
Rule 6: balance NEVER goes below 0.00 (amount ge=0.01 enforced).
Point 8: BiometricConfirmRequest carries the pending_tx_token that must be
         verified (type=="pending_transaction" AND not expired) before money moves.
"""
import uuid
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


# ══════════════════════════════════════════════════════════════════════════════
# REQUEST SCHEMAS
# ══════════════════════════════════════════════════════════════════════════════

class SendMoneyRequest(BaseModel):
    """
    Initiate a peer-to-peer transfer.

    Auth: provide EITHER pin OR biometric_token (not both required).
      - biometric_token="local_device_success" → hardware biometric mock
      - pin="XXXX" → 4-digit transaction PIN (Bcrypt verified)
    If neither is supplied the request is rejected with 400.
    """

    recipient_identifier: str = Field(
        ...,
        description="Recipient's phone number (+923XXXXXXXXX) or user UUID",
        examples=["+923001234567"],
    )
    amount: Decimal = Field(..., gt=0, decimal_places=2, examples=["500.00"])
    note: Optional[str] = Field(None, max_length=255, examples=["Dinner split"])
    idempotency_key: Optional[str] = Field(
        None,
        max_length=100,
        description="Unique client key to prevent duplicate submissions",
    )
    pin: Optional[str] = Field(
        None,
        pattern=r"^\d{4}$",
        description="4-digit transaction PIN — required if biometric_token is absent",
    )
    biometric_token: Optional[str] = Field(
        None,
        description=(
            "Hardware biometric mock token from the mobile device. "
            "Pass 'local_device_success' to authorise via biometric."
        ),
    )


class ExternalTransferRequest(BaseModel):
    """Send money to an external bank account (inter-bank transfer)."""

    bank_code: str = Field(..., max_length=10, examples=["HBL"])
    account_number: str = Field(..., min_length=4, max_length=30, examples=["****1234"])
    amount: Decimal = Field(..., gt=0, decimal_places=2, examples=["10000.00"])
    pin: str = Field(..., pattern=r"^\d{4}$", description="Transaction PIN")
    note: Optional[str] = Field(None, max_length=255)
    idempotency_key: Optional[str] = Field(None, max_length=100)


class TopUpRequest(BaseModel):
    """Mobile network top-up (Jazz, Telenor, Zong, Ufone, Warid)."""

    phone_number: str = Field(
        ...,
        description="Mobile number to top up (can differ from sender's number)",
        examples=["03001234567"],
    )
    amount: Decimal = Field(..., gt=0, decimal_places=2, examples=["200.00"])
    network: str = Field(
        ...,
        description="Mobile network operator",
        examples=["Jazz"],
    )
    pin: str = Field(..., pattern=r"^\d{4}$", description="Transaction PIN")

    @field_validator("network")
    @classmethod
    def validate_network(cls, v: str) -> str:
        allowed = {"Jazz", "Telenor", "Zong", "Ufone", "Warid"}
        if v not in allowed:
            raise ValueError(f"Network must be one of: {', '.join(sorted(allowed))}")
        return v


class BillPayRequest(BaseModel):
    """Utility / bill payment."""

    consumer_number: str = Field(
        ...,
        min_length=4,
        max_length=30,
        description="Consumer / reference number on the bill",
        examples=["1234567890"],
    )
    company: str = Field(
        ...,
        max_length=100,
        description="Biller company name or code",
        examples=["LESCO"],
    )
    amount: Decimal = Field(..., gt=0, decimal_places=2, examples=["2500.00"])
    pin: str = Field(..., pattern=r"^\d{4}$", description="Transaction PIN")


class BiometricConfirmRequest(BaseModel):
    """
    Confirm a pending transaction using biometric authentication.

    Point 8: pending_tx_token expires after 60 seconds.
             Service MUST call verify_pending_tx_token() which checks
             type == "pending_transaction" AND not expired.
    """

    pending_tx_token: str = Field(
        ...,
        description="JWT issued by the /transactions/send endpoint (60s expiry)",
    )


class TransactionHistoryRequest(BaseModel):
    """Pagination + filter parameters for GET /transactions/history."""

    page: int = Field(1, ge=1, description="1-based page number")
    per_page: int = Field(20, ge=1, le=100, description="Items per page (max 100)")
    tx_type: Optional[str] = Field(
        None,
        description="Filter by type: send | receive | topup | bill | bank_transfer | refund",
    )
    status: Optional[str] = Field(
        None,
        description="Filter by status: pending | completed | failed | reversed",
    )
    start_date: Optional[datetime] = Field(None, description="UTC ISO-8601 start filter")
    end_date: Optional[datetime] = Field(None, description="UTC ISO-8601 end filter")

    @field_validator("tx_type")
    @classmethod
    def validate_type(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        allowed = {"send", "receive", "topup", "bill", "bank_transfer", "refund"}
        if v not in allowed:
            raise ValueError(f"tx_type must be one of: {', '.join(sorted(allowed))}")
        return v

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        allowed = {"pending", "completed", "failed", "reversed"}
        if v not in allowed:
            raise ValueError(f"status must be one of: {', '.join(sorted(allowed))}")
        return v


# ══════════════════════════════════════════════════════════════════════════════
# RESPONSE SCHEMAS
# ══════════════════════════════════════════════════════════════════════════════

class TransactionResponse(BaseModel):
    """
    Full transaction record including v3 admin/fraud flag fields.
    Returned for individual lookups and history lists.
    """

    id: uuid.UUID
    reference_number: str
    sender_id: Optional[uuid.UUID] = None
    recipient_id: Optional[uuid.UUID] = None
    amount: Decimal
    fee: Decimal
    type: str
    status: str
    description: Optional[str] = None
    external_ref: Optional[str] = None
    tx_metadata: Optional[dict] = Field(None, alias="tx_metadata")
    idempotency_key: Optional[str] = None
    # v3 admin/fraud fields
    is_flagged: bool
    flag_reason: Optional[str] = None
    flagged_by: Optional[uuid.UUID] = None
    flagged_at: Optional[datetime] = None
    reversed_by: Optional[uuid.UUID] = None
    reversal_reason: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None

    model_config = {"from_attributes": True, "populate_by_name": True}


class PendingTransactionResponse(BaseModel):
    """
    Returned immediately by POST /transactions/send.
    Client must confirm via POST /transactions/biometric-confirm
    within 60 seconds (Point 8).
    """

    pending_tx_token: str = Field(
        ...,
        description="Short-lived JWT (60s). Pass to /transactions/biometric-confirm.",
    )
    expires_in: int = Field(60, description="Seconds until pending_tx_token expires")
    amount: Decimal = Field(..., description="PKR amount to be transferred")
    recipient_name: str = Field(..., description="Full name of the recipient")

    model_config = {"from_attributes": True}


class TopUpReceiptResponse(BaseModel):
    """Receipt returned by POST /transactions/topup."""

    reference_number: str
    phone_number: str
    network: str
    amount: Decimal
    status: str = "completed"
    created_at: datetime

    model_config = {"from_attributes": True}


class BillReceiptResponse(BaseModel):
    """Receipt returned by POST /transactions/bills/pay."""

    reference_number: str
    company: str
    consumer_number: str
    amount: Decimal
    status: str = "completed"
    bill_info: dict
    created_at: datetime

    model_config = {"from_attributes": True}


class ExternalTransferReceiptResponse(BaseModel):
    """Receipt returned by POST /transactions/send-external."""

    reference_number: str
    bank_code: str
    account_number: str
    amount: Decimal
    status: str = "completed"
    created_at: datetime

    model_config = {"from_attributes": True}


class TransactionListResponse(BaseModel):
    """Paginated list of transactions returned by GET /transactions/history."""

    items: List[TransactionResponse]
    total: int
    page: int
    per_page: int
    has_next: bool


class DailyLimitStatusResponse(BaseModel):
    """Daily spend / limit data returned by GET /users/daily-limit-status."""

    verification_tier: int
    daily_limit: Decimal
    spent_today: Decimal
    remaining: Decimal
    resets_at: Optional[str] = Field(
        None,
        description="ISO-8601 UTC timestamp when the daily window resets (midnight)",
    )
