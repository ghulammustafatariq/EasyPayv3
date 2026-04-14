"""
app/services/fingerprint_service.py — EasyPay v3.0 Fingerprint + NADRA Service

Rules Enforced:
  Rule  4 / Point 9 — NEVER store raw fingerprint data.
                       Each finger's feature dict is SHA-256 hashed (deterministic).
                       Bcrypt is NOT used — SHA-256 allows re-matching same input.
  Point 10          — simulate_nadra_verisys() MUST await asyncio.sleep(2.5).
                       Without the delay the demo looks fake.
  Point 15          — After verification, calculate_and_save_tier() is called
                       explicitly. Tier is NEVER auto-calculated.

This module is intentionally separate from kyc_service.py (B10 spec requirement).
The KYC route /kyc/fingerprint imports from here, not from kyc_service.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core import security
from app.core.dependencies import calculate_and_save_tier
from app.core.encryption import hash_fingerprint_data
from app.core.exceptions import EasyPayException
from app.models.database import FingerprintScan, User
from app.services import notification_service
from app.schemas.kyc import FingerprintDataRequest, FingerprintVerifyResponse

logger = logging.getLogger("easypay")

# ── Exact NADRA success message required by spec ───────────────────────────────
_NADRA_SUCCESS_MSG = (
    "Biometric data successfully verified via secure NADRA API integration."
)


# ══════════════════════════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _parse_uuid(user_id: str | uuid.UUID) -> uuid.UUID:
    if isinstance(user_id, uuid.UUID):
        return user_id
    return uuid.UUID(str(user_id))


async def _fetch_user(db: AsyncSession, uid: uuid.UUID) -> User:
    result = await db.execute(
        select(User).options(selectinload(User.wallet)).where(User.id == uid)
    )
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    return user


# ══════════════════════════════════════════════════════════════════════════════
# simulate_nadra_verisys — standalone mandatory function
# ══════════════════════════════════════════════════════════════════════════════

async def simulate_nadra_verisys(cnic_ref: str, quality_score: float) -> dict:
    """
    Simulate a NADRA VERISYS biometric match.

    Point 10: MUST await asyncio.sleep(2.5) — without this the demo looks fake.

    Args:
        cnic_ref:      NADRA internal reference string (not the raw CNIC number).
        quality_score: Average fingerprint quality 0–100. Score < 40 → failure.

    Returns a dict with:
        matched (bool), verification_id (str), confidence (float)
    """
    # Point 10 — mandatory 2.5-second simulated latency
    await asyncio.sleep(2.5)

    if quality_score < 40:
        logger.warning("NADRA VERISYS: low quality score %.1f — returning failure", quality_score)
        return {
            "matched": False,
            "verification_id": None,
            "confidence": 0.0,
            "reason": "Fingerprint quality too low for NADRA verification.",
        }

    verification_id = f"NADRA-VRY-{security.generate_reference().replace('EP-', '')}"
    return {
        "matched": True,
        "verification_id": verification_id,
        "confidence": 0.97,
    }


# ══════════════════════════════════════════════════════════════════════════════
# process_fingerprint_scan — main entry point
# ══════════════════════════════════════════════════════════════════════════════

async def process_fingerprint_scan(
    db: AsyncSession,
    user_id: str | uuid.UUID,
    data: FingerprintDataRequest,
) -> FingerprintVerifyResponse:
    """
    Process fingerprint scan data and run NADRA VERISYS simulation.

    Steps:
      1. Validate exactly 8 fingerprint records (schema also enforces this).
      2. SHA-256 hash each finger's feature dict (Point 9 — never store raw data).
      3. Compute average quality score.
      4. Check quality guard: avg < 40 → reject before calling NADRA.
      5. Call simulate_nadra_verisys() (Point 10: 2.5s delay inside).
      6. On match: persist all 8 FingerprintScan rows, set nadra_verified=True,
         fingerprint_verified=True, recalculate tier (Point 15).
      7. Insert Notification record (Rule 11 — FCM wired in B13).
      8. Return FingerprintVerifyResponse.

    Point 9: The mobile app sends only numeric feature vectors extracted
             on-device by ML Kit — never raw images.
    """
    uid = _parse_uuid(user_id)
    user = await _fetch_user(db, uid)

    if not user.cnic_verified:
        raise EasyPayException(
            detail="CNIC must be verified before fingerprint scan.",
            error_code="KYC_CNIC_NOT_VERIFIED",
        )

    # ── 1. Validate count ─────────────────────────────────────────────────────
    if len(data.fingers) != 8:
        raise EasyPayException(
            detail="Exactly 8 fingerprint records are required.",
            error_code="KYC_FINGERPRINT_COUNT_INVALID",
        )

    # ── 2. SHA-256 hash each finger + compute quality (Point 9) ──────────────
    total_quality = 0.0
    hashed_fingers = []
    for finger in data.fingers:
        feature_dict = finger.model_dump()
        pattern_hash = hash_fingerprint_data(feature_dict)
        total_quality += finger.quality_score
        hashed_fingers.append((finger, pattern_hash))

    avg_quality = total_quality / len(data.fingers)

    # ── 3. Quality guard before NADRA call ───────────────────────────────────
    if avg_quality < 40:
        raise EasyPayException(
            detail=(
                f"Average fingerprint quality score {avg_quality:.1f}/100 is too low. "
                "Please rescan your fingerprints carefully and ensure clean sensor contact."
            ),
            error_code="KYC_FINGERPRINT_QUALITY_LOW",
        )

    # ── 4. NADRA VERISYS simulation (Point 10 — 2.5s inside) ─────────────────
    nadra_result = await simulate_nadra_verisys(
        cnic_ref=str(uid),
        quality_score=avg_quality,
    )

    if not nadra_result["matched"]:
        raise EasyPayException(
            detail="NADRA VERISYS could not match the provided biometric data.",
            error_code="KYC_NADRA_MATCH_FAILED",
        )

    verification_id: str = nadra_result["verification_id"]
    confidence: float = nadra_result["confidence"]

    # ── 5. Persist FingerprintScan rows ──────────────────────────────────────
    now_utc = datetime.now(timezone.utc)
    for finger, pattern_hash in hashed_fingers:
        scan = FingerprintScan(
            user_id=uid,
            finger_position=finger.position,
            ridge_count=finger.ridge_count,
            minutiae_points=finger.minutiae_points,
            quality_score=finger.quality_score,
            pattern_hash=pattern_hash,
            verisys_ref=verification_id,
        )
        db.add(scan)

    # ── 6. Update user flags + tier (Point 15) ────────────────────────────────
    user.fingerprint_verified = True
    user.fingerprint_verified_at = now_utc
    user.nadra_verified = True
    user.nadra_verification_id = verification_id
    # fingerprint_verified + nadra_verified → get_user_tier returns 3
    new_tier = await calculate_and_save_tier(db, user)

    # ── 7. Notification + FCM push (B13) ────────────────────────────────────
    await notification_service.create_notification(
        db, uid,
        title="NADRA Verified ✅",
        body=f"Fingerprint biometric matched via NADRA VERISYS. Tier {new_tier} unlocked.",
        type="system",
        data={"step": "fingerprint", "nadra_ref": verification_id, "tier": new_tier},
    )

    logger.info(
        "Fingerprint scan completed for user %s | ref=%s | avg_quality=%.1f | tier→%d",
        uid, verification_id, avg_quality, new_tier,
    )

    return FingerprintVerifyResponse(
        matched=True,
        verification_id=verification_id,
        confidence=round(confidence, 4),
        message=_NADRA_SUCCESS_MSG,
    )
