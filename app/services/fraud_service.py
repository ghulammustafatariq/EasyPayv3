"""
app/services/fraud_service.py — EasyPay v3.0 Fraud Detection Engine (B16)

6 Fraud Rules:
  1. HIGH_AMOUNT      — amount > 80,000 PKR                  → Medium  (+10)
  2. HIGH_VELOCITY    — > 5 sends in last 10 minutes          → High    (+20)
  3. FAILED_PINS      — sender.login_attempts >= 3            → High    (+20)
  4. NEW_ACCOUNT_LARGE— account < 24 hrs old AND amount > 10k → Critical(+30)
  5. ROUND_NUMBERS    — last 5 txns all round numbers         → Low     (+5)
  6. NIGHT_ACTIVITY   — PKT time 02:00–05:00                  → Low     (+5)

If risk_score > 80 after updates:
  • Auto-freeze wallet
  • Create admin notification (system type)
  • Send FCM to admin if fcm_token exists

Point 19 — evaluate_transaction() MUST run after the money-movement commit,
            never inside the db.begin() block.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import FraudFlag, Transaction, User, Wallet
from app.services import notification_service

logger = logging.getLogger("easypay.fraud")

# ── Severity → score weight ───────────────────────────────────────────────────
_WEIGHTS: dict[str, int] = {
    "Critical": 30,
    "High": 20,
    "Medium": 10,
    "Low": 5,
}

_RISK_FREEZE_THRESHOLD = 80

# Pakistan Standard Time offset (UTC+5)
_PKT_OFFSET = timedelta(hours=5)


# ══════════════════════════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ══════════════════════════════════════════════════════════════════════════════

async def _create_fraud_flag(
    db: AsyncSession,
    user_id: uuid.UUID,
    transaction_id: uuid.UUID | None,
    rule_triggered: str,
    severity: str,
    details: dict,
) -> FraudFlag:
    """
    Insert a FraudFlag record and mark the transaction as flagged.
    Flushes but does NOT commit — caller commits after all rules run.
    """
    flag = FraudFlag(
        user_id=user_id,
        transaction_id=transaction_id,
        rule_triggered=rule_triggered,
        severity=severity,
        details=details,
        status="active",
    )
    db.add(flag)

    # Mark the transaction itself as flagged
    if transaction_id:
        txn_result = await db.execute(
            select(Transaction).where(Transaction.id == transaction_id)
        )
        txn = txn_result.scalar_one_or_none()
        if txn:
            txn.is_flagged = True
            txn.flag_reason = rule_triggered

    logger.info(
        "FraudFlag created: rule=%s severity=%s user=%s tx=%s",
        rule_triggered, severity, user_id, transaction_id,
    )
    await db.flush()
    return flag


async def _get_active_flags(
    db: AsyncSession,
    severity_filter: str | None = None,
) -> list[FraudFlag]:
    """Return active FraudFlag records sorted by severity DESC, created_at DESC."""
    severity_order = {"Critical": 4, "High": 3, "Medium": 2, "Low": 1}

    stmt = select(FraudFlag).where(FraudFlag.status == "active")
    if severity_filter:
        stmt = stmt.where(FraudFlag.severity == severity_filter)

    result = await db.execute(stmt)
    flags = result.scalars().all()

    return sorted(
        flags,
        key=lambda f: (severity_order.get(f.severity, 0), f.created_at),
        reverse=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
# 6 FRAUD RULES
# ══════════════════════════════════════════════════════════════════════════════

def _rule_high_amount(transaction: Transaction) -> bool:
    """Rule 1: transaction.amount > 80,000 PKR."""
    return transaction.amount > Decimal("80000")


async def _rule_high_velocity(
    db: AsyncSession, sender_id: uuid.UUID
) -> bool:
    """Rule 2: more than 5 outgoing sends in the last 10 minutes."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
    result = await db.execute(
        select(func.count(Transaction.id)).where(
            Transaction.sender_id == sender_id,
            Transaction.type == "send",
            Transaction.status == "completed",
            Transaction.created_at >= cutoff,
        )
    )
    count = result.scalar_one()
    return count > 5


