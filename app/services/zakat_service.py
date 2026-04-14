"""Zakat calculation and payment service."""
from decimal import Decimal
from uuid import UUID
from datetime import datetime, timezone

import httpx
import logging

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core import security
from app.core.exceptions import InsufficientBalanceError, WalletFrozenError
from app.models.database import ZakatCalculation, User, Wallet, Transaction
from app.schemas.zakat import ZakatCalculateRequest

logger = logging.getLogger(__name__)

# ── Zakat constants (Hanafi fiqh, standard in Pakistan) ──────────────────────
NISAB_SILVER_GRAMS = Decimal("612")      # 612g silver = Nisab threshold
ZAKAT_RATE         = Decimal("0.025")    # 2.5%
TROY_OZ_TO_GRAMS   = Decimal("31.1035")

# ── Default fallback rates (used when live fetch fails) ──────────────────────
# Updated periodically — not relied upon in production; live fetch preferred
_FALLBACK_GOLD_PKR_PER_GRAM   = Decimal("27000.00")   # ≈ PKR 27,000/g
_FALLBACK_SILVER_PKR_PER_GRAM = Decimal("300.00")     # ≈ PKR 300/g
_FALLBACK_USD_PKR              = Decimal("278.00")

_METALS_LIVE_URL   = "https://api.metals.live/v1/spot"
_EXCHANGE_RATE_URL = "https://open.er-api.com/v6/latest/USD"
_HTTP_TIMEOUT      = httpx.Timeout(connect=8.0, read=12.0, write=5.0, pool=5.0)


