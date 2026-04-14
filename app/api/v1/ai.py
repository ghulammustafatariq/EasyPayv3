"""
app/api/v1/ai.py — EasyPay v3.0 AI Endpoints

Routes (B14):
  POST   /ai/chat            → 200  send message, get AI reply
  GET    /ai/chat/history    → 200  full conversation history
  DELETE /ai/chat/history    → 200  clear conversation history
  GET    /ai/insights        → 200  financial insights (7-day cache)
  GET    /ai/insights/refresh → 200  force-regenerate insights
  GET    /ai/health-score    → 200  financial health score

All routes require a verified JWT (get_current_verified_user).
"""
from fastapi import APIRouter, Body, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_verified_user, get_db
from app.models.database import User
from app.schemas.base import success_response
from app.services import ai_service

router = APIRouter(prefix="/ai", tags=["AI"])


# ══════════════════════════════════════════════════════════════════════════════
# POST /ai/chat
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/chat", status_code=status.HTTP_200_OK)
async def chat(
    message: str = Body(..., embed=True, min_length=1, max_length=1000),
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Send a message to the EasyPay AI assistant and receive a reply.

    The assistant maintains conversation context across calls using the
    stored ChatSession.  If the user's intent is to send money, the response
    will include a payment_action payload instead of a plain message.

    Response types:
      • {"type": "message",        "content": "..."}
      • {"type": "payment_action", "content": "...", "amount": X, "recipient": "Y"}
    """
    result = await ai_service.send_chat_message(db, current_user, message)
    return success_response(
        message="AI response generated successfully.",
        data=result,
    )


# ══════════════════════════════════════════════════════════════════════════════
# GET /ai/chat/history
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/chat/history", status_code=status.HTTP_200_OK)
async def get_chat_history(
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Retrieve the full conversation history for the authenticated user.

    Returns a list of message objects with role ('user' or 'assistant')
    and content.  Returns an empty list if no conversation has started.
    """
    messages = await ai_service.get_chat_history(db, current_user.id)
    return success_response(
        message="Chat history retrieved successfully.",
        data={"messages": messages, "count": len(messages)},
    )


# ══════════════════════════════════════════════════════════════════════════════
# DELETE /ai/chat/history
# ══════════════════════════════════════════════════════════════════════════════

@router.delete("/chat/history", status_code=status.HTTP_200_OK)
async def clear_chat_history(
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Delete the full conversation history for the authenticated user.

    All ChatSession records for this user are permanently removed.
    The next call to POST /ai/chat will start a fresh conversation.
    """
    await ai_service.clear_chat_history(db, current_user.id)
    return success_response(
        message="Chat history cleared successfully.",
        data={},
    )


# ══════════════════════════════════════════════════════════════════════════════
# GET /ai/insights
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/insights", status_code=status.HTTP_200_OK)
async def get_insights(
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Return AI-generated financial insights for the authenticated user.

    Results are cached for 7 days.  Use GET /ai/insights/refresh to
    force regeneration before the cache expires.

    Response includes:
      health_score, health_label, top_categories, monthly_comparison,
      savings_tips, unusual_spending
    """
    data = await ai_service.get_or_generate_insights(db, current_user.id)
    return success_response(
        message="Financial insights retrieved successfully.",
        data=data,
    )


# ══════════════════════════════════════════════════════════════════════════════
# GET /ai/insights/refresh
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/insights/refresh", status_code=status.HTTP_200_OK)
async def refresh_insights(
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Force-regenerate financial insights, bypassing the 7-day cache.

    Fetches the latest 90 days of transactions, calls DeepSeek, and
    refreshes the stored insight record with a new 7-day expiry.
    """
    data = await ai_service.get_or_generate_insights(
        db, current_user.id, force_refresh=True
    )
    return success_response(
        message="Financial insights refreshed successfully.",
        data=data,
    )


# ══════════════════════════════════════════════════════════════════════════════
# GET /ai/health-score
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/health-score", status_code=status.HTTP_200_OK)
async def get_health_score(
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Return the user's financial health score (0–100) with a label.

    Uses the cached insight if available; otherwise generates insights
    from the last 90 days of transactions before returning the score.

    Labels: Poor (0–39) | Fair (40–59) | Good (60–79) | Excellent (80–100)
    """
    data = await ai_service.get_health_score(db, current_user.id)
    return success_response(
        message="Financial health score retrieved successfully.",
        data=data,
    )
