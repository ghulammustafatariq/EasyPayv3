# EASYPAY v3.0 — ALL COPILOT PROMPTS
# BACKEND: B01–B23 | FRONTEND: F01–F21
# Execute backend first (Day 1), then frontend (Day 2-3)
# NEVER skip a prompt. NEVER run two at once.

---

# ══════════════════════════════════════════════════════
#                  BACKEND PROMPTS (B01–B23)
# ══════════════════════════════════════════════════════

## B01 — PROJECT SCAFFOLD & ENVIRONMENT
```
Read COPILOT_AGENT_RULES_v3.md completely first.

Create complete project scaffold:
1. requirements.txt with ALL packages from rules file
2. .env.example with all keys from rules file (empty values)
3. Procfile: web: uvicorn main:app --host 0.0.0.0 --port $PORT --workers 2
4. railway.json with healthcheck at /health and pre-deploy: alembic upgrade head
5. main.py: FastAPI app, CORS, SlowAPI, global exception handler, health endpoint,
   lifespan function that seeds admin superuser from .env on startup
6. app/core/config.py: Pydantic Settings reading all .env variables
   Include async_database_url property replacing postgresql:// with postgresql+asyncpg://
7. app/schemas/base.py: success_response() and error_response() helpers
8. All __init__.py files in every folder
9. Configure cloudinary in main.py startup using settings
Test: uvicorn main:app --reload starts without errors
```

## B02 — DATABASE MODELS (ALL 16 TABLES)
```
Read DATABASE_SCHEMA_v3.md completely first.

Create app/db/base.py: AsyncEngine + AsyncSessionLocal + Base + get_db()
Create app/models/database.py with ALL 16 SQLAlchemy ORM models:
  User, Wallet, Transaction, BankAccount, OTPCode, RefreshToken,
  AIInsight, ChatSession, Notification, LoginAudit,
  BusinessProfile, BusinessDocument, FingerprintScan,
  AdminAction, FraudFlag, SystemAnnouncement

Critical: All PKs = UUID, all money = Numeric(12,2), all timestamps = DateTime(timezone=True)

User model must include ALL v3 columns including:
  is_superuser, risk_score, is_flagged, flag_reason

Transaction model must include:
  is_flagged, flag_reason, flagged_by, flagged_at, reversed_by, reversal_reason

Initialize alembic, run migration, confirm all 16 tables created.
```

## B03 — SECURITY + ENCRYPTION MODULE
```
Create app/core/security.py:
- hash_password(plain) / verify_password(plain, hashed) — Bcrypt cost=12
- hash_pin(plain) / verify_pin(plain, hashed) — Bcrypt, constant-time
- validate_pin_format(pin) — exactly 4 digits
- create_access_token(user_id, is_admin=False) — JWT HS256
  If is_admin=True: expiry = ADMIN_JWT_EXPIRY_HOURS (2h), add scope="admin"
  If is_admin=False: expiry = JWT_EXPIRY_HOURS (24h), scope="user"
- create_refresh_token(user_id) — JWT HS256, 7d expiry
- decode_token(token) — returns payload or raises JWTError
- generate_totp(secret) — HMAC-SHA1 based 6-digit OTP
- hash_otp(otp) / verify_otp(plain, hashed) — Bcrypt
- generate_reference() — "EP-XXXXXXXX" format
- create_pending_tx_token(sender_id, recipient_id, amount) — 60s expiry JWT
- verify_pending_tx_token(token) — returns payload or raises if expired
- mask_account_number(account) — "****XXXX"

Create app/core/encryption.py:
- encrypt_sensitive(plaintext) — AES-256-GCM
- decrypt_sensitive(encrypted) — reverses above
- hash_fingerprint_data(data_dict) — SHA-256 of ridge characteristics

Create app/core/deepseek.py:
- call_deepseek_chat(prompt, json_mode=True)
- call_deepseek_vision(image_base64, prompt)
- call_deepseek_vision_two_images(img1_base64, img2_base64, prompt)
All wrapped in try/except raising AIServiceUnavailableError.
```

## B04 — DEPENDENCIES + MIDDLEWARE
```
Create app/core/dependencies.py:
- get_db() — async generator
- get_current_user() — HTTPBearer → decode JWT → fetch user → check active
- get_current_verified_user() — also checks is_verified
- get_current_admin() — checks is_superuser=True AND X-Admin-Key header matches ADMIN_SECRET_HEADER
- verify_transaction_pin(pin, current_user) — Bcrypt verify with lockout after 3 fails
- get_user_tier(user) — returns 0-4 based on verification flags

Create app/core/exceptions.py with ALL custom exceptions:
InsufficientBalanceError, WalletFrozenError, DailyLimitExceededError,
RecipientNotFoundError, OTPExpiredError, OTPInvalidError, PINInvalidError,
PINLockedError, AIServiceUnavailableError, BiometricTokenExpiredError,
CNICVerificationError, LivenessVerificationError, BusinessVerificationError,
AdminSelfActionError, AdminKeyInvalidError, FraudFlagNotFoundError,
ReversalError

Register in main.py:
- EasyPayException handler → error_response format
- HTTPException handler → error_response format
- Global Exception handler → INTERNAL_SERVER_ERROR
- Request ID middleware
- Request timing middleware
- CORS middleware
- SlowAPI limiter
- Security headers middleware (HSTS, X-Frame-Options, X-Content-Type-Options)
```

