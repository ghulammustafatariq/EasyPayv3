"""
app/services/admin_service.py — EasyPay v3.0 Admin Service (B17)

Functions:
  get_dashboard_stats    — real-time system metrics
  get_chart_data         — daily transaction volume for last N days
  block_user             — deactivate + freeze + notify + log
  unblock_user           — reactivate + conditional unfreeze + notify + log
  delete_user            — soft delete + log
  override_tier          — change KYC tier + update daily limit + log
  reverse_transaction    — atomic reversal with db.begin() (Point 18)

Security rules enforced:
  Rule 14 — Every admin action MUST be logged to admin_actions table.
  Rule 15 — get_current_admin dependency (JWT + X-Admin-Key) enforced at route layer.
  Point 18 — reverse_transaction uses async with db.begin() + .with_for_update().
  AdminSelfActionError raised when admin_id == target_user_id.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.core.exceptions import AdminSelfActionError, ReversalError
from app.models.database import (
    AdminAction,
    FraudFlag,
    Notification,
    Transaction,
    User,
    Wallet,
)
from app.services import notification_service

logger = logging.getLogger("easypay.admin")

# Tier → daily limit (mirrors TIER_DAILY_LIMITS in dependencies.py)
_TIER_DAILY_LIMITS: dict[int, Decimal] = {
    0: Decimal("0.00"),
    1: Decimal("25000.00"),
    2: Decimal("100000.00"),
    3: Decimal("500000.00"),
    4: Decimal("2000000.00"),
}


# ── Internal helper: log an admin action ──────────────────────────────────────

async def _log_admin_action(
    db: AsyncSession,
    admin_id: uuid.UUID,
    action_type: str,
    reason: str,
    *,
    target_user_id: uuid.UUID | None = None,
    target_txn_id: uuid.UUID | None = None,
    target_biz_id: uuid.UUID | None = None,
    metadata: dict | None = None,
) -> AdminAction:
    """
    Rule 14: Persist every admin action to admin_actions.
    Flushes but does NOT commit — caller commits after the full action.
    """
    action = AdminAction(
        admin_id=admin_id,
        action_type=action_type,
        target_user_id=target_user_id,
        target_txn_id=target_txn_id,
        target_biz_id=target_biz_id,
        reason=reason,
        action_metadata=metadata or {},
    )
    db.add(action)
    await db.flush()
    logger.info(
        "AdminAction: admin=%s action=%s target_user=%s target_txn=%s",
        admin_id, action_type, target_user_id, target_txn_id,
    )
    return action


# ══════════════════════════════════════════════════════════════════════════════
# DASHBOARD STATS
# ══════════════════════════════════════════════════════════════════════════════

async def get_dashboard_stats(db: AsyncSession) -> dict[str, Any]:
    """
    Return real-time system-wide metrics for the admin dashboard.

    Metrics:
      total_users, active_today (last_login_at today), new_today (created today),
      total_transactions_today, transaction_volume_today, pending_kyc_reviews,
      active_fraud_alerts, total_system_balance
    """
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Total users
    total_users_res = await db.execute(select(func.count(User.id)))
    total_users = total_users_res.scalar_one()

    # Active today (logged in today)
    active_today_res = await db.execute(
        select(func.count(User.id)).where(User.last_login_at >= today_start)
    )
    active_today = active_today_res.scalar_one()

    # New registrations today
    new_today_res = await db.execute(
        select(func.count(User.id)).where(User.created_at >= today_start)
    )
    new_today = new_today_res.scalar_one()

    # Transactions today
    txn_count_res = await db.execute(
        select(func.count(Transaction.id)).where(
            Transaction.created_at >= today_start,
            Transaction.status == "completed",
        )
    )
    total_transactions_today = txn_count_res.scalar_one()

    # Transaction volume today
    txn_vol_res = await db.execute(
        select(func.coalesce(func.sum(Transaction.amount), Decimal("0.00"))).where(
            Transaction.created_at >= today_start,
            Transaction.status == "completed",
        )
    )
    transaction_volume_today = txn_vol_res.scalar_one()

    # Pending KYC reviews (CNIC + biometric verified but business still pending/under_review)
    pending_kyc_res = await db.execute(
        select(func.count(User.id)).where(
            User.cnic_verified == True,
            User.biometric_verified == True,
            User.business_status.in_(["pending", "under_review"]),
        )
    )
    pending_kyc_reviews = pending_kyc_res.scalar_one()

    # Active fraud alerts
    active_fraud_res = await db.execute(
        select(func.count(FraudFlag.id)).where(FraudFlag.status == "active")
    )
    active_fraud_alerts = active_fraud_res.scalar_one()

    # Total system balance (sum of all wallet balances)
    total_balance_res = await db.execute(
        select(func.coalesce(func.sum(Wallet.balance), Decimal("0.00")))
    )
    total_system_balance = total_balance_res.scalar_one()

    return {
        "total_users": total_users,
        "active_today": active_today,
        "new_today": new_today,
        "total_transactions_today": total_transactions_today,
        "transaction_volume_today": str(transaction_volume_today),
        "pending_kyc_reviews": pending_kyc_reviews,
        "active_fraud_alerts": active_fraud_alerts,
        "total_system_balance": str(total_system_balance),
        "generated_at": now.isoformat(),
    }


async def get_chart_data(
    db: AsyncSession,
    days: int = 30,
) -> list[dict[str, Any]]:
    """
    Return daily transaction count and volume for the last N days.
    Each entry: {date, transaction_count, volume_pkr}
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    result = await db.execute(
        select(
            func.date(Transaction.created_at).label("txn_date"),
            func.count(Transaction.id).label("transaction_count"),
            func.coalesce(func.sum(Transaction.amount), Decimal("0.00")).label("volume"),
        )
        .where(
            Transaction.created_at >= cutoff,
            Transaction.status == "completed",
        )
        .group_by(func.date(Transaction.created_at))
        .order_by(func.date(Transaction.created_at).asc())
    )

    rows = result.all()
    return [
        {
            "date": str(row.txn_date),
            "transaction_count": row.transaction_count,
            "volume_pkr": str(row.volume),
        }
        for row in rows
    ]


