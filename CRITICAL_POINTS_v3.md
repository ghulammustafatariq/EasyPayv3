# EASYPAY v3.0 — CRITICAL POINTS & KNOWN PITFALLS
# 20 points total. Read before every single prompt.

---

## ⚠️ POINT 1 — SQLAlchemy 2.0 Async
Use: await db.execute(select(User).where(...))
NEVER: db.query(User).filter(...).first()

## ⚠️ POINT 2 — Database URL Prefix
Railway sets postgresql:// — replace with postgresql+asyncpg://
Do this in config.py async_database_url property.

## ⚠️ POINT 3 — Atomic Send Money
ALWAYS use async with db.begin() + .with_for_update() on both wallet rows.
Missing with_for_update() = double-spending vulnerability.

## ⚠️ POINT 4 — AES-256-GCM Key Management
ENCRYPTION_KEY must be exactly 32 bytes (256 bits) base64-encoded.
Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
If this key is lost, ALL encrypted CNIC values become unreadable. Back it up.

## ⚠️ POINT 5 — Cloudinary Private Upload
ALWAYS set type="private" for CNIC, selfie, and business documents.
NEVER use public. Documents would be accessible to anyone with the URL.
Admin viewing KYC documents uses get_signed_url() which expires after 60 minutes.

## ⚠️ POINT 6 — DeepSeek Vision Response Parsing
DeepSeek may return markdown-wrapped JSON:
```json { ... } ```
Always strip before parsing:
  text = response.replace("```json", "").replace("```", "").strip()
  data = json.loads(text)

## ⚠️ POINT 7 — FCM Service Account JSON
FCM_SERVICE_ACCOUNT_JSON must be the COMPLETE service account JSON from Firebase console.
Store as single-line JSON string in .env. Never split across multiple env variables.
In config.py: parse with json.loads(settings.FCM_SERVICE_ACCOUNT_JSON)

## ⚠️ POINT 8 — Biometric Pending Transaction Token
60-second expiry is intentionally short. Verify with verify_pending_tx_token()
which checks: type == "pending_transaction" AND not expired.
Money NEVER moves until token is verified AND biometric confirmed.

## ⚠️ POINT 9 — Fingerprint: SHA-256 Not Bcrypt
Fingerprint pattern data uses SHA-256 (not Bcrypt).
Reason: SHA-256 is deterministic — same input = same hash.
Bcrypt is random-salted — can't match the same fingerprint twice.
Use hashlib.sha256 for fingerprints. Use Bcrypt for passwords/PINs/OTPs only.

## ⚠️ POINT 10 — NADRA VERISYS Simulation Delay
Always include await asyncio.sleep(2.5) in simulate_nadra_verisys().
Without the delay the demo looks fake. 2.5 seconds feels like a real government API.

## ⚠️ POINT 11 — ML Kit on Android Emulator
ML Kit FaceDetector and CameraX work on physical devices.
On emulator: face detection may not work reliably.
Always demo liveness and fingerprint scan on a PHYSICAL Android device.

## ⚠️ POINT 12 — Certificate Pinning Breaking Development
Certificate pinning WILL break if Railway domain changes or SSL rotates.
Solution: disable pinning in debug builds only:
  if (BuildConfig.DEBUG) { /* no pinning */ } else { /* add pinning */ }

## ⚠️ POINT 13 — Twilio Free Tier — Verify Numbers
Twilio trial requires verifying recipient numbers in Twilio dashboard.
Before demo: add your number + instructor's number to verified list.
Without this, Twilio returns error 21608 and SMS is not sent.

## ⚠️ POINT 14 — Business Auto-Approve Threshold
0.85 is the minimum auto-approve confidence. Never lower in production.
For demo: test with clear, well-lit, real documents.
If failing: temporarily set to 0.60 ONLY for demo, document the change.

## ⚠️ POINT 15 — Verification Tier Not Updated Automatically
When kyc_service sets cnic_verified=True or biometric_verified=True,
MUST also call calculate_and_save_tier(db, user) to update verification_tier.
The tier is not auto-calculated — it must be explicitly set.

## ⚠️ POINT 16 — Railway Cold Starts Kill Your Demo
Free tier Railway apps sleep after 30 minutes of inactivity.
First request after sleep takes 20-30 seconds.
Fix: Set up UptimeRobot (free) to ping /health every 14 minutes.
OR send a test request 5 minutes before demo.

## ⚠️ POINT 17 — Admin Superuser Seeding (NEW v3)
The admin account is seeded in main.py startup lifespan function.
If the admin already exists, skip creation (check by phone number first).
NEVER create admin through the normal /auth/register endpoint.
Admin password must be Bcrypt hashed same as regular passwords.

## ⚠️ POINT 18 — Admin Audit Log Is Mandatory (NEW v3)
Every single admin action must log to admin_actions BEFORE the action completes.
If the action fails after logging, mark it as failed in metadata.
Without audit logs, your app fails fintech compliance review instantly.

## ⚠️ POINT 19 — Fraud Detection Must Not Block Transactions (NEW v3)
Fraud detection runs AFTER a transaction completes — it never blocks money movement.
It only creates fraud_flags records and increases risk_score on the user.
Admin then reviews flags and decides to escalate (block user) or resolve.
Exception: if risk_score > 80, auto-freeze wallet and send admin alert.

## ⚠️ POINT 20 — Two Fingerprint Systems Must Never Mix (NEW v3)
System 1 (KYC back camera NADRA scan) and System 2 (BiometricPrompt daily login)
are completely separate systems with different hardware and purposes.
Never call the NADRA fingerprint endpoint from the biometric login flow.
Never use the hardware sensor result to update nadra_verified flag.
They are independent. System 2 can only be enabled after System 1 completes.
