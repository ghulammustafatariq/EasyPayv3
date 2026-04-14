"""
app/services/transaction_service.py â€” EasyPay v3.0 P2P Transfer Service

ACID Compliance:
  Point 3 â€” Balance mutations wrapped in `async with db.begin()`.
             BOTH wallets fetched inside that block with .with_for_update()
             to acquire row-level locks.  Missing this = double-spend risk.
  Rule  5 â€” Balance NEVER goes below 0.00.
  Point 19 â€” Fraud detection runs AFTER commit, outside db.begin() block.

Auth modes:
  biometric_token == "local_device_success" â†’ hardware biometric mock (mobile)
  pin provided                              â†’ Bcrypt verify against sender.pin_hash
  Neither                                   â†’ 400 Bad Request

Biometric pending flow (B08 spec):
  amount >= PKR 1,000 â†’ create pending_tx_token (60s JWT) and return
                         PendingTransactionResponse immediately.
  Client calls confirm_biometric_transaction() within 60 s to complete.

Daily limits (COPILOT_AGENT_RULES â¿):
  Tier 0 â†’ PKR       0 (unverified â€” no transfers allowed)
  Tier 1 â†’ PKR  25,000
  Tier 2 â†’ PKR 100,000
  Tier 3 â†’ PKR 500,000
  Tier 4 â†’ PKR 2,000,000
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.core import security
from app.core.exceptions import (
    DailyLimitExceededError,
    EasyPayException,
    InsufficientBalanceError,
    PINInvalidError,
    RecipientNotFoundError,
    WalletFrozenError,
)
from app.models.database import Notification, Transaction, User, Wallet
from app.schemas.transactions import (
    PendingTransactionResponse,
    SendMoneyRequest,
    TransactionResponse,
)
from app.services import fraud_service, wallet_service

logger = logging.getLogger("easypay")

# â”€â”€ Daily limits mirror COPILOT_AGENT_RULES â¿ exactly â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
_TIER_DAILY_LIMITS: dict[int, Decimal] = {
    0: Decimal("0.00"),
    1: Decimal("25000.00"),
    2: Decimal("100000.00"),
    3: Decimal("500000.00"),
    4: Decimal("2000000.00"),
}

# Hardware biometric mock sentinel from mobile SDK
_BIOMETRIC_MOCK_SUCCESS = "local_device_success"

# Transfers >= PKR 1,000 require biometric confirmation (Point 8)
_BIOMETRIC_THRESHOLD = Decimal("1000.00")


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# INTERNAL HELPERS
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def _parse_uuid(user_id: str | uuid.UUID) -> uuid.UUID:
    if isinstance(user_id, uuid.UUID):
        return user_id
    return uuid.UUID(str(user_id))


def _is_phone(identifier: str) -> bool:
    """Return True if identifier looks like a Pakistani phone number."""
    return identifier.startswith("+") or identifier.startswith("0")


async def _fetch_user_with_wallet(
    db: AsyncSession,
    uid: uuid.UUID,
    not_found_message: str = "User not found.",
) -> User:
    """
    Point 1: await db.execute(select(...).options(joinedload(...))).
    Raises HTTPException 404 if the user does not exist.
    """
    result = await db.execute(
        select(User)
        .where(User.id == uid)
        .options(joinedload(User.wallet))
    )
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail=not_found_message)
    return user


async def _resolve_recipient(db: AsyncSession, identifier: str) -> User:
    """Resolve recipient by phone number or UUID string."""
    if _is_phone(identifier):
        result = await db.execute(
            select(User)
            .where(User.phone_number == identifier)
            .options(joinedload(User.wallet))
        )
        recipient = result.scalars().first()
    else:
        try:
            recipient_uuid = uuid.UUID(identifier)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="recipient_identifier must be a valid phone number or UUID.",
            )
        recipient = await _fetch_user_with_wallet(db, recipient_uuid, "Recipient not found.")

    if not recipient:
        raise RecipientNotFoundError()
    if not recipient.is_active or not recipient.is_verified:
        raise RecipientNotFoundError(
            detail="Recipient account is not active or not verified.",
            error_code="RECIPIENT_INACTIVE",
        )
    return recipient


def _build_notification(
    user_id: uuid.UUID,
    title: str,
    body: str,
    notif_type: str,
    data: dict,
) -> Notification:
    return Notification(
        user_id=user_id,
        title=title,
        body=body,
        type=notif_type,
        data=data,
    )


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# send_money â€” main entry point
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

async def send_money(
    db: AsyncSession,
    sender_id: str | uuid.UUID,
    data: SendMoneyRequest,
) -> Transaction | PendingTransactionResponse:
    """
    Initiate a P2P transfer.

    If amount < PKR 1,000:
        Performs the transfer immediately (ACID, with_for_update) and returns
        the completed Transaction ORM object.

    If amount >= PKR 1,000:
        Creates a 60-second pending_tx_token and returns
        PendingTransactionResponse. Client must call
        confirm_biometric_transaction() to finalise.

    Steps (immediate path):
      1.  Fetch sender + wallet (404 guard).
      2.  Authenticate via biometric mock OR PIN.
      3.  Resolve recipient + wallet (404 guard).
      4.  Block self-transfers.
      5.  Reset daily limit window if expired.
      6.  Validate tier daily limit.
      7.  Validate sufficient balance.
      8.  Check both wallets not frozen.
      9.  Idempotency check.
     10.  async with db.begin(): re-fetch both wallets FOR UPDATE â†’ mutate
          balances â†’ insert Transaction â†’ insert 2Ã— Notification.
     11.  After commit: call fraud_service.evaluate_transaction() (Point 19).
     12.  Return ORM Transaction.
    """
    sender_uid = _parse_uuid(sender_id)

    # â”€â”€ 1. Fetch sender â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    sender = await _fetch_user_with_wallet(db, sender_uid, "Sender not found.")

    # â”€â”€ 2. Authenticate â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if data.biometric_token is not None:
        if data.biometric_token != _BIOMETRIC_MOCK_SUCCESS:
            raise EasyPayException(
                detail="Biometric authentication failed. Please try again.",
                error_code="BIOMETRIC_AUTH_FAILED",
            )
    elif data.pin is not None:
        if not sender.pin_hash:
            raise EasyPayException(
                detail="Transaction PIN is not set. Please set a PIN first.",
                error_code="PIN_NOT_SET",
            )
        if not security.verify_pin(data.pin, sender.pin_hash):
            raise PINInvalidError()
    else:
        raise HTTPException(
            status_code=400,
            detail="Must provide PIN or biometric_token to authorise this transfer.",
        )

    # â”€â”€ 3. Resolve recipient â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    recipient = await _resolve_recipient(db, data.recipient_identifier)

    # â”€â”€ 4. Self-transfer guard â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if sender_uid == recipient.id:
        raise EasyPayException(
            detail="You cannot transfer money to your own wallet.",
            error_code="SELF_TRANSFER_NOT_ALLOWED",
        )

    # â”€â”€ 5. Daily limit window reset â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    await wallet_service.reset_daily_limit_if_needed(db, sender.wallet)

    # â”€â”€ 6. Tier daily limit check â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    allowed_limit = _TIER_DAILY_LIMITS.get(sender.verification_tier, Decimal("0.00"))
    if allowed_limit == Decimal("0.00"):
        raise DailyLimitExceededError(
            detail="Your account is not verified. Complete KYC to enable transfers.",
            error_code="WALLET_TIER_NOT_VERIFIED",
        )
    if sender.wallet.daily_spent + data.amount > allowed_limit:
        remaining = allowed_limit - sender.wallet.daily_spent
        raise DailyLimitExceededError(
            detail=(
                f"This transfer would exceed your daily limit of "
                f"PKR {allowed_limit:,.2f}. "
                f"You have PKR {remaining:,.2f} remaining today."
            ),
        )

    # â”€â”€ 7. Balance check â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if sender.wallet.balance < data.amount:
        raise InsufficientBalanceError(
            detail=(
                f"Wallet balance is PKR {sender.wallet.balance:,.2f}. "
                f"You need PKR {data.amount:,.2f}."
            ),
        )

    # â”€â”€ 8. Frozen wallet checks â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if sender.wallet.is_frozen:
        raise WalletFrozenError()
    if recipient.wallet is None:
        raise RecipientNotFoundError(
            detail="Recipient does not have an active wallet.",
            error_code="RECIPIENT_NO_WALLET",
        )
    if recipient.wallet.is_frozen:
        raise EasyPayException(
            detail="Recipient wallet is frozen. Transfer cannot proceed.",
            error_code="RECIPIENT_WALLET_FROZEN",
        )

    # â”€â”€ Biometric pending flow â€” amounts >= PKR 1,000 (Point 8 / B08 spec) â”€â”€â”€
    if data.amount >= _BIOMETRIC_THRESHOLD:
        pending_token = security.create_pending_tx_token(
            sender_id=str(sender_uid),
            recipient_id=str(recipient.id),
            amount=float(data.amount),
        )
        return PendingTransactionResponse(
            pending_tx_token=pending_token,
            expires_in=60,
            amount=data.amount,
            recipient_name=recipient.full_name,
        )

    # â”€â”€ 9. Idempotency check â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if data.idempotency_key:
        existing = await db.execute(
            select(Transaction).where(
                Transaction.idempotency_key == data.idempotency_key
            )
        )
        existing_tx = existing.scalars().first()
        if existing_tx:
            logger.info(
                "Idempotency hit for key %s â€” returning existing tx %s",
                data.idempotency_key, existing_tx.id,
            )
            return existing_tx

    # â”€â”€ 10. ACID block: FOR UPDATE locks â†’ mutate balances â†’ persist â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    new_tx = await _execute_transfer(
        db=db,
        sender_uid=sender_uid,
        sender=sender,
        recipient=recipient,
        amount=data.amount,
        note=data.note,
        idempotency_key=data.idempotency_key,
        auth_method="biometric" if data.biometric_token else "pin",
    )

    # â”€â”€ 11. Fraud detection â€” AFTER commit (Point 19) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    try:
        await fraud_service.evaluate_transaction(db, new_tx, sender)
    except Exception as exc:  # noqa: BLE001 â€” fraud check must NEVER crash transfer
        logger.error("Fraud evaluation failed for tx %s: %s", new_tx.id, exc)

    return new_tx


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# confirm_biometric_transaction â€” finalise a pending transfer
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

async def confirm_biometric_transaction(
    db: AsyncSession,
    user_id: str | uuid.UUID,
    pending_tx_token: str,
) -> Transaction:
    """
    Complete a pending P2P transfer after biometric confirmation.

    Called by POST /transactions/confirm-biometric.

    Steps:
      1. Decode and validate pending_tx_token (60-second window, Point 8).
      2. Verify token belongs to the authenticated user (sender_id).
      3. Re-validate balance + daily limits (state may have changed in 60 s).
      4. Execute ACID transfer (same as send_money immediate path).
      5. Run fraud detection.
      6. Return completed Transaction.
    """
    uid = _parse_uuid(user_id)

    # â”€â”€ 1. Verify token â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    try:
        payload = security.verify_pending_tx_token(pending_tx_token)
    except JWTError:
        raise EasyPayException(
            detail="Biometric confirmation window has expired. Please initiate the transfer again.",
            error_code="BIOMETRIC_TOKEN_EXPIRED",
        )

    # â”€â”€ 2. Ownership check â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    token_sender_id = uuid.UUID(payload["sender_id"])
    if uid != token_sender_id:
        raise EasyPayException(
            detail="This confirmation token does not belong to your account.",
            error_code="AUTH_TOKEN_INVALID",
        )

    recipient_id = uuid.UUID(payload["recipient_id"])
    amount = Decimal(str(payload["amount"]))

    # â”€â”€ 3. Re-fetch both parties with current state â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    sender = await _fetch_user_with_wallet(db, uid, "Sender not found.")
    recipient = await _fetch_user_with_wallet(db, recipient_id, "Recipient not found.")

    # Re-validate (brief window means state likely unchanged, but must check)
    await wallet_service.reset_daily_limit_if_needed(db, sender.wallet)
    allowed_limit = _TIER_DAILY_LIMITS.get(sender.verification_tier, Decimal("0.00"))
    if sender.wallet.daily_spent + amount > allowed_limit:
        raise DailyLimitExceededError()
    if sender.wallet.balance < amount:
        raise InsufficientBalanceError()
    if sender.wallet.is_frozen:
        raise WalletFrozenError()

    # â”€â”€ 4. ACID transfer â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    new_tx = await _execute_transfer(
        db=db,
        sender_uid=uid,
        sender=sender,
        recipient=recipient,
        amount=amount,
        note=None,
        idempotency_key=None,
        auth_method="biometric",
    )

    # â”€â”€ 5. Fraud detection â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    try:
        await fraud_service.evaluate_transaction(db, new_tx, sender)
    except Exception as exc:  # noqa: BLE001
        logger.error("Fraud evaluation failed for tx %s: %s", new_tx.id, exc)

    return new_tx


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# _execute_transfer â€” ACID inner function
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

async def _execute_transfer(
    db: AsyncSession,
    sender_uid: uuid.UUID,
    sender: User,
    recipient: User,
    amount: Decimal,
    note: str | None,
    idempotency_key: str | None,
    auth_method: str,
) -> Transaction:
    """
    Point 3: ACID money movement inside async with db.begin().

    - Re-fetches both wallet rows with SELECT ... FOR UPDATE (row locks).
    - Applies balance mutations (Decimal arithmetic â€” no float).
    - Inserts Transaction record.
    - Inserts Notification for sender AND recipient (Rule 11 / B08 spec).
    - Commits atomically.

    Returns the refreshed Transaction ORM object.
    """
    reference = security.generate_reference()
    now_utc = datetime.now(timezone.utc)

    async with db.begin_nested():
        # â”€â”€ Re-fetch with row locks (Point 3) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        sender_wallet_result = await db.execute(
            select(Wallet).where(Wallet.user_id == sender_uid).with_for_update()
        )
        sender_wallet: Wallet = sender_wallet_result.scalars().first()

        recipient_wallet_result = await db.execute(
            select(Wallet).where(Wallet.user_id == recipient.id).with_for_update()
        )
        recipient_wallet: Wallet = recipient_wallet_result.scalars().first()

        # Final balance guard with locked rows
        if sender_wallet.balance < amount:
            raise InsufficientBalanceError(
                detail=(
                    f"Wallet balance is PKR {sender_wallet.balance:,.2f}. "
                    f"You need PKR {amount:,.2f}."
                ),
            )

        # â”€â”€ Balance mutations (Decimal â€” Rule 6) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        sender_wallet.balance -= amount
        sender_wallet.daily_spent += amount
        recipient_wallet.balance += amount

        # â”€â”€ Transaction record â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        new_tx = Transaction(
            reference_number=reference,
            sender_id=sender_uid,
            recipient_id=recipient.id,
            amount=amount,
            fee=Decimal("0.00"),
            type="send",
            status="completed",
            description=note,
            idempotency_key=idempotency_key,
            completed_at=now_utc,
            tx_metadata={
                "auth_method": auth_method,
                "sender_phone": sender.phone_number,
                "recipient_phone": recipient.phone_number,
            },
        )
        db.add(new_tx)

        # â”€â”€ Notifications for both parties (B08 spec) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        sender_notif = _build_notification(
            user_id=sender_uid,
            title="Transfer Sent",
            body=f"PKR {amount:,.2f} sent to {recipient.full_name}. Ref: {reference}",
            notif_type="transaction",
            data={"reference_number": reference, "amount": str(amount)},
        )
        recipient_notif = _build_notification(
            user_id=recipient.id,
            title="Money Received",
            body=f"PKR {amount:,.2f} received from {sender.full_name}. Ref: {reference}",
            notif_type="transaction",
            data={"reference_number": reference, "amount": str(amount)},
        )
        db.add(sender_notif)
        db.add(recipient_notif)
        # begin_nested() releases the SAVEPOINT on success

    await db.commit()
    # Refresh to get server-generated fields (created_at, id)
    await db.refresh(new_tx)

    logger.info(
        "P2P transfer: %s → %s | PKR %.2f | ref=%s",
        sender.phone_number, recipient.phone_number, amount, reference,
    )
    return new_tx


# ══════════════════════════════════════════════════════════════════════════════
# NETWORK_PREFIXES — mobile operator detection from phone prefix
# ══════════════════════════════════════════════════════════════════════════════

NETWORK_PREFIXES: dict[str, str] = {
    # Jazz / Warid
    "0300": "Jazz", "0301": "Jazz", "0302": "Jazz", "0303": "Jazz",
    "0304": "Jazz", "0305": "Jazz", "0306": "Jazz", "0307": "Jazz",
    "0308": "Jazz", "0309": "Jazz",
    "0311": "Jazz", "0312": "Jazz", "0313": "Jazz", "0314": "Jazz",
    "0315": "Jazz", "0316": "Jazz", "0317": "Jazz", "0318": "Jazz",
    "0319": "Jazz",
    # Telenor
    "0340": "Telenor", "0341": "Telenor", "0342": "Telenor",
    "0343": "Telenor", "0344": "Telenor", "0345": "Telenor",
    "0346": "Telenor", "0347": "Telenor", "0348": "Telenor",
    # Zong
    "0310": "Zong", "0320": "Zong", "0321": "Zong", "0322": "Zong",
    "0323": "Zong", "0324": "Zong", "0325": "Zong", "0326": "Zong",
    "0327": "Zong", "0328": "Zong", "0329": "Zong",
    # Ufone
    "0330": "Ufone", "0331": "Ufone", "0332": "Ufone", "0333": "Ufone",
    "0334": "Ufone", "0335": "Ufone", "0336": "Ufone", "0337": "Ufone",
    "0338": "Ufone", "0339": "Ufone",
}


def _detect_network(phone_number: str) -> str | None:
    """
    Extract the 04-digit prefix from a Pakistani mobile number and map to operator.
    Accepts formats: 03XXXXXXXXX or +923XXXXXXXXX.
    Returns None if the prefix is unrecognised.
    """
    # Normalise to local format
    if phone_number.startswith("+92"):
        phone_number = "0" + phone_number[3:]
    prefix = phone_number[:4]
    return NETWORK_PREFIXES.get(prefix)


# ══════════════════════════════════════════════════════════════════════════════
# send_external — simulated inter-bank / JazzCash transfer
# ══════════════════════════════════════════════════════════════════════════════

async def send_external(
    db: AsyncSession,
    user_id: str | uuid.UUID,
    data,  # ExternalTransferRequest
) -> Transaction:
    """
    Simulated external bank transfer (JazzCash / HBL / etc.).

    - PIN is verified by the route layer before this is called.
    - Deducts from sender wallet (ACID, FOR UPDATE).
    - Inserts Transaction(type='bank_transfer').
    - Inserts Notification for sender.
    - Runs fraud detection after commit.
    """
    uid = _parse_uuid(user_id)
    sender = await _fetch_user_with_wallet(db, uid, "Sender not found.")

    # Wallet guards
    await wallet_service.reset_daily_limit_if_needed(db, sender.wallet)
    allowed_limit = _TIER_DAILY_LIMITS.get(sender.verification_tier, Decimal("0.00"))
    if allowed_limit == Decimal("0.00"):
        raise DailyLimitExceededError(
            detail="Complete KYC to enable external transfers.",
            error_code="WALLET_TIER_NOT_VERIFIED",
        )
    if sender.wallet.daily_spent + data.amount > allowed_limit:
        remaining = allowed_limit - sender.wallet.daily_spent
        raise DailyLimitExceededError(
            detail=(
                f"Transfer would exceed daily limit of PKR {allowed_limit:,.2f}. "
                f"Remaining: PKR {remaining:,.2f}."
            ),
        )
    if sender.wallet.balance < data.amount:
        raise InsufficientBalanceError(
            detail=f"Balance PKR {sender.wallet.balance:,.2f}. Need PKR {data.amount:,.2f}.",
        )
    if sender.wallet.is_frozen:
        raise WalletFrozenError()

    # Idempotency
    if data.idempotency_key:
        result = await db.execute(
            select(Transaction).where(Transaction.idempotency_key == data.idempotency_key)
        )
        existing = result.scalars().first()
        if existing:
            return existing

    reference = security.generate_reference()
    now_utc = datetime.now(timezone.utc)

    async with db.begin():
        wallet_res = await db.execute(
            select(Wallet).where(Wallet.user_id == uid).with_for_update()
        )
        wallet: Wallet = wallet_res.scalars().first()

        if wallet.balance < data.amount:
            raise InsufficientBalanceError()

        wallet.balance -= data.amount
        wallet.daily_spent += data.amount

        new_tx = Transaction(
            reference_number=reference,
            sender_id=uid,
            recipient_id=None,
            amount=data.amount,
            fee=Decimal("0.00"),
            type="bank_transfer",
            status="completed",
            description=data.note,
            idempotency_key=data.idempotency_key,
            external_ref=f"{data.bank_code}:{data.account_number}",
            completed_at=now_utc,
            tx_metadata={
                "bank_code": data.bank_code,
                "account_number": data.account_number,
                "simulated": True,
            },
        )
        db.add(new_tx)
        db.add(Notification(
            user_id=uid,
            title="Bank Transfer Sent",
            body=(
                f"PKR {data.amount:,.2f} sent to {data.bank_code} "
                f"a/c {data.account_number[-4:].rjust(len(data.account_number), '*')}. "
                f"Ref: {reference}"
            ),
            type="transaction",
            data={"reference_number": reference, "amount": str(data.amount)},
        ))

    await db.refresh(new_tx)
    try:
        await fraud_service.evaluate_transaction(db, new_tx, sender)
    except Exception as exc:
        logger.error("Fraud evaluation failed for ext-tx %s: %s", new_tx.id, exc)

    logger.info("External transfer: %s → %s/%s | PKR %.2f | ref=%s",
                sender.phone_number, data.bank_code, data.account_number,
                data.amount, reference)
    return new_tx


# ══════════════════════════════════════════════════════════════════════════════
# process_topup — mobile network recharge
# ══════════════════════════════════════════════════════════════════════════════

async def process_topup(
    db: AsyncSession,
    user_id: str | uuid.UUID,
    data,  # TopUpRequest
) -> Transaction:
    """
    Mobile network top-up.

    Network is detected from NETWORK_PREFIXES if not explicitly given; the
    request schema already validates the explicit `network` field.

    - Deducts amount from sender wallet (ACID, FOR UPDATE).
    - Inserts Transaction(type='topup').
    - Inserts Notification for sender.
    """
    uid = _parse_uuid(user_id)
    sender = await _fetch_user_with_wallet(db, uid, "User not found.")

    # Auto-detect network if schema allows override (TopUpRequest has explicit network)
    network = data.network
    detected = _detect_network(data.phone_number)
    if detected and network != detected:
        # Log mismatch but trust explicit field from validated schema
        logger.debug("Network explicit=%s detected=%s for %s", network, detected, data.phone_number)

    # Wallet guards
    await wallet_service.reset_daily_limit_if_needed(db, sender.wallet)
    if sender.wallet.balance < data.amount:
        raise InsufficientBalanceError(
            detail=f"Balance PKR {sender.wallet.balance:,.2f}. Need PKR {data.amount:,.2f}.",
        )
    if sender.wallet.is_frozen:
        raise WalletFrozenError()

    reference = security.generate_reference()
    now_utc = datetime.now(timezone.utc)

    async with db.begin_nested():
        wallet_res = await db.execute(
            select(Wallet).where(Wallet.user_id == uid).with_for_update()
        )
        wallet: Wallet = wallet_res.scalars().first()

        if wallet.balance < data.amount:
            raise InsufficientBalanceError()

        wallet.balance -= data.amount
        # Top-ups count toward daily_spent
        wallet.daily_spent += data.amount

        new_tx = Transaction(
            reference_number=reference,
            sender_id=uid,
            recipient_id=None,
            amount=data.amount,
            fee=Decimal("0.00"),
            type="topup",
            status="completed",
            description=f"{network} top-up for {data.phone_number}",
            completed_at=now_utc,
            tx_metadata={
                "phone_number": data.phone_number,
                "network": network,
                "simulated": True,
            },
        )
        db.add(new_tx)
        db.add(Notification(
            user_id=uid,
            title="Top-Up Successful",
            body=f"PKR {data.amount:,.2f} {network} top-up sent to {data.phone_number}. Ref: {reference}",
            type="transaction",
            data={"reference_number": reference, "amount": str(data.amount), "network": network},
        ))

    await db.commit()
    await db.refresh(new_tx)
    try:
        await fraud_service.evaluate_transaction(db, new_tx, sender)
    except Exception as exc:
        logger.error("Fraud evaluation failed for topup %s: %s", new_tx.id, exc)

    logger.info("Top-up: %s → %s (%s) | PKR %.2f | ref=%s",
                sender.phone_number, data.phone_number, network, data.amount, reference)
    return new_tx


# ══════════════════════════════════════════════════════════════════════════════
# pay_bill — utility / bill payment
# ══════════════════════════════════════════════════════════════════════════════

# Simulated bill info returned to the client before deduction
_SIMULATED_BILL_INFO: dict = {
    "due_date": "2026-04-30",
    "billing_month": "March 2026",
    "units_consumed": None,
    "arrears": "0.00",
    "status": "unpaid",
}


async def pay_bill(
    db: AsyncSession,
    user_id: str | uuid.UUID,
    data,  # BillPayRequest
) -> Transaction:
    """
    Utility bill payment (LESCO, SNGPL, PTCL, etc.) — fully simulated.

    - Deducts amount from sender wallet (ACID, FOR UPDATE).
    - Inserts Transaction(type='bill').
    - Inserts Notification for sender.
    """
    uid = _parse_uuid(user_id)
    sender = await _fetch_user_with_wallet(db, uid, "User not found.")

    await wallet_service.reset_daily_limit_if_needed(db, sender.wallet)
    if sender.wallet.balance < data.amount:
        raise InsufficientBalanceError(
            detail=f"Balance PKR {sender.wallet.balance:,.2f}. Need PKR {data.amount:,.2f}.",
        )
    if sender.wallet.is_frozen:
        raise WalletFrozenError()

    reference = security.generate_reference()
    now_utc = datetime.now(timezone.utc)

    async with db.begin_nested():
        wallet_res = await db.execute(
            select(Wallet).where(Wallet.user_id == uid).with_for_update()
        )
        wallet: Wallet = wallet_res.scalars().first()

        if wallet.balance < data.amount:
            raise InsufficientBalanceError()

        wallet.balance -= data.amount
        wallet.daily_spent += data.amount

        bill_meta = {
            **_SIMULATED_BILL_INFO,
            "company": data.company,
            "consumer_number": data.consumer_number,
            "simulated": True,
        }

        new_tx = Transaction(
            reference_number=reference,
            sender_id=uid,
            recipient_id=None,
            amount=data.amount,
            fee=Decimal("0.00"),
            type="bill",
            status="completed",
            description=f"{data.company} bill — consumer {data.consumer_number}",
            completed_at=now_utc,
            tx_metadata=bill_meta,
        )
        db.add(new_tx)
        db.add(Notification(
            user_id=uid,
            title="Bill Payment Successful",
            body=(
                f"PKR {data.amount:,.2f} paid to {data.company}. "
                f"Consumer: {data.consumer_number}. Ref: {reference}"
            ),
            type="transaction",
            data={"reference_number": reference, "amount": str(data.amount), "company": data.company},
        ))

    await db.commit()
    await db.refresh(new_tx)
    try:
        await fraud_service.evaluate_transaction(db, new_tx, sender)
    except Exception as exc:
        logger.error("Fraud evaluation failed for bill %s: %s", new_tx.id, exc)

    logger.info("Bill pay: %s → %s (ref=%s) | PKR %.2f",
                sender.phone_number, data.company, reference, data.amount)
    return new_tx


# ══════════════════════════════════════════════════════════════════════════════
# get_transaction_history — paginated, filtered
# ══════════════════════════════════════════════════════════════════════════════

async def get_transaction_history(
    db: AsyncSession,
    user_id: str | uuid.UUID,
    params,  # TransactionHistoryRequest
) -> dict:
    """
    Returns paginated transaction history for the authenticated user.

    Matches on sender_id OR recipient_id so all sides of a transfer appear.
    Supports filtering by type, status, start_date, end_date.
    """
    from sqlalchemy import and_, func as sa_func, or_

    uid = _parse_uuid(user_id)
    offset = (params.page - 1) * params.per_page

    conditions = [or_(Transaction.sender_id == uid, Transaction.recipient_id == uid)]

    if params.tx_type:
        conditions.append(Transaction.type == params.tx_type)
    if params.status:
        conditions.append(Transaction.status == params.status)
    if params.start_date:
        conditions.append(Transaction.created_at >= params.start_date)
    if params.end_date:
        conditions.append(Transaction.created_at <= params.end_date)

    where_clause = and_(*conditions)

    # Total count
    count_result = await db.execute(
        select(sa_func.count()).select_from(Transaction).where(where_clause)
    )
    total: int = count_result.scalar_one()

    # Page of results
    rows_result = await db.execute(
        select(Transaction)
        .where(where_clause)
        .order_by(Transaction.created_at.desc())
        .offset(offset)
        .limit(params.per_page)
    )
    items = rows_result.scalars().all()

    return {
        "items": items,
        "total": total,
        "page": params.page,
        "per_page": params.per_page,
        "has_next": (offset + params.per_page) < total,
    }


# ══════════════════════════════════════════════════════════════════════════════
# get_transaction_by_id — single record, ownership enforced
# ══════════════════════════════════════════════════════════════════════════════

async def get_transaction_by_id(
    db: AsyncSession,
    user_id: str | uuid.UUID,
    tx_id: str | uuid.UUID,
) -> Transaction:
    """
    Fetch a single transaction by UUID.
    Only the sender OR recipient may retrieve it.
    Raises 404 if not found or caller is not a party to the transaction.
    """
    from sqlalchemy import or_

    uid = _parse_uuid(user_id)
    tid = _parse_uuid(tx_id)

    result = await db.execute(
        select(Transaction).where(
            Transaction.id == tid,
            or_(Transaction.sender_id == uid, Transaction.recipient_id == uid),
        )
    )
    tx = result.scalars().first()
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found.")
    return tx

