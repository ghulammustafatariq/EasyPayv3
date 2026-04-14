"""
tests/conftest.py — Shared fixtures for EasyPay v3.0 integration tests.

Uses httpx.AsyncClient with ASGITransport against the real FastAPI app.
Tests run against a real PostgreSQL database (same DATABASE_URL from .env).
Each test cleans up after itself via direct DB queries where needed.
"""
import os
import pytest
import pytest_asyncio
import httpx

# ── Force test env overrides BEFORE importing main ────────────────────────────
# These satisfy B21 _validate_config() so the app starts without crashing.
os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-32-chars-long!!")
os.environ.setdefault(
    "ENCRYPTION_KEY", "MzI2NF9iaXRfZmVybmV0X2tleV9mb3JfdGVzdGluZz0="
)  # valid Fernet key placeholder; real one comes from .env
os.environ.setdefault("ADMIN_SECRET_HEADER", "test-admin-key-for-integration-tests!")
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-test-placeholder")

import main  # noqa: E402 — must come after env overrides

BASE_URL = "http://test"
ADMIN_KEY = os.environ["ADMIN_SECRET_HEADER"]


@pytest_asyncio.fixture(scope="function")
async def client():
    """Async HTTP client wired to the FastAPI app via ASGI transport."""
    transport = httpx.ASGITransport(app=main.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as c:
        yield c


# ── Phone number helpers to avoid collision between test runs ────────────────
import random
import string


def _rand_phone() -> str:
    """Return a random Pakistani-format phone number unlikely to exist."""
    suffix = "".join(random.choices(string.digits, k=7))
    return f"+923{random.choice('0134569')}{suffix}"


def _rand_email() -> str:
    suffix = "".join(random.choices(string.ascii_lowercase, k=8))
    return f"test_{suffix}@easypay.test"
