"""
app/api/v1/external_payments.py — EasyPay v3.0

Outbound payment endpoints: wallet-to-wallet transfers across 5 Pakistani
digital wallets (JazzCash, Easypaisa, NayaPay, UPay, SadaPay) plus bank
transfers and bill payments via 1LINK/BPSP.

All endpoints:
  - Require a valid JWT (get_current_verified_user)
  - Require the user's 4-digit transaction PIN
  - Deduct from the sender's EasyPay wallet atomically (with_for_update)
  - Record an `external_transfer` / `bill` Transaction
  - On external network failure after balance deduction → refund immediately
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import security
from app.core.dependencies import get_current_verified_user, get_db
from app.core.exceptions import (
    EasyPayException,
    InsufficientBalanceError,
    PINInvalidError,
    WalletFrozenError,
)
from app.models.database import Transaction, User, Wallet
from app.schemas.base import success_response
from app.services import payment_network_service as pns
from app.services.payment_network_service import ExternalNetworkError
from app.services.wallet_service import check_and_reset_daily_limit

router = APIRouter(prefix="/external", tags=["External Payments"])
logger = logging.getLogger("easypay")


# ── DAILY LIMIT ───────────────────────────────────────────────────────────────
_TIER_DAILY_LIMITS: dict[int, Decimal] = {
    0: Decimal("0.00"),
    1: Decimal("25000.00"),
    2: Decimal("100000.00"),
    3: Decimal("500000.00"),
    4: Decimal("2000000.00"),
}


# ── REQUEST SCHEMAS ───────────────────────────────────────────────────────────

class WalletLookupRequest(BaseModel):
    network: str = Field(..., description="Target wallet network: JAZZCASH, EASYPAISA, NAYAPAY, UPAY, SADAPAY")
    mobile_number: str = Field(..., description="Recipient's registered mobile number")


class WalletSendRequest(BaseModel):
    network: str = Field(..., description="Target wallet network")
    receiver_mobile: str = Field(..., description="Recipient's registered mobile number")
    amount: Decimal = Field(..., gt=0, description="Amount in PKR")
    pin: str = Field(..., min_length=4, max_length=4, description="4-digit transaction PIN")
    description: str = Field(default="", max_length=200)


class BankLookupRequest(BaseModel):
    bank_code: str = Field(..., description="Destination bank code (e.g. HBL, UBL, MCB)")
    account_number: str = Field(..., description="Destination account number")


class BankSendRequest(BaseModel):
    bank_code: str = Field(..., description="Destination bank code")
    account_number: str = Field(..., description="Destination account number")
    amount: Decimal = Field(..., gt=0, description="Amount in PKR")
    pin: str = Field(..., min_length=4, max_length=4, description="4-digit transaction PIN")
    remarks: str = Field(default="", max_length=200)


class BillFetchRequest(BaseModel):
    company: str = Field(..., description="Utility company code (e.g. LESCO, MEPCO, SNGPL, PTCL)")
    consumer_number: str = Field(..., description="Consumer/reference number")


class BillPayRequest(BaseModel):
    company: str = Field(..., description="Utility company code")
    consumer_number: str = Field(..., description="Consumer/reference number")
    amount: Decimal = Field(..., gt=0, description="Exact amount shown on bill")
    pin: str = Field(..., min_length=4, max_length=4, description="4-digit transaction PIN")


# ── HELPERS ───────────────────────────────────────────────────────────────────

async def _deduct_wallet(
    db: AsyncSession,
    user: User,
    amount: Decimal,
    txn_type: str,
    description: str,
    external_ref: str | None = None,
) -> Transaction:
    """
    Atomically deduct `amount` from user's wallet and record a Transaction.
    Uses SELECT FOR UPDATE to prevent double-spend.
    Validates: PIN already verified by caller, wallet not frozen, balance ≥ amount,
    daily limit not exceeded.
    Returns the committed Transaction ORM object.
    """
    async with db.begin_nested():
        wallet = (
            await db.execute(
                select(Wallet)
                .where(Wallet.user_id == user.id)
                .with_for_update()
            )
        ).scalars().first()

        if not wallet:
            raise EasyPayException(
                status_code=404,
                error_code="WALLET_NOT_FOUND",
                message="Your EasyPay wallet was not found.",
            )

        if wallet.is_frozen:
            raise WalletFrozenError()

        # Reset daily window if it has expired (inline, inside the transaction)
        now_utc = datetime.now(timezone.utc)
        reset_at = wallet.limit_reset_at
        if reset_at is not None and reset_at.tzinfo is None:
            reset_at = reset_at.replace(tzinfo=timezone.utc)
        if reset_at is None or reset_at < now_utc:
            wallet.daily_spent = Decimal("0.00")
            wallet.limit_reset_at = now_utc + timedelta(hours=24)

        # Balance check
        if wallet.balance < amount:
            raise InsufficientBalanceError()

        # Daily limit check
        tier_limit = (
            user.daily_limit_override
            or wallet.daily_limit
            or _TIER_DAILY_LIMITS.get(user.verification_tier, Decimal("0.00"))
        )
        if tier_limit == Decimal("0.00"):
            raise EasyPayException(
                status_code=403,
                error_code="WALLET_DAILY_LIMIT_EXCEEDED",
                message="Your account tier does not allow external transfers. Please complete KYC.",
            )
        if wallet.daily_spent + amount > tier_limit:
            raise EasyPayException(
                status_code=400,
                error_code="WALLET_DAILY_LIMIT_EXCEEDED",
                message=(
                    f"Daily limit of PKR {tier_limit:,.2f} would be exceeded. "
                    f"Spent today: PKR {wallet.daily_spent:,.2f}."
                ),
            )

        wallet.balance -= amount
        wallet.daily_spent += amount

        txn = Transaction(
            reference_number=security.generate_reference(),
            sender_id=user.id,
            amount=amount,
            fee=Decimal("0.00"),
            type=txn_type,
            status="completed",
            description=description,
            external_ref=external_ref,
        )
        db.add(txn)

    return txn


async def _refund_wallet(
    db: AsyncSession,
    user_id: uuid.UUID,
    amount: Decimal,
    original_ref: str,
) -> None:
    """Refund a deducted amount back to user's wallet after an external failure."""
    async with db.begin_nested():
        wallet = (
            await db.execute(
                select(Wallet)
                .where(Wallet.user_id == user_id)
                .with_for_update()
            )
        ).scalars().first()

        if wallet:
            wallet.balance += amount
            if wallet.daily_spent >= amount:
                wallet.daily_spent -= amount

            refund_txn = Transaction(
                reference_number=security.generate_reference(),
                recipient_id=user_id,
                amount=amount,
                fee=Decimal("0.00"),
                type="refund",
                status="completed",
                description=f"Refund for failed external transfer {original_ref}",
            )
            db.add(refund_txn)


