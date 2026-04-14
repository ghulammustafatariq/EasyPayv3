"""
app/api/v1/auth.py — EasyPay v3.0 Authentication Endpoints

Rules Enforced:
  Rule 2  — NEVER return password_hash / pin_hash / cnic_encrypted.
             All user returns go through UserResponse, which omits those fields.
  Rule 1  — All password/PIN operations delegate to security.* (Bcrypt cost=12).
  Rule 9  — /auth/* routes are the ONLY public routes (no JWT required).
             /logout and /pin DO require JWT (Depends(get_current_user)).
  Rule 10 — All errors use error_response() envelope via EasyPayException or
             HTTPException with structured detail (handled globally in main.py).
"""
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import Request as FastAPIRequest

from app.core.dependencies import get_current_user, get_db
from app.core.limiter import limiter
from app.schemas.auth import (
    FCMTokenRequest,
    LoginRequest,
    OTPSendRequest,
    OTPVerifyRequest,
    PasswordResetRequest,
    PINLoginRequest,
    PINSetRequest,
    PINVerifyRequest,
    RefreshTokenRequest,
    UserRegisterRequest,
)
from app.schemas.base import success_response
from app.schemas.users import UserResponse
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["Authentication"])


# ─────────────────────────────────────────────────────────────────────────────
# POST /auth/register
# ─────────────────────────────────────────────────────────────────────────────
@router.post(
    "/register",
    response_model=None,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user account",
)
async def register(
    data: UserRegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Register a new user.

    - Creates User + Wallet with PKR 0.00 balance.
    - Sends a 6-digit OTP to the phone (Twilio or demo bypass).
    - Returns the new user profile wrapped in success_response().
    - Rule 2: password_hash / pin_hash / cnic_encrypted are NEVER in the response.
    """
    result = await auth_service.register_user(db, data)
    return success_response(
        "Registration successful. Please verify your phone number with the OTP sent.",
        result,
    )


# ─────────────────────────────────────────────────────────────────────────────
# POST /auth/otp/verify
# ─────────────────────────────────────────────────────────────────────────────
@router.post(
    "/otp/verify",
    status_code=status.HTTP_200_OK,
    summary="Verify phone OTP and activate account",
)
async def verify_otp(
    data: OTPVerifyRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Verify the 6-digit OTP sent to the user's phone during registration.
    On success, sets is_verified=True and verification_tier=1.
    Returns an access token so the client can immediately proceed to PIN setup.
    """
    result = await auth_service.verify_otp_and_activate(db, data.phone, data.otp_code)
    return success_response(
        "Phone verified. Account activated.",
        {
            "access_token": result["access_token"],
            "refresh_token": result["refresh_token"],
            "token_type": result["token_type"],
            "expires_in": result["expires_in"],
            "user": UserResponse.model_validate(result["user"]).model_dump(),
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# POST /auth/otp/send  — Rate limited: 3/hour
# ─────────────────────────────────────────────────────────────────────────────
@router.post(
    "/otp/send",
    status_code=status.HTTP_200_OK,
    summary="Resend registration OTP (rate limited: 3/hour)",
)
@limiter.limit("3/hour")
async def resend_otp(
    request: FastAPIRequest,
    data: OTPSendRequest,
    db: AsyncSession = Depends(get_db),
):
    """Resend the registration OTP to the user's phone. Max 3 per hour per IP."""
    await auth_service.resend_otp(db, data.phone)
    return success_response("A new OTP has been sent to your phone number.")


# ─────────────────────────────────────────────────────────────────────────────
# POST /auth/login
# ─────────────────────────────────────────────────────────────────────────────
@router.post(
    "/login",
    status_code=status.HTTP_200_OK,
    summary="Log in and receive JWT tokens",
)
async def login(
    data: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Authenticate with phone + password.

    - Admin users receive a 2-hour access token (Rule 17).
    - Regular users receive a 24-hour access token.
    - Returns access_token, refresh_token, token_type, and the user profile.
    - Rule 2: password_hash / pin_hash / cnic_encrypted absent from user payload.
    """
    ip = request.client.host if request.client else None
    result = await auth_service.login_user(db, data.phone, data.password, ip_address=ip)
    return success_response(
        "Login successful.",
        {
            "access_token": result["access_token"],
            "refresh_token": result["refresh_token"],
            "token_type": "bearer",
            "expires_in": result["expires_in"],
            "user": UserResponse.model_validate(result["user"]).model_dump(),
        },
    )


@router.post(
    "/login/pin",
    status_code=status.HTTP_200_OK,
    summary="Log in using phone + transaction PIN (MPIN)",
)
async def login_pin(
    data: PINLoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Authenticate with phone + 4-digit PIN (MPIN).
    Only works if the user has already set a PIN.
    """
    result = await auth_service.login_with_pin(db, data.phone, data.pin)
    return success_response(
        "PIN login successful.",
        {
            "access_token": result["access_token"],
            "refresh_token": result["refresh_token"],
            "token_type": result["token_type"],
            "expires_in": result["expires_in"],
            "user": UserResponse.model_validate(result["user"]).model_dump(),
        },
    )



# ─────────────────────────────────────────────────────────────────────────────
# POST /auth/refresh
# ─────────────────────────────────────────────────────────────────────────────
@router.post(
    "/token/refresh",
    status_code=status.HTTP_200_OK,
    summary="Rotate refresh token and get new access token",
)
async def refresh_token(
    data: RefreshTokenRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Exchange a valid refresh token for a new access+refresh token pair.
    The old refresh token is revoked (rotation pattern prevents replay).
    """
    result = await auth_service.refresh_access_token(db, data.refresh_token)
    return success_response("Token refreshed successfully.", result)


# ─────────────────────────────────────────────────────────────────────────────
# POST /auth/logout  — JWT required
# ─────────────────────────────────────────────────────────────────────────────
@router.post(
    "/logout",
    status_code=status.HTTP_200_OK,
    summary="Log out (revoke refresh token)",
)
async def logout(
    data: RefreshTokenRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Revoke the supplied refresh token so it cannot be used again.
    The access token expires naturally (no server-side invalidation needed
    because of its short 24h / 2h lifespan).
    """
    await auth_service.logout_user(db, current_user.id, data.refresh_token)
    return success_response("Logged out successfully.")


# ─────────────────────────────────────────────────────────────────────────────
# POST /auth/password-reset/initiate
# ─────────────────────────────────────────────────────────────────────────────
@router.post(
    "/password/reset/initiate",
    status_code=status.HTTP_200_OK,
    summary="Request a password-reset OTP",
)
async def password_reset_initiate(
    data: OTPSendRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Send a password-reset OTP to the user's phone.
    Always returns 200 even if the phone is not found (prevents user enumeration).
    """
    await auth_service.initiate_password_reset(db, data.phone)
    return success_response(
        "If this phone number is registered, you will receive an OTP shortly."
    )


# ─────────────────────────────────────────────────────────────────────────────
# POST /auth/password-reset/complete
# ─────────────────────────────────────────────────────────────────────────────
@router.post(
    "/password/reset/complete",
    status_code=status.HTTP_200_OK,
    summary="Complete password reset using OTP",
)
async def password_reset_complete(
    data: PasswordResetRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Verify the password-reset OTP and set a new password.
    All active refresh tokens for the user are revoked to force logouts
    on all other devices.
    """
    await auth_service.complete_password_reset(
        db, data.phone, data.otp_code, data.new_password
    )
    return success_response(
        "Password reset successful. Please log in with your new password."
    )


# ─────────────────────────────────────────────────────────────────────────────
# POST /auth/pin  — JWT required
# ─────────────────────────────────────────────────────────────────────────────
@router.post(
    "/pin/set",
    status_code=status.HTTP_200_OK,
    summary="Set or update transaction PIN",
)
async def set_pin(
    data: PINSetRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Set or update the 4-digit transaction PIN for the authenticated user.
    Rule 1: PIN is Bcrypt-hashed (cost=12) before storage. Never plaintext.
    """
    await auth_service.set_pin(db, current_user.id, data.pin)
    return success_response("Transaction PIN set successfully.")


# ─────────────────────────────────────────────────────────────────────────────
# POST /auth/pin/verify  — JWT required
# ─────────────────────────────────────────────────────────────────────────────
@router.post(
    "/pin/verify",
    status_code=status.HTTP_200_OK,
    summary="Verify transaction PIN (pre-auth check)",
)
async def verify_pin(
    data: PINVerifyRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Verify the 4-digit transaction PIN for the authenticated user.

    - Increments login_attempts on each failure.
    - Resets counter on success.
    - Locks account after 3 consecutive wrong PINs (PINLockedError → 403).
    - Rule 1: Comparison is constant-time Bcrypt (security.verify_pin).
    """
    from app.core.dependencies import verify_transaction_pin
    await verify_transaction_pin(pin=data.pin, current_user=current_user, db=db)
    return success_response("PIN verified successfully.")


# ─────────────────────────────────────────────────────────────────────────────
# GET /auth/dev/otp/{phone}  — DEVELOPMENT ONLY
# Returns the pending OTP for a phone so SMTP is not required during dev.
# ─────────────────────────────────────────────────────────────────────────────
@router.get(
    "/dev/otp/{phone}",
    status_code=status.HTTP_200_OK,
    summary="[DEV ONLY] Get pending OTP for a phone number or email",
    include_in_schema=True,
)
async def dev_get_otp(phone: str):
    from app.core.config import settings
    from app.services.auth_service import DEV_OTP_STORE, _normalize_phone
    if settings.ENVIRONMENT != "development":
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Not found")
    # Try by phone (canonical), then by email (the {phone} param may be an email)
    canonical = _normalize_phone(phone)
    otp = DEV_OTP_STORE.get(canonical) or DEV_OTP_STORE.get(phone)
    if not otp:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail={"message": "No pending OTP. Register or reset password first."})
    return success_response("OTP retrieved (dev mode only).", {"key": phone, "otp": otp})
