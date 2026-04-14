"""
app/services/wallet_service.py — EasyPay v3.0 Wallet Service

Critical Points observed:
  Point 1: ALL queries use await db.execute(select(...)) — NEVER db.query()
  Rule  6: Wallet balance NEVER goes below PKR 0.00
  Rule  5: Mutations that touch balance use async with db.begin() +
           with_for_update() (enforced at the transaction-service layer)
           check_and_reset_daily_limit commits ONLY when a reset occurs.

Transaction history note:
  The transactions table has sender_id / recipient_id (user IDs), NOT wallet IDs.
  History is queried via wallet.user_id, not wallet.id.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import List

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import TIER_DAILY_LIMITS
from app.models.database import Transaction, Wallet

logger = logging.getLogger("easypay")


# ══════════════════════════════════════════════════════════════════════════════
# INTERNAL HELPER
# ══════════════════════════════════════════════════════════════════════════════

def _parse_uuid(user_id: str | uuid.UUID) -> uuid.UUID:
    if isinstance(user_id, uuid.UUID):
        return user_id
    return uuid.UUID(str(user_id))


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC SERVICE FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

async def get_wallet(
    db: AsyncSession,
    user_id: str | uuid.UUID,
) -> Wallet:
    """
    Fetch the wallet that belongs to user_id.
    Raises HTTPException 404 if none exists.
    """
    uid = _parse_uuid(user_id)
    result = await db.execute(select(Wallet).where(Wallet.user_id == uid))
    wallet = result.scalars().first()
    if not wallet:
        raise HTTPException(status_code=404, detail="Wallet not found.")
    return wallet


async def get_wallet_summary(
    db: AsyncSession,
    user_id: str | uuid.UUID,
) -> dict:
    """
    Fetch the user's wallet and their 10 most recent transactions.

    Transaction history note: the transactions table references users via
    sender_id / recipient_id (both UUIDs pointing to users.id), not wallet IDs.
    We therefore filter by wallet.user_id on both sides.

    Returns a dict that maps 1-to-1 onto WalletSummaryResponse.
    """
    uid = _parse_uuid(user_id)

    # ── 1. Fetch wallet ───────────────────────────────────────────────────────
    wallet = await get_wallet(db, uid)

    # ── 2. Check / reset daily limit before returning summary ─────────────────
    await check_and_reset_daily_limit(db, wallet)

    # ── 3. Fetch last 10 transactions (sent or received by this user) ─────────
    # Point 1: await db.execute(select(...))
    txn_result = await db.execute(
        select(Transaction)
        .where(
            or_(
                Transaction.sender_id == uid,
                Transaction.recipient_id == uid,
            )
        )
        .order_by(Transaction.created_at.desc())
        .limit(10)
    )
    recent: List[Transaction] = list(txn_result.scalars().all())

    return {
        "id": wallet.id,
        "balance": wallet.balance,
        "currency": wallet.currency,
        "is_frozen": wallet.is_frozen,
        "daily_limit": wallet.daily_limit,
        "daily_spent": wallet.daily_spent,
        "limit_reset_at": wallet.limit_reset_at,
        "recent_transactions": recent,
    }


async def check_and_reset_daily_limit(
    db: AsyncSession,
    wallet: Wallet,
) -> None:
    """
    Internal utility: reset daily_spent to zero when the 24-hour window expires.

    Algorithm:
      1. Normalise limit_reset_at to a timezone-aware UTC datetime.
      2. If it is in the past, reset daily_spent to 0.00 and push
         limit_reset_at forward by exactly 24 hours from now.
      3. Commit ONLY when a reset actually occurs (avoids spurious commits).

    This function is called before every wallet read that exposes daily_spent
    so the client always sees an up-to-date value.
    """
    now_utc = datetime.now(timezone.utc)

    reset_at = wallet.limit_reset_at
    # Normalise to timezone-aware if the DB returned a naïve datetime
    if reset_at is not None and reset_at.tzinfo is None:
        reset_at = reset_at.replace(tzinfo=timezone.utc)

    if reset_at is None or reset_at < now_utc:
        wallet.daily_spent = Decimal("0.00")
        wallet.limit_reset_at = now_utc + timedelta(hours=24)
        await db.commit()
        logger.info(
            "Daily limit reset for wallet %s; next reset at %s",
            wallet.id,
            wallet.limit_reset_at,
        )


# ── B08 spec alias ────────────────────────────────────────────────────────────
reset_daily_limit_if_needed = check_and_reset_daily_limit


async def check_transaction_allowed(
    wallet: Wallet,
    amount: Decimal,
    user_tier: int,
) -> None:
    """
    Validate that a proposed transaction is allowed for this wallet and tier.

    Checks (in order):
      1. Wallet is not frozen.
      2. Sufficient balance (balance >= amount).
      3. Tier daily limit not exceeded (daily_spent + amount <= tier limit).

    Raises:
        WalletFrozenError          — wallet.is_frozen is True.
        InsufficientBalanceError   — balance < amount.
        DailyLimitExceededError    — would exceed tier daily limit.

    Note: does NOT commit anything. Call reset_daily_limit_if_needed() first
    so daily_spent reflects the current window.
    """
    from app.core.exceptions import (
        DailyLimitExceededError,
        InsufficientBalanceError,
        WalletFrozenError,
    )

    if wallet.is_frozen:
        raise WalletFrozenError()

    if wallet.balance < amount:
        raise InsufficientBalanceError(
            detail=(
                f"Wallet balance is PKR {wallet.balance:,.2f}. "
                f"You need PKR {amount:,.2f}."
            ),
        )

    allowed = wallet.daily_limit or TIER_DAILY_LIMITS.get(user_tier, Decimal("0.00"))
    if allowed == Decimal("0.00"):
        raise DailyLimitExceededError(
            detail="Your account is not verified. Complete KYC to enable transfers.",
            error_code="WALLET_TIER_NOT_VERIFIED",
        )
    if wallet.daily_spent + amount > allowed:
        remaining = allowed - wallet.daily_spent
        raise DailyLimitExceededError(
            detail=(
                f"This transfer would exceed your daily limit of PKR {allowed:,.2f}. "
                f"You have PKR {remaining:,.2f} remaining today."
            ),
        )
