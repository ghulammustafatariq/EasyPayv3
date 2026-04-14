"""
app/models/database.py — EasyPay v3.0 SQLAlchemy Models (ALL 16 TABLES)

Source of truth: DATABASE_SCHEMA_v3.md

Critical Rules enforced:
  - ALL PKs        → UUID (uuid.uuid4 Python default + gen_random_uuid() server)
  - ALL money      → Numeric(12,2)   NEVER float
  - ALL timestamps → DateTime(timezone=True) server_default=func.now()
  - Wallet balance → CHECK (balance >= 0.00)
  - CNIC           → AES-256-GCM encrypted in cnic_encrypted column (Rule 7)
  - Fingerprint    → SHA-256 hash only in pattern_hash — never raw image (Point 9)
  - `metadata` SQL column is mapped to a distinct Python attribute name
    (`tx_metadata`, `action_metadata`) to avoid shadowing Base.metadata
"""
import uuid
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base

# Convenience alias — timezone-aware DateTime (generates TIMESTAMPTZ on PostgreSQL)
TZ = DateTime(timezone=True)


# ══════════════════════════════════════════════════════════════════════════════
# TABLE 1: USERS
# ══════════════════════════════════════════════════════════════════════════════
class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("account_type IN ('individual','business')", name="ck_users_account_type"),
        CheckConstraint(
            "business_status IN ('pending','under_review','approved','rejected')",
            name="ck_users_business_status",
        ),
        CheckConstraint("verification_tier BETWEEN 0 AND 4", name="ck_users_tier"),
        CheckConstraint("risk_score BETWEEN 0 AND 100", name="ck_users_risk_score"),
        Index("idx_users_phone", "phone_number"),
        Index("idx_users_email", "email"),
        Index("idx_users_tier", "verification_tier"),
        Index("idx_users_superuser", "is_superuser"),
        Index("idx_users_risk", "risk_score"),  # DESC handled by postgres planner
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    phone_number: Mapped[str] = mapped_column(String(15), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(100), nullable=False)
    cnic_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    pin_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)

    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)
    login_attempts: Mapped[int] = mapped_column(Integer, default=0)
    biometric_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    profile_photo_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # FCM
    fcm_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    fcm_token_updated: Mapped[object | None] = mapped_column(TZ, nullable=True)

    # KYC
    cnic_front_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    cnic_back_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    cnic_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    liveness_selfie_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    biometric_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    biometric_verified_at: Mapped[object | None] = mapped_column(TZ, nullable=True)
    fingerprint_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    fingerprint_verified_at: Mapped[object | None] = mapped_column(TZ, nullable=True)
    nadra_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    nadra_verification_id: Mapped[str | None] = mapped_column(String(50), nullable=True)

    account_type: Mapped[str] = mapped_column(String(20), default="individual")
    business_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    verification_tier: Mapped[int] = mapped_column(Integer, default=1)
    daily_limit_override: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)

    # v3 fraud columns
    risk_score: Mapped[int] = mapped_column(Integer, default=0)
    is_flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    flag_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[object] = mapped_column(TZ, server_default=func.now())
    last_login_at: Mapped[object | None] = mapped_column(TZ, nullable=True)

    @property
    def has_pin(self) -> bool:
        return self.pin_hash is not None

    # Relationships
    wallet: Mapped["Wallet"] = relationship("Wallet", back_populates="user", uselist=False)
    transactions_sent: Mapped[list["Transaction"]] = relationship(
        "Transaction", foreign_keys="Transaction.sender_id", back_populates="sender"
    )
    transactions_received: Mapped[list["Transaction"]] = relationship(
        "Transaction", foreign_keys="Transaction.recipient_id", back_populates="recipient"
    )
    notifications: Mapped[list["Notification"]] = relationship(
        "Notification", back_populates="user"
    )
    otp_codes: Mapped[list["OTPCode"]] = relationship("OTPCode", back_populates="user")
    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        "RefreshToken", back_populates="user"
    )
    bank_accounts: Mapped[list["BankAccount"]] = relationship(
        "BankAccount", back_populates="user"
    )
    ai_insight: Mapped["AIInsight | None"] = relationship(
        "AIInsight", back_populates="user", uselist=False
    )
    chat_sessions: Mapped[list["ChatSession"]] = relationship(
        "ChatSession", back_populates="user"
    )
    login_audits: Mapped[list["LoginAudit"]] = relationship(
        "LoginAudit", back_populates="user"
    )
    business_profile: Mapped["BusinessProfile | None"] = relationship(
        "BusinessProfile", foreign_keys="[BusinessProfile.user_id]", back_populates="user", uselist=False
    )
    fingerprint_scans: Mapped[list["FingerprintScan"]] = relationship(
        "FingerprintScan", back_populates="user"
    )
    fraud_flags: Mapped[list["FraudFlag"]] = relationship(
        "FraudFlag", foreign_keys="FraudFlag.user_id", back_populates="user"
    )
    admin_actions_performed: Mapped[list["AdminAction"]] = relationship(
        "AdminAction", foreign_keys="AdminAction.admin_id", back_populates="admin"
    )
    system_announcements: Mapped[list["SystemAnnouncement"]] = relationship(
        "SystemAnnouncement",
        foreign_keys="SystemAnnouncement.admin_id",
        back_populates="admin",
    )
    # B24 — Virtual / physical cards
    cards: Mapped[list["VirtualCard"]] = relationship(
        "VirtualCard", back_populates="user", cascade="all, delete-orphan"
    )
    # New feature modules
    zakat_calculations: Mapped[list["ZakatCalculation"]] = relationship(
        "ZakatCalculation", back_populates="user", lazy="selectin"
    )
    trusted_circle_settings: Mapped["TrustedCircleSettings | None"] = relationship(
        "TrustedCircleSettings", back_populates="user", uselist=False, lazy="selectin"
    )
    hissa_groups_created: Mapped[list["HissaGroup"]] = relationship(
        "HissaGroup", back_populates="creator"
    )