## B05 — ALL PYDANTIC SCHEMAS v3.0
```
Create all schema files:

app/schemas/auth.py: UserRegisterRequest, LoginRequest, OTPSendRequest,
  OTPVerifyRequest, PINSetRequest, PasswordResetRequest, TokenResponse, RefreshTokenRequest

app/schemas/users.py: UserResponse (NEVER include password_hash/pin_hash/cnic_encrypted),
  UserUpdateRequest, UserSearchResponse, VerificationStatusResponse

app/schemas/wallet.py: WalletResponse, WalletSummaryResponse

app/schemas/transactions.py: SendMoneyRequest, ExternalTransferRequest,
  TransactionResponse, TransactionHistoryRequest, TopUpRequest, BillPayRequest,
  BiometricConfirmRequest, PendingTransactionResponse

app/schemas/kyc.py: CNICUploadRequest, CNICExtractedData, LivenessVerifyRequest,
  LivenessResult, FingerprintDataRequest, FingerprintVerifyResponse

app/schemas/business.py: BusinessRegisterRequest, BusinessDocumentUploadRequest,
  BusinessVerificationStatus

app/schemas/notifications.py: NotificationResponse, UnreadCountResponse

app/schemas/admin.py:
  AdminUserListResponse, AdminUserDetailResponse,
  AdminBlockRequest(reason: str), AdminTierOverrideRequest(tier: int),
  AdminKYCActionRequest(reason: str, rejection_reasons: list),
  AdminTransactionListResponse, AdminFlagTransactionRequest(reason: str),
  AdminReversalRequest(reason: str),
  AdminBusinessActionRequest(reason: str, rejection_reasons: list),
  FraudFlagResponse, FraudResolveRequest(notes: str),
  AdminDashboardStats, AdminChartData,
  BroadcastRequest(title: str, body: str, segment: str)

All schemas: model_config = {"from_attributes": True}
```

## B06 — AUTH SERVICE + TWILIO OTP
```
Create app/services/auth_service.py:
1. register_user() — validate unique phone+CNIC → hash password → create User+Wallet
   → generate TOTP OTP → send via Twilio → return user
2. send_otp_twilio(phone, otp) — real Twilio SMS; log error but never crash if SMS fails
   SMS body: "Your EasyPay verification code is: {otp}\nValid 5 minutes. Never share this code."
3. verify_otp_and_activate() — find OTP → check expiry → verify hash → mark used → set is_verified=True
4. login_user() — find user → check locked → verify Bcrypt → issue JWT+refresh token
   If user.is_superuser: create_access_token(user_id, is_admin=True) → 2h expiry
   Else: create_access_token(user_id) → 24h expiry
   → log to login_audit → return tokens+user
5. logout_user() — revoke refresh token
6. refresh_access_token() — verify → rotate → return new pair
7. resend_otp() — rate check (3/hour) → new OTP → Twilio SMS
8. initiate_password_reset() → send OTP
9. complete_password_reset() → verify OTP → hash new password → revoke all refresh tokens
10. set_pin() → validate format → Bcrypt hash → save
11. seed_admin_user() — called in main.py startup:
    Check if ADMIN_PHONE exists → if not: create with is_superuser=True, is_verified=True
```

## B07 — AUTH API ROUTES
```
Create app/api/v1/auth.py:
POST /auth/register → 201
POST /auth/otp/verify → 200
POST /auth/login → 200 with TokenResponse + UserResponse
  Response includes is_superuser flag so Android knows to show Admin Panel button
POST /auth/logout → 200 (protected)
POST /auth/token/refresh → 200
POST /auth/otp/send → @limiter.limit("3/hour")
POST /auth/password/reset/initiate → 200
POST /auth/password/reset/complete → 200
POST /auth/pin/set → 200 (protected)
POST /auth/pin/verify → 200 (protected)
POST /users/fcm-token → update user.fcm_token (protected)
Register router in main.py under /api/v1
```

## B08 — WALLET + TRANSACTION SERVICE (ATOMIC)
```
Create app/services/wallet_service.py:
- get_wallet, get_wallet_summary
- check_transaction_allowed(wallet, amount, user_tier)
- reset_daily_limit_if_needed(wallet)

Create app/services/transaction_service.py:
send_money_internal(db, sender_id, recipient_identifier, amount, note, idempotency_key):
  MUST use async with db.begin():
    - Check idempotency key
    - Find recipient
    - Lock both wallets with .with_for_update()
    - Validate balance + daily limit + tier
    - Debit sender, credit recipient, update daily_spent
    - Create Transaction record (status=completed)
    - Create Notification for both parties (triggers FCM)
    - Call fraud_service.evaluate_transaction(db, transaction, sender) — AFTER completion
    - Return Transaction

If amount >= PKR 1000:
  Create pending_transaction_token (60s JWT) instead of immediate processing
  Return PendingTransactionResponse

confirm_biometric_transaction(db, user_id, pending_tx_token):
  - verify_pending_tx_token() → get sender/recipient/amount
  - Process the actual transfer atomically
  - Run fraud detection
  - Return receipt
```

