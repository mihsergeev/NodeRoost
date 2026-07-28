"""node_metric_samples, node_status

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-17

"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "node_metric_samples",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("online", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_node_metric_samples_ts", "node_metric_samples", ["ts"])
    op.create_table(
        "node_status",
        sa.Column("node_id", sa.String(length=32), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("online", sa.Boolean(), nullable=False),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "key_alerted", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )


def downgrade() -> None:
    op.drop_table("node_status")
    op.drop_index("ix_node_metric_samples_ts", table_name="node_metric_samples")
    op.drop_table("node_metric_samples")
