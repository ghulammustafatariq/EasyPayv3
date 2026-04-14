"""
app/services/card_service.py — EasyPay v3.0 Simulated Card Service (B24)

"Simulated" means: no real card network or external processor.
Card numbers, CVVs, and delivery tracking are generated internally.
Architecture is production-correct — only the external network calls are mocked.

Security rules enforced:
  Rule 1  — CVV hashed Bcrypt cost=12 (via security.hash_password).
  Rule 7  — Card number Fernet-encrypted (never plaintext in DB).
  Rule 5  — Physical card fee deducted atomically inside db.begin().
  Rule 11 — FCM notification sent on every delivery status change.
  Rule 10 — All errors raised via HTTPException with error_response envelope.
  B24     — cvv_encrypted stored once; deleted on first GET /details reveal.
"""
from __future__ import annotations

import random
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import decrypt_sensitive, encrypt_sensitive
from app.core.security import hash_password
from app.models.database import Transaction, User, VirtualCard, Wallet
from app.schemas.base import error_response
from app.schemas.cards import (
    BlockCardRequest,
    CardIssueRequest,
    ReplaceCardRequest,
    UpdateLimitsRequest,
    UpdateSettingsRequest,
)
from app.services import notification_service

# ── EasyPay simulated card BIN ─────────────────────────────────────────────
_CARD_PREFIX = "4276"

# ── Fees ────────────────────────────────────────────────────────────────────
PHYSICAL_CARD_FEE = Decimal("500.00")
PHYSICAL_REPLACEMENT_FEE = Decimal("300.00")

# ── Tier card-limit caps ─────────────────────────────────────────────────────
_TIER_DAILY_CAP: dict[int, Decimal] = {
    2: Decimal("100000"),
    3: Decimal("500000"),
    4: Decimal("2000000"),
}


# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _raise(code: str, message: str, status: int = 400) -> None:
    """Raise an HTTPException with a v3.0 error envelope."""
    raise HTTPException(
        status_code=status,
        detail=error_response(code, message),
    )


def _generate_reference() -> str:
    """Generate an 8-char uppercase hex reference: EP-XXXXXXXX."""
    return f"EP-{secrets.token_hex(4).upper()}"


def _format_card_number(raw: str) -> str:
    """Return 16-digit number formatted as '4276 XXXX XXXX XXXX'."""
    return f"{raw[:4]} {raw[4:8]} {raw[8:12]} {raw[12:]}"


def _mask_card_number(raw: str) -> str:
    """Return masked form: '4276 **** **** XXXX'."""
    return f"{raw[:4]} **** **** {raw[-4:]}"


async def _get_user_wallet(db: AsyncSession, user_id: uuid.UUID) -> Wallet:
    result = await db.execute(
        select(Wallet).where(Wallet.user_id == user_id)
    )
    wallet = result.scalar_one_or_none()
    if not wallet:
        _raise("WALLET_NOT_FOUND", "Wallet not found", 404)
    return wallet  # type: ignore[return-value]


# ─────────────────────────────────────────────────────────────────────────────
# CORE CARD GENERATION (shared by issue_card and replace_card)
# ─────────────────────────────────────────────────────────────────────────────

async def _create_card_record(
    db: AsyncSession,
    user: User,
    wallet: Wallet,
    card_type: str,
    card_holder_name: str,
    delivery_address: str | None,
    daily_limit: Decimal = Decimal("50000.00"),
    monthly_limit: Decimal = Decimal("200000.00"),
) -> tuple[VirtualCard, str, str]:
    """
    Generate and persist a new VirtualCard row.

    Returns (card, raw_cvv, formatted_full_number).
    raw_cvv and formatted_full_number are NEVER stored in plaintext.
    """
    # Generate 16-digit card number
    raw_number = _CARD_PREFIX + "".join(str(random.randint(0, 9)) for _ in range(12))
    formatted_full = _format_card_number(raw_number)
    masked = _mask_card_number(raw_number)

    # Generate 3-digit CVV
    raw_cvv = str(random.randint(100, 999))

    # Expiry: 3 years from now
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=3 * 365)

    # Encrypt card number; hash + encrypt CVV
    encrypted_number = encrypt_sensitive(raw_number)
    cvv_hashed = hash_password(raw_cvv)
    cvv_encrypted = encrypt_sensitive(raw_cvv)          # one-time reveal token

    card = VirtualCard(
        user_id=user.id,
        wallet_id=wallet.id,
        card_number_encrypted=encrypted_number,
        card_number_masked=masked,
        card_holder_name=card_holder_name,
        expiry_month=expires_at.month,
        expiry_year=expires_at.year,
        cvv_hash=cvv_hashed,
        cvv_encrypted=cvv_encrypted,
        card_type=card_type,
        status="active" if card_type == "virtual" else "pending_activation",
        daily_limit=daily_limit,
        monthly_limit=monthly_limit,
        is_contactless_enabled=(card_type == "physical"),
        expires_at=expires_at,
        activated_at=now if card_type == "virtual" else None,
        is_frozen=False,
        is_online_enabled=True,
    )

    if card_type == "physical":
        card.delivery_status = "processing"
        card.delivery_address = delivery_address
        card.delivery_tracking_id = f"EPD-{secrets.token_hex(4).upper()}"
        card.estimated_delivery_at = now + timedelta(days=random.randint(5, 7))

    db.add(card)
    return card, raw_cvv, formatted_full


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC SERVICE FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

