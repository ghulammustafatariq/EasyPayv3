"""
app/core/config.py — EasyPay v3.0 Settings

Critical Points observed:
  Point 2 — async_database_url replaces postgresql:// → postgresql+asyncpg://
  Point 7 — FCM JSON parsed via json.loads()
"""
import json
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── Database ──────────────────────────────────────────────────────────────
    DATABASE_URL: str = ""

    # ── Security ──────────────────────────────────────────────────────────────
    SECRET_KEY: str = ""
    ENCRYPTION_KEY: str = ""
    ADMIN_SECRET_HEADER: str = ""

    # ── JWT ───────────────────────────────────────────────────────────────────
    JWT_EXPIRY_HOURS: int = 24
    ADMIN_JWT_EXPIRY_HOURS: int = 2       # Rule 17: admin JWT is 2 h, not 24 h
    REFRESH_TOKEN_EXPIRY_DAYS: int = 7

    # ── OTP & Pending Transactions ────────────────────────────────────────────
    OTP_EXPIRY_MINUTES: int = 5
    PENDING_TX_TOKEN_EXPIRY_SECONDS: int = 60   # Rule 12 / Point 8: 60-second window

    # ── Admin Seed ────────────────────────────────────────────────────────────
    ADMIN_PHONE: str = ""
    ADMIN_PASSWORD: str = ""
    ADMIN_EMAIL: str = ""

    # ── Twilio (legacy — kept for reference) ────────────────────────────────
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_PHONE_NUMBER: str = ""

    # ── Email / SMTP (OTP delivery) ───────────────────────────────────────────
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""          # Gmail App Password (16 chars, no spaces)
    SMTP_FROM_NAME: str = "EasyPay"

    # ── Resend (HTTPS-based email — preferred on Railway, replaces SMTP) ──────
    # Get free API key at https://resend.com → 3,000 emails/month free
    RESEND_API_KEY: str = ""
    RESEND_FROM_EMAIL: str = "onboarding@resend.dev"   # replace with your domain once verified

    # ── Cloudinary ────────────────────────────────────────────────────────────
    CLOUDINARY_CLOUD_NAME: str = ""
    CLOUDINARY_API_KEY: str = ""
    CLOUDINARY_API_SECRET: str = ""

    # ── Firebase FCM ──────────────────────────────────────────────────────────
    FCM_PROJECT_ID: str = ""
    FCM_SERVICE_ACCOUNT_JSON: str = "{}"   # single-line JSON string (Point 7)

    # ── DeepSeek AI ───────────────────────────────────────────────────────────
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"

    # ── OCR.space (CNIC text extraction) ─────────────────────────────────────
    OCR_SPACE_API_KEY: str = ""

    # ── Google Gemini (Vision OCR) ────────────────────────────────────────────
    GEMINI_API_KEY: str = ""

    # ── Face++ (Liveness / Face Compare) ─────────────────────────────────────
    FACEPLUSPLUS_API_KEY: str = ""
    FACEPLUSPLUS_API_SECRET: str = ""
    FACEPLUSPLUS_BASE_URL: str = "https://api-us.faceplusplus.com"

    # ── Business Verification Thresholds (Rule 14 / Point 14) ────────────────
    BUSINESS_AI_AUTO_APPROVE_THRESHOLD: float = 0.85
    BUSINESS_AI_MANUAL_REVIEW_THRESHOLD: float = 0.60

    # ── Fraud Detection Thresholds (Point 19 / v3) ───────────────────────────
    FRAUD_HIGH_TX_AMOUNT: float = 80000.0
    FRAUD_VELOCITY_COUNT: int = 5
    FRAUD_VELOCITY_MINUTES: int = 10
    FRAUD_NEW_ACCOUNT_HOURS: int = 24
    FRAUD_NEW_ACCOUNT_AMOUNT: float = 10000.0

    # ── App ───────────────────────────────────────────────────────────────────
    STRIPE_SECRET_KEY: str = ""
    STRIPE_PUBLISHABLE_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    ENVIRONMENT: str = "development"
    ALLOWED_ORIGINS: str = "*"

    # ── JazzCash (Mobile Wallet Top-Up) ───────────────────────────────────────
    JAZZCASH_MERCHANT_ID: str = ""
    JAZZCASH_PASSWORD: str = ""
    JAZZCASH_HASH_KEY: str = ""
    JAZZCASH_BASE_URL: str = "https://sandbox.jazzcash.com.pk/ApplicationAPI/API/2.0/Purchase/DoMWalletTransaction"

    # ── EasyPaisa (Mobile Wallet Top-Up) ─────────────────────────────────────
    EASYPAISA_STORE_ID: str = ""
    EASYPAISA_USERNAME: str = ""
    EASYPAISA_PASSWORD: str = ""
    EASYPAISA_HASH_KEY: str = ""
    EASYPAISA_BASE_URL: str = "https://easypay1.easypaisa.com.pk/tpg/v1/initiation"

    # ── Mock Networks ─────────────────────────────────────────────────────────
    JAZZCASH_URL: str = "http://localhost:9001"
    EASYPAISA_URL: str = "http://localhost:9002"

    # ─────────────────────────────────────────────────────────────────────────
    # CRITICAL POINT 2 — Railway sets postgresql://, asyncpg requires
    # postgresql+asyncpg://. Also handle Heroku-style postgres:// shorthand.
    # ─────────────────────────────────────────────────────────────────────────
    @property
    def async_database_url(self) -> str:
        url = self.DATABASE_URL
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url

    # CRITICAL POINT 7 — parse FCM JSON at runtime, never split across env vars
    @property
    def fcm_service_account(self) -> dict:
        return json.loads(self.FCM_SERVICE_ACCOUNT_JSON)

    model_config = {"env_file": ".env", "case_sensitive": True}


settings = Settings()
