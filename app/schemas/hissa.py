from pydantic import BaseModel, Field
from decimal import Decimal
from typing import Optional
from uuid import UUID
from datetime import datetime


class CreateGroupRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    emoji: str = Field("🎉", max_length=10)
    member_phones: list[str] = Field(default_factory=list)
    # phones of members to invite; creator auto-added


class AddExpenseRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    amount: Decimal = Field(..., gt=0)
    paid_by_id: UUID  # member user_id
    split_type: str = Field("equal")  # equal | custom | percentage
    split_data: Optional[dict] = None
    # custom: {"user_id": amount}
    # percentage: {"user_id": percent (0-100)}


class AddMemberRequest(BaseModel):
    phone_number: str


class SettleRequest(BaseModel):
    member_user_id: UUID  # who is being settled with


class MemberResponse(BaseModel):
    user_id: UUID
    full_name: str
    phone_number: str
    net_balance: Decimal  # positive = owed TO this member; negative = owes

    model_config = {"from_attributes": True}


class ExpenseResponse(BaseModel):
    id: UUID
    title: str
    amount: Decimal
    paid_by_id: UUID
    paid_by_name: str
    split_type: str
    split_data: Optional[dict] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class GroupSummaryResponse(BaseModel):
    id: UUID
    name: str
    emoji: str
    member_count: int
    total_expenses: Decimal
    my_net_balance: Decimal
    is_settled: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class GroupDetailResponse(BaseModel):
    id: UUID
    name: str
    emoji: str
    creator_id: UUID
    is_settled: bool
    members: list[MemberResponse]
    expenses: list[ExpenseResponse]
    total_expenses: Decimal
    created_at: datetime

    model_config = {"from_attributes": True}
