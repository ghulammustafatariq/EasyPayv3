"""
app/schemas/auth.py — EasyPay v3.0 Authentication Schemas

Rule 2: NEVER include password_hash, pin_hash, or cnic_encrypted in responses.
All request schemas that accept passwords apply minimum-strength validation
on the Pydantic layer to fail fast before any DB work.
"""
import re
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator


# ── Shared validator ──────────────────────────────────────────────────────────

def _validate_password_strength(v: str) -> str:
    """Minimum: 8 chars, 1 uppercase, 1 digit, 1 special char."""
    if len(v) < 8:
        raise ValueError("Password must be at least 8 characters long.")
    if not re.search(r"[A-Z]", v):
        raise ValueError("Password must contain at least one uppercase letter.")
    if not re.search(r"\d", v):
        raise ValueError("Password must contain at least one digit.")
    if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", v):
        raise ValueError("Password must contain at least one special character.")
    return v


def _validate_phone(v: str) -> str:
    """Pakistani phone: +923XXXXXXXXX (13 chars) or 03XXXXXXXXX (11 chars)."""
    clean = v.strip().replace(" ", "").replace("-", "")
    if not re.fullmatch(r"(\+923\d{9}|03\d{9})", clean):
        raise ValueError(
            "Phone number must be in format +923XXXXXXXXX or 03XXXXXXXXX."
        )
    return clean


# ══════════════════════════════════════════════════════════════════════════════
# REQUEST SCHEMAS
# ══════════════════════════════════════════════════════════════════════════════

class UserRegisterRequest(BaseModel):
    """Registration payload — Rule 2: cnic stored encrypted, never echoed back."""

    phone: str = Field(..., examples=["+923001234567"])
    email: EmailStr = Field(..., examples=["user@example.com"])
    full_name: str = Field(..., min_length=2, max_length=100, examples=["Ali Khan"])
    cnic: str = Field(
        ...,
        pattern=r"^\d{5}-\d{7}-\d$",
        description="Pakistani CNIC in format XXXXX-XXXXXXX-X",
        examples=["42201-1234567-9"],
    )
    password: str = Field(..., min_length=8, examples=["Str0ng@Pass"])

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        return _validate_phone(v)

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        return _validate_password_strength(v)


class LoginRequest(BaseModel):
    phone: str = Field(..., examples=["+923001234567"])
    password: str = Field(..., examples=["Str0ng@Pass"])

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        return _validate_phone(v)


class OTPSendRequest(BaseModel):
    phone: str = Field(..., examples=["+923001234567"])

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        return _validate_phone(v)


class OTPVerifyRequest(BaseModel):
    phone: str = Field(..., examples=["+923001234567"])
    otp_code: str = Field(..., pattern=r"^\d{6}$", examples=["482910"])

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        return _validate_phone(v)


class PINSetRequest(BaseModel):
    """Transaction PIN — exactly 4 digits, validated here before Bcrypt hashing."""

    pin: str = Field(
        ...,
        pattern=r"^\d{4}$",
        description="Exactly 4 digits",
        examples=["1234"],
    )


class PasswordResetRequest(BaseModel):
    phone: str = Field(..., examples=["+923001234567"])
    otp_code: str = Field(..., pattern=r"^\d{6}$", examples=["482910"])
    new_password: str = Field(..., min_length=8, examples=["NewStr0ng@Pass"])

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        return _validate_phone(v)

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        return _validate_password_strength(v)


class PINVerifyRequest(BaseModel):
    """PIN verification payload — used on /auth/pin/verify endpoint."""

    pin: str = Field(
        ...,
        pattern=r"^\d{4}$",
        description="Exactly 4 digits",
        examples=["1234"],
    )


class PINLoginRequest(BaseModel):
    phone: str = Field(..., examples=["+923001234567"])
    pin: str = Field(..., pattern=r"^\d{4}$", examples=["1234"])

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        return _validate_phone(v)



class FCMTokenRequest(BaseModel):
    """FCM device token update payload."""

    fcm_token: str = Field(..., min_length=1, description="Firebase Cloud Messaging device token")


class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(..., description="JWT refresh token")


# ══════════════════════════════════════════════════════════════════════════════
# RESPONSE SCHEMAS
# ══════════════════════════════════════════════════════════════════════════════

class TokenResponse(BaseModel):
    """Returned after successful login or token refresh."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(..., description="Access token expiry in seconds")

    model_config = {"from_attributes": True}
