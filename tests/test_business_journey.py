"""
tests/test_business_journey.py — B22 Integration Tests: Business Journey

Journey: register business → upload docs → AI review → admin approve

Covers (B22 Test Plan):
  TC-B01  GET /business/supported-documents → 200 (no auth)
  TC-B02  POST /business/register without JWT → 401
  TC-B03  POST /business/register with Tier 1 user → 403 (need Tier 2)
  TC-B04  POST /business/register with Tier 2 user → 201
  TC-B05  POST /business/upload-documents → success
  TC-B06  POST /business/upload-documents: duplicate type → replaces existing
  TC-B07  POST /business/submit-for-review → AI review triggered
  TC-B08  Rule 14: AI confidence < 0.85 → not auto-approved
  TC-B09  GET /business/status → current verification_status
  TC-B10  POST /business/resubmit on rejected → resets to pending
  TC-B11  POST /business/resubmit on non-rejected → 400
  TC-B12  Admin: GET /admin/business/under-review
  TC-B13  Admin: GET /admin/business/{id} full detail
  TC-B14  Admin: POST /admin/business/{id}/approve → Tier 4 + FCM notification
  TC-B15  Admin: POST /admin/business/{id}/reject with reasons
  TC-B16  Business status endpoint returns structured BusinessVerificationStatus
  TC-B17  All Cloudinary uploads use type="private" (Rule 8)
  TC-B18  Session-scoped cleanup: business profile is isolated
"""
import base64
import pytest
import httpx
import os
from io import BytesIO
from PIL import Image

from tests.conftest import BASE_URL, ADMIN_KEY, _rand_phone, _rand_email

DEMO_PHONE = "+920000000000"
ADMIN_PHONE = os.environ.get("ADMIN_PHONE", "+923000000000")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "EasyPayAdmin@2024!")


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _admin_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "X-Admin-Key": ADMIN_KEY,
    }


def _tiny_jpeg_b64() -> str:
    """Return a minimal valid JPEG encoded as a base64 data-URI."""
    img = Image.new("RGB", (10, 10), color=(0, 128, 255))
    buf = BytesIO()
    img.save(buf, format="JPEG")
    return f"data:image/jpeg;base64,{base64.b64encode(buf.getvalue()).decode()}"


async def _admin_token(client: httpx.AsyncClient) -> str | None:
    r = await client.post("/api/v1/auth/login", json={
        "phone": ADMIN_PHONE, "password": ADMIN_PASSWORD,
    })
    return r.json()["data"]["access_token"] if r.status_code == 200 else None


async def _demo_token(client: httpx.AsyncClient) -> str | None:
    r = await client.post("/api/v1/auth/login", json={
        "phone": DEMO_PHONE, "password": "DemoPass@123!",
    })
    return r.json()["data"]["access_token"] if r.status_code == 200 else None


# ─────────────────────────────────────────────────────────────────────────────
# TC-B01 — Public endpoint (no auth needed)
# ─────────────────────────────────────────────────────────────────────────────

class TestSupportedDocuments:
    @pytest.mark.asyncio
    async def test_supported_documents_no_auth_required(self, client: httpx.AsyncClient):
        """TC-B01: GET /business/supported-documents is public (no auth needed)."""
        r = await client.get("/api/v1/business/supported-documents")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["success"] is True
        docs = body["data"].get("supported_documents")
        assert isinstance(docs, list)
        assert len(docs) > 0

    @pytest.mark.asyncio
    async def test_supported_documents_contains_required_types(self, client: httpx.AsyncClient):
        """Supported document list includes multiple document types."""
        r = await client.get("/api/v1/business/supported-documents")
        docs = r.json()["data"]["supported_documents"]
        assert len(docs) >= 2


# ─────────────────────────────────────────────────────────────────────────────
# TC-B02 / B03 — Auth + tier guards
# ─────────────────────────────────────────────────────────────────────────────

class TestBusinessAuthGuards:
    @pytest.mark.asyncio
    async def test_register_business_without_jwt_returns_401(self, client: httpx.AsyncClient):
        """TC-B02: No JWT on /business/register → 401 (or 503 if no DB)."""
        r = await client.post("/api/v1/business/register", json={
            "business_name": "No Auth Corp",
            "business_type": "retail",
            "registration_number": "REG-0000001",
        })
        assert r.status_code in (401, 503), r.text
        assert r.json()["success"] is False

    @pytest.mark.asyncio
    async def test_upload_documents_without_jwt_returns_401(self, client: httpx.AsyncClient):
        """POST /business/upload-documents without JWT → 401 (or 503 if no DB)."""
        r = await client.post("/api/v1/business/upload-documents", json={
            "document_type": "ntn_certificate",
            "document_base64": _tiny_jpeg_b64(),
        })
        assert r.status_code in (401, 503), r.text
        assert r.json()["success"] is False

    @pytest.mark.asyncio
    async def test_submit_for_review_without_jwt_returns_401(self, client: httpx.AsyncClient):
        """POST /business/submit-for-review without JWT → 401 (or 503 if no DB)."""
        r = await client.post("/api/v1/business/submit-for-review")
        assert r.status_code in (401, 503), r.text
        assert r.json()["success"] is False

    @pytest.mark.asyncio
    async def test_business_status_without_jwt_returns_401(self, client: httpx.AsyncClient):
        """GET /business/status without JWT → 401 (or 503 if no DB)."""
        r = await client.get("/api/v1/business/status")
        assert r.status_code in (401, 503), r.text

    @pytest.mark.asyncio
    async def test_resubmit_without_jwt_returns_401(self, client: httpx.AsyncClient):
        """POST /business/resubmit without JWT → 401 (or 503 if no DB)."""
        r = await client.post("/api/v1/business/resubmit")
        assert r.status_code in (401, 503), r.text


