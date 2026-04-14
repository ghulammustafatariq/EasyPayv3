"""
app/services/ai_service.py — EasyPay v3.0 AI Service

B14 functionality:
  • Multi-turn AI chat  (DeepSeek conversation endpoint)
  • Financial insights  (DeepSeek single-prompt, 7-day cache)
  • Financial health score (extracted from cached insight)

Privacy rules enforced:
  Rule 3  — NEVER send password_hash, pin_hash, cnic_encrypted, phone, or
             full_name to DeepSeek.  Transaction data is masked to
             type + amount + date (YYYY-MM-DD) only.
  Point 6 — call_deepseek_chat() strips markdown fences internally;
             no extra stripping is needed for that call path.
"""
import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import settings
from app.core.deepseek import _TIMEOUT, _auth_headers, call_deepseek_chat
from app.core.exceptions import AIServiceUnavailableError
from app.models.database import AIInsight, ChatSession, Transaction, User, Wallet

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_CHAT_MODEL = "deepseek-chat"

_SYSTEM_PROMPT = (
    "You are EasyPay AI, a friendly and knowledgeable financial assistant "
    "embedded in the EasyPay Pakistani digital wallet app. "
    "You help users understand spending habits, answer questions about "
    "EasyPay features, and give practical money-saving advice. "
    "Currency is Pakistani Rupees (PKR). Keep answers concise and clear. "
    "If the user asks you to send money, transfer funds, or pay someone, "
    "extract the intent in EXACTLY this format on its own line at the end:\n"
    "ACTION:SEND_MONEY|amount:<numeric_amount>|recipient:<phone_or_name>"
)

_INSIGHT_CACHE_DAYS = 7
_CHAT_CONTEXT_LIMIT = 10   # last N messages from history sent to DeepSeek
_CHAT_TXN_LIMIT     = 30   # last N transactions for chat context
_INSIGHT_TXN_LIMIT  = 200  # last N transactions for insight generation (90 days)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _score_to_label(score: int | None) -> str:
    """Convert a numeric health score to a human-readable label."""
    if score is None:
        return "Unknown"
    if score >= 80:
        return "Excellent"
    if score >= 60:
        return "Good"
    if score >= 40:
        return "Fair"
    return "Poor"


async def _call_deepseek_conversation(messages: list[dict]) -> str:
    """
    Call DeepSeek /v1/chat/completions with a full messages array.

    Unlike call_deepseek_chat (single prompt → JSON mode), this helper
    is used for multi-turn conversation where JSON output is NOT required.

    Returns:
        Raw assistant text content string (not parsed to JSON).

    Raises:
        AIServiceUnavailableError — network failure, non-2xx status.
    """
    payload: dict[str, Any] = {
        "model": _CHAT_MODEL,
        "messages": messages,
        "temperature": 0.7,
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
            "DeepSeek HTTP error %s: %s",
            exc.response.status_code,
            exc.response.text[:500],
        )
        raise AIServiceUnavailableError(
            f"DeepSeek returned HTTP {exc.response.status_code}"
        ) from exc
    except httpx.RequestError as exc:
        logger.error("DeepSeek request error: %s", exc)
        raise AIServiceUnavailableError(f"Could not reach DeepSeek: {exc}") from exc

    return resp.json()["choices"][0]["message"]["content"]


