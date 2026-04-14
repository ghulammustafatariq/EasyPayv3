from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.core.dependencies import get_current_verified_user, get_db
from app.models.database import User
from app.schemas.base import success_response
from app.schemas.trusted_circle import TrustedCircleSettingsRequest, AddContactRequest
from app.services import trusted_circle_service

router = APIRouter(prefix="/trusted-circle", tags=["Trusted Circle"])


@router.get("")
async def get_circle(
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """Get Trusted Circle settings and all contacts."""
    data = await trusted_circle_service.get_settings_with_contacts(db, current_user.id)
    return success_response(message="Trusted Circle retrieved", data=data)


@router.patch("/settings")
async def update_settings(
    data: TrustedCircleSettingsRequest,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """Update Trusted Circle on/off and rules."""
    await trusted_circle_service.update_settings(db, current_user.id, data)
    return success_response(message="Settings updated")


@router.post("/contacts")
async def add_contact(
    data: AddContactRequest,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """Add a contact to Trusted Circle by phone number."""
    contact = await trusted_circle_service.add_contact(db, current_user, data)
    return success_response(message="Contact added to Trusted Circle", data=contact)


@router.delete("/contacts/{contact_id}")
async def remove_contact(
    contact_id: UUID,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove a contact from Trusted Circle."""
    await trusted_circle_service.remove_contact(db, current_user.id, contact_id)
    return success_response(message="Contact removed from Trusted Circle")


@router.get("/check/{recipient_id}")
async def check_recipient(
    recipient_id: UUID,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Check if a recipient is in sender's Trusted Circle.
    Flutter calls this before showing the Send Money confirm button.
    """
    result = await trusted_circle_service.check_recipient_in_circle(
        db, current_user.id, recipient_id
    )
    return success_response(message="Circle check complete", data=result)
