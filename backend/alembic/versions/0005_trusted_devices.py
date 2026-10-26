"""Доверенные устройства: вход без кода 2FA на своей машине.

Код из приложения приходилось вводить каждый вход, и на своём компьютере это
превращалось в рутину, от которой хочется выключить 2FA целиком. Запомненное
устройство оставляет второй фактор там, где он и нужен: на чужой машине код
спросят, а знание пароля без кода по-прежнему никуда не пускает.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trusted_devices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(length=64), nullable=False, index=True),
        # в базе только sha256 от секрета из куки: утекшая копия базы не даёт
        # войти, как и украденный хэш пароля
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("label", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("ip", sa.String(length=64), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_used_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("trusted_devices")
