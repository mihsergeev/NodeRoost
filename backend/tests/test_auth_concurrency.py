import asyncio
from collections import OrderedDict
from contextvars import ContextVar
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app import ratelimit, totp
from app.bootstrap import ensure_admin
from app.config import get_settings
from app.main import create_app
from app.models import User
from tests.conftest import ADMIN_PASSWORD

LOGIN = {"username": "admin", "password": ADMIN_PASSWORD}
STEP_UP = {"password": ADMIN_PASSWORD}
COUNTER = 1_700_000_000 // 30


@pytest.fixture
async def auth_client(session_factory, monkeypatch):
    monkeypatch.setattr(ratelimit, "_failures", OrderedDict())
    monkeypatch.setattr(totp, "time", SimpleNamespace(time=lambda: 1_700_000_000))
    monkeypatch.setattr(totp, "random_secret", lambda: "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ")
    app = create_app()
    app.state.session_factory = session_factory
    await ensure_admin(session_factory, get_settings())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    await app.state.engine.dispose()


async def _setup(client):
    login = await client.post("/api/auth/login", json=LOGIN)
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    setup = await client.post("/api/auth/2fa/setup", json=STEP_UP, headers=headers)
    assert setup.status_code == 200
    return headers, setup.json()["secret"]


async def _enable(client, headers, secret):
    enabled = await client.post(
        "/api/auth/2fa/enable", headers=headers,
        json={**STEP_UP, "otp": totp._hotp(secret, COUNTER - 1)},
    )
    assert enabled.status_code == 200


def _same_snapshot(monkeypatch):
    """Both HTTP requests read the user before either request writes it."""
    original = AsyncSession.scalar
    ready = asyncio.Event()
    reads = 0

    async def scalar(self, *args, **kwargs):
        nonlocal reads
        result = await original(self, *args, **kwargs)
        if isinstance(result, User):
            reads += 1
            if reads == 2:
                ready.set()
            await asyncio.wait_for(ready.wait(), timeout=5)
        return result

    monkeypatch.setattr(AsyncSession, "scalar", scalar)


@pytest.mark.parametrize(
    ("action", "rejected"), [("login", 401), ("enable", 400), ("disable", 400)],
)
async def test_totp_is_consumed_once_by_concurrent_requests(
    auth_client, monkeypatch, action, rejected,
):
    client = auth_client
    headers, secret = await _setup(client)
    if action != "enable":
        await _enable(client, headers, secret)

    _same_snapshot(monkeypatch)
    otp = totp._hotp(secret, COUNTER)
    if action == "login":
        path, data = "/api/auth/login", {**LOGIN, "otp": otp, "remember": True}
    else:
        path, data = f"/api/auth/2fa/{action}", {**STEP_UP, "otp": otp}
    replies = await asyncio.gather(*(client.post(path, headers=headers, json=data) for _ in range(2)))
    assert sorted(r.status_code for r in replies) == [200, rejected]
    if action == "login":
        assert sum("access_token" in r.json() for r in replies) == 1
        assert sum("set-cookie" in r.headers for r in replies) == 1


async def test_setup_cannot_replace_the_secret_of_concurrent_enable(auth_client, monkeypatch):
    client = auth_client
    headers, secret = await _setup(client)
    monkeypatch.setattr(totp, "random_secret", lambda: "JBSWY3DPEHPK3PXP")
    _same_snapshot(monkeypatch)

    setup, enable = await asyncio.gather(
        client.post("/api/auth/2fa/setup", json=STEP_UP, headers=headers),
        client.post(
            "/api/auth/2fa/enable", headers=headers,
            json={**STEP_UP, "otp": totp._hotp(secret, COUNTER - 1)},
        ),
    )
    assert (setup.status_code, enable.status_code) in {(200, 400), (409, 200)}
    status = await client.get("/api/auth/2fa", headers=headers)
    assert status.json()["enabled"] is (enable.status_code == 200)
    if enable.status_code == 200:
        valid = await client.post("/api/auth/login", json={**LOGIN, "otp": totp._hotp(secret, COUNTER)})
        assert valid.status_code == 200


async def test_invalid_disable_codes_are_rate_limited(auth_client, monkeypatch):
    client = auth_client
    headers, secret = await _setup(client)
    await _enable(client, headers, secret)
    monkeypatch.setattr(ratelimit, "MAX_FAILURES", 2)
    valid = {totp._hotp(secret, COUNTER + offset) for offset in (-1, 0, 1)}
    wrong = next(f"{n:06d}" for n in range(4) if f"{n:06d}" not in valid)
    for _ in range(2):
        reply = await client.post("/api/auth/2fa/disable", headers=headers, json={**STEP_UP, "otp": wrong})
        assert reply.status_code == 400
    reply = await client.post(
        "/api/auth/2fa/disable", headers=headers,
        json={**STEP_UP, "otp": totp._hotp(secret, COUNTER)},
    )
    assert reply.status_code == 429
    assert (await client.get("/api/auth/2fa", headers=headers)).json() == {"enabled": True}


async def test_concurrent_password_changes_cannot_both_issue_valid_tokens(auth_client, monkeypatch):
    client = auth_client
    login = await client.post("/api/auth/login", json=LOGIN)
    old = {"Authorization": f"Bearer {login.json()['access_token']}"}
    _same_snapshot(monkeypatch)

    async def change(password):
        return await client.post(
            "/api/auth/password", headers=old,
            json={"current_password": ADMIN_PASSWORD, "new_password": password},
        )

    replies = await asyncio.gather(change("fixture-new-pass-one"), change("fixture-new-pass-two"))
    assert sorted(r.status_code for r in replies) == [200, 409]
    winner = next(r for r in replies if r.status_code == 200)
    current = {"Authorization": f"Bearer {winner.json()['access_token']}"}
    assert (await client.get("/api/auth/me", headers=current)).status_code == 200
    assert (await client.get("/api/auth/me", headers=old)).status_code == 401


async def test_stale_logout_cannot_restore_a_revoked_token(auth_client, monkeypatch):
    client = auth_client
    login = await client.post("/api/auth/login", json=LOGIN)
    old = {"Authorization": f"Bearer {login.json()['access_token']}"}
    original = AsyncSession.scalar
    read = asyncio.Event()
    release = asyncio.Event()
    stale_request = ContextVar("stale_request", default=False)

    async def scalar(self, *args, **kwargs):
        result = await original(self, *args, **kwargs)
        if isinstance(result, User) and stale_request.get():
            read.set()
            await asyncio.wait_for(release.wait(), timeout=10)
        return result

    monkeypatch.setattr(AsyncSession, "scalar", scalar)

    async def stale_logout():
        stale_request.set(True)
        return await client.post("/api/auth/logout", headers=old)

    stale = asyncio.create_task(stale_logout())
    try:
        await asyncio.wait_for(read.wait(), timeout=5)
        changed = await client.post(
            "/api/auth/password", headers=old,
            json={"current_password": ADMIN_PASSWORD, "new_password": "fixture-new-password"},
        )
        current = {"Authorization": f"Bearer {changed.json()['access_token']}"}
        assert (await client.post("/api/auth/logout", headers=current)).status_code == 204
    finally:
        release.set()
        assert (await stale).status_code == 204
    assert (await client.get("/api/auth/me", headers=current)).status_code == 401
