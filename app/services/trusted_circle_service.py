"""Trusted Circle — whitelist contacts for money transfers."""
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.database import TrustedCircleSettings, TrustedCircleContact, User
from app.schemas.trusted_circle import TrustedCircleSettingsRequest, AddContactRequest


async def _get_or_create_settings(db: AsyncSession, user_id: UUID) -> TrustedCircleSettings:
    result = await db.execute(
        select(TrustedCircleSettings).where(TrustedCircleSettings.user_id == user_id)
    )
    settings = result.scalar_one_or_none()
    if not settings:
        settings = TrustedCircleSettings(user_id=user_id)
        db.add(settings)
        await db.flush()
    return settings


async def get_settings_with_contacts(db: AsyncSession, user_id: UUID) -> dict:
    settings = await _get_or_create_settings(db, user_id)

    result = await db.execute(
        select(TrustedCircleContact, User)
        .join(User, User.id == TrustedCircleContact.contact_id)
        .where(TrustedCircleContact.owner_id == user_id)
        .order_by(TrustedCircleContact.added_at.desc())
    )
    rows = result.all()

    contacts = [
        {
            "contact_id": str(row.TrustedCircleContact.contact_id),
            "full_name": row.User.full_name,
            "phone_number": row.User.phone_number,
            "verification_tier": row.User.verification_tier,
            "added_at": row.TrustedCircleContact.added_at,
        }
        for row in rows
    ]

    return {
        "is_enabled": settings.is_enabled,
        "require_pin_for_non_circle": settings.require_pin_for_non_circle,
        "notify_on_non_circle": settings.notify_on_non_circle,
        "max_non_circle_amount": settings.max_non_circle_amount,
        "contacts": contacts,
    }


async def update_settings(
    db: AsyncSession, user_id: UUID, data: TrustedCircleSettingsRequest
) -> TrustedCircleSettings:
    settings = await _get_or_create_settings(db, user_id)
    settings.is_enabled = data.is_enabled
    settings.require_pin_for_non_circle = data.require_pin_for_non_circle
    settings.notify_on_non_circle = data.notify_on_non_circle
    settings.max_non_circle_amount = data.max_non_circle_amount
    await db.commit()
    return settings


async def add_contact(db: AsyncSession, owner: User, data: AddContactRequest) -> dict:
    result = await db.execute(
        select(User).where(
            User.phone_number == data.phone_number,
            User.is_active == True,
        )
    )
    contact = result.scalar_one_or_none()
    if not contact:
        raise HTTPException(status_code=404, detail="No EasyPay user found with that phone number")
    if contact.id == owner.id:
        raise HTTPException(status_code=400, detail="You cannot add yourself to your Trusted Circle")

    existing = await db.execute(
        select(TrustedCircleContact).where(
            TrustedCircleContact.owner_id == owner.id,
            TrustedCircleContact.contact_id == contact.id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="This contact is already in your Trusted Circle")

    entry = TrustedCircleContact(owner_id=owner.id, contact_id=contact.id)
    db.add(entry)
    await db.commit()
    await db.refresh(entry)

    return {
        "contact_id": str(contact.id),
        "full_name": contact.full_name,
        "phone_number": contact.phone_number,
        "verification_tier": contact.verification_tier,
        "added_at": entry.added_at,
    }


async def remove_contact(db: AsyncSession, owner_id: UUID, contact_id: UUID) -> None:
    result = await db.execute(
        select(TrustedCircleContact).where(
            TrustedCircleContact.owner_id == owner_id,
            TrustedCircleContact.contact_id == contact_id,
        )
    )
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Contact not in your Trusted Circle")
    await db.delete(entry)
    await db.commit()


async def check_recipient_in_circle(
    db: AsyncSession, owner_id: UUID, recipient_id: UUID
) -> dict:
    """
    Called before a send_money operation to check Trusted Circle rules.
    Returns what action the frontend should take.
    """
    settings_result = await db.execute(
        select(TrustedCircleSettings).where(TrustedCircleSettings.user_id == owner_id)
    )
    settings = settings_result.scalar_one_or_none()

    if not settings or not settings.is_enabled:
        return {
            "is_in_circle": True,
            "circle_enabled": False,
            "requires_extra_confirmation": False,
            "max_allowed_amount": None,
        }

    contact_result = await db.execute(
        select(TrustedCircleContact).where(
            TrustedCircleContact.owner_id == owner_id,
            TrustedCircleContact.contact_id == recipient_id,
        )
    )
    is_in_circle = contact_result.scalar_one_or_none() is not None

    return {
        "is_in_circle": is_in_circle,
        "circle_enabled": True,
        "requires_extra_confirmation": not is_in_circle and settings.require_pin_for_non_circle,
        "max_allowed_amount": settings.max_non_circle_amount if not is_in_circle else None,
    }
