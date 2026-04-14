"""
app/services/auth_service.py — EasyPay v3.0 Authentication Service

Rules Enforced:
  Point 1  — ALL DB queries use await db.execute(select(...)). NEVER .query().
  Rule 1   — Passwords and PINs ALWAYS Bcrypt cost=12. NEVER plaintext.
  Rule 2   — NEVER include password_hash / pin_hash / cnic_encrypted in return values.
  Rule 17  — Admin JWT expiry is ADMIN_JWT_EXPIRY_HOURS (2h), not 24h.
  Point 13 — Twilio call wrapped in try/except; NEVER crash the API.
  Point 17 — seed_admin_user() checks ADMIN_PHONE first (prevents duplicates).

Demo Bypass Logic (Presentation Edition):
  Phone +923019101418  → real Twilio SMS sent
  Any other phone      → OTP printed to console only (no Twilio call)
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from fastapi import HTTPException
from jose import JWTError
from sqlalchemy import and_, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import security
from app.core.config import settings
from app.core.exceptions import (
    OTPExpiredError,
    OTPInvalidError,
    PINLockedError,
)
from app.models.database import (
    LoginAudit,
    OTPCode,
    RefreshToken,
    User,
    Wallet,
)
from app.schemas.auth import UserRegisterRequest

logger = logging.getLogger(__name__)

# ── Presentation phone — receives real Twilio SMS ─────────────────────────────
_DEMO_REAL_PHONE = "+923019101418"


# ═════════════════════════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _hash_refresh_token(token: str) -> str:
    """SHA-256 hex digest of a refresh token string (for DB storage)."""
    return hashlib.sha256(token.encode()).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _otp_expiry() -> datetime:
    return _utc_now() + timedelta(minutes=settings.OTP_EXPIRY_MINUTES)


def _refresh_expiry() -> datetime:
    return _utc_now() + timedelta(days=settings.REFRESH_TOKEN_EXPIRY_DAYS)


def _normalize_phone(phone: str) -> str:
    """Normalize Pakistani phone to +923XXXXXXXXX canonical form.
    Accepts: 03XXXXXXXXX  → +923XXXXXXXXX
             923XXXXXXXXX → +923XXXXXXXXX
             +923XXXXXXXXX → unchanged
    """
    p = phone.strip()
    if p.startswith("03") and len(p) == 11:
        return "+92" + p[1:]
    if p.startswith("923") and len(p) == 12:
        return "+" + p
    return p


async def _get_user_by_phone(db: AsyncSession, phone: str) -> User | None:
    """Point 1: ALWAYS await db.execute(select(...)). NEVER .query()."""
    result = await db.execute(select(User).where(User.phone_number == _normalize_phone(phone)))
    return result.scalar_one_or_none()


async def _get_user_by_id(db: AsyncSession, user_id) -> User | None:
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def _save_otp(
    db: AsyncSession,
    user_id,
    plain_otp: str,
    purpose: str,
) -> None:
    """Hash OTP with Bcrypt (Rule 1) and persist to otp_codes table."""
    otp_record = OTPCode(
        user_id=user_id,
        code_hash=security.hash_otp(plain_otp),
        purpose=purpose,
        expires_at=_otp_expiry(),
        is_used=False,
    )
    db.add(otp_record)
    await db.flush()


# ═════════════════════════════════════════════════════════════════════════════
# EMAIL OTP
# ═════════════════════════════════════════════════════════════════════════════

async def send_otp_email(email: str, otp: str) -> None:
    """
    Send an OTP via email (Gmail SMTP).

    If SMTP_USER is not configured or SMTP fails, the OTP is printed to the
    console.  In development mode the OTP is also stored in DEV_OTP_STORE
    (keyed by email) so the /auth/dev/otp endpoint can return it.
    """
    # Always store in dev store so the endpoint works regardless of SMTP
    if settings.ENVIRONMENT == "development":
        DEV_OTP_STORE[email] = otp

    if not settings.SMTP_USER:
        print(f"🌟 DEMO BYPASS (no SMTP configured): OTP for {email} is {otp} 🌟")
        return

    import asyncio
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    def _send() -> None:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"EasyPay — Your verification code is {otp}"
        msg["From"] = f"{settings.SMTP_FROM_NAME} <{settings.SMTP_USER}>"
        msg["To"] = email

        html = (
            f"<div style='font-family:sans-serif;max-width:480px;margin:auto'>"
            f"<h2 style='color:#1565C0'>EasyPay Verification</h2>"
            f"<p>Your one-time code is:</p>"
            f"<h1 style='letter-spacing:6px;color:#333'>{otp}</h1>"
            f"<p>This code expires in 5 minutes. Never share it with anyone.</p>"
            f"</div>"
        )
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as server:
            server.starttls()
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.sendmail(settings.SMTP_USER, email, msg.as_string())

    try:
        await asyncio.to_thread(_send)
        logger.info("OTP email sent successfully to %s", email)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Email send failed to %s — OTP delivery skipped. Error: %s",
            email,
            exc,
        )
        print(f"⚠️  Email failed — OTP for {email}: {otp}")


# ═════════════════════════════════════════════════════════════════════════════
# IN-MEMORY STAGING DICTIONARY
# Satisfies the requirement: NO DATA IN DB UNTIL OTP CONFIRMED!
# ═════════════════════════════════════════════════════════════════════════════
PENDING_REGISTRATIONS_CACHE: dict[str, dict] = {}

# Dev-only store: phone → plain OTP (only populated when ENVIRONMENT=development)
DEV_OTP_STORE: dict[str, str] = {}


# ═════════════════════════════════════════════════════════════════════════════
# REGISTRATION
# ═════════════════════════════════════════════════════════════════════════════

async def register_user(db: AsyncSession, data: UserRegisterRequest) -> dict:
    """
    Register a new user (Pending Phase).

    Rule: No row is created in 'users' until OTP is confirmed. Data is held in memory.
    """
    canonical_phone = _normalize_phone(data.phone)

    # ── Uniqueness checks in actual DB ────────────────────────────────
    try:
        dup_query = await db.execute(
            select(User.id).where(User.phone_number == canonical_phone)
        )
        if dup_query.scalar_one_or_none():
            raise EasyPayException(
                status_code=409,
                code="PHONE_NUMBER_ALREADY_REGISTERED",
                message="This phone number is already registered.",
            )
    except EasyPayException:
        raise
    except Exception as db_exc:
        logger.exception("register_user: DB duplicate check failed — %s", db_exc)
        raise EasyPayException(
            status_code=503,
            code="DATABASE_UNAVAILABLE",
            message="Service temporarily unavailable. Please try again.",
        )

    # ── Encrypt CNIC (Rule 7) ────────────────────────────────────────────────
    from app.core.encryption import encrypt_sensitive
    cnic_encrypted = encrypt_sensitive(data.cnic)

    # ── Stage in Memory ──────────────────────────────────────────────────────
    plain_otp = security.generate_totp(canonical_phone)

    # Dev mode: store plain OTP so /dev/otp endpoint can return it
    if settings.ENVIRONMENT == "development":
        DEV_OTP_STORE[canonical_phone] = plain_otp

    import time
    PENDING_REGISTRATIONS_CACHE[canonical_phone] = {
        "email": str(data.email),
        "full_name": data.full_name,
        "password_hash": security.hash_password(data.password),
        "cnic_encrypted": cnic_encrypted,
        "otp_hash": security.hash_password(plain_otp),
        "expires_at": time.time() + 300  # 5 minutes
    }

    # ── Send OTP ──
    await send_otp_email(str(data.email), plain_otp)

    return {"message": "Verification code sent. No account created yet."}



# ═════════════════════════════════════════════════════════════════════════════
# OTP VERIFICATION
# ═════════════════════════════════════════════════════════════════════════════

async def verify_otp_and_activate(
    db: AsyncSession,
    phone: str,
    otp_code: str,
) -> dict:
    """
    Verify OTP from Memory Cache and FINALLY send data to database.
    """
    import time
    phone = _normalize_phone(phone)

    # 1. Fetch from memory
    pending = PENDING_REGISTRATIONS_CACHE.get(phone)

    if not pending:
        # Check if they really exist in main DB
        user = await _get_user_by_phone(db, phone)
        if user:
            # Maybe it's a standard token verification during login but UI got mashed?
            raise HTTPException(status_code=400, detail={"success": False, "message": "Already verified. Please log in."})
        raise HTTPException(status_code=404, detail={"success": False, "message": "No registration session found in memory."})

    # 2. Check Expiry
    if pending["expires_at"] < time.time():
        del PENDING_REGISTRATIONS_CACHE[phone]
        raise OTPExpiredError()

    # 3. Check Hash
    if not security.verify_password(otp_code, pending["otp_hash"]):
        raise OTPInvalidError()

    # 4. Check for duplicate email/phone before inserting
    existing = await _get_user_by_phone(db, phone)
    if existing:
        del PENDING_REGISTRATIONS_CACHE[phone]
        raise HTTPException(status_code=409, detail={"success": False, "message": "An account with this phone number already exists. Please log in."})
    existing_email_result = await db.execute(
        select(User).where(User.email == pending["email"])
    )
    if existing_email_result.scalars().first():
        del PENDING_REGISTRATIONS_CACHE[phone]
        raise HTTPException(status_code=409, detail={"success": False, "message": "An account with this email already exists."})

    # 5. FINALLY SEND TO DATABASE!
    user = User(
        phone_number=phone,  # already normalized above
        email=pending["email"],
        full_name=pending["full_name"],
        password_hash=pending["password_hash"],
        cnic_encrypted=pending["cnic_encrypted"],
        is_verified=True,
        verification_tier=1,
    )
    db.add(user)
    await db.flush() # get user.id

    # ── Create Wallet ──
    wallet = Wallet(
        user_id=user.id,
        balance=Decimal("0.00"),
        currency="PKR",
        daily_limit=Decimal("25000.00"),
    )
    db.add(wallet)
    
    # ── Cleanup Memory ──
    del PENDING_REGISTRATIONS_CACHE[phone]
    await db.commit()
    
    # ── Tokens ──
    is_admin = bool(user.is_superuser)
    access_token = security.create_access_token(str(user.id), is_admin=is_admin)

    refresh_token = security.create_refresh_token(str(user.id))
    
    rt = RefreshToken(
        user_id=user.id,
        token_hash=_hash_refresh_token(refresh_token),
        expires_at=_refresh_expiry()
    )
    db.add(rt)
    user.last_login_at = _utc_now()
    await db.commit()

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": 24 * 3600,
        "user": user
    }


# ═════════════════════════════════════════════════════════════════════════════
# LOGIN
# ═════════════════════════════════════════════════════════════════════════════

async def login_user(
    db: AsyncSession,
    phone: str,
    password: str,
    ip_address: str | None = None,
) -> dict:
    """
    Authenticate a user and issue JWT tokens.

    Admin users: access token expiry = ADMIN_JWT_EXPIRY_HOURS (2h). Rule 17.
    Regular users: access token expiry = JWT_EXPIRY_HOURS (24h).

    Returns dict with keys: access_token, refresh_token, token_type, expires_in, user.
    """
    phone = _normalize_phone(phone)
    user = await _get_user_by_phone(db, phone)

    async def _audit(success: bool, reason: str | None = None) -> None:
        entry = LoginAudit(
            user_id=user.id if user else None,
            phone_number=phone,
            ip_address=ip_address,
            success=success,
            failure_reason=reason,
        )
        db.add(entry)
        await db.commit()

    if user is None:
        await _audit(False, "USER_NOT_FOUND")
        raise HTTPException(
            status_code=401,
            detail={
                "success": False,
                "error": {
                    "code": "AUTH_INVALID_CREDENTIALS",
                    "message": "Phone number or password is incorrect.",
                    "details": {},
                    "request_id": "req_login",
                    "timestamp": _utc_now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
            },
        )

    if user.is_locked:
        await _audit(False, "ACCOUNT_LOCKED")
        raise PINLockedError(
            detail="Your account is locked due to multiple failed attempts. Contact support.",
            error_code="AUTH_ACCOUNT_LOCKED",
        )

    if not security.verify_password(password, user.password_hash):
        user.login_attempts = (user.login_attempts or 0) + 1
        if user.login_attempts >= 5:
            user.is_locked = True
            await _audit(False, "MAX_ATTEMPTS_LOCKED")
            await db.commit()
            raise PINLockedError(
                detail="Account locked after 5 failed login attempts. Contact support.",
                error_code="AUTH_ACCOUNT_LOCKED",
            )
        await _audit(False, "WRONG_PASSWORD")
        await db.commit()
        raise HTTPException(
            status_code=401,
            detail={
                "success": False,
                "error": {
                    "code": "AUTH_INVALID_CREDENTIALS",
                    "message": "Phone number or password is incorrect.",
                    "details": {},
                    "request_id": "req_login",
                    "timestamp": _utc_now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
            },
        )

    # ── Reset failed-attempt counter on success ───────────────────────────────
    if user.login_attempts != 0:
        user.login_attempts = 0

    # ── Issue tokens ──────────────────────────────────────────────────────────
    # Rule 17: admin gets 2h JWT, regular gets 24h JWT
    is_admin = bool(user.is_superuser)
    access_token = security.create_access_token(str(user.id), is_admin=is_admin)
    raw_refresh = security.create_refresh_token(str(user.id))

    # Persist hashed refresh token
    rt = RefreshToken(
        user_id=user.id,
        token_hash=_hash_refresh_token(raw_refresh),
        expires_at=_refresh_expiry(),
        is_revoked=False,
    )
    db.add(rt)

    # Update last_login_at
    user.last_login_at = _utc_now()
    await _audit(True)  # also commits

    expires_in = (
        settings.ADMIN_JWT_EXPIRY_HOURS * 3600
        if is_admin
        else settings.JWT_EXPIRY_HOURS * 3600
    )

    logger.info("Login success: phone=%s admin=%s", phone, is_admin)
    return {
        "access_token": access_token,
        "refresh_token": raw_refresh,
        "token_type": "bearer",
        "expires_in": expires_in,
        "user": user,
    }


async def login_with_pin(db: AsyncSession, phone: str, pin: str) -> dict:
    """
    Login using ONLY phone and MPIN (4-digit PIN).
    Used for 'Quick Login' once the user is remembered on device.
    """
    user = await _get_user_by_phone(db, phone)
    if not user:
        raise HTTPException(
            status_code=401,
            detail={"success": False, "message": "Invalid phone number or PIN."}
        )

    if not user.is_active:
        raise HTTPException(
            status_code=403,
            detail={"success": False, "message": "Account is inactive. Please contact support."}
        )

    if user.is_locked:
        raise HTTPException(
            status_code=403,
            detail={"success": False, "message": "Account is locked due to multiple failed attempts. Contact support."}
        )

    if not user.pin_hash:
        raise HTTPException(
            status_code=400,
            detail={"success": False, "message": "PIN not set. Use password login first."}
        )

    if not security.verify_pin(pin, user.pin_hash):
        raise HTTPException(
            status_code=401,
            detail={"success": False, "message": "Incorrect PIN."}
        )

    # ── Reset failed-attempt counter on successful PIN login ─────────────────
    if user.login_attempts != 0:
        user.login_attempts = 0

    # ── Issue tokens ──────────────────────────────────────────────────────────
    is_admin = bool(user.is_superuser)
    access_token = security.create_access_token(str(user.id), is_admin=is_admin)
    raw_refresh = security.create_refresh_token(str(user.id))

    rt = RefreshToken(
        user_id=user.id,
        token_hash=_hash_refresh_token(raw_refresh),
        expires_at=_refresh_expiry(),
        is_revoked=False,
    )
    db.add(rt)
    user.last_login_at = _utc_now()
    await db.commit()

    return {
        "access_token": access_token,
        "refresh_token": raw_refresh,
        "token_type": "bearer",
        "expires_in": 24 * 3600,
        "user": user,
    }


# ═════════════════════════════════════════════════════════════════════════════
# LOGOUT
# ═════════════════════════════════════════════════════════════════════════════

async def logout_user(
    db: AsyncSession,
    user_id,
    refresh_token: str,
) -> None:
    """Revoke the supplied refresh token (soft-delete)."""
    token_hash = _hash_refresh_token(refresh_token)
    result = await db.execute(
        select(RefreshToken).where(
            and_(
                RefreshToken.user_id == user_id,
                RefreshToken.token_hash == token_hash,
                RefreshToken.is_revoked == False,  # noqa: E712
            )
        )
    )
    rt = result.scalar_one_or_none()
    if rt is not None:
        rt.is_revoked = True
        await db.commit()


# ═════════════════════════════════════════════════════════════════════════════
# TOKEN REFRESH
# ═════════════════════════════════════════════════════════════════════════════

async def refresh_access_token(
    db: AsyncSession,
    refresh_token: str,
) -> dict:
    """
    Rotate refresh tokens (revoke old, issue new pair).

    Raises 401 if token is not found, revoked, expired, or has wrong type.
    """
    token_hash = _hash_refresh_token(refresh_token)
    now = _utc_now()

    result = await db.execute(
        select(RefreshToken).where(
            and_(
                RefreshToken.token_hash == token_hash,
                RefreshToken.is_revoked == False,  # noqa: E712
                RefreshToken.expires_at > now,
            )
        )
    )
    rt = result.scalar_one_or_none()

    if rt is None:
        raise HTTPException(
            status_code=401,
            detail={
                "success": False,
                "error": {
                    "code": "AUTH_TOKEN_INVALID",
                    "message": "Refresh token is invalid, revoked, or expired.",
                    "details": {},
                    "request_id": "req_refresh",
                    "timestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
            },
        )

    # Validate JWT payload as well
    try:
        payload = security.decode_token(refresh_token)
        if payload.get("type") != "refresh":
            raise ValueError("wrong type")
    except (JWTError, ValueError):
        raise HTTPException(
            status_code=401,
            detail={
                "success": False,
                "error": {
                    "code": "AUTH_TOKEN_INVALID",
                    "message": "Refresh token is malformed.",
                    "details": {},
                    "request_id": "req_refresh",
                    "timestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
            },
        )

    user = await _get_user_by_id(db, rt.user_id)
    if user is None or not user.is_active or user.is_locked:
        raise HTTPException(status_code=401, detail="Account unavailable.")

    # ── Rotation: revoke old, issue new ───────────────────────────────────────
    rt.is_revoked = True
    await db.flush()

    is_admin = bool(user.is_superuser)
    new_access = security.create_access_token(str(user.id), is_admin=is_admin)
    new_refresh = security.create_refresh_token(str(user.id))

    new_rt = RefreshToken(
        user_id=user.id,
        token_hash=_hash_refresh_token(new_refresh),
        expires_at=_refresh_expiry(),
        is_revoked=False,
    )
    db.add(new_rt)
    await db.commit()

    expires_in = (
        settings.ADMIN_JWT_EXPIRY_HOURS * 3600
        if is_admin
        else settings.JWT_EXPIRY_HOURS * 3600
    )
    return {
        "access_token": new_access,
        "refresh_token": new_refresh,
        "token_type": "bearer",
        "expires_in": expires_in,
    }


# ═════════════════════════════════════════════════════════════════════════════
# RESEND OTP
# ═════════════════════════════════════════════════════════════════════════════

async def resend_otp(db: AsyncSession, phone: str) -> None:
    """
    Issue a new registration OTP (previous ones are superseded naturally
    because we always look at the latest unexpired, unused record).
    """
    user = await _get_user_by_phone(db, phone)
    if user is None:
        raise HTTPException(
            status_code=404,
            detail={
                "success": False,
                "error": {
                    "code": "USER_NOT_FOUND",
                    "message": "No account found with this phone number.",
                    "details": {},
                    "request_id": "req_resend",
                    "timestamp": _utc_now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
            },
        )

    plain_otp = security.generate_totp(phone)
    await _save_otp(db, user.id, plain_otp, purpose="registration")
    await db.commit()
    await send_otp_email(user.email, plain_otp)


# ═════════════════════════════════════════════════════════════════════════════
# BANK LINKING OTP
# ═════════════════════════════════════════════════════════════════════════════

async def request_bank_linking_otp(db: AsyncSession, user: "User") -> str:
    """
    Generate a bank_linking OTP, persist it, and email it to the user.
    Returns an obfuscated email hint (e.g. "gh***@gmail.com").
    """
    plain_otp = security.generate_totp(str(user.phone_number))
    await _save_otp(db, user.id, plain_otp, purpose="bank_linking")
    await db.commit()
    await send_otp_email(user.email, plain_otp)
    parts = user.email.split("@")
    hint = parts[0][:2] + "***@" + parts[1] if len(parts) == 2 else "***"
    return hint


# ═════════════════════════════════════════════════════════════════════════════
# PASSWORD RESET
# ═════════════════════════════════════════════════════════════════════════════

async def initiate_password_reset(db: AsyncSession, phone: str) -> None:
    """
    Send a password-reset OTP. Silently succeeds if phone is not found
    to avoid user-enumeration.
    """
    user = await _get_user_by_phone(db, phone)
    if user is None:
        # Silent success — do not reveal whether phone exists
        print(f"🌟 DEMO BYPASS: password reset requested for unknown phone {phone} 🌟")
        return

    plain_otp = security.generate_totp(phone)
    await _save_otp(db, user.id, plain_otp, purpose="password_reset")
    await db.commit()
    await send_otp_email(user.email, plain_otp)


async def complete_password_reset(
    db: AsyncSession,
    phone: str,
    otp_code: str,
    new_password: str,
) -> None:
    """
    Verify password-reset OTP, update password hash, revoke ALL refresh tokens
    to force logouts on other devices.

    Rule 1: new password is Bcrypt-hashed (cost=12).
    """
    user = await _get_user_by_phone(db, phone)
    if user is None:
        raise HTTPException(
            status_code=404,
            detail={
                "success": False,
                "error": {
                    "code": "USER_NOT_FOUND",
                    "message": "No account found with this phone number.",
                    "details": {},
                    "request_id": "req_pwreset",
                    "timestamp": _utc_now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
            },
        )

    now = _utc_now()
    result = await db.execute(
        select(OTPCode)
        .where(
            and_(
                OTPCode.user_id == user.id,
                OTPCode.purpose == "password_reset",
                OTPCode.is_used == False,  # noqa: E712
                OTPCode.expires_at > now,
            )
        )
        .order_by(OTPCode.created_at.desc())
        .limit(1)
    )
    otp_record = result.scalar_one_or_none()

    if otp_record is None:
        raise OTPExpiredError()

    if not security.verify_otp(otp_code, otp_record.code_hash):
        raise OTPInvalidError()

    # ── Consume OTP ───────────────────────────────────────────────────────────
    otp_record.is_used = True

    # ── Update password (Rule 1: Bcrypt cost=12) ──────────────────────────────
    user.password_hash = security.hash_password(new_password)
    user.login_attempts = 0

    # ── Revoke ALL active refresh tokens (force logout everywhere) ────────────
    await db.execute(
        update(RefreshToken)
        .where(
            and_(
                RefreshToken.user_id == user.id,
                RefreshToken.is_revoked == False,  # noqa: E712
            )
        )
        .values(is_revoked=True)
    )
    await db.commit()
    logger.info("Password reset complete for user %s", user.id)


# ═════════════════════════════════════════════════════════════════════════════
# PIN MANAGEMENT
# ═════════════════════════════════════════════════════════════════════════════

async def set_pin(db: AsyncSession, user_id, pin: str) -> None:
    """
    Set or update the transaction PIN for a user.

    Rule 1: PIN is Bcrypt-hashed (cost=12). NEVER stored plaintext.
    Validates format (exactly 4 digits) via security.validate_pin_format()
    before hashing.
    """
    if not security.validate_pin_format(pin):
        raise HTTPException(
            status_code=400,
            detail={
                "success": False,
                "error": {
                    "code": "PIN_INVALID",
                    "message": "PIN must be exactly 4 digits (0–9).",
                    "details": {},
                    "request_id": "req_setpin",
                    "timestamp": _utc_now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
            },
        )

    user = await _get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")

    user.pin_hash = security.hash_pin(pin)
    await db.commit()
    logger.info("PIN set for user %s", user_id)


# ═════════════════════════════════════════════════════════════════════════════
# ADMIN SEED  (Critical Point 17)
# ═════════════════════════════════════════════════════════════════════════════

async def seed_admin_user(db: AsyncSession) -> None:
    """
    Create the admin superuser from .env if it does not already exist.

    Critical Point 17: called once on application startup (via lifespan).
    Checks ADMIN_PHONE first to prevent duplicate creation on hot-restart.

    Rules enforced:
      - Rule 1: Password is Bcrypt-hashed (cost=12). NEVER plaintext.
      - is_superuser=True, is_verified=True, verification_tier=4.
      - Associated wallet seeded with PKR 0.00 balance.
    """
    if not all([settings.ADMIN_PHONE, settings.ADMIN_PASSWORD, settings.ADMIN_EMAIL]):
        logger.warning("seed_admin_user: ADMIN_PHONE/PASSWORD/EMAIL not configured — skipping.")
        return

    existing = await _get_user_by_phone(db, settings.ADMIN_PHONE)
    if existing is not None:
        logger.info("seed_admin_user: admin already exists (id=%s) — skipped.", existing.id)
        return

    admin = User(
        phone_number=settings.ADMIN_PHONE,
        email=settings.ADMIN_EMAIL,
        full_name="EasyPay Admin",
        password_hash=security.hash_password(settings.ADMIN_PASSWORD),  # Rule 1
        is_verified=True,
        is_active=True,
        is_locked=False,
        is_superuser=True,
        account_type="individual",
        verification_tier=4,
    )
    db.add(admin)
    await db.flush()

    wallet = Wallet(
        user_id=admin.id,
        balance=Decimal("0.00"),
        currency="PKR",
    )
    db.add(wallet)
    await db.commit()
    logger.info(
        "seed_admin_user: admin superuser created (phone=%s id=%s).",
        settings.ADMIN_PHONE,
        admin.id,
    )