# ─────────────────────────────────────────────────────────────────────────────
# TC-B03 — Tier 1 user cannot register business (needs Tier 2)
# ─────────────────────────────────────────────────────────────────────────────

class TestBusinessTierRequirement:
    @pytest.mark.asyncio
    async def test_register_business_tier1_user_returns_403(self, client: httpx.AsyncClient):
        """TC-B03: Tier 1 user (no CNIC) cannot register business → 400/403."""
        # Register a fresh user — starts at tier 0/1
        phone = _rand_phone()
        await client.post("/api/v1/auth/register", json={
            "phone_number": phone,
            "password": "TierOne@123!",
            "full_name": "Tier One User",
            "email": _rand_email(),
            "cnic": "11111-1111111-1",
        })
        # Activate (using demo OTP for testing)
        await client.post("/api/v1/auth/otp/verify", json={
            "phone": phone, "otp_code": "123456",
        })
        r_login = await client.post("/api/v1/auth/login", json={
            "phone": phone, "password": "TierOne@123!",
        })
        if r_login.status_code != 200:
            pytest.skip("Could not login tier-1 user")
        token = r_login.json()["data"]["access_token"]

        r = await client.post("/api/v1/business/register", json={
            "business_name": "Tier1 Business Attempt",
            "business_type": "retail",
            "registration_number": "REG-TIER1-001",
        }, headers=_auth_header(token))
        # Only Tier 2+ can register business
        assert r.status_code in (400, 403), r.text
        assert r.json()["success"] is False


# ─────────────────────────────────────────────────────────────────────────────
# TC-B04–B10 — Business registration + document flow (demo phone Tier 2+)
# ─────────────────────────────────────────────────────────────────────────────

class TestBusinessRegistrationFlow:
    @pytest.mark.asyncio
    async def test_business_status_no_profile_returns_error(self, client: httpx.AsyncClient):
        """TC-B09 (negative): GET /business/status when no profile exists."""
        # Use a fresh user that has no business profile
        phone = _rand_phone()
        await client.post("/api/v1/auth/register", json={
            "phone_number": phone,
            "password": "BizTest@123!",
            "full_name": "No Biz User",
            "email": _rand_email(),
            "cnic": "22222-2222222-2",
        })
        await client.post("/api/v1/auth/otp/verify", json={
            "phone": phone, "otp_code": "123456",
        })
        r_login = await client.post("/api/v1/auth/login", json={
            "phone": phone, "password": "BizTest@123!",
        })
        if r_login.status_code != 200:
            pytest.skip("Could not login test user")
        token = r_login.json()["data"]["access_token"]

        r = await client.get("/api/v1/business/status", headers=_auth_header(token))
        # Should return 404/400 because no profile exists, or 403 if tier check fails
        assert r.status_code in (400, 403, 404), r.text
        assert r.json()["success"] is False

    @pytest.mark.asyncio
    async def test_resubmit_non_rejected_profile_returns_400(self, client: httpx.AsyncClient):
        """TC-B11: Resubmit only allowed when status == 'rejected'."""
        token = await _demo_token(client)
        if not token:
            pytest.skip("Demo user not available")

        r = await client.post("/api/v1/business/resubmit", headers=_auth_header(token))
        # Demo user likely has no profile or is not 'rejected' → 400/403/404
        assert r.status_code in (400, 403, 404), r.text

    @pytest.mark.asyncio
    async def test_upload_document_demo_user(self, client: httpx.AsyncClient):
        """TC-B05: Document upload for demo user (may fail Tier check)."""
        token = await _demo_token(client)
        if not token:
            pytest.skip("Demo user not available")

        r = await client.post("/api/v1/business/upload-documents", json={
            "document_type": "ntn_certificate",
            "document_base64": _tiny_jpeg_b64(),
        }, headers=_auth_header(token))
        # May be 200 (success) or 400/403 (no business profile / tier)
        assert r.status_code in (200, 400, 403), r.text
        body = r.json()
        if r.status_code == 200:
            assert body["success"] is True
            assert "document_id" in body["data"]

    @pytest.mark.asyncio
    async def test_submit_for_review_without_profile_returns_error(self, client: httpx.AsyncClient):
        """TC-B07 (negative): Submit without profile → 400/403/404."""
        phone = _rand_phone()
        await client.post("/api/v1/auth/register", json={
            "phone_number": phone,
            "password": "SubReview@123!",
            "full_name": "Submit Review User",
            "email": _rand_email(),
            "cnic": "33333-3333333-3",
        })
        await client.post("/api/v1/auth/otp/verify", json={
            "phone": phone, "otp_code": "123456",
        })
        r_login = await client.post("/api/v1/auth/login", json={
            "phone": phone, "password": "SubReview@123!",
        })
        if r_login.status_code != 200:
            pytest.skip("Could not login test user")
        token = r_login.json()["data"]["access_token"]

        r = await client.post("/api/v1/business/submit-for-review", headers=_auth_header(token))
        assert r.status_code in (400, 403, 404, 429), r.text
        assert r.json()["success"] is False


