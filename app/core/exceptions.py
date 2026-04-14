"""
app/core/exceptions.py — EasyPay v3.0 Custom Domain Exception Hierarchy

All exceptions inherit from EasyPayException and are caught by the global
handler in main.py, which converts them to the standard error_response()
envelope defined in RESPONSE_STANDARDS_v3.md.

Usage:
    raise InsufficientBalanceError(
        detail="Wallet balance is PKR 500.00. You need PKR 1,200.00.",
        error_code="WALLET_INSUFFICIENT_BALANCE",
    )

Each subclass ships with a sensible default detail and error_code so callers
can raise them with zero arguments when the default message is sufficient.
"""
from __future__ import annotations


# ══════════════════════════════════════════════════════════════════════════════
# BASE EXCEPTION
# ══════════════════════════════════════════════════════════════════════════════

class EasyPayException(Exception):
    """
    Root exception for all EasyPay domain errors.

    Attributes:
        detail      — Human-readable message the client can act on.
        error_code  — UPPER_SNAKE_CASE code from RESPONSE_STANDARDS_v3.md.
        status_code — HTTP status code (set as class attribute on subclasses).
    """

    status_code: int = 400

    def __init__(
        self,
        detail: str = "An unexpected error occurred.",
        error_code: str | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        # Fall back to the class name uppercased if no code is supplied.
        self.error_code = error_code or type(self).__name__.upper()


# ══════════════════════════════════════════════════════════════════════════════
# WALLET
# ══════════════════════════════════════════════════════════════════════════════

class InsufficientBalanceError(EasyPayException):
    """Wallet balance is too low for the requested transaction."""

    status_code = 400

    def __init__(
        self,
        detail: str = "Insufficient wallet balance for this transaction.",
        error_code: str = "WALLET_INSUFFICIENT_BALANCE",
    ) -> None:
        super().__init__(detail, error_code)


class WalletFrozenError(EasyPayException):
    """Wallet has been administratively frozen and cannot be used."""

    status_code = 403

    def __init__(
        self,
        detail: str = "This wallet has been administratively frozen. Contact support.",
        error_code: str = "WALLET_FROZEN",
    ) -> None:
        super().__init__(detail, error_code)


class DailyLimitExceededError(EasyPayException):
    """Transaction would exceed the user's daily send limit for their tier."""

    status_code = 400

    def __init__(
        self,
        detail: str = "Daily transaction limit for your verification tier has been reached.",
        error_code: str = "WALLET_DAILY_LIMIT_EXCEEDED",
    ) -> None:
        super().__init__(detail, error_code)


# ══════════════════════════════════════════════════════════════════════════════
# RECIPIENT
# ══════════════════════════════════════════════════════════════════════════════

class RecipientNotFoundError(EasyPayException):
    """Transfer recipient phone/ID does not match any active account."""

    status_code = 404

    def __init__(
        self,
        detail: str = "Recipient not found. Please check the phone number and try again.",
        error_code: str = "USER_NOT_FOUND",
    ) -> None:
        super().__init__(detail, error_code)


# ══════════════════════════════════════════════════════════════════════════════
# OTP
# ══════════════════════════════════════════════════════════════════════════════

class OTPExpiredError(EasyPayException):
    """OTP code is older than OTP_EXPIRY_MINUTES and can no longer be used."""

    status_code = 400

    def __init__(
        self,
        detail: str = "The OTP code has expired. Please request a new one.",
        error_code: str = "OTP_EXPIRED",
    ) -> None:
        super().__init__(detail, error_code)


class OTPInvalidError(EasyPayException):
    """OTP code does not match the one that was sent."""

    status_code = 400

    def __init__(
        self,
        detail: str = "The OTP code is incorrect. Please try again.",
        error_code: str = "OTP_INVALID",
    ) -> None:
        super().__init__(detail, error_code)


# ══════════════════════════════════════════════════════════════════════════════
# PIN
# ══════════════════════════════════════════════════════════════════════════════

class PINInvalidError(EasyPayException):
    """Transaction PIN is incorrect (< 3 failures so account is not yet locked)."""

    status_code = 400

    def __init__(
        self,
        detail: str = "Transaction PIN is incorrect.",
        error_code: str = "PIN_INVALID",
    ) -> None:
        super().__init__(detail, error_code)


class PINLockedError(EasyPayException):
    """Account locked after 3 consecutive wrong PIN attempts."""

    status_code = 403

    def __init__(
        self,
        detail: str = "Account locked after 3 incorrect PIN attempts. Contact support to unlock.",
        error_code: str = "PIN_LOCKED",
    ) -> None:
        super().__init__(detail, error_code)


# ══════════════════════════════════════════════════════════════════════════════
# AI / DEEPSEEK
# ══════════════════════════════════════════════════════════════════════════════

class AIServiceUnavailableError(EasyPayException):
    """
    DeepSeek API is unreachable, returned a non-200 status, or the response
    could not be parsed as JSON.

    HTTP 503 so load balancers / clients know to retry.
    """

    status_code = 503

    def __init__(
        self,
        detail: str = "AI service is temporarily unavailable. Please try again shortly.",
        error_code: str = "AI_SERVICE_UNAVAILABLE",
    ) -> None:
        super().__init__(detail, error_code)


# ══════════════════════════════════════════════════════════════════════════════
# BIOMETRIC
# ══════════════════════════════════════════════════════════════════════════════

class BiometricTokenExpiredError(EasyPayException):
    """
    The 60-second pending transaction biometric window has passed.
    Point 8: Money NEVER moves after this — user must re-initiate.
    """

    status_code = 400

    def __init__(
        self,
        detail: str = "Biometric confirmation window expired (60 seconds). Please start the transaction again.",
        error_code: str = "BIOMETRIC_TOKEN_EXPIRED",
    ) -> None:
        super().__init__(detail, error_code)


# ══════════════════════════════════════════════════════════════════════════════
# KYC
# ══════════════════════════════════════════════════════════════════════════════

class CNICVerificationError(EasyPayException):
    """DeepSeek vision could not extract valid fields from the CNIC image."""

    status_code = 400

    def __init__(
        self,
        detail: str = "Could not verify CNIC from the provided image. Ensure the image is clear and unobstructed.",
        error_code: str = "CNIC_VERIFICATION_FAILED",
    ) -> None:
        super().__init__(detail, error_code)


class CNICNameMismatchError(EasyPayException):
    """
    The name extracted from the CNIC image does not match the account holder's
    registered full name. Raised before cnic_verified is set to prevent
    identity fraud.
    """

    status_code = 422

    def __init__(
        self,
        extracted: str = "",
        registered: str = "",
    ) -> None:
        detail = (
            f"The name on this CNIC (\u2018{extracted}\u2019) does not match "
            f"your registered account name (\u2018{registered}\u2019). "
            "Please upload your own CNIC."
        )
        super().__init__(detail, "KYC_NAME_MISMATCH")


class LivenessVerificationError(EasyPayException):
    """Face match confidence is below the required 80 % threshold."""

    status_code = 400

    def __init__(
        self,
        detail: str = "Liveness verification failed. Please ensure good lighting and try again.",
        error_code: str = "LIVENESS_VERIFICATION_FAILED",
    ) -> None:
        super().__init__(detail, error_code)


class BusinessVerificationError(EasyPayException):
    """Business document AI verification did not meet the required confidence threshold."""

    status_code = 400

    def __init__(
        self,
        detail: str = "Business verification failed. Please upload clear, valid business documents.",
        error_code: str = "BUSINESS_VERIFICATION_FAILED",
    ) -> None:
        super().__init__(detail, error_code)


# ══════════════════════════════════════════════════════════════════════════════
# ADMIN
# ══════════════════════════════════════════════════════════════════════════════

class AdminSelfActionError(EasyPayException):
    """
    Admin attempted to perform a prohibited action on their own account
    (Rule 18: block, delete, or role-change own account is not allowed).
    """

    status_code = 400

    def __init__(
        self,
        detail: str = "Administrators cannot perform this action on their own account.",
        error_code: str = "ADMIN_SELF_ACTION",
    ) -> None:
        super().__init__(detail, error_code)


class AdminKeyInvalidError(EasyPayException):
    """X-Admin-Key header is missing or does not match settings.ADMIN_SECRET_HEADER."""

    status_code = 403

    def __init__(
        self,
        detail: str = "X-Admin-Key header is missing or invalid.",
        error_code: str = "ADMIN_KEY_INVALID",
    ) -> None:
        super().__init__(detail, error_code)


# ══════════════════════════════════════════════════════════════════════════════
# FRAUD
# ══════════════════════════════════════════════════════════════════════════════

class FraudFlagNotFoundError(EasyPayException):
    """Requested fraud flag ID does not exist in the fraud_flags table."""

    status_code = 404

    def __init__(
        self,
        detail: str = "Fraud flag not found.",
        error_code: str = "FRAUD_FLAG_NOT_FOUND",
    ) -> None:
        super().__init__(detail, error_code)


# ══════════════════════════════════════════════════════════════════════════════
# TRANSACTION REVERSAL
# ══════════════════════════════════════════════════════════════════════════════

class ReversalError(EasyPayException):
    """
    Transaction cannot be reversed — either already reversed, not in
    'completed' status, or the atomic credit-back failed (Rule 19).
    """

    status_code = 400

    def __init__(
        self,
        detail: str = "This transaction cannot be reversed.",
        error_code: str = "REVERSAL_ERROR",
    ) -> None:
        super().__init__(detail, error_code)


# ══════════════════════════════════════════════════════════════════════════════
# ZAKAT
# ══════════════════════════════════════════════════════════════════════════════

class ZakatAlreadyPaidError(EasyPayException):
    """Zakat has already been paid for this calculation."""

    status_code = 400

    def __init__(
        self,
        detail: str = "Zakat has already been paid for this calculation.",
        error_code: str = "ZAKAT_ALREADY_PAID",
    ) -> None:
        super().__init__(detail, error_code)


# ══════════════════════════════════════════════════════════════════════════════
# TRUSTED CIRCLE
# ══════════════════════════════════════════════════════════════════════════════

class ContactAlreadyInCircleError(EasyPayException):
    """Contact is already in the user's Trusted Circle."""

    status_code = 409

    def __init__(
        self,
        detail: str = "This contact is already in your Trusted Circle.",
        error_code: str = "CONTACT_ALREADY_IN_CIRCLE",
    ) -> None:
        super().__init__(detail, error_code)


# ══════════════════════════════════════════════════════════════════════════════
# HISSA
# ══════════════════════════════════════════════════════════════════════════════

class GroupNotFoundError(EasyPayException):
    """Hissa group not found."""

    status_code = 404

    def __init__(
        self,
        detail: str = "Hissa group not found.",
        error_code: str = "GROUP_NOT_FOUND",
    ) -> None:
        super().__init__(detail, error_code)


class NotGroupMemberError(EasyPayException):
    """User is not a member of this Hissa group."""

    status_code = 403

    def __init__(
        self,
        detail: str = "You are not a member of this group.",
        error_code: str = "NOT_GROUP_MEMBER",
    ) -> None:
        super().__init__(detail, error_code)
