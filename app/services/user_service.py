"""
app/services/user_service.py — EasyPay v3.0 User Profile Service

Critical Points observed:
  Point 1 : ALL queries use await db.execute(select(...)) — NEVER db.query()
  Rule  2 : NEVER expose password_hash, pin_hash, or cnic_encrypted
  Rule  8 : Profile photos are NOT KYC docs — Cloudinary default type ("upload")
             Only CNIC / selfie / business documents use type="private"
  Point 5 : Cloudinary is configured once from settings at module import time
"""
from __future__ import annotations

import base64
import io
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import cloudinary
import cloudinary.uploader
import qrcode
from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.core import security
from app.core.config import settings
from app.models.database import OTPCode, User, Wallet
from app.schemas.users import UserUpdateRequest

logger = logging.getLogger("easypay")

# ── Cloudinary configuration ──────────────────────────────────────────────────
# Configured once at import time; all upload calls in this module inherit it.
cloudinary.config(
    cloud_name=settings.CLOUDINARY_CLOUD_NAME,
    api_key=settings.CLOUDINARY_API_KEY,
    api_secret=settings.CLOUDINARY_API_SECRET,
    secure=True,
)


# ══════════════════════════════════════════════════════════════════════════════
# INTERNAL HELPER
# ══════════════════════════════════════════════════════════════════════════════

def _parse_uuid(user_id: str | uuid.UUID) -> uuid.UUID:
    """Accept either a UUID object or a plain string for query comparisons."""
    if isinstance(user_id, uuid.UUID):
        return user_id
    return uuid.UUID(str(user_id))


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC SERVICE FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

async def get_user_profile(
    db: AsyncSession,
    user_id: str | uuid.UUID,
) -> User:
    """
    Fetch a user by ID with wallet and business_profile eagerly loaded.

    Point 1: Uses await db.execute(select(...)) — never db.query().
    Raises HTTPException 404 if the user does not exist.
    """
    uid = _parse_uuid(user_id)
    result = await db.execute(
        select(User)
        .where(User.id == uid)
        .options(
            joinedload(User.wallet),
            joinedload(User.business_profile),
        )
    )
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    return user


async def update_profile(
    db: AsyncSession,
    user_id: str | uuid.UUID,
    data: UserUpdateRequest,
) -> User:
    """
    Update allowed user fields: email, full_name, profile_photo_url.

    Only fields that are explicitly provided (non-None) are changed.
    Raises 409 if the new email is already taken by another account.
    Returns the refreshed user ORM object.
    """
    uid = _parse_uuid(user_id)

    result = await db.execute(select(User).where(User.id == uid))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    if data.email is not None:
        # Uniqueness guard — reject if another account owns this email
        conflict = await db.execute(
            select(User).where(User.email == data.email, User.id != uid)
        )
        if conflict.scalars().first():
            raise HTTPException(
                status_code=409,
                detail="This email address is already registered to another account.",
            )
        user.email = data.email

    if data.full_name is not None:
        user.full_name = data.full_name

    if data.profile_photo_url is not None:
        user.profile_photo_url = data.profile_photo_url

    await db.commit()
    await db.refresh(user)
    return user


async def upload_profile_photo(
    db: AsyncSession,
    user_id: str | uuid.UUID,
    base64_image: str,
) -> str:
    """
    Upload a base64-encoded profile photo to Cloudinary and persist the URL.

    Rule 8 note: Profile photos are NOT KYC or business documents — they use
    Cloudinary's default "upload" delivery type (public CDN).  Only CNIC,
    selfie, and business documents use type="private".

    Steps:
      1. Construct a data-URI so Cloudinary knows the content type.
      2. Upload with cloudinary.uploader.upload().
      3. Extract secure_url from the response dict.
      4. Persist the URL on the User record and commit.
      5. Return the secure_url string.
    """
    uid = _parse_uuid(user_id)

    result = await db.execute(select(User).where(User.id == uid))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    # Build the data URI — Cloudinary SDK accepts it directly
    data_uri = f"data:image/jpeg;base64,{base64_image}"

    upload_result = cloudinary.uploader.upload(
        data_uri,
        folder="easypay/profile_photos",
        public_id=f"user_{uid}",
        overwrite=True,
        resource_type="image",
    )
    secure_url: str = upload_result["secure_url"]

    user.profile_photo_url = secure_url
    await db.commit()

    logger.info("Profile photo updated for user %s → %s", uid, secure_url)
    return secure_url


async def get_verification_status(
    db: AsyncSession,
    user_id: str | uuid.UUID,
) -> dict:
    """
    Return a dict that maps 1-to-1 onto VerificationStatusResponse.

    daily_limit and daily_spent come from the user's Wallet row.
    The boolean KYC flags are read directly from the User model.
    """
    uid = _parse_uuid(user_id)

    result = await db.execute(
        select(User)
        .where(User.id == uid)
        .options(joinedload(User.wallet))
    )
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    wallet: Optional[Wallet] = user.wallet

    return {
        "verification_tier": user.verification_tier,
        "is_verified": user.is_verified,
        "cnic_verified": user.cnic_verified,
        "biometric_verified": user.biometric_verified,
        "fingerprint_verified": user.fingerprint_verified,
        "nadra_verified": user.nadra_verified,
        "business_status": user.business_status,
        # Pull limits from the wallet; fall back to zero if wallet is absent.
        "daily_limit": wallet.daily_limit if wallet else Decimal("0.00"),
        "daily_spent": wallet.daily_spent if wallet else Decimal("0.00"),
    }


