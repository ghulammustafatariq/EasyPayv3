"""
app/api/v1/router.py — EasyPay v3.0 Main v1 API Router

All feature routers are included here with their sub-prefixes.
The combined api_router is mounted at /api/v1 in main.py.

Routers are imported as they are created across prompts B07–B12.
Commented-out lines will be uncommented when the corresponding module is built.
"""
from fastapi import APIRouter

from app.api.v1 import admin, ai, auth, banking, business, cards, kyc, notifications, transactions, users, wallets
from app.api.v1 import zakat, trusted_circle, hissa
from app.api.v1 import external_payments
from app.api.v1 import payments

api_router = APIRouter(prefix="/api/v1")

# ── Auth (B07) ────────────────────────────────────────────────────────────────
api_router.include_router(auth.router)

# ── Users (B08) ───────────────────────────────────────────────────────────────
api_router.include_router(users.router)

# ── Wallets (B08) ─────────────────────────────────────────────────────────────
api_router.include_router(wallets.router)

# ── KYC & Biometrics (B11) ───────────────────────────────────────────────────
api_router.include_router(kyc.router)

# ── Transactions (B13) ───────────────────────────────────────────────────────
api_router.include_router(transactions.router)

# ── Business Verification (B12) ──────────────────────────────────────────────
api_router.include_router(business.router)

# ── Notifications (B13) ───────────────────────────────────────────────────────
api_router.include_router(notifications.router)

# ── AI Chat & Insights (B14) ──────────────────────────────────────────────────
api_router.include_router(ai.router)

# ── Banking / Bank Accounts (B15) ─────────────────────────────────────────────
api_router.include_router(banking.router)

# ── Admin (B18–B20) ───────────────────────────────────────────────────────────
api_router.include_router(admin.router)

# ── Cards (B24) ───────────────────────────────────────────────────────────────
api_router.include_router(cards.router)

# ── Zakat Calculator ──────────────────────────────────────────────────────────
api_router.include_router(zakat.router)

# ── Trusted Circle ────────────────────────────────────────────────────────────
api_router.include_router(trusted_circle.router)

# ── Hissa Collection ──────────────────────────────────────────────────────────
api_router.include_router(hissa.router)
# ── External Payments (wallets, IBFT, bills) ─────────────────────────────────
api_router.include_router(external_payments.router)

# ── Stripe Payments ───────────────────────────────────────────────────────────
api_router.include_router(payments.router)