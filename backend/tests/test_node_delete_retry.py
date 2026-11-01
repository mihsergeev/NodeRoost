import asyncio
import hashlib
import json
from copy import deepcopy

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app import models, settings_store
from app.api import nodes as api_nodes
from app.bootstrap import ensure_admin
from app.config import get_settings
from app.hs_client import HeadscaleError
from tests.conftest import ADMIN_PASSWORD


def hs_error(code):
    error = HeadscaleError(f"headscale {code}: test response")
    error.status_code = code
    return error


async def seed_case(client, session_factory, monkeypatch):
    class Headscale:
        present = True
        accepted_deletes = 0
        errors = []

        async def delete_node(self, node_id):
            assert node_id == "2"
            if self.errors:
                raise self.errors.pop(0)
            if not self.present:
                raise hs_error(404)
            self.present = False
            self.accepted_deletes += 1

    app = client._transport.app
    monkeypatch.setattr(app.state, "session_factory", session_factory)
    await ensure_admin(session_factory, get_settings())
    hs = Headscale()
    monkeypatch.setattr(api_nodes, "get_client", lambda _: hs)
    monkeypatch.setattr(api_nodes, "require_hs", lambda _: None)
    pushes = []

    async def apply_policy(*_):
        pushes.append(True)

    monkeypatch.setattr(api_nodes, "apply_policy", apply_policy)
    data = {
        settings_store.NODE_META_KEY: {
            "2": {"kind": "server", "exit_gateway": True, "group": "Acme"},
            "5": {"kind": "server", "exit_gateway": True},
            "1": {"kind": "device", "description": "keep this note", "muted": True,
                  "hidden": True, "group": "Lab", "subgroup": "QA",
                  "exit_via": ["2", "5"], "force_exit": "2"},
            "4": {"kind": "device", "exit_via": ["2"]},
            "6": {"kind": "device", "exit_via": ["5"], "force_exit": "5"},
        },
        settings_store.AGENT_KEY: {
            "2": {"token": "example-gateway-token", "exit": True},
            "1": {"token": "example-client-token", "use_exit": "100.64.10.2"},
            "6": {"token": "example-other-token", "use_exit": "100.64.10.5"},
        },
        settings_store.ACL_RULES_KEY: [
            {"src": {"kind": "node", "value": "1"}, "dst": {"kind": "node", "value": "2"}, "ports": "443"},
            {"src": {"kind": "node", "value": "1"}, "dst": {"kind": "node", "value": "5"}, "ports": "22"},
        ],
        settings_store.ROUTING_KEY: {
            "removed": {"src_kind": "node", "src": ["1"], "via": "2"},
            "kept": {"src_kind": "node", "src": ["2", "6"], "via": "5"},
        },
        settings_store.DNS_RECORDS_KEY: [
            {"name": "gateway.example.test", "node_id": "2"},
            {"name": "other.example.test", "node_id": "5"},
        ],
        "unrelated": {"mode": "preserve"},
    }
    async with session_factory() as session:
        for key, value in data.items():
            await settings_store.set_raw(session, key, json.dumps(value))
    login = await client.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD})
    assert login.status_code == 200
    headers = {"Authorization": "Bearer " + login.json()["access_token"]}

    async def snapshot():
        async with session_factory() as session:
            values = {row.key: json.loads(row.value) for row in await session.scalars(select(models.AppSetting))}
            users = [(u.id, u.username, u.password_hash, u.totp_secret, u.totp_enabled, u.token_version)
                     for u in await session.scalars(select(models.User))]
            values["auth_digest"] = hashlib.sha256(json.dumps(users).encode()).hexdigest()
            return values

    before = await snapshot()
    return hs, headers, pushes, before, snapshot


def expected_cleanup(before):
    expected = deepcopy(before)
    meta = expected[settings_store.NODE_META_KEY]
    meta.pop("2")
    meta["1"]["exit_via"] = ["5"]
    meta["1"].pop("force_exit")
    meta["4"].pop("exit_via")
    agents = expected[settings_store.AGENT_KEY]
    agents.pop("2")
    agents["1"].pop("use_exit")
    expected[settings_store.ACL_RULES_KEY] = expected[settings_store.ACL_RULES_KEY][1:]
    expected[settings_store.ROUTING_KEY] = {"kept": {"src_kind": "node", "src": ["6"], "via": "5"}}
    expected[settings_store.DNS_RECORDS_KEY] = expected[settings_store.DNS_RECORDS_KEY][1:]
    return expected


@pytest.mark.parametrize("fail_after_write", range(1, 8))
async def test_local_cleanup_rolls_back_and_retry_finishes_when_headscale_is_already_missing(
    client, session_factory, monkeypatch, fail_after_write
):
    hs, headers, pushes, before, snapshot = await seed_case(client, session_factory, monkeypatch)
    original = settings_store._set_raw
    writes = 0

    async def fail_after_write_once(*args, **kwargs):
        nonlocal writes
        await original(*args, **kwargs)
        writes += 1
        if writes == fail_after_write:
            raise OperationalError("injected write failure", {}, RuntimeError("test database failure"))

    monkeypatch.setattr(settings_store, "_set_raw", fail_after_write_once)
    with pytest.raises(OperationalError):
        await client.delete("/api/nodes/2", headers=headers)
    assert hs.present is False and hs.accepted_deletes == 1
    assert pushes == []
    assert await snapshot() == before
    monkeypatch.setattr(settings_store, "_set_raw", original)
    response = await client.delete("/api/nodes/2", headers=headers)
    assert response.status_code == 204
    assert await snapshot() == expected_cleanup(before)
    assert hs.accepted_deletes == 1 and pushes == [True]
    response = await client.delete("/api/nodes/2", headers=headers)
    assert response.status_code == 204
    assert await snapshot() == expected_cleanup(before)


async def test_commit_failure_leaves_cleanup_retryable(client, session_factory, monkeypatch):
    hs, headers, pushes, before, snapshot = await seed_case(client, session_factory, monkeypatch)
    original = AsyncSession.commit

    async def fail_commit(self):
        raise OperationalError("injected commit failure", {}, RuntimeError("test database failure"))

    monkeypatch.setattr(AsyncSession, "commit", fail_commit)
    with pytest.raises(OperationalError):
        await client.delete("/api/nodes/2", headers=headers)
    assert hs.present is False and pushes == []
    assert await snapshot() == before
    monkeypatch.setattr(AsyncSession, "commit", original)
    response = await client.delete("/api/nodes/2", headers=headers)
    assert response.status_code == 204
    assert await snapshot() == expected_cleanup(before)


async def test_lost_commit_acknowledgement_is_safe_to_retry(client, session_factory, monkeypatch):
    hs, headers, pushes, before, snapshot = await seed_case(client, session_factory, monkeypatch)
    original = AsyncSession.commit
    first = True

    async def lose_commit_ack(self):
        nonlocal first
        await original(self)
        if first:
            first = False
            raise OperationalError("lost commit acknowledgement", {}, RuntimeError("test connection failure"))

    monkeypatch.setattr(AsyncSession, "commit", lose_commit_ack)
    with pytest.raises(OperationalError):
        await client.delete("/api/nodes/2", headers=headers)
    assert hs.present is False and pushes == []
    assert await snapshot() == expected_cleanup(before)
    response = await client.delete("/api/nodes/2", headers=headers)
    assert response.status_code == 204
    assert await snapshot() == expected_cleanup(before)


