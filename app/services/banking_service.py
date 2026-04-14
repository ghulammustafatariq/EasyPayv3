"""
app/services/banking_service.py — EasyPay v3.0 Bank Account Service

B15 functionality:
  • get_user_bank_accounts — list all linked bank accounts
  • link_bank_account      — link new account (OTP required, max 3)
  • unlink_bank_account    — remove a linked account
  • set_primary_account    — mark one account as primary

Security rules:
  Rule  1 — OTP verified with Bcrypt before any bank write action.
  Point 1 — All queries use await db.execute(select(...)) — NEVER db.query().
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import security
from app.models.database import BankAccount, OTPCode, User

logger = logging.getLogger("easypay")

# Maximum linked bank accounts per user.
_MAX_BANK_ACCOUNTS = 3


# ── Internal OTP verification (mirrors user_service helper) ──────────────────

async def _verify_bank_otp(
    db: AsyncSession,
    user_id: uuid.UUID,
    otp_code: str,
) -> None:
    """
    Verify a bank_linking OTP.  Marks it used on success.

    Raises HTTPException 400 on invalid/expired OTP.
    """
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(OTPCode)
        .where(
            OTPCode.user_id == user_id,
            OTPCode.purpose == "bank_linking",
            OTPCode.is_used == False,
            OTPCode.expires_at > now,
        )
        .order_by(OTPCode.created_at.desc())
        .limit(1)
    )
    otp_record = result.scalar_one_or_none()

    if not otp_record:
        raise HTTPException(
            status_code=400,
            detail="Bank linking OTP not found or expired. Please request a new one.",
        )

    if not security.verify_otp(otp_code, otp_record.code_hash):
        raise HTTPException(status_code=400, detail="Invalid OTP code.")

    otp_record.is_used = True
    await db.commit()


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC SERVICE FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

async def get_user_bank_accounts(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> list[dict]:
    """
    Return all bank accounts linked to the given user, ordered by created_at.
    """
    result = await db.execute(
        select(BankAccount)
        .where(BankAccount.user_id == user_id)
        .order_by(BankAccount.created_at.asc())
    )
    accounts = result.scalars().all()
    return [
        {
            "id": str(a.id),
            "bank_name": a.bank_name,
            "account_number_masked": a.account_number_masked,
            "account_title": a.account_title,
            "is_primary": a.is_primary,
            "is_verified": a.is_verified,
            "created_at": a.created_at.isoformat(),
        }
        for a in accounts
    ]


async def link_bank_account(
    db: AsyncSession,
    user_id: uuid.UUID,
    bank_name: str,
    account_number: str,
    account_title: str,
    otp_code: str,
) -> dict:
    """
    Link a new bank account after OTP verification.

    Rules:
      • Max 3 bank accounts per user (raises 400 if exceeded).
      • OTP purpose must be "bank_linking".
      • account_number is masked before storage (last 4 digits shown: ****1234).
      • First linked account is automatically set as primary.

    Returns the new BankAccount as a dict.
    """
    await _verify_bank_otp(db, user_id, otp_code)

    # ── Enforce max 3 limit ───────────────────────────────────────────────────
    count_result = await db.execute(
        select(BankAccount).where(BankAccount.user_id == user_id)
    )
    existing = count_result.scalars().all()
    if len(existing) >= _MAX_BANK_ACCOUNTS:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum of {_MAX_BANK_ACCOUNTS} bank accounts allowed per user.",
        )

    # ── Mask account number (keep only last 4 digits) ─────────────────────────
    masked = "****" + account_number[-4:] if len(account_number) >= 4 else "****"

    # ── First account is automatically primary ────────────────────────────────
    is_first = len(existing) == 0

    account = BankAccount(
        user_id=user_id,
        bank_name=bank_name,
        account_number_masked=masked,
        account_title=account_title,
        is_primary=is_first,
        is_verified=False,  # Bank verification happens externally
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)

    logger.info(
        "Bank account linked for user %s: %s %s is_primary=%s",
        user_id, bank_name, masked, is_first,
    )
    return {
        "id": str(account.id),
        "bank_name": account.bank_name,
        "account_number_masked": account.account_number_masked,
        "account_title": account.account_title,
        "is_primary": account.is_primary,
        "is_verified": account.is_verified,
        "created_at": account.created_at.isoformat(),
    }


async def unlink_bank_account(
    db: AsyncSession,
    user_id: uuid.UUID,
    account_id: uuid.UUID,
) -> None:
    """
    Remove a bank account owned by the user.

    Raises 404 if the account does not exist or belongs to another user.
    If the deleted account was primary and other accounts exist,
    the oldest remaining account is promoted to primary.
    """
    result = await db.execute(
        select(BankAccount).where(
            BankAccount.id == account_id,
            BankAccount.user_id == user_id,
        )
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Bank account not found.")

    was_primary = account.is_primary
    await db.delete(account)
    await db.flush()

    # ── Promote oldest remaining account if primary was deleted ───────────────
    if was_primary:
        remaining_result = await db.execute(
            select(BankAccount)
            .where(BankAccount.user_id == user_id)
            .order_by(BankAccount.created_at.asc())
            .limit(1)
        )
        next_primary = remaining_result.scalar_one_or_none()
        if next_primary:
            next_primary.is_primary = True

    await db.commit()
    logger.info("Bank account %s unlinked for user %s", account_id, user_id)


async def set_primary_account(
    db: AsyncSession,
    user_id: uuid.UUID,
    account_id: uuid.UUID,
) -> dict:
    """
    Mark a bank account as primary, clearing the flag on all others.

    Raises 404 if the account does not exist or belongs to another user.
    """
    # Fetch all accounts for the user
    all_result = await db.execute(
        select(BankAccount).where(BankAccount.user_id == user_id)
    )
    all_accounts = all_result.scalars().all()

    target = next((a for a in all_accounts if a.id == account_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="Bank account not found.")

    for acc in all_accounts:
        acc.is_primary = acc.id == account_id

    await db.commit()
    await db.refresh(target)

    logger.info("Primary bank account set to %s for user %s", account_id, user_id)
    return {
        "id": str(target.id),
        "bank_name": target.bank_name,
        "account_number_masked": target.account_number_masked,
        "account_title": target.account_title,
        "is_primary": target.is_primary,
        "is_verified": target.is_verified,
        "created_at": target.created_at.isoformat(),
    }
