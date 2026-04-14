from pydantic import BaseModel, Field
from decimal import Decimal
from typing import Optional
from uuid import UUID
from datetime import datetime


class ZakatCalculateRequest(BaseModel):
    include_wallet_balance: bool = True
    # Cash & receivables
    cash_at_hand: Decimal = Field(Decimal("0.00"), ge=0)
    receivables: Decimal = Field(Decimal("0.00"), ge=0)
    debts: Decimal = Field(Decimal("0.00"), ge=0)
    # Precious metals — two ways to enter (mutually exclusive per metal):
    #   Option A: direct PKR market value  →  gold_value_pkr / silver_value_pkr
    #   Option B: weight in grams + rate   →  gold_grams + gold_rate_per_gram
    # If gold_value_pkr > 0 it takes precedence; grams are ignored for that metal.
    gold_value_pkr: Decimal = Field(
        Decimal("0.00"), ge=0,
        description="Total market value of gold in PKR. Overrides gold_grams when > 0."
    )
    gold_grams: Decimal = Field(Decimal("0.000"), ge=0)
    gold_rate_per_gram: Decimal = Field(
        Decimal("0.00"), ge=0,
        description="PKR per gram. Leave 0 to auto-fetch live rate."
    )
    silver_value_pkr: Decimal = Field(
        Decimal("0.00"), ge=0,
        description="Total market value of silver in PKR. Overrides silver_grams when > 0."
    )
    silver_grams: Decimal = Field(Decimal("0.000"), ge=0)
    silver_rate_per_gram: Decimal = Field(
        Decimal("0.00"), ge=0,
        description="PKR per gram. Leave 0 to auto-fetch live rate."
    )
    # Extended asset categories
    business_inventory: Decimal = Field(Decimal("0.00"), ge=0)
    stocks_value: Decimal = Field(Decimal("0.00"), ge=0, description="Market value of stocks/shares (PKR)")
    crypto_value: Decimal = Field(Decimal("0.00"), ge=0, description="Current value of cryptocurrency (PKR)")
    property_value: Decimal = Field(Decimal("0.00"), ge=0, description="Investment property value (PKR). Exclude primary residence.")
    other_assets: Decimal = Field(Decimal("0.00"), ge=0, description="Any other Zakatable assets (PKR)")
    # Rate fetch behaviour
    auto_fetch_rates: bool = Field(
        True,
        description="Automatically fetch live PKR gold/silver rates. Overrides zero-value rates."
    )


class ZakatPayRequest(BaseModel):
    calculation_id: UUID


class ZakatCalculationResponse(BaseModel):
    id: UUID
    wallet_balance: Decimal
    cash_at_hand: Decimal
    gold_grams: Decimal
    gold_rate_per_gram: Decimal
    silver_grams: Decimal
    silver_rate_per_gram: Decimal
    business_inventory: Decimal
    stocks_value: Decimal
    crypto_value: Decimal
    property_value: Decimal
    other_assets: Decimal
    receivables: Decimal
    debts: Decimal
    nisab_threshold: Decimal
    total_wealth: Decimal
    zakat_due: Decimal
    is_zakat_applicable: bool
    paid_from_wallet: bool
    paid_at: Optional[datetime] = None
    created_at: datetime
    # Rate provenance
    gold_rate_source: str = "manual"
    silver_rate_source: str = "manual"
    usd_pkr_rate: Optional[Decimal] = None

    model_config = {"from_attributes": True}


class ZakatLiveRatesResponse(BaseModel):
    gold_rate_per_gram_pkr: Decimal
    silver_rate_per_gram_pkr: Decimal
    nisab_threshold_pkr: Decimal
    usd_pkr_rate: Decimal
    gold_per_oz_usd: Decimal
    silver_per_oz_usd: Decimal
    source: str
    fetched_at: str