# ══════════════════════════════════════════════════════════════════════════════
# WALLET ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/wallet/lookup", status_code=status.HTTP_200_OK, response_model=dict)
async def wallet_lookup(
    data: WalletLookupRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_verified_user),
):
    """
    Verify a recipient's wallet account exists on a target network before sending.
    No funds are moved; returns the account holder name.
    """
    result = await pns.lookup_wallet_account(
        network=data.network,
        mobile_number=data.mobile_number,
    )
    return success_response(
        message="Account found.",
        data=result,
    )


@router.post("/wallet/send", status_code=status.HTTP_200_OK, response_model=dict)
async def wallet_send(
    data: WalletSendRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_verified_user),
):
    """
    Send money from your EasyPay wallet to a wallet on JazzCash, Easypaisa,
    NayaPay, UPay, or SadaPay.

    Flow:
      1. Verify PIN.
      2. Deduct from EasyPay wallet (ACID).
      3. Call external network.  On failure → refund EasyPay wallet.
      4. Return the completed transaction reference.
    """
    if not security.verify_pin(data.pin, current_user.pin_hash):
        raise PINInvalidError()

    amount = data.amount
    ref = security.generate_reference()

    txn = await _deduct_wallet(
        db=db,
        user=current_user,
        amount=amount,
        txn_type="external_transfer",
        description=f"Transfer to {data.network} wallet {data.receiver_mobile}",
        external_ref=ref,
    )
    await db.commit()

    try:
        net_result = await pns.send_to_wallet(
            network=data.network,
            sender_mobile=current_user.phone_number,
            receiver_mobile=data.receiver_mobile,
            amount=float(amount),
            transaction_ref=ref,
            description=data.description,
        )
    except ExternalNetworkError as exc:
        logger.warning("External wallet send failed (%s): %s", exc.code, exc.message)
        await _refund_wallet(db, current_user.id, amount, ref)
        await db.commit()
        raise EasyPayException(
            status_code=502,
            error_code=exc.code,
            message=exc.message,
        )

    return success_response(
        message=f"PKR {amount:,.2f} sent to {data.network} wallet successfully.",
        data={
            "easypay_reference": txn.reference_number,
            "network_transaction_id": net_result["transaction_id"],
            "network": data.network,
            "receiver_mobile": data.receiver_mobile,
            "receiver_name": net_result["receiver_name"],
            "amount": float(amount),
            "currency": "PKR",
        },
    )


# ══════════════════════════════════════════════════════════════════════════════
# BANK TRANSFER ENDPOINTS (IBFT via 1LINK)
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/bank/lookup", status_code=status.HTTP_200_OK, response_model=dict)
async def bank_lookup(
    data: BankLookupRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_verified_user),
):
    """
    Verify a destination bank account exists via 1LINK IBFT before sending.
    No funds are moved; returns the account title.
    """
    result = await pns.lookup_bank_account(
        bank_code=data.bank_code,
        account_number=data.account_number,
    )
    return success_response(
        message="Bank account found.",
        data=result,
    )