# ══════════════════════════════════════════════════════════════════════════════
# TABLE 2: WALLETS
# ══════════════════════════════════════════════════════════════════════════════
class Wallet(Base):
    __tablename__ = "wallets"
    __table_args__ = (
        CheckConstraint("balance >= 0.00", name="ck_wallets_balance_non_negative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    balance: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00")
    )
    currency: Mapped[str] = mapped_column(String(3), default="PKR")
    is_frozen: Mapped[bool] = mapped_column(Boolean, default=False)
    daily_limit: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("25000.00")
    )
    daily_spent: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0.00")
    )
    limit_reset_at: Mapped[object] = mapped_column(TZ, server_default=func.now())
    updated_at: Mapped[object] = mapped_column(
        TZ, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship("User", back_populates="wallet")


# ══════════════════════════════════════════════════════════════════════════════
# TABLE 3: TRANSACTIONS
# ══════════════════════════════════════════════════════════════════════════════
class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint(
            "type IN ('send','receive','topup','bill','bank_transfer','refund','card_payment','card_refund','zakat','external_transfer')",
            name="ck_txn_type",
        ),
        CheckConstraint(
            "status IN ('pending','completed','failed','reversed')",
            name="ck_txn_status",
        ),
        CheckConstraint("amount > 0", name="ck_txn_amount_positive"),
        Index("idx_txn_sender", "sender_id", "created_at"),
        Index("idx_txn_recipient", "recipient_id", "created_at"),
        Index("idx_txn_card", "card_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    reference_number: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    sender_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    recipient_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    fee: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Python attr renamed to avoid shadowing Base.metadata; DB column stays "metadata"
    tx_metadata: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    idempotency_key: Mapped[str | None] = mapped_column(
        String(100), unique=True, nullable=True
    )
    # v3 fraud flagging
    is_flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    flag_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    flagged_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    flagged_at: Mapped[object | None] = mapped_column(TZ, nullable=True)
    reversed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    reversal_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[object] = mapped_column(TZ, server_default=func.now())
    completed_at: Mapped[object | None] = mapped_column(TZ, nullable=True)

    # B24 — Card-linked transaction fields
    card_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("virtual_cards.id"), nullable=True
    )
    card_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)
    merchant_name: Mapped[str | None] = mapped_column(String(100), nullable=True)

    sender: Mapped["User | None"] = relationship(
        "User", foreign_keys=[sender_id], back_populates="transactions_sent"
    )
    recipient: Mapped["User | None"] = relationship(
        "User", foreign_keys=[recipient_id], back_populates="transactions_received"
    )
    fraud_flags: Mapped[list["FraudFlag"]] = relationship(
        "FraudFlag", back_populates="transaction"
    )


