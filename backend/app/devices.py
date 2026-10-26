"""Доверенные устройства: вход без кода 2FA на своей машине.

Зачем. Код из приложения на своём компьютере вводится по десять раз в день, и
ровно поэтому 2FA в итоге выключают совсем. Запомненное устройство оставляет
второй фактор там, где он и защищает: с чужой машины код спросят.

Что именно запоминается. В браузер уходит HttpOnly-кука со случайным секретом,
на сервере лежит только его sha256. Кука заменяет КОД, но не пароль: без
верного пароля она не делает ничего, поэтому угнанная копия куки сама по себе в
панель не пускает. Срок жизни фиксированный (по умолчанию 30 дней) и не
продлевается при каждом входе - иначе «раз в месяц» превращается в «никогда».
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import settings_store
from app.models import TrustedDevice

COOKIE_NAME = "noderoost_device"
COOKIE_PATH = "/api/auth"
# Ключ настройки: сколько дней помнить устройство. 0 - не помнить вовсе.
TRUST_DAYS_KEY = "trusted_device_days"
DEFAULT_DAYS = 30
MAX_DAYS = 365
# Больше этого числа устройств у одного пользователя не держим: список, который
# никто не прочитает, - это не список, а свалка забытых браузеров.
MAX_DEVICES = 10


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _utc(dt: datetime) -> datetime:
    """Дата из базы в UTC: зону хранит Postgres, sqlite отдаёт её без зоны."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def label_from_agent(user_agent: str) -> str:
    """Короткое имя устройства для списка: «Chrome, Windows».

    Разбирать User-Agent всерьёз незачем: человеку нужно опознать свою машину
    среди двух-трёх, а не получить точную версию браузера.
    """
    ua = user_agent or ""
    browser = ""
    for name, mark in (
        ("Firefox", "Firefox"),
        ("Edge", "Edg/"),
        ("Opera", "OPR/"),
        ("Chrome", "Chrome/"),
        ("Safari", "Safari/"),
    ):
        if mark in ua:
            browser = name
            break
    system = ""
    for name, mark in (
        ("Windows", "Windows"),
        ("macOS", "Mac OS"),
        ("Android", "Android"),
        ("iOS", "iPhone"),
        ("iPad", "iPad"),
        ("Linux", "Linux"),
    ):
        if mark in ua:
            system = name
            break
    both = ", ".join(x for x in (browser, system) if x)
    return both[:128] or "неизвестное устройство"


async def trust_days(session: AsyncSession) -> int:
    """Сколько дней помнить устройство; 0 - код спрашивать каждый раз."""
    raw = await settings_store.get_raw(session, TRUST_DAYS_KEY)
    if raw is None:
        return DEFAULT_DAYS
    try:
        return max(0, min(MAX_DAYS, int(raw)))
    except (TypeError, ValueError):
        return DEFAULT_DAYS


async def set_trust_days(session: AsyncSession, days: int) -> int:
    days = max(0, min(MAX_DAYS, int(days)))
    await settings_store.set_raw(session, TRUST_DAYS_KEY, str(days))
    if days == 0:
        # Выключили - значит код должен спрашиваться уже со следующего входа, а
        # не когда-нибудь потом: выданное раньше доверие снимаем сразу.
        await session.execute(delete(TrustedDevice))
    await session.commit()
    return days


async def _drop_expired(session: AsyncSession) -> None:
    await session.execute(
        delete(TrustedDevice).where(TrustedDevice.expires_at <= datetime.now(timezone.utc))
    )


async def check(session: AsyncSession, username: str, token: str | None) -> bool:
    """Можно ли пропустить этот вход без кода.

    Срок проверяем сами, а не только чистилкой: на sqlite сравнение в SQL идёт
    по строке, и запись с другим форматом даты могла бы пережить свой срок.
    """
    if not token:
        return False
    if await trust_days(session) <= 0:
        return False
    dev = await session.scalar(
        select(TrustedDevice).where(
            TrustedDevice.token_hash == _hash(token),
            TrustedDevice.username == username,
        )
    )
    if dev is None:
        return False
    if _utc(dev.expires_at) <= datetime.now(timezone.utc):
        await session.delete(dev)
        await session.commit()
        return False
    dev.last_used_at = datetime.now(timezone.utc)
    await session.commit()
    return True


async def remember(
    session: AsyncSession, username: str, user_agent: str, ip: str
) -> tuple[str, int] | None:
    """Завести доверие этому устройству. Возвращает (секрет, срок в секундах)."""
    days = await trust_days(session)
    if days <= 0:
        return None
    await _drop_expired(session)
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    session.add(
        TrustedDevice(
            username=username,
            token_hash=_hash(token),
            label=label_from_agent(user_agent),
            ip=ip[:64],
            created_at=now,
            last_used_at=now,
            expires_at=now + timedelta(days=days),
        )
    )
    await session.commit()
    extra = (await _all(session, username))[MAX_DEVICES:]
    for dev in extra:  # лишние - самые давние по последнему входу
        await session.delete(dev)
    if extra:
        await session.commit()
    return token, days * 24 * 3600


async def _all(session: AsyncSession, username: str) -> list[TrustedDevice]:
    rows = await session.scalars(
        select(TrustedDevice)
        .where(TrustedDevice.username == username)
        .order_by(TrustedDevice.last_used_at.desc())
    )
    return list(rows)


async def listing(session: AsyncSession, username: str) -> list[TrustedDevice]:
    await _drop_expired(session)
    await session.commit()
    return await _all(session, username)


async def forget(session: AsyncSession, username: str, device_id: int) -> bool:
    dev = await session.scalar(
        select(TrustedDevice).where(
            TrustedDevice.id == device_id, TrustedDevice.username == username
        )
    )
    if dev is None:
        return False
    await session.delete(dev)
    await session.commit()
    return True


async def forget_all(session: AsyncSession, username: str) -> int:
    devs = await _all(session, username)
    for dev in devs:
        await session.delete(dev)
    if devs:
        await session.commit()
    return len(devs)


def current_id(devices: list[TrustedDevice], token: str | None) -> int | None:
    """Какое из устройств в списке - то, с которого смотрят сейчас."""
    if not token:
        return None
    want = _hash(token)
    for dev in devices:
        if dev.token_hash == want:
            return dev.id
    return None