def _rule_failed_pins(sender: User) -> bool:
    """Rule 3: sender has 3 or more failed login attempts."""
    return sender.login_attempts >= 3


def _rule_new_account_large(sender: User, transaction: Transaction) -> bool:
    """Rule 4: account < 24 hrs old AND transaction amount > 10,000 PKR."""
    now = datetime.now(timezone.utc)
    created = sender.created_at
    # Ensure timezone-aware comparison
    if hasattr(created, "tzinfo") and created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    account_age = now - created
    return account_age < timedelta(hours=24) and transaction.amount > Decimal("10000")


async def _rule_round_numbers(
    db: AsyncSession, sender_id: uuid.UUID
) -> bool:
    """Rule 5: last 5 completed sends are all round numbers (divisible by 100)."""
    result = await db.execute(
        select(Transaction.amount)
        .where(
            Transaction.sender_id == sender_id,
            Transaction.type == "send",
            Transaction.status == "completed",
        )
        .order_by(Transaction.created_at.desc())
        .limit(5)
    )
    amounts = [row[0] for row in result.all()]
    if len(amounts) < 5:
        return False
    return all(a % 100 == 0 for a in amounts)


def _rule_night_activity() -> bool:
    """Rule 6: current PKT time is between 02:00 and 05:00."""
    pkt_now = datetime.now(timezone.utc) + _PKT_OFFSET
    return 2 <= pkt_now.hour < 5


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════════════════════════════════════

