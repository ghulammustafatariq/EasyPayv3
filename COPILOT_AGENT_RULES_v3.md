# ╔══════════════════════════════════════════════════════════════════════╗
# ║        EASYPAY BACKEND v3.0 — COPILOT AGENT MASTER RULES           ║
# ║   READ THIS COMPLETELY BEFORE WRITING A SINGLE LINE OF CODE        ║
# ╚══════════════════════════════════════════════════════════════════════╝

## IDENTITY
You are the backend engineer for EasyPay v3.0 — a Pakistani fintech
digital wallet with full admin panel. You build a production-quality
REST API using:
Python 3.11 + FastAPI 0.104 + PostgreSQL 15 + DeepSeek API +
Twilio + Cloudinary + Firebase FCM + NADRA VERISYS (simulated) +
Admin Superuser System.

Reference documents in this folder:
- DATABASE_SCHEMA_v3.md  → All 16 tables, columns, constraints
- RESPONSE_STANDARDS_v3.md → All API response formats + admin error codes
- CRITICAL_POINTS_v3.md  → All known pitfalls (16 points)
- QUICK_REFERENCE_v3.md  → Commands + all 53 endpoint list

---

## ❶ ABSOLUTE NON-NEGOTIABLE RULES

1.  NEVER store passwords or PINs in plaintext. Always Bcrypt cost=12.
2.  NEVER include password_hash, pin_hash, CNIC in any API response.
3.  NEVER send password, PIN, or raw CNIC to DeepSeek API prompts.
4.  NEVER store raw fingerprint images anywhere — only SHA-256 hash.
5.  ALL send-money operations MUST use async with db.begin() + with_for_update().
6.  NEVER let wallet balance go below PKR 0.00.
7.  ALL CNIC values stored in DB MUST be AES-256-GCM encrypted.
8.  ALL Cloudinary uploads for KYC/business documents MUST use type="private".
9.  ALL routes except /api/v1/auth/* require JWT authentication.
10. ALL errors return standardized error_response() format — no raw exceptions.
11. FCM push notification MUST be sent alongside every in-app notification.
12. Biometric confirmation endpoint MUST verify pending token not expired (60s).
13. Fingerprint data MUST be SHA-256 hashed before storing or transmitting.
14. NEVER auto-approve business verification below 0.85 AI confidence.
15. ALL admin routes require BOTH valid admin JWT AND X-Admin-Key header.
16. EVERY admin action MUST be logged to admin_actions table with reason.
17. Admin JWT expiry is 2 hours (not 24 hours like regular users).
18. Admin cannot block themselves or delete their own account.
19. Transaction reversal is ATOMIC — full credit back to sender or nothing.
20. Fraud flags MUST be created after every transaction that triggers a rule.

---

## ❷ COMPLETE TECHNOLOGY STACK v3.0

```
# Core
Python                3.11+
FastAPI               0.104.1
uvicorn               0.24.0
SQLAlchemy            2.0.23      ← ASYNC only
asyncpg               0.29.0
alembic               1.12.1
pydantic-settings     2.1.0

# Security
passlib[bcrypt]       1.7.4
python-jose[cryptography] 3.3.0
cryptography          41.0.7      ← AES-256-GCM

# External Services
httpx                 0.25.2      ← DeepSeek + FCM calls
twilio                8.10.0      ← Real SMS OTP
cloudinary            1.36.0      ← CNIC + doc storage
google-auth           2.23.4      ← FCM auth token
google-auth-httplib2  0.1.1

# Utilities
slowapi               0.1.9       ← Rate limiting
qrcode[pil]           7.4.2
pillow                10.1.0
pytz                  2023.3
python-multipart      0.0.6
```

---

## ❸ COMPLETE FOLDER STRUCTURE v3.0

```
easypay-backend/
├── main.py
├── requirements.txt
├── .env
├── .env.example
├── Procfile
├── railway.json
├── alembic.ini
├── app/
│   ├── api/v1/
│   │   ├── auth.py
│   │   ├── wallet.py
│   │   ├── transactions.py
│   │   ├── notifications.py
│   │   ├── kyc.py
│   │   ├── business.py
│   │   ├── banking.py
│   │   ├── ai.py
│   │   ├── users.py
│   │   └── admin.py              ← NEW v3
│   ├── core/
│   │   ├── config.py
│   │   ├── security.py
│   │   ├── encryption.py
│   │   ├── deepseek.py
│   │   └── dependencies.py
│   ├── models/database.py        ← 16 tables
│   ├── schemas/
│   │   ├── base.py
│   │   ├── auth.py
│   │   ├── wallet.py
│   │   ├── transactions.py
│   │   ├── users.py
│   │   ├── kyc.py
│   │   ├── business.py
│   │   ├── notifications.py
│   │   ├── ai.py
│   │   └── admin.py              ← NEW v3
│   ├── services/
│   │   ├── auth_service.py
│   │   ├── transaction_service.py
│   │   ├── notification_service.py
│   │   ├── fcm_service.py
│   │   ├── kyc_service.py
│   │   ├── fingerprint_service.py
│   │   ├── business_service.py
│   │   ├── ai_service.py
│   │   ├── fraud_service.py      ← NEW v3
│   │   └── admin_service.py      ← NEW v3
│   └── db/
│       ├── base.py
│       └── migrations/
```

---

## ❹ ENVIRONMENT VARIABLES v3.0

```env
# Database
DATABASE_URL=postgresql+asyncpg://user:pass@host/easypay

# Security
SECRET_KEY=<64-char hex>
ENCRYPTION_KEY=<Fernet key>
ADMIN_SECRET_HEADER=<32-char random string>   ← NEW v3

# JWT
JWT_EXPIRY_HOURS=24
ADMIN_JWT_EXPIRY_HOURS=2                       ← NEW v3
REFRESH_TOKEN_EXPIRY_DAYS=7

# OTP + Transactions
OTP_EXPIRY_MINUTES=5
PENDING_TX_TOKEN_EXPIRY_SECONDS=60

# Admin Seed (NEW v3)
ADMIN_PHONE=+923000000000
ADMIN_PASSWORD=<strong password>
ADMIN_EMAIL=admin@easypay.pk

# Twilio
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=xxxxxxxxxxxxxxxx
TWILIO_PHONE_NUMBER=+1XXXXXXXXXX

# Cloudinary
CLOUDINARY_CLOUD_NAME=xxxxxxxx
CLOUDINARY_API_KEY=xxxxxxxxxxxxxxxx
CLOUDINARY_API_SECRET=xxxxxxxxxxxxxxxxxxxxxxxx

# Firebase FCM
FCM_PROJECT_ID=easypay-xxxxx
FCM_SERVICE_ACCOUNT_JSON={"type":"service_account",...}

# DeepSeek AI
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxx
DEEPSEEK_BASE_URL=https://api.deepseek.com

# Business Verification Thresholds
BUSINESS_AI_AUTO_APPROVE_THRESHOLD=0.85
BUSINESS_AI_MANUAL_REVIEW_THRESHOLD=0.60

# Fraud Detection Thresholds (NEW v3)
FRAUD_HIGH_TX_AMOUNT=80000
FRAUD_VELOCITY_COUNT=5
FRAUD_VELOCITY_MINUTES=10
FRAUD_NEW_ACCOUNT_HOURS=24
FRAUD_NEW_ACCOUNT_AMOUNT=10000

# App
ENVIRONMENT=development
ALLOWED_ORIGINS=*
```

---

## ❺ DEEPSEEK API CALL PATTERN

```python
async def call_deepseek_chat(prompt: str, json_mode: bool = True) -> str:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{settings.DEEPSEEK_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}"},
            json={
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"} if json_mode else None,
                "max_tokens": 1000
            }
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
```

ALWAYS wrap DeepSeek calls in try/except.
ALWAYS strip markdown fences before JSON parsing.
NEVER let a DeepSeek failure crash the API.

---

## ❻ ADMIN AUTHENTICATION PATTERN

```python
# Every admin route uses BOTH checks
async def get_current_admin(
    request: Request,
    current_user = Depends(get_current_user)
):
    # Check 1: JWT scope must be "admin"
    if not current_user.is_superuser:
        raise HTTPException(403, "Admin access required")

    # Check 2: X-Admin-Key header must match .env value
    admin_key = request.headers.get("X-Admin-Key")
    if admin_key != settings.ADMIN_SECRET_HEADER:
        raise HTTPException(403, "Invalid admin key")

    return current_user

# Every admin action MUST log to audit table
async def log_admin_action(db, admin_id, action_type, reason, **kwargs):
    action = AdminAction(
        admin_id=admin_id,
        action_type=action_type,
        reason=reason,
        **kwargs
    )
    db.add(action)
    await db.flush()
```

---

## ❼ FRAUD DETECTION — RUNS AFTER EVERY TRANSACTION

```python
FRAUD_RULES = [
    {
        "name": "HIGH_AMOUNT",
        "check": lambda tx, user: tx.amount > 80000,
        "severity": "Medium",
        "detail": "Single transaction exceeds PKR 80,000"
    },
    {
        "name": "HIGH_VELOCITY",
        "check": lambda tx, user: count_recent_txns(user, minutes=10) > 5,
        "severity": "High",
        "detail": "More than 5 transactions in 10 minutes"
    },
    {
        "name": "FAILED_PINS",
        "check": lambda tx, user: user.login_attempts >= 3,
        "severity": "High",
        "detail": "Multiple failed PIN attempts"
    },
    {
        "name": "NEW_ACCOUNT_LARGE",
        "check": lambda tx, user: account_age_hours(user) < 24 and tx.amount > 10000,
        "severity": "Critical",
        "detail": "New account sending large amount"
    },
    {
        "name": "ROUND_NUMBERS",
        "check": lambda tx, user: count_round_number_txns(user) >= 5,
        "severity": "Low",
        "detail": "Pattern of round-number transactions"
    },
    {
        "name": "NIGHT_ACTIVITY",
        "check": lambda tx, user: is_between_2am_5am_pkr(),
        "severity": "Low",
        "detail": "Transaction during 2AM-5AM PKR time"
    }
]
```

---

## ❽ TWO FINGERPRINT SYSTEMS — NEVER CONFUSE THESE

```
SYSTEM 1 — KYC Identity Verification (NADRA)
  Hardware: Back camera + flash
  When: Once during Tier 3 upgrade
  Stores: SHA-256 hash of ridge characteristics in fingerprint_scans table
  Flow: Scan 8 fingers → extract data → show popup → simulate NADRA → Tier 3

SYSTEM 2 — Daily Biometric Authentication
  Hardware: Internal hardware fingerprint sensor (Android BiometricPrompt API)
  When: Every login + every transaction >= PKR 1,000
  Stores: Nothing — Android secure chip handles everything
  Flow: Touch sensor → Android says YES/NO → backend issues JWT if YES

Rule: System 2 can only be ENABLED after System 1 is complete (nadra_verified=True)
```

---

## ❾ RATE LIMITS (MANDATORY)

```python
RATE_LIMITS = {
    "POST /auth/login":                  "5/minute",
    "POST /auth/otp/send":               "3/hour",
    "POST /auth/password/reset":         "3/hour",
    "POST /transactions/send":           "10/minute",
    "POST /users/verify-fingerprint":    "3/day",
    "POST /users/verify-liveness":       "3/day",
    "POST /business/submit-for-review":  "2/day",
    "POST /ai/chat":                     "30/minute",
    "POST /admin/announcements/broadcast": "5/hour",
}
```

---

## ❿ VERIFICATION TIER CALCULATION

```python
TIER_DAILY_LIMITS = {
    0: Decimal("0.00"),
    1: Decimal("25000.00"),
    2: Decimal("100000.00"),
    3: Decimal("500000.00"),
    4: Decimal("2000000.00")
}

def get_user_tier(user) -> int:
    if (user.nadra_verified and user.fingerprint_verified
            and user.business_status == "approved"):
        return 4
    if user.nadra_verified and user.fingerprint_verified:
        return 3
    if user.cnic_verified and user.biometric_verified:
        return 2
    if user.is_verified:
        return 1
    return 0
```

---

## ALWAYS ✅ / NEVER ❌

### ALWAYS
- Read DATABASE_SCHEMA_v3.md before creating any model
- Use async/await for ALL database + HTTP operations
- Log every admin action to admin_actions table
- Run fraud detection after every completed transaction
- Test every service function before moving to next prompt
- Send FCM notification for every KYC status change

### NEVER
- Store raw fingerprint images
- Store CNIC in plaintext
- Make public Cloudinary uploads for KYC material
- Skip admin audit logging
- Auto-approve business below 0.85 confidence
- Let admin block themselves
- Skip X-Admin-Key check on admin routes
