"""
app/api/v1/payments.py — Stripe Payment Integration for Wallet Top-Up

Endpoints:
  POST /payments/create-intent         — Creates Stripe PaymentIntent, returns client_secret
  POST /payments/webhook               — Stripe webhook (no auth) — credits wallet on success
  POST /payments/confirm-topup         — Confirms Stripe card top-up from app
  POST /payments/mobile-wallet-topup   — Top-up via JazzCash or EasyPaisa mobile wallet
"""
import logging
import re
from datetime import datetime, timezone
from decimal import Decimal

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.dependencies import get_current_verified_user, get_db
from app.core.security import generate_reference
from app.models.database import Transaction, User, Wallet
from app.schemas.base import success_response
from app.services import payment_network_service as pns
from app.services.payment_network_service import ExternalNetworkError

logger = logging.getLogger(__name__)

stripe.api_key = settings.STRIPE_SECRET_KEY

router = APIRouter(prefix="/payments", tags=["Payments"])


# ── Request Schema ────────────────────────────────────────────────────────────

class CreateIntentRequest(BaseModel):
    amount: float = Field(..., gt=0, description="Top-up amount in PKR")


# ── POST /payments/create-intent ──────────────────────────────────────────────

@router.post("/create-intent")
async def create_payment_intent(
    data: CreateIntentRequest,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Called by Android app before showing Stripe PaymentSheet.
    Creates a Stripe PaymentIntent and returns the client_secret.
    """
    if data.amount < 100:
        raise HTTPException(400, detail="Minimum top-up is PKR 100")
    if data.amount > 100_000:
        raise HTTPException(400, detail="Maximum top-up is PKR 100,000")

    # PKR is a zero-decimal currency in Stripe — amount in paisa
    amount_paisa = int(data.amount * 100)

    try:
        intent = stripe.PaymentIntent.create(
            amount=amount_paisa,
            currency="pkr",
            metadata={
                "user_id": str(current_user.id),
                "user_phone": current_user.phone_number,
                "platform": "easypay_android",
            },
            description=f"EasyPay wallet top-up for {current_user.phone_number}",
        )
    except stripe.error.StripeError as e:
        logger.error("Stripe PaymentIntent creation failed: %s", e)
        raise HTTPException(502, detail="Payment service unavailable. Please try again.")

    logger.info(
        "Created PaymentIntent %s for user %s, amount PKR %.2f",
        intent.id, current_user.id, data.amount,
    )

    return success_response(
        message="Payment intent created",
        data={
            "client_secret": intent.client_secret,
            "publishable_key": settings.STRIPE_PUBLISHABLE_KEY,
            "amount": data.amount,
            "payment_intent_id": intent.id,
        },
    )


# ── POST /payments/webhook ────────────────────────────────────────────────────

@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Stripe calls this endpoint after payment events.
    On payment_intent.succeeded → credits the user's EasyPay wallet.

    IMPORTANT: This endpoint has NO JWT auth — Stripe signs the payload instead.
    """
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        raise HTTPException(400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError:
        raise HTTPException(400, detail="Invalid webhook signature")

    if event["type"] == "payment_intent.succeeded":
        intent = event["data"]["object"]
        user_id = getattr(intent.metadata, "user_id", None)
        amount_paisa = intent.amount
        amount_pkr = Decimal(str(amount_paisa)) / 100

        if not user_id:
            logger.warning("Webhook: payment_intent.succeeded without user_id metadata")
            return {"received": True}

        # Credit wallet
        wallet_result = await db.execute(
            select(Wallet).where(Wallet.user_id == user_id)
        )
        wallet = wallet_result.scalar_one_or_none()

        if wallet:
            wallet.balance += amount_pkr

            txn = Transaction(
                reference_number=generate_reference(),
                sender_id=None,
                recipient_id=user_id,
                amount=amount_pkr,
                fee=Decimal("0.00"),
                type="topup",
                status="completed",
                description="Wallet top-up via card",
                tx_metadata={
                    "stripe_payment_intent": intent.id,
                    "payment_method": "card",
                },
                completed_at=datetime.now(timezone.utc),
            )
            db.add(txn)
            await db.commit()

            logger.info(
                "Webhook: credited PKR %s to user %s wallet (PI: %s)",
                amount_pkr, user_id, intent.id,
            )
        else:
            logger.error("Webhook: wallet not found for user_id=%s", user_id)

    return {"received": True}


# ── POST /payments/confirm-topup ──────────────────────────────────────────────

class ConfirmTopupRequest(BaseModel):
    payment_intent_id: str


@router.post("/confirm-topup")
async def confirm_topup(
    data: ConfirmTopupRequest,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Called by the Android app immediately after PaymentSheet reports success.
    Retrieves the PaymentIntent from Stripe and verifies it actually succeeded
    before crediting the wallet. No webhook secret required.
    """
    try:
        intent = stripe.PaymentIntent.retrieve(data.payment_intent_id)
    except stripe.error.StripeError as e:
        logger.error("Stripe retrieve failed: %s", e)
        raise HTTPException(502, detail="Could not verify payment with Stripe.")

    if intent.status != "succeeded":
        raise HTTPException(400, detail=f"Payment not completed (status: {intent.status})")

    # Anti-replay: make sure this PaymentIntent belongs to the current user
    if getattr(intent.metadata, "user_id", None) != str(current_user.id):
        raise HTTPException(403, detail="Payment intent does not belong to this account.")

    # Anti-replay: check if this PaymentIntent was already credited
    existing = await db.execute(
        select(Transaction).where(
            Transaction.tx_metadata["stripe_payment_intent"].astext == data.payment_intent_id
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, detail="This payment has already been credited to your wallet.")

    amount_pkr = Decimal(str(intent.amount)) / 100

    wallet_result = await db.execute(
        select(Wallet).where(Wallet.user_id == current_user.id)
    )
    wallet = wallet_result.scalar_one_or_none()
    if not wallet:
        raise HTTPException(404, detail="Wallet not found.")

    wallet.balance += amount_pkr

    txn = Transaction(
        reference_number=generate_reference(),
        sender_id=None,
        recipient_id=current_user.id,
        amount=amount_pkr,
        fee=Decimal("0.00"),
        type="topup",
        status="completed",
        description="Wallet top-up via card",
        tx_metadata={
            "stripe_payment_intent": data.payment_intent_id,
            "payment_method": "card",
        },
        completed_at=datetime.now(timezone.utc),
    )
    db.add(txn)
    await db.commit()

    logger.info(
        "Confirmed card top-up PKR %s for user %s (PI: %s)",
        amount_pkr, current_user.id, data.payment_intent_id,
    )

    return success_response(
        message=f"PKR {amount_pkr:.2f} added to your wallet.",
        data={
            "amount": float(amount_pkr),
            "payment_intent_id": data.payment_intent_id,
        },
    )


# ── POST /payments/mobile-wallet-topup ───────────────────────────────────────

_PK_PHONE_RE = re.compile(r"^(\+92|0092|0)[3][0-9]{9}$")


class MobileWalletTopupRequest(BaseModel):
    provider: str = Field(..., description="JAZZCASH or EASYPAISA")
    mobile_number: str = Field(..., min_length=10, max_length=15)
    amount: float = Field(..., gt=0)


@router.post("/mobile-wallet-topup")
async def mobile_wallet_topup(
    data: MobileWalletTopupRequest,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Top-up the EasyPay wallet by collecting payment from the user's
    JazzCash or EasyPaisa mobile wallet.
    Uses the real sandbox API when credentials are configured; otherwise
    falls back to the local mock network server.
    """
    provider = data.provider.upper()
    if provider not in ("JAZZCASH", "EASYPAISA"):
        raise HTTPException(400, detail="provider must be JAZZCASH or EASYPAISA")

    mobile = data.mobile_number.strip().replace("-", "").replace(" ", "")
    if not _PK_PHONE_RE.match(mobile):
        raise HTTPException(400, detail="Invalid Pakistani mobile number format")

    if data.amount < 100:
        raise HTTPException(400, detail="Minimum top-up is PKR 100")
    if data.amount > 25_000:
        raise HTTPException(400, detail="Maximum mobile wallet top-up is PKR 25,000")

    txn_ref = f"EP{generate_reference()[-10:]}"

    # ── Call external network ─────────────────────────────────────────────────
    try:
        provider_result = await pns.collect_mobile_wallet_topup(
            network=provider,
            mobile_number=mobile,
            amount=data.amount,
            transaction_ref=txn_ref,
        )
    except ExternalNetworkError as exc:
        raise HTTPException(
            status_code=400,
            detail=exc.message,
        )

    amount_dec = Decimal(str(data.amount))

    # ── Credit wallet (with_for_update prevents race conditions) ─────────────
    wallet_result = await db.execute(
        select(Wallet).where(Wallet.user_id == current_user.id).with_for_update()
    )
    wallet = wallet_result.scalar_one_or_none()
    if not wallet:
        raise HTTPException(404, detail="Wallet not found.")

    wallet.balance += amount_dec

    txn = Transaction(
        reference_number=txn_ref,
        sender_id=None,
        recipient_id=current_user.id,
        amount=amount_dec,
        fee=Decimal("0.00"),
        type="topup",
        status="completed",
        description=f"Wallet top-up via {provider.capitalize()}",
        tx_metadata={
            "provider": provider,
            "provider_mobile": mobile,
            "provider_ref": provider_result.get("provider_ref", txn_ref),
            "payment_method": "mobile_wallet",
        },
        completed_at=datetime.now(timezone.utc),
    )
    db.add(txn)
    await db.commit()
    await db.refresh(wallet)

    logger.info(
        "Mobile wallet top-up PKR %.2f for user %s via %s (ref: %s)",
        data.amount, current_user.id, provider, txn_ref,
    )

    return success_response(
        message=f"PKR {data.amount:.2f} added to your wallet via {provider.capitalize()}.",
        data={
            "amount": data.amount,
            "reference_number": txn_ref,
            "provider": provider,
            "provider_ref": provider_result.get("provider_ref", txn_ref),
            "new_balance": float(wallet.balance),
        },
    )
