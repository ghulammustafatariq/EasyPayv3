"""
app/core/dependencies.py — EasyPay v3.0 FastAPI Dependencies

Critical Rules Enforced:
  Point 1  — ALL SQLAlchemy queries use await db.execute(select(...)).
             NEVER use .query() or sync operations.
  Rule 1   — PIN verification uses Bcrypt cost=12 (via security.verify_pin).
  Rule 15  — Admin routes require BOTH valid admin JWT AND X-Admin-Key header.
  Rule 17  — Admin JWT expiry is 2 h (enforced in security.create_access_token).
  Rule 18  — Admin self-action guard lives in route handlers, not here.
  Tier ❿  — get_user_tier() mirrors COPILOT_AGENT_RULES_v3.md ❿ exactly.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import AsyncGenerator

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import security
from app.core.config import settings
from app.core.exceptions import PINInvalidError, PINLockedError
from app.db.base import get_db as _base_get_db

# ── Re-export get_db so route modules only need one import location ─────────
get_db = _base_get_db

# ── HTTP Bearer scheme — auto_error=False → we return a custom 401 message ─
_bearer = HTTPBearer(auto_error=False)

# ── Tier daily limits (mirrors RULES ❿) ─────────────────────────────────────
TIER_DAILY_LIMITS: dict[int, Decimal] = {
    0: Decimal("0.00"),
    1: Decimal("25000.00"),
    2: Decimal("100000.00"),
    3: Decimal("500000.00"),
    4: Decimal("2000000.00"),
}


# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL HELPER — build raw error detail dict for HTTPException.detail
# ─────────────────────────────────────────────────────────────────────────────

def _err(code: str, message: str, details: dict | None = None) -> dict:
    """
    Build a v3.0-compliant error envelope dict to pass as HTTPException.detail.

    The global StarletteHTTPException handler in main.py detects this structure
    (success=False) and passes it through without re-wrapping it.
    """
    return {
        "success": False,
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "request_id": f"req_{uuid.uuid4().hex[:8]}",
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# get_current_user
# ─────────────────────────────────────────────────────────────────────────────

async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
):
    """
    Decode Bearer JWT and return the authenticated User ORM object.

    Raises:
        401 AUTH_TOKEN_MISSING       — No Authorization header present.
        401 AUTH_TOKEN_EXPIRED       — Token is past its exp claim.
        401 AUTH_TOKEN_INVALID       — Token is malformed or tampered.
        401 USER_NOT_FOUND           — sub UUID no longer exists in DB.
        403 AUTH_ACCOUNT_NOT_VERIFIED — is_active is False.
        403 AUTH_ACCOUNT_LOCKED      — is_locked is True.
    """
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail=_err("AUTH_TOKEN_MISSING", "Authentication token is required."),
        )

    try:
        payload = security.decode_token(credentials.credentials)
    except JWTError as exc:
        msg = str(exc).lower()
        if "expired" in msg or "signature has expired" in msg:
            raise HTTPException(
                status_code=401,
                detail=_err(
                    "AUTH_TOKEN_EXPIRED",
                    "Access token has expired. Please log in again.",
                ),
            )
        raise HTTPException(
            status_code=401,
            detail=_err(
                "AUTH_TOKEN_INVALID",
                "Access token is invalid or has been tampered with.",
            ),
        )

    user_id: str | None = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=401,
            detail=_err("AUTH_TOKEN_INVALID", "Token payload is missing subject claim."),
        )

    # Point 1: ALWAYS await db.execute(select(...)) — NEVER .query()
    from app.models.database import User  # local import prevents circular dep

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=401,
            detail=_err(
                "USER_NOT_FOUND",
                "User associated with this token no longer exists.",
            ),
        )

    if not user.is_active:
        raise HTTPException(
            status_code=403,
            detail=_err(
                "AUTH_ACCOUNT_NOT_VERIFIED",
                "Your account is inactive. Please contact support.",
            ),
        )

    if user.is_locked:
        raise HTTPException(
            status_code=403,
            detail=_err(
                "AUTH_ACCOUNT_LOCKED",
                "Your account has been locked due to multiple failed attempts.",
            ),
        )

    return user


# ─────────────────────────────────────────────────────────────────────────────
# get_current_verified_user
# ─────────────────────────────────────────────────────────────────────────────

async def get_current_verified_user(
    current_user=Depends(get_current_user),
):
    """
    Extends get_current_user — additionally requires is_verified == True.

    is_verified is set to True after the user confirms their phone OTP
    during registration. Must be True before accessing any transactional
    endpoints.

    Raises:
        403 AUTH_ACCOUNT_NOT_VERIFIED — Phone OTP not yet confirmed.
    """
    if not current_user.is_verified:
        raise HTTPException(
            status_code=403,
            detail=_err(
                "AUTH_ACCOUNT_NOT_VERIFIED",
                "Please verify your phone number before accessing this feature.",
            ),
        )
    return current_user


# ─────────────────────────────────────────────────────────────────────────────
# get_current_admin  (Rule 15: JWT scope AND X-Admin-Key BOTH required)
# ─────────────────────────────────────────────────────────────────────────────

async def get_current_admin(
    request: Request,
    current_user=Depends(get_current_user),
):
    """
    Admin authentication gate — enforces TWO independent checks (Rule 15 / ❻):

    Check 1: current_user.is_superuser must be True (encoded in JWT at login).
    Check 2: X-Admin-Key request header must match settings.ADMIN_SECRET_HEADER.

    Both checks are always performed independently. Failing either raises 403.

    Raises:
        403 ADMIN_ACCESS_REQUIRED — Authenticated user is not a superuser.
        403 ADMIN_KEY_INVALID     — Header absent or value does not match.
    """
    # Check 1 — superuser flag (Rule 15 / ❻ Check 1)
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=403,
            detail=_err(
                "ADMIN_ACCESS_REQUIRED",
                "Admin access is required for this endpoint.",
            ),
        )

    # Check 2 — X-Admin-Key header (Rule 15 / ❻ Check 2)
    admin_key = request.headers.get("X-Admin-Key")
    if not admin_key or admin_key != settings.ADMIN_SECRET_HEADER:
        raise HTTPException(
            status_code=403,
            detail=_err(
                "ADMIN_KEY_INVALID",
                "X-Admin-Key header is missing or invalid.",
            ),
        )

    return current_user


# ─────────────────────────────────────────────────────────────────────────────
# verify_transaction_pin
# ─────────────────────────────────────────────────────────────────────────────

async def verify_transaction_pin(
    pin: str,
    current_user,
    db: AsyncSession,
) -> None:
    """
    Verify the user's transaction PIN. Lock the account after 3 failures.

    This is NOT a FastAPI dependency (it takes caller-supplied arguments).
    Call it from transaction route handlers after resolving current_user:

        await verify_transaction_pin(pin=body.pin, current_user=user, db=db)

    Args:
        pin:          Raw 4-digit PIN string entered by the user.
        current_user: User ORM object (from get_current_verified_user).
        db:           Active AsyncSession (from get_db).

    Raises:
        HTTPException 400 PIN_NOT_SET — User has not set a PIN yet.
        PINLockedError                — Account is now locked (3rd wrong PIN).
        PINInvalidError               — PIN is wrong; < 3 total failures.
    """
    if not current_user.pin_hash:
        raise HTTPException(
            status_code=400,
            detail=_err("PIN_NOT_SET", "You have not set a transaction PIN yet."),
        )

    if security.verify_pin(pin, current_user.pin_hash):
        # Correct PIN — reset failure counter
        if current_user.login_attempts != 0:
            current_user.login_attempts = 0
            await db.commit()
        return

    # Wrong PIN — increment counter
    current_user.login_attempts = (current_user.login_attempts or 0) + 1

    if current_user.login_attempts >= 3:
        current_user.is_locked = True
        await db.commit()
        raise PINLockedError(
            detail=f"Account locked after {current_user.login_attempts} incorrect PIN "
                   "attempts. Contact support to unlock your account.",
        )

    await db.commit()

    remaining = max(0, 3 - current_user.login_attempts)
    raise PINInvalidError(
        detail=f"Incorrect PIN. "
               f"{remaining} attempt{'s' if remaining != 1 else ''} remaining before lockout.",
    )


# ─────────────────────────────────────────────────────────────────────────────
# get_user_tier  (mirrors COPILOT_AGENT_RULES_v3.md ❿ exactly)
# ─────────────────────────────────────────────────────────────────────────────

def get_user_tier(user) -> int:
    """
    Compute and return the user's live verification tier (0–4).

    Tier  Description                             Daily Limit
    ────  ──────────────────────────────────────  ────────────
      4   CNIC + biometric + fingerprint + NADRA  PKR 2,000,000
      3   CNIC + biometric (liveness) verified    PKR   500,000
      2   CNIC verified                           PKR   100,000
      1   Phone OTP verified (is_verified=True)   PKR    25,000
      0   Unverified                              PKR        0

    Important (Critical Point 15): this function computes the tier from live
    model fields. After any KYC step changes these fields, you MUST persist
    the result back to user.verification_tier via calculate_and_save_tier().
    """
    if (
        user.cnic_verified
        and user.biometric_verified
        and user.fingerprint_verified
        and user.nadra_verified
    ):
        return 4

    if user.cnic_verified and user.biometric_verified:
        return 3

    if user.cnic_verified:
        return 2

    if user.is_verified:
        return 1

    return 0


async def calculate_and_save_tier(db: AsyncSession, user) -> int:
    """
    Compute the user's live verification tier and persist it.

    Critical Point 15: MUST be called after any KYC flag changes.
    The tier is NOT auto-calculated — it must be explicitly saved.
    Also updates wallet.daily_limit to match the new tier.
    Returns the computed tier integer.
    """
    tier = get_user_tier(user)
    user.verification_tier = tier

    # Update wallet daily limit to match new tier
    if user.wallet is not None:
        user.wallet.daily_limit = TIER_DAILY_LIMITS.get(tier, Decimal("0.00"))

    await db.commit()
    return tier