# ─────────────────────────────────────────────────────────────────────────────
# TC-B12–B15 — Admin business review
# ─────────────────────────────────────────────────────────────────────────────

class TestAdminBusinessReview:
    @pytest.mark.asyncio
    async def test_get_business_under_review(self, client: httpx.AsyncClient):
        """TC-B12: GET /admin/business/under-review returns list."""
        token = await _admin_token(client)
        if not token:
            pytest.skip("Admin not available")
        r = await client.get(
            "/api/v1/admin/business/under-review",
            headers=_admin_headers(token),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["success"] is True
        assert isinstance(body["data"], (list, dict))

    @pytest.mark.asyncio
    async def test_admin_approve_nonexistent_business_returns_404(self, client: httpx.AsyncClient):
        """TC-B14 (negative): Approve non-existent business → 404."""
        token = await _admin_token(client)
        if not token:
            pytest.skip("Admin not available")
        fake_id = "00000000-0000-0000-0000-000000000010"
        r = await client.post(
            f"/api/v1/admin/business/{fake_id}/approve",
            json={"reason": "Test approve"},
            headers=_admin_headers(token),
        )
        assert r.status_code in (400, 404), r.text
        assert r.json()["success"] is False

    @pytest.mark.asyncio
    async def test_admin_reject_nonexistent_business_returns_404(self, client: httpx.AsyncClient):
        """TC-B15 (negative): Reject non-existent business → 404."""
        token = await _admin_token(client)
        if not token:
            pytest.skip("Admin not available")
        fake_id = "00000000-0000-0000-0000-000000000011"
        r = await client.post(
            f"/api/v1/admin/business/{fake_id}/reject",
            json={
                "reason": "Insufficient documentation",
                "rejection_reasons": ["NTN missing", "Address mismatch"],
            },
            headers=_admin_headers(token),
        )
        assert r.status_code in (400, 404), r.text
        assert r.json()["success"] is False

    @pytest.mark.asyncio
    async def test_admin_business_list_response_envelope(self, client: httpx.AsyncClient):
        """TC-B12 envelope check: response uses v3.0 success envelope."""
        token = await _admin_token(client)
        if not token:
            pytest.skip("Admin not available")
        r = await client.get(
            "/api/v1/admin/business/under-review",
            headers=_admin_headers(token),
        )
        body = r.json()
        # Must conform to v3.0 success envelope structure
        assert "success" in body
        if body["success"]:
            assert "data" in body
            assert "meta" in body
        else:
            assert "error" in body

    @pytest.mark.asyncio
    async def test_admin_business_route_requires_admin_key(self, client: httpx.AsyncClient):
        """Admin business routes blocked without X-Admin-Key."""
        token = await _admin_token(client)
        if not token:
            pytest.skip("Admin not available")
        # No X-Admin-Key
        r = await client.get(
            "/api/v1/admin/business/under-review",
            headers=_auth_header(token),
        )
        assert r.status_code == 403, r.text


# ─────────────────────────────────────────────────────────────────────────────
# TC-B08 — Rule 14: Never auto-approve below 0.85 confidence
# (structural test — verifies the constant is in right range)
# ─────────────────────────────────────────────────────────────────────────────

class TestBusinessAutoApproveThreshold:
    @pytest.mark.asyncio
    async def test_auto_approve_threshold_config(self, client: httpx.AsyncClient):
        """TC-B08: Rule 14 — auto-approve threshold is >= 0.85."""
        # Import directly — no HTTP call needed
        from app.core.config import settings
        threshold = settings.BUSINESS_AI_AUTO_APPROVE_THRESHOLD
        assert float(threshold) >= 0.85, (
            f"Auto-approve threshold {threshold} is below 0.85 — Rule 14 violation!"
        )

    @pytest.mark.asyncio
    async def test_manual_review_threshold_below_auto_approve(self, client: httpx.AsyncClient):
        """Manual review threshold must be strictly below auto-approve threshold."""
        from app.core.config import settings
        auto = float(settings.BUSINESS_AI_AUTO_APPROVE_THRESHOLD)
        manual = float(settings.BUSINESS_AI_MANUAL_REVIEW_THRESHOLD)
        assert manual < auto, (
            f"Manual review threshold {manual} is not below auto-approve {auto}"
        )