async def issue_card(
    db: AsyncSession,
    user: User,
    request: CardIssueRequest,
) -> tuple[VirtualCard, str | None, str | None]:
    """
    Issue a virtual or physical card.

    Returns (card, raw_cvv, formatted_full_number).
    raw_cvv / formatted_full_number are None for physical cards
    (details shown only after activation via GET /details).
    """
    # ── Validation ────────────────────────────────────────────────────────────
    if not user.pin_hash:
        _raise("CARD_PIN_REQUIRED", "Set a wallet PIN before issuing a card")

    if request.card_type == "virtual" and user.verification_tier < 2:
        _raise(
            "CARD_TIER_INSUFFICIENT",
            "Virtual card requires Standard verification (Tier 2+)",
            403,
        )
    if request.card_type == "physical" and user.verification_tier < 3:
        _raise(
            "CARD_TIER_INSUFFICIENT",
            "Physical card requires Advanced verification (Tier 3+)",
            403,
        )
    if request.card_type == "physical" and not request.delivery_address:
        _raise("CARD_ADDRESS_REQUIRED", "Delivery address is required for physical card")

    # One active card per type per user
    existing = await db.execute(
        select(VirtualCard)
        .where(VirtualCard.user_id == user.id)
        .where(VirtualCard.card_type == request.card_type)
        .where(VirtualCard.status.in_(["pending_activation", "active", "frozen"]))
    )
    if existing.scalar_one_or_none():
        _raise(
            "CARD_ALREADY_EXISTS",
            f"You already have an active {request.card_type} card",
            409,
        )

    card_holder_name = (
        request.card_holder_name.upper()
        if request.card_holder_name
        else user.full_name.upper()
    )

    wallet_result = await db.execute(
        select(Wallet).where(Wallet.user_id == user.id).with_for_update()
    )
    wallet = wallet_result.scalar_one()

    if request.card_type == "physical":
        if wallet.balance < PHYSICAL_CARD_FEE:
            _raise(
                "WALLET_INSUFFICIENT_BALANCE",
                f"PKR {PHYSICAL_CARD_FEE} required to issue physical card",
            )
        wallet.balance -= PHYSICAL_CARD_FEE
        wallet.daily_spent = (wallet.daily_spent or Decimal("0.00")) + PHYSICAL_CARD_FEE

        fee_tx = Transaction(
            reference_number=_generate_reference(),
            sender_id=user.id,
            amount=PHYSICAL_CARD_FEE,
            type="bill",
            status="completed",
            description="Physical card issuance fee",
            completed_at=datetime.now(timezone.utc),
        )
        db.add(fee_tx)

    card, raw_cvv, formatted_full = await _create_card_record(
        db,
        user,
        wallet,
        card_type=request.card_type,
        card_holder_name=card_holder_name,
        delivery_address=request.delivery_address,
    )
    await db.commit()

    # Reload card so relationships + server_defaults are populated
    await db.refresh(card)

    if request.card_type == "virtual":
        return card, raw_cvv, formatted_full
    return card, None, None


async def freeze_card(db: AsyncSession, card: VirtualCard, user: User) -> VirtualCard:
    if card.user_id != user.id:
        _raise("CARD_NOT_FOUND", "Card not found", 404)
    if card.status == "blocked":
        _raise("CARD_BLOCKED", "Blocked cards cannot be frozen")
    if card.status == "frozen":
        _raise("CARD_ALREADY_FROZEN", "Card is already frozen")
    if card.status == "expired":
        _raise("CARD_EXPIRED", "Card has expired")

    card.is_frozen = True
    card.status = "frozen"
    await db.commit()
    await db.refresh(card)
    return card


