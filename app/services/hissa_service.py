"""Hissa Collection — group expense splitting."""
from decimal import Decimal
from uuid import UUID
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.models.database import HissaGroup, HissaGroupMember, HissaExpense, User
from app.schemas.hissa import CreateGroupRequest, AddExpenseRequest, AddMemberRequest


async def _get_group_or_404(
    db: AsyncSession, group_id: UUID, user_id: UUID
) -> HissaGroup:
    result = await db.execute(
        select(HissaGroup).where(HissaGroup.id == group_id)
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    member_result = await db.execute(
        select(HissaGroupMember).where(
            HissaGroupMember.group_id == group_id,
            HissaGroupMember.user_id == user_id,
        )
    )
    if not member_result.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="You are not a member of this group")

    return group


async def create_group(
    db: AsyncSession, creator: User, data: CreateGroupRequest
) -> HissaGroup:
    group = HissaGroup(
        name=data.name,
        emoji=data.emoji,
        creator_id=creator.id,
    )
    db.add(group)
    await db.flush()

    # Auto-add creator as member
    db.add(HissaGroupMember(group_id=group.id, user_id=creator.id))

    # Add invited members by phone
    for phone in data.member_phones:
        result = await db.execute(
            select(User).where(User.phone_number == phone, User.is_active == True)
        )
        member_user = result.scalar_one_or_none()
        if member_user and member_user.id != creator.id:
            db.add(HissaGroupMember(group_id=group.id, user_id=member_user.id))

    await db.commit()
    await db.refresh(group)
    return group


async def get_my_groups(db: AsyncSession, user_id: UUID) -> list[dict]:
    result = await db.execute(
        select(HissaGroupMember, HissaGroup)
        .join(HissaGroup, HissaGroup.id == HissaGroupMember.group_id)
        .where(HissaGroupMember.user_id == user_id)
        .order_by(HissaGroup.created_at.desc())
    )
    rows = result.all()

    groups = []
    for row in rows:
        group = row.HissaGroup
        member = row.HissaGroupMember

        count_result = await db.execute(
            select(func.count()).where(HissaGroupMember.group_id == group.id)
        )
        member_count = count_result.scalar()

        total_result = await db.execute(
            select(func.coalesce(func.sum(HissaExpense.amount), 0))
            .where(HissaExpense.group_id == group.id)
        )
        total_expenses = Decimal(str(total_result.scalar()))

        groups.append({
            "id": str(group.id),
            "name": group.name,
            "emoji": group.emoji,
            "member_count": member_count,
            "total_expenses": total_expenses,
            "my_net_balance": member.net_balance,
            "is_settled": group.is_settled,
            "created_at": group.created_at,
        })

    return groups


async def get_group_detail(
    db: AsyncSession, group_id: UUID, user_id: UUID
) -> dict:
    group = await _get_group_or_404(db, group_id, user_id)

    members_result = await db.execute(
        select(HissaGroupMember, User)
        .join(User, User.id == HissaGroupMember.user_id)
        .where(HissaGroupMember.group_id == group_id)
    )
    members_rows = members_result.all()

    members = [
        {
            "user_id": str(row.User.id),
            "full_name": row.User.full_name,
            "phone_number": row.User.phone_number,
            "net_balance": row.HissaGroupMember.net_balance,
        }
        for row in members_rows
    ]

    expenses_result = await db.execute(
        select(HissaExpense, User)
        .join(User, User.id == HissaExpense.paid_by_id)
        .where(HissaExpense.group_id == group_id)
        .order_by(HissaExpense.created_at.desc())
    )
    expense_rows = expenses_result.all()

    expenses = [
        {
            "id": str(row.HissaExpense.id),
            "title": row.HissaExpense.title,
            "amount": row.HissaExpense.amount,
            "paid_by_id": str(row.HissaExpense.paid_by_id),
            "paid_by_name": row.User.full_name,
            "split_type": row.HissaExpense.split_type,
            "split_data": row.HissaExpense.split_data,
            "created_at": row.HissaExpense.created_at,
        }
        for row in expense_rows
    ]

    total_result = await db.execute(
        select(func.coalesce(func.sum(HissaExpense.amount), 0))
        .where(HissaExpense.group_id == group_id)
    )
    total_expenses = Decimal(str(total_result.scalar()))

    return {
        "id": str(group.id),
        "name": group.name,
        "emoji": group.emoji,
        "creator_id": str(group.creator_id),
        "is_settled": group.is_settled,
        "members": members,
        "expenses": expenses,
        "total_expenses": total_expenses,
        "created_at": group.created_at,
    }