async def _get_masked_transactions(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    days: int,
    limit: int,
) -> list[dict]:
    """
    Fetch transactions for a user and return ONLY type, amount, date.

    Rule 3: sender_id, recipient_id, reference_number, description,
    and external_ref MUST NOT be included in the returned data.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    result = await db.execute(
        select(Transaction.type, Transaction.amount, Transaction.created_at)
        .where(
            or_(
                Transaction.sender_id == user_id,
                Transaction.recipient_id == user_id,
            ),
            Transaction.created_at >= cutoff,
            Transaction.status == "completed",
        )
        .order_by(Transaction.created_at.desc())
        .limit(limit)
    )
    return [
        {
            "type": row.type,
            "amount": str(row.amount),
            "date": row.created_at.date().isoformat(),
        }
        for row in result.all()
    ]


# ── Insights ──────────────────────────────────────────────────────────────────

async def get_or_generate_insights(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """
    Return AI-generated financial insights for the user.

    Checks the ai_insights cache first.  If the record is missing or expired
    (or force_refresh=True), fetches the last 90 days of transactions, calls
    DeepSeek, and upserts the result with a 7-day expiry.

    Returns:
        insight_data dict with keys:
          health_score, health_label, top_categories,
          monthly_comparison, savings_tips, unusual_spending
    """
    now = datetime.now(timezone.utc)

    # ── 1. Cache lookup ────────────────────────────────────────────────────────
    result = await db.execute(
        select(AIInsight).where(AIInsight.user_id == user_id)
    )
    insight = result.scalar_one_or_none()

    if not force_refresh and insight and insight.expires_at > now:
        return insight.insight_data

    # ── 2. Fetch masked transactions (last 90 days) ───────────────────────────
    masked_txns = await _get_masked_transactions(
        db, user_id, days=90, limit=_INSIGHT_TXN_LIMIT
    )

    # ── 3. Build prompt and call DeepSeek ─────────────────────────────────────
    prompt = (
        "Analyze the following financial transactions from a Pakistani digital "
        "wallet user and return ONLY valid JSON matching this exact schema:\n"
        '{\n'
        '  "health_score": <integer 0-100>,\n'
        '  "health_label": "<Poor|Fair|Good|Excellent>",\n'
        '  "top_categories": [\n'
        '    {"category": "<transaction_type>", "amount": <number>, "percentage": <number 0-100>}\n'
        '  ],\n'
        '  "monthly_comparison": {\n'
        '    "current": <number>,\n'
        '    "previous": <number>,\n'
        '    "change_pct": <number>\n'
        '  },\n'
        '  "savings_tips": ["<actionable tip 1>", "<actionable tip 2>"],\n'
        '  "unusual_spending": ["<description if any anomaly found>"]\n'
        '}\n\n'
        f"Transactions (last 90 days, {len(masked_txns)} records):\n"
        f"{json.dumps(masked_txns)}"
    )

    data = await call_deepseek_chat(prompt, json_mode=True)

    # ── 4. Upsert ai_insights ─────────────────────────────────────────────────
    new_expiry = now + timedelta(days=_INSIGHT_CACHE_DAYS)
    score = int(data.get("health_score", 50))

    if insight:
        insight.insight_data = data
        insight.health_score = score
        insight.generated_at = now
        insight.expires_at = new_expiry
        flag_modified(insight, "insight_data")
    else:
        db.add(
            AIInsight(
                user_id=user_id,
                insight_data=data,
                health_score=score,
                generated_at=now,
                expires_at=new_expiry,
            )
        )

    await db.commit()
    return data


# ── Health score ──────────────────────────────────────────────────────────────

async def get_health_score(
    db: AsyncSession, user_id: uuid.UUID
) -> dict[str, Any]:
    """
    Return the cached health score.  Generates insights first if not cached.
    """
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(AIInsight).where(AIInsight.user_id == user_id)
    )
    insight = result.scalar_one_or_none()

    if insight and insight.expires_at > now:
        return {
            "health_score": insight.health_score,
            "health_label": _score_to_label(insight.health_score),
            "generated_at": insight.generated_at.isoformat(),
            "expires_at": insight.expires_at.isoformat(),
        }

    # Trigger generation so subsequent calls are cached
    data = await get_or_generate_insights(db, user_id)
    return {
        "health_score": data.get("health_score"),
        "health_label": data.get("health_label", _score_to_label(data.get("health_score"))),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=_INSIGHT_CACHE_DAYS)).isoformat(),
    }


# ── Chat ──────────────────────────────────────────────────────────────────────

async def send_chat_message(
    db: AsyncSession,
    user: User,
    message: str,
) -> dict[str, Any]:
    """
    Send a user message, get an AI reply, persist history, detect payment actions.

    Context supplied to DeepSeek (Rule 3 compliant):
      • First name only (first word of full_name — NOT the full name)
      • Wallet balance in PKR
      • Last 30 completed transactions: type, amount, date only
      • Last 10 messages from conversation history

    Returns:
        {"type": "message", "content": "..."}
        OR
        {"type": "payment_action", "content": "...", "amount": X, "recipient": "Y"}
    """
    user_id = user.id

    # ── 1. Wallet balance for context ─────────────────────────────────────────
    wallet_result = await db.execute(
        select(Wallet.balance).where(Wallet.user_id == user_id)
    )
    balance = wallet_result.scalar_one_or_none() or 0

    # ── 2. Last 30 completed transactions (masked) ────────────────────────────
    recent_txns = await _get_masked_transactions(
        db, user_id, days=365, limit=_CHAT_TXN_LIMIT
    )

    # ── 3. Load latest ChatSession (or create one) ────────────────────────────
    session_result = await db.execute(
        select(ChatSession)
        .where(ChatSession.user_id == user_id)
        .order_by(ChatSession.last_message_at.desc())
        .limit(1)
    )
    session = session_result.scalar_one_or_none()
    if not session:
        session = ChatSession(user_id=user_id, messages=[])
        db.add(session)
        await db.flush()  # get DB-assigned id before mutation

    # ── 4. Build messages payload ─────────────────────────────────────────────
    # Rule 3: use first word of full_name (not full_name itself)
    first_name = user.full_name.split()[0] if user.full_name else "User"

    system_content = (
        f"{_SYSTEM_PROMPT}\n\n"
        f"User: {first_name}. "
        f"Wallet balance: PKR {balance:,.2f}. "
        f"Recent transactions ({len(recent_txns)}): {json.dumps(recent_txns)}"
    )

    history: list[dict] = session.messages or []
    last_n = history[-_CHAT_CONTEXT_LIMIT:] if len(history) > _CHAT_CONTEXT_LIMIT else history

    messages = [
        {"role": "system", "content": system_content},
        *last_n,
        {"role": "user", "content": message},
    ]

    # ── 5. Call DeepSeek ──────────────────────────────────────────────────────
    reply: str = await _call_deepseek_conversation(messages)

    # ── 6. Detect payment action intent ──────────────────────────────────────
    action_match = re.search(
        r"ACTION:SEND_MONEY\|amount:(\d+(?:\.\d+)?)\|recipient:([^\s|]+)",
        reply,
        re.IGNORECASE,
    )

    # ── 7. Persist updated history ────────────────────────────────────────────
    updated_messages = list(history) + [
        {"role": "user", "content": message},
        {"role": "assistant", "content": reply},
    ]
    session.messages = updated_messages
    session.last_message_at = datetime.now(timezone.utc)
    flag_modified(session, "messages")
    await db.commit()

    # ── 8. Return ─────────────────────────────────────────────────────────────
    if action_match:
        # Strip the ACTION line from the visible reply
        clean_reply = reply[: action_match.start()].strip()
        return {
            "type": "payment_action",
            "content": clean_reply or "I'll help you send money.",
            "amount": float(action_match.group(1)),
            "recipient": action_match.group(2),
        }

    return {"type": "message", "content": reply}


# ── Chat history ──────────────────────────────────────────────────────────────

async def get_chat_history(
    db: AsyncSession, user_id: uuid.UUID
) -> list[dict]:
    """
    Return the messages list from the user's most recent ChatSession.
    Returns an empty list if no session exists.
    """
    result = await db.execute(
        select(ChatSession)
        .where(ChatSession.user_id == user_id)
        .order_by(ChatSession.last_message_at.desc())
        .limit(1)
    )
    session = result.scalar_one_or_none()
    return session.messages if session else []


async def clear_chat_history(
    db: AsyncSession, user_id: uuid.UUID
) -> None:
    """
    Delete all ChatSession records for the user (full conversation reset).
    """
    result = await db.execute(
        select(ChatSession).where(ChatSession.user_id == user_id)
    )
    sessions = result.scalars().all()
    for s in sessions:
        await db.delete(s)
    await db.commit()
