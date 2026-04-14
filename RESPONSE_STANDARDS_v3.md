# EASYPAY v3.0 — API RESPONSE STANDARDS
# Every API response MUST follow these exact formats.

---

## ✅ SUCCESS RESPONSE ENVELOPE

```json
{
  "success": true,
  "message": "Human-readable success message",
  "data": { },
  "meta": {
    "request_id": "req_a1b2c3d4",
    "timestamp": "2026-04-05T10:30:00Z",
    "version": "3.0"
  }
}
```

**Paginated list data:**
```json
{
  "items": [...],
  "total": 150,
  "page": 1,
  "per_page": 20,
  "total_pages": 8,
  "has_next": true,
  "has_prev": false
}
```

---

## ❌ ERROR RESPONSE ENVELOPE

```json
{
  "success": false,
  "error": {
    "code": "ERROR_CODE_SNAKE_CASE",
    "message": "Human-readable message the user can act on",
    "details": {},
    "request_id": "req_a1b2c3d4",
    "timestamp": "2026-04-05T10:30:00Z"
  }
}
```

---

## ERROR CODES — USER ROUTES

| Error Code | HTTP | Trigger |
|---|---|---|
| AUTH_INVALID_CREDENTIALS | 401 | Wrong phone or password |
| AUTH_TOKEN_EXPIRED | 401 | JWT has expired |
| AUTH_TOKEN_INVALID | 401 | JWT is malformed or tampered |
| AUTH_TOKEN_MISSING | 401 | No Authorization header |
| AUTH_ACCOUNT_LOCKED | 403 | 5+ failed login attempts |
| AUTH_ACCOUNT_NOT_VERIFIED | 403 | Phone OTP not confirmed yet |
| AUTH_INSUFFICIENT_PERMISSION | 403 | Accessing another user's data |
| USER_NOT_FOUND | 404 | User ID/phone not in database |
| USER_ALREADY_EXISTS | 409 | Duplicate phone or CNIC |
| OTP_INVALID | 400 | OTP code does not match |
| OTP_EXPIRED | 400 | OTP older than 5 minutes |
| OTP_ALREADY_USED | 400 | OTP was already consumed |
| PIN_INVALID | 400 | Wrong transaction PIN |
| PIN_LOCKED | 403 | 3 wrong PIN attempts |
| PIN_NOT_SET | 400 | User has not set a PIN yet |
| WALLET_INSUFFICIENT_BALANCE | 400 | Send amount exceeds balance |
| WALLET_FROZEN | 403 | Wallet is administratively frozen |
| WALLET_DAILY_LIMIT_EXCEEDED | 400 | Daily transaction limit reached |
| TRANSACTION_NOT_FOUND | 404 | Transaction ID not found |
| TRANSACTION_SELF_TRANSFER | 400 | Sender and recipient are the same |
| BIOMETRIC_TOKEN_EXPIRED | 400 | 60s biometric window expired |
| BIOMETRIC_NOT_ENABLED | 403 | User has not enabled biometric login |
| KYC_TIER_INSUFFICIENT | 403 | Action requires higher verification tier |
| CNIC_VERIFICATION_FAILED | 400 | DeepSeek could not read CNIC |
| LIVENESS_VERIFICATION_FAILED | 400 | Face match below 80% confidence |
| FINGERPRINT_QUALITY_POOR | 400 | Ridge quality score below 40 |
| BUSINESS_ALREADY_REGISTERED | 409 | User already has a business profile |
| BUSINESS_REQUIRES_TIER2 | 403 | Must complete CNIC + liveness first |
| AI_SERVICE_UNAVAILABLE | 503 | DeepSeek API unreachable |
| RATE_LIMIT_EXCEEDED | 429 | Too many requests |

## ERROR CODES — ADMIN ROUTES (NEW v3)

| Error Code | HTTP | Trigger |
|---|---|---|
| ADMIN_ACCESS_REQUIRED | 403 | Non-admin accessing admin route |
| ADMIN_KEY_INVALID | 403 | X-Admin-Key header missing or wrong |
| ADMIN_SELF_ACTION | 400 | Admin trying to block/delete themselves |
| ADMIN_REASON_REQUIRED | 400 | No reason provided for admin action |
| FRAUD_FLAG_NOT_FOUND | 404 | Fraud flag ID not found |
| FRAUD_FLAG_ALREADY_RESOLVED | 400 | Flag already resolved or escalated |
| REVERSAL_ALREADY_REVERSED | 400 | Transaction already reversed |
| REVERSAL_NOT_COMPLETED | 400 | Can only reverse completed transactions |

---

## PYTHON RESPONSE HELPERS

```python
# app/schemas/base.py
import uuid
from datetime import datetime, timezone

def success_response(message: str, data=None, meta_extra: dict = {}):
    return {
        "success": True,
        "message": message,
        "data": data or {},
        "meta": {
            "request_id": f"req_{uuid.uuid4().hex[:8]}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": "3.0",
            **meta_extra
        }
    }

def error_response(code: str, message: str, details: dict = {}, status_code: int = 400):
    from fastapi import HTTPException
    raise HTTPException(
        status_code=status_code,
        detail={
            "success": False,
            "error": {
                "code": code,
                "message": message,
                "details": details,
                "request_id": f"req_{uuid.uuid4().hex[:8]}",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        }
    )
```
