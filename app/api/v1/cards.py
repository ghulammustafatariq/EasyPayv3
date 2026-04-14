"""
app/api/v1/cards.py — EasyPay v3.0 Card Endpoints (B24)

All 11 endpoints follow Rule 10 (standardised error_response),
Rule 15 (JWT required on all routes), and B24 design spec.

Rate limit: POST /cards/issue → 10/day per IP (SlowAPI).
Sensitive endpoints (issue, details, block, replace) require PIN query param.
"""

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_verified_user, get_db, verify_transaction_pin
from app.core.limiter import limiter
from app.models.database import Transaction, VirtualCard
from app.schemas.base import error_response, success_response
from app.schemas.cards import (
    BlockCardRequest,
    CardResponse,
    CardTransactionResponse,
    ReplaceCardRequest,
    UpdateLimitsRequest,
    UpdateSettingsRequest,
    CardIssueRequest,
)
from app.services import card_service

router = APIRouter(prefix="/cards", tags=["Cards"])


# ─────────────────────────────────────────────────────────────────────────────
# HELPER
# ─────────────────────────────────────────────────────────────────────────────

async def _get_user_card(
    db: AsyncSession, card_id: uuid.UUID, user_id: uuid.UUID
) -> VirtualCard:
    result = await db.execute(
        select(VirtualCard)
        .where(VirtualCard.id == card_id)
        .where(VirtualCard.user_id == user_id)
    )
    card = result.scalar_one_or_none()
    if not card:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=404,
            detail=error_response("CARD_NOT_FOUND", "Card not found"),
        )
    return card


