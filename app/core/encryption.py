"""
app/core/encryption.py — EasyPay v3.0 Sensitive Data Encryption

Rules Enforced:
  Point 4 — ENCRYPTION_KEY must be a 32-byte base64 Fernet key.
             Use Fernet(settings.ENCRYPTION_KEY.encode()) — never raw AES.
  Point 9 — hash_fingerprint_data() uses SHA-256 (hashlib.sha256).
             NEVER use Bcrypt here — fingerprints must be deterministic
             (same input must always produce the same hash for matching).
"""
import hashlib
import json

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


# ─────────────────────────────────────────────────────────────────────────────
# Symmetric encryption helpers — Fernet (AES-128-CBC + HMAC-SHA256)
# Point 4: ENCRYPTION_KEY is a 32-byte base64 URL-safe string generated with
#           Fernet.generate_key().  Store it in .env as ENCRYPTION_KEY=<value>.
# ─────────────────────────────────────────────────────────────────────────────

def _fernet() -> Fernet:
    """Return a Fernet instance initialised from settings.ENCRYPTION_KEY."""
    return Fernet(settings.ENCRYPTION_KEY.encode())


def encrypt_sensitive(plaintext: str) -> str:
    """
    Encrypt a sensitive string (e.g. CNIC) using Fernet.

    Returns the encrypted ciphertext as a URL-safe base64 string.
    The same key is always used; a new random IV is generated per call,
    so two calls with the same plaintext will produce different ciphertexts
    (safe for storage, not suitable for deterministic lookups).

    Raises:
        ValueError — if ENCRYPTION_KEY is malformed / wrong length
    """
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_sensitive(encrypted: str) -> str:
    """
    Decrypt a Fernet-encrypted ciphertext back to plaintext.

    Args:
        encrypted: The ciphertext produced by encrypt_sensitive().

    Returns:
        The original plaintext string.

    Raises:
        cryptography.fernet.InvalidToken — if ciphertext is tampered or key
                                           does not match. Callers MUST catch
                                           this and return 400 / 403 — never
                                           expose the raw exception to clients.
    """
    return _fernet().decrypt(encrypted.encode()).decode()


# ─────────────────────────────────────────────────────────────────────────────
# Fingerprint hashing — SHA-256  (Point 9)
# CRITICAL: This MUST be deterministic.  NEVER use Bcrypt here.
# Two scans of the same biometric data must produce the exact same hash so we
# can match against the stored value in the fingerprint_scans table.
# ─────────────────────────────────────────────────────────────────────────────

def hash_fingerprint_data(data_dict: dict) -> str:
    """
    Deterministically hash a fingerprint feature dictionary using SHA-256.

    Keys are sorted before serialisation so {"a": 1, "b": 2} and
    {"b": 2, "a": 1} produce identical hashes.

    Args:
        data_dict: Raw fingerprint feature vectors / metadata dict produced
                   by the biometric scanning library.

    Returns:
        64-character lowercase hex string (SHA-256 digest).

    IMPORTANT — Bcrypt is intentionally NOT used here.  Bcrypt generates a
    random salt on every call; two calls with identical input would produce
    different hashes, making biometric matching impossible.
    """
    canonical = json.dumps(data_dict, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
