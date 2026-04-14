# EASYPAY v3.0 — COMPLETE DATABASE SCHEMA
# 16 tables total (13 from v2 + 3 admin tables)
# Source of truth for ALL SQLAlchemy models.

---

## CRITICAL RULES
- ALL PKs → UUID (gen_random_uuid())
- ALL money → DECIMAL(12,2) NEVER float
- ALL timestamps → TIMESTAMPTZ DEFAULT NOW()
- ALL CNIC values → AES-256-GCM encrypted in cnic_encrypted column
- Wallet balance → CHECK (balance >= 0.00)
- Fingerprint images → NEVER stored anywhere — SHA-256 hash only
- Admin actions → EVERY admin operation logged to admin_actions table

---

## COMPLETE SQL SCHEMA

```sql
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ══════════════════════════════════════════════
-- TABLE 1: USERS (v3.0 — all columns)
-- ══════════════════════════════════════════════
CREATE TABLE users (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    phone_number            VARCHAR(15) UNIQUE NOT NULL,
    email                   VARCHAR(255) UNIQUE NOT NULL,
    full_name               VARCHAR(100) NOT NULL,
    cnic_encrypted          TEXT,
    password_hash           VARCHAR(255) NOT NULL,
    pin_hash                VARCHAR(255),
    is_verified             BOOLEAN DEFAULT FALSE,
    is_active               BOOLEAN DEFAULT TRUE,
    is_locked               BOOLEAN DEFAULT FALSE,
    is_superuser            BOOLEAN DEFAULT FALSE,       -- NEW v3: admin flag
    login_attempts          INTEGER DEFAULT 0,
    biometric_enabled       BOOLEAN DEFAULT FALSE,
    profile_photo_url       TEXT,
    -- KYC columns (v2)
    fcm_token               TEXT,
    fcm_token_updated       TIMESTAMPTZ,
    cnic_front_url          TEXT,
    cnic_back_url           TEXT,
    cnic_verified           BOOLEAN DEFAULT FALSE,
    liveness_selfie_url     TEXT,
    biometric_verified      BOOLEAN DEFAULT FALSE,
    biometric_verified_at   TIMESTAMPTZ,
    fingerprint_verified    BOOLEAN DEFAULT FALSE,
    fingerprint_verified_at TIMESTAMPTZ,
    nadra_verified          BOOLEAN DEFAULT FALSE,
    nadra_verification_id   VARCHAR(50),
    account_type            VARCHAR(20) DEFAULT 'individual'
                                CHECK (account_type IN ('individual','business')),
    business_status         VARCHAR(20)
                                CHECK (business_status IN ('pending','under_review','approved','rejected')),
    verification_tier       INTEGER DEFAULT 1 CHECK (verification_tier BETWEEN 0 AND 4),
    daily_limit_override    DECIMAL(12,2),
    -- NEW v3: risk/fraud columns
    risk_score              INTEGER DEFAULT 0 CHECK (risk_score BETWEEN 0 AND 100),
    is_flagged              BOOLEAN DEFAULT FALSE,
    flag_reason             TEXT,
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    last_login_at           TIMESTAMPTZ
);
CREATE INDEX idx_users_phone ON users(phone_number);
CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_users_tier ON users(verification_tier);
CREATE INDEX idx_users_superuser ON users(is_superuser);
CREATE INDEX idx_users_risk ON users(risk_score DESC);

-- ══════════════════════════════════════════════
-- TABLE 2: WALLETS
-- ══════════════════════════════════════════════
CREATE TABLE wallets (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    balance         DECIMAL(12,2) NOT NULL DEFAULT 0.00 CHECK (balance >= 0.00),
    currency        VARCHAR(3) DEFAULT 'PKR',
    is_frozen       BOOLEAN DEFAULT FALSE,
    daily_limit     DECIMAL(12,2) DEFAULT 25000.00,
    daily_spent     DECIMAL(12,2) DEFAULT 0.00,
    limit_reset_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ══════════════════════════════════════════════
-- TABLE 3: TRANSACTIONS (v3 — flagging added)
-- ══════════════════════════════════════════════
CREATE TABLE transactions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    reference_number    VARCHAR(20) UNIQUE NOT NULL,
    sender_id           UUID REFERENCES users(id),
    recipient_id        UUID REFERENCES users(id),
    amount              DECIMAL(12,2) NOT NULL CHECK (amount > 0),
    fee                 DECIMAL(12,2) DEFAULT 0.00,
    type                VARCHAR(20) NOT NULL CHECK (type IN ('send','receive','topup','bill','bank_transfer','refund')),
    status              VARCHAR(20) NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending','completed','failed','reversed')),
    description         TEXT,
    external_ref        VARCHAR(100),
    metadata            JSONB DEFAULT '{}',
    idempotency_key     VARCHAR(100) UNIQUE,
    -- NEW v3: fraud flagging
    is_flagged          BOOLEAN DEFAULT FALSE,
    flag_reason         TEXT,
    flagged_by          UUID REFERENCES users(id),   -- admin user id
    flagged_at          TIMESTAMPTZ,
    reversed_by         UUID REFERENCES users(id),   -- admin user id
    reversal_reason     TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    completed_at        TIMESTAMPTZ
);
CREATE INDEX idx_txn_sender ON transactions(sender_id, created_at DESC);
CREATE INDEX idx_txn_recipient ON transactions(recipient_id, created_at DESC);
CREATE INDEX idx_txn_flagged ON transactions(is_flagged) WHERE is_flagged = TRUE;

-- ══════════════════════════════════════════════
-- TABLE 4: BANK ACCOUNTS
-- ══════════════════════════════════════════════
CREATE TABLE bank_accounts (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                 UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    bank_name               VARCHAR(50) NOT NULL,
    account_number_masked   VARCHAR(20) NOT NULL,
    account_title           VARCHAR(100) NOT NULL,
    is_primary              BOOLEAN DEFAULT FALSE,
    is_verified             BOOLEAN DEFAULT FALSE,
    created_at              TIMESTAMPTZ DEFAULT NOW()
);

-- ══════════════════════════════════════════════
-- TABLE 5: OTP CODES
-- ══════════════════════════════════════════════
CREATE TABLE otp_codes (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID REFERENCES users(id) ON DELETE CASCADE,
    code_hash   VARCHAR(255) NOT NULL,
    purpose     VARCHAR(30) NOT NULL CHECK (purpose IN
                    ('registration','password_reset','pin_change','bank_linking','security_change')),
    expires_at  TIMESTAMPTZ NOT NULL,
    is_used     BOOLEAN DEFAULT FALSE,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ══════════════════════════════════════════════
-- TABLE 6: REFRESH TOKENS
-- ══════════════════════════════════════════════
CREATE TABLE refresh_tokens (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash  VARCHAR(255) NOT NULL UNIQUE,
    expires_at  TIMESTAMPTZ NOT NULL,
    is_revoked  BOOLEAN DEFAULT FALSE,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ══════════════════════════════════════════════
-- TABLE 7: AI INSIGHTS
-- ══════════════════════════════════════════════
CREATE TABLE ai_insights (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    insight_data    JSONB NOT NULL DEFAULT '{}',
    health_score    INTEGER CHECK (health_score BETWEEN 0 AND 100),
    generated_at    TIMESTAMPTZ DEFAULT NOW(),
    expires_at      TIMESTAMPTZ DEFAULT NOW() + INTERVAL '7 days'
);

-- ══════════════════════════════════════════════
-- TABLE 8: CHAT SESSIONS
-- ══════════════════════════════════════════════
CREATE TABLE chat_sessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    messages        JSONB NOT NULL DEFAULT '[]',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    last_message_at TIMESTAMPTZ DEFAULT NOW()
);

-- ══════════════════════════════════════════════
-- TABLE 9: NOTIFICATIONS
-- ══════════════════════════════════════════════
CREATE TABLE notifications (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title       VARCHAR(100) NOT NULL,
    body        TEXT NOT NULL,
    type        VARCHAR(30) NOT NULL CHECK (type IN ('transaction','security','system','ai_insight','admin')),
    is_read     BOOLEAN DEFAULT FALSE,
    data        JSONB DEFAULT '{}',
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_notif_user ON notifications(user_id, is_read, created_at DESC);

-- ══════════════════════════════════════════════
-- TABLE 10: LOGIN AUDIT
-- ══════════════════════════════════════════════
CREATE TABLE login_audit (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID REFERENCES users(id),
    phone_number    VARCHAR(15),
    ip_address      VARCHAR(45),
    success         BOOLEAN NOT NULL,
    failure_reason  VARCHAR(50),
    attempted_at    TIMESTAMPTZ DEFAULT NOW()
);

-- ══════════════════════════════════════════════
-- TABLE 11: BUSINESS PROFILES
-- ══════════════════════════════════════════════
CREATE TABLE business_profiles (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    business_name       VARCHAR(200) NOT NULL,
    business_type       VARCHAR(30) NOT NULL CHECK (business_type IN ('sole_proprietor','registered_company')),
    ntn_number          VARCHAR(20),
    business_address    TEXT,
    verification_status VARCHAR(20) DEFAULT 'pending'
                            CHECK (verification_status IN ('pending','under_review','approved','rejected')),
    ai_confidence_score DECIMAL(4,3),
    rejection_reasons   JSONB DEFAULT '[]',
    submitted_at        TIMESTAMPTZ DEFAULT NOW(),
    reviewed_at         TIMESTAMPTZ,
    reviewed_by         UUID REFERENCES users(id)   -- admin user id
);

-- ══════════════════════════════════════════════
-- TABLE 12: BUSINESS DOCUMENTS
-- ══════════════════════════════════════════════
CREATE TABLE business_documents (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id      UUID NOT NULL REFERENCES business_profiles(id) ON DELETE CASCADE,
    document_type    VARCHAR(50) NOT NULL,
    cloudinary_url   TEXT NOT NULL,
    ai_verdict       JSONB,
    is_valid         BOOLEAN,
    confidence_score DECIMAL(4,3),
    uploaded_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_bizdoc_business ON business_documents(business_id);

-- ══════════════════════════════════════════════
-- TABLE 13: FINGERPRINT SCANS (hashes only)
-- ══════════════════════════════════════════════
CREATE TABLE fingerprint_scans (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    finger_position VARCHAR(20) NOT NULL,
    pattern_type    VARCHAR(20),
    ridge_count     INTEGER,
    minutiae_points INTEGER,
    quality_score   INTEGER CHECK (quality_score BETWEEN 0 AND 100),
    pattern_hash    VARCHAR(64) NOT NULL,   -- SHA-256 ONLY — never raw image
    verisys_ref     VARCHAR(50),
    scanned_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_fingerprint_user ON fingerprint_scans(user_id);

-- ══════════════════════════════════════════════
-- TABLE 14: ADMIN ACTIONS (NEW v3)
-- ══════════════════════════════════════════════
CREATE TABLE admin_actions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    admin_id        UUID NOT NULL REFERENCES users(id),
    action_type     VARCHAR(50) NOT NULL CHECK (action_type IN (
                        'block_user','unblock_user','delete_user','override_tier',
                        'approve_kyc','reject_kyc','flag_transaction',
                        'reverse_transaction','approve_business','reject_business',
                        'resolve_fraud','escalate_fraud','broadcast_notification')),
    target_user_id  UUID REFERENCES users(id),
    target_txn_id   UUID REFERENCES transactions(id),
    target_biz_id   UUID REFERENCES business_profiles(id),
    reason          TEXT NOT NULL,
    metadata        JSONB DEFAULT '{}',
    performed_at    TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_admin_actions_admin ON admin_actions(admin_id, performed_at DESC);
CREATE INDEX idx_admin_actions_target ON admin_actions(target_user_id);

-- ══════════════════════════════════════════════
-- TABLE 15: FRAUD FLAGS (NEW v3)
-- ══════════════════════════════════════════════
CREATE TABLE fraud_flags (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id),
    transaction_id  UUID REFERENCES transactions(id),
    rule_triggered  VARCHAR(100) NOT NULL,
    severity        VARCHAR(10) NOT NULL CHECK (severity IN ('Low','Medium','High','Critical')),
    details         JSONB DEFAULT '{}',
    status          VARCHAR(20) DEFAULT 'active'
                        CHECK (status IN ('active','resolved','escalated')),
    resolved_by     UUID REFERENCES users(id),
    resolved_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_fraud_active ON fraud_flags(status, severity, created_at DESC);
CREATE INDEX idx_fraud_user ON fraud_flags(user_id);

-- ══════════════════════════════════════════════
-- TABLE 16: SYSTEM ANNOUNCEMENTS (NEW v3)
-- ══════════════════════════════════════════════
CREATE TABLE system_announcements (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    admin_id        UUID NOT NULL REFERENCES users(id),
    title           VARCHAR(100) NOT NULL,
    body            TEXT NOT NULL,
    segment         VARCHAR(20) NOT NULL CHECK (segment IN
                        ('all','tier1_only','tier3_plus','business_only')),
    recipient_count INTEGER DEFAULT 0,
    sent_at         TIMESTAMPTZ DEFAULT NOW()
);
```

