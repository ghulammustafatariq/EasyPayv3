from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_verified_user, get_db
from app.models.database import User
from app.schemas.base import success_response
from app.schemas.zakat import (
    ZakatCalculateRequest,
    ZakatPayRequest,
    ZakatCalculationResponse,
    ZakatLiveRatesResponse,
)
from app.services import zakat_service

router = APIRouter(prefix="/zakat", tags=["Zakat"])


@router.get("/live-rates")
async def get_live_zakat_rates(
    current_user: User = Depends(get_current_verified_user),
):
    """
    Return live PKR gold & silver rates and the current Nisab threshold.
    Rates are sourced from metals.live (spot USD/oz) + open.er-api.com (USD/PKR).
    """
    rates = await zakat_service.fetch_live_zakat_rates()
    return success_response(
        message="Live Zakat rates fetched successfully",
        data=ZakatLiveRatesResponse(
            gold_rate_per_gram_pkr=rates["gold_per_gram_pkr"],
            silver_rate_per_gram_pkr=rates["silver_per_gram_pkr"],
            nisab_threshold_pkr=rates["nisab_threshold_pkr"],
            usd_pkr_rate=rates["usd_pkr"],
            gold_per_oz_usd=rates["gold_per_oz_usd"],
            silver_per_oz_usd=rates["silver_per_oz_usd"],
            source=rates["source"],
            fetched_at=rates["fetched_at"],
        ).model_dump(),
    )


@router.post("/calculate")
async def calculate_zakat(
    data: ZakatCalculateRequest,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Calculate Zakat from submitted assets. Saves and returns the calculation.
    Pass auto_fetch_rates=true (default) to auto-fill live gold/silver PKR rates.
    """
    result = await zakat_service.calculate_zakat(db, current_user, data)
    return success_response(
        message="Zakat calculated successfully",
        data=ZakatCalculationResponse.model_validate(result).model_dump(),
    )


@router.post("/pay")
async def pay_zakat(
    data: ZakatPayRequest,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """Deduct zakat_due from wallet and mark calculation as paid."""
    result = await zakat_service.pay_zakat_from_wallet(db, current_user, data.calculation_id)
    return success_response(
        message="Zakat payment successful",
        data=ZakatCalculationResponse.model_validate(result).model_dump(),
    )


@router.get("/history")
async def get_zakat_history(
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """Return last 20 Zakat calculations for this user."""
    history = await zakat_service.get_zakat_history(db, current_user.id)
    return success_response(
        message="Zakat calculation history retrieved",
        data={
            "calculations": [
                ZakatCalculationResponse.model_validate(c).model_dump()
                for c in history
            ]
        },
    )
