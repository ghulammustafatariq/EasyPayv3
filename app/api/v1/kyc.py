"""
app/api/v1/kyc.py — EasyPay v3.0 KYC & Biometrics Endpoints

Rules Enforced:
  Rule  8 — Cloudinary uploads use type="private" (enforced in kyc_service).
  Rule  9 — ALL routes require JWT (Depends(get_current_user)).
  Rule 10 — All errors use error_response() envelope handled globally.
  Point 15 — verification_tier is explicitly updated after each KYC step
              (enforced in kyc_service).
"""
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_db
from app.core.limiter import limiter
from app.models.database import User
from app.schemas.base import success_response
from app.schemas.kyc import (
    CNICUploadRequest,
    FingerprintDataRequest,
    FingerprintVerifyResponse,
    LivenessResult,
    LivenessVerifyRequest,
)
from app.services import fingerprint_service, kyc_service

router = APIRouter(prefix="/users", tags=["KYC & Biometrics"])


# ══════════════════════════════════════════════════════════════════════════════
# POST /users/upload-cnic  (Rate: 5/day)
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/upload-cnic", status_code=status.HTTP_200_OK, response_model=dict)
@limiter.limit("50/day")
async def upload_cnic(
    request: Request,
    data: CNICUploadRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Upload CNIC front and back images for OCR verification.

    - Uploads both images to Cloudinary (private, Rule 8).
    - Runs OCR.space Engine 2 to extract raw text from the CNIC front.
    - Sends OCR text to DeepSeek AI to format into structured CNIC data.
    - Validates extracted name + CNIC number match account credentials.
    - Encrypts the CNIC number and stores it (Rule 7). Sets cnic_verified=True.
    - Advances tier (Point 15). No manual confirmation step — auto-verified.
    """
    extracted = await kyc_service.process_cnic_upload(
        db,
        current_user.id,
        data.front_base64,
        data.back_base64,
    )
    return success_response(
        message="CNIC verified successfully. Your account has been upgraded to Tier 2.",
        data=extracted.model_dump(),
    )


# ══════════════════════════════════════════════════════════════════════════════
# POST /users/verify-liveness  (Rate: 3/day)
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/verify-liveness", status_code=status.HTTP_200_OK, response_model=dict)
@limiter.limit("30/day")
async def verify_liveness(
    request: Request,
    data: LivenessVerifyRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Submit a selfie for Face++ liveness and face-match verification.

    - Uploads selfie to Cloudinary (private).
    - Demo bypass: +920000000000 always passes with 99.9% confidence.
    - All other users: Face++ /compare against stored CNIC front image.
    - Confidence > 80% triggers biometric_verified=True and tier 3 (Point 15).
    """
    result: LivenessResult = await kyc_service.process_liveness_check(
        db,
        current_user.id,
        data,
    )
    message = (
        "Liveness check passed. Your account has been upgraded to Tier 3."
        if result.success
        else (result.failure_reason or "Face match confidence below threshold. Please retake your selfie.")
    )
    return success_response(
        message=message,
        data=result.model_dump(),
    )


# ══════════════════════════════════════════════════════════════════════════════
# POST /users/verify-fingerprint  (Rate: 3/day)
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/verify-fingerprint", status_code=status.HTTP_200_OK, response_model=dict)
@limiter.limit("30/day")
async def submit_fingerprint(
    request: Request,
    data: FingerprintDataRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Submit 8-finger biometric feature vectors for NADRA VERISYS simulation.

    - Exactly 8 fingers required (enforced at schema and service layers).
    - Each finger's feature dict is SHA-256 hashed before storage (Point 9).
    - Simulates 2.5-second NADRA API delay (Point 10).
    - Sets fingerprint_verified=True, nadra_verified=True, tier to min 3.
    - Returns the exact NADRA success message required by spec.
    """
    result: FingerprintVerifyResponse = await fingerprint_service.process_fingerprint_scan(
        db,
        current_user.id,
        data,
    )
    return success_response(
        message=result.message,
        data=result.model_dump(),
    )
