"""Запомненное устройство: вход без кода 2FA на своей машине.

Проверяем ровно то, ради чего оно сделано, и ровно то, чем оно опасно: код не
спрашивают только на этой машине и только с верным паролем, а любое сомнение
(смена пароля, выключение 2FA, конец срока) доверие снимает.
"""

import base64
import hashlib
import hmac
import struct
import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app import devices
from app.models import TrustedDevice, User
from tests.conftest import ADMIN_PASSWORD


def code_at(secret: str, step_offset: int = 0) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(
        key, struct.pack(">Q", int(time.time()) // 30 + step_offset), hashlib.sha1
    ).digest()
    off = digest[19] & 15
    return "%06d" % (
        (struct.unpack(">I", digest[off:off + 4])[0] & 0x7FFFFFFF) % 10**6
    )


async def _reset_counter() -> None:
    """Обнулить счётчик использованных кодов.

    Коды одноразовые и окно всего три шага по 30 секунд, а база у тестов общая:
    без сброса второй же тест упирается в "этот код уже использован".
    """
    from app.config import get_settings
    from app.db import create_engine_and_factory

    engine, factory = create_engine_and_factory(get_settings().db_url)
    async with factory() as session:
        user = await session.scalar(select(User).where(User.username == "admin"))
        if user is not None:
            user.totp_last_counter = 0
            await session.commit()
    await engine.dispose()


async def _enable_2fa(client) -> tuple[str, dict]:
    """Включает 2FA и возвращает (секрет, заголовки с токеном)."""
    tok = (await client.post(
        "/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD}
    )).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    secret = (await client.post(
        "/api/auth/2fa/setup", json={"password": ADMIN_PASSWORD}, headers=h
    )).json()["secret"]
    await _reset_counter()
    r = await client.post(
        "/api/auth/2fa/enable",
        json={"otp": code_at(secret), "password": ADMIN_PASSWORD},
        headers=h,
    )
    assert r.status_code == 200
    await _reset_counter()
    return secret, h


async def _disable_2fa(client, secret: str, h: dict, step: int = 0) -> None:
    await _reset_counter()
    r = await client.post(
        "/api/auth/2fa/disable",
        json={"otp": code_at(secret, step), "password": ADMIN_PASSWORD},
        headers=h,
    )
    assert r.status_code == 200


@pytest.fixture(autouse=True)
async def _clean(client):
    """База у тестов общая: убираем за собой 2FA и устройства."""
    yield
    from app.config import get_settings
    from app.db import create_engine_and_factory

    engine, factory = create_engine_and_factory(get_settings().db_url)
    async with factory() as session:
        for dev in (await session.scalars(select(TrustedDevice))).all():
            await session.delete(dev)
        user = await session.scalar(select(User).where(User.username == "admin"))
        if user is not None:
            user.totp_enabled = False
            user.totp_secret = ""
        await session.commit()
    await engine.dispose()
    client.cookies.clear()


async def test_remembered_device_skips_the_code_next_time(client):
    secret, h = await _enable_2fa(client)

    first = await client.post(
        "/api/auth/login",
        json={
            "username": "admin",
            "password": ADMIN_PASSWORD,
            "otp": code_at(secret, 1),
            "remember": True,
        },
    )
    assert first.status_code == 200
    assert devices.COOKIE_NAME in client.cookies

    # второй вход - уже без кода
    again = await client.post(
        "/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD}
    )
    assert again.status_code == 200

    await _disable_2fa(client, secret, h)


async def test_another_browser_is_still_asked_for_the_code(client):
    """Весь смысл в том, что запоминается машина, а не учетная запись."""
    secret, h = await _enable_2fa(client)
    await client.post(
        "/api/auth/login",
        json={
            "username": "admin",
            "password": ADMIN_PASSWORD,
            "otp": code_at(secret, 1),
            "remember": True,
        },
    )
    client.cookies.clear()  # другой браузер
    r = await client.post(
        "/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD}
    )
    assert r.status_code == 401 and r.json()["detail"] == "2fa_required"

    await _disable_2fa(client, secret, h)


async def test_the_cookie_alone_does_not_let_anyone_in(client):
    """Кука снимает второй фактор, но не первый: без пароля она бесполезна."""
    secret, h = await _enable_2fa(client)
    await client.post(
        "/api/auth/login",
        json={
            "username": "admin",
            "password": ADMIN_PASSWORD,
            "otp": code_at(secret, 1),
            "remember": True,
        },
    )
    r = await client.post(
        "/api/auth/login", json={"username": "admin", "password": "wrong-one"}
    )
    assert r.status_code == 401

    await _disable_2fa(client, secret, h)


async def test_remember_is_ignored_without_a_valid_code(client):
    """Просьба запомнить выполняется только вместе с пройденной 2FA."""
    secret, h = await _enable_2fa(client)
    bad = await client.post(
        "/api/auth/login",
        json={
            "username": "admin",
            "password": ADMIN_PASSWORD,
            "otp": "000000",
            "remember": True,
        },
    )
    assert bad.status_code == 401
    assert devices.COOKIE_NAME not in client.cookies

    await _disable_2fa(client, secret, h)


async def test_expired_trust_asks_for_the_code_again(session):
    """Срок не продлевается сам: месяц прошел - код спросят."""
    now = datetime.now(timezone.utc)
    session.add(
        TrustedDevice(
            username="admin",
            token_hash=devices._hash("secret-token"),
            expires_at=now - timedelta(minutes=1),
            created_at=now - timedelta(days=31),
            last_used_at=now - timedelta(days=1),
        )
    )
    await session.commit()
    assert await devices.check(session, "admin", "secret-token") is False
    assert (await session.scalars(select(TrustedDevice))).all() == []


async def test_turning_the_setting_off_forgets_everything(session):
    now = datetime.now(timezone.utc)
    session.add(
        TrustedDevice(
            username="admin",
            token_hash=devices._hash("tok"),
            expires_at=now + timedelta(days=30),
            created_at=now,
            last_used_at=now,
        )
    )
    await session.commit()

    assert await devices.set_trust_days(session, 0) == 0
    assert (await session.scalars(select(TrustedDevice))).all() == []
    # и новое доверие больше не выдается
    assert await devices.remember(session, "admin", "curl/8", "127.0.0.1") is None


async def test_password_change_drops_trusted_devices(client):
    """Пароль меняют и когда думают, что его узнали. Устройства, которым код не
    нужен, в этот момент оставлять нельзя."""
    secret, h = await _enable_2fa(client)
    await client.post(
        "/api/auth/login",
        json={
            "username": "admin",
            "password": ADMIN_PASSWORD,
            "otp": code_at(secret, 1),
            "remember": True,
        },
    )
    new_password = ADMIN_PASSWORD + "-x"
    r = await client.post(
        "/api/auth/password",
        json={"current_password": ADMIN_PASSWORD, "new_password": new_password},
        headers=h,
    )
    assert r.status_code == 200
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}

    back = await client.post(
        "/api/auth/login", json={"username": "admin", "password": new_password}
    )
    assert back.status_code == 401 and back.json()["detail"] == "2fa_required"

    # возвращаем пароль и выключаем 2FA
    r = await client.post(
        "/api/auth/password",
        json={"current_password": new_password, "new_password": ADMIN_PASSWORD},
        headers=h,
    )
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    await _disable_2fa(client, secret, h)


async def test_label_reads_like_a_device_name():
    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/141.0 Safari/537.36"
    )
    assert devices.label_from_agent(ua) == "Chrome, Windows"
    assert devices.label_from_agent("") == "неизвестное устройство"
