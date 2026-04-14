# EASYPAY v3.0 — QUICK REFERENCE CARD

---

## PROMPT EXECUTION ORDER

### BACKEND (Day 1 — B01 to B23)
B01 Scaffold | B02 DB Models (16 tables) | B03 Security + Encryption + DeepSeek
B04 Dependencies + Middleware | B05 All Schemas | B06 Auth + Twilio OTP
B07 Auth Routes | B08 Wallet + Transaction Service | B09 Transaction Routes
B10 KYC Service (CNIC + Liveness + Fingerprint) | B11 KYC Routes
B12 Business Verification | B13 Notifications + FCM | B14 AI (DeepSeek)
B15 Profile + Banking | B16 Fraud Detection Engine | B17 Admin Service + Routes
B18 Admin User + KYC Management | B19 Admin Transaction + Business
B20 Admin Dashboard + Broadcast | B21 Security Hardening
B22 Error Handling | B23 Railway Deploy + Tests

### FRONTEND (Day 2-3 — F01 to F21)
F01 Setup + Dependencies | F02 Auth Screens | F03 Dashboard | F04 Send Money
F05 Receive + QR | F06 History | F07 Top-Up + Bills | F08 Notification Center
F09 KYC Hub | F10 CNIC Upload | F11 Liveness Screen | F12 Fingerprint + Popup
F13 Business Screens | F14 AI Screens | F15 Profile | F16 Bank Accounts
F17 Shared Components | F18 Navigation | F19 Error Handling
F20 Admin Android Screens | F21 APK Build + Certificate Pinning

---

## KEY COMMANDS

```bash
# Start backend
uvicorn main:app --reload --port 8000

# Run tests
pytest tests/ -v --tb=short

# DB migration
alembic revision --autogenerate -m "description"
alembic upgrade head

# View all endpoints
open http://localhost:8000/docs

# Generate SECRET_KEY
python -c "import secrets; print(secrets.token_hex(32))"

# Generate ENCRYPTION_KEY
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Generate ADMIN_SECRET_HEADER
python -c "import secrets; print(secrets.token_hex(16))"

# Get Railway cert SHA-256
openssl s_client -connect easypay-api.railway.app:443 < /dev/null 2>/dev/null | openssl x509 -fingerprint -sha256 -noout
```

---

## ALL API ENDPOINTS v3.0 (53 total)

### Auth (10)
POST /api/v1/auth/register
POST /api/v1/auth/login
POST /api/v1/auth/logout
POST /api/v1/auth/otp/send
POST /api/v1/auth/otp/verify
POST /api/v1/auth/token/refresh
POST /api/v1/auth/pin/set
POST /api/v1/auth/pin/verify
POST /api/v1/auth/password/reset/initiate
POST /api/v1/auth/password/reset/complete

### Wallet + Transactions (10)
GET  /api/v1/wallet/balance
GET  /api/v1/wallet/summary
POST /api/v1/transactions/send
POST /api/v1/transactions/confirm-biometric
POST /api/v1/transactions/send-external
GET  /api/v1/transactions/history
GET  /api/v1/transactions/{id}
POST /api/v1/transactions/topup
POST /api/v1/transactions/bills/pay
GET  /api/v1/transactions/bills/categories

### KYC (4)
POST /api/v1/users/upload-cnic
POST /api/v1/users/verify-liveness
POST /api/v1/users/verify-fingerprint
GET  /api/v1/users/verification-status

### Business (6)
POST /api/v1/business/register
POST /api/v1/business/upload-documents
POST /api/v1/business/submit-for-review
GET  /api/v1/business/status
GET  /api/v1/business/supported-documents
POST /api/v1/business/resubmit

### Notifications (5)
POST /api/v1/users/fcm-token
GET  /api/v1/notifications
PATCH /api/v1/notifications/{id}/read
POST /api/v1/notifications/mark-all-read
GET  /api/v1/notifications/unread-count
DELETE /api/v1/notifications/{id}

