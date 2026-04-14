"""
app/schemas/business.py — EasyPay v3.0 Business Schemas

Rule 8:  ALL Cloudinary uploads for business documents use type="private".
Rule 14: NEVER auto-approve below 0.85 AI confidence — enforced in business_service.py.
         Schemas carry ai_confidence_score so routes can include it in responses.
"""
import uuid
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, Field


# ══════════════════════════════════════════════════════════════════════════════
# REQUEST SCHEMAS
# ══════════════════════════════════════════════════════════════════════════════

class BusinessRegisterRequest(BaseModel):
    """
    Submit a business profile for Tier 4 verification.
    Requires Tier 2 (cnic_verified + biometric_verified) to proceed.
    """

    business_name: str = Field(
        ...,
        min_length=2,
        max_length=200,
        examples=["Khan Traders"],
    )
    business_type: str = Field(
        ...,
        description="sole_proprietor | registered_company",
        examples=["sole_proprietor"],
    )
    ntn_number: Optional[str] = Field(
        None,
        max_length=20,
        description="National Tax Number (if available)",
        examples=["1234567-8"],
    )
    business_address: Optional[str] = Field(
        None,
        max_length=500,
        examples=["Shop 5, Main Market, Karachi"],
    )


class BusinessDocumentUploadRequest(BaseModel):
    """
    Upload a single supporting document for business verification.
    Image must be base64-encoded; the service uploads to Cloudinary
    with type="private" (Rule 8) and stores the secure URL.
    """

    document_type: str = Field(
        ...,
        max_length=50,
        description=(
            "One of: NTN Certificate | Business Registration | Bank Statement | "
            "Shop Rent Agreement | SECP Incorporation Certificate | "
            "Memorandum of Association"
        ),
        examples=["NTN Certificate"],
    )
    image_base64: str = Field(
        ...,
        description="Base64-encoded document image (JPEG / PNG, without data URI prefix)",
    )


# ══════════════════════════════════════════════════════════════════════════════
# RESPONSE SCHEMAS
# ══════════════════════════════════════════════════════════════════════════════

class BusinessVerificationStatus(BaseModel):
    """
    Current verification state of the user's business profile.
    ai_confidence_score is included for transparency.
    Rule 14: score >= 0.85 → auto-approved; 0.60–0.85 → manual review;
             < 0.60 → rejected. Enforcement lives in business_service.py.
    """

    status: str = Field(
        ...,
        description="pending | under_review | approved | rejected",
    )
    ai_confidence_score: Optional[Decimal] = Field(
        None,
        ge=Decimal("0.000"),
        le=Decimal("1.000"),
        description="DeepSeek document analysis confidence (3 decimal places)",
    )
    rejection_reasons: List[str] = Field(
        default_factory=list,
        description="List of human-readable rejection reasons, populated if status=rejected",
    )

    model_config = {"from_attributes": True}
