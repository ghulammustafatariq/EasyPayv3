"""
app/services/business_service.py — EasyPay v3.0 Business Verification Service

Critical Rules Enforced:
  Rule  8 — ALL Cloudinary uploads use type="private"
             Folder: easypay/business/{user_id}/{sanitized_document_type}
  Rule 14 — NEVER auto-approve below 0.85 AI confidence
  Point 6 — Strip DeepSeek markdown fences before json.loads()
             (handled internally by call_deepseek_vision via _parse_response)
  Point 15 — calculate_and_save_tier() MUST be called after business approval
  Sync    — user.business_status AND BusinessProfile.verification_status
             must always be updated together
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import base64

import cloudinary
import cloudinary.uploader
import cloudinary.utils
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deepseek import call_deepseek_chat
from app.core.dependencies import calculate_and_save_tier
from app.core.exceptions import AIServiceUnavailableError, EasyPayException
from app.models.database import (
    BusinessDocument,
    BusinessProfile,
    User,
)
from app.schemas.business import BusinessDocumentUploadRequest, BusinessRegisterRequest
from app.services import notification_service

logger = logging.getLogger(__name__)

_BUSINESS_PLATINUM_DAILY_LIMIT = Decimal("5000000.00")

# ── SDK configuration ────────────────────────────────────────────────────────
cloudinary.config(
    cloud_name=settings.CLOUDINARY_CLOUD_NAME,
    api_key=settings.CLOUDINARY_API_KEY,
    api_secret=settings.CLOUDINARY_API_SECRET,
)
# DeepSeek is used for document vision review (call_deepseek_vision)

# ── Supported document types (also exposed via GET /business/supported-documents) ─
SUPPORTED_DOCUMENTS: list[str] = [
    "NTN Certificate",
    "Business Registration",
    "Bank Statement",
    "Shop Rent Agreement",
    "SECP Incorporation Certificate",
    "Memorandum of Association",
]

# ── httpx timeouts ───────────────────────────────────────────────────────────
_TIMEOUT     = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=5.0)
_OCR_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=5.0)

# ── OCR.space endpoint ───────────────────────────────────────────────────────
_OCR_SPACE_URL = "https://api.ocr.space/parse/image"

# ── DeepSeek text verification prompt template ───────────────────────────────
_TEXT_VERIFY_PROMPT = """\
You are a financial compliance officer at a Pakistani fintech company reviewing business documents.
The following text was extracted via OCR from a document submitted as: "{document_type}".

OCR Text:
---
{ocr_text}
---

Analyze this text and return ONLY a valid JSON object with no additional text, markdown, or explanation:
{{"is_valid": true, "confidence": 0.000, "document_type_detected": "...", "rejection_reason": null}}