# ══════════════════════════════════════════════════════════════════════════════
# TABLE 4: BANK ACCOUNTS
# ══════════════════════════════════════════════════════════════════════════════
class BankAccount(Base):
    __tablename__ = "bank_accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    bank_name: Mapped[str] = mapped_column(String(50), nullable=False)
    account_number_masked: Mapped[str] = mapped_column(String(20), nullable=False)
    account_title: Mapped[str] = mapped_column(String(100), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[object] = mapped_column(TZ, server_default=func.now())

    user: Mapped["User"] = relationship("User", back_populates="bank_accounts")


# ══════════════════════════════════════════════════════════════════════════════
# TABLE 5: OTP CODES
# ══════════════════════════════════════════════════════════════════════════════
class OTPCode(Base):
    __tablename__ = "otp_codes"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('registration','password_reset','pin_change','bank_linking','security_change')",
            name="ck_otp_purpose",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    code_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    purpose: Mapped[str] = mapped_column(String(30), nullable=False)
    expires_at: Mapped[object] = mapped_column(TZ, nullable=False)
    is_used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[object] = mapped_column(TZ, server_default=func.now())

    user: Mapped["User | None"] = relationship("User", back_populates="otp_codes")


# ══════════════════════════════════════════════════════════════════════════════
# TABLE 6: REFRESH TOKENS
# ══════════════════════════════════════════════════════════════════════════════
class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    expires_at: Mapped[object] = mapped_column(TZ, nullable=False)
    is_revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[object] = mapped_column(TZ, server_default=func.now())

    user: Mapped["User"] = relationship("User", back_populates="refresh_tokens")



# ══════════════════════════════════════════════════════════════════════════════
# TABLE 7: AI INSIGHTS
# ══════════════════════════════════════════════════════════════════════════════
class AIInsight(Base):
    __tablename__ = "ai_insights"
    __table_args__ = (
        CheckConstraint(
            "health_score BETWEEN 0 AND 100", name="ck_ai_health_score"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    insight_data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    health_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    generated_at: Mapped[object] = mapped_column(TZ, server_default=func.now())
    expires_at: Mapped[object] = mapped_column(TZ, nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="ai_insight")


# ══════════════════════════════════════════════════════════════════════════════
# TABLE 8: CHAT SESSIONS
# ══════════════════════════════════════════════════════════════════════════════
class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    messages: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[object] = mapped_column(TZ, server_default=func.now())
    last_message_at: Mapped[object] = mapped_column(TZ, server_default=func.now())

    user: Mapped["User"] = relationship("User", back_populates="chat_sessions")


# ══════════════════════════════════════════════════════════════════════════════
# TABLE 9: NOTIFICATIONS
# ══════════════════════════════════════════════════════════════════════════════
class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint(
            "type IN ('transaction','security','system','ai_insight','admin')",
            name="ck_notif_type",
        ),
        Index("idx_notif_user", "user_id", "is_read", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(String(30), nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[object] = mapped_column(TZ, server_default=func.now())

    user: Mapped["User"] = relationship("User", back_populates="notifications")


# ══════════════════════════════════════════════════════════════════════════════
# TABLE 10: LOGIN AUDIT
# ══════════════════════════════════════════════════════════════════════════════
class LoginAudit(Base):
    __tablename__ = "login_audit"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    phone_number: Mapped[str | None] = mapped_column(String(15), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    attempted_at: Mapped[object] = mapped_column(TZ, server_default=func.now())

    user: Mapped["User | None"] = relationship("User", back_populates="login_audits")


# ══════════════════════════════════════════════════════════════════════════════
# TABLE 11: BUSINESS PROFILES
# ══════════════════════════════════════════════════════════════════════════════
class BusinessProfile(Base):
    __tablename__ = "business_profiles"
    __table_args__ = (
        CheckConstraint(
            "business_type IN ('sole_proprietor','registered_company')",
            name="ck_biz_type",
        ),
        CheckConstraint(
            "verification_status IN ('pending','under_review','approved','rejected')",
            name="ck_biz_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    business_name: Mapped[str] = mapped_column(String(200), nullable=False)
    business_type: Mapped[str] = mapped_column(String(30), nullable=False)
    ntn_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    business_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    verification_status: Mapped[str] = mapped_column(String(20), default="pending")
    ai_confidence_score: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 3), nullable=True
    )
    rejection_reasons: Mapped[list] = mapped_column(JSONB, default=list)
    submitted_at: Mapped[object] = mapped_column(TZ, server_default=func.now())
    reviewed_at: Mapped[object | None] = mapped_column(TZ, nullable=True)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )

    user: Mapped["User"] = relationship(
        "User", foreign_keys=[user_id], back_populates="business_profile"
    )
    documents: Mapped[list["BusinessDocument"]] = relationship(
        "BusinessDocument", back_populates="business"
    )


# ══════════════════════════════════════════════════════════════════════════════
# TABLE 12: BUSINESS DOCUMENTS
# ══════════════════════════════════════════════════════════════════════════════
class BusinessDocument(Base):
    __tablename__ = "business_documents"
    __table_args__ = (
        Index("idx_bizdoc_business", "business_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("business_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_type: Mapped[str] = mapped_column(String(50), nullable=False)
    cloudinary_url: Mapped[str] = mapped_column(Text, nullable=False)
    ai_verdict: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    is_valid: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    confidence_score: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 3), nullable=True
    )
    uploaded_at: Mapped[object] = mapped_column(TZ, server_default=func.now())

    business: Mapped["BusinessProfile"] = relationship(
        "BusinessProfile", back_populates="documents"
    )


# ══════════════════════════════════════════════════════════════════════════════
# TABLE 13: FINGERPRINT SCANS — SHA-256 hash only, never raw image (Point 9)
# ══════════════════════════════════════════════════════════════════════════════
class FingerprintScan(Base):
    __tablename__ = "fingerprint_scans"
    __table_args__ = (
        CheckConstraint(
            "quality_score BETWEEN 0 AND 100", name="ck_fp_quality"
        ),
        Index("idx_fingerprint_user", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    finger_position: Mapped[str] = mapped_column(String(20), nullable=False)
    pattern_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    ridge_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    minutiae_points: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quality_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pattern_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )  # SHA-256 only — NEVER store raw image
    verisys_ref: Mapped[str | None] = mapped_column(String(50), nullable=True)
    scanned_at: Mapped[object] = mapped_column(TZ, server_default=func.now())

    user: Mapped["User"] = relationship("User", back_populates="fingerprint_scans")


# ══════════════════════════════════════════════════════════════════════════════
# TABLE 14: ADMIN ACTIONS (v3) — every admin action MUST be logged (Rule 16)
# ══════════════════════════════════════════════════════════════════════════════
class AdminAction(Base):
    __tablename__ = "admin_actions"
    __table_args__ = (
        CheckConstraint(
            "action_type IN ("
            "'block_user','unblock_user','delete_user','override_tier',"
            "'approve_kyc','reject_kyc','flag_transaction',"
            "'reverse_transaction','approve_business','reject_business',"
            "'resolve_fraud','escalate_fraud','broadcast_notification')",
            name="ck_admin_action_type",
        ),
        Index("idx_admin_actions_admin", "admin_id", "performed_at"),
        Index("idx_admin_actions_target", "target_user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    admin_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    action_type: Mapped[str] = mapped_column(String(50), nullable=False)
    target_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    target_txn_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id"), nullable=True
    )
    target_biz_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("business_profiles.id"), nullable=True
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    # Python attr renamed to avoid shadowing Base.metadata; DB column stays "metadata"
    action_metadata: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    performed_at: Mapped[object] = mapped_column(TZ, server_default=func.now())

    admin: Mapped["User"] = relationship(
        "User", foreign_keys=[admin_id], back_populates="admin_actions_performed"
    )


# ══════════════════════════════════════════════════════════════════════════════
# TABLE 15: FRAUD FLAGS (v3) — created after transaction, NEVER blocks (Point 19)
# ══════════════════════════════════════════════════════════════════════════════
class FraudFlag(Base):
    __tablename__ = "fraud_flags"
    __table_args__ = (
        CheckConstraint(
            "severity IN ('Low','Medium','High','Critical')",
            name="ck_fraud_severity",
        ),
        CheckConstraint(
            "status IN ('active','resolved','escalated')",
            name="ck_fraud_status",
        ),
        Index("idx_fraud_active", "status", "severity", "created_at"),
        Index("idx_fraud_user", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id"), nullable=True
    )
    rule_triggered: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="active")
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    resolved_at: Mapped[object | None] = mapped_column(TZ, nullable=True)
    created_at: Mapped[object] = mapped_column(TZ, server_default=func.now())

    user: Mapped["User"] = relationship(
        "User", foreign_keys=[user_id], back_populates="fraud_flags"
    )
    transaction: Mapped["Transaction | None"] = relationship(
        "Transaction", back_populates="fraud_flags"
    )


# ══════════════════════════════════════════════════════════════════════════════
# TABLE 16: SYSTEM ANNOUNCEMENTS (NEW v3)
# Admin broadcasts pushed to segmented user groups
# ══════════════════════════════════════════════════════════════════════════════
class SystemAnnouncement(Base):
    __tablename__ = "system_announcements"
    __table_args__ = (
        CheckConstraint(
            "segment IN ('all','tier1_only','tier3_plus','business_only')",
            name="ck_announce_segment",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    admin_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    segment: Mapped[str] = mapped_column(String(20), nullable=False)
    recipient_count: Mapped[int] = mapped_column(Integer, default=0)
    sent_at: Mapped[object] = mapped_column(TZ, server_default=func.now())

    admin: Mapped["User"] = relationship(
        "User", foreign_keys=[admin_id], back_populates="system_announcements"
    )


# ══════════════════════════════════════════════════════════════════════════════
# TABLE 17: VIRTUAL CARDS  (B24 — Simulated Card System)
# Stores both virtual and physical EasyPay cards.
# Card numbers are AES-256-GCM encrypted; CVV is Bcrypt-hashed + one-time
# Fernet-encrypted for a single reveal window.
# ══════════════════════════════════════════════════════════════════════════════
class VirtualCard(Base):
    __tablename__ = "virtual_cards"
    __table_args__ = (
        CheckConstraint(
            "card_type IN ('virtual','physical')",
            name="ck_card_type",
        ),
        CheckConstraint(
            "status IN ('pending_activation','active','frozen','blocked','replaced','expired')",
            name="ck_card_status",
        ),
        CheckConstraint(
            "delivery_status IS NULL OR delivery_status IN ("
            "'processing','dispatched','out_for_delivery','delivered')",
            name="ck_card_delivery_status",
        ),
        Index("idx_cards_user", "user_id"),
        Index("idx_cards_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    wallet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wallets.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Card identity — NEVER store plaintext card number
    card_number_encrypted: Mapped[str] = mapped_column(Text, nullable=False)        # Fernet
    card_number_masked: Mapped[str] = mapped_column(String(19), nullable=False)     # "4276 **** **** 1234"
    card_holder_name: Mapped[str] = mapped_column(String(100), nullable=False)
    expiry_month: Mapped[int] = mapped_column(Integer, nullable=False)              # 1–12
    expiry_year: Mapped[int] = mapped_column(Integer, nullable=False)               # e.g. 2029

    # CVV: hashed for verification + encrypted for one-time reveal window
    cvv_hash: Mapped[str] = mapped_column(String(255), nullable=False)              # Bcrypt cost=12
    cvv_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)          # Fernet; deleted after first reveal

    # Card type and status
    card_type: Mapped[str] = mapped_column(String(20), nullable=False, default="virtual")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending_activation")

    # Spending controls
    daily_limit: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("50000.00"))
    monthly_limit: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("200000.00"))
    daily_spent: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    monthly_spent: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    limit_reset_at: Mapped[object] = mapped_column(TZ, server_default=func.now())

    # Toggle controls
    is_frozen: Mapped[bool] = mapped_column(Boolean, default=False)
    is_online_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_contactless_enabled: Mapped[bool] = mapped_column(Boolean, default=False)   # True for physical only

    # Card PIN (separate from wallet PIN) — set after issue
    card_pin_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Physical card delivery (null for virtual)
    delivery_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    delivery_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivery_tracking_id: Mapped[str | None] = mapped_column(String(50), nullable=True)  # "EPD-XXXXXXXX"
    estimated_delivery_at: Mapped[object | None] = mapped_column(TZ, nullable=True)
    dispatched_at: Mapped[object | None] = mapped_column(TZ, nullable=True)
    delivered_at: Mapped[object | None] = mapped_column(TZ, nullable=True)

    # Card lifecycle
    block_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("virtual_cards.id"), nullable=True
    )
    issued_at: Mapped[object] = mapped_column(TZ, server_default=func.now())
    activated_at: Mapped[object | None] = mapped_column(TZ, nullable=True)
    expires_at: Mapped[object] = mapped_column(TZ, nullable=False)                  # issued_at + 3 years

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="cards")
    wallet: Mapped["Wallet"] = relationship("Wallet")
    replaced_by: Mapped["VirtualCard | None"] = relationship(
        "VirtualCard",
        foreign_keys=[replaced_by_id],
        remote_side="VirtualCard.id",
    )


# ══════════════════════════════════════════════════════════════════════════════
# TABLE 18: ZAKAT CALCULATIONS
# ══════════════════════════════════════════════════════════════════════════════
class ZakatCalculation(Base):
    __tablename__ = "zakat_calculations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    # Asset inputs
    wallet_balance: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00")
    )
    cash_at_hand: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00")
    )
    gold_grams: Mapped[Decimal] = mapped_column(
        Numeric(10, 3), nullable=False, default=Decimal("0.000")
    )
    gold_rate_per_gram: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal("0.00")
    )
    silver_grams: Mapped[Decimal] = mapped_column(
        Numeric(10, 3), nullable=False, default=Decimal("0.000")
    )
    silver_rate_per_gram: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal("0.00")
    )
    business_inventory: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00")
    )
    receivables: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00")
    )
    debts: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00")
    )
    # Extended asset categories (Stocks, Crypto, Property, Other)
    stocks_value: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00")
    )
    crypto_value: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00")
    )
    property_value: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00")
    )
    other_assets: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00")
    )
    # Rate provenance: "live" or "manual"
    gold_rate_source: Mapped[str] = mapped_column(
        String(10), nullable=False, default="manual"
    )
    silver_rate_source: Mapped[str] = mapped_column(
        String(10), nullable=False, default="manual"
    )
    usd_pkr_rate: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 4), nullable=True
    )

    # Computed results (stored for history)
    nisab_threshold: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    total_wealth: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    zakat_due: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    is_zakat_applicable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Payment tracking
    paid_from_wallet: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    payment_txn_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transactions.id"), nullable=True
    )
    paid_at: Mapped[object | None] = mapped_column(TZ, nullable=True)

    created_at: Mapped[object] = mapped_column(TZ, server_default=func.now(), nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="zakat_calculations")


