"""
app/api/v1/business.py — EasyPay v3.0 Business Verification Endpoints

Routes:
  POST /business/register           → 201  (Tier 2: cnic_verified + biometric_verified)
  POST /business/upload-documents   → 200
  POST /business/submit-for-review  → 200  (Rate: 2/day)
  GET  /business/status             → 200
  GET  /business/supported-documents → 200  (No auth required)
  POST /business/resubmit           → 200

Rules Enforced:
  Rule  8 — Cloudinary type="private" (enforced in business_service.py).
  Rule 14 — NEVER auto-approve below 0.85 confidence (enforced in business_service.py).
  SlowAPI — submit-for-review rate-limited to 2/day; request: Request must be
             the FIRST positional parameter in rate-limited handlers.
"""
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_verified_user, get_db
from app.core.exceptions import EasyPayException
from app.core.limiter import limiter
from app.models.database import BusinessProfile, User
from app.schemas.base import success_response
from app.schemas.business import (
    BusinessDocumentUploadRequest,
    BusinessRegisterRequest,
    BusinessVerificationStatus,
)
from app.services import business_service

router = APIRouter(prefix="/business", tags=["Business Verification"])


# ══════════════════════════════════════════════════════════════════════════════
# POST /business/register  →  201
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register_business(
    body: BusinessRegisterRequest,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Register a new business profile for Tier 4 verification.
    Requires Tier 2 (cnic_verified + biometric_verified).
    """
    profile = await business_service.register_business(
        db, str(current_user.id), body
    )
    return success_response(
        message="Business profile created successfully.",
        data={
            "business_id": str(profile.id),
            "business_name": profile.business_name,
            "business_type": profile.business_type,
            "verification_status": profile.verification_status,
        },
    )


# ══════════════════════════════════════════════════════════════════════════════
# POST /business/upload-documents  →  200
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/upload-documents", status_code=status.HTTP_200_OK)
async def upload_business_document(
    body: BusinessDocumentUploadRequest,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload a single supporting document.
    Rule 8: stored to Cloudinary as type="private".
    Re-uploading the same document_type replaces the previous record.
    """
    doc = await business_service.upload_business_document(
        db, str(current_user.id), body
    )
    return success_response(
        message=f"Document '{doc.document_type}' uploaded successfully.",
        data={
            "document_id": str(doc.id),
            "document_type": doc.document_type,
            "uploaded_at": doc.uploaded_at.isoformat() if doc.uploaded_at else None,
        },
    )


# ══════════════════════════════════════════════════════════════════════════════
# POST /business/submit-for-review  →  200  (Rate: 2/day)
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/submit-for-review", status_code=status.HTTP_200_OK)
@limiter.limit("2/day")
async def submit_for_review(
    request: Request,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Trigger DeepSeek Vision AI review on all uploaded documents.

      ALL valid + avg >= 0.85  → auto-approved → Tier 4
      ALL valid + avg 0.60–0.84 → manual review
      Any invalid              → rejected

    Rate-limited to 2 submissions per day (SlowAPI).
    """
    result = await business_service.submit_for_ai_review(db, str(current_user.id))
    return success_response(
        message=result.get("message", "Business documents submitted for AI review."),
        data=result,
    )


# ══════════════════════════════════════════════════════════════════════════════
# GET /business/status  →  200
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/status", status_code=status.HTTP_200_OK)
async def get_business_status(
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the current verification status of the user's business profile."""
    result = await db.execute(
        select(BusinessProfile).where(BusinessProfile.user_id == current_user.id)
    )
    profile = result.scalar_one_or_none()
    if profile is None:
        raise EasyPayException(
            detail="No business profile found. Please register your business first.",
            error_code="BUSINESS_NOT_FOUND",
        )

    return success_response(
        message="Business verification status retrieved.",
        data=status_data.model_dump()
    )


# ══════════════════════════════════════════════════════════════════════════════
# GET /business/supported-documents  →  200  (No auth)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/supported-documents", status_code=status.HTTP_200_OK)
async def get_supported_documents():
    """
    Returns the static list of accepted document types for business verification.
    No authentication required.
    """
    return success_response(
        message="Supported documents retrieved.",
        data={"supported_documents": business_service.SUPPORTED_DOCUMENTS}
    )


# ══════════════════════════════════════════════════════════════════════════════
# POST /business/resubmit  →  200
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/resubmit", status_code=status.HTTP_200_OK)
async def resubmit_business(
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Reset a rejected business profile back to 'pending'.
    Only allowed when verification_status == 'rejected'.
    Clears rejection_reasons and resets all document AI verdicts.
    """
    result = await business_service.resubmit_business(db, str(current_user.id))
    return success_response(
        message="Business profile reset. Please upload updated documents and resubmit for review.",
        data=result,
    )