---

## TIER DAILY LIMITS
```python
TIER_DAILY_LIMITS = {
    0: Decimal("0.00"),
    1: Decimal("25000.00"),
    2: Decimal("100000.00"),
    3: Decimal("500000.00"),
    4: Decimal("2000000.00")
}
```

## FRAUD DETECTION RULES
| Rule | Threshold | Severity |
|---|---|---|
| High single transaction | > PKR 80,000 | Medium |
| Velocity — too many sends | > 5 transactions in 10 minutes | High |
| Multiple failed PINs | > 3 in 1 hour | High |
| New account large transfer | Account < 24hrs + amount > PKR 10,000 | Critical |
| Round number pattern | 5+ consecutive round-number transactions | Low |
| Night activity | Transaction 2AM–5AM PKR time | Low |

## SUPPORTED BANKS
HBL, UBL, MCB, Meezan Bank, Allied Bank, Bank Alfalah, Habib Metro, Faysal Bank, Standard Chartered, JS Bank

## NETWORK PREFIXES (Top-Up Auto-Detection)
Jazz: 0300-0309 | Telenor: 0340-0346 | Zong: 0310-0319 | Ufone: 0331-0336 | Warid: 0320-0323

## REQUIRED BUSINESS DOCUMENTS
sole_proprietor: NTN Certificate, Business Registration, Bank Statement (3mo), Shop Rent Agreement
registered_company: SECP Incorporation Certificate, NTN Certificate, Company Bank Statement, Memorandum of Association

## TWO FINGERPRINT SYSTEMS (Critical Distinction)
| System | Purpose | Hardware | When Used |
|---|---|---|---|
| KYC Scan (NADRA) | Identity verification | Back camera + flash | Once during Tier 3 upgrade |
| Biometric Login | Daily authentication | Internal hardware sensor (BiometricPrompt) | Every login + PKR 1000+ transactions |
