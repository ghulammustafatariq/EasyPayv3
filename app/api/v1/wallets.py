"""
app/api/v1/wallets.py — EasyPay v3.0 Wallet Endpoints

Rules Enforced:
  Rule  6 — Wallet balance NEVER goes below PKR 0.00 (enforced at service/DB).
  Rule  9 — ALL routes require JWT (Depends(get_current_user)).
  Rule 10 — All errors use error_response() envelope handled globally.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_db
from app.models.database import User
from app.schemas.base import success_response
from app.schemas.transactions import TransactionResponse
from app.schemas.wallet import WalletSummaryResponse
from app.services import wallet_service

router = APIRouter(prefix="/wallets", tags=["Wallets"])


# ══════════════════════════════════════════════════════════════════════════════
# GET /wallets/me
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/me", response_model=dict)
async def get_my_wallet_summary(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return the authenticated user's wallet balance, daily limits,
    and their 10 most recent transactions.

    Daily limit is auto-reset if the 24-hour window has expired
    before the summary is returned.
    """
    summary = await wallet_service.get_wallet_summary(db, current_user.id)

    # Serialise ORM Transaction objects inside the summary dict before
    # passing to WalletSummaryResponse so Pydantic receives plain dicts.
    summary["recent_transactions"] = [
        TransactionResponse.model_validate(tx).model_dump()
        for tx in summary["recent_transactions"]
    ]

    return success_response(
        message="Wallet summary retrieved successfully.",
        data=WalletSummaryResponse(**summary).model_dump(),
    )