## B09 — TRANSACTION ROUTES + TOPUP + BILLS
```
Create app/api/v1/transactions.py:
POST /transactions/send → check amount → if >= PKR 1000 return pending token else process
POST /transactions/confirm-biometric → complete pending transaction
POST /transactions/send-external → simulated JazzCash/bank transfer
GET  /transactions/history → paginated with filters
GET  /transactions/{id} → single transaction
POST /transactions/topup → detect network from NETWORK_PREFIXES → deduct → FCM → receipt
POST /transactions/bills/pay → simulate bill fetch → deduct → FCM → receipt
GET  /transactions/bills/categories → static list (no auth)
GET  /users/daily-limit-status → spent today vs limit (protected)

All financial routes require PIN verification via verify_transaction_pin dependency.
```

## B10 — KYC SERVICE (CNIC + LIVENESS + FINGERPRINT)
```
Create app/services/kyc_service.py:

upload_cnic_and_extract(db, user_id, front_base64, back_base64):
  1. Upload front + back to Cloudinary private: easypay/cnic/{user_id}/front|back
  2. Call DeepSeek Vision on front image with CNIC extraction prompt
  3. Validate extracted CNIC format (XXXXX-XXXXXXX-X regex)
  4. Encrypt CNIC with AES-256-GCM
  5. Update user: cnic_front_url, cnic_back_url, cnic_encrypted, cnic_verified=True
  6. Recalculate tier
  7. Send FCM: "CNIC Verified ✅ Proceed to face verification"
  8. Return CNICExtractedData

verify_liveness(db, user_id, selfie_base64):
  1. Get user's CNIC front URL from DB
  2. Download CNIC image from Cloudinary
  3. Call DeepSeek Vision TWO images (selfie + CNIC) with liveness prompt
  4. Parse: is_live_person, face_match_confidence, same_person
  5. If confidence >= 0.80 AND is_live_person AND same_person:
     Upload selfie to Cloudinary private: easypay/selfies/{user_id}/liveness
     Update user: biometric_verified=True, liveness_selfie_url=url
     Recalculate tier → send FCM: "Identity Verified ✅ Tier 2 unlocked"
  6. Return LivenessResult

Create app/services/fingerprint_service.py:

process_fingerprint_scan(db, user_id, fingerprint_data_list):
  1. Validate 8 fingers present
  2. Hash each finger's ridge data with SHA-256
  3. Call simulate_nadra_verisys(cnic, quality_score)
  4. Save all 8 FingerprintScan records to DB
  5. If matched: update user nadra_verified=True, nadra_verification_id
     Recalculate tier → send FCM: "NADRA Verified ✅ Tier 3 unlocked"
  6. Return verification result

simulate_nadra_verisys(cnic, quality_score):
  await asyncio.sleep(2.5)  # Realistic latency — mandatory
  if quality_score < 40: return failure
  return {matched: True, verification_id: "NADRA-VRY-XXXXXXXX", confidence: 0.97}
```

## B11 — KYC API ROUTES
```
Create app/api/v1/kyc.py:
POST /users/upload-cnic → Rate: 5/day → CNICUploadRequest → kyc_service
POST /users/verify-liveness → Rate: 3/day → LivenessVerifyRequest → kyc_service
POST /users/verify-fingerprint → Rate: 3/day → FingerprintDataRequest → fingerprint_service
GET  /users/verification-status → returns tier + all boolean flags + daily limits
```

## B12 — BUSINESS VERIFICATION SERVICE + ROUTES
```
Create app/services/business_service.py:

register_business(db, user_id, data):
  - Verify user is at least Tier 2 (cnic_verified + biometric_verified)
  - Create BusinessProfile (status=pending)
  - Update user.account_type = "business"

upload_business_document(db, user_id, document_type, image_base64):
  - Upload to Cloudinary private: easypay/business/{user_id}/{document_type}
  - Create BusinessDocument record

submit_for_ai_review(db, user_id):
  - Fetch all uploaded documents
  - For each: call DeepSeek Vision with business verification prompt
  - Store AI verdict in BusinessDocument records
  - Calculate average confidence
  - ALL valid + avg >= 0.85 → auto_approve() → send FCM "Business Approved ✅"
  - ALL valid + avg 0.60-0.84 → flag_manual_review() → send FCM "Under Review"
  - Any invalid → auto_reject() → send FCM "Rejected — reason listed"

Create app/api/v1/business.py:
POST /business/register → 201
POST /business/upload-documents → 200
POST /business/submit-for-review → Rate: 2/day → 200
GET  /business/status → 200
GET  /business/supported-documents → No auth → 200
POST /business/resubmit → 200
```

## B13 — NOTIFICATION SERVICE + FCM
```
Create app/services/fcm_service.py:
send_push_notification(device_token, title, body, data={}):
  Use google-auth to get FCM access token
  httpx POST to FCM v1 API
  NEVER crash parent operation if FCM fails — log error and continue

Update app/services/notification_service.py:
create_notification(db, user_id, title, body, type, data={}):
  1. Save to notifications table
  2. If user.fcm_token exists → call fcm_service.send_push_notification()

Create app/api/v1/notifications.py:
GET    /notifications?unread_only=false&page=1&per_page=20
PATCH  /notifications/{id}/read
POST   /notifications/mark-all-read
GET    /notifications/unread-count
DELETE /notifications/{id}
```

