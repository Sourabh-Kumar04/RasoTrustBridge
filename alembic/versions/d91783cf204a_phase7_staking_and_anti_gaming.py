"""phase7_staking_and_anti_gaming

Revision ID: d91783cf204a
Revises: b628144fe79d
Create Date: 2026-10-20 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd91783cf204a'
down_revision: Union[str, Sequence[str], None] = 'b628144fe79d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    if "stakes" not in existing_tables:
        op.create_table(
            "stakes",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("contract_id", sa.String(length=32), sa.ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False, unique=True, index=True),
            sa.Column("freelancer_id", sa.String(length=32), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True),
            sa.Column("amount", sa.Float(), nullable=False),
            sa.Column("currency", sa.String(length=8), nullable=False, server_default="USD"),
            sa.Column("stake_percentage", sa.Float(), nullable=False, server_default="5.0"),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="HELD"),
            sa.Column("paypal_order_id", sa.String(length=128), nullable=True),
            sa.Column("paypal_capture_id", sa.String(length=128), nullable=True),
            sa.Column("slashed_amount", sa.Float(), nullable=False, server_default="0.0"),
            sa.Column("returned_amount", sa.Float(), nullable=False, server_default="0.0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    if "stakes" in existing_tables:
        op.drop_table("stakes")