# ══════════════════════════════════════════════════════════════════════════════
# USER MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

async def block_user(
    db: AsyncSession,
    admin_id: uuid.UUID,
    target_user_id: uuid.UUID,
    reason: str,
) -> None:
    """
    Block a user:
      1. Raise AdminSelfActionError if admin targets themselves.
      2. Set user.is_active=False, user.is_locked=True.
      3. Freeze wallet.
      4. Send FCM notification to user.
      5. Log to admin_actions.
    """
    if admin_id == target_user_id:
        raise AdminSelfActionError("Admins cannot block their own account.")

    result = await db.execute(
        select(User).where(User.id == target_user_id).options(joinedload(User.wallet))
    )
    user = result.scalar_one_or_none()
    if not user:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="User not found.")

    user.is_active = False
    user.is_locked = True

    if user.wallet:
        user.wallet.is_frozen = True

    await db.flush()

    await _log_admin_action(
        db,
        admin_id=admin_id,
        action_type="block_user",
        reason=reason,
        target_user_id=target_user_id,
        metadata={"risk_score": user.risk_score},
    )
    await db.commit()

    await notification_service.create_notification(
        db,
        user_id=target_user_id,
        title="Account Suspended",
        body="Your account has been suspended by an administrator. Please contact support.",
        type="security",
        data={"reason": reason},
    )

    logger.info("Admin %s blocked user %s — reason: %s", admin_id, target_user_id, reason)


async def unblock_user(
    db: AsyncSession,
    admin_id: uuid.UUID,
    target_user_id: uuid.UUID,
    reason: str,
) -> None:
    """
    Unblock a user:
      1. Set user.is_active=True, user.is_locked=False.
      2. Unfreeze wallet ONLY IF no active Critical fraud flags remain.
      3. Send FCM notification to user.
      4. Log to admin_actions.
    """
    result = await db.execute(
        select(User).where(User.id == target_user_id).options(joinedload(User.wallet))
    )
    user = result.scalar_one_or_none()
    if not user:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="User not found.")

    user.is_active = True
    user.is_locked = False

    # Unfreeze wallet only if no active Critical fraud flags
    critical_flags_res = await db.execute(
        select(func.count(FraudFlag.id)).where(
            FraudFlag.user_id == target_user_id,
            FraudFlag.severity == "Critical",
            FraudFlag.status == "active",
        )
    )
    critical_count = critical_flags_res.scalar_one()

    wallet_unfrozen = False
    if critical_count == 0 and user.wallet:
        user.wallet.is_frozen = False
        wallet_unfrozen = True

    await db.flush()

    await _log_admin_action(
        db,
        admin_id=admin_id,
        action_type="unblock_user",
        reason=reason,
        target_user_id=target_user_id,
        metadata={"wallet_unfrozen": wallet_unfrozen, "critical_flags_remaining": critical_count},
    )
    await db.commit()

    body = "Your account has been reinstated."
    if not wallet_unfrozen:
        body += " Note: Your wallet remains frozen pending fraud review."

    await notification_service.create_notification(
        db,
        user_id=target_user_id,
        title="Account Reinstated",
        body=body,
        type="security",
        data={"wallet_unfrozen": wallet_unfrozen},
    )

    logger.info("Admin %s unblocked user %s — wallet_unfrozen=%s", admin_id, target_user_id, wallet_unfrozen)


