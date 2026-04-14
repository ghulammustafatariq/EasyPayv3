from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.core.dependencies import get_current_verified_user, get_db
from app.models.database import User
from app.schemas.base import success_response
from app.schemas.hissa import CreateGroupRequest, AddExpenseRequest, AddMemberRequest
from app.services import hissa_service

router = APIRouter(prefix="/hissa", tags=["Hissa Collection"])


@router.get("")
async def get_my_groups(
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """List all Hissa groups the user is a member of."""
    groups = await hissa_service.get_my_groups(db, current_user.id)
    return success_response(message="Hissa groups retrieved", data={"groups": groups})


@router.post("")
async def create_group(
    data: CreateGroupRequest,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new Hissa group. Creator is auto-added as first member."""
    group = await hissa_service.create_group(db, current_user, data)
    return success_response(
        message="Group created",
        data={"id": str(group.id), "name": group.name},
    )


@router.get("/{group_id}")
async def get_group_detail(
    group_id: UUID,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """Full group detail: members, expenses, balances."""
    detail = await hissa_service.get_group_detail(db, group_id, current_user.id)
    return success_response(message="Group detail retrieved", data=detail)


@router.post("/{group_id}/expenses")
async def add_expense(
    group_id: UUID,
    data: AddExpenseRequest,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """Add an expense and recalculate member balances."""
    expense = await hissa_service.add_expense(db, group_id, current_user.id, data)
    return success_response(message="Expense added", data={"id": str(expense.id)})


@router.post("/{group_id}/members")
async def add_member(
    group_id: UUID,
    data: AddMemberRequest,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """Add a new member to the group by phone number."""
    await hissa_service.add_member(db, group_id, current_user.id, data)
    return success_response(message="Member added to group")


@router.patch("/{group_id}/settle")
async def mark_settled(
    group_id: UUID,
    current_user: User = Depends(get_current_verified_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark the group as fully settled. Only creator can do this."""
    await hissa_service.mark_settled(db, group_id, current_user.id)
    return success_response(message="Group marked as settled")
