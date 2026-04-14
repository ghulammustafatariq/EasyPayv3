"""
app/core/deepseek.py — EasyPay v3.0 DeepSeek AI Service Client

Rules Enforced:
  Point 6 — Strip ALL markdown fences before json.loads().
             Pattern: text.replace("```json", "").replace("```", "").strip()
             Applies to EVERY response from DeepSeek — never skip this.
  Rule 5  — All external HTTP calls use httpx.AsyncClient with proper
             timeouts. No requests.get() / sync httpx.
  Rule 6  — API key sent as Bearer token in Authorization header.
"""
import json
import logging
from typing import Any

import httpx

from app.core.config import settings
from app.core.exceptions import AIServiceUnavailableError  # central exception hierarchy

logger = logging.getLogger(__name__)

# Default timeouts (seconds): connect=10, read/write=30, pool=5
_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=5.0)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _strip_markdown_fences(text: str) -> str:
    """
    Remove ```json and ``` markdown code fences from a DeepSeek response.

    Point 6: This MUST be applied before every json.loads() call.
    Stripping is intentionally applied even when no fences are present —
    the operation is idempotent and avoids branching logic.
    """
    return text.replace("```json", "").replace("```", "").strip()


def _parse_response(text: str) -> dict[str, Any]:
    """Strip fences and parse JSON.  Raises AIServiceUnavailableError on failure."""
    cleaned = _strip_markdown_fences(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.error("DeepSeek JSON parse error: %s | raw: %.200s", exc, text)
        raise AIServiceUnavailableError(
            f"DeepSeek returned non-JSON response: {exc}"
        ) from exc


def _auth_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }


# ── Public API ────────────────────────────────────────────────────────────────

async def call_deepseek_chat(prompt: str, json_mode: bool = True) -> dict[str, Any]:
    """
    Send a chat completion request to DeepSeek.

    Args:
        prompt:    User-facing prompt (system context should be embedded here).
        json_mode: When True, sets response_format to {"type": "json_object"}
                   and strips markdown fences from the reply (Point 6).

    Returns:
        Parsed JSON dict from the model response.

    Raises:
        AIServiceUnavailableError — network error, non-200 status, or bad JSON.
    """
    payload: dict[str, Any] = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{settings.DEEPSEEK_BASE_URL}/v1/chat/completions",
                headers=_auth_headers(),
                json=payload,
            )
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.error("DeepSeek HTTP error %s: %s", exc.response.status_code, exc.response.text[:500])
        raise AIServiceUnavailableError(
            f"DeepSeek returned HTTP {exc.response.status_code}"
        ) from exc
    except httpx.RequestError as exc:
        logger.error("DeepSeek request error: %s", exc)
        raise AIServiceUnavailableError(f"Could not reach DeepSeek: {exc}") from exc

    raw_content: str = resp.json()["choices"][0]["message"]["content"]
    return _parse_response(raw_content)


async def call_deepseek_vision(image_base64: str, prompt: str) -> dict[str, Any]:
    """
    Send a single-image vision request to DeepSeek.

    Args:
        image_base64: Base64-encoded image string (without data URI prefix).
        prompt:       Instruction for the vision model (e.g. "Extract CNIC fields.").

    Returns:
        Parsed JSON dict from the vision model response.

    Raises:
        AIServiceUnavailableError — network error, non-200 status, or bad JSON.
    """
    payload: dict[str, Any] = {
        "model": "deepseek-chat",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_base64}"
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "response_format": {"type": "json_object"},
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{settings.DEEPSEEK_BASE_URL}/v1/chat/completions",
                headers=_auth_headers(),
                json=payload,
            )
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.error("DeepSeek vision HTTP error %s: %s", exc.response.status_code, exc.response.text[:500])
        raise AIServiceUnavailableError(
            f"DeepSeek vision returned HTTP {exc.response.status_code}"
        ) from exc
    except httpx.RequestError as exc:
        logger.error("DeepSeek vision request error: %s", exc)
        raise AIServiceUnavailableError(f"Could not reach DeepSeek vision: {exc}") from exc

    raw_content: str = resp.json()["choices"][0]["message"]["content"]
    return _parse_response(raw_content)


async def call_deepseek_vision_two_images(
    img1_base64: str,
    img2_base64: str,
    prompt: str,
) -> dict[str, Any]:
    """
    Send a two-image vision request to DeepSeek (e.g. live-face vs CNIC photo comparison).

    Args:
        img1_base64: Base64-encoded first image (e.g. live selfie).
        img2_base64: Base64-encoded second image (e.g. CNIC face crop).
        prompt:      Instruction for the model.

    Returns:
        Parsed JSON dict from the vision model response.

    Raises:
        AIServiceUnavailableError — network error, non-200 status, or bad JSON.
    """
    payload: dict[str, Any] = {
        "model": "deepseek-chat",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{img1_base64}"
                        },
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{img2_base64}"
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "response_format": {"type": "json_object"},
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{settings.DEEPSEEK_BASE_URL}/v1/chat/completions",
                headers=_auth_headers(),
                json=payload,
            )
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.error(
            "DeepSeek two-image vision HTTP error %s: %s",
            exc.response.status_code,
            exc.response.text[:500],
        )
        raise AIServiceUnavailableError(
            f"DeepSeek two-image vision returned HTTP {exc.response.status_code}"
        ) from exc
    except httpx.RequestError as exc:
        logger.error("DeepSeek two-image vision request error: %s", exc)
        raise AIServiceUnavailableError(
            f"Could not reach DeepSeek two-image vision: {exc}"
        ) from exc

    raw_content: str = resp.json()["choices"][0]["message"]["content"]
    return _parse_response(raw_content)