@router.post("/bank/send", status_code=status.HTTP_200_OK, response_model=dict)
async def bank_send(
    data: BankSendRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_verified_user),
):
    """
    Transfer funds from your EasyPay wallet to any Pakistani bank account
    via 1LINK IBFT.

    Flow:
      1. Verify PIN.
      2. Deduct from EasyPay wallet (ACID).
      3. Submit IBFT to 1LINK.  On failure → refund EasyPay wallet.
      4. Return IBFT STAN + RRN.
    """
    if not security.verify_pin(data.pin, current_user.pin_hash):
        raise PINInvalidError()

    amount = data.amount
    ref = security.generate_reference()

    txn = await _deduct_wallet(
        db=db,
        user=current_user,
        amount=amount,
        txn_type="bank_transfer",
        description=f"IBFT to {data.bank_code} account {data.account_number}",
        external_ref=ref,
    )
    await db.commit()

    try:
        ibft_result = await pns.send_bank_transfer(
            source_account=str(current_user.id),
            dest_bank_code=data.bank_code,
            dest_account_number=data.account_number,
            amount=float(amount),
            transaction_ref=ref,
            remarks=data.remarks,
        )
    except ExternalNetworkError as exc:
        logger.warning("IBFT failed (%s): %s", exc.code, exc.message)
        await _refund_wallet(db, current_user.id, amount, ref)
        await db.commit()
        raise EasyPayException(
            status_code=502,
            error_code=exc.code,
            message=exc.message,
        )

    return success_response(
        message=f"PKR {amount:,.2f} transferred to {ibft_result['dest_account_title']} successfully.",
        data={
            "easypay_reference": txn.reference_number,
            "ibft_stan": ibft_result["stan"],
            "ibft_rrn": ibft_result["rrn"],
            "dest_bank": data.bank_code.upper(),
            "dest_account": data.account_number,
            "dest_account_title": ibft_result["dest_account_title"],
            "amount": float(amount),
            "currency": "PKR",
            "status": ibft_result["status"],
        },
    )


# ══════════════════════════════════════════════════════════════════════════════
# BILL PAYMENT ENDPOINTS (via 1LINK BPSP)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/bills/companies", status_code=status.HTTP_200_OK, response_model=dict)
async def list_bill_companies(
    current_user: User = Depends(get_current_verified_user),
):
    """Return the full list of supported bill payment companies."""
    companies = await pns.get_bill_companies()
    return success_response(
        message="Supported bill companies retrieved.",
        data={"companies": companies},
    )


@router.post("/bills/fetch", status_code=status.HTTP_200_OK, response_model=dict)
async def fetch_bill(
    data: BillFetchRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_verified_user),
):
    """
    Fetch bill details for a consumer number before making payment.
    Returns the due amount, due date, and billing information.
    """
    try:
        bill = await pns.fetch_bill(
            company=data.company,
            consumer_number=data.consumer_number,
        )
    except ExternalNetworkError as exc:
        raise EasyPayException(detail=exc.message, error_code=exc.code)

    return success_response(
        message="Bill details retrieved.",
        data=bill,
    )


@router.post("/bills/pay", status_code=status.HTTP_200_OK, response_model=dict)
async def pay_bill(
    data: BillPayRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_verified_user),
):
    """
    Pay a utility or government bill via 1LINK BPSP.

    Flow:
      1. Verify PIN.
      2. Deduct from EasyPay wallet (ACID).
      3. Submit payment to 1LINK BPSP.  On failure → refund EasyPay wallet.
      4. Return BPSP reference number.
    """
    if not security.verify_pin(data.pin, current_user.pin_hash):
        raise PINInvalidError()

    amount = data.amount
    ref = security.generate_reference()

    txn = await _deduct_wallet(
        db=db,
        user=current_user,
        amount=amount,
        txn_type="bill",
        description=f"{data.company} bill payment for consumer {data.consumer_number}",
        external_ref=ref,
    )
    await db.commit()

    try:
        pay_result = await pns.pay_bill(
            company=data.company,
            consumer_number=data.consumer_number,
            amount=float(amount),
            transaction_ref=ref,
        )
    except ExternalNetworkError as exc:
        logger.warning("Bill payment failed (%s): %s", exc.code, exc.message)
        await _refund_wallet(db, current_user.id, amount, ref)
        await db.commit()
        raise EasyPayException(detail=exc.message, error_code=exc.code)

    return success_response(
        message=f"{data.company} bill paid successfully.",
        data={
            "easypay_reference": txn.reference_number,
            "bpsp_reference": pay_result["bpsp_reference"],
            "company": pay_result["company"],
            "consumer_number": data.consumer_number,
            "consumer_name": pay_result.get("consumer_name", ""),
            "amount_paid": float(amount),
            "paid_at": pay_result["paid_at"],
        },
    )