### AI (6)
GET  /api/v1/ai/insights
GET  /api/v1/ai/insights/refresh
POST /api/v1/ai/chat
GET  /api/v1/ai/chat/history
DELETE /api/v1/ai/chat/history
GET  /api/v1/ai/health-score

### Users + Banking (8)
GET  /api/v1/users/profile
PATCH /api/v1/users/profile
GET  /api/v1/users/qr-code
GET  /api/v1/users/search
GET  /api/v1/users/daily-limit-status
GET  /api/v1/bank-accounts
POST /api/v1/bank-accounts/link
DELETE /api/v1/bank-accounts/{id}
PATCH /api/v1/bank-accounts/{id}/set-primary

### Admin — NEW v3 (17)
POST /api/v1/admin/auth/login
GET  /api/v1/admin/dashboard/stats
GET  /api/v1/admin/dashboard/chart-data
GET  /api/v1/admin/users
GET  /api/v1/admin/users/{id}
POST /api/v1/admin/users/{id}/block
POST /api/v1/admin/users/{id}/unblock
DELETE /api/v1/admin/users/{id}
PATCH /api/v1/admin/users/{id}/tier
GET  /api/v1/admin/kyc/pending
GET  /api/v1/admin/kyc/{user_id}/documents
POST /api/v1/admin/kyc/{user_id}/approve
POST /api/v1/admin/kyc/{user_id}/reject
GET  /api/v1/admin/transactions
GET  /api/v1/admin/transactions/flagged
POST /api/v1/admin/transactions/{id}/flag
POST /api/v1/admin/transactions/{id}/reverse
GET  /api/v1/admin/transactions/stats
GET  /api/v1/admin/business/under-review
GET  /api/v1/admin/business/{id}
POST /api/v1/admin/business/{id}/approve
POST /api/v1/admin/business/{id}/reject
GET  /api/v1/admin/fraud/alerts
POST /api/v1/admin/fraud/{flag_id}/resolve
POST /api/v1/admin/fraud/{flag_id}/escalate
POST /api/v1/admin/announcements/broadcast

### System (2)
GET /health
GET /health/detailed

---

## EXTERNAL SERVICE SETUP CHECKLIST

- [ ] Twilio: Sign up → verify demo numbers → copy SID + Auth Token + Phone Number
- [ ] Cloudinary: Sign up → copy Cloud Name + API Key + API Secret
- [ ] Firebase: Create project → Android app → google-services.json → Enable FCM → Service account JSON
- [ ] DeepSeek: platform.deepseek.com → API Keys → Create key
- [ ] Railway: Connect GitHub → PostgreSQL plugin → Set all env variables

---

## DEMO SCRIPT (12 minutes)

1. Admin login → show dashboard stats (30 sec)
2. Show fraud alerts panel (30 sec)
3. User registration + Twilio OTP to real phone (1 min)
4. Login + dashboard with balance (30 sec)
5. Send PKR 2,000 → biometric confirmation → receipt (1.5 min)
6. FCM notification on second device lock screen (30 sec)
7. Admin panel → see the transaction just made (30 sec)
8. CNIC upload → DeepSeek extracts data (1 min)
9. Liveness → blink challenge → face match result (1 min)
10. Fingerprint scan → data popup → NADRA VERISYS popup (2 min)
11. Business verification → AI confidence score (1 min)
12. Admin KYC review → approve a pending case (30 sec)
13. AI chatbot → spending question (30 sec)
Total: ~12 minutes

---

## ANDROID ADMIN SCREENS (6 screens — F20)
Screen 1: Admin Dashboard (stats cards + 30-day chart)
Screen 2: User Management (search + block/unblock/delete)
Screen 3: KYC Review (view docs + approve/reject)
Screen 4: Transaction Monitor (flag + reverse)
Screen 5: Fraud Alerts (resolve + escalate)
Screen 6: Broadcast Notification (segment selector + send)

Admin visibility: Profile screen shows "Admin Panel" button ONLY if user.is_superuser == true
