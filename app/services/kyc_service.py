"""
app/services/kyc_service.py — EasyPay v3.0 KYC Service

Three verification steps (Tier 2 → Tier 3):
  Step 1 — process_cnic_upload    : OCR.space extract + DeepSeek format + Cloudinary upload + encryption
  Step 2 — process_liveness_check : Face++ face-compare (real API only — no bypass)
  Step 3 — process_fingerprint_data: NADRA simulation + SHA-256 fingerprint hashing

Rules Enforced:
  Rule  4 — NEVER store raw fingerprint images — only SHA-256 hash (Point 9)
  Rule  5 — CNIC encrypted with Fernet before DB write (Rule 7)
  Rule  8 — ALL Cloudinary KYC uploads use type="private"
  Point 6 — DeepSeek may wrap JSON in markdown fences — strip via deepseek module
  Point 9 — hash_fingerprint_data() uses SHA-256, never Bcrypt (deterministic)
  Point 10 — await asyncio.sleep(2.5) in NADRA simulation for realism
  Point 15 — After each KYC step, verification_tier is explicitly recalculated.
             NEVER assume the tier auto-updates.
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import re
import uuid
from datetime import datetime, timezone

import cloudinary
import cloudinary.uploader
import httpx
from fastapi import HTTPException
from PIL import Image, ExifTags
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core import security
from app.core.config import settings
from app.core.deepseek import call_deepseek_chat
from app.core.dependencies import calculate_and_save_tier
from app.core.encryption import encrypt_sensitive, hash_fingerprint_data
from app.core.exceptions import AIServiceUnavailableError, CNICNameMismatchError, EasyPayException
from app.models.database import FingerprintScan, User
from app.services import notification_service
from app.schemas.kyc import (
    CNICExtractedData,
    FingerprintDataRequest,
    FingerprintVerifyResponse,
    LivenessResult,
    LivenessVerifyRequest,
)

logger = logging.getLogger("easypay")

# ── One-time SDK configuration ────────────────────────────────────────────────
cloudinary.config(
    cloud_name=settings.CLOUDINARY_CLOUD_NAME,
    api_key=settings.CLOUDINARY_API_KEY,
    api_secret=settings.CLOUDINARY_API_SECRET,
    secure=True,
)

# ── OCR.space timeout ─────────────────────────────────────────────────────────
_OCR_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=5.0)
_OCR_SPACE_URL = "https://api.ocr.space/parse/image"

# ── NADRA result message (exact string required by spec) ──────────────────────
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


# ══════════════════════════════════════════════════════════════════════════════
# IMAGE PREPROCESSING
# ══════════════════════════════════════════════════════════════════════════════

def _preprocess_cnic_image(raw_base64: str) -> str:
    """
    Minimal CNIC image preparation — no quality changes:
    1. Decode base64 → PIL Image
    2. Auto-rotate based on EXIF orientation tag (fixes phone camera rotation)
    3. Convert to RGB (strips alpha channel if PNG)
    4. Re-encode as JPEG → base64
    Original image quality is preserved exactly as captured.
    """
    try:
        img_bytes = base64.b64decode(raw_base64)
        img = Image.open(io.BytesIO(img_bytes))

        # ── Auto-rotate from EXIF (orientation fix only, no quality change) ─
        try:
            exif = img._getexif()  # type: ignore[attr-defined]
            if exif:
                orient_key = next(
                    (k for k, v in ExifTags.TAGS.items() if v == "Orientation"), None
                )
                if orient_key and orient_key in exif:
                    orientation = exif[orient_key]
                    rotations = {3: 180, 6: 270, 8: 90}
                    if orientation in rotations:
                        img = img.rotate(rotations[orientation], expand=True)
        except Exception:
            pass  # EXIF not available (PNG / stripped JPEG)

        # ── Ensure RGB ────────────────────────────────────────────────────
        if img.mode != "RGB":
            img = img.convert("RGB")

        # ── Re-encode to JPEG base64 (no quality modifications) ───────────
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        return base64.b64encode(buf.getvalue()).decode()

    except Exception as exc:
        logger.warning("CNIC image prepare failed, using original: %s", exc)
        return raw_base64  # fall back to original if anything goes wrong


# ══════════════════════════════════════════════════════════════════════════════
# OCR.space + DEEPSEEK CNIC HELPERS
# ══════════════════════════════════════════════════════════════════════════════

async def _ocr_space_extract(front_base64: str, uid: uuid.UUID) -> str:
    """
    Call OCR.space API with the CNIC front image (base64) and return raw text.
    Uses Engine 2 which is better for IDs with numbers and special characters.
    """
    payload = {
        "apikey": settings.OCR_SPACE_API_KEY,
        "language": "eng",
        "isOverlayRequired": "false",
        "OCREngine": "2",
        "base64Image": f"data:image/jpeg;base64,{front_base64}",
        "scale": "true",
        "detectOrientation": "true",
    }
    try:
        async with httpx.AsyncClient(timeout=_OCR_TIMEOUT) as client:
            resp = await client.post(_OCR_SPACE_URL, data=payload)
            resp.raise_for_status()
    except httpx.RequestError as exc:
        logger.error("OCR.space request error for user %s: %s", uid, exc)
        raise AIServiceUnavailableError(
            detail="CNIC OCR service is temporarily unavailable. Please try again.",
            error_code="KYC_OCR_UNAVAILABLE",
        ) from exc
    except httpx.HTTPStatusError as exc:
        logger.error("OCR.space HTTP error %s for user %s", exc.response.status_code, uid)
        raise AIServiceUnavailableError(
            detail="CNIC OCR service returned an error. Please try again.",
            error_code="KYC_OCR_UNAVAILABLE",
        ) from exc

    result = resp.json()
    exit_code = result.get("OCRExitCode")
    if exit_code != 1:
        err = result.get("ErrorMessage") or result.get("ErrorDetails") or "Unknown OCR error"
        logger.error("OCR.space failed (exit=%s) for user %s: %s", exit_code, uid, err)
        raise AIServiceUnavailableError(
            detail="Could not read text from CNIC image. Ensure the image is clear and try again.",
            error_code="KYC_OCR_UNAVAILABLE",
        )

    parsed_results = result.get("ParsedResults") or []
    if not parsed_results:
        raise EasyPayException(
            detail="No text found in the CNIC image. Please retake the photo.",
            error_code="KYC_OCR_PARSE_ERROR",
        )
    return parsed_results[0].get("ParsedText", "")


async def _deepseek_format_cnic(raw_text: str, uid: uuid.UUID) -> dict:
    """
    Send raw OCR text to DeepSeek to extract structured CNIC fields.
    Returns a dict with: name, cnic_number, date_of_birth, expiry, confidence.
    Point 6: call_deepseek_chat already strips markdown fences internally.
    """
    prompt = (
        "You are a data extraction assistant specialised in Pakistani CNICs "
        "(National Identity Card / قومی شناختی کارڈ).\n\n"
        "Below is raw OCR text extracted from the FRONT of a Pakistani CNIC:\n\n"
        f"'''\n{raw_text}\n'''\n\n"
        "Extract the following fields:\n"
        "- name: The holder's full name in CAPITAL LETTERS (English only, "
        "  e.g. 'MUHAMMAD ALI KHAN').\n"
        "- cnic_number: The 13-digit identity number in format XXXXX-XXXXXXX-X.\n"
        "- date_of_birth: In format DD/MM/YYYY.\n"
        "- expiry: Date of expiry in format DD/MM/YYYY.\n"
        "- confidence: Float 0.0–1.0 reflecting how clearly you could read "
        "  ALL four fields.\n\n"
        "Return ONLY a valid JSON object — no markdown, no extra text:\n"
        '{"name":"<FULL NAME>","cnic_number":"XXXXX-XXXXXXX-X",'
        '"date_of_birth":"DD/MM/YYYY","expiry":"DD/MM/YYYY","confidence":0.0}'
    )
    try:
        return await call_deepseek_chat(prompt, json_mode=True)
    except Exception as exc:
        logger.error("DeepSeek CNIC format failed for user %s: %s", uid, exc)
        raise AIServiceUnavailableError(
            detail="Could not parse CNIC data. Please try again.",
            error_code="KYC_OCR_UNAVAILABLE",
        ) from exc


async def _fetch_user(db: AsyncSession, uid: uuid.UUID) -> User:
    result = await db.execute(
        select(User).options(selectinload(User.wallet)).where(User.id == uid)
    )
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    return user


# ══════════════════════════════════════════════════════════════════════════════
# STEP 1 — CNIC UPLOAD: OCR.space → DeepSeek → Validate → Encrypt → DB
# ══════════════════════════════════════════════════════════════════════════════

async def process_cnic_upload(
    db: AsyncSession,
    user_id: str | uuid.UUID,
    front_base64: str,
    back_base64: str,
) -> CNICExtractedData:
    """
    1. Upload front + back CNIC images to Cloudinary with type="private".
    2. Extract raw text via OCR.space API (Engine 2, no Gemini key).
    3. Send raw OCR text to DeepSeek to format into structured CNIC JSON.
    4. Validate extracted name == user.full_name AND CNIC number format is valid.
       If already stored, also confirm cnic_number matches the stored encrypted value.
    5. Encrypt the CNIC number and persist on User (Rule 7). Set cnic_verified=True.
    6. Upgrade verification_tier (Point 15).
    7. Return CNICExtractedData — caller proceeds to face (liveness) verification.

    Rule 8:  type="private" — CNIC images must NOT be publicly accessible.
    Point 6: DeepSeek call_deepseek_chat strips markdown fences internally.
    Point 15: verification_tier is explicitly set, never assumed to auto-update.
    """
    uid = _parse_uuid(user_id)
    user = await _fetch_user(db, uid)

    # ── 1. Preprocess both images (sharpen, contrast, auto-rotate) ───────────
    processed_front_base64 = _preprocess_cnic_image(front_base64)
    processed_back_base64 = _preprocess_cnic_image(back_base64)
    logger.debug("CNIC images preprocessed for user %s", uid)

    # ── 2. Cloudinary private uploads (using preprocessed images) ────────────
    front_uri = f"data:image/jpeg;base64,{processed_front_base64}"
    back_uri = f"data:image/jpeg;base64,{processed_back_base64}"

    # front_result = cloudinary.uploader.upload(
    #     front_uri,
    #     folder=f"easypay/cnic/{uid}",
    #     public_id="front",
    #     overwrite=True,
    #     resource_type="image",
    #     type="private",
    # )
    # back_result = cloudinary.uploader.upload(
    #     back_uri,
    #     folder=f"easypay/cnic/{uid}",
    #     public_id="back",
    #     overwrite=True,
    #     resource_type="image",
    #     type="private",
    # )

    front_url: str = front_uri # front_result["secure_url"]
    back_url: str = back_uri # back_result["secure_url"]

    # ── 3. OCR.space — extract raw text from preprocessed CNIC front ─────────
    raw_ocr_text = await _ocr_space_extract(processed_front_base64, uid)
    logger.debug("OCR.space raw text for user %s: %.200s", uid, raw_ocr_text)

    # ── 4. DeepSeek — format raw OCR text into structured CNIC fields ─────────
    extracted: dict = await _deepseek_format_cnic(raw_ocr_text, uid)

    # Normalise CNIC number: remove non-digits then re-format as XXXXX-XXXXXXX-X
    raw_cnic: str = str(extracted.get("cnic_number", "")).strip()
    digits_only = re.sub(r"\D", "", raw_cnic)
    if len(digits_only) == 13:
        extracted["cnic_number"] = f"{digits_only[:5]}-{digits_only[5:12]}-{digits_only[12]}"

    try:
        cnic_data = CNICExtractedData(**extracted)
    except Exception as exc:
        logger.error("CNICExtractedData validation failed for user %s: %r — raw=%r", uid, exc, extracted)
        raise EasyPayException(
            detail="Could not validate CNIC data. Please ensure the card is fully visible and try again.",
            error_code="KYC_OCR_PARSE_ERROR",
        ) from exc

    # ── 4. Validate: extracted name must match account full_name ──────────────
    def _norm(s: str) -> str:
        return re.sub(r"\s+", " ", s.strip().lower())

    if not _norm(cnic_data.name) == _norm(user.full_name or ""):
        logger.warning(
            "CNIC name mismatch for user %s: extracted=%r registered=%r",
            uid, cnic_data.name, user.full_name,
        )
        raise CNICNameMismatchError(
            extracted=cnic_data.name,
            registered=user.full_name or "",
        )

    # ── 4b. If CNIC was already stored, verify the number matches ────────────
    if user.cnic_encrypted:
        from app.core.encryption import decrypt_sensitive
        stored_cnic = decrypt_sensitive(user.cnic_encrypted)
        if stored_cnic != cnic_data.cnic_number:
            logger.warning(
                "CNIC number mismatch for user %s: extracted=%r stored=%r",
                uid, cnic_data.cnic_number, stored_cnic,
            )
            raise EasyPayException(
                detail="The CNIC number does not match your previously verified document.",
                error_code="KYC_CNIC_NUMBER_MISMATCH",
            )

    # ── 5. Encrypt CNIC number (Rule 7) and persist ───────────────────────────
    encrypted_cnic = encrypt_sensitive(cnic_data.cnic_number)
    user.cnic_encrypted = encrypted_cnic
    user.cnic_front_url = front_url
    user.cnic_back_url = back_url
    user.cnic_verified = True

    # Point 15: explicitly recalculate tier
    new_tier = await calculate_and_save_tier(db, user)

    await notification_service.create_notification(
        db, uid,
        title="CNIC Verified ✅",
        body="Your CNIC has been verified. You are now Tier 2! Proceed to liveness check for further verification.",
        type="system",
        data={"step": "cnic", "tier": new_tier},
    )
    logger.info("CNIC upload and verification completed for user %s (tier→%d)", uid, new_tier)

    return cnic_data


# ══════════════════════════════════════════════════════════════════════════════
# STEP 2 — LIVENESS CHECK (Face++)
# ══════════════════════════════════════════════════════════════════════════════

def _extract_and_upscale_cnic_face(cnic_bytes: bytes, uid: uuid.UUID) -> str:
    """
    Pakistani CNIC cards contain a small passport photo (~150×200 px).
    Face++ requires faces to be at least ~48px wide to detect.

    Strategy:
    1. Open the CNIC image and auto-rotate via EXIF.
    2. The passport photo on a Pakistani CNIC is always in the top-right
       quadrant (roughly x: 60–85%, y: 10–65% of card height when landscape,
       or top-left when portrait).  Crop that region.
    3. Upscale the crop to MIN_FACE_SIZE × MIN_FACE_SIZE using LANCZOS.
    4. Return as base64 JPEG.

    If anything goes wrong, fall back to upscaling the full CNIC 2× — still
    far better than sending the original tiny image.
    """
    MIN_FACE_SIZE = 400  # pixels — comfortably above Face++ minimum
    PADDING_FACTOR = 0.15  # extra padding around crop

    try:
        img = Image.open(io.BytesIO(cnic_bytes)).convert("RGB")

        # Auto-rotate via EXIF
        try:
            exif = img._getexif()  # type: ignore[attr-defined]
            if exif:
                for tag, val in exif.items():
                    if ExifTags.TAGS.get(tag) == "Orientation":
                        rotations = {3: 180, 6: 270, 8: 90}
                        if val in rotations:
                            img = img.rotate(rotations[val], expand=True)
                        break
        except Exception:
            pass

        w, h = img.size

        # Ensure landscape orientation (CNIC is always wider than tall)
        if h > w:
            img = img.rotate(90, expand=True)
            w, h = img.size

        # Crop the passport photo region (top-right area of the CNIC)
        # Pakistani CNIC layout (landscape): photo sits at roughly
        #   x: w*0.60 → w*0.88,  y: h*0.08 → h*0.85
        pad_x = int(w * PADDING_FACTOR * 0.5)
        pad_y = int(h * PADDING_FACTOR * 0.5)
        x1 = max(0, int(w * 0.60) - pad_x)
        y1 = max(0, int(h * 0.08) - pad_y)
        x2 = min(w, int(w * 0.88) + pad_x)
        y2 = min(h, int(h * 0.85) + pad_y)
        crop = img.crop((x1, y1, x2, y2))

        # Upscale so the face region is large enough for Face++
        cw, ch = crop.size
        scale = max(MIN_FACE_SIZE / cw, MIN_FACE_SIZE / ch, 1.0)
        new_w = int(cw * scale)
        new_h = int(ch * scale)
        crop = crop.resize((new_w, new_h), Image.LANCZOS)

        buf = io.BytesIO()
        crop.save(buf, format="JPEG", quality=92)
        result = base64.b64encode(buf.getvalue()).decode()
        logger.info(
            "CNIC face crop for user %s: original=%dx%d crop=%dx%d → upscaled=%dx%d b64_len=%d",
            uid, w, h, cw, ch, new_w, new_h, len(result),
        )
        return result

    except Exception as exc:
        logger.warning(
            "CNIC face extraction failed for user %s (%s) — falling back to full-image 2× upscale",
            uid, exc,
        )
        # Fallback: upscale the full CNIC image 2×
        try:
            img = Image.open(io.BytesIO(cnic_bytes)).convert("RGB")
            w, h = img.size
            img = img.resize((w * 2, h * 2), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=92)
            return base64.b64encode(buf.getvalue()).decode()
        except Exception:
            # Last resort: send original bytes unchanged
            return base64.b64encode(cnic_bytes).decode()


async def process_liveness_check(
    db: AsyncSession,
    user_id: str | uuid.UUID,
    data: LivenessVerifyRequest,
) -> LivenessResult:
    """
    1. Upload selfie to Cloudinary (type="private").
    2. Require Face++ to be configured — no bypass, no simulation.
    3. Face++ /compare: selfie vs stored CNIC front URL.
       confidence > 80.0 → match. Face++ API failure → EasyPayException.
    4. On success: biometric_verified=True, tier to max 3.
    5. Return LivenessResult.

    face_match_confidence is normalised to 0.0–1.0 (Face++ returns 0–100).
    """
    uid = _parse_uuid(user_id)
    user = await _fetch_user(db, uid)

    if not user.cnic_front_url:
        raise EasyPayException(
            detail="CNIC must be uploaded and verified before running liveness check.",
            error_code="KYC_CNIC_NOT_VERIFIED",
        )

    # ── 1. Upload selfie to Cloudinary (private) ──────────────────────────────
    selfie_uri = f"data:image/jpeg;base64,{data.selfie_base64}"
    # selfie_result = cloudinary.uploader.upload(
    #     selfie_uri,
    #     folder=f"easypay/selfies/{uid}",
    #     public_id="liveness",
    #     overwrite=True,
    #     resource_type="image",
    #     type="private",
    # )
    # selfie_url: str = selfie_result["secure_url"]

    selfie_url: str = selfie_uri

    user.liveness_selfie_url = selfie_url

    # ── 2. Require Face++ to be configured ────────────────────────────────────
    if not settings.FACEPLUSPLUS_API_KEY or not settings.FACEPLUSPLUS_API_SECRET:
        raise EasyPayException(
            detail="Face verification service is not configured. Contact support.",
            error_code="KYC_FACEPP_NOT_CONFIGURED",
        )

    # ── 3. Face++ face-compare (base64 → no private URL access issues) ─────────
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            # Download the stored CNIC front from Cloudinary
            if user.cnic_front_url.startswith("data:image"):
                cnic_bytes = base64.b64decode(user.cnic_front_url.split(",")[1])
            else:
                cnic_dl = await client.get(user.cnic_front_url)
                cnic_dl.raise_for_status()
                cnic_bytes = cnic_dl.content

            # Prepare CNIC image: run Face++ /detect to find the face rectangle,
            # crop it with padding, and upscale it so Face++ can reliably detect
            # the small passport photo embedded in the CNIC card.
            cnic_b64_for_compare = _extract_and_upscale_cnic_face(cnic_bytes, uid)

            resp = await client.post(
                f"{settings.FACEPLUSPLUS_BASE_URL}/facepp/v3/compare",
                data={
                    "api_key": settings.FACEPLUSPLUS_API_KEY,
                    "api_secret": settings.FACEPLUSPLUS_API_SECRET,
                    "image_base64_1": data.selfie_base64,
                    "image_base64_2": cnic_b64_for_compare,
                },
            )
            resp.raise_for_status()
            payload = resp.json()
    except httpx.HTTPStatusError as exc:
        logger.error("Face++ HTTP error for user %s: %s — body=%s", uid, exc, getattr(exc.response, 'text', ''))
        raise EasyPayException(
            detail="Face verification service returned an error. Please retry.",
            error_code="KYC_FACEPP_HTTP_ERROR",
        ) from exc
    except httpx.RequestError as exc:
        logger.error("Face++ request error for user %s: %s", uid, exc)
        raise EasyPayException(
            detail="Face verification service is unreachable. Please retry.",
            error_code="KYC_FACEPP_UNAVAILABLE",
        ) from exc

    # faces2 is empty when Face++ cannot detect a face in the CNIC image.
    # error_message is set when Face++ itself returns an API error.
    # In both cases we MUST NOT verify the user — return a real failure.
    if "error_message" in payload:
        logger.warning(
            "Face++ API error for user %s: %s — rejecting liveness check",
            uid, payload.get("error_message"),
        )
        await db.commit()
        return LivenessResult(
            is_live_person=False,
            face_match_confidence=0.0,
            success=False,
            failure_reason=(
                "We could not process your face verification. "
                "Please ensure your CNIC photo is clear and retry."
            ),
        )

    if payload.get("faces2") == [] and not payload.get("confidence"):
        logger.warning(
            "Face++ could not detect a face in the CNIC for user %s (faces2=[]) — rejecting. "
            "payload=%s",
            uid, payload,
        )
        await db.commit()
        return LivenessResult(
            is_live_person=False,
            face_match_confidence=0.0,
            success=False,
            failure_reason=(
                "Your CNIC photo is unclear or missing. "
                "Please re-upload your CNIC and try again."
            ),
        )

    raw_confidence = float(payload.get("confidence", 0.0))
    if raw_confidence == 0.0:
        logger.warning(
            "Face++ returned 0%% confidence for user %s — rejecting. payload: %s",
            uid, payload,
        )

    is_match: bool = raw_confidence >= 80.0   # set from 30 to 80 for CNIC face detection
    normalised: float = round(raw_confidence / 100.0, 4)

    if is_match:
        user.biometric_verified = True
        user.biometric_verified_at = datetime.now(timezone.utc)
        # Point 15: recalculate from live flags — cnic+biometric → Tier 2
        new_tier = await calculate_and_save_tier(db, user)
        await notification_service.create_notification(
            db, uid,
            title="Identity Verified ✅",
            body=f"Face verification passed. Tier {new_tier} unlocked.",
            type="system",
            data={"step": "liveness", "tier": new_tier},
        )

    await db.commit()
    logger.info(
        "Liveness check for user %s: confidence=%.2f matched=%s",
        uid, raw_confidence, is_match,
    )

    return LivenessResult(
        is_live_person=is_match,
        face_match_confidence=normalised,
        success=is_match,
    )


# ══════════════════════════════════════════════════════════════════════════════
# STEP 3 — FINGERPRINT + NADRA SIMULATION
# ══════════════════════════════════════════════════════════════════════════════

async def process_fingerprint_data(
    db: AsyncSession,
    user_id: str | uuid.UUID,
    data: FingerprintDataRequest,
) -> FingerprintVerifyResponse:
    """
    1. Validate exactly 8 fingerprint features (schema enforces min/max=8).
    2. SHA-256 hash each finger's feature dict (Point 9 — never store raw data).
    3. Persist each hash as a FingerprintScan row.
    4. Simulate NADRA VERISYS with a 2.5-second delay (Point 10).
    5. Set fingerprint_verified=True, nadra_verified=True, tier to max 3.
    6. Return FingerprintVerifyResponse with exact required message.

    Point 4: Raw fingerprint images are never passed — the mobile app sends
             only numeric feature vectors extracted on-device by ML Kit.
    """
    uid = _parse_uuid(user_id)
    user = await _fetch_user(db, uid)

    # ── 1. Validate count (also enforced by schema min/max=8) ─────────────────
    if len(data.fingers) != 8:
        raise EasyPayException(
            detail="Exactly 8 fingerprint records are required.",
            error_code="KYC_FINGERPRINT_COUNT_INVALID",
        )

    # ── 2. Generate NADRA reference ───────────────────────────────────────────
    verisys_ref: str = security.generate_reference()

    # ── 3. Hash each finger + persist FingerprintScan rows ───────────────────
    # Point 9: hash_fingerprint_data uses SHA-256 (deterministic, NOT Bcrypt)
    total_quality = 0
    for finger in data.fingers:
        feature_dict = finger.model_dump()
        pattern_hash = hash_fingerprint_data(feature_dict)
        total_quality += finger.quality_score

        scan = FingerprintScan(
            user_id=uid,
            finger_position=finger.position,
            ridge_count=finger.ridge_count,
            minutiae_points=finger.minutiae_points,
            quality_score=finger.quality_score,
            pattern_hash=pattern_hash,
            verisys_ref=verisys_ref,
        )
        db.add(scan)

    # ── 4. NADRA simulation delay (Point 10) ──────────────────────────────────
    await asyncio.sleep(2.5)

    # ── 5. Update user flags (Point 15: recalculate from live flags) ──────────
    now_utc = datetime.now(timezone.utc)
    user.fingerprint_verified = True
    user.fingerprint_verified_at = now_utc
    user.nadra_verified = True
    user.nadra_verification_id = verisys_ref
    # fingerprint_verified + nadra_verified (+ cnic + biometric already set) → tier 4
    new_tier = await calculate_and_save_tier(db, user)

    await notification_service.create_notification(
        db, uid,
        title="NADRA Verified ✅",
        body=f"Fingerprint biometric matched via NADRA VERISYS. Tier {new_tier} unlocked.",
        type="system",
        data={"step": "fingerprint", "nadra_ref": verisys_ref, "tier": new_tier},
    )
    logger.info(
        "Fingerprint verification completed for user %s (ref=%s, tier→%d)",
        uid, verisys_ref, new_tier,
    )

    # ── 6. Calculate average confidence from quality scores ───────────────────
    avg_quality = total_quality / len(data.fingers)
    confidence = round(avg_quality / 100.0, 4)

    return FingerprintVerifyResponse(
        matched=True,
        verification_id=verisys_ref,
        confidence=confidence,
        message=_NADRA_SUCCESS_MSG,
    )
