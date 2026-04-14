"""
app/api/v1/admin.py — EasyPay v3.0 Admin Endpoints

Part 1 (B18) — Users + KYC:
  GET    /admin/users
  GET    /admin/users/{user_id}
  POST   /admin/users/{user_id}/block
  POST   /admin/users/{user_id}/unblock
  DELETE /admin/users/{user_id}
  PATCH  /admin/users/{user_id}/tier

  GET    /admin/kyc/pending
  GET    /admin/kyc/{user_id}/documents
  POST   /admin/kyc/{user_id}/approve
  POST   /admin/kyc/{user_id}/reject

Part 2 (B19) — Transactions + Fraud + Business:
  GET    /admin/transactions
  GET    /admin/transactions/flagged
  POST   /admin/transactions/{txn_id}/flag
  POST   /admin/transactions/{txn_id}/reverse
  GET    /admin/transactions/stats

  GET    /admin/business/under-review
  GET    /admin/business/{biz_id}
  POST   /admin/business/{biz_id}/approve
  POST   /admin/business/{biz_id}/reject

  GET    /admin/fraud/alerts
  POST   /admin/fraud/{flag_id}/resolve
  POST   /admin/fraud/{flag_id}/escalate

Part 3 (B20) — Dashboard + Broadcast:
  GET    /admin/dashboard/stats
  GET    /admin/dashboard/chart-data
  POST   /admin/announcements/broadcast

All routes require get_current_admin (is_superuser=True + X-Admin-Key header).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

import cloudinary.utils
from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.core.dependencies import (
    calculate_and_save_tier,
    get_current_admin,
    get_db,
    TIER_DAILY_LIMITS,
)
from app.core.exceptions import AdminSelfActionError, ReversalError
from app.models.database import (
    AdminAction,
    BusinessDocument,
    BusinessProfile,
    FraudFlag,
    SystemAnnouncement,
    Transaction,
    User,
    Wallet,
)
from app.schemas.base import success_response
from app.services import admin_service, notification_service

router = APIRouter(prefix="/admin", tags=["Admin"])


# ── Request models ─────────────────────────────────────────────────────────────

class AdminActionRequest(BaseModel):
    reason: str = Field(..., min_length=5, max_length=500)


class AdminTierOverrideRequest(BaseModel):
    tier: int = Field(..., ge=0, le=4)
    reason: str = Field(..., min_length=5, max_length=500)


class AdminKYCActionRequest(BaseModel):
    reason: str = Field(..., min_length=5, max_length=500)
    rejection_reasons: list[str] = Field(default_factory=list)


class AdminFlagTransactionRequest(BaseModel):
    reason: str = Field(..., min_length=5, max_length=500)


class AdminReversalRequest(BaseModel):
    reason: str = Field(..., min_length=5, max_length=500)


class AdminBizActionRequest(BaseModel):
    reason: str = Field(..., min_length=5, max_length=500)
    rejection_reasons: list[str] = Field(default_factory=list)


class BroadcastRequest(BaseModel):
    title: str = Field(..., min_length=3, max_length=100)
    body: str = Field(..., min_length=5, max_length=500)
    segment: str = Field(..., pattern="^(all|tier1_only|tier3_plus|business_only)$")


# ══════════════════════════════════════════════════════════════════════════════
# B18 PART 1 — USER MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/users", status_code=status.HTTP_200_OK)
async def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    tier: Optional[int] = Query(None, ge=0, le=4),
    is_active: Optional[bool] = Query(None),
    min_risk_score: Optional[int] = Query(None, ge=0, le=100),
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """
    Paginated user list with optional filters: tier, active status, risk_score.
    """
    stmt = select(User)
    if tier is not None:
        stmt = stmt.where(User.verification_tier == tier)
    if is_active is not None:
        stmt = stmt.where(User.is_active == is_active)
    if min_risk_score is not None:
        stmt = stmt.where(User.risk_score >= min_risk_score)

    # Count total for pagination
    count_result = await db.execute(select(func.count()).select_from(stmt.subquery()))
    total = count_result.scalar_one()

    # Apply pagination
    stmt = stmt.order_by(User.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    users = result.scalars().all()

    return success_response(
        message="Users retrieved successfully.",
        data={
            "users": [
                {
                    "id": str(u.id),
                    "phone_number": u.phone_number,
                    "full_name": u.full_name,
                    "email": u.email,
                    "is_active": u.is_active,
                    "is_locked": u.is_locked,
                    "verification_tier": u.verification_tier,
                    "risk_score": u.risk_score,
                    "is_flagged": u.is_flagged,
                    "account_type": u.account_type,
                    "business_status": u.business_status,
                    "created_at": u.created_at.isoformat(),
                }
                for u in users
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        },
    )


@router.get("/users/{user_id}", status_code=status.HTTP_200_OK)
async def get_user_detail(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """Full user profile including KYC flags, risk_score, and wallet balance."""
    result = await db.execute(
        select(User)
        .where(User.id == user_id)
        .options(joinedload(User.wallet), joinedload(User.business_profile))
    )
    user = result.scalar_one_or_none()
    if not user:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="User not found.")

    wallet = user.wallet
    return success_response(
        message="User retrieved successfully.",
        data={
            "id": str(user.id),
            "phone_number": user.phone_number,
            "full_name": user.full_name,
            "email": user.email,
            "is_active": user.is_active,
            "is_locked": user.is_locked,
            "is_superuser": user.is_superuser,
            "verification_tier": user.verification_tier,
            "account_type": user.account_type,
            "business_status": user.business_status,
            "cnic_verified": user.cnic_verified,
            "biometric_verified": user.biometric_verified,
            "fingerprint_verified": user.fingerprint_verified,
            "nadra_verified": user.nadra_verified,
            "risk_score": user.risk_score,
            "is_flagged": user.is_flagged,
            "flag_reason": user.flag_reason,
            "login_attempts": user.login_attempts,
            "created_at": user.created_at.isoformat(),
            "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
            "wallet": {
                "balance": str(wallet.balance),
                "is_frozen": wallet.is_frozen,
                "daily_limit": str(wallet.daily_limit),
                "daily_spent": str(wallet.daily_spent),
            } if wallet else None,
        },
    )


@router.post("/users/{user_id}/block", status_code=status.HTTP_200_OK)
async def block_user(
    user_id: uuid.UUID,
    body: AdminActionRequest,
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """Block a user account and freeze their wallet."""
    try:
        await admin_service.block_user(db, current_admin.id, user_id, body.reason)
    except AdminSelfActionError as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=str(exc))
    return success_response(message="User blocked successfully.", data={})


@router.post("/users/{user_id}/unblock", status_code=status.HTTP_200_OK)
async def unblock_user(
    user_id: uuid.UUID,
    body: AdminActionRequest,
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """
    Unblock a user account.
    Wallet is unfrozen only if no active Critical fraud flags remain.
    """
    await admin_service.unblock_user(db, current_admin.id, user_id, body.reason)
    return success_response(message="User unblocked successfully.", data={})


@router.delete("/users/{user_id}", status_code=status.HTTP_200_OK)
async def delete_user(
    user_id: uuid.UUID,
    body: AdminActionRequest,
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """Soft-delete a user (is_active=False). Data is preserved."""
    try:
        await admin_service.delete_user(db, current_admin.id, user_id, body.reason)
    except AdminSelfActionError as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=str(exc))
    return success_response(message="User deleted successfully.", data={})


@router.patch("/users/{user_id}/tier", status_code=status.HTTP_200_OK)
async def override_user_tier(
    user_id: uuid.UUID,
    body: AdminTierOverrideRequest,
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """Override a user's KYC verification tier and sync their daily wallet limit."""
    await admin_service.override_tier(db, current_admin.id, user_id, body.tier, body.reason)
    return success_response(message="User tier updated successfully.", data={"new_tier": body.tier})