## B14 — AI SERVICE (DEEPSEEK CHAT + INSIGHTS)
```
Create app/services/ai_service.py using DeepSeek (not Gemini):

get_or_generate_insights(db, user_id):
  Check ai_insights cache → if valid return cached
  Otherwise: fetch last 90 days transactions → mask sensitive data →
  DeepSeek JSON prompt → parse → save with 7-day expiry

DeepSeek insights JSON response format:
  {health_score, health_label, top_categories, monthly_comparison,
   savings_tips, unusual_spending}

send_chat_message(db, user_id, message):
  Build context: first name + balance + last 30 transactions (type+amount+date only)
  Include last 10 chat messages as conversation history
  Call DeepSeek with conversation history
  Detect "ACTION:SEND_MONEY|amount:X|recipient:Y" pattern in response
  Return {type: "message"|"payment_action", content/amount/recipient}

Create app/api/v1/ai.py:
POST /ai/chat | GET /ai/chat/history | DELETE /ai/chat/history
GET  /ai/insights | GET /ai/insights/refresh | GET /ai/health-score
```

## B15 — USER PROFILE + BANK ACCOUNTS + QR
```
Create app/services/user_service.py:
- get_user_by_id, get_user_by_phone, search_users (max 10 results)
- update_profile(db, user_id, data)
- get_user_qr_data(db, user_id) → qrcode lib → base64 PNG
- toggle_biometric(db, user_id, enable, otp_code)
- deactivate_account(db, user_id, password, otp_code)

Create app/services/banking_service.py:
- get_user_bank_accounts, link_bank_account (requires OTP + max 3)
- unlink_bank_account, set_primary_account

Create app/api/v1/users.py + app/api/v1/banking.py with all CRUD routes.
```

## B16 — FRAUD DETECTION ENGINE (NEW v3)
```
Create app/services/fraud_service.py:

evaluate_transaction(db, transaction, sender):
  Run all 6 fraud rules against the completed transaction.
  For each triggered rule: create FraudFlag record.
  Update user.risk_score += severity_weight:
    Critical=30, High=20, Medium=10, Low=5
  Cap risk_score at 100.
  If risk_score > 80:
    Freeze wallet automatically
    Create admin notification: "Critical: User {phone} auto-flagged"
    Send FCM to admin device if fcm_token exists

FRAUD_RULES to implement:
1. HIGH_AMOUNT: transaction.amount > 80000 → Medium
2. HIGH_VELOCITY: count sends in last 10 min > 5 → High
3. FAILED_PINS: sender.login_attempts >= 3 → High
4. NEW_ACCOUNT_LARGE: account < 24hrs old AND amount > 10000 → Critical
5. ROUND_NUMBERS: last 5 txns all round numbers → Low
6. NIGHT_ACTIVITY: current PKR time between 02:00-05:00 → Low

create_fraud_flag(db, user_id, transaction_id, rule_triggered, severity, details):
  Insert into fraud_flags table
  Update transaction.is_flagged = True, flag_reason = rule_triggered

get_active_flags(db, severity_filter=None):
  Return sorted by severity DESC, created_at DESC
```

## B17 — ADMIN SERVICE (NEW v3)
```
Create app/services/admin_service.py:

get_dashboard_stats(db) → AdminDashboardStats:
  total_users, active_today, new_today, total_transactions_today,
  transaction_volume_today, pending_kyc_reviews, active_fraud_alerts,
  total_system_balance

get_chart_data(db, days=30) → List of {date, transaction_count, volume}

block_user(db, admin_id, target_user_id, reason):
  Set user.is_active=False, user.is_locked=True
  Freeze wallet: wallet.is_frozen=True
  Send FCM to user: "Your account has been suspended"
  Log to admin_actions: action_type="block_user", reason=reason
  If admin_id == target_user_id: raise AdminSelfActionError

unblock_user(db, admin_id, target_user_id, reason):
  Set user.is_active=True, user.is_locked=False
  Unfreeze wallet if no active Critical fraud flags
  Send FCM to user: "Your account has been reinstated"
  Log to admin_actions

delete_user(db, admin_id, target_user_id, reason):
  Soft delete: set user.is_active=False
  Log to admin_actions
  If admin_id == target_user_id: raise AdminSelfActionError

override_tier(db, admin_id, target_user_id, new_tier, reason):
  Set user.verification_tier = new_tier
  Update wallet.daily_limit from TIER_DAILY_LIMITS
  Log to admin_actions

reverse_transaction(db, admin_id, transaction_id, reason):
  MUST use async with db.begin():
    Fetch transaction with .with_for_update()
    Verify status == "completed" and not already reversed
    Credit sender wallet: balance += transaction.amount
    Update transaction.status = "reversed"
    Set transaction.reversed_by = admin_id, reversal_reason = reason
    Create Notification for sender: "PKR X refunded — reversed by admin"
    Log to admin_actions
```

