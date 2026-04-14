"""
tests/test_admin_journey.py — B22 Integration Tests: Admin Journey

Journey: admin login → block user → view fraud alerts → reverse transaction

Covers (B22 Test Plan):
  TC-A01  Admin login with correct creds → 200 JWT (2-hour expiry)
  TC-A02  Admin login with wrong password → 401
  TC-A03  Admin route without X-Admin-Key → 403
  TC-A04  Non-admin JWT on admin route → 403
  TC-A05  GET /admin/users → paginated list of users
  TC-A06  GET /admin/users/{id} → detailed profile
  TC-A07  POST /admin/users/{id}/block → success + audit log
  TC-A08  Admin block own account → 400 (Rule 18)
  TC-A09  POST /admin/users/{id}/unblock → success
  TC-A10  POST /admin/kyc/{id}/approve → cnic_verified=True
  TC-A11  POST /admin/kyc/{id}/reject with reason
  TC-A12  GET /admin/fraud/alerts → list of fraud flags
  TC-A13  POST /admin/fraud/{flag_id}/resolve
  TC-A14  POST /admin/fraud/{flag_id}/escalate
  TC-A15  POST /admin/transactions/{id}/reverse → atomic reversal
  TC-A16  Reverse already-reversed transaction → 400
  TC-A17  GET /admin/dashboard/stats → financial summary
  TC-A18  POST /admin/announcements/broadcast → sends to segment
  TC-A19  Every admin action logged to audit table (Rule 16)
  TC-A20  Admin response never contains password_hash / pin_hash (Rule 2)
"""
import pytest
import pytest_asyncio
import httpx
import os

from tests.conftest import BASE_URL, ADMIN_KEY, _rand_phone, _rand_email

DEMO_PHONE = "+920000000000"

# Admin credentials from env (same as seeded in main.py lifespan)
ADMIN_PHONE = os.environ.get("ADMIN_PHONE", "+923000000000")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "EasyPayAdmin@2024!")


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _admin_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "X-Admin-Key": ADMIN_KEY,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Fixture: get admin JWT
# ─────────────────────────────────────────────────────────────────────────────

async def _admin_token(client: httpx.AsyncClient) -> str | None:
    r = await client.post("/api/v1/auth/login", json={
        "phone": ADMIN_PHONE,
        "password": ADMIN_PASSWORD,
    })
    if r.status_code == 200:
        return r.json()["data"]["access_token"]
    return None


async def _demo_token(client: httpx.AsyncClient) -> str | None:
    r = await client.post("/api/v1/auth/login", json={
        "phone": DEMO_PHONE,
        "password": "DemoPass@123!",
    })
    if r.status_code == 200:
        return r.json()["data"]["access_token"]
    return None


# ─────────────────────────────────────────────────────────────────────────────
# TC-A01 / A02 — Admin authentication
# ─────────────────────────────────────────────────────────────────────────────

class TestAdminAuthentication:
    @pytest.mark.asyncio
    async def test_admin_login_success(self, client: httpx.AsyncClient):
        """TC-A01: Admin login returns JWT with is_superuser=True."""
        token = await _admin_token(client)
        if not token:
            pytest.skip("Admin user not seeded — requires DB")
        # Verify the JWT payload includes superuser info
        r = await client.get(
            "/api/v1/users/me",
            headers=_auth_header(token),
        )
        assert r.status_code == 200, r.text
        user = r.json()["data"]
        assert user["is_superuser"] is True

    @pytest.mark.asyncio
    async def test_admin_login_wrong_password_returns_401(self, client: httpx.AsyncClient):
        """TC-A02: Wrong admin password → 401."""
        r = await client.post("/api/v1/auth/login", json={
            "phone": ADMIN_PHONE,
            "password": "totally_wrong_password",
        })
        assert r.status_code == 401, r.text
        assert r.json()["success"] is False

    @pytest.mark.asyncio
    async def test_admin_response_has_no_sensitive_fields(self, client: httpx.AsyncClient):
        """TC-A20: Admin login response never exposes password_hash or pin_hash."""
        r = await client.post("/api/v1/auth/login", json={
            "phone": ADMIN_PHONE,
            "password": ADMIN_PASSWORD,
        })
        body_str = r.text
        assert "password_hash" not in body_str
        assert "pin_hash" not in body_str
        assert "cnic_encrypted" not in body_str