async def unfreeze_card(db: AsyncSession, card: VirtualCard, user: User) -> VirtualCard:
    if card.user_id != user.id:
        _raise("CARD_NOT_FOUND", "Card not found", 404)
    if card.status != "frozen":
        _raise("CARD_NOT_FROZEN", "Card is not frozen")

    card.is_frozen = False
    card.status = "active"
    await db.commit()
    await db.refresh(card)
    return card


async def update_card_limits(
    db: AsyncSession,
    card: VirtualCard,
    user: User,
    request: UpdateLimitsRequest,
) -> VirtualCard:
    if card.user_id != user.id:
        _raise("CARD_NOT_FOUND", "Card not found", 404)
    if card.status in ("blocked", "expired", "replaced"):
        _raise("CARD_INACTIVE", "Cannot update limits on inactive card")

    max_daily = _TIER_DAILY_CAP.get(user.verification_tier, Decimal("25000"))

    if request.daily_limit is not None:
        if request.daily_limit > max_daily:
            _raise(
                "CARD_LIMIT_EXCEEDED",
                f"Daily limit cannot exceed PKR {max_daily} for your tier",
            )
        card.daily_limit = request.daily_limit

    if request.monthly_limit is not None:
        max_monthly = max_daily * 30
        if request.monthly_limit > max_monthly:
            _raise(
                "CARD_LIMIT_EXCEEDED",
                f"Monthly limit cannot exceed PKR {max_monthly} for your tier",
            )
        card.monthly_limit = request.monthly_limit

    await db.commit()
    await db.refresh(card)
    return card


async def update_card_settings(
    db: AsyncSession,
    card: VirtualCard,
    user: User,
    request: UpdateSettingsRequest,
) -> VirtualCard:
    if card.user_id != user.id:
        _raise("CARD_NOT_FOUND", "Card not found", 404)
    if card.status in ("blocked", "expired", "replaced"):
        _raise("CARD_INACTIVE", "Cannot update settings on inactive card")
    if request.is_contactless_enabled and card.card_type == "virtual":
        _raise(
            "CARD_SETTING_INVALID",
            "Contactless payments only available on physical cards",
        )

    if request.is_online_enabled is not None:
        card.is_online_enabled = request.is_online_enabled
    if request.is_contactless_enabled is not None:
        card.is_contactless_enabled = request.is_contactless_enabled

    await db.commit()
    await db.refresh(card)
    return card


async def block_card(
    db: AsyncSession,
    card: VirtualCard,
    user: User,
    request: BlockCardRequest,
) -> VirtualCard:
    if card.user_id != user.id:
        _raise("CARD_NOT_FOUND", "Card not found", 404)
    if card.status in ("blocked", "expired", "replaced"):
        _raise("CARD_ALREADY_INACTIVE", "Card is already blocked, expired, or replaced")
    if not request.reason.strip():
        _raise("BLOCK_REASON_REQUIRED", "Block reason is required")

    card.status = "blocked"
    card.block_reason = request.reason
    card.is_frozen = False
    await db.commit()
    await db.refresh(card)
    return card


async def replace_card(
    db: AsyncSession,
    card: VirtualCard,
    user: User,
    request: ReplaceCardRequest,
) -> tuple[VirtualCard, str | None, str | None]:
    """
    Block old card and issue a new one of the same type.
    Physical replacement: PKR 300 fee.
    Returns (new_card, raw_cvv, formatted_full).
    """
    if card.user_id != user.id:
        _raise("CARD_NOT_FOUND", "Card not found", 404)
    if card.status == "replaced":
        _raise("CARD_ALREADY_REPLACED", "Card has already been replaced")
    if card.status == "expired":
        _raise("CARD_EXPIRED", "Expired cards cannot be replaced")

    replacement_fee = (
        PHYSICAL_REPLACEMENT_FEE if card.card_type == "physical" else Decimal("0.00")
    )

    wallet_result = await db.execute(
        select(Wallet).where(Wallet.user_id == user.id).with_for_update()
    )
    wallet = wallet_result.scalar_one()

    if replacement_fee > 0:
        if wallet.balance < replacement_fee:
            _raise(
                "WALLET_INSUFFICIENT_BALANCE",
                f"PKR {replacement_fee} required for card replacement",
            )
        wallet.balance -= replacement_fee

        fee_tx = Transaction(
            reference_number=_generate_reference(),
            sender_id=user.id,
            amount=replacement_fee,
            type="bill",
            status="completed",
            description="Card replacement fee",
            completed_at=datetime.now(timezone.utc),
        )
        db.add(fee_tx)

    # Block old card
    card.status = "replaced"

    delivery_addr = request.delivery_address or card.delivery_address
    new_card, raw_cvv, formatted_full = await _create_card_record(
        db,
        user,
        wallet,
        card_type=card.card_type,
        card_holder_name=card.card_holder_name,
        delivery_address=delivery_addr,
        daily_limit=card.daily_limit,
        monthly_limit=card.monthly_limit,
    )
    await db.flush()
    card.replaced_by_id = new_card.id
    await db.commit()

    await db.refresh(new_card)

    if card.card_type == "virtual":
        return new_card, raw_cvv, formatted_full
    return new_card, None, None


