"""Убрать retry_after: пауза после отказа была нужна из-за лимитов Let's Encrypt.

Своя CA подписывает мгновенно и без лимитов — считать неудачные попытки больше
некому, а колонка, которую никто не читает, врёт о том, как всё устроено.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("certificates", "retry_after")


def downgrade() -> None:
    op.add_column(
        "certificates",
        sa.Column("retry_after", sa.DateTime(timezone=True), nullable=True),
    )