# ══════════════════════════════════════════════════════════════════════════════
# TABLE 19: TRUSTED CIRCLE SETTINGS
# ══════════════════════════════════════════════════════════════════════════════
class TrustedCircleSettings(Base):
    __tablename__ = "trusted_circle_settings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    require_pin_for_non_circle: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notify_on_non_circle: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    max_non_circle_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )

    created_at: Mapped[object] = mapped_column(TZ, server_default=func.now(), nullable=False)
    updated_at: Mapped[object | None] = mapped_column(TZ, onupdate=func.now(), nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="trusted_circle_settings")


# ══════════════════════════════════════════════════════════════════════════════
# TABLE 20: TRUSTED CIRCLE CONTACTS
# ══════════════════════════════════════════════════════════════════════════════
class TrustedCircleContact(Base):
    __tablename__ = "trusted_circle_contacts"
    __table_args__ = (
        UniqueConstraint("owner_id", "contact_id", name="uq_trusted_circle_contact"),
        Index("idx_trusted_circle_owner", "owner_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    added_at: Mapped[object] = mapped_column(TZ, server_default=func.now(), nullable=False)

    owner: Mapped["User"] = relationship("User", foreign_keys=[owner_id])
    contact: Mapped["User"] = relationship("User", foreign_keys=[contact_id])


# ══════════════════════════════════════════════════════════════════════════════
# TABLE 21: HISSA GROUPS
# ══════════════════════════════════════════════════════════════════════════════
class HissaGroup(Base):
    __tablename__ = "hissa_groups"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    emoji: Mapped[str] = mapped_column(String(10), nullable=False, default="🎉")
    creator_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    is_settled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    settled_at: Mapped[object | None] = mapped_column(TZ, nullable=True)
    created_at: Mapped[object] = mapped_column(TZ, server_default=func.now(), nullable=False)

    creator: Mapped["User"] = relationship("User", back_populates="hissa_groups_created")
    members: Mapped[list["HissaGroupMember"]] = relationship(
        "HissaGroupMember", back_populates="group", cascade="all, delete-orphan"
    )
    expenses: Mapped[list["HissaExpense"]] = relationship(
        "HissaExpense", back_populates="group", cascade="all, delete-orphan"
    )


# ══════════════════════════════════════════════════════════════════════════════
# TABLE 22: HISSA GROUP MEMBERS
# ══════════════════════════════════════════════════════════════════════════════
class HissaGroupMember(Base):
    __tablename__ = "hissa_group_members"
    __table_args__ = (
        UniqueConstraint("group_id", "user_id", name="uq_hissa_group_member"),
        Index("idx_hissa_member_group", "group_id"),
        Index("idx_hissa_member_user", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hissa_groups.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # positive = others owe this member; negative = this member owes others
    net_balance: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00")
    )
    joined_at: Mapped[object] = mapped_column(TZ, server_default=func.now(), nullable=False)

    group: Mapped["HissaGroup"] = relationship("HissaGroup", back_populates="members")
    user: Mapped["User"] = relationship("User")


# ══════════════════════════════════════════════════════════════════════════════
# TABLE 23: HISSA EXPENSES
# ══════════════════════════════════════════════════════════════════════════════
class HissaExpense(Base):
    __tablename__ = "hissa_expenses"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hissa_groups.id", ondelete="CASCADE"), nullable=False
    )
    paid_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    # equal | custom | percentage
    split_type: Mapped[str] = mapped_column(String(20), nullable=False, default="equal")
    # For custom/percentage: {"user_id": amount_or_pct, ...}
    split_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[object] = mapped_column(TZ, server_default=func.now(), nullable=False)

    group: Mapped["HissaGroup"] = relationship("HissaGroup", back_populates="expenses")
    paid_by: Mapped["User"] = relationship("User")