## B18 — ADMIN KYC + BUSINESS REVIEW ROUTES (NEW v3)
```
Create app/api/v1/admin.py — Part 1: Users + KYC:

All routes require get_current_admin dependency (checks is_superuser + X-Admin-Key).

GET  /admin/users → paginated, filterable by tier/status/risk_score
GET  /admin/users/{id} → full profile including KYC flags and risk_score
POST /admin/users/{id}/block → AdminBlockRequest → admin_service.block_user()
POST /admin/users/{id}/unblock → admin_service.unblock_user()
DELETE /admin/users/{id} → admin_service.delete_user()
PATCH /admin/users/{id}/tier → AdminTierOverrideRequest → admin_service.override_tier()

GET  /admin/kyc/pending → users where (cnic_verified=True AND biometric_verified=True
     AND business_status IN ('pending','under_review')) OR any manual_review flagged
GET  /admin/kyc/{user_id}/documents → return signed Cloudinary URLs (60min expiry)
     for cnic_front_url, cnic_back_url, liveness_selfie_url, all business docs
POST /admin/kyc/{user_id}/approve → AdminKYCActionRequest → set cnic_verified=True
     → recalculate tier → send FCM to user → log admin action
POST /admin/kyc/{user_id}/reject → AdminKYCActionRequest → send FCM with reasons
     → log admin action
```

## B19 — ADMIN TRANSACTION + FRAUD + BUSINESS (NEW v3)
```
Continue app/api/v1/admin.py — Part 2:

GET  /admin/transactions → full list with filters (date range, amount range, type, flagged_only)
GET  /admin/transactions/flagged → only is_flagged=True
POST /admin/transactions/{id}/flag → AdminFlagTransactionRequest → set is_flagged=True
POST /admin/transactions/{id}/reverse → AdminReversalRequest → admin_service.reverse_transaction()
GET  /admin/transactions/stats → volume by day/week/month, total fees

GET  /admin/business/under-review → all business_profiles where status IN ('pending','under_review')
GET  /admin/business/{id} → full profile + all documents + AI verdicts
POST /admin/business/{id}/approve → set approved → update user tier → send FCM → log
POST /admin/business/{id}/reject → set rejected with reasons → send FCM → log

GET  /admin/fraud/alerts → all active fraud_flags sorted by severity DESC
POST /admin/fraud/{flag_id}/resolve → set status=resolved → log admin action
POST /admin/fraud/{flag_id}/escalate → set status=escalated → call admin_service.block_user()
     → log admin action with escalation details
```

## B20 — ADMIN DASHBOARD + BROADCAST (NEW v3)
```
Continue app/api/v1/admin.py — Part 3:

GET /admin/dashboard/stats → admin_service.get_dashboard_stats()
  Returns: AdminDashboardStats with all real-time metrics

GET /admin/dashboard/chart-data → admin_service.get_chart_data(days=30)
  Returns: list of {date, transaction_count, volume_pkr} for last 30 days

POST /admin/announcements/broadcast → BroadcastRequest:
  Rate: 5/hour
  Query users by segment:
    "all" → all active users with fcm_token
    "tier1_only" → verification_tier == 1
    "tier3_plus" → verification_tier >= 3
    "business_only" → account_type == "business"
  For each user: create_notification(type="admin") + fcm push
  Save to system_announcements with recipient_count
  Log to admin_actions
  Return {sent_to: N, segment: "all"}
```

## B21 — SECURITY HARDENING
```
Add to main.py startup:
- Verify DATABASE_URL reachable
- Verify SECRET_KEY length >= 32 chars
- Verify ENCRYPTION_KEY is valid Fernet key
- Verify ADMIN_SECRET_HEADER is set and >= 16 chars
- Verify DEEPSEEK_API_KEY is set
- Log startup summary of all configured services

Security headers middleware in main.py:
- Strict-Transport-Security: max-age=31536000
- X-Content-Type-Options: nosniff
- X-Frame-Options: DENY
- X-Admin-Key validation for all /admin/* routes

Logging rules in app/core/logging_config.py:
- NEVER log: password, password_hash, pin_hash, cnic, cnic_encrypted, api_key
- LOG every KYC decision with timestamp + user_id + confidence + outcome
- LOG every admin action with admin_id + action + target + reason
- LOG every fraud flag creation with rule_triggered + severity
```

## B22 — ERROR HANDLING + INTEGRATION TESTS
```
Finalize all exception handlers.
Add request ID to all log entries.
Create GET /health/detailed: tests DB + DeepSeek + Cloudinary + FCM connectivity.

Write integration tests:
- Full user journey: register → OTP → login → send money → fraud check
- KYC journey: CNIC upload → liveness → fingerprint → tier upgrade
- Admin journey: login → block user → view fraud alerts → reverse transaction
- Business journey: register → upload docs → AI review → admin approve

All 78+ test cases from Test Plan v3 must pass.
```

## B23 — RAILWAY DEPLOYMENT
```
Verify railway.json:
  startCommand: "alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port $PORT --workers 2"
  healthcheckPath: "/health"

Set all 20+ env variables in Railway dashboard.
Deploy → verify /health returns 200.
Set up UptimeRobot to ping /health every 14 minutes (prevents cold starts).
Document Railway URL for Android Retrofit BASE_URL.
```

---

# ══════════════════════════════════════════════════════
#               FRONTEND PROMPTS (F01–F21)
# ANDROID — Kotlin + Jetpack Compose + Material Design 3
# ══════════════════════════════════════════════════════