async def test_lost_headscale_acknowledgement_is_safe_to_retry(client, session_factory, monkeypatch):
    hs, headers, pushes, before, snapshot = await seed_case(client, session_factory, monkeypatch)
    original = hs.delete_node

    async def lose_delete_ack(node_id):
        await original(node_id)
        raise HeadscaleError("headscale request timed out after accepting deletion")

    hs.delete_node = lose_delete_ack
    response = await client.delete("/api/nodes/2", headers=headers)
    assert response.status_code == 502
    assert hs.present is False and pushes == []
    assert await snapshot() == before
    hs.delete_node = original
    response = await client.delete("/api/nodes/2", headers=headers)
    assert response.status_code == 204
    assert await snapshot() == expected_cleanup(before)


async def test_agent_poll_waits_for_cleanup_and_does_not_restore_deleted_config(client, session_factory, monkeypatch):
    monkeypatch.setattr(settings_store, "_write_lock", asyncio.Lock())
    hs, headers, pushes, before, snapshot = await seed_case(client, session_factory, monkeypatch)
    original = settings_store._set_raw
    staged = asyncio.Event()
    release = asyncio.Event()
    first = True

    async def pause_after_first_write(*args, **kwargs):
        nonlocal first
        await original(*args, **kwargs)
        if first:
            first = False
            staged.set()
            await release.wait()

    monkeypatch.setattr(settings_store, "_set_raw", pause_after_first_write)
    deleting = asyncio.create_task(client.delete("/api/nodes/2", headers=headers))
    await asyncio.wait_for(staged.wait(), 5)
    entered = [asyncio.Event(), asyncio.Event()]

    async def poll(token, started):
        async with session_factory() as session:
            started.set()
            await settings_store.touch_agent_poll(session, token)

    polling = [asyncio.create_task(poll(token, event)) for token, event in zip(
        ("example-gateway-token", "example-other-token"), entered
    )]
    for event in entered:
        await asyncio.wait_for(event.wait(), 5)
    assert not any(task.done() for task in polling)
    assert await snapshot() == before
    release.set()
    assert (await asyncio.wait_for(deleting, 5)).status_code == 204
    await asyncio.wait_for(asyncio.gather(*polling), 5)
    actual = await snapshot()
    assert actual[settings_store.AGENT_KEY]["6"].pop("last_poll")
    assert actual == expected_cleanup(before)
    assert hs.accepted_deletes == 1


async def test_atomic_update_rollback_clears_session_flag_after_cancellation(session_factory):
    async with session_factory() as session:
        with pytest.raises(asyncio.CancelledError):
            async with settings_store.atomic_update(session):
                await settings_store.set_raw(session, "cancelled", json.dumps({"value": "discard"}))
                raise asyncio.CancelledError()
        assert not session.info.get(settings_store._ATOMIC_UPDATE)
        await settings_store.set_raw(session, "after_cancel", json.dumps({"value": "keep"}))
    async with session_factory() as session:
        assert await settings_store.get_raw(session, "cancelled") is None
        assert await settings_store.get_raw(session, "after_cancel") == json.dumps({"value": "keep"})


async def test_atomic_update_does_not_defer_another_session_commit(session_factory):
    async with session_factory() as first, session_factory() as other:
        with pytest.raises(RuntimeError, match="abort first session"):
            async with settings_store.atomic_update(first):
                await settings_store.set_raw(other, "other_session", json.dumps({"value": "keep"}))
                raise RuntimeError("abort first session")
    async with session_factory() as session:
        assert await settings_store.get_raw(session, "other_session") == json.dumps({"value": "keep"})


@pytest.mark.parametrize("error", [hs_error(401), hs_error(403), hs_error(503), HeadscaleError("headscale 404: text is not a typed response"), HeadscaleError("headscale 503: upstream mentioned 404", status_code=503), hs_error(500)])
async def test_other_headscale_errors_never_start_cleanup(client, session_factory, monkeypatch, error):
    hs, headers, pushes, before, snapshot = await seed_case(client, session_factory, monkeypatch)
    hs.errors = [error]
    response = await client.delete("/api/nodes/2", headers=headers)
    assert response.status_code == 502
    assert hs.present is True and hs.accepted_deletes == 0 and pushes == []
    assert await snapshot() == before