async def add_expense(
    db: AsyncSession, group_id: UUID, user_id: UUID, data: AddExpenseRequest
) -> HissaExpense:
    group = await _get_group_or_404(db, group_id, user_id)

    if group.is_settled:
        raise HTTPException(status_code=400, detail="Cannot add expense to a settled group")

    members_result = await db.execute(
        select(HissaGroupMember).where(HissaGroupMember.group_id == group_id)
    )
    members = members_result.scalars().all()
    member_map = {str(m.user_id): m for m in members}

    if str(data.paid_by_id) not in member_map:
        raise HTTPException(status_code=400, detail="paid_by user is not a member of this group")

    shares: dict[str, Decimal] = {}

    if data.split_type == "equal":
        per_person = (data.amount / len(members)).quantize(Decimal("0.01"))
        for m in members:
            shares[str(m.user_id)] = per_person

    elif data.split_type == "custom":
        if not data.split_data:
            raise HTTPException(status_code=400, detail="split_data required for custom split")
        total_assigned = sum(Decimal(str(v)) for v in data.split_data.values())
        if abs(total_assigned - data.amount) > Decimal("1.00"):
            raise HTTPException(
                status_code=400,
                detail=f"Custom split amounts ({total_assigned}) must sum to expense amount ({data.amount})",
            )
        shares = {k: Decimal(str(v)) for k, v in data.split_data.items()}

    elif data.split_type == "percentage":
        if not data.split_data:
            raise HTTPException(status_code=400, detail="split_data required for percentage split")
        total_pct = sum(Decimal(str(v)) for v in data.split_data.values())
        if abs(total_pct - 100) > Decimal("0.01"):
            raise HTTPException(status_code=400, detail="Percentages must sum to 100")
        shares = {
            k: (data.amount * Decimal(str(v)) / 100).quantize(Decimal("0.01"))
            for k, v in data.split_data.items()
        }

    else:
        raise HTTPException(status_code=400, detail="split_type must be equal, custom, or percentage")

    payer_id = str(data.paid_by_id)
    async with db.begin_nested():
        for uid_str, share in shares.items():
            if uid_str not in member_map:
                continue
            member = member_map[uid_str]
            if uid_str == payer_id:
                # Payer paid the whole amount, deduct their own share
                member.net_balance += (data.amount - share)
            else:
                # Others owe their share
                member.net_balance -= share

        expense = HissaExpense(
            group_id=group_id,
            paid_by_id=data.paid_by_id,
            title=data.title,
            amount=data.amount,
            split_type=data.split_type,
            split_data=data.split_data,
        )
        db.add(expense)

    await db.commit()
    await db.refresh(expense)
    return expense


async def add_member(
    db: AsyncSession, group_id: UUID, requester_id: UUID, data: AddMemberRequest
) -> HissaGroupMember:
    await _get_group_or_404(db, group_id, requester_id)

    user_result = await db.execute(
        select(User).where(User.phone_number == data.phone_number, User.is_active == True)
    )
    new_user = user_result.scalar_one_or_none()
    if not new_user:
        raise HTTPException(status_code=404, detail="No EasyPay user found with that phone number")

    existing = await db.execute(
        select(HissaGroupMember).where(
            HissaGroupMember.group_id == group_id,
            HissaGroupMember.user_id == new_user.id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="User is already a member of this group")

    member = HissaGroupMember(group_id=group_id, user_id=new_user.id)
    db.add(member)
    await db.commit()
    await db.refresh(member)
    return member


async def mark_settled(
    db: AsyncSession, group_id: UUID, user_id: UUID
) -> HissaGroup:
    group = await _get_group_or_404(db, group_id, user_id)

    if group.creator_id != user_id:
        raise HTTPException(status_code=403, detail="Only the group creator can mark it as settled")

    group.is_settled = True
    group.settled_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(group)
    return group