## ANDROID SETUP RULES (Read before F01)
```
- Kotlin 1.9+, Jetpack Compose 1.5+, minSdkVersion 26, targetSdkVersion 34
- Architecture: MVVM + Clean Architecture + Repository pattern
- DI: Hilt
- HTTP: Retrofit2 + OkHttp with AuthInterceptor + Certificate Pinning
- Navigation: Jetpack Navigation Compose (single activity)
- State: StateFlow + ViewModel
- Camera: CameraX + ML Kit
- Notifications: Firebase Messaging Service
- Material Design 3 throughout
- Base URL from BuildConfig.API_BASE_URL
- Admin routes: separate AdminRetrofitClient with X-Admin-Key header injected
```

## F01 — ANDROID PROJECT SETUP + DEPENDENCIES
```
Create Android project: EasyPay, package: com.easypay, minSdk 26.

Key dependencies in build.gradle:
- Compose UI + Material 3
- Navigation Compose
- Retrofit2 + OkHttp + logging interceptor
- Hilt
- ML Kit face-detection + barcode-scanning
- CameraX (camera2, lifecycle, view)
- Firebase messaging
- Coil image loading
- ZXing core (QR)
- Biometric
- Security crypto (EncryptedSharedPreferences)

Create RetrofitClient (user) with:
- BaseURL from BuildConfig.API_BASE_URL
- AuthInterceptor: reads JWT from EncryptedSharedPreferences
- Certificate pinning (debug: disabled, release: enabled)
- 30s timeout

Create AdminRetrofitClient with:
- Same base URL
- AdminInterceptor: injects both Authorization header AND X-Admin-Key header
- Reads admin key from BuildConfig.ADMIN_SECRET_KEY
```

## F02 — AUTH SCREENS (Login + Signup + OTP + PIN)
```
Create screens in ui/screens/auth/:

LoginScreen: Phone (+92 prefix), password, login button,
  biometric icon (visible only if biometric_enabled=true in prefs),
  "Create Account" link.
  On login success: check response.is_superuser → if true store admin flag in prefs
  Navigate to Dashboard normally (Admin Panel button will appear in profile)

SignupScreen: 3-step flow with progress indicator
  Step 1: Full name, phone, email
  Step 2: CNIC (XXXXX-XXXXXXX-X mask), password, confirm password
  Step 3: Summary + confirm

OTPScreen: 6-box OTP input, 5:00 countdown, resend button

PINSetupScreen: 4-dot PIN keypad, confirm PIN entry

Store JWT in EncryptedSharedPreferences on login.
Store is_superuser boolean in EncryptedSharedPreferences on login.
```

## F03 — HOME DASHBOARD SCREEN
```
Create ui/screens/dashboard/DashboardScreen.kt:

Layout:
1. Top bar: logo + "Hello, {name}" + bell icon with unread badge
2. Balance card: large PKR amount, eye toggle, gradient background
3. Quick actions: Send, Receive, Top-Up, Pay Bills
4. AI insight mini-card: health score + one tip
5. Recent transactions: last 5
6. KYC upgrade banner if tier < 3

Bottom navigation: Home, History, AI Chat, Profile
Pull-to-refresh supported.
```

## F04 — SEND MONEY SCREEN + BIOMETRIC CONFIRMATION
```
Create ui/screens/send/SendMoneyScreen.kt:

Step 1: Recipient (phone search OR QR scan)
Step 2: Amount (PKR prefix, note field, balance shown)
Step 3: Confirm (summary card + PIN bottom sheet)

If backend returns requires_biometric=true:
  Show 60-second countdown overlay
  Trigger BiometricPrompt immediately
  On success: POST /transactions/confirm-biometric with pending_tx_token
  On timeout: show "Confirmation window expired" → retry option

Step 4: Receipt with checkmark animation + share button
```

## F05 — RECEIVE MONEY + QR CODE SCREENS
```
ReceiveMoneyScreen: display base64 QR from GET /users/qr-code
  User name + phone + EasyPay ID below QR + share button
  "Scan to Pay" tab → CameraX QR scanner

QRScannerScreen: CameraX + ZXing
  Full screen camera preview + square overlay animation
  On scan: parse JSON → navigate to Send with pre-filled recipient
```

## F06 — TRANSACTION HISTORY + DETAIL
```
TransactionHistoryScreen:
  Paginated LazyColumn, load more on scroll
  Filter chips: All, Sent, Received, Top-Up, Bills
  Date range picker + search bar
  Flagged transactions show red warning badge (is_flagged=true from API)

TransactionDetailScreen: full receipt + reference (copyable) + share
```

## F07 — MOBILE TOP-UP + BILL PAYMENTS
```
TopUpScreen: number input, auto-detect network → show Jazz/Telenor/Zong badge
  Amount presets: PKR 50, 100, 200, 500, 1000 + custom
  PIN confirmation + receipt

BillPaymentScreen: category grid → sub-category → consumer number
  "Fetch Bill" → show simulated amount → PIN confirm → receipt
```

## F08 — NOTIFICATION CENTER
```
NotificationCenterScreen:
  Filter tabs: All | Unread | Transactions | Security | System | Admin
  Each item: icon by type, title (bold if unread), body preview, time
  Unread = colored left border. Tap = mark read. Swipe = delete.

Bell badge on Dashboard: shows unread count, updates on screen enter.

FirebaseMessagingService:
  onMessageReceived() → show system notification
  onNewToken() → POST /users/fcm-token
```