# ══════════════════════════════════════════════════════════════════════════════
# B18 PART 1 — KYC REVIEW
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/kyc/pending", status_code=status.HTTP_200_OK)
async def list_pending_kyc(
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """
    List users who have completed CNIC + biometric verification and whose
    business account is pending/under_review admin review.
    """
    result = await db.execute(
        select(User)
        .where(
            User.cnic_verified == True,
            User.biometric_verified == True,
            User.business_status.in_(["pending", "under_review"]),
        )
        .order_by(User.created_at.asc())
    )
    users = result.scalars().all()

    return success_response(
        message="Pending KYC reviews retrieved.",
        data={
            "users": [
                {
                    "id": str(u.id),
                    "phone_number": u.phone_number,
                    "full_name": u.full_name,
                    "verification_tier": u.verification_tier,
                    "business_status": u.business_status,
                    "cnic_verified": u.cnic_verified,
                    "biometric_verified": u.biometric_verified,
                    "fingerprint_verified": u.fingerprint_verified,
                    "nadra_verified": u.nadra_verified,
                    "created_at": u.created_at.isoformat(),
                }
                for u in users
            ],
            "count": len(users),
        },
    )


@router.get("/kyc/{user_id}/documents", status_code=status.HTTP_200_OK)
async def get_kyc_documents(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """
    Return signed Cloudinary URLs (60-min expiry) for all KYC documents:
    cnic_front, cnic_back, liveness_selfie, and all business documents.
    All assets are private — signed URLs are required.
    """
    result = await db.execute(
        select(User)
        .where(User.id == user_id)
        .options(
            joinedload(User.business_profile).selectinload(BusinessProfile.documents)
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="User not found.")

    def _signed_url(public_id_or_url: str | None) -> str | None:
        """Generate a 60-minute signed URL for a private Cloudinary asset."""
        if not public_id_or_url:
            return None
        # Extract public_id from full URL if needed
        if public_id_or_url.startswith("http"):
            # Derive public_id from URL (strip base + extension)
            parts = public_id_or_url.split("/upload/")
            if len(parts) == 2:
                # Remove version prefix vXXXXXX/ if present and file extension
                path = parts[1]
                if path.startswith("v") and "/" in path:
                    path = path.split("/", 1)[1]
                public_id = path.rsplit(".", 1)[0]
            else:
                return public_id_or_url
        else:
            public_id = public_id_or_url

        signed, _ = cloudinary.utils.cloudinary_url(
            public_id,
            resource_type="image",
            type="private",
            sign_url=True,
            secure=True,
            expires_at=int(datetime.now(timezone.utc).timestamp()) + 3600,
        )
        return signed

    biz_docs = []
    if user.business_profile:
        for doc in user.business_profile.documents:
            biz_docs.append({
                "id": str(doc.id),
                "document_type": doc.document_type,
                "signed_url": _signed_url(doc.cloudinary_url),
                "ai_verdict": doc.ai_verdict,
                "is_valid": doc.is_valid,
                "confidence_score": str(doc.confidence_score) if doc.confidence_score else None,
                "uploaded_at": doc.uploaded_at.isoformat(),
            })

    return success_response(
        message="KYC documents retrieved.",
        data={
            "user_id": str(user_id),
            "cnic_front_url": _signed_url(user.cnic_front_url),
            "cnic_back_url": _signed_url(user.cnic_back_url),
            "liveness_selfie_url": _signed_url(user.liveness_selfie_url),
            "business_documents": biz_docs,
        },
    )


@router.post("/kyc/{user_id}/approve", status_code=status.HTTP_200_OK)
async def approve_kyc(
    user_id: uuid.UUID,
    body: AdminKYCActionRequest,
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """
    Approve a user's KYC.
    Sets cnic_verified=True, recalculates tier, sends FCM, logs action.
    """
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="User not found.")

    user.cnic_verified = True
    new_tier = await calculate_and_save_tier(db, user)

    # Log admin action
    action = AdminAction(
        admin_id=current_admin.id,
        action_type="approve_kyc",
        target_user_id=user_id,
        reason=body.reason,
        action_metadata={"new_tier": new_tier},
    )
    db.add(action)
    await db.commit()

    await notification_service.create_notification(
        db,
        user_id=user_id,
        title="KYC Approved",
        body=f"Your identity verification has been approved. You are now Tier {new_tier}.",
        type="system",
        data={"new_tier": new_tier},
    )

    return success_response(
        message="KYC approved successfully.",
        data={"user_id": str(user_id), "new_tier": new_tier},
    )


@router.post("/kyc/{user_id}/reject", status_code=status.HTTP_200_OK)
async def reject_kyc(
    user_id: uuid.UUID,
    body: AdminKYCActionRequest,
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """
    Reject a user's KYC submission.
    Sends FCM with rejection reasons and logs the admin action.
    """
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="User not found.")

    action = AdminAction(
        admin_id=current_admin.id,
        action_type="reject_kyc",
        target_user_id=user_id,
        reason=body.reason,
        action_metadata={"rejection_reasons": body.rejection_reasons},
    )
    db.add(action)
    await db.commit()

    reasons_text = "; ".join(body.rejection_reasons) if body.rejection_reasons else body.reason
    await notification_service.create_notification(
        db,
        user_id=user_id,
        title="KYC Rejected",
        body=f"Your identity verification was rejected. Reasons: {reasons_text}",
        type="system",
        data={"rejection_reasons": body.rejection_reasons},
    )

    return success_response(message="KYC rejected successfully.", data={})


# ══════════════════════════════════════════════════════════════════════════════
# B19 PART 2 — TRANSACTIONS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/transactions", status_code=status.HTTP_200_OK)
async def list_transactions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    type: Optional[str] = Query(None),
    flagged_only: bool = Query(False),
    min_amount: Optional[float] = Query(None),
    max_amount: Optional[float] = Query(None),
    date_from: Optional[str] = Query(None, description="ISO date YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="ISO date YYYY-MM-DD"),
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """Full transaction list with filters: type, flagged, amount range, date range."""
    stmt = select(Transaction)
    if type:
        stmt = stmt.where(Transaction.type == type)
    if flagged_only:
        stmt = stmt.where(Transaction.is_flagged == True)
    if min_amount is not None:
        stmt = stmt.where(Transaction.amount >= min_amount)
    if max_amount is not None:
        stmt = stmt.where(Transaction.amount <= max_amount)
    if date_from:
        stmt = stmt.where(Transaction.created_at >= datetime.fromisoformat(date_from).replace(tzinfo=timezone.utc))
    if date_to:
        stmt = stmt.where(Transaction.created_at <= datetime.fromisoformat(date_to).replace(tzinfo=timezone.utc))

    count_result = await db.execute(select(func.count()).select_from(stmt.subquery()))
    total = count_result.scalar_one()

    stmt = stmt.order_by(Transaction.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    txns = result.scalars().all()

    return success_response(
        message="Transactions retrieved.",
        data={
            "transactions": [
                {
                    "id": str(t.id),
                    "reference_number": t.reference_number,
                    "type": t.type,
                    "amount": str(t.amount),
                    "fee": str(t.fee),
                    "status": t.status,
                    "is_flagged": t.is_flagged,
                    "flag_reason": t.flag_reason,
                    "sender_id": str(t.sender_id) if t.sender_id else None,
                    "recipient_id": str(t.recipient_id) if t.recipient_id else None,
                    "created_at": t.created_at.isoformat(),
                }
                for t in txns
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        },
    )


@router.get("/transactions/flagged", status_code=status.HTTP_200_OK)
async def list_flagged_transactions(
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """Return all transactions where is_flagged=True, newest first."""
    result = await db.execute(
        select(Transaction)
        .where(Transaction.is_flagged == True)
        .order_by(Transaction.created_at.desc())
    )
    txns = result.scalars().all()
    return success_response(
        message="Flagged transactions retrieved.",
        data={
            "transactions": [
                {
                    "id": str(t.id),
                    "reference_number": t.reference_number,
                    "type": t.type,
                    "amount": str(t.amount),
                    "status": t.status,
                    "flag_reason": t.flag_reason,
                    "sender_id": str(t.sender_id) if t.sender_id else None,
                    "created_at": t.created_at.isoformat(),
                }
                for t in txns
            ],
            "count": len(txns),
        },
    )


@router.post("/transactions/{txn_id}/flag", status_code=status.HTTP_200_OK)
async def flag_transaction(
    txn_id: uuid.UUID,
    body: AdminFlagTransactionRequest,
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """Manually flag a transaction as suspicious."""
    result = await db.execute(select(Transaction).where(Transaction.id == txn_id))
    txn = result.scalar_one_or_none()
    if not txn:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Transaction not found.")

    txn.is_flagged = True
    txn.flag_reason = body.reason
    txn.flagged_by = current_admin.id
    txn.flagged_at = datetime.now(timezone.utc)

    action = AdminAction(
        admin_id=current_admin.id,
        action_type="flag_transaction",
        target_txn_id=txn_id,
        target_user_id=txn.sender_id,
        reason=body.reason,
        action_metadata={"amount": str(txn.amount), "reference": txn.reference_number},
    )
    db.add(action)
    await db.commit()

    return success_response(message="Transaction flagged.", data={"transaction_id": str(txn_id)})


@router.post("/transactions/{txn_id}/reverse", status_code=status.HTTP_200_OK)
async def reverse_transaction(
    txn_id: uuid.UUID,
    body: AdminReversalRequest,
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """
    Reverse a completed transaction.
    Uses db.begin() + with_for_update() to prevent double-reversal (Point 18).
    """
    try:
        result = await admin_service.reverse_transaction(db, current_admin.id, txn_id, body.reason)
    except ReversalError as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=str(exc))
    return success_response(message="Transaction reversed successfully.", data=result)


@router.get("/transactions/stats", status_code=status.HTTP_200_OK)
async def transaction_stats(
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """Return transaction volume aggregated by day, week, and month, plus total fees."""
    now = datetime.now(timezone.utc)

    async def _volume(since: datetime) -> dict:
        r = await db.execute(
            select(
                func.count(Transaction.id),
                func.coalesce(func.sum(Transaction.amount), 0),
                func.coalesce(func.sum(Transaction.fee), 0),
            ).where(
                Transaction.created_at >= since,
                Transaction.status == "completed",
            )
        )
        count, volume, fees = r.one()
        return {"count": count, "volume": str(volume), "fees": str(fees)}

    day = await _volume(now.replace(hour=0, minute=0, second=0, microsecond=0))
    week = await _volume(now - __import__("datetime").timedelta(days=7))
    month = await _volume(now - __import__("datetime").timedelta(days=30))

    return success_response(
        message="Transaction stats retrieved.",
        data={"today": day, "last_7_days": week, "last_30_days": month},
    )


# ══════════════════════════════════════════════════════════════════════════════
# B19 PART 2 — BUSINESS REVIEW
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/business/under-review", status_code=status.HTTP_200_OK)
async def list_business_under_review(
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """List all business profiles pending or under admin review."""
    result = await db.execute(
        select(BusinessProfile)
        .where(BusinessProfile.verification_status.in_(["pending", "under_review"]))
        .options(joinedload(BusinessProfile.user))
        .order_by(BusinessProfile.submitted_at.asc())
    )
    profiles = result.scalars().all()

    return success_response(
        message="Business profiles under review retrieved.",
        data={
            "profiles": [
                {
                    "id": str(p.id),
                    "user_id": str(p.user_id),
                    "business_name": p.business_name,
                    "business_type": p.business_type,
                    "verification_status": p.verification_status,
                    "ai_confidence_score": str(p.ai_confidence_score) if p.ai_confidence_score else None,
                    "submitted_at": p.submitted_at.isoformat(),
                    "owner_phone": p.user.phone_number if p.user else None,
                }
                for p in profiles
            ],
            "count": len(profiles),
        },
    )


@router.get("/business/{biz_id}", status_code=status.HTTP_200_OK)
async def get_business_detail(
    biz_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """Full business profile with all documents, AI verdicts, and owner info."""
    result = await db.execute(
        select(BusinessProfile)
        .where(BusinessProfile.id == biz_id)
        .options(
            joinedload(BusinessProfile.user),
            selectinload(BusinessProfile.documents),
        )
    )
    profile = result.scalar_one_or_none()
    if not profile:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Business profile not found.")

    return success_response(
        message="Business profile retrieved.",
        data={
            "id": str(profile.id),
            "user_id": str(profile.user_id),
            "business_name": profile.business_name,
            "business_type": profile.business_type,
            "ntn_number": profile.ntn_number,
            "business_address": profile.business_address,
            "verification_status": profile.verification_status,
            "ai_confidence_score": str(profile.ai_confidence_score) if profile.ai_confidence_score else None,
            "rejection_reasons": profile.rejection_reasons,
            "submitted_at": profile.submitted_at.isoformat(),
            "reviewed_at": profile.reviewed_at.isoformat() if profile.reviewed_at else None,
            "documents": [
                {
                    "id": str(d.id),
                    "document_type": d.document_type,
                    "cloudinary_url": d.cloudinary_url,
                    "ai_verdict": d.ai_verdict,
                    "is_valid": d.is_valid,
                    "confidence_score": str(d.confidence_score) if d.confidence_score else None,
                    "uploaded_at": d.uploaded_at.isoformat(),
                }
                for d in profile.documents
            ],
            "owner": {
                "phone_number": profile.user.phone_number,
                "full_name": profile.user.full_name,
                "email": profile.user.email,
            } if profile.user else None,
        },
    )


@router.post("/business/{biz_id}/approve", status_code=status.HTTP_200_OK)
async def approve_business(
    biz_id: uuid.UUID,
    body: AdminBizActionRequest,
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """Approve a business profile, upgrade user tier, notify via FCM, log action."""
    result = await db.execute(
        select(BusinessProfile)
        .where(BusinessProfile.id == biz_id)
        .options(joinedload(BusinessProfile.user).joinedload(User.wallet))
    )
    profile = result.scalar_one_or_none()
    if not profile:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Business profile not found.")

    now = datetime.now(timezone.utc)
    profile.verification_status = "approved"
    profile.reviewed_at = now
    profile.reviewed_by = current_admin.id

    user = profile.user
    user.business_status = "approved"
    new_tier = await calculate_and_save_tier(db, user)

    action = AdminAction(
        admin_id=current_admin.id,
        action_type="approve_business",
        target_user_id=profile.user_id,
        target_biz_id=biz_id,
        reason=body.reason,
        action_metadata={"new_tier": new_tier},
    )
    db.add(action)
    await db.commit()

    await notification_service.create_notification(
        db,
        user_id=profile.user_id,
        title="Business Verified",
        body=f"Your business '{profile.business_name}' has been approved. You are now Tier {new_tier}.",
        type="system",
        data={"new_tier": new_tier, "business_id": str(biz_id)},
    )

    return success_response(
        message="Business approved successfully.",
        data={"business_id": str(biz_id), "new_tier": new_tier},
    )


@router.post("/business/{biz_id}/reject", status_code=status.HTTP_200_OK)
async def reject_business(
    biz_id: uuid.UUID,
    body: AdminBizActionRequest,
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """Reject a business profile with reasons, notify via FCM, log action."""
    result = await db.execute(
        select(BusinessProfile).where(BusinessProfile.id == biz_id)
    )
    profile = result.scalar_one_or_none()
    if not profile:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Business profile not found.")

    now = datetime.now(timezone.utc)
    profile.verification_status = "rejected"
    profile.reviewed_at = now
    profile.reviewed_by = current_admin.id
    profile.rejection_reasons = body.rejection_reasons

    result2 = await db.execute(select(User).where(User.id == profile.user_id))
    user = result2.scalar_one_or_none()
    if user:
        user.business_status = "rejected"

    action = AdminAction(
        admin_id=current_admin.id,
        action_type="reject_business",
        target_user_id=profile.user_id,
        target_biz_id=biz_id,
        reason=body.reason,
        action_metadata={"rejection_reasons": body.rejection_reasons},
    )
    db.add(action)
    await db.commit()

    reasons_text = "; ".join(body.rejection_reasons) if body.rejection_reasons else body.reason
    await notification_service.create_notification(
        db,
        user_id=profile.user_id,
        title="Business Verification Rejected",
        body=f"Your business verification was rejected. Reasons: {reasons_text}",
        type="system",
        data={"rejection_reasons": body.rejection_reasons, "business_id": str(biz_id)},
    )

    return success_response(message="Business rejected.", data={})


# ══════════════════════════════════════════════════════════════════════════════
# B19 PART 2 — FRAUD MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/fraud/alerts", status_code=status.HTTP_200_OK)
async def list_fraud_alerts(
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """All active fraud flags sorted by severity DESC, created_at DESC."""
    from app.services import fraud_service
    flags = await fraud_service.get_active_flags(db)
    return success_response(
        message="Fraud alerts retrieved.",
        data={"alerts": flags, "count": len(flags)},
    )


@router.post("/fraud/{flag_id}/resolve", status_code=status.HTTP_200_OK)
async def resolve_fraud_flag(
    flag_id: uuid.UUID,
    body: AdminActionRequest,
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """Mark a fraud flag as resolved and log the admin action."""
    result = await db.execute(select(FraudFlag).where(FraudFlag.id == flag_id))
    flag = result.scalar_one_or_none()
    if not flag:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Fraud flag not found.")

    now = datetime.now(timezone.utc)
    flag.status = "resolved"
    flag.resolved_by = current_admin.id
    flag.resolved_at = now

    action = AdminAction(
        admin_id=current_admin.id,
        action_type="resolve_fraud",
        target_user_id=flag.user_id,
        reason=body.reason,
        action_metadata={"flag_id": str(flag_id), "rule": flag.rule_triggered},
    )
    db.add(action)
    await db.commit()

    return success_response(message="Fraud flag resolved.", data={"flag_id": str(flag_id)})


@router.post("/fraud/{flag_id}/escalate", status_code=status.HTTP_200_OK)
async def escalate_fraud_flag(
    flag_id: uuid.UUID,
    body: AdminActionRequest,
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """
    Escalate a fraud flag: mark as escalated and block the user immediately.
    """
    result = await db.execute(select(FraudFlag).where(FraudFlag.id == flag_id))
    flag = result.scalar_one_or_none()
    if not flag:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Fraud flag not found.")

    flag.status = "escalated"

    action = AdminAction(
        admin_id=current_admin.id,
        action_type="escalate_fraud",
        target_user_id=flag.user_id,
        reason=body.reason,
        action_metadata={"flag_id": str(flag_id), "rule": flag.rule_triggered, "severity": flag.severity},
    )
    db.add(action)
    await db.flush()
    await db.commit()

    # Block the user (freeze wallet + notify)
    try:
        await admin_service.block_user(
            db,
            current_admin.id,
            flag.user_id,
            reason=f"Fraud escalation: {flag.rule_triggered} — {body.reason}",
        )
    except AdminSelfActionError:
        pass  # Edge case: admin is flagged — skip block

    return success_response(
        message="Fraud flag escalated and user blocked.",
        data={"flag_id": str(flag_id), "user_id": str(flag.user_id)},
    )


# ══════════════════════════════════════════════════════════════════════════════
# B20 PART 3 — DASHBOARD + BROADCAST
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/dashboard/stats", status_code=status.HTTP_200_OK)
async def dashboard_stats(
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """Real-time system metrics: users, transactions, fraud alerts, total balance."""
    stats = await admin_service.get_dashboard_stats(db)
    return success_response(message="Dashboard stats retrieved.", data=stats)


@router.get("/dashboard/chart-data", status_code=status.HTTP_200_OK)
async def dashboard_chart_data(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """Daily transaction count and volume for the last N days."""
    chart = await admin_service.get_chart_data(db, days=days)
    return success_response(message="Chart data retrieved.", data={"chart": chart})


@router.post("/announcements/broadcast", status_code=status.HTTP_200_OK)
async def broadcast_announcement(
    body: BroadcastRequest,
    db: AsyncSession = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """
    Broadcast a push notification to a user segment.
    Segments: all | tier1_only | tier3_plus | business_only
    Saves to system_announcements and logs to admin_actions.
    """
    # Build query for target segment
    stmt = select(User).where(User.is_active == True, User.fcm_token.isnot(None))
    if body.segment == "tier1_only":
        stmt = stmt.where(User.verification_tier == 1)
    elif body.segment == "tier3_plus":
        stmt = stmt.where(User.verification_tier >= 3)
    elif body.segment == "business_only":
        stmt = stmt.where(User.account_type == "business")
    # "all" — no extra filter

    result = await db.execute(stmt)
    target_users = result.scalars().all()

    # Send notification to each user
    for user in target_users:
        await notification_service.create_notification(
            db,
            user_id=user.id,
            title=body.title,
            body=body.body,
            type="admin",
            data={"segment": body.segment},
        )

    recipient_count = len(target_users)

    # Save system announcement record
    announcement = SystemAnnouncement(
        admin_id=current_admin.id,
        title=body.title,
        body=body.body,
        segment=body.segment,
        recipient_count=recipient_count,
    )
    db.add(announcement)

    # Log admin action
    action = AdminAction(
        admin_id=current_admin.id,
        action_type="broadcast_notification",
        reason=f"Broadcast to segment: {body.segment}",
        action_metadata={
            "title": body.title,
            "segment": body.segment,
            "recipient_count": recipient_count,
        },
    )
    db.add(action)
    await db.commit()

    return success_response(
        message="Broadcast sent successfully.",
        data={"sent_to": recipient_count, "segment": body.segment},
    )
