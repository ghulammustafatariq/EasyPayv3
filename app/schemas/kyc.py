"""
app/schemas/kyc.py — EasyPay v3.0 KYC Schemas

Rule 4:  NEVER store raw fingerprint images — only SHA-256 hash.
Rule 7:  CNIC values stored encrypted; CNICExtractedData exposes parsed
         fields ONLY (number, name, dob), never the raw cnic_encrypted column.
Point 9: Fingerprint uses SHA-256, not Bcrypt — handled at the service layer.
         Schemas here accept raw feature dicts; hashing happens in
         encryption.hash_fingerprint_data() before any DB write.
"""
from typing import List, Optional

from pydantic import BaseModel, Field


# ══════════════════════════════════════════════════════════════════════════════
# CNIC  (KYC Tier 2 step 1 — liveness then follows)
# ══════════════════════════════════════════════════════════════════════════════

class CNICUploadRequest(BaseModel):
    """
    Base64-encoded front and back images of the CNIC.
    Images are sent to DeepSeek Vision for extraction and then
    uploaded to Cloudinary with type="private" (Rule 8).
    Raw CNIC number extracted is AES-256-GCM encrypted before storing (Rule 7).
    """

    front_base64: str = Field(
        ...,
        description="Base64-encoded JPEG/PNG of CNIC front (without data URI prefix)",
    )
    back_base64: str = Field(
        ...,
        description="Base64-encoded JPEG/PNG of CNIC back (without data URI prefix)",
    )


class CNICExtractedData(BaseModel):
    """
    Fields parsed from CNIC images by Gemini Vision.
    Rule 2/7: cnic_encrypted is NEVER returned in API responses.
    This schema exposes ONLY the human-readable extracted fields.
    Field names match what the Flutter app expects (name, expiry, confidence).
    """

    cnic_number: str = Field(
        ...,
        description="Extracted CNIC in format XXXXX-XXXXXXX-X",
        examples=["42201-1234567-9"],
    )
    name: str = Field(..., description="Full name as printed on CNIC in capitals")
    date_of_birth: str = Field(..., description="DD/MM/YYYY as extracted from CNIC")
    expiry: str = Field(..., description="DD/MM/YYYY expiry as printed on CNIC")
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Gemini extraction confidence 0.0–1.0",
    )

    model_config = {"from_attributes": True}


# ══════════════════════════════════════════════════════════════════════════════
# LIVENESS  (KYC Tier 2 step 2)
# ══════════════════════════════════════════════════════════════════════════════

class LivenessVerifyRequest(BaseModel):
    """
    Selfie uploaded for face-liveness check.
    DeepSeek Vision compares it against the CNIC photo.
    Minimum 80% face_match_confidence required to pass (RESPONSE_STANDARDS).
    """

    selfie_base64: str = Field(
        ...,
        description="Base64-encoded selfie JPEG/PNG (without data URI prefix)",
    )


class LivenessResult(BaseModel):
    """Result of DeepSeek Vision face-match and liveness analysis."""

    is_live_person: bool = Field(
        ...,
        description="True if the image passes anti-spoofing checks",
    )
    face_match_confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="0.0–1.0 confidence that selfie matches the CNIC photo",
    )
    success: bool = Field(
        ...,
        description="True when is_live_person=True AND face_match_confidence >= 0.80",
    )
    failure_reason: str | None = Field(
        None,
        description="Human-readable reason for failure, or null on success.",
    )

    model_config = {"from_attributes": True}


# ══════════════════════════════════════════════════════════════════════════════
# FINGERPRINT  (KYC Tier 3 — NADRA VERISYS)
#
# Rule 4 / Point 9: fingerprint images are NEVER stored or transmitted.
# The Android app extracts biometric features locally and sends ONLY the
# numeric feature dictionary. The backend hashes this with SHA-256 before
# writing to fingerprint_scans.pattern_hash.
# ══════════════════════════════════════════════════════════════════════════════

class FingerprintFeature(BaseModel):
    """Feature data for a single finger — extracted on-device by ML Kit."""

    position: str = Field(
        ...,
        description=(
            "Finger identifier, e.g. 'right_thumb', 'left_index', etc."
        ),
        examples=["right_thumb"],
    )
    ridge_count: int = Field(..., ge=0, description="Number of ridges between deltas")
    minutiae_points: int = Field(..., ge=0, description="Count of ridge endings + bifurcations")
    quality_score: int = Field(
        ...,
        ge=0,
        le=100,
        description="Quality score 0–100. Minimum 40 required (FINGERPRINT_QUALITY_POOR).",
    )


class FingerprintDataRequest(BaseModel):
    """
    8 finger feature dictionaries submitted during Tier 3 NADRA verification.
    Each dict is SHA-256 hashed at the service layer before storing (Point 9).
    """

    fingers: List[FingerprintFeature] = Field(
        ...,
        min_length=8,
        max_length=8,
        description="Exactly 8 finger feature records (all mandatory fingers)",
    )


class FingerprintVerifyResponse(BaseModel):
    """Response from the fingerprint verification / NADRA simulation endpoint."""

    matched: bool = Field(
        ...,
        description="True if all 8 hashes matched stored records",
    )
    verification_id: str = Field(
        ...,
        description="NADRA simulation reference ID (verisys_ref)",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Overall match confidence across 8 fingers",
    )
    message: str = Field(
        ...,
        description="Human-readable verification result message",
    )

    model_config = {"from_attributes": True}
