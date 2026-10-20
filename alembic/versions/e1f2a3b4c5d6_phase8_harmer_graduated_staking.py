"""phase8_harmer_graduated_staking

Revision ID: e1f2a3b4c5d6
Revises: d91783cf204a
Create Date: 2026-10-20 12:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, Sequence[str], None] = 'd91783cf204a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    # 1. client_deposits table
    if "client_deposits" not in existing_tables:
        op.create_table(
            "client_deposits",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("contract_id", sa.String(length=32), sa.ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False, unique=True, index=True),
            sa.Column("client_id", sa.String(length=32), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True),
            sa.Column("amount", sa.Float(), nullable=False),
            sa.Column("currency", sa.String(length=8), nullable=False, server_default="USD"),
            sa.Column("party", sa.String(length=32), nullable=False, server_default="client"),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="HELD"),
            sa.Column("paypal_order_id", sa.String(length=128), nullable=True),
            sa.Column("paypal_capture_id", sa.String(length=128), nullable=True),
            sa.Column("forfeited_amount", sa.Float(), nullable=False, server_default="0.0"),
            sa.Column("returned_amount", sa.Float(), nullable=False, server_default="0.0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        )

    # 2. Add party to stakes
    if "stakes" in existing_tables:
        stake_cols = [c["name"] for c in inspector.get_columns("stakes")]
        if "party" not in stake_cols:
            op.add_column("stakes", sa.Column("party", sa.String(length=32), nullable=False, server_default="freelancer"))

    # 3. Add decided_by to disputes
    if "disputes" in existing_tables:
        dispute_cols = [c["name"] for c in inspector.get_columns("disputes")]
        if "decided_by" not in dispute_cols:
            op.add_column("disputes", sa.Column("decided_by", sa.String(length=32), nullable=True, server_default="arbiter"))

    # 4. Add stake_slash_percentage to contracts
    if "contracts" in existing_tables:
        contract_cols = [c["name"] for c in inspector.get_columns("contracts")]
        if "stake_slash_percentage" not in contract_cols:
            op.add_column("contracts", sa.Column("stake_slash_percentage", sa.Float(), nullable=False, server_default="50.0"))

    # 5. Add disputes_lost to users
    if "users" in existing_tables:
        user_cols = [c["name"] for c in inspector.get_columns("users")]
        if "disputes_lost" not in user_cols:
            op.add_column("users", sa.Column("disputes_lost", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    if "client_deposits" in existing_tables:
        op.drop_table("client_deposits")