## F09 — KYC VERIFICATION HUB
```
KYCHubScreen: tier progress (1→4), current tier badge + daily limit
Verification steps list:
  Step 1 ✅ Phone Verified
  Step 2 → CNIC Upload (if not done)
  Step 3 → Face Verification (locked until Step 2)
  Step 4 → Fingerprint Scan (locked until Step 3)
  Step 5 → Business Verification (optional)

Benefit shown for each: "Complete Step 2 → unlock PKR 100,000/day"
```

## F10 — CNIC UPLOAD SCREEN
```
CNICUploadScreen:
  Two cards: CNIC Front | CNIC Back
  Tap → CameraX with document guide overlay
  Preview + "Retake" option
  POST /users/upload-cnic with both base64 images
  Loading: "Analyzing your CNIC..."
  Success: show extracted data (CNIC number, name, DOB)
  User confirms → back to KYC Hub
```

## F11 — LIVENESS DETECTION SCREEN
```
LivenessScreen:
  Full-screen front camera (CameraX)
  Oval face guide overlay
  Challenge text: "Please BLINK TWICE" (random each session)
  ML Kit FaceDetector: eyeOpenProbability < 0.2 on both eyes = blink
  blinkCount reaches 2 → capture frame → POST /users/verify-liveness
  Loading: "Verifying your identity..."
  Success: green checkmark → back to KYC Hub
  Failure: reason + "Try Again" (max 3 attempts)
```

## F12 — FINGERPRINT SCAN SCREEN + DATA POPUP
```
FingerprintScanScreen:
  Instruction screen: "We will scan all 8 fingers" + finger diagram
  For each of 8 fingers:
    Title: "Place your {finger_name} on the BACK camera"
    CameraX back camera + flash ON
    Finger-shaped overlay guide
    Auto-capture when uniform skin tone detected for 1 second
    "Captured ✓" → next finger
    Progress bar: "3 of 8 fingers scanned"

After all 8:
  Extract ridge characteristics via ML Kit edge detection
  Show FingerprintDataPopup (AlertDialog):
    Title: "🔍 Fingerprint Data Extracted"
    Table: Pattern Type, Ridge Count, Minutiae Points, Quality Score, Pattern Hash (first 16 chars)
    Privacy note: "No fingerprint image is stored or transmitted"
    [Cancel] [Send to NADRA VERISYS]

On confirm: POST /users/verify-fingerprint
Loading: "Connecting to NADRA VERISYS..." (2.5s minimum shown)
Second popup: NADRA response with reference number + confidence
Navigate to KYC Hub on success
```

## F13 — BUSINESS VERIFICATION SCREENS
```
BusinessRegistrationScreen: type cards + name + NTN + address
BusinessDocumentUploadScreen: required docs list + camera capture per doc
  "Submit for AI Verification" → POST /business/submit-for-review
  Loading: "AI is analyzing your documents..."
BusinessVerificationStatusScreen:
  Status badge (Pending/Under Review/Approved/Rejected)
  AI confidence score gauge
  Rejection reasons if rejected + resubmit button
```

## F14 — AI INSIGHTS + CHATBOT SCREENS
```
AIInsightsScreen:
  Health score gauge (0-100, animated, color coded)
  Spending categories pie chart (Canvas API)
  Month comparison bar chart
  Savings tips (expandable cards)
  Refresh button + last updated timestamp

AIChatbotScreen:
  Chat bubbles (user right, AI left)
  Quick suggestion chips on first open
  TextField + send button
  AnimatedDots loading indicator
  If payment_action returned: show confirmation dialog
```

## F15 — PROFILE + SETTINGS
```
ProfileScreen:
  Photo + name + phone + account type badge + tier badge
  Settings sections: Security, Account, Business, Support, Danger zone
  KYC upgrade card if tier < 4
  If is_superuser == true: show "Admin Panel" button → navigate to AdminDashboardScreen
  Admin button is INVISIBLE to non-admin users (conditional rendering based on stored pref)
```

## F16 — BANK ACCOUNTS SCREEN
```
BankAccountsScreen:
  List: bank logo + masked number + Primary badge
  FAB → AddBankAccountSheet: bank selector + account number + title + OTP verify
  Swipe to delete + tap to set primary
  Max 3 accounts enforced with message
```

## F17 — SHARED COMPONENTS LIBRARY
```
Create ui/components/:
EasyPayButton.kt: Primary, Secondary, Danger variants with loading state
EasyPayTextField.kt: Outlined with error + helper text
PINBottomSheet.kt: 4-dot indicator + numeric keypad + biometric option
BalanceCard.kt: gradient + mask toggle + shimmer loading
TransactionItem.kt: icon + name + amount + time + fraud badge
LoadingOverlay.kt: semi-transparent + spinner + message
ErrorSnackbar.kt: red snackbar with action
VerificationBadge.kt: tier badge with color coding
BiometricHelper.kt: BiometricPrompt wrapper
ConfirmDialog.kt: two-button dialog
AdminActionDialog.kt: reason input + confirm/cancel (NEW v3)
FraudBadge.kt: red "⚠ Flagged" chip (NEW v3)
```

