"""
app/api/v1/users.py — EasyPay v3.0 User Profile Endpoints

Rules Enforced:
  Rule  2 — NEVER return password_hash / pin_hash / cnic_encrypted.
             All user responses go through UserResponse which omits those fields.
  Rule  9 — ALL routes require JWT (Depends(get_current_user)).
  Rule 10 — All errors use error_response() envelope via EasyPayException /
             HTTPException, handled globally in main.py.
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_db
from app.models.database import User
from app.schemas.auth import FCMTokenRequest
from app.schemas.base import success_response
from app.schemas.transactions import DailyLimitStatusResponse
from app.schemas.users import UserResponse, UserUpdateRequest, VerificationStatusResponse
from app.services import user_service, wallet_service

router = APIRouter(prefix="/users", tags=["Users"])


# ── Inline request model for photo upload ────────────────────────────────────
class PhotoUploadRequest(BaseModel):
    base64_image: str


# ══════════════════════════════════════════════════════════════════════════════
# GET /users/me
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/me", response_model=dict)
async def get_my_profile(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return the authenticated user's full profile.
    Wallet and business_profile are eagerly loaded.
    """
    user = await user_service.get_user_profile(db, current_user.id)
    return success_response(
        message="Profile retrieved successfully.",
        data=UserResponse.model_validate(user).model_dump(),
    )


# ══════════════════════════════════════════════════════════════════════════════
# PATCH /users/me
# ══════════════════════════════════════════════════════════════════════════════

@router.patch("/me", response_model=dict)
async def update_my_profile(
    body: UserUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Update the authenticated user's editable fields
    (email, full_name, profile_photo_url).
    Only provided (non-None) fields are changed.
    """
    user = await user_service.update_profile(db, current_user.id, body)
    return success_response(
        message="Profile updated successfully.",
        data=UserResponse.model_validate(user).model_dump(),
    )


# ══════════════════════════════════════════════════════════════════════════════
# POST /users/me/photo
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/me/photo", response_model=dict)
async def upload_my_photo(
    body: PhotoUploadRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Upload a base64-encoded profile photo to Cloudinary.
    Returns the CDN URL of the uploaded image.
    """
    url = await user_service.upload_profile_photo(
        db, current_user.id, body.base64_image
    )
    return success_response(
        message="Profile photo uploaded successfully.",
        data={"url": url},
    )


# ══════════════════════════════════════════════════════════════════════════════
# GET /users/me/verification-status
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/verification-status", response_model=dict)
async def get_my_verification_status(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return the authenticated user's current KYC tier, boolean flags,
    and the resulting daily transaction limits.
    """
    status_data = await user_service.get_verification_status(db, current_user.id)
    return success_response(
        message="Verification status retrieved successfully.",
        data=VerificationStatusResponse(**status_data).model_dump(),
    )


# ══════════════════════════════════════════════════════════════════════════════
# POST /users/fcm-token
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/fcm-token", response_model=dict)
async def update_fcm_token(
    body: FCMTokenRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Register or update the Firebase Cloud Messaging device token for push
    notifications. The token is tied to the authenticated user's account.
    Called by the Android app on login and whenever FCM rotates the token.
    """
    from datetime import datetime, timezone
    current_user.fcm_token = body.fcm_token
    current_user.fcm_token_updated = datetime.now(timezone.utc)
    await db.commit()
    return success_response("FCM token updated successfully.")


# ══════════════════════════════════════════════════════════════════════════════
# GET /users/daily-limit-status
# ══════════════════════════════════════════════════════════════════════════════

from app.core.dependencies import TIER_DAILY_LIMITS  # noqa: E402 — deferred import


@router.get("/daily-limit-status", response_model=dict)
async def get_daily_limit_status(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return how much the user has spent today vs their tier daily limit.

    Fields:
      - verification_tier: current KYC tier (0-4)
      - daily_limit: maximum PKR allowed per day for this tier
      - spent_today: PKR already spent in current daily window
      - remaining: daily_limit - spent_today
      - resets_at: ISO-8601 UTC timestamp of the next midnight reset
    """
    from datetime import datetime, timezone

    wallet = await wallet_service.get_wallet(db, current_user.id)
    await wallet_service.check_and_reset_daily_limit(db, wallet)

    tier = current_user.verification_tier
    daily_limit = TIER_DAILY_LIMITS.get(tier, 0)
    spent = wallet.daily_spent
    remaining = max(daily_limit - spent, 0)

    # Next midnight UTC
    now_utc = datetime.now(timezone.utc)
    midnight_utc = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    from datetime import timedelta
    resets_at = (midnight_utc + timedelta(days=1)).isoformat()

    status_data = DailyLimitStatusResponse(
        verification_tier=tier,
        daily_limit=daily_limit,
        spent_today=spent,
        remaining=remaining,
        resets_at=resets_at,
    )
    return success_response(
        message="Daily limit status retrieved successfully.",
        data=status_data.model_dump(),
    )


# ══════════════════════════════════════════════════════════════════════════════
# GET /users/search  (B15)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/search", response_model=dict)
async def search_users(
    q: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Search for users by phone number or name (max 10 results).
    Phone numbers are masked in results (Rule 2).
    """
    if len(q) < 3:
        return success_response(
            message="Query too short.",
            data={"results": [], "count": 0},
        )
    results = await user_service.search_users(db, q, current_user.id)
    return success_response(
        message="Search completed.",
        data={"results": results, "count": len(results)},
    )


# ══════════════════════════════════════════════════════════════════════════════
# GET /users/me/qr  (B15)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/me/qr", response_model=dict)
async def get_my_qr_code(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Generate a QR code PNG (base64) encoding the user's phone number.
    The mobile app renders this for peer-to-peer payment scanning.
    """
    qr_b64 = await user_service.get_user_qr_data(db, current_user.id)
    return success_response(
        message="QR code generated.",
        data={"qr_base64": qr_b64},
    )


# ══════════════════════════════════════════════════════════════════════════════
# POST /users/me/biometric  (B15)
# ══════════════════════════════════════════════════════════════════════════════

class BiometricToggleRequest(BaseModel):
    enable: bool
    otp_code: str


@router.post("/me/biometric", response_model=dict)
async def toggle_biometric(
    body: BiometricToggleRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Enable or disable biometric login.  Requires a valid security_change OTP.
    """
    user = await user_service.toggle_biometric(
        db, current_user.id, body.enable, body.otp_code
    )
    return success_response(
        message=f"Biometric {'enabled' if body.enable else 'disabled'} successfully.",
        data={"biometric_enabled": user.biometric_enabled},
    )


# ══════════════════════════════════════════════════════════════════════════════
# POST /users/me/deactivate  (B15)
# ══════════════════════════════════════════════════════════════════════════════

class DeactivateAccountRequest(BaseModel):
    password: str
    otp_code: str


@router.post("/me/deactivate", response_model=dict)
async def deactivate_account(
    body: DeactivateAccountRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Soft-deactivate the authenticated user's account.
    Requires correct password + valid security_change OTP.
    Data is preserved; account can be reactivated by an admin.
    """
    await user_service.deactivate_account(
        db, current_user.id, body.password, body.otp_code
    )
    return success_response(
        message="Account deactivated successfully. Contact support to reactivate.",
        data={},
    )
