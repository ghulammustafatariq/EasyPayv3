"""
app/api/v1/banking.py — EasyPay v3.0 Bank Account Endpoints

Routes (B15):
  POST   /banking/otp/request        → send bank_linking OTP to user email
  GET    /banking/accounts           → list linked bank accounts
  POST   /banking/accounts           → link new bank account (OTP required)
  DELETE /banking/accounts/{id}      → unlink a bank account
  PATCH  /banking/accounts/{id}/primary → set as primary account

All routes require JWT (get_current_user).
"""
import uuid

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_db
from app.models.database import User
from app.schemas.base import success_response
from app.services import banking_service, auth_service

router = APIRouter(prefix="/banking", tags=["Banking"])


# ══════════════════════════════════════════════════════════════════════════════
# POST /banking/otp/request
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/otp/request", status_code=status.HTTP_200_OK)
async def request_bank_otp(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Send a bank_linking OTP to the authenticated user's registered email.
    The OTP must be submitted when calling POST /banking/accounts.
    """
    email_hint = await auth_service.request_bank_linking_otp(db, current_user)
    return success_response(
        message="OTP sent to your registered email address.",
        data={"email_hint": email_hint},
    )


# ── Request models ─────────────────────────────────────────────────────────────

class LinkBankAccountRequest(BaseModel):
    bank_name: str = Field(..., min_length=2, max_length=50, examples=["HBL"])
    account_number: str = Field(..., min_length=4, max_length=30, examples=["1234567890"])
    account_title: str = Field(..., min_length=2, max_length=100, examples=["Ali Khan"])
    otp_code: str = Field(..., min_length=6, max_length=6, examples=["123456"])


# ══════════════════════════════════════════════════════════════════════════════
# GET /banking/accounts
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/accounts", status_code=status.HTTP_200_OK)
async def list_bank_accounts(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Return all bank accounts linked to the authenticated user.
    Account numbers are masked (only last 4 digits shown).
    """
    accounts = await banking_service.get_user_bank_accounts(db, current_user.id)
    return success_response(
        message="Bank accounts retrieved successfully.",
        data={"accounts": accounts, "count": len(accounts)},
    )


# ══════════════════════════════════════════════════════════════════════════════
# POST /banking/accounts
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/accounts", status_code=status.HTTP_200_OK)
async def link_bank_account(
    body: LinkBankAccountRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Link a new bank account.

    Requirements:
      • A valid bank_linking OTP.
      • Maximum 3 accounts per user.

    The account number is masked before storage.
    The first linked account is automatically set as primary.
    """
    account = await banking_service.link_bank_account(
        db,
        current_user.id,
        bank_name=body.bank_name,
        account_number=body.account_number,
        account_title=body.account_title,
        otp_code=body.otp_code,
    )
    return success_response(
        message="Bank account linked successfully.",
        data=account,
    )


# ══════════════════════════════════════════════════════════════════════════════
# DELETE /banking/accounts/{account_id}
# ══════════════════════════════════════════════════════════════════════════════

@router.delete("/accounts/{account_id}", status_code=status.HTTP_200_OK)
async def unlink_bank_account(
    account_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Unlink a bank account.

    If the deleted account was the primary, the oldest remaining account is
    automatically promoted to primary.
    """
    await banking_service.unlink_bank_account(db, current_user.id, account_id)
    return success_response(
        message="Bank account unlinked successfully.",
        data={},
    )


# ══════════════════════════════════════════════════════════════════════════════
# PATCH /banking/accounts/{account_id}/primary
# ══════════════════════════════════════════════════════════════════════════════

@router.patch("/accounts/{account_id}/primary", status_code=status.HTTP_200_OK)
async def set_primary_account(
    account_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Mark a bank account as primary.
    All other accounts for this user will have is_primary set to False.
    """
    account = await banking_service.set_primary_account(db, current_user.id, account_id)
    return success_response(
        message="Primary bank account updated successfully.",
        data=account,
    )