## F18 — NAVIGATION + DEEP LINKS
```
AppNavGraph.kt:
Auth graph: Splash → Login → Signup → OTP → PINSetup
Main graph: Dashboard, Send, Receive, History, TopUp, Bills,
  Notifications, KYCHub, CNICUpload, Liveness, FingerprintScan,
  BusinessRegister, BusinessDocuments, BusinessStatus,
  AIInsights, AIChat, Profile, BankAccounts,
  AdminDashboard, AdminUsers, AdminKYC, AdminTransactions,
  AdminFraud, AdminBroadcast  ← NEW v3 admin screens

Deep link from FCM tap:
  transaction → TransactionDetail/{id}
  kyc → KYCHub
  business → BusinessStatus
  admin → AdminDashboard (only if is_superuser)
```

## F19 — ERROR HANDLING + OFFLINE STATE
```
All ViewModels:
  isLoading StateFlow<Boolean>
  errorMessage StateFlow<String?>
  Parse all API error responses → extract error.code + error.message

NetworkMonitor.kt: observe ConnectivityManager
No-internet banner on all screens when offline
Retry button on failed loads
Disable action buttons when offline

Error code → user message mapping:
  WALLET_INSUFFICIENT_BALANCE → "Insufficient balance. Add funds to continue."
  AUTH_ACCOUNT_LOCKED → "Account locked after multiple failed attempts."
  RATE_LIMIT_EXCEEDED → "Too many attempts. Please wait before trying again."
  AI_SERVICE_UNAVAILABLE → "AI features temporarily unavailable."
  BIOMETRIC_TOKEN_EXPIRED → "Confirmation window expired. Please retry."
  ADMIN_KEY_INVALID → "Admin session invalid. Please log in again."
  WALLET_FROZEN → "Your wallet has been frozen. Contact support."
```

## F20 — ADMIN ANDROID SCREENS (NEW v3)
```
Create all 6 admin screens in ui/screens/admin/:
All screens use AdminRetrofitClient (includes X-Admin-Key header automatically)

AdminDashboardScreen.kt:
  6 stat cards: Total Users, Today Transactions, System Volume,
    Pending KYC, Active Fraud Alerts, New Users Today
  LineChart: 30-day transaction volume (Recharts or MPAndroidChart)
  Quick action buttons to each admin screen

AdminUsersScreen.kt:
  Search bar + filter (tier, status, risk score)
  User rows: name, phone, tier badge, risk score bar, status
  Tap → AdminUserDetailSheet:
    All user fields + KYC flags + risk score
    [Block User] [Unblock] [Override Tier] buttons
    Each action → AdminActionDialog requiring reason text input

AdminKYCReviewScreen.kt:
  List of pending KYC cases with submission date + AI confidence
  Tap → AdminKYCDetailSheet:
    Display CNIC front/back images (loaded from signed URLs)
    Display liveness selfie
    Display business documents if any
    AI verdict for each document
    [Approve] [Reject] buttons with reason required for rejection

AdminTransactionScreen.kt:
  Full transaction list + "Show Flagged Only" toggle
  Flagged transactions: red FraudBadge chip
  Tap → AdminTransactionDetail:
    All transaction fields
    [Flag Transaction] [Reverse Transaction] buttons
    Reversal shows warning dialog: "This will credit PKR X back to sender"
    Mandatory reason field

AdminFraudAlertsScreen.kt:
  Active fraud flags sorted by severity (Critical → High → Medium → Low)
  Color-coded chips: Critical=red, High=orange, Medium=yellow, Low=gray
  Each alert: user phone + rule triggered + transaction amount + time
  [Resolve] → notes dialog
  [Escalate] → confirmation "This will block user and freeze wallet" + reason

AdminBroadcastScreen.kt:
  Title + body text inputs
  Segment selector: All Users | Tier 1 Only | Tier 3+ | Business Only
  Preview card showing how notification will look
  Estimated recipient count (from API)
  [Send Broadcast] → confirmation dialog
```

## F21 — FINAL INTEGRATION + APK BUILD
```
1. Replace all hardcoded URLs with BuildConfig.API_BASE_URL
   Debug: http://10.0.2.2:8000/api/v1
   Release: https://easypay-api.railway.app/api/v1

2. Add ADMIN_SECRET_KEY to BuildConfig (from local.properties, not committed)

3. Certificate pinning for release:
   Get SHA-256: openssl s_client -connect easypay-api.railway.app:443 ...
   Add to AdminRetrofitClient and RetrofitClient

4. Build release APK: ./gradlew assembleRelease

5. Demo checklist:
   [ ] Admin login → dashboard shows real stats
   [ ] User registration + real Twilio SMS
   [ ] Send money PKR 2000 → biometric confirm → receipt
   [ ] FCM notification on lock screen
   [ ] CNIC upload → extracted data shown
   [ ] Liveness blink detection
   [ ] Fingerprint scan → data popup → NADRA popup
   [ ] Admin reviews KYC → approves
   [ ] Admin fraud alerts visible
   [ ] Admin transaction reverse working
   [ ] Business verification AI flow
   [ ] AI chatbot responds
   [ ] All 25+ screens accessible
   [ ] Admin screens invisible to non-admin users
   [ ] Certificate pinning blocks wrong cert
```
