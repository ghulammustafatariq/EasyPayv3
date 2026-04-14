"""
app/schemas/wallet.py — EasyPay v3.0 Wallet Schemas

Rule 6: balance NEVER goes below 0.00 — enforced at the DB level
        (CHECK constraint) and the service layer. Schemas reflect
        this with ge=0 constraints.
"""
import uuid
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, Field

from app.schemas.transactions import TransactionResponse


# ══════════════════════════════════════════════════════════════════════════════
# WALLET RESPONSE
# ══════════════════════════════════════════════════════════════════════════════

class WalletResponse(BaseModel):
    """Core wallet data returned by GET /wallet."""

    id: uuid.UUID
    balance: Decimal = Field(..., ge=0, decimal_places=2, description="PKR balance")
    currency: str = Field(default="PKR", max_length=3)
    is_frozen: bool
    daily_limit: Decimal = Field(..., ge=0, decimal_places=2)
    daily_spent: Decimal = Field(..., ge=0, decimal_places=2)
    limit_reset_at: datetime = Field(..., description="When daily_spent resets to 0")

    model_config = {"from_attributes": True}


class WalletSummaryResponse(BaseModel):
    """
    Wallet details combined with the user's recent transaction history.
    Returned by GET /wallet/summary.
    """

    id: uuid.UUID
    balance: Decimal = Field(..., ge=0, decimal_places=2)
    currency: str = Field(default="PKR", max_length=3)
    is_frozen: bool
    daily_limit: Decimal = Field(..., ge=0, decimal_places=2)
    daily_spent: Decimal = Field(..., ge=0, decimal_places=2)
    limit_reset_at: datetime
    recent_transactions: List[TransactionResponse] = Field(
        default_factory=list,
        description="Up to 10 most recent transactions",
    )

    model_config = {"from_attributes": True}