# ─────────────────────────────────────────────────────────────────────────────
# POST /cards/issue  — rate-limited 10/day
# ─────────────────────────────────────────────────────────────────────────────
@router.post("/issue", status_code=201)
@limiter.limit("10/day")
async def issue_card(
    request: Request,
    body: CardIssueRequest,
    pin: str,
    current_user=Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Issue a new virtual or physical card.

    - Virtual:  active instantly; full card details returned once in response.
    - Physical: PKR 500 fee deducted atomically; delivery simulation begins.
                Details shown after GET /details once card is active.
    Requires wallet PIN.
    """
    await verify_transaction_pin(pin=pin, current_user=current_user, db=db)
    card, raw_cvv, full_number = await card_service.issue_card(db, current_user, body)

    details = None
    if raw_cvv:  # virtual card — one-time reveal
        details = {
            "card_number": full_number,
            "cvv": raw_cvv,
            "expiry_month": card.expiry_month,
            "expiry_year": str(card.expiry_year)[-2:],
            "card_holder_name": card.card_holder_name,
        }

    note = (
        "Save your card details — CVV will not be shown again"
        if raw_cvv
        else "Your physical card will be delivered in 5–7 business days"
    )

    return success_response(
        message=f"{'Virtual' if body.card_type == 'virtual' else 'Physical'} card issued successfully",
        data={
            "card": CardResponse.model_validate(card).model_dump(),
            "details": details,
            "note": note,
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /cards/my-cards
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/my-cards")
async def get_my_cards(
    current_user=Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """Returns all non-replaced cards for the authenticated user."""
    result = await db.execute(
        select(VirtualCard)
        .where(VirtualCard.user_id == current_user.id)
        .where(VirtualCard.status.notin_(["replaced"]))
        .order_by(VirtualCard.issued_at.desc())
    )
    cards = result.scalars().all()
    return success_response(
        message=f"{len(cards)} card(s) found",
        data={"cards": [CardResponse.model_validate(c).model_dump() for c in cards]},
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /cards/{card_id}
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/{card_id}")
async def get_card(
    card_id: uuid.UUID,
    current_user=Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    card = await _get_user_card(db, card_id, current_user.id)
    return success_response(
        message="Card retrieved",
        data={"card": CardResponse.model_validate(card).model_dump()},
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /cards/{card_id}/details
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/{card_id}/details")
async def get_card_details(
    card_id: uuid.UUID,
    pin: str,
    current_user=Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns full card number and CVV.
    SENSITIVE: requires PIN.  CVV is shown once then displayed as '•••'.
    """
    await verify_transaction_pin(pin=pin, current_user=current_user, db=db)
    card = await _get_user_card(db, card_id, current_user.id)
    details = await card_service.get_card_details(db, card, current_user)
    return success_response(
        message="Card details retrieved — keep these secure",
        data={"details": details},
    )


# ─────────────────────────────────────────────────────────────────────────────
# PATCH /cards/{card_id}/freeze
# ─────────────────────────────────────────────────────────────────────────────
@router.patch("/{card_id}/freeze")
async def freeze_card(
    card_id: uuid.UUID,
    current_user=Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    card = await _get_user_card(db, card_id, current_user.id)
    card = await card_service.freeze_card(db, card, current_user)
    return success_response(
        message="Card frozen — all transactions will be declined",
        data={"card": CardResponse.model_validate(card).model_dump()},
    )


# ─────────────────────────────────────────────────────────────────────────────
# PATCH /cards/{card_id}/unfreeze
# ─────────────────────────────────────────────────────────────────────────────
@router.patch("/{card_id}/unfreeze")
async def unfreeze_card(
    card_id: uuid.UUID,
    current_user=Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    card = await _get_user_card(db, card_id, current_user.id)
    card = await card_service.unfreeze_card(db, card, current_user)
    return success_response(
        message="Card unfrozen — transactions are active again",
        data={"card": CardResponse.model_validate(card).model_dump()},
    )


# ─────────────────────────────────────────────────────────────────────────────
# PATCH /cards/{card_id}/limits
# ─────────────────────────────────────────────────────────────────────────────
@router.patch("/{card_id}/limits")
async def update_limits(
    card_id: uuid.UUID,
    body: UpdateLimitsRequest,
    current_user=Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    card = await _get_user_card(db, card_id, current_user.id)
    card = await card_service.update_card_limits(db, card, current_user, body)
    return success_response(
        message="Spending limits updated",
        data={"card": CardResponse.model_validate(card).model_dump()},
    )


# ─────────────────────────────────────────────────────────────────────────────
# PATCH /cards/{card_id}/settings
# ─────────────────────────────────────────────────────────────────────────────
@router.patch("/{card_id}/settings")
async def update_settings(
    card_id: uuid.UUID,
    body: UpdateSettingsRequest,
    current_user=Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    card = await _get_user_card(db, card_id, current_user.id)
    card = await card_service.update_card_settings(db, card, current_user, body)
    return success_response(
        message="Card settings updated",
        data={"card": CardResponse.model_validate(card).model_dump()},
    )


# ─────────────────────────────────────────────────────────────────────────────
# POST /cards/{card_id}/block
# ─────────────────────────────────────────────────────────────────────────────
@router.post("/{card_id}/block")
async def block_card(
    card_id: uuid.UUID,
    body: BlockCardRequest,
    pin: str,
    current_user=Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """Permanently blocks card. Cannot be reversed — request a replacement instead."""
    await verify_transaction_pin(pin=pin, current_user=current_user, db=db)
    card = await _get_user_card(db, card_id, current_user.id)
    card = await card_service.block_card(db, card, current_user, body)
    return success_response(
        message="Card permanently blocked. Request a replacement if needed.",
        data={"card": CardResponse.model_validate(card).model_dump()},
    )


# ─────────────────────────────────────────────────────────────────────────────
# POST /cards/{card_id}/replace
# ─────────────────────────────────────────────────────────────────────────────
@router.post("/{card_id}/replace")
async def replace_card(
    card_id: uuid.UUID,
    body: ReplaceCardRequest,
    pin: str,
    current_user=Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Replace card (lost/stolen/damaged).

    Physical replacement: PKR 300 fee deducted atomically.
    Virtual replacement: free; new details shown once.
    """
    await verify_transaction_pin(pin=pin, current_user=current_user, db=db)
    card = await _get_user_card(db, card_id, current_user.id)
    new_card, raw_cvv, formatted_full = await card_service.replace_card(
        db, card, current_user, body
    )

    details = None
    if raw_cvv:
        details = {
            "card_number": formatted_full,
            "cvv": raw_cvv,
            "expiry_month": new_card.expiry_month,
            "expiry_year": str(new_card.expiry_year)[-2:],
            "card_holder_name": new_card.card_holder_name,
        }

    return success_response(
        message="Card replaced successfully",
        data={
            "new_card": CardResponse.model_validate(new_card).model_dump(),
            "details": details,
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /cards/{card_id}/transactions
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/{card_id}/transactions")
async def get_card_transactions(
    card_id: uuid.UUID,
    page: int = 1,
    per_page: int = 20,
    current_user=Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """Returns transactions made with this specific card, paginated."""
    card = await _get_user_card(db, card_id, current_user.id)

    offset = (page - 1) * per_page
    result = await db.execute(
        select(Transaction)
        .where(Transaction.card_id == card.id)
        .order_by(Transaction.created_at.desc())
        .offset(offset)
        .limit(per_page)
    )
    txns = result.scalars().all()

    total = await db.scalar(
        select(func.count()).where(Transaction.card_id == card.id)
    )
    total = total or 0

    return success_response(
        message=f"{total} card transaction(s) found",
        data={
            "items": [CardTransactionResponse.model_validate(t).model_dump() for t in txns],
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": (total + per_page - 1) // per_page if total else 0,
        },
    )