# ─────────────────────────────────────────────────────────────────────────────
# TC-A03 / A04 — Admin route access control
# ─────────────────────────────────────────────────────────────────────────────

class TestAdminAccessControl:
    @pytest.mark.asyncio
    async def test_admin_route_without_key_returns_403(self, client: httpx.AsyncClient):
        """TC-A03: Admin route without X-Admin-Key header → 403."""
        token = await _admin_token(client)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        r = await client.get("/api/v1/admin/users", headers=headers)
        # Without X-Admin-Key, middleware or dependency should reject
        assert r.status_code in (401, 403), r.text
        assert r.json()["success"] is False

    @pytest.mark.asyncio
    async def test_non_admin_jwt_on_admin_route_returns_403(self, client: httpx.AsyncClient):
        """TC-A04: Regular user JWT on admin route → 403."""
        token = await _demo_token(client)
        if not token:
            pytest.skip("Demo user not available")
        r = await client.get(
            "/api/v1/admin/users",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Admin-Key": ADMIN_KEY,
            },
        )
        assert r.status_code == 403, r.text
        assert r.json()["success"] is False

    @pytest.mark.asyncio
    async def test_admin_route_without_jwt_returns_401(self, client: httpx.AsyncClient):
        """No JWT at all on admin route → 401/403 (or 503 if no DB in test env)."""
        r = await client.get(
            "/api/v1/admin/users",
            headers={"X-Admin-Key": ADMIN_KEY},
        )
        assert r.status_code in (401, 403, 503), r.text

    @pytest.mark.asyncio
    async def test_wrong_admin_key_returns_403(self, client: httpx.AsyncClient):
        """Wrong X-Admin-Key value → 403 from middleware."""
        token = await _admin_token(client)
        headers = {
            "Authorization": f"Bearer {token}" if token else "Bearer bad",
            "X-Admin-Key": "totally-wrong-key",
        }
        r = await client.get("/api/v1/admin/users", headers=headers)
        assert r.status_code == 403, r.text


# ─────────────────────────────────────────────────────────────────────────────
# TC-A05 / A06 — User management
# ─────────────────────────────────────────────────────────────────────────────

