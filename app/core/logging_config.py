"""
app/core/logging_config.py — EasyPay v3.0 Structured Logging

B21 Logging Rules:
  - NEVER log: password, password_hash, pin_hash, cnic, cnic_encrypted, api_key
  - LOG every KYC decision with timestamp + user_id + confidence + outcome
  - LOG every admin action with admin_id + action + target + reason
  - LOG every fraud flag creation with rule_triggered + severity

B22: RequestIdFilter injects request_id from ContextVar into every log record.
"""
import logging
import re
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

# ─────────────────────────────────────────────────────────────────────────────
# Sensitive field scrubber — never let secrets appear in logs
# ─────────────────────────────────────────────────────────────────────────────
_SENSITIVE_FIELDS = frozenset({
    "password",
    "password_hash",
    "pin_hash",
    "cnic",
    "cnic_encrypted",
    "api_key",
    "secret_key",
    "encryption_key",
    "admin_secret_header",
    "access_token",
    "refresh_token",
    "pin",
})

_SENSITIVE_PATTERN = re.compile(
    r'("(?:' + "|".join(re.escape(f) for f in _SENSITIVE_FIELDS) + r')"\s*:\s*)"[^"]*"',
    re.IGNORECASE,
)


class SensitiveDataFilter(logging.Filter):
    """
    Logging filter that scrubs sensitive field values from log messages.
    Replaces values for known sensitive keys with [REDACTED].
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _scrub(record.msg)

        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: "[REDACTED]" if k.lower() in _SENSITIVE_FIELDS else v
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, (list, tuple)):
                record.args = tuple(
                    _scrub(str(a)) if isinstance(a, str) else a
                    for a in record.args
                )

        return True


def _scrub(text: str) -> str:
    """Replace sensitive field values in a log string with [REDACTED]."""
    return _SENSITIVE_PATTERN.sub(r'\1"[REDACTED]"', text)


# ─────────────────────────────────────────────────────────────────────────────
# B22 — Request ID injection — injects request_id into every log record
# ─────────────────────────────────────────────────────────────────────────────
class RequestIdFilter(logging.Filter):
    """
    Injects the current request_id (from ContextVar) into every LogRecord
    so the formatter can include %(request_id)s in every line.
    """

    def __init__(self, request_id_var: ContextVar[str]) -> None:
        super().__init__()
        self._var = request_id_var

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = self._var.get("-")  # type: ignore[attr-defined]
        return True


# ─────────────────────────────────────────────────────────────────────────────
# Structured log helpers — emit machine-readable entries for key events
# ─────────────────────────────────────────────────────────────────────────────
_kyc_logger = logging.getLogger("easypay.kyc")
_admin_logger = logging.getLogger("easypay.admin")
_fraud_logger = logging.getLogger("easypay.fraud")


def log_kyc_decision(
    *,
    user_id: int,
    decision: str,
    confidence: float | None = None,
    outcome: str,
    reviewed_by: str = "admin",
    extra: dict[str, Any] | None = None,
) -> None:
    """LOG every KYC decision: timestamp + user_id + confidence + outcome."""
    _kyc_logger.info(
        "KYC_DECISION | user_id=%s | decision=%s | confidence=%s | outcome=%s | reviewed_by=%s | ts=%s%s",
        user_id,
        decision,
        f"{confidence:.4f}" if confidence is not None else "N/A",
        outcome,
        reviewed_by,
        datetime.now(timezone.utc).isoformat(),
        f" | extra={extra}" if extra else "",
    )


def log_admin_action(
    *,
    admin_id: int,
    action: str,
    target: str,
    reason: str,
    extra: dict[str, Any] | None = None,
) -> None:
    """LOG every admin action: admin_id + action + target + reason."""
    _admin_logger.info(
        "ADMIN_ACTION | admin_id=%s | action=%s | target=%s | reason=%s | ts=%s%s",
        admin_id,
        action,
        target,
        reason,
        datetime.now(timezone.utc).isoformat(),
        f" | extra={extra}" if extra else "",
    )


def log_fraud_flag(
    *,
    user_id: int,
    rule_triggered: str,
    severity: str,
    risk_score: int,
    transaction_id: int | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """LOG every fraud flag creation: rule_triggered + severity."""
    _fraud_logger.warning(
        "FRAUD_FLAG | user_id=%s | rule=%s | severity=%s | risk_score=%s | txn_id=%s | ts=%s%s",
        user_id,
        rule_triggered,
        severity,
        risk_score,
        transaction_id or "N/A",
        datetime.now(timezone.utc).isoformat(),
        f" | extra={extra}" if extra else "",
    )


# ─────────────────────────────────────────────────────────────────────────────
# configure_logging() — call once from main.py
# ─────────────────────────────────────────────────────────────────────────────
def configure_logging(request_id_var: ContextVar[str] | None = None) -> None:
    """
    Apply SensitiveDataFilter and (optionally) RequestIdFilter to all loggers.
    Call this once during application startup before first log statement.
    """
    scrubber = SensitiveDataFilter()
    root = logging.getLogger()
    root.addFilter(scrubber)

    if request_id_var is not None:
        rid_filter = RequestIdFilter(request_id_var)
        root.addFilter(rid_filter)

    for name in ("easypay", "easypay.kyc", "easypay.admin", "easypay.fraud"):
        lg = logging.getLogger(name)
        lg.addFilter(scrubber)
        if request_id_var is not None:
            lg.addFilter(RequestIdFilter(request_id_var))
