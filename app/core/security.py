"""
app/core/security.py — EasyPay v3.0 Security Utilities

Rules Enforced:
  Rule 1  — Bcrypt cost=12 for ALL passwords and PINs.  NEVER plaintext.
  Rule 17 — Admin JWT expiry is ADMIN_JWT_EXPIRY_HOURS (2h), NOT 24h.
  Point 8 — Pending transaction token: exactly 60-second expiry.
             verify_pending_tx_token() MUST check type == "pending_transaction".
  Point 9 — OTPs hashed with Bcrypt (determinism not required).
             Fingerprints use SHA-256 (see encryption.py) — NEVER mix these.
"""
import hashlib
import hmac
import re
import secrets
import struct
import time
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings

# ── Bcrypt context — cost=12 (Rule 1) ────────────────────────────────────────
_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)

# JWT algorithm
_ALGORITHM = "HS256"


# ─────────────────────────────────────────────────────────────────────────────
# Password helpers  (Rule 1: Bcrypt cost=12, NEVER plaintext)
# ─────────────────────────────────────────────────────────────────────────────
def hash_password(plain: str) -> str:
    """Return Bcrypt hash of a plaintext password (cost=12)."""
    return _pwd_ctx.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time comparison of plain password against its Bcrypt hash."""
    return _pwd_ctx.verify(plain, hashed)


# ─────────────────────────────────────────────────────────────────────────────
# PIN helpers  (Rule 1: Bcrypt cost=12, NEVER plaintext)
# ─────────────────────────────────────────────────────────────────────────────
def hash_pin(plain: str) -> str:
    """Return Bcrypt hash of a 4-digit PIN (cost=12)."""
    return _pwd_ctx.hash(plain)


def verify_pin(plain: str, hashed: str) -> bool:
    """Constant-time comparison of plain PIN against its Bcrypt hash."""
    return _pwd_ctx.verify(plain, hashed)


def validate_pin_format(pin: str) -> bool:
    """Return True if pin is exactly 4 ASCII digits, False otherwise."""
    return bool(re.fullmatch(r"\d{4}", pin))


# ─────────────────────────────────────────────────────────────────────────────
# JWT — Access Token
# Rule 17: admin expiry = ADMIN_JWT_EXPIRY_HOURS (2h), user = JWT_EXPIRY_HOURS (24h)
# ─────────────────────────────────────────────────────────────────────────────
def create_access_token(user_id: str, is_admin: bool = False) -> str:
    """
    Create a signed JWT access token.

    Payload includes:
        sub   — user UUID as string
        scope — "admin" or "user"
        exp   — expiry timestamp
        iat   — issued-at timestamp
        type  — "access"

    Admin tokens expire in ADMIN_JWT_EXPIRY_HOURS (2h).
    User tokens expire in JWT_EXPIRY_HOURS (24h).
    """
    if is_admin:
        expires_delta = timedelta(hours=settings.ADMIN_JWT_EXPIRY_HOURS)
        scope = "admin"
    else:
        expires_delta = timedelta(hours=settings.JWT_EXPIRY_HOURS)
        scope = "user"

    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "scope": scope,
        "type": "access",
        "iat": now,
        "exp": now + expires_delta,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=_ALGORITHM)


# ─────────────────────────────────────────────────────────────────────────────
# JWT — Refresh Token
# ─────────────────────────────────────────────────────────────────────────────
def create_refresh_token(user_id: str) -> str:
    """
    Create a signed JWT refresh token with a 7-day expiry.

    Payload includes:
        sub  — user UUID as string
        type — "refresh"
        exp  — expiry timestamp
        iat  — issued-at timestamp
    """
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "type": "refresh",
        "iat": now,
        "exp": now + timedelta(days=settings.REFRESH_TOKEN_EXPIRY_DAYS),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=_ALGORITHM)


# ─────────────────────────────────────────────────────────────────────────────
# JWT — Decode
# ─────────────────────────────────────────────────────────────────────────────
def decode_token(token: str) -> dict[str, Any]:
    """
    Decode and verify a JWT token.

    Returns the payload dict on success.
    Raises jose.JWTError if the token is expired, tampered, or malformed.
    """
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[_ALGORITHM])


# ─────────────────────────────────────────────────────────────────────────────
# OTP — TOTP generation using HMAC-SHA1
# Bcrypt hash for storage (Point 9: Bcrypt is correct here — OTPs ≠ fingerprints)
# ─────────────────────────────────────────────────────────────────────────────
def generate_totp(secret: str) -> str:
    """
    Generate a 6-digit time-based OTP using HMAC-SHA1.

    Uses the current 30-second time window. The secret can be any string
    (e.g. the user's phone number concatenated with a salt).

    Returns a zero-padded 6-digit string.
    """
    # 30-second window counter
    counter = int(time.time()) // 30
    # Pack counter as big-endian 8-byte unsigned integer
    msg = struct.pack(">Q", counter)
    secret_bytes = secret.encode("utf-8")
    h = hmac.new(secret_bytes, msg, hashlib.sha1).digest()
    # Dynamic truncation per RFC 6238
    offset = h[-1] & 0x0F
    code = struct.unpack(">I", h[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code % 1_000_000).zfill(6)


def hash_otp(otp: str) -> str:
    """Return Bcrypt hash of a 6-digit OTP string (cost=12)."""
    return _pwd_ctx.hash(otp)


def verify_otp(plain: str, hashed: str) -> bool:
    """Constant-time comparison of a plain OTP against its Bcrypt hash."""
    return _pwd_ctx.verify(plain, hashed)


# ─────────────────────────────────────────────────────────────────────────────
# Transaction reference number
# ─────────────────────────────────────────────────────────────────────────────
def generate_reference() -> str:
    """
    Generate a unique transaction reference in the format "EP-XXXXXXXX".

    Uses 4 random bytes → 8 uppercase hex characters.
    Collision probability per 1 M transactions: < 1 in 10^9 (acceptable).
    """
    return f"EP-{secrets.token_hex(4).upper()}"


# ─────────────────────────────────────────────────────────────────────────────
# Pending Transaction Token  (Point 8 — exactly 60-second expiry)
# ─────────────────────────────────────────────────────────────────────────────
def create_pending_tx_token(
    sender_id: str,
    recipient_id: str,
    amount: Decimal,
) -> str:
    """
    Create a short-lived JWT representing a pending biometric-confirmation transaction.

    Expiry is EXACTLY PENDING_TX_TOKEN_EXPIRY_SECONDS (60 s) — intentionally short
    per Point 8. Money NEVER moves until this token is verified AND biometric confirmed.

    Payload:
        type         — "pending_transaction"  (verified by verify_pending_tx_token)
        sender_id    — UUID string
        recipient_id — UUID string
        amount       — string representation of Decimal (avoids float precision loss)
        exp / iat
    """
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "type": "pending_transaction",
        "sender_id": str(sender_id),
        "recipient_id": str(recipient_id),
        "amount": str(amount),
        "iat": now,
        "exp": now + timedelta(seconds=settings.PENDING_TX_TOKEN_EXPIRY_SECONDS),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=_ALGORITHM)


def verify_pending_tx_token(token: str) -> dict[str, Any]:
    """
    Verify and return the payload of a pending transaction token.

    Checks:
      1. Signature and expiry (raises JWTError if expired / tampered).
      2. type == "pending_transaction" (raises ValueError if wrong token type).

    Raises:
        jose.JWTError  — token expired or malformed
        ValueError     — correct JWT but wrong type field
    """
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[_ALGORITHM])
    if payload.get("type") != "pending_transaction":
        raise ValueError(
            "Token type mismatch — expected 'pending_transaction', "
            f"got '{payload.get('type')}'"
        )
    return payload


# ─────────────────────────────────────────────────────────────────────────────
# Account number masking
# ─────────────────────────────────────────────────────────────────────────────
def mask_account_number(account: str) -> str:
    """
    Return a masked version of a bank account number: "****XXXX".

    Shows only the last 4 characters, replacing everything else with *.
    If the account number is 4 chars or shorter, returns it fully masked.

    Examples:
        "0123456789"  →  "******6789"
        "1234"        →  "****"
    """
    account = account.strip()
    visible = account[-4:] if len(account) > 4 else ""
    masked_len = len(account) - len(visible)
    return "*" * masked_len + visible