Rules:
- is_valid: true only if the text contains clear evidence of an official business document that matches or is closely related to "{document_type}"
- confidence: float 0.0–1.0 rounded to 3 decimal places
- document_type_detected: your best guess at what this document actually is
- rejection_reason: null when is_valid is true; brief human-readable reason when false (e.g. "Text is too blurry", "Document type mismatch", "No official stamp or registration number found")
"""


# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _sanitize_doc_type(document_type: str) -> str:
    """Convert 'NTN Certificate' → 'ntn_certificate' for Cloudinary paths."""
    return document_type.replace(" ", "_").lower()


def _strip_fences(text: str) -> str:
    """Strip Markdown code fences Gemini may wrap around JSON. Point 6."""
    text = re.sub(r"```(?:json)?\s*", "", text)
    text = text.replace("```", "")
    return text.strip()


async def _run_ocr_on_document(image_base64: str) -> str:
    """
    Extract text from a business document image using OCR.space (free tier).

    Uses OCR Engine 2 (optimised for complex layouts and printed text).
    Falls back to empty string on any error — caller handles empty-text case.
    """
    api_key = settings.OCR_SPACE_API_KEY or "helloworld"  # free demo key as fallback
    try:
        async with httpx.AsyncClient(timeout=_OCR_TIMEOUT) as client:
            resp = await client.post(
                _OCR_SPACE_URL,
                data={
                    "apikey": api_key,
                    "base64Image": f"data:image/jpeg;base64,{image_base64}",
                    "language": "eng",
                    "isOverlayRequired": "false",
                    "OCREngine": "2",
                    "detectOrientation": "true",
                    "scale": "true",
                },
            )
            resp.raise_for_status()
        result = resp.json()
        if result.get("IsErroredOnProcessing", False):
            err = result.get("ErrorMessage") or "unknown OCR error"
            logger.warning("OCR.space error: %s", err)
            return ""
        parsed = result.get("ParsedResults", [])
        if not parsed:
            return ""
        text = parsed[0].get("ParsedText", "").strip()
        logger.info("OCR.space extracted %d chars for business doc", len(text))
        return text
    except Exception as exc:
        logger.warning("OCR.space request failed: %s", exc)
        return ""


async def _verify_doc_text_with_deepseek(
    ocr_text: str,
    document_type: str,
) -> dict[str, Any]:
    """
    Send OCR-extracted text to DeepSeek Chat to determine if it is a valid
    official business document of the claimed type.

    Returns a dict with keys: is_valid, confidence, document_type_detected, rejection_reason.
    Falls back to a low-confidence rejection if OCR returned no text.
    """
    if not ocr_text.strip():
        return {
            "is_valid": False,
            "confidence": 0.1,
            "document_type_detected": "unknown",
            "rejection_reason": (
                "No readable text could be extracted from the document. "
                "Ensure the image is clear and well-lit."
            ),
        }

    prompt = _TEXT_VERIFY_PROMPT.format(
        document_type=document_type,
        ocr_text=ocr_text[:4000],  # cap at 4 000 chars to stay within token budget
    )
    try:
        return await call_deepseek_chat(prompt, json_mode=True)
    except AIServiceUnavailableError:
        raise
    except Exception as exc:
        logger.error("DeepSeek text verification failed: %s", exc)
        raise AIServiceUnavailableError(
            detail="Document AI review service is temporarily unavailable.",
            error_code="BUSINESS_AI_UNAVAILABLE",
        ) from exc


async def _download_cloudinary_image_bytes(public_id: str) -> bytes:
    """
    Generate a signed download URL for a private Cloudinary asset and
    download the raw image bytes with httpx.
    Used by submit_for_ai_review to run OCR on legacy documents.
    """
    signed_url, _ = cloudinary.utils.cloudinary_url(
        public_id,
        resource_type="image",
        type="private",
        sign_url=True,
        secure=True,
    )
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get(signed_url)
        response.raise_for_status()
    return response.content


# ─────────────────────────────────────────────────────────────────────────────
# register_business
# ─────────────────────────────────────────────────────────────────────────────

async def register_business(
    db: AsyncSession,
    user_id: str,
    data: BusinessRegisterRequest,
) -> BusinessProfile:
    """
    Register a new business profile.

    Requirements:
      - User must be Tier 2 (cnic_verified AND biometric_verified).
      - Only one BusinessProfile per user (unique constraint on user_id).
    """
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise EasyPayException(
            detail="User not found.",
            error_code="USER_NOT_FOUND",
        )

    # Tier 2 gate: cnic_verified + biometric_verified required
    if not (user.cnic_verified and user.biometric_verified):
        raise EasyPayException(
            detail=(
                "Business registration requires KYC Tier 2. "
                "Complete CNIC upload and biometric verification first."
            ),
            error_code="KYC_TIER_INSUFFICIENT",
        )

    # Prevent duplicate registration
    existing_result = await db.execute(
        select(BusinessProfile).where(BusinessProfile.user_id == user_id)
    )
    if existing_result.scalar_one_or_none() is not None:
        raise EasyPayException(
            detail="A business profile already exists for this account.",
            error_code="BUSINESS_ALREADY_REGISTERED",
        )

    profile = BusinessProfile(
        user_id=user.id,
        business_name=data.business_name,
        business_type=data.business_type,
        ntn_number=data.ntn_number,
        business_address=data.business_address,
        verification_status="pending",
    )
    db.add(profile)

    # Mark user account type as business
    user.account_type = "business"

    await db.commit()
    await db.refresh(profile)
    return profile


# ─────────────────────────────────────────────────────────────────────────────
# upload_business_document
# ─────────────────────────────────────────────────────────────────────────────

async def upload_business_document(
    db: AsyncSession,
    user_id: str,
    data: BusinessDocumentUploadRequest,
) -> BusinessDocument:
    """
    Upload a single business document to Cloudinary (private) and persist a
    BusinessDocument record.

    Rule 8: Cloudinary type="private".
    Folder: easypay/business/{user_id}
    Public ID: {sanitized_document_type}
    Idempotent: re-uploading the same document_type replaces the previous record.
    """
    # Ensure user + business profile exist (auto-create profile when missing)
    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if user is None:
        raise EasyPayException(
            detail="User not found.",
            error_code="USER_NOT_FOUND",
        )

    profile_result = await db.execute(
        select(BusinessProfile).where(BusinessProfile.user_id == user_id)
    )
    profile = profile_result.scalar_one_or_none()
    if profile is None:
        profile = BusinessProfile(
            user_id=user.id,
            business_name=f"{user.full_name} Business",
            business_type="sole_proprietor",
            verification_status="pending",
        )
        db.add(profile)
        user.account_type = "business"
        user.business_status = "pending"
        await db.flush()

    if profile.verification_status not in ("pending", "rejected"):
        raise EasyPayException(
            detail=(
                f"Cannot upload documents when business status is "
                f"'{profile.verification_status}'."
            ),
            error_code="BUSINESS_INVALID_STATE",
        )

    # ── Cloudinary private upload (Rule 8) ────────────────────────────────────
    sanitized = _sanitize_doc_type(data.document_type)
    doc_uri = f"data:image/jpeg;base64,{data.image_base64}"

    upload_result = cloudinary.uploader.upload(
        doc_uri,
        folder=f"easypay/business/{user_id}",
        public_id=sanitized,
        overwrite=True,
        resource_type="image",
        type="private",
    )
    secure_url: str = upload_result["secure_url"]

    # ── OCR → DeepSeek text verification (immediate per-document verdict) ─────
    logger.info(
        "Running OCR+AI verification for '%s' (user %s)",
        data.document_type, user_id,
    )
    ocr_text = await _run_ocr_on_document(data.image_base64)
    verdict  = await _verify_doc_text_with_deepseek(ocr_text, data.document_type)

    is_valid     = bool(verdict.get("is_valid", False))
    raw_conf     = verdict.get("confidence", 0.0)
    confidence   = Decimal(str(raw_conf)).quantize(Decimal("0.001"))

    logger.info(
        "Doc verdict for user %s | type='%s' | valid=%s | conf=%.3f",
        user_id, data.document_type, is_valid, confidence,
    )

    # ── Upsert: replace existing doc of same type ─────────────────────────────
    existing_result = await db.execute(
        select(BusinessDocument).where(
            BusinessDocument.business_id == profile.id,
            BusinessDocument.document_type == data.document_type,
        )
    )
    existing = existing_result.scalar_one_or_none()

    if existing is not None:
        existing.cloudinary_url  = secure_url
        existing.ai_verdict      = verdict
        existing.is_valid        = is_valid
        existing.confidence_score = confidence
        await db.commit()
        await db.refresh(existing)
        return existing

    doc = BusinessDocument(
        business_id=profile.id,
        document_type=data.document_type,
        cloudinary_url=secure_url,
        ai_verdict=verdict,
        is_valid=is_valid,
        confidence_score=confidence,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return doc


# ─────────────────────────────────────────────────────────────────────────────
# submit_for_ai_review
# ─────────────────────────────────────────────────────────────────────────────

async def submit_for_ai_review(
    db: AsyncSession,
    user_id: str,
) -> dict[str, Any]:
    """
    Run OCR.space text extraction + DeepSeek Chat text verification on every
    uploaded document and apply the verdict:

      ALL valid + avg confidence >= 0.85  → auto_approve  (Rule 14)
      ALL valid + avg confidence 0.60–0.84 → flag_manual_review
      Any invalid document                 → auto_reject

    Point 6: DeepSeek JSON is parsed in json_mode=True via call_deepseek_chat.
    Point 15: calculate_and_save_tier() called after approval → Tier 4.
    """
    # Fetch user + profile (auto-create profile when missing)
    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if user is None:
        raise EasyPayException(
            detail="User not found.",
            error_code="USER_NOT_FOUND",
        )

    # Fetch profile
    profile_result = await db.execute(
        select(BusinessProfile).where(BusinessProfile.user_id == user_id)
    )
    profile = profile_result.scalar_one_or_none()
    if profile is None:
        profile = BusinessProfile(
            user_id=user.id,
            business_name=f"{user.full_name} Business",
            business_type="sole_proprietor",
            verification_status="pending",
        )
        db.add(profile)
        user.account_type = "business"
        user.business_status = "pending"
        await db.flush()

    if profile.verification_status not in ("pending", "rejected"):
        raise EasyPayException(
            detail=(
                f"Cannot submit for review when business status is "
                f"'{profile.verification_status}'."
            ),
            error_code="BUSINESS_INVALID_STATE",
        )

    # Fetch all documents
    docs_result = await db.execute(
        select(BusinessDocument).where(BusinessDocument.business_id == profile.id)
    )
    documents = list(docs_result.scalars().all())

    if not documents:
        raise EasyPayException(
            detail=(
                "No documents uploaded. "
                "Please upload at least one business document before submitting."
            ),
            error_code="BUSINESS_NO_DOCUMENTS",
        )

    # Mark profile as under review while processing
    profile.verification_status = "under_review"
    profile.submitted_at = datetime.now(timezone.utc)
    await db.commit()

    # ── AI review loop ────────────────────────────────────────────────────────
    # Documents get their verdict at upload time (OCR + DeepSeek Chat).
    # If a document somehow has no verdict yet (e.g. uploaded before this
    # pipeline was introduced), run OCR + text verification now.
    rejection_reasons: list[str] = []
    confidence_scores: list[Decimal] = []
    all_valid = True

    for doc in documents:
        if doc.ai_verdict is not None and doc.is_valid is not None:
            # Verdict already stored from upload-time OCR+AI — reuse it
            is_valid = doc.is_valid
            confidence = doc.confidence_score or Decimal("0.000")
            rejection_reason: str | None = (
                (doc.ai_verdict or {}).get("rejection_reason") if not is_valid else None
            )
        else:
            # Fallback: OCR + DeepSeek text verification (legacy docs)
            public_id = f"easypay/business/{user_id}/{_sanitize_doc_type(doc.document_type)}"
            try:
                image_bytes = await _download_cloudinary_image_bytes(public_id)
            except (httpx.HTTPError, Exception) as exc:
                logger.error(
                    "Failed to download Cloudinary document %s for user %s: %s",
                    public_id, user_id, exc,
                )
                raise AIServiceUnavailableError(
                    detail="Could not retrieve business document for AI review. Please try again.",
                    error_code="BUSINESS_DOC_UNAVAILABLE",
                ) from exc

            image_b64 = base64.b64encode(image_bytes).decode("utf-8")
            ocr_text = await _run_ocr_on_document(image_b64)
            verdict: dict[str, Any] = await _verify_doc_text_with_deepseek(
                ocr_text,
                doc.document_type,
            )
            is_valid = bool(verdict.get("is_valid", False))
            raw_confidence = verdict.get("confidence", 0.0)
            confidence = Decimal(str(raw_confidence)).quantize(Decimal("0.001"))
            rejection_reason = verdict.get("rejection_reason")

            doc.ai_verdict = verdict
            doc.is_valid = is_valid
            doc.confidence_score = confidence

        confidence_scores.append(confidence)
        if not is_valid:
            all_valid = False
            reason = rejection_reason or f"Document '{doc.document_type}' failed AI validation."
            rejection_reasons.append(reason)

    await db.commit()

    # ── Apply verdict ─────────────────────────────────────────────────────────
    avg_confidence = (
        sum(confidence_scores) / Decimal(len(confidence_scores))
        if confidence_scores
        else Decimal("0.000")
    )
    avg_confidence = avg_confidence.quantize(Decimal("0.001"))

    profile.ai_confidence_score = avg_confidence
    profile.reviewed_at = datetime.now(timezone.utc)

    if not all_valid:
        # Any invalid document → reject (Rule 14)
        profile.verification_status = "rejected"
        profile.rejection_reasons = rejection_reasons
        user.business_status = "rejected"
        await notification_service.create_notification(
            db, user.id,
            title="Business Verification Rejected",
            body=f"Your business verification was rejected. Reason(s): {'; '.join(rejection_reasons)}",
            type="system",
        )
        return {
            "verdict": "rejected",
            "message": "Business verification rejected due to invalid documents.",
            "rejection_reasons": rejection_reasons,
            "ai_confidence_score": str(avg_confidence),
        }

    if avg_confidence >= Decimal("0.85"):
        # ALL valid + avg >= 0.85 → auto-approve (Rule 14)
        profile.verification_status = "approved"
        profile.rejection_reasons = []
        user.business_status = "approved"
        user.account_type = "business"
        await calculate_and_save_tier(db, user)  # → Tier 4 (Point 15)
        user.daily_limit_override = _BUSINESS_PLATINUM_DAILY_LIMIT
        if user.wallet is not None:
            user.wallet.daily_limit = _BUSINESS_PLATINUM_DAILY_LIMIT
        await notification_service.create_notification(
            db, user.id,
            title="Business Platinum Approved ✅",
            body="Congratulations! Your business is verified. Your daily limit is now PKR 5,000,000 (Business Platinum).",
            type="system",
        )
        return {
            "verdict": "approved",
            "message": "Business verification approved. Upgraded to Business Platinum with PKR 5,000,000 daily limit.",
            "ai_confidence_score": str(avg_confidence),
        }

    # avg 0.60–0.84 → manual review
    profile.verification_status = "under_review"
    profile.rejection_reasons = []
    user.business_status = "under_review"
    await notification_service.create_notification(
        db, user.id,
        title="Business Under Review",
        body="Your business documents are under manual review. We will notify you shortly.",
        type="system",
    )
    return {
        "verdict": "under_review",
        "message": "Your documents are under manual review. We will notify you within 24–48 hours.",
        "ai_confidence_score": str(avg_confidence),
    }


# ─────────────────────────────────────────────────────────────────────────────
# resubmit_business
# ─────────────────────────────────────────────────────────────────────────────

async def resubmit_business(
    db: AsyncSession,
    user_id: str,
) -> dict[str, Any]:
    """
    Reset a rejected business profile back to 'pending' so the user can
    upload updated documents and resubmit for AI review.

    Only allowed when verification_status == 'rejected'.
    Clears rejection_reasons and resets all document AI verdicts.
    """
    profile_result = await db.execute(
        select(BusinessProfile).where(BusinessProfile.user_id == user_id)
    )
    profile = profile_result.scalar_one_or_none()
    if profile is None:
        raise EasyPayException(
            detail="Business profile not found.",
            error_code="BUSINESS_NOT_FOUND",
        )

    if profile.verification_status != "rejected":
        raise EasyPayException(
            detail=(
                f"Resubmission is only allowed when status is 'rejected'. "
                f"Current status: '{profile.verification_status}'."
            ),
            error_code="BUSINESS_INVALID_STATE",
        )

    # Reset profile
    profile.verification_status = "pending"
    profile.rejection_reasons = []
    profile.ai_confidence_score = None
    profile.reviewed_at = None

    # Fetch user for status sync
    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one()
    user.business_status = "pending"

    # Reset all document AI verdicts so they will be re-analysed
    docs_result = await db.execute(
        select(BusinessDocument).where(BusinessDocument.business_id == profile.id)
    )
    for doc in docs_result.scalars().all():
        doc.ai_verdict = None
        doc.is_valid = None
        doc.confidence_score = None

    await db.commit()

    return {
        "business_id": str(profile.id),
        "verification_status": profile.verification_status,
    }