async def evaluate_transaction(
    db: AsyncSession,
    transaction: Transaction,
    sender: User,
) -> None:
    """
    Evaluate a completed transaction against all 6 fraud rules.

    Point 19 — Must run AFTER the money-movement commit, never inside
    the db.begin() block.

    For each triggered rule:
      1. Create a FraudFlag record.
      2. Increment sender.risk_score by the rule's weight (cap at 100).

    If risk_score > 80 after all rules:
      • Freeze the wallet.
      • Send admin notification + FCM.
    """
    triggered_any = False

    # ── Rule 1: HIGH_AMOUNT ───────────────────────────────────────────────────
    if _rule_high_amount(transaction):
        await _create_fraud_flag(
            db,
            user_id=sender.id,
            transaction_id=transaction.id,
            rule_triggered="HIGH_AMOUNT",
            severity="Medium",
            details={"amount": str(transaction.amount), "threshold": "80000"},
        )
        sender.risk_score = min(100, sender.risk_score + _WEIGHTS["Medium"])
        triggered_any = True

    # ── Rule 2: HIGH_VELOCITY ─────────────────────────────────────────────────
    if await _rule_high_velocity(db, sender.id):
        await _create_fraud_flag(
            db,
            user_id=sender.id,
            transaction_id=transaction.id,
            rule_triggered="HIGH_VELOCITY",
            severity="High",
            details={"window_minutes": 10, "tx_id": str(transaction.id)},
        )
        sender.risk_score = min(100, sender.risk_score + _WEIGHTS["High"])
        triggered_any = True

    # ── Rule 3: FAILED_PINS ───────────────────────────────────────────────────
    if _rule_failed_pins(sender):
        await _create_fraud_flag(
            db,
            user_id=sender.id,
            transaction_id=transaction.id,
            rule_triggered="FAILED_PINS",
            severity="High",
            details={"login_attempts": sender.login_attempts},
        )
        sender.risk_score = min(100, sender.risk_score + _WEIGHTS["High"])
        triggered_any = True

    # ── Rule 4: NEW_ACCOUNT_LARGE ─────────────────────────────────────────────
    if _rule_new_account_large(sender, transaction):
        await _create_fraud_flag(
            db,
            user_id=sender.id,
            transaction_id=transaction.id,
            rule_triggered="NEW_ACCOUNT_LARGE",
            severity="Critical",
            details={
                "amount": str(transaction.amount),
                "account_age_hours": round(
                    (datetime.now(timezone.utc) - (
                        sender.created_at if sender.created_at.tzinfo
                        else sender.created_at.replace(tzinfo=timezone.utc)
                    )).total_seconds() / 3600,
                    2,
                ),
            },
        )
        sender.risk_score = min(100, sender.risk_score + _WEIGHTS["Critical"])
        triggered_any = True

    # ── Rule 5: ROUND_NUMBERS ─────────────────────────────────────────────────
    if await _rule_round_numbers(db, sender.id):
        await _create_fraud_flag(
            db,
            user_id=sender.id,
            transaction_id=transaction.id,
            rule_triggered="ROUND_NUMBERS",
            severity="Low",
            details={"note": "Last 5 sends are all round numbers (divisible by 100)"},
        )
        sender.risk_score = min(100, sender.risk_score + _WEIGHTS["Low"])
        triggered_any = True

    # ── Rule 6: NIGHT_ACTIVITY ────────────────────────────────────────────────
    if _rule_night_activity():
        pkt_time = (datetime.now(timezone.utc) + _PKT_OFFSET).strftime("%H:%M PKT")
        await _create_fraud_flag(
            db,
            user_id=sender.id,
            transaction_id=transaction.id,
            rule_triggered="NIGHT_ACTIVITY",
            severity="Low",
            details={"pkt_time": pkt_time, "window": "02:00–05:00 PKT"},
        )
        sender.risk_score = min(100, sender.risk_score + _WEIGHTS["Low"])
        triggered_any = True

    if not triggered_any:
        return

    # ── Persist updated risk_score ────────────────────────────────────────────
    await db.commit()

    # ── Auto-freeze if risk_score > 80 ────────────────────────────────────────
    if sender.risk_score > _RISK_FREEZE_THRESHOLD:
        wallet_result = await db.execute(
            select(Wallet).where(Wallet.user_id == sender.id)
        )
        wallet = wallet_result.scalar_one_or_none()
        if wallet and not wallet.is_frozen:
            wallet.is_frozen = True
            await db.commit()
            logger.warning(
                "Wallet auto-frozen for user %s (risk_score=%s)",
                sender.id, sender.risk_score,
            )

        # ── Notify the user their wallet has been frozen ───────────────────────
        await notification_service.create_notification(
            db,
            user_id=sender.id,
            title="Account Suspended",
            body=(
                "Your wallet has been temporarily frozen due to suspicious activity. "
                "Please contact support to resolve this."
            ),
            type="security",
            data={
                "risk_score": sender.risk_score,
                "tx_id": str(transaction.id),
            },
        )

        # ── Notify admin ──────────────────────────────────────────────────────
        admin_result = await db.execute(
            select(User).where(User.is_superuser == True, User.is_active == True).limit(1)
        )
        admin = admin_result.scalar_one_or_none()
        if admin:
            await notification_service.create_notification(
                db,
                user_id=admin.id,
                title="Critical: Auto-Flagged User",
                body=(
                    f"User {sender.phone_number} has been auto-flagged and wallet frozen. "
                    f"Risk score: {sender.risk_score}/100."
                ),
                type="system",
                data={
                    "flagged_user_id": str(sender.id),
                    "risk_score": sender.risk_score,
                    "tx_id": str(transaction.id),
                },
            )


async def get_active_flags(
    db: AsyncSession,
    severity_filter: str | None = None,
) -> list[dict]:
    """
    Return all active fraud flags sorted by severity DESC, created_at DESC.
    Used by admin endpoints (B19).
    """
    flags = await _get_active_flags(db, severity_filter)
    return [
        {
            "id": str(f.id),
            "user_id": str(f.user_id),
            "transaction_id": str(f.transaction_id) if f.transaction_id else None,
            "rule_triggered": f.rule_triggered,
            "severity": f.severity,
            "details": f.details,
            "status": f.status,
            "created_at": f.created_at.isoformat(),
        }
        for f in flags
    ]