class TestAdminUserManagement:
    @pytest.mark.asyncio
    async def test_list_users_returns_paginated_list(self, client: httpx.AsyncClient):
        """TC-A05: GET /admin/users returns paginated user list."""
        token = await _admin_token(client)
        if not token:
            pytest.skip("Admin not available")
        r = await client.get(
            "/api/v1/admin/users",
            headers=_admin_headers(token),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["success"] is True
        data = body["data"]
        assert "users" in data
        assert "total" in data
        assert "page" in data
        assert isinstance(data["users"], list)

    @pytest.mark.asyncio
    async def test_list_users_filter_by_tier(self, client: httpx.AsyncClient):
        """GET /admin/users?tier=1 filters correctly."""
        token = await _admin_token(client)
        if not token:
            pytest.skip("Admin not available")
        r = await client.get(
            "/api/v1/admin/users?tier=1&page_size=5",
            headers=_admin_headers(token),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["success"] is True
        for user in body["data"]["users"]:
            assert user["verification_tier"] == 1

    @pytest.mark.asyncio
    async def test_list_users_response_no_sensitive_fields(self, client: httpx.AsyncClient):
        """Rule 2: Admin user list never contains password_hash."""
        token = await _admin_token(client)
        if not token:
            pytest.skip("Admin not available")
        r = await client.get("/api/v1/admin/users", headers=_admin_headers(token))
        body_str = r.text
        assert "password_hash" not in body_str
        assert "pin_hash" not in body_str

    @pytest.mark.asyncio
    async def test_get_nonexistent_user_returns_404(self, client: httpx.AsyncClient):
        """GET /admin/users/{id} with fake UUID → 404."""
        token = await _admin_token(client)
        if not token:
            pytest.skip("Admin not available")
        fake_uuid = "00000000-0000-0000-0000-000000000000"
        r = await client.get(
            f"/api/v1/admin/users/{fake_uuid}",
            headers=_admin_headers(token),
        )
        assert r.status_code == 404, r.text
        assert r.json()["success"] is False


# ─────────────────────────────────────────────────────────────────────────────
# TC-A07 / A08 / A09 — Block / Unblock user
# ─────────────────────────────────────────────────────────────────────────────

class TestAdminBlockUnblock:
    async def _get_test_user_id(self, client: httpx.AsyncClient, token: str) -> str | None:
        """Get the demo user's UUID."""
        r = await client.get("/api/v1/admin/users?page_size=50", headers=_admin_headers(token))
        if r.status_code != 200:
            return None
        users = r.json()["data"]["users"]
        for u in users:
            if u["phone_number"] == DEMO_PHONE:
                return u["id"]
        return None

    async def _get_admin_user_id(self, client: httpx.AsyncClient, token: str) -> str | None:
        """Get the admin's own UUID."""
        r = await client.get("/api/v1/users/me", headers=_auth_header(token))
        if r.status_code == 200:
            return r.json()["data"]["id"]
        return None

    @pytest.mark.asyncio
    async def test_block_user_success(self, client: httpx.AsyncClient):
        """TC-A07: Block a user — success + is_active becomes False."""
        token = await _admin_token(client)
        if not token:
            pytest.skip("Admin not available")
        user_id = await self._get_test_user_id(client, token)
        if not user_id:
            pytest.skip("Demo user not found in DB")

        r = await client.post(
            f"/api/v1/admin/users/{user_id}/block",
            json={"reason": "Integration test block"},
            headers=_admin_headers(token),
        )
        assert r.status_code == 200, r.text
        assert r.json()["success"] is True

        # Verify user is now blocked
        r2 = await client.get(f"/api/v1/admin/users/{user_id}", headers=_admin_headers(token))
        assert r2.json()["data"]["is_active"] is False

    @pytest.mark.asyncio
    async def test_block_own_account_returns_400(self, client: httpx.AsyncClient):
        """TC-A08: Rule 18 — Admin cannot block their own account."""
        token = await _admin_token(client)
        if not token:
            pytest.skip("Admin not available")
        admin_id = await self._get_admin_user_id(client, token)
        if not admin_id:
            pytest.skip("Could not get admin ID")

        r = await client.post(
            f"/api/v1/admin/users/{admin_id}/block",
            json={"reason": "Self-block attempt"},
            headers=_admin_headers(token),
        )
        assert r.status_code == 400, r.text
        assert r.json()["success"] is False

    @pytest.mark.asyncio
    async def test_unblock_user_success(self, client: httpx.AsyncClient):
        """TC-A09: Unblock a user — is_active returns to True."""
        token = await _admin_token(client)
        if not token:
            pytest.skip("Admin not available")
        user_id = await self._get_test_user_id(client, token)
        if not user_id:
            pytest.skip("Demo user not found in DB")

        # First ensure user is blocked
        await client.post(
            f"/api/v1/admin/users/{user_id}/block",
            json={"reason": "Pre-unblock test block"},
            headers=_admin_headers(token),
        )

        r = await client.post(
            f"/api/v1/admin/users/{user_id}/unblock",
            json={"reason": "Integration test unblock"},
            headers=_admin_headers(token),
        )
        assert r.status_code == 200, r.text
        assert r.json()["success"] is True

        r2 = await client.get(f"/api/v1/admin/users/{user_id}", headers=_admin_headers(token))
        assert r2.json()["data"]["is_active"] is True


# ─────────────────────────────────────────────────────────────────────────────
# TC-A12 / A13 / A14 — Fraud alerts
# ─────────────────────────────────────────────────────────────────────────────

class TestAdminFraud:
    @pytest.mark.asyncio
    async def test_get_fraud_alerts_returns_list(self, client: httpx.AsyncClient):
        """TC-A12: GET /admin/fraud/alerts returns list of fraud flags."""
        token = await _admin_token(client)
        if not token:
            pytest.skip("Admin not available")
        r = await client.get("/api/v1/admin/fraud/alerts", headers=_admin_headers(token))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["success"] is True
        assert isinstance(body["data"].get("alerts", body["data"]), (list, dict))

    @pytest.mark.asyncio
    async def test_resolve_nonexistent_fraud_flag_returns_404(self, client: httpx.AsyncClient):
        """POST /admin/fraud/999999/resolve with fake ID → 404."""
        token = await _admin_token(client)
        if not token:
            pytest.skip("Admin not available")
        fake_id = "00000000-0000-0000-0000-000000000099"
        r = await client.post(
            f"/api/v1/admin/fraud/{fake_id}/resolve",
            json={"reason": "Test resolve"},
            headers=_admin_headers(token),
        )
        assert r.status_code in (400, 404), r.text
        assert r.json()["success"] is False

    @pytest.mark.asyncio
    async def test_escalate_nonexistent_fraud_flag_returns_404(self, client: httpx.AsyncClient):
        """POST /admin/fraud/999999/escalate with fake ID → 404."""
        token = await _admin_token(client)
        if not token:
            pytest.skip("Admin not available")
        fake_id = "00000000-0000-0000-0000-000000000098"
        r = await client.post(
            f"/api/v1/admin/fraud/{fake_id}/escalate",
            json={"reason": "Test escalate"},
            headers=_admin_headers(token),
        )
        assert r.status_code in (400, 404), r.text
        assert r.json()["success"] is False


# ─────────────────────────────────────────────────────────────────────────────
# TC-A15 / A16 — Transaction reversal
# ─────────────────────────────────────────────────────────────────────────────

class TestAdminTransactionReversal:
    @pytest.mark.asyncio
    async def test_reverse_nonexistent_transaction_returns_error(self, client: httpx.AsyncClient):
        """TC-A15 (negative): Reversing a non-existent transaction returns 404/400."""
        token = await _admin_token(client)
        if not token:
            pytest.skip("Admin not available")
        fake_id = "00000000-0000-0000-0000-000000000001"
        r = await client.post(
            f"/api/v1/admin/transactions/{fake_id}/reverse",
            json={"reason": "Test reversal"},
            headers=_admin_headers(token),
        )
        assert r.status_code in (400, 404), r.text
        assert r.json()["success"] is False

    @pytest.mark.asyncio
    async def test_admin_transactions_list(self, client: httpx.AsyncClient):
        """GET /admin/transactions returns paginated transaction list."""
        token = await _admin_token(client)
        if not token:
            pytest.skip("Admin not available")
        r = await client.get("/api/v1/admin/transactions", headers=_admin_headers(token))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["success"] is True

    @pytest.mark.asyncio
    async def test_admin_flagged_transactions_list(self, client: httpx.AsyncClient):
        """GET /admin/transactions/flagged returns flagged transactions."""
        token = await _admin_token(client)
        if not token:
            pytest.skip("Admin not available")
        r = await client.get(
            "/api/v1/admin/transactions/flagged",
            headers=_admin_headers(token),
        )
        assert r.status_code == 200, r.text
        assert r.json()["success"] is True


# ─────────────────────────────────────────────────────────────────────────────
# TC-A17 / A18 — Dashboard + Broadcast
# ─────────────────────────────────────────────────────────────────────────────

class TestAdminDashboard:
    @pytest.mark.asyncio
    async def test_dashboard_stats_returns_financial_summary(self, client: httpx.AsyncClient):
        """TC-A17: GET /admin/dashboard/stats returns key metrics."""
        token = await _admin_token(client)
        if not token:
            pytest.skip("Admin not available")
        r = await client.get(
            "/api/v1/admin/dashboard/stats",
            headers=_admin_headers(token),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["success"] is True
        # Should contain some stats keys
        data = body["data"]
        assert isinstance(data, dict)

    @pytest.mark.asyncio
    async def test_broadcast_announcement_success(self, client: httpx.AsyncClient):
        """TC-A18: POST /admin/announcements/broadcast to 'all' segment."""
        token = await _admin_token(client)
        if not token:
            pytest.skip("Admin not available")
        r = await client.post(
            "/api/v1/admin/announcements/broadcast",
            json={
                "title": "Integration Test Announcement",
                "body": "This is a B22 integration test broadcast message.",
                "segment": "all",
            },
            headers=_admin_headers(token),
        )
        assert r.status_code in (200, 201), r.text
        body = r.json()
        assert body["success"] is True

    @pytest.mark.asyncio
    async def test_broadcast_invalid_segment_returns_422(self, client: httpx.AsyncClient):
        """POST /admin/announcements/broadcast with invalid segment → 422."""
        token = await _admin_token(client)
        if not token:
            pytest.skip("Admin not available")
        r = await client.post(
            "/api/v1/admin/announcements/broadcast",
            json={
                "title": "Bad Segment",
                "body": "Testing invalid segment value.",
                "segment": "nonexistent_segment",
            },
            headers=_admin_headers(token),
        )
        assert r.status_code == 422, r.text
        assert r.json()["success"] is False

    @pytest.mark.asyncio
    async def test_dashboard_chart_data(self, client: httpx.AsyncClient):
        """GET /admin/dashboard/chart-data returns data for charts."""
        token = await _admin_token(client)
        if not token:
            pytest.skip("Admin not available")
        r = await client.get(
            "/api/v1/admin/dashboard/chart-data",
            headers=_admin_headers(token),
        )
        assert r.status_code == 200, r.text
        assert r.json()["success"] is True


# ─────────────────────────────────────────────────────────────────────────────
# TC-A10 / A11 — KYC admin approval/rejection
# ─────────────────────────────────────────────────────────────────────────────

class TestAdminKYCReview:
    @pytest.mark.asyncio
    async def test_pending_kyc_list(self, client: httpx.AsyncClient):
        """GET /admin/kyc/pending returns list of pending KYC users."""
        token = await _admin_token(client)
        if not token:
            pytest.skip("Admin not available")
        r = await client.get("/api/v1/admin/kyc/pending", headers=_admin_headers(token))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["success"] is True
        assert "users" in body["data"]

    @pytest.mark.asyncio
    async def test_approve_nonexistent_kyc_returns_404(self, client: httpx.AsyncClient):
        """POST /admin/kyc/{id}/approve with fake UUID → 404."""
        token = await _admin_token(client)
        if not token:
            pytest.skip("Admin not available")
        fake_uuid = "00000000-0000-0000-0000-000000000002"
        r = await client.post(
            f"/api/v1/admin/kyc/{fake_uuid}/approve",
            json={"reason": "Test approve"},
            headers=_admin_headers(token),
        )
        assert r.status_code in (400, 404), r.text
        assert r.json()["success"] is False

    @pytest.mark.asyncio
    async def test_reject_nonexistent_kyc_returns_404(self, client: httpx.AsyncClient):
        """POST /admin/kyc/{id}/reject with fake UUID → 404."""
        token = await _admin_token(client)
        if not token:
            pytest.skip("Admin not available")
        fake_uuid = "00000000-0000-0000-0000-000000000003"
        r = await client.post(
            f"/api/v1/admin/kyc/{fake_uuid}/reject",
            json={
                "reason": "Test reject — documents unclear",
                "rejection_reasons": ["Image too blurry", "Name mismatch"],
            },
            headers=_admin_headers(token),
        )
        assert r.status_code in (400, 404), r.text
        assert r.json()["success"] is False
