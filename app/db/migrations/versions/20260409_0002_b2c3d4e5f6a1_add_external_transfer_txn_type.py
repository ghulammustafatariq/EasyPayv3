"""Add external_transfer to transactions type constraint

Revision ID: b2c3d4e5f6a1
Revises: a1b2c3d4e5f6
Create Date: 2026-04-09 00:02:00.000000
"""
from alembic import op

revision = 'b2c3d4e5f6a1'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint('ck_txn_type', 'transactions', type_='check')
    op.create_check_constraint(
        'ck_txn_type',
        'transactions',
        "type IN ('send','receive','topup','bill','bank_transfer','refund',"
        "'card_payment','card_refund','zakat','external_transfer')",
    )


def downgrade() -> None:
    op.drop_constraint('ck_txn_type', 'transactions', type_='check')
    op.create_check_constraint(
        'ck_txn_type',
        'transactions',
        "type IN ('send','receive','topup','bill','bank_transfer','refund',"
        "'card_payment','card_refund','zakat')",
    )