async def get_card_details(
    db: AsyncSession,
    card: VirtualCard,
    user: User,
) -> dict:
    """
    Returns full card number and CVV — SENSITIVE.

    CVV reveal is one-time: cvv_encrypted is deleted from DB after the first
    call.  Subsequent calls return cvv = '•••'.
    Requires PIN verification at the route level.
    """
    if card.user_id != user.id:
        _raise("CARD_NOT_FOUND", "Card not found", 404)
    if card.status not in ("active", "frozen"):
        _raise("CARD_INACTIVE", "Card details only available for active cards")

    full_number = decrypt_sensitive(card.card_number_encrypted)
    formatted = _format_card_number(full_number)

    if card.cvv_encrypted:
        cvv_display = decrypt_sensitive(card.cvv_encrypted)
        # One-time reveal: delete encrypted CVV
        card.cvv_encrypted = None
        await db.commit()
    else:
        cvv_display = "•••"

    return {
        "card_number": formatted,
        "cvv": cvv_display,
        "expiry_month": card.expiry_month,
        "expiry_year": str(card.expiry_year)[-2:],
        "card_holder_name": card.card_holder_name,
    }


# ─────────────────────────────────────────────────────────────────────────────
# BACKGROUND DELIVERY SIMULATION  (called every 30 minutes from lifespan task)
# ─────────────────────────────────────────────────────────────────────────────

async def simulate_delivery_progress(db: AsyncSession) -> None:
    """
    Advance physical card delivery status automatically.

    Thresholds (hours since issued_at):
      ≥ 24  h → processing   → dispatched
      ≥ 96  h → dispatched   → out_for_delivery
      ≥ 120 h → out_for_delivery → delivered + card activated
    """
    now = datetime.now(timezone.utc)

    result = await db.execute(
        select(VirtualCard)
        .where(VirtualCard.card_type == "physical")
        .where(
            VirtualCard.delivery_status.in_(
                ["processing", "dispatched", "out_for_delivery"]
            )
        )
    )

    for card in result.scalars():
        # issued_at may be a naive datetime from server_default — make it aware
        issued = card.issued_at
        if issued.tzinfo is None:
            issued = issued.replace(tzinfo=timezone.utc)
        hours = (now - issued).total_seconds() / 3600

        if card.delivery_status == "processing" and hours >= 24:
            card.delivery_status = "dispatched"
            card.dispatched_at = now
            await notification_service.create_notification(
                db=db,
                user_id=card.user_id,
                title="Card Dispatched",
                body=(
                    f"Your EasyPay physical card "
                    f"({card.card_number_masked}) is on its way!"
                ),
                type="system",
                data={"card_id": str(card.id)},
            )

        elif card.delivery_status == "dispatched" and hours >= 96:
            card.delivery_status = "out_for_delivery"
            await notification_service.create_notification(
                db=db,
                user_id=card.user_id,
                title="Card Out for Delivery",
                body="Your EasyPay card will be delivered today!",
                type="system",
                data={"card_id": str(card.id)},
            )

        elif card.delivery_status == "out_for_delivery" and hours >= 120:
            card.delivery_status = "delivered"
            card.status = "active"
            card.delivered_at = now
            card.activated_at = now
            card.is_contactless_enabled = True
            await notification_service.create_notification(
                db=db,
                user_id=card.user_id,
                title="Card Delivered!",
                body=(
                    f"Your EasyPay card ending in "
                    f"{card.card_number_masked[-4:]} is now active."
                ),
                type="system",
                data={"card_id": str(card.id)},
            )

    await db.commit()