async def delete_user(
    db: AsyncSession,
    admin_id: uuid.UUID,
    target_user_id: uuid.UUID,
    reason: str,
) -> None:
    """
    Soft-delete a user (is_active=False). Data is preserved.
    Raises AdminSelfActionError if admin targets themselves.
    """
    if admin_id == target_user_id:
        raise AdminSelfActionError("Admins cannot delete their own account.")

    result = await db.execute(select(User).where(User.id == target_user_id))
    user = result.scalar_one_or_none()
    if not user:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="User not found.")

    user.is_active = False

    await _log_admin_action(
        db,
        admin_id=admin_id,
        action_type="delete_user",
        reason=reason,
        target_user_id=target_user_id,
    )
    await db.commit()

    logger.info("Admin %s soft-deleted user %s — reason: %s", admin_id, target_user_id, reason)


async def override_tier(
    db: AsyncSession,
    admin_id: uuid.UUID,
    target_user_id: uuid.UUID,
    new_tier: int,
    reason: str,
) -> None:
    """
    Override a user's KYC verification tier and update their wallet daily limit.
    """
    if new_tier not in _TIER_DAILY_LIMITS:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=f"Invalid tier: {new_tier}. Must be 0–4.")

    result = await db.execute(
        select(User).where(User.id == target_user_id).options(joinedload(User.wallet))
    )
    user = result.scalar_one_or_none()
    if not user:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="User not found.")

    old_tier = user.verification_tier
    user.verification_tier = new_tier

    if user.wallet:
        user.wallet.daily_limit = _TIER_DAILY_LIMITS[new_tier]

    await _log_admin_action(
        db,
        admin_id=admin_id,
        action_type="override_tier",
        reason=reason,
        target_user_id=target_user_id,
        metadata={"old_tier": old_tier, "new_tier": new_tier},
    )
    await db.commit()

    logger.info(
        "Admin %s overrode tier for user %s: %s → %s",
        admin_id, target_user_id, old_tier, new_tier,
    )


# ══════════════════════════════════════════════════════════════════════════════
# TRANSACTION REVERSAL
# ══════════════════════════════════════════════════════════════════════════════

async def reverse_transaction(
    db: AsyncSession,
    admin_id: uuid.UUID,
    transaction_id: uuid.UUID,
    reason: str,
) -> dict[str, Any]:
    """
    Atomically reverse a completed transaction.

    Point 18 — MUST use async with db.begin() + .with_for_update() to prevent
    double-reversal race conditions.

    Steps:
      1. Lock the transaction row.
      2. Verify status == "completed" (raise ReversalError if already reversed/failed).
      3. Credit the sender's wallet: balance += transaction.amount.
      4. Set transaction.status = "reversed", reversed_by, reversal_reason.
      5. Notify the sender via FCM.
      6. Log to admin_actions.

    Returns:
        dict with reversal details.
    """
    async with db.begin():
        # ── 1. Fetch + lock transaction ───────────────────────────────────────
        txn_result = await db.execute(
            select(Transaction)
            .where(Transaction.id == transaction_id)
            .with_for_update()
        )
        txn = txn_result.scalar_one_or_none()

        if not txn:
            raise ReversalError("Transaction not found.")

        # ── 2. Guard: must be completed, not already reversed ─────────────────
        if txn.status != "completed":
            raise ReversalError(
                f"Transaction cannot be reversed — current status: {txn.status}."
            )

        # ── 3. Credit sender wallet ───────────────────────────────────────────
        if txn.sender_id:
            wallet_result = await db.execute(
                select(Wallet)
                .where(Wallet.user_id == txn.sender_id)
                .with_for_update()
            )
            wallet = wallet_result.scalar_one_or_none()
            if wallet:
                wallet.balance += txn.amount

        # ── 4. Update transaction ─────────────────────────────────────────────
        txn.status = "reversed"
        txn.reversed_by = admin_id
        txn.reversal_reason = reason

        # ── 5. Log admin action (inside the transaction) ──────────────────────
        action = AdminAction(
            admin_id=admin_id,
            action_type="reverse_transaction",
            target_txn_id=transaction_id,
            target_user_id=txn.sender_id,
            reason=reason,
            action_metadata={
                "amount": str(txn.amount),
                "reference_number": txn.reference_number,
            },
        )
        db.add(action)

    # ── 6. Notify sender (outside db.begin() — non-fatal) ────────────────────
    if txn.sender_id:
        await notification_service.create_notification(
            db,
            user_id=txn.sender_id,
            title="Transaction Reversed",
            body=f"PKR {txn.amount:,.2f} has been refunded to your wallet — reversed by admin.",
            type="transaction",
            data={
                "transaction_id": str(transaction_id),
                "amount": str(txn.amount),
                "reference_number": txn.reference_number,
            },
        )

    logger.info(
        "Admin %s reversed transaction %s (PKR %s) — reason: %s",
        admin_id, transaction_id, txn.amount, reason,
    )

    return {
        "transaction_id": str(transaction_id),
        "reference_number": txn.reference_number,
        "amount": str(txn.amount),
        "status": "reversed",
        "reversed_by": str(admin_id),
        "reason": reason,
    }
