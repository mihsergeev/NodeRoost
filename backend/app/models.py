from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    totp_secret: Mapped[str] = mapped_column(String(64), default="")
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # последний использованный TOTP-счётчик (защита от повторного использования кода)
    totp_last_counter: Mapped[int] = mapped_column(BigInteger, default=0)
    # версия токена: смена пароля инкрементит её и инвалидирует старые JWT
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AuditLog(Base):
    """Журнал действий: кто/когда/что сделал (вход, смена пароля, операции)."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    username: Mapped[str] = mapped_column(String(64), default="")
    action: Mapped[str] = mapped_column(String(48))
    target: Mapped[str] = mapped_column(String(255), default="")
    detail: Mapped[str] = mapped_column(Text, default="")


class AppSetting(Base):
    """Настройки панели (key-value), редактируемые из UI. Значение — строка/JSON."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class NodeMetricSample(Base):
    """Снимок числа нод онлайн/всего (снимается фоновым коллектором) — для графика."""

    __tablename__ = "node_metric_samples"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    total: Mapped[int] = mapped_column(Integer, default=0)
    online: Mapped[int] = mapped_column(Integer, default=0)


class Certificate(Base):
    """Сертификат для имени внутри сети (Let's Encrypt через панель).

    Приватного ключа здесь НЕТ и быть не должно: его генерит нода, панель видит
    только CSR и подписанный сертификат. Поэтому строка безопасна для бэкапа и
    для журнала — в ней нет ничего, чего не видно в публичных CT-логах.
    """

    __tablename__ = "certificates"

    name: Mapped[str] = mapped_column(String(253), primary_key=True)
    node_id: Mapped[str] = mapped_column(String(32), default="")
    # ok | issuing | error — что показывать администратору
    status: Mapped[str] = mapped_column(String(16), default="issuing")
    cert_pem: Mapped[str] = mapped_column(Text, default="")
    not_after: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error: Mapped[str] = mapped_column(Text, default="")
    # Раньше этого времени за выпуском не ходим. У Let's Encrypt лимит на
    # неудачные проверки (5 в час на имя), и агент, спрашивающий раз в минуту,
    # сжёг бы его за пять минут — а потом ждал бы час на ровном месте.
    retry_after: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class NodeStatus(Base):
    """Последний известный online/offline статус ноды (для алертов о падении) +
    флаг «уже предупредили об истечении ключа» (дедуп)."""

    __tablename__ = "node_status"

    node_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    online: Mapped[bool] = mapped_column(Boolean)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    key_alerted: Mapped[bool] = mapped_column(Boolean, default=False)
