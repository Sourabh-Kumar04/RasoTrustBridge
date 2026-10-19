"""initial_schema_postgresql_phase5

Revision ID: b628144fe79d
Revises: 
Create Date: 2026-10-19 19:50:42.285832

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b628144fe79d'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    # 1. Create base tables if fresh PostgreSQL database
    if "users" not in existing_tables:
        op.create_table(
            "users",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("email", sa.String(length=255), unique=True, index=True, nullable=False),
            sa.Column("hashed_password", sa.String(length=255), nullable=False),
            sa.Column("role", sa.String(length=32), nullable=False, server_default="client"),
            sa.Column("display_name", sa.String(length=128), nullable=False),
            sa.Column("trust_score", sa.Float(), nullable=False, server_default="50.0"),
            sa.Column("completed_deals", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("disputed_deals", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )

    if "contracts" not in existing_tables:
        op.create_table(
            "contracts",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("brief", sa.Text(), nullable=False),
            sa.Column("client_id", sa.String(length=32), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True),
            sa.Column("freelancer_id", sa.String(length=32), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True),
            sa.Column("client_name", sa.String(length=128), nullable=False),
            sa.Column("client_email", sa.String(length=255), nullable=False),
            sa.Column("client_country", sa.String(length=8), nullable=False, server_default="US"),
            sa.Column("developer_name", sa.String(length=128), nullable=False),
            sa.Column("developer_email", sa.String(length=255), nullable=False),
            sa.Column("developer_country", sa.String(length=8), nullable=False, server_default="IN"),
            sa.Column("base_currency", sa.String(length=8), nullable=False, server_default="USD"),
            sa.Column("payout_currency", sa.String(length=8), nullable=False, server_default="INR"),
            sa.Column("total_amount", sa.Float(), nullable=False),
            sa.Column("fx_strategy", sa.String(32), nullable=False, server_default="LOCKED_AT_SIGNING"),
            sa.Column("locked_fx_rate", sa.Float(), nullable=True),
            sa.Column("status", sa.String(32), nullable=False, server_default="DRAFT"),
            sa.Column("paypal_order_id", sa.String(length=128), nullable=True),
            sa.Column("paypal_capture_id", sa.String(length=128), nullable=True),
            sa.Column("buyer_timeout_days", sa.Integer(), nullable=False, server_default="7"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("funded_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        )

    if "milestones" not in existing_tables:
        op.create_table(
            "milestones",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("contract_id", sa.String(length=32), sa.ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("order_index", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("amount", sa.Float(), nullable=False),
            sa.Column("payout_amount", sa.Float(), nullable=True),
            sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
            sa.Column("verification_type", sa.String(32), nullable=False, server_default="CODE_DELIVERY"),
            sa.Column("acceptance_criteria", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("evidence_url", sa.String(length=512), nullable=True),
            sa.Column("evidence_notes", sa.Text(), nullable=True),
            sa.Column("evidence_payload", sa.Text(), nullable=True),
            sa.Column("verification_score", sa.Float(), nullable=True),
            sa.Column("verification_log", sa.Text(), nullable=True),
            sa.Column("verified_by", sa.String(length=64), nullable=True),
            sa.Column("paypal_capture_id", sa.String(length=128), nullable=True),
            sa.Column("paypal_payout_batch_id", sa.String(length=128), nullable=True),
            sa.Column("paypal_payout_item_id", sa.String(length=128), nullable=True),
            sa.Column("idempotency_key", sa.String(length=128), nullable=True, index=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("funded_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True, index=True),
            sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("disputed_at", sa.DateTime(timezone=True), nullable=True),
        )

    if "audit_logs" not in existing_tables:
        op.create_table(
            "audit_logs",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("contract_id", sa.String(length=32), sa.ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("milestone_id", sa.String(length=32), nullable=True),
            sa.Column("actor_role", sa.String(32), nullable=False),
            sa.Column("actor_identifier", sa.String(length=255), nullable=False),
            sa.Column("action", sa.String(length=64), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("metadata_json", sa.Text(), nullable=True),
            sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        )

    if "disputes" not in existing_tables:
        op.create_table(
            "disputes",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("contract_id", sa.String(length=32), sa.ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("milestone_id", sa.String(length=32), sa.ForeignKey("milestones.id", ondelete="CASCADE"), nullable=False, unique=True),
            sa.Column("initiator_role", sa.String(32), nullable=False),
            sa.Column("initiator_email", sa.String(length=255), nullable=False),
            sa.Column("reason", sa.String(length=255), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("evidence_bundle", sa.Text(), nullable=True),
            sa.Column("status", sa.String(32), nullable=False, server_default="OPEN"),
            sa.Column("resolution_notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        )

    if "escrow_holds" not in existing_tables:
        op.create_table(
            "escrow_holds",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("contract_id", sa.String(length=32), sa.ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("milestone_id", sa.String(length=32), nullable=True),
            sa.Column("amount", sa.Float(), nullable=False),
            sa.Column("currency", sa.String(length=8), nullable=False, server_default="USD"),
            sa.Column("paypal_order_id", sa.String(length=128), nullable=False),
            sa.Column("paypal_capture_id", sa.String(length=128), nullable=True),
            sa.Column("paypal_status", sa.String(length=64), nullable=False),
            sa.Column("idempotency_key", sa.String(length=128), unique=True, index=True, nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        )

    if "notifications" not in existing_tables:
        op.create_table(
            "notifications",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("user_id", sa.String(length=32), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("contract_id", sa.String(length=32), sa.ForeignKey("contracts.id", ondelete="CASCADE"), nullable=True, index=True),
            sa.Column("milestone_id", sa.String(length=32), nullable=True),
            sa.Column("type", sa.String(length=64), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("is_read", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("email_sent", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("email_status", sa.String(length=32), nullable=False, server_default="pending"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )

    # 2. Processed Webhook Deliveries (for Webhook Deduplication)
    if "processed_webhook_deliveries" not in existing_tables:
        op.create_table(
            'processed_webhook_deliveries',
            sa.Column('delivery_id', sa.String(length=128), nullable=False),
            sa.Column('source', sa.String(length=32), nullable=False),
            sa.Column('event_type', sa.String(length=64), nullable=True),
            sa.Column('payload_hash', sa.String(length=64), nullable=True),
            sa.Column('processed_at', sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint('delivery_id')
        )
        op.create_index(op.f('ix_processed_webhook_deliveries_source'), 'processed_webhook_deliveries', ['source'], unique=False)

    # 3. Immutable Ledger Entries (for Event-Sourced Ledger)
    if "ledger_entries" not in existing_tables:
        op.create_table(
            'ledger_entries',
            sa.Column('id', sa.String(length=32), nullable=False),
            sa.Column('contract_id', sa.String(length=32), sa.ForeignKey('contracts.id', ondelete='CASCADE'), nullable=False),
            sa.Column('milestone_id', sa.String(length=32), nullable=True),
            sa.Column('entry_type', sa.String(length=32), nullable=False),
            sa.Column('amount', sa.Float(), nullable=False),
            sa.Column('currency', sa.String(length=8), nullable=False, server_default='USD'),
            sa.Column('paypal_reference', sa.String(length=128), nullable=True),
            sa.Column('idempotency_key', sa.String(length=128), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_ledger_entries_contract_id'), 'ledger_entries', ['contract_id'], unique=False)
        op.create_index(op.f('ix_ledger_entries_entry_type'), 'ledger_entries', ['entry_type'], unique=False)
        op.create_index(op.f('ix_ledger_entries_idempotency_key'), 'ledger_entries', ['idempotency_key'], unique=False)
        op.create_index(op.f('ix_ledger_entries_milestone_id'), 'ledger_entries', ['milestone_id'], unique=False)
        op.create_index(op.f('ix_ledger_entries_paypal_reference'), 'ledger_entries', ['paypal_reference'], unique=False)

    # 4. Alter columns on existing tables if upgraded from older schema
    if "contracts" in existing_tables:
        contract_cols = [c["name"] for c in inspector.get_columns("contracts")]
        if "buyer_timeout_days" not in contract_cols:
            op.add_column('contracts', sa.Column('buyer_timeout_days', sa.Integer(), nullable=False, server_default='7'))

    if "escrow_holds" in existing_tables:
        hold_cols = [c["name"] for c in inspector.get_columns("escrow_holds")]
        if "idempotency_key" not in hold_cols:
            op.add_column('escrow_holds', sa.Column('idempotency_key', sa.String(length=128), nullable=True))
            op.create_index(op.f('ix_escrow_holds_idempotency_key'), 'escrow_holds', ['idempotency_key'], unique=True)

    if "milestones" in existing_tables:
        ms_cols = [c["name"] for c in inspector.get_columns("milestones")]
        if "idempotency_key" not in ms_cols:
            op.add_column('milestones', sa.Column('idempotency_key', sa.String(length=128), nullable=True))
            op.create_index(op.f('ix_milestones_idempotency_key'), 'milestones', ['idempotency_key'], unique=False)
        if "expires_at" not in ms_cols:
            op.add_column('milestones', sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True))
            op.create_index(op.f('ix_milestones_expires_at'), 'milestones', ['expires_at'], unique=False)

    if "users" in existing_tables:
        user_cols = [c["name"] for c in inspector.get_columns("users")]
        if "trust_score" not in user_cols:
            op.add_column('users', sa.Column('trust_score', sa.Float(), nullable=False, server_default='50.0'))
        if "completed_deals" not in user_cols:
            op.add_column('users', sa.Column('completed_deals', sa.Integer(), nullable=False, server_default='0'))
        if "disputed_deals" not in user_cols:
            op.add_column('users', sa.Column('disputed_deals', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    pass