# ══════════════════════════════════════════════════════════════════════════════
# B15 — LOOKUP + SEARCH
# ══════════════════════════════════════════════════════════════════════════════

async def get_user_by_phone(
    db: AsyncSession,
    phone_number: str,
) -> User | None:
    """Return user by phone number, or None if not found."""
    result = await db.execute(
        select(User).where(User.phone_number == phone_number)
    )
    return result.scalar_one_or_none()


async def search_users(
    db: AsyncSession,
    query: str,
    exclude_user_id: uuid.UUID,
) -> list[dict]:
    """
    Search users by phone number prefix or full_name substring.
    Returns at most 10 results, excluding the requesting user.
    Phone numbers are masked (Rule 2 — do not expose full phone to peers).
    """
    result = await db.execute(
        select(User)
        .where(
            User.id != exclude_user_id,
            User.is_active == True,
            or_(
                User.phone_number.ilike(f"%{query}%"),
                User.full_name.ilike(f"%{query}%"),
            ),
        )
        .limit(10)
    )
    users = result.scalars().all()

    def _mask_phone(phone: str) -> str:
        """Mask middle digits: +92300****567"""
        if len(phone) < 7:
            return phone
        return phone[:5] + "****" + phone[-3:]

    return [
        {
            "id": str(u.id),
            "full_name": u.full_name,
            "phone_number": _mask_phone(u.phone_number),
            "profile_photo_url": u.profile_photo_url,
        }
        for u in users
    ]


# ══════════════════════════════════════════════════════════════════════════════
# B15 — QR CODE
# ══════════════════════════════════════════════════════════════════════════════

async def get_user_qr_data(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> str:
    """
    Generate a QR code PNG (base64-encoded) encoding the user's phone number.

    The QR payload is the user's phone number — safe to encode (not PII-critical
    for sender identification in a payment context).

    Returns:
        Base64-encoded PNG string (no data-URI prefix).
    """
    uid = _parse_uuid(user_id)
    result = await db.execute(select(User).where(User.id == uid))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(user.phone_number)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("utf-8")


# ══════════════════════════════════════════════════════════════════════════════
# B15 — BIOMETRIC TOGGLE
# ══════════════════════════════════════════════════════════════════════════════

async def _verify_otp_for_action(
    db: AsyncSession,
    user_id: uuid.UUID,
    otp_code: str,
    purpose: str,
) -> None:
    """
    Shared OTP verification helper for security-sensitive user actions.

    Finds the latest unused, non-expired OTP for the given user + purpose,
    verifies it via Bcrypt, marks it used, and commits.

    Raises:
        HTTPException 400 — OTP not found, expired, or invalid.
    """
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(OTPCode)
        .where(
            OTPCode.user_id == user_id,
            OTPCode.purpose == purpose,
            OTPCode.is_used == False,
            OTPCode.expires_at > now,
        )
        .order_by(OTPCode.created_at.desc())
        .limit(1)
    )
    otp_record = result.scalar_one_or_none()

    if not otp_record:
        raise HTTPException(
            status_code=400,
            detail="OTP not found or has expired. Please request a new one.",
        )

    if not security.verify_otp(otp_code, otp_record.code_hash):
        raise HTTPException(status_code=400, detail="Invalid OTP code.")

    otp_record.is_used = True
    await db.commit()


async def toggle_biometric(
    db: AsyncSession,
    user_id: uuid.UUID,
    enable: bool,
    otp_code: str,
) -> User:
    """
    Enable or disable biometric login for the authenticated user.

    Requires OTP (purpose="security_change") to confirm intent.
    Returns the updated user object.
    """
    await _verify_otp_for_action(db, user_id, otp_code, "security_change")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    user.biometric_enabled = enable
    await db.commit()
    await db.refresh(user)

    logger.info(
        "Biometric %s for user %s",
        "enabled" if enable else "disabled",
        user_id,
    )
    return user


# ══════════════════════════════════════════════════════════════════════════════
# B15 — ACCOUNT DEACTIVATION
# ══════════════════════════════════════════════════════════════════════════════

async def deactivate_account(
    db: AsyncSession,
    user_id: uuid.UUID,
    password: str,
    otp_code: str,
) -> None:
    """
    Soft-deactivate a user account.

    Requires:
      1. Correct password (Bcrypt verify).
      2. Valid OTP (purpose="security_change").

    Sets is_active=False, is_locked=True.  Data is preserved (soft delete).

    Raises:
        HTTPException 400 — wrong password.
        HTTPException 400 — invalid/expired OTP.
    """
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    if not security.verify_password(password, user.password_hash):
        raise HTTPException(status_code=400, detail="Incorrect password.")

    await _verify_otp_for_action(db, user_id, otp_code, "security_change")

    user.is_active = False
    user.is_locked = True
    await db.commit()

    logger.info("Account deactivated for user %s", user_id)
