"""
app/api/v1/transactions.py — EasyPay v3.0 Transaction Endpoints

Rules Enforced:
  Rule  5 — ACID transfers: enforced in transaction_service (with_for_update).
  Rule  6 — Balance never goes below 0.00 (enforced in service + DB constraint).
  Rule  9 — ALL routes require JWT (Depends(get_current_user)).
  Rule 10 — All domain errors (InsufficientBalanceError, DailyLimitExceededError,
             PINInvalidError, etc.) bubble up as EasyPayException and are caught
             by the global handler in main.py — no try/except needed here.
"""
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_db, verify_transaction_pin
from app.models.database import User
from app.schemas.base import success_response
from app.schemas.transactions import (
    BiometricConfirmRequest,
    BillPayRequest,
    BillReceiptResponse,
    DailyLimitStatusResponse,
    ExternalTransferReceiptResponse,
    ExternalTransferRequest,
    PendingTransactionResponse,
    SendMoneyRequest,
    TopUpReceiptResponse,
    TopUpRequest,
    TransactionHistoryRequest,
    TransactionListResponse,
    TransactionResponse,
)
from app.services import transaction_service

router = APIRouter(prefix="/transactions", tags=["Transactions"])


# ══════════════════════════════════════════════════════════════════════════════
# POST /transactions/send
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/send", status_code=status.HTTP_200_OK, response_model=dict)
async def send_money(
    data: SendMoneyRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Initiate a P2P money transfer to another EasyPay user.

    Auth options (supply exactly one):
      - **pin**: 4-digit transaction PIN (Bcrypt verified).
      - **biometric_token**: pass `"local_device_success"` for hardware
        biometric mock from the mobile device.

    The transfer is ACID-compliant:
      - Both wallet rows are locked with `SELECT ... FOR UPDATE`.
      - Balance is never allowed to go below PKR 0.00.
      - Daily tier limits are enforced before any funds move.
      - A completed `Transaction` record is returned immediately.

    Possible error codes:
      - `WALLET_INSUFFICIENT_BALANCE` (400)
      - `WALLET_DAILY_LIMIT_EXCEEDED` (400)
      - `PIN_INVALID` (400)
      - `WALLET_FROZEN` (403)
      - `USER_NOT_FOUND` (404)
      - `SELF_TRANSFER_NOT_ALLOWED` (400)
    """
    tx = await transaction_service.send_money(db, current_user.id, data)

    # Biometric pending path — amount >= PKR 1,000 returns a PendingTransactionResponse
    # instead of a completed Transaction ORM object.
    if isinstance(tx, PendingTransactionResponse):
        return success_response(
            message="Biometric confirmation required.",
            data=tx.model_dump(),
        )

    return success_response(
        message="Transfer completed successfully.",
        data=TransactionResponse.model_validate(tx).model_dump(),
    )


# ══════════════════════════════════════════════════════════════════════════════
# POST /transactions/confirm-biometric
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/confirm-biometric", status_code=status.HTTP_200_OK, response_model=dict)
async def confirm_biometric(
    data: BiometricConfirmRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Confirm a pending biometric transaction using the token returned by /send.

    The token is a short-lived JWT (5 min) containing the frozen transfer intent.
    After successful biometric confirmation on the device, the mobile client
    calls this endpoint to execute the actual transfer.

    Possible error codes:
      - `BIOMETRIC_TOKEN_EXPIRED` (401)
      - `WALLET_INSUFFICIENT_BALANCE` (400)
      - `WALLET_FROZEN` (403)
      - `USER_NOT_FOUND` (404)
    """
    tx = await transaction_service.confirm_biometric_transaction(
        db, current_user.id, data.pending_tx_token
    )
    return success_response(
        message="Transfer completed successfully.",
        data=TransactionResponse.model_validate(tx).model_dump(),
    )


# ══════════════════════════════════════════════════════════════════════════════
# POST /transactions/send-external — simulated inter-bank / JazzCash transfer
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/send-external", status_code=status.HTTP_200_OK, response_model=dict)
async def send_external(
    data: ExternalTransferRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Simulated external bank transfer.

    Verifies PIN, deducts from wallet, returns a receipt.
    No real inter-bank API is called — this is a demo simulation.
    """
    await verify_transaction_pin(pin=data.pin, current_user=current_user, db=db)
    tx = await transaction_service.send_external(db, current_user.id, data)
    receipt = ExternalTransferReceiptResponse(
        reference_number=tx.reference_number,
        bank_code=data.bank_code,
        account_number=data.account_number,
        amount=tx.amount,
        status=tx.status,
        created_at=tx.created_at,
    )
    return success_response(
        message="External transfer completed successfully.",
        data=receipt.model_dump(),
    )


# ══════════════════════════════════════════════════════════════════════════════
# GET /transactions/history — paginated with optional filters
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/history", status_code=status.HTTP_200_OK, response_model=dict)
async def get_transaction_history(
    page: int = Query(1, ge=1, description="1-based page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    tx_type: str | None = Query(None, description="send|receive|topup|bill|bank_transfer|refund"),
    tx_status: str | None = Query(None, alias="status", description="pending|completed|failed|reversed"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return paginated transaction history for the authenticated user.
    Includes transactions where the user is sender OR recipient.
    """
    params = TransactionHistoryRequest(
        page=page,
        per_page=per_page,
        tx_type=tx_type,
        status=tx_status,
    )
    result = await transaction_service.get_transaction_history(db, current_user.id, params)
    list_response = TransactionListResponse(
        items=[TransactionResponse.model_validate(t) for t in result["items"]],
        total=result["total"],
        page=result["page"],
        per_page=result["per_page"],
        has_next=result["has_next"],
    )
    return success_response(
        message="Transaction history retrieved successfully.",
        data=list_response.model_dump(),
    )


# ══════════════════════════════════════════════════════════════════════════════
# GET /transactions/{id} — single transaction (ownership enforced)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/{tx_id}", status_code=status.HTTP_200_OK, response_model=dict)
async def get_transaction(
    tx_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Fetch a single transaction by UUID.
    Only the sender or recipient of the transaction may retrieve it.
    Returns 404 if not found or the caller is not a party.
    """
    tx = await transaction_service.get_transaction_by_id(db, current_user.id, tx_id)
    return success_response(
        message="Transaction retrieved successfully.",
        data=TransactionResponse.model_validate(tx).model_dump(),
    )


# ══════════════════════════════════════════════════════════════════════════════
# POST /transactions/topup — mobile network top-up
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/topup", status_code=status.HTTP_200_OK, response_model=dict)
async def topup(
    data: TopUpRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Recharge a mobile number (Jazz / Telenor / Zong / Ufone / Warid).

    Network is detected automatically from the phone prefix and validated
    against the explicitly supplied `network` field.
    PIN is verified inside TopUpRequest schema and checked here.
    """
    await verify_transaction_pin(pin=data.pin, current_user=current_user, db=db)
    tx = await transaction_service.process_topup(db, current_user.id, data)
    receipt = TopUpReceiptResponse(
        reference_number=tx.reference_number,
        phone_number=data.phone_number,
        network=data.network,
        amount=tx.amount,
        status=tx.status,
        created_at=tx.created_at,
    )
    return success_response(
        message="Top-up completed successfully.",
        data=receipt.model_dump(),
    )


# ══════════════════════════════════════════════════════════════════════════════
# POST /transactions/bills/pay — utility bill payment
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/bills/pay", status_code=status.HTTP_200_OK, response_model=dict)
async def pay_bill(
    data: BillPayRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Pay a utility bill (LESCO, SNGPL, PTCL, KWSB, etc.) — simulated.

    Simulates fetching the bill from the biller, then deducts amount from
    wallet and returns a receipt. PIN required.
    """
    await verify_transaction_pin(pin=data.pin, current_user=current_user, db=db)
    tx = await transaction_service.pay_bill(db, current_user.id, data)
    receipt = BillReceiptResponse(
        reference_number=tx.reference_number,
        company=data.company,
        consumer_number=data.consumer_number,
        amount=tx.amount,
        status=tx.status,
        bill_info=tx.tx_metadata,
        created_at=tx.created_at,
    )
    return success_response(
        message="Bill payment completed successfully.",
        data=receipt.model_dump(),
    )


# ══════════════════════════════════════════════════════════════════════════════
# GET /transactions/bills/categories — static list, no auth required
# ══════════════════════════════════════════════════════════════════════════════

_BILL_CATEGORIES = [
    {"id": "electricity", "name": "Electricity", "companies": ["LESCO", "IESCO", "MEPCO", "PESCO", "QESCO", "HESCO", "GEPCO", "FESCO", "SEPCO"]},
    {"id": "gas",         "name": "Gas",         "companies": ["SNGPL", "SSGCL"]},
    {"id": "telecom",     "name": "Telecom",      "companies": ["PTCL", "SCO"]},
    {"id": "water",       "name": "Water",        "companies": ["KWSB", "WASA Lahore", "WASA Faisalabad"]},
    {"id": "internet",    "name": "Internet",     "companies": ["StormFiber", "Nayatel", "TWA", "LinkDotNet"]},
    {"id": "education",   "name": "Education Fees","companies": ["HEC", "University Fee"]},
    {"id": "insurance",   "name": "Insurance",    "companies": ["State Life", "Jubilee Life", "EFU Life"]},
]


@router.get("/bills/categories", status_code=status.HTTP_200_OK, response_model=dict)
async def get_bill_categories():
    """
    Return the static list of supported bill categories and biller companies.
    This endpoint does NOT require authentication.
    """
    return success_response(
        message="Bill categories retrieved successfully.",
        data={"categories": _BILL_CATEGORIES},
    )
