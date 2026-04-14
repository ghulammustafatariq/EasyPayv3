"""Add zakat, trusted_circle, hissa tables

Revision ID: a1b2c3d4e5f6
Revises: 8421aa8322ba
Create Date: 2026-04-09 00:01:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = 'a1b2c3d4e5f6'
down_revision = '8421aa8322ba'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Extend transactions type constraint to include 'zakat' ---
    op.drop_constraint('ck_txn_type', 'transactions', type_='check')
    op.create_check_constraint(
        'ck_txn_type',
        'transactions',
        "type IN ('send','receive','topup','bill','bank_transfer','refund','card_payment','card_refund','zakat')",
    )

    # --- zakat_calculations ---
    op.create_table(
        'zakat_calculations',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('wallet_balance', sa.Numeric(12, 2), nullable=False, server_default='0.00'),
        sa.Column('cash_at_hand', sa.Numeric(12, 2), nullable=False, server_default='0.00'),
        sa.Column('gold_grams', sa.Numeric(10, 3), nullable=False, server_default='0.000'),
        sa.Column('gold_rate_per_gram', sa.Numeric(10, 2), nullable=False, server_default='0.00'),
        sa.Column('silver_grams', sa.Numeric(10, 3), nullable=False, server_default='0.000'),
        sa.Column('silver_rate_per_gram', sa.Numeric(10, 2), nullable=False, server_default='0.00'),
        sa.Column('business_inventory', sa.Numeric(12, 2), nullable=False, server_default='0.00'),
        sa.Column('receivables', sa.Numeric(12, 2), nullable=False, server_default='0.00'),
        sa.Column('debts', sa.Numeric(12, 2), nullable=False, server_default='0.00'),
        sa.Column('nisab_threshold', sa.Numeric(12, 2), nullable=False),
        sa.Column('total_wealth', sa.Numeric(12, 2), nullable=False),
        sa.Column('zakat_due', sa.Numeric(12, 2), nullable=False),
        sa.Column('is_zakat_applicable', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('paid_from_wallet', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('payment_txn_id', UUID(as_uuid=True),
                  sa.ForeignKey('transactions.id'), nullable=True),
        sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )

    # --- trusted_circle_settings ---
    op.create_table(
        'trusted_circle_settings',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='CASCADE'),
                  nullable=False, unique=True),
        sa.Column('is_enabled', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('require_pin_for_non_circle', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('notify_on_non_circle', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('max_non_circle_amount', sa.Numeric(12, 2), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    )

    # --- trusted_circle_contacts ---
    op.create_table(
        'trusted_circle_contacts',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('owner_id', UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('contact_id', UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('added_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('owner_id', 'contact_id', name='uq_trusted_circle_contact'),
    )
    op.create_index('idx_trusted_circle_owner', 'trusted_circle_contacts', ['owner_id'])

    # --- hissa_groups ---
    op.create_table(
        'hissa_groups',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('emoji', sa.String(10), nullable=False, server_default='🎉'),
        sa.Column('creator_id', UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('is_settled', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('settled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )

    # --- hissa_group_members ---
    op.create_table(
        'hissa_group_members',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('group_id', UUID(as_uuid=True),
                  sa.ForeignKey('hissa_groups.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('net_balance', sa.Numeric(12, 2), nullable=False, server_default='0.00'),
        sa.Column('joined_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('group_id', 'user_id', name='uq_hissa_group_member'),
    )
    op.create_index('idx_hissa_member_group', 'hissa_group_members', ['group_id'])
    op.create_index('idx_hissa_member_user', 'hissa_group_members', ['user_id'])

    # --- hissa_expenses ---
    op.create_table(
        'hissa_expenses',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('group_id', UUID(as_uuid=True),
                  sa.ForeignKey('hissa_groups.id', ondelete='CASCADE'), nullable=False),
        sa.Column('paid_by_id', UUID(as_uuid=True),
                  sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('title', sa.String(200), nullable=False),
        sa.Column('amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('split_type', sa.String(20), nullable=False, server_default='equal'),
        sa.Column('split_data', JSONB, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('hissa_expenses')
    op.drop_index('idx_hissa_member_user', table_name='hissa_group_members')
    op.drop_index('idx_hissa_member_group', table_name='hissa_group_members')
    op.drop_table('hissa_group_members')
    op.drop_table('hissa_groups')
    op.drop_index('idx_trusted_circle_owner', table_name='trusted_circle_contacts')
    op.drop_table('trusted_circle_contacts')
    op.drop_table('trusted_circle_settings')
    op.drop_table('zakat_calculations')

    # Restore original transactions type constraint
    op.drop_constraint('ck_txn_type', 'transactions', type_='check')
    op.create_check_constraint(
        'ck_txn_type',
        'transactions',
        "type IN ('send','receive','topup','bill','bank_transfer','refund','card_payment','card_refund')",
    )
