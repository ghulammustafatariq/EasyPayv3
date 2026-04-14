"""
app/services/fcm_service.py — EasyPay v3.0 Firebase Cloud Messaging Service

Critical Rules Enforced:
  Point 7 — FCM_SERVICE_ACCOUNT_JSON is complete JSON as single-line string in .env
             Parsed at runtime via settings.fcm_service_account (never split env vars)
  Rule  5 — All external HTTP calls use httpx.AsyncClient with timeouts.
             NEVER uses requests.get() or sync httpx.
  B13    — send_push_notification() NEVER crashes its caller.
             Full try/except — log error and return False silently.
             FCM failure must NEVER block the DB save or the response.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx
from google.oauth2 import service_account
from google.auth.transport.requests import Request as GoogleAuthRequest

from app.core.config import settings

logger = logging.getLogger(__name__)

# FCM v1 API endpoint
_FCM_SEND_URL = (
    "https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
)

# Required OAuth2 scope for FCM v1
_FCM_SCOPES = ["https://www.googleapis.com/auth/firebase.messaging"]

# httpx timeout
_TIMEOUT = httpx.Timeout(connect=10.0, read=15.0, write=10.0, pool=5.0)


def _get_fcm_access_token() -> str | None:
    """
    Use the service account credentials to obtain a short-lived FCM bearer token.
    Returns None if the service account JSON is empty / invalid (dev mode).

    Point 7: json.loads() is done inside settings.fcm_service_account property.
    This function is synchronous (google-auth Credentials.refresh is sync).
    """
    sa_info = settings.fcm_service_account  # parsed dict from .env
    if not sa_info or sa_info.get("type") != "service_account":
        logger.warning("FCM service account not configured — push notifications disabled.")
        return None

    try:
        credentials = service_account.Credentials.from_service_account_info(
            sa_info, scopes=_FCM_SCOPES
        )
        credentials.refresh(GoogleAuthRequest())
        return credentials.token
    except Exception as exc:
        logger.error("FCM token refresh failed: %s", exc)
        return None


async def send_push_notification(
    device_token: str,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
) -> bool:
    """
    Send an FCM v1 push notification to a single device.

    Args:
        device_token: The Android/iOS FCM registration token from User.fcm_token.
        title:        Notification title (shown in system tray).
        body:         Notification body text.
        data:         Optional key-value dict for silent/data messages.

    Returns:
        True if the push was accepted by FCM, False on any error.

    CRITICAL: This function NEVER raises. All failures are logged and False is
    returned so the caller (notification_service) can continue unaffected.
    """
    if not device_token:
        return False

    access_token = _get_fcm_access_token()
    if access_token is None:
        return False  # FCM not configured — skip silently

    project_id = settings.FCM_PROJECT_ID
    url = _FCM_SEND_URL.format(project_id=project_id)

    # Build FCM v1 message payload
    message: dict[str, Any] = {
        "message": {
            "token": device_token,
            "notification": {
                "title": title,
                "body": body,
            },
        }
    }
    if data:
        # FCM data payload values must all be strings
        message["message"]["data"] = {k: str(v) for k, v in data.items()}

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(url, headers=headers, json=message)
            if response.status_code == 200:
                logger.info(
                    "FCM push sent successfully to token ...%s | title=%r",
                    device_token[-8:],
                    title,
                )
                return True
            else:
                logger.error(
                    "FCM push failed — HTTP %s: %s",
                    response.status_code,
                    response.text[:300],
                )
                return False
    except Exception as exc:
        logger.error("FCM push exception (non-fatal): %s", exc)
        return False
