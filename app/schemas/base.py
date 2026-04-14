"""
app/schemas/base.py — EasyPay v3.0 Response Helpers

Implements the exact JSON envelopes defined in RESPONSE_STANDARDS_v3.md.

SUCCESS envelope:
    { "success": true, "message": "...", "data": {...},
      "meta": { "request_id": "req_<8hex>", "timestamp": "<UTC ISO>", "version": "3.0" } }

ERROR envelope:
    { "success": false,
      "error": { "code": "...", "message": "...", "details": {...},
                 "request_id": "req_<8hex>", "timestamp": "<UTC ISO>" } }
"""
import uuid
from datetime import datetime, timezone
from typing import Any, Optional


def _request_id() -> str:
    """Generate a short, prefixed request ID: req_<8 hex chars>."""
    return f"req_{uuid.uuid4().hex[:8]}"


def _utc_now() -> str:
    """Return the current UTC time as an ISO-8601 string with Z suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def success_response(
    message: str,
    data: Any = None,
) -> dict:
    """
    Build a standardised success envelope.

    Args:
        message: Human-readable success message shown to the client.
        data:    Response payload (dict, list, or scalar). Defaults to {}.

    Returns:
        Dict matching the v3.0 success envelope.
    """
    return {
        "success": True,
        "message": message,
        "data": data if data is not None else {},
        "meta": {
            "request_id": _request_id(),
            "timestamp": _utc_now(),
            "version": "3.0",
        },
    }


def error_response(
    code: str,
    message: str,
    details: Optional[dict] = None,
) -> dict:
    """
    Build a standardised error envelope.

    Args:
        code:    UPPER_SNAKE_CASE error code from RESPONSE_STANDARDS_v3.md.
        message: Human-readable message the user can act on.
        details: Optional dict with extra context (validation errors, etc.).

    Returns:
        Dict matching the v3.0 error envelope.
    """
    return {
        "success": False,
        "error": {
            "code": code,
            "message": message,
            "details": details if details is not None else {},
            "request_id": _request_id(),
            "timestamp": _utc_now(),
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# B24 — Card System Error Codes (RESPONSE_STANDARDS_v3.md supplement)
# ─────────────────────────────────────────────────────────────────────────────
# Code                        | HTTP | Meaning
# ─────────────────────────────────────────────────────────────────────────────
# CARD_TIER_INSUFFICIENT      | 403  | Tier too low for requested card type
# CARD_ALREADY_EXISTS         | 409  | User already has an active card of this type
# CARD_ADDRESS_REQUIRED       | 400  | Physical card requires delivery_address
# CARD_NOT_FOUND              | 404  | Card ID not found or not owned by user
# CARD_BLOCKED                | 400  | Card is permanently blocked
# CARD_EXPIRED                | 400  | Card has expired
# CARD_ALREADY_FROZEN         | 400  | Card is already in frozen state
# CARD_NOT_FROZEN             | 400  | Unfreeze attempted on non-frozen card
# CARD_INACTIVE               | 400  | Action not allowed on blocked/expired/replaced card
# CARD_SETTING_INVALID        | 400  | Setting not valid for this card type
# CARD_ALREADY_INACTIVE       | 400  | Block attempted on already-inactive card
# CARD_ALREADY_REPLACED       | 400  | Replace attempted on already-replaced card
# CARD_PIN_REQUIRED           | 400  | Wallet PIN must be set before issuing a card
# BLOCK_REASON_REQUIRED       | 400  | Block reason field is mandatory
# CARD_LIMIT_EXCEEDED         | 400  | Requested limit exceeds tier maximum