# ─────────────────────────────────────────────────────────────────────────────
# LIVE RATES
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_live_zakat_rates() -> dict:
    """
    Fetch current gold / silver PKR rates from two free public APIs:
      1. https://api.metals.live/v1/spot  — spot prices in USD/troy-oz
      2. https://open.er-api.com/v6/latest/USD — USD → PKR exchange rate

    Returns a dict with keys:
      gold_per_oz_usd, silver_per_oz_usd, usd_pkr,
      gold_per_gram_pkr, silver_per_gram_pkr, nisab_threshold_pkr, source
    Falls back gracefully to hardcoded defaults if either API is unavailable.
    """
    gold_usd   = None
    silver_usd = None
    usd_pkr    = None
    source     = "fallback"

    # ── Fetch spot metals prices ──────────────────────────────────────────────
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(_METALS_LIVE_URL)
            resp.raise_for_status()
            metals: list[dict] = resp.json()
            # Response is an array: [{"gold": X}, {"silver": X}, ...]
            for item in metals:
                if "gold" in item:
                    gold_usd = Decimal(str(item["gold"]))
                if "silver" in item:
                    silver_usd = Decimal(str(item["silver"]))
        logger.info("Metals.live: gold=$%.2f/oz  silver=$%.2f/oz", gold_usd, silver_usd)
    except Exception as exc:
        logger.warning("metals.live fetch failed: %s — using fallback rates", exc)

    # ── Fetch USD/PKR exchange rate ───────────────────────────────────────────
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.get(_EXCHANGE_RATE_URL)
            resp.raise_for_status()
            fx_data = resp.json()
            pkr_rate = fx_data.get("rates", {}).get("PKR")
            if pkr_rate:
                usd_pkr = Decimal(str(pkr_rate))
        logger.info("open.er-api: USD/PKR=%.4f", usd_pkr)
    except Exception as exc:
        logger.warning("open.er-api fetch failed: %s — using fallback USD/PKR", exc)

    # ── Apply fallbacks for any missing values ────────────────────────────────
    usd_pkr    = usd_pkr    or _FALLBACK_USD_PKR
    gold_usd   = gold_usd   or (_FALLBACK_GOLD_PKR_PER_GRAM * TROY_OZ_TO_GRAMS / usd_pkr)
    silver_usd = silver_usd or (_FALLBACK_SILVER_PKR_PER_GRAM * TROY_OZ_TO_GRAMS / usd_pkr)

    if gold_usd and silver_usd and usd_pkr:
        source = "live"

    # ── Convert USD/troy-oz → PKR/gram ────────────────────────────────────────
    gold_pkr_per_gram   = (gold_usd   * usd_pkr / TROY_OZ_TO_GRAMS).quantize(Decimal("0.01"))
    silver_pkr_per_gram = (silver_usd * usd_pkr / TROY_OZ_TO_GRAMS).quantize(Decimal("0.01"))
    nisab_pkr           = (NISAB_SILVER_GRAMS * silver_pkr_per_gram).quantize(Decimal("0.01"))

    return {
        "gold_per_oz_usd":      gold_usd.quantize(Decimal("0.01")),
        "silver_per_oz_usd":    silver_usd.quantize(Decimal("0.01")),
        "usd_pkr":              usd_pkr.quantize(Decimal("0.0001")),
        "gold_per_gram_pkr":    gold_pkr_per_gram,
        "silver_per_gram_pkr":  silver_pkr_per_gram,
        "nisab_threshold_pkr":  nisab_pkr,
        "source":               source,
        "fetched_at":           datetime.now(timezone.utc).isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# CALCULATE
# ─────────────────────────────────────────────────────────────────────────────

async def calculate_zakat(
    db: AsyncSession,
    user: User,
    data: ZakatCalculateRequest,
) -> ZakatCalculation:
    """
    Calculate Zakat based on submitted assets.

    Rate auto-fetch:
      - If auto_fetch_rates=True AND gold_rate_per_gram == 0 → fetch live rate
      - If auto_fetch_rates=True AND silver_rate_per_gram == 0 → fetch live rate
      Rates entered manually by the user override live rates.

    All 9 Zakatable categories are included:
      wallet balance, cash at hand, gold, silver, business inventory,
      stocks, crypto, property (investment), other assets minus debts.

    Saves and returns the ZakatCalculation record (no wallet deduction here).
    """
    gold_rate   = data.gold_rate_per_gram
    silver_rate = data.silver_rate_per_gram
    gold_source   = "manual"
    silver_source = "manual"
    usd_pkr_rate: Decimal | None = None

    # ── Auto-fetch live rates if not provided ─────────────────────────────────
    if data.auto_fetch_rates and (gold_rate == 0 or silver_rate == 0):
        try:
            live = await fetch_live_zakat_rates()
            if gold_rate == 0:
                gold_rate   = live["gold_per_gram_pkr"]
                gold_source = live["source"]
            if silver_rate == 0:
                silver_rate   = live["silver_per_gram_pkr"]
                silver_source = live["source"]
            usd_pkr_rate = live["usd_pkr"]
        except Exception as exc:
            logger.error("Live rate fetch failed during calculate_zakat: %s", exc)
            # Keep provided values; use fallback if still 0
            if gold_rate == 0:
                gold_rate   = _FALLBACK_GOLD_PKR_PER_GRAM
                gold_source = "fallback"
            if silver_rate == 0:
                silver_rate   = _FALLBACK_SILVER_PKR_PER_GRAM
                silver_source = "fallback"

    # ── Wallet balance ────────────────────────────────────────────────────────
    wallet_balance = Decimal("0.00")
    if data.include_wallet_balance:
        result = await db.execute(
            select(Wallet).where(Wallet.user_id == user.id)
        )
        wallet = result.scalar_one_or_none()
        if wallet:
            wallet_balance = wallet.balance

    # ── Nisab threshold (silver standard, Hanafi) ─────────────────────────────
    nisab_threshold = (NISAB_SILVER_GRAMS * silver_rate).quantize(Decimal("0.01"))
    if nisab_threshold == 0:
        nisab_threshold = _FALLBACK_SILVER_PKR_PER_GRAM * NISAB_SILVER_GRAMS

    # ── Compute total Zakatable wealth ────────────────────────────────────────
    # Gold: use direct PKR value if provided, otherwise grams × rate
    if data.gold_value_pkr > 0:
        gold_value = data.gold_value_pkr
    else:
        gold_value = data.gold_grams * gold_rate

    # Silver: same logic
    if data.silver_value_pkr > 0:
        silver_value = data.silver_value_pkr
    else:
        silver_value = data.silver_grams * silver_rate

    total_wealth = (
        wallet_balance
        + data.cash_at_hand
        + gold_value
        + silver_value
        + data.business_inventory
        + data.stocks_value
        + data.crypto_value
        + data.property_value
        + data.other_assets
        + data.receivables
        - data.debts
    )
    total_wealth = max(Decimal("0.00"), total_wealth).quantize(Decimal("0.01"))

    is_zakat_applicable = total_wealth >= nisab_threshold
    zakat_due = (
        (total_wealth * ZAKAT_RATE).quantize(Decimal("0.01"))
        if is_zakat_applicable
        else Decimal("0.00")
    )

    record = ZakatCalculation(
        user_id=user.id,
        wallet_balance=wallet_balance,
        cash_at_hand=data.cash_at_hand,
        gold_grams=data.gold_grams,
        gold_rate_per_gram=gold_rate,
        silver_grams=data.silver_grams,
        silver_rate_per_gram=silver_rate,
        business_inventory=data.business_inventory,
        stocks_value=data.stocks_value,
        crypto_value=data.crypto_value,
        property_value=data.property_value,
        other_assets=data.other_assets,
        receivables=data.receivables,
        debts=data.debts,
        nisab_threshold=nisab_threshold,
        total_wealth=total_wealth,
        zakat_due=zakat_due,
        is_zakat_applicable=is_zakat_applicable,
        gold_rate_source=gold_source,
        silver_rate_source=silver_source,
        usd_pkr_rate=usd_pkr_rate,
        paid_from_wallet=False,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


async def pay_zakat_from_wallet(
    db: AsyncSession,
    user: User,
    calculation_id: UUID,
) -> ZakatCalculation:
    """
    Deduct zakat_due from user's wallet and mark calculation as paid.
    Uses ACID-safe SELECT FOR UPDATE.
    """
    result = await db.execute(
        select(ZakatCalculation).where(
            ZakatCalculation.id == calculation_id,
            ZakatCalculation.user_id == user.id,
        )
    )
    calc = result.scalar_one_or_none()
    if not calc:
        raise HTTPException(status_code=404, detail="Calculation not found")
    if calc.paid_from_wallet:
        raise HTTPException(status_code=400, detail="Zakat already paid for this calculation")
    if not calc.is_zakat_applicable or calc.zakat_due <= 0:
        raise HTTPException(status_code=400, detail="No Zakat due on this calculation")

    async with db.begin_nested():
        wallet_result = await db.execute(
            select(Wallet).where(Wallet.user_id == user.id).with_for_update()
        )
        wallet = wallet_result.scalar_one()

        if wallet.is_frozen:
            raise WalletFrozenError()
        if wallet.balance < calc.zakat_due:
            raise InsufficientBalanceError()

        wallet.balance -= calc.zakat_due

        txn = Transaction(
            reference_number=security.generate_reference(),
            sender_id=user.id,
            recipient_id=user.id,  # self-transaction (charity deduction)
            amount=calc.zakat_due,
            fee=Decimal("0.00"),
            type="zakat",
            status="completed",
            description="Zakat payment",
            completed_at=datetime.now(timezone.utc),
        )
        db.add(txn)
        await db.flush()

        calc.paid_from_wallet = True
        calc.payment_txn_id = txn.id
        calc.paid_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(calc)
    return calc


async def get_zakat_history(
    db: AsyncSession,
    user_id: UUID,
) -> list[ZakatCalculation]:
    result = await db.execute(
        select(ZakatCalculation)
        .where(ZakatCalculation.user_id == user_id)
        .order_by(ZakatCalculation.created_at.desc())
        .limit(20)
    )
    return list(result.scalars().all())
