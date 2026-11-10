import json

import pytest

from app import settings_store
from app.api.nodes import _map_node, _normalize_tags
from tests.conftest import ADMIN_PASSWORD


def test_normalize_tags():
    # дописывает tag:, дедуп, пропускает пустые, сохраняет порядок
    assert _normalize_tags(["foo", "tag:bar", "  ", "foo"]) == ["tag:foo", "tag:bar"]


def test_map_node_basic():
    n = {
        "id": "7",
        "name": "laptop",
        "givenName": "laptop-1",
        "ipAddresses": ["100.64.0.5", "fd7a:115c::5"],
        "online": True,
        "lastSeen": "0001-01-01T00:00:00Z",  # нулевое время
        "expiry": "0001-01-01T00:00:00Z",
        "forcedTags": ["tag:srv"],
        "validTags": ["tag:srv", "tag:ok"],
        "registerMethod": "REGISTER_METHOD_AUTH_KEY",
        "createdAt": "2026-07-17T00:00:00Z",
    }
    out = _map_node(n)
    assert out.id == "7"
    assert out.name == "laptop-1"  # givenName приоритетнее name
    assert out.hostname == "laptop"
    assert out.online is True
    assert out.ip_addresses == ["100.64.0.5", "fd7a:115c::5"]
    assert out.last_seen is None and out.expiry is None  # нулевое время → None
    assert out.tags == ["tag:srv", "tag:ok"]  # union(valid, forced), valid первым
    assert out.forced_tags == ["tag:srv"]
    assert out.key_expired is False


def test_map_node_expired_key():
    out = _map_node({"id": "1", "name": "x", "expiry": "2000-01-01T00:00:00Z"})
    assert out.expiry is not None
    assert out.key_expired is True


def test_map_node_routes_exit():
    n = {
        "id": "1",
        "name": "r",
        "availableRoutes": ["0.0.0.0/0", "::/0", "10.0.0.0/24"],
        "approvedRoutes": ["0.0.0.0/0", "::/0"],  # exit одобрен, subnet нет
        "subnetRoutes": ["0.0.0.0/0", "::/0"],
    }
    out = _map_node(n)
    assert out.advertises_exit_node is True
    assert out.is_exit_node is True
    assert out.available_routes == ["0.0.0.0/0", "::/0", "10.0.0.0/24"]
    assert out.subnet_routes == []  # exit-CIDR отфильтрованы из отображения subnet


def test_map_node_routes_subnet():
    n = {
        "id": "2",
        "name": "s",
        "availableRoutes": ["10.0.0.0/24"],
        "approvedRoutes": ["10.0.0.0/24"],
        "subnetRoutes": ["10.0.0.0/24"],
    }
    out = _map_node(n)
    assert out.subnet_routes == ["10.0.0.0/24"]
    assert out.is_exit_node is False
    assert out.advertises_exit_node is False


async def _login(client):
    r = await client.post(
        "/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD}
    )
    return r.json()["access_token"]


async def _policy_nodes(client, monkeypatch):
    from app import policy_apply
    from app.api import nodes as api_nodes
    from app.hs_client import HeadscaleError

    class Control:
        def __init__(self):
            self.nodes = {
                "70": {"id": "70", "ipAddresses": ["100.64.0.70"], "tags": ["tag:servers"]},
                "71": {"id": "71", "ipAddresses": ["100.64.0.71"], "tags": []},
            }
            self.policy = {}
            self.pushes = 0
            self.lose_push_response = None

        async def get_node(self, nid):
            return self.nodes.get(nid)

        async def get_nodes(self):
            return list(self.nodes.values())

        async def approve_routes(self, nid, routes):
            self.nodes[nid]["approvedRoutes"] = routes
            return self.nodes[nid]

        async def set_node_tags(self, nid, tags):
            assert policy_apply._write_lock.locked()
            assert set(tags) <= self.policy["tagOwners"].keys()
            self.nodes[nid]["tags"] = tags
            return self.nodes[nid]

        async def set_policy(self, policy):
            assert policy_apply._write_lock.locked()
            self.policy = json.loads(policy)
            self.pushes += 1
            if self.pushes == self.lose_push_response:
                raise HeadscaleError("ответ Headscale потерян после записи")

    hs = Control()
    monkeypatch.setattr(api_nodes, "get_client", lambda _: hs)
    monkeypatch.setattr(api_nodes, "require_hs", lambda _: None)
    return hs, {"Authorization": f"Bearer {await _login(client)}"}


async def test_admin_revocation_rolls_back_after_headscale_loses_response(client, monkeypatch):
    hs, headers = await _policy_nodes(client, monkeypatch)
    url = "/api/nodes/71/meta"
    grant = await client.post(url, headers=headers, json={"admin": True, "description": "before"})
    assert grant.status_code == 200, grant.text
    assert grant.json()["admin"]
    previous = hs.policy
    assert any(r["src"] == ["100.64.0.71"] for r in previous["acls"])

    hs.lose_push_response = hs.pushes + 1
    failed = await client.post(url, headers=headers, json={"admin": False, "description": "after"})
    assert failed.status_code == 502, failed.text
    assert hs.policy == previous
    unchanged = (await client.get("/api/nodes/71", headers=headers)).json()
    assert unchanged["admin"] is True and unchanged["description"] == "before"

    retried = await client.post(url, headers=headers, json={"admin": False})
    assert retried.status_code == 200, retried.text
    assert not retried.json()["admin"]
    assert not any(r["src"] == ["100.64.0.71"] for r in hs.policy["acls"])


async def test_exit_client_revocation_rolls_back_then_clears_forced_agent(client, monkeypatch):
    hs, headers = await _policy_nodes(client, monkeypatch)
    gateway = await client.post("/api/nodes/70/meta", headers=headers, json={"exit_gateway": True})
    assert gateway.status_code == 200, gateway.text
    force = await client.post("/api/nodes/71/meta", headers=headers, json={"force_exit": "70"})
    assert force.status_code == 200, force.text
    assert force.json()["exit_via"] == ["70"]
    previous = hs.policy
    assert previous["grants"][0]["via"] == ["tag:xgw-70"]

    url = "/api/nodes/70/exit-clients"
    hs.lose_push_response = hs.pushes + 1
    failed = await client.post(url, headers=headers, json={"devices": []})
    assert failed.status_code == 502, failed.text
    assert hs.policy == previous
    factory = client._transport.app.state.session_factory
    async with factory() as session:
        assert (await settings_store.get_node_meta(session))["71"]["force_exit"] == "70"
        assert (await settings_store.get_agent_all(session))["71"]["use_exit"] == "100.64.0.70"

    removed = await client.post(url, headers=headers, json={"devices": []})
    assert removed.status_code == 200, removed.text
    updated = (await client.get("/api/nodes/71", headers=headers)).json()
    assert updated["exit_via"] == [] and updated["force_exit"] == ""
    assert not hs.policy.get("grants")
    async with factory() as session:
        assert "use_exit" not in (await settings_store.get_agent_all(session))["71"]


async def test_roles_restore_previous_tags_and_policy_after_final_push_fails(client, monkeypatch):
    hs, headers = await _policy_nodes(client, monkeypatch)
    url = "/api/nodes/70/tags"
    initial = await client.post(url, headers=headers, json={"tags": ["original"]})
    assert initial.status_code == 200, initial.text
    previous = hs.policy
    hs.lose_push_response = hs.pushes + 2  # объявление прошло, итоговый ACL не подтверждён
    failed = await client.post(url, headers=headers, json={"tags": ["replacement"]})
    assert failed.status_code == 502, failed.text
    assert hs.nodes["70"]["tags"] == ["tag:original"]
    assert hs.policy == previous

    retried = await client.post(url, headers=headers, json={"tags": ["replacement"]})
    assert retried.status_code == 200, retried.text
    assert hs.nodes["70"]["tags"] == ["tag:replacement"]
    assert "tag:replacement" in hs.policy["tagOwners"]
    assert "tag:original" not in hs.policy["tagOwners"]


async def test_cannot_remove_last_server_role_from_gateway(client, monkeypatch):
    hs, headers = await _policy_nodes(client, monkeypatch)
    gateway = await client.post("/api/nodes/70/meta", headers=headers, json={"exit_gateway": True})
    assert gateway.status_code == 200, gateway.text
    removed = await client.post("/api/nodes/70/tags", headers=headers, json={"tags": []})
    assert removed.status_code == 400, removed.text
    assert set(hs.nodes["70"]["tags"]) == {"tag:servers", "tag:xgw-70"}


@pytest.mark.parametrize("alias", ["0x46", "0106"])
async def test_node_id_alias_cannot_bypass_gateway_checks(client, monkeypatch, alias):
    hs, headers = await _policy_nodes(client, monkeypatch)
    gateway = await client.post("/api/nodes/70/meta", headers=headers, json={"exit_gateway": True})
    assert gateway.status_code == 200, gateway.text

    async def get_node(nid):
        return hs.nodes.get("70" if nid == alias else nid)

    set_node_tags = hs.set_node_tags

    async def set_tags(nid, tags):
        return await set_node_tags("70" if nid == alias else nid, tags)

    async def delete_node(nid):
        hs.nodes.pop("70" if nid == alias else nid)

    monkeypatch.setattr(hs, "get_node", get_node)
    monkeypatch.setattr(hs, "set_node_tags", set_tags)
    monkeypatch.setattr(hs, "delete_node", delete_node, raising=False)
    changed = await client.post(f"/api/nodes/{alias}/tags", headers=headers, json={"tags": []})
    assert changed.status_code == 422, changed.text
    deleted = await client.delete(f"/api/nodes/{alias}", headers=headers)
    assert deleted.status_code == 422, deleted.text
    assert set(hs.nodes["70"]["tags"]) == {"tag:servers", "tag:xgw-70"}


async def test_route_removal_rolls_back_after_lost_headscale_response(client, monkeypatch):
    from app.hs_client import HeadscaleError

    hs, headers = await _policy_nodes(client, monkeypatch)
    route = "10.10.0.0/24"
    hs.nodes["70"]["approvedRoutes"] = [route]
    factory = client._transport.app.state.session_factory
    original = {"70": {"routes": [route], "panel_approved": [route]}}
    async with factory() as session:
        await settings_store.set_agent_all(session, original)

    approve = hs.approve_routes

    async def lose_removal(nid, routes):
        node = await approve(nid, routes)
        if not routes:
            raise HeadscaleError("lost response")
        return node

    monkeypatch.setattr(hs, "approve_routes", lose_removal)
    url = "/api/nodes/70/routes"
    failed = await client.post(url, headers=headers, json={"routes": []})
    assert failed.status_code == 502, failed.text
    async with factory() as session:
        assert await settings_store.get_agent_all(session) == original
    assert hs.nodes["70"]["approvedRoutes"] == [route]

    monkeypatch.setattr(hs, "approve_routes", approve)
    removed = await client.post(url, headers=headers, json={"routes": []})
    assert removed.status_code == 200, removed.text
    assert hs.nodes["70"]["approvedRoutes"] == []
    async with factory() as session:
        assert not (await settings_store.get_agent_all(session))["70"]["routes"]


async def test_collector_does_not_restore_manually_revoked_route(client, monkeypatch):
    from app import collector
    from app.config import get_settings

    hs, headers = await _policy_nodes(client, monkeypatch)
    monkeypatch.setattr(collector, "get_client", lambda _: hs)
    route = "10.10.0.0/24"
    hs.nodes["70"].update(availableRoutes=[route], approvedRoutes=[route])
    factory = client._transport.app.state.session_factory
    # Оставляем чужой ручной маршрут, которого не было в первом снимке цикла.
    other = "10.11.0.0/24"
    changed = await client.post("/api/nodes/70/routes", headers=headers, json={"routes": [other]})
    assert changed.status_code == 200, changed.text
    async with factory() as session:
        await settings_store.set_agent_all(session, {"70": {"routes": ["10.12.0.0/24"]}})
    hs.nodes["70"]["availableRoutes"].append("10.12.0.0/24")
    await collector._auto_approve_requested(factory, get_settings())
    assert hs.nodes["70"]["approvedRoutes"] == [other, "10.12.0.0/24"]


@pytest.mark.parametrize("cancel", [False, True])
async def test_route_kind_and_acl_roll_back_if_policy_push_is_interrupted(client, monkeypatch, cancel):
    from types import SimpleNamespace

    from anyio import CancelScope
    from anyio.lowlevel import checkpoint

    from app.api.nodes import set_routes
    from app.hs_client import HeadscaleError
    from app.schemas import NodeRoutesIn

    hs, headers = await _policy_nodes(client, monkeypatch)
    hs.nodes["72"] = {"id": "72", "ipAddresses": ["100.64.0.72"], "tags": []}
    factory = client._transport.app.state.session_factory
    async with factory() as session:
        await settings_store.set_acl_rules(session, [{
            "src": {"kind": "node", "value": "71"},
            "dst": {"kind": "servers", "value": ""},
            "ports": "22",
        }])
    push = hs.set_policy
    previous = None
    scope = CancelScope()

    async def interrupt_first_push(policy):
        nonlocal previous
        await push(policy)
        if previous is None:
            previous = hs.policy
            if cancel:
                scope.cancel()
                await checkpoint()
            raise HeadscaleError("lost policy response")

    monkeypatch.setattr(hs, "set_policy", interrupt_first_push)
    url = "/api/nodes/72/routes"
    body = {"routes": ["10.13.0.0/24"]}
    if cancel:
        async with factory() as session:
            with scope:
                await set_routes(
                    "72", NodeRoutesIn(**body), None, SimpleNamespace(username="admin"), session
                )
        assert scope.cancelled_caught
    else:
        failed = await client.post(url, headers=headers, json=body)
        assert failed.status_code == 502, failed.text
    assert hs.nodes["72"]["approvedRoutes"] == []
    assert "100.64.0.72:22" in previous["acls"][0]["dst"]
    assert "100.64.0.72:22" not in hs.policy["acls"][0]["dst"]

    retried = await client.post(url, headers=headers, json=body)
    assert retried.status_code == 200 and retried.json()["kind"] == "server"
    assert "100.64.0.72:22" in hs.policy["acls"][0]["dst"]


async def test_single_exit_prefix_approves_both_families(client, monkeypatch):
    hs, headers = await _policy_nodes(client, monkeypatch)
    resp = await client.post(
        "/api/nodes/70/routes", headers=headers, json={"routes": ["0.0.0.0/0"]}
    )
    assert resp.status_code == 200, resp.text
    assert hs.nodes["70"]["approvedRoutes"] == ["0.0.0.0/0", "::/0"]


async def test_reconnect_returns_prepared_script_after_headscale_delete_response_is_lost(
    client, monkeypatch,
):
    from app.hs_client import HeadscaleError

    hs, headers = await _policy_nodes(client, monkeypatch)
    hs.nodes["73"] = {
        "id": "73", "givenName": "gateway", "tags": ["tag:edge", "tag:xgw-73"],
    }
    observed_roles = []
    get_node = hs.get_node

    async def read(nid):
        node = await get_node(nid)
        if not node:
            raise HeadscaleError("node not found", status_code=404)
        return node

    async def ensure_user(name):
        return {"id": "1", "name": name}

    async def create_key(uid, *, acl_tags, **options):
        observed_roles.extend(acl_tags)
        return {"id": "123", "key": "hskey-auth-test"}

    async def delete_and_lose_response(nid):
        hs.nodes.pop(nid)
        raise HeadscaleError("lost delete response")

    monkeypatch.setattr(hs, "get_node", read)
    monkeypatch.setattr(hs, "ensure_user", ensure_user, raising=False)
    monkeypatch.setattr(hs, "create_preauthkey", create_key, raising=False)
    monkeypatch.setattr(hs, "delete_node", delete_and_lose_response, raising=False)
    resp = await client.post("/api/nodes/73/reconnect", headers=headers, json={"os": "linux"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["key_id"] == "123"
    assert "hskey-auth-test" in resp.json()["script"]
    assert observed_roles == ["tag:edge"]


async def test_direction_rechecks_gateway_kind_after_resolving_destination(client, monkeypatch):
    from app import routing
    from app.api import routing as api_routing

    hs, headers = await _policy_nodes(client, monkeypatch)
    monkeypatch.setattr(api_routing, "get_client", lambda _: hs)
    monkeypatch.setattr(api_routing, "require_hs", lambda _: None)

    async def resolve_after_kind_changes(dst):
        changed = await client.post("/api/nodes/70/meta", headers=headers, json={
            "kind": "device", "exit_gateway": False,
        })
        assert changed.status_code == 200, changed.text
        return ["203.0.113.9"], ""

    monkeypatch.setattr(routing, "resolve_dst", resolve_after_kind_changes)
    response = await client.post("/api/routing", headers=headers, json={
        "src": ["71"], "via": "70", "dst": "edge.example",
    })
    assert response.status_code == 400, response.text
    assert "Через устройство" in response.json()["detail"]
    factory = client._transport.app.state.session_factory
    async with factory() as session:
        assert not await settings_store.get_routing(session)


async def test_direction_delete_retries_after_headscale_loses_acl_response(client, monkeypatch):
    from app.api import routing as api_routing

    hs, headers = await _policy_nodes(client, monkeypatch)
    hs.nodes["75"] = {"id": "75", "ipAddresses": ["100.64.0.75"], "tags": ["tag:servers"]}
    monkeypatch.setattr(api_routing, "get_client", lambda _: hs)
    monkeypatch.setattr(api_routing, "require_hs", lambda _: None)
    created = await client.post("/api/routing", headers=headers, json={
        "src": ["71"], "via": "75", "dst": "203.0.113.8",
    })
    assert created.status_code == 200, created.text
    previous = hs.policy
    assert hs.nodes["75"]["approvedRoutes"] == ["203.0.113.8/32"]
    did = created.json()["id"]

    hs.lose_push_response = hs.pushes + 1
    url = f"/api/routing/{did}"
    failed = await client.delete(url, headers=headers)
    assert failed.status_code == 502, failed.text
    assert hs.policy == previous
    factory = client._transport.app.state.session_factory
    async with factory() as session:
        assert did in await settings_store.get_routing(session)

    removed = await client.delete(url, headers=headers)
    assert removed.status_code == 204, removed.text
    assert hs.nodes["75"]["approvedRoutes"] == []
    assert "203.0.113.8" not in json.dumps(hs.policy)
    async with factory() as session:
        assert did not in await settings_store.get_routing(session)


async def test_agent_config_update_keeps_concurrent_route_ownership(client, monkeypatch):
    import asyncio

    from app import routing
    from app.api import agent as api_agent

    hs, headers = await _policy_nodes(client, monkeypatch)
    monkeypatch.setattr(api_agent, "get_client", lambda _: hs)
    monkeypatch.setattr(api_agent, "require_hs", lambda _: None)
    monkeypatch.setattr(settings_store, "_write_lock", asyncio.Lock())
    route, other = "10.10.0.0/24", "10.11.0.0/24"
    hs.nodes["74"] = {"id": "74", "availableRoutes": [route], "tags": ["tag:servers"]}
    factory = client._transport.app.state.session_factory
    async with factory() as session:
        cfg = await settings_store.get_agent_all(session)
        cfg["74"] = {"routes": [route], "token": "test-token"}
        await settings_store.set_agent_all(session, cfg)

    reading_nodes = asyncio.Event()
    resume_reconcile = asyncio.Event()
    get_nodes = hs.get_nodes

    async def paused_nodes():
        reading_nodes.set()
        await resume_reconcile.wait()
        return await get_nodes()

    async def reconcile():
        async with factory() as session:
            await routing.reconcile_approvals(session, hs)

    monkeypatch.setattr(hs, "get_nodes", paused_nodes)
    collect = asyncio.create_task(reconcile())
    try:
        await asyncio.wait_for(reading_nodes.wait(), timeout=5)
        put = asyncio.create_task(client.put(
            "/api/agent/74", headers=headers, json={"routes": [route, other], "exit_node": False},
        ))
        await asyncio.wait({put}, timeout=0.05)
        assert not put.done()
    finally:
        resume_reconcile.set()
        await asyncio.wait_for(collect, timeout=5)
    resp = await asyncio.wait_for(put, timeout=5)
    assert resp.status_code == 200, resp.text
    async with factory() as session:
        saved = (await settings_store.get_agent_all(session))["74"]
    assert saved["routes"] == [route, other]
    assert saved["panel_approved"] == [route]


async def test_nodes_requires_auth(client):
    r = await client.get("/api/nodes")
    assert r.status_code == 401


async def test_nodes_503_without_key(client):
    # в тестах NODEROOST_HEADSCALE_API_KEY пуст → require_hs отдаёт 503
    token = await _login(client)
    r = await client.get("/api/nodes", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 503


async def test_users_endpoint_is_gone(client):
    # раздел «Пользователи» выпилен: субъект доступа — сама нода, роутера больше нет
    r = await client.get("/api/users")
    assert r.status_code == 404


async def test_node_meta_store(session):
    from app import settings_store

    # описание + тип сохраняются и подхватываются _map_node
    await settings_store.set_node_meta(session, "5", "  сервер мониторинга  ", "server")
    meta = await settings_store.get_node_meta(session)
    assert meta["5"] == {"description": "сервер мониторинга", "kind": "server"}
    out = _map_node({"id": "5", "name": "x"}, meta)
    assert out.description == "сервер мониторинга"
    assert out.kind == "server"

    # ручной тип «device» перебивает авто-определение (у ноды есть теги → авто server)
    await settings_store.set_node_meta(session, "5", "", "device")
    meta = await settings_store.get_node_meta(session)
    assert meta["5"] == {"kind": "device"}
    assert _map_node({"id": "5", "name": "x", "forcedTags": ["tag:srv"]}, meta).kind == "device"

    # флаг «админ» сохраняется и читается в _map_node
    await settings_store.set_node_meta(session, "7", "", "device", admin=True)
    meta = await settings_store.get_node_meta(session)
    assert meta["7"] == {"kind": "device", "admin": True}
    assert _map_node({"id": "7", "name": "z"}, meta).admin is True
    assert _map_node({"id": "8", "name": "q"}, meta).admin is False

    # пустые поля убирают запись целиком
    await settings_store.set_node_meta(session, "5", "  ", "")
    assert "5" not in await settings_store.get_node_meta(session)


def test_guess_kind():
    # теги / exit / анонс маршрутов → сервер; чистая нода → устройство
    assert _map_node({"id": "1", "name": "a", "forcedTags": ["tag:web"]}).kind == "server"
    assert _map_node({"id": "2", "name": "b", "approvedRoutes": ["0.0.0.0/0"]}).kind == "server"
    # АНОНС ноды типом больше не управляет: availableRoutes нода выставляет себе
    # сама (`tailscale set --advertise-routes`), и раньше одной такой командой
    # взломанное устройство переводило себя в «серверы» — выпадало из изоляции и
    # получало доступы, выданные всем серверам. Судим только по одобренному.
    assert _map_node({"id": "3", "name": "c", "availableRoutes": ["10.0.0.0/24"]}).kind == "device"
    assert _map_node({"id": "4", "name": "d", "approvedRoutes": ["10.0.0.0/24"]}).kind == "server"
    assert _map_node({"id": "4", "name": "d"}).kind == "device"


def test_tags_read_from_the_029_field():
    """headscale 0.29 отдаёт теги ОДНИМ полем «tags». Мы читали forcedTags/
    validTags, которых там больше нет: тег навешивался, но панель его не видела,
    а следующее сохранение затирало пустым списком."""
    n = _map_node({"id": "9", "name": "srv", "tags": ["tag:vpn"]})
    assert n.tags == ["tag:vpn"]
    assert n.forced_tags == ["tag:vpn"]  # в 0.29 все теги редактируемые


def test_tags_still_read_from_the_old_fields():
    """Совместимость со старым headscale: если пришли forcedTags/validTags —
    читаем их, объединяя без дублей."""
    n = _map_node(
        {"id": "9", "name": "srv", "forcedTags": ["tag:a"], "validTags": ["tag:a", "tag:b"]}
    )
    assert n.tags == ["tag:a", "tag:b"]
    # редактируемые — только forcedTags: validTags мог выдать ключ подключения
    assert n.forced_tags == ["tag:a"]


# --- частичная запись меты не должна стирать настройки безопасности ---


async def test_meta_write_is_a_merge_not_an_overwrite(session):
    """Перетаскивание карточки в другую группу шлёт ТОЛЬКО группу. Раньше запись
    перетиралась целиком, и вместе с группой молча слетали «не слать алерты»,
    «шлюз выхода» и принудительный туннель — настройка безопасности исчезала как
    побочный эффект перетаскивания."""
    await settings_store.set_node_meta(
        session, "7", description="сервер", kind="server", muted=True,
        exit_gateway=True, exit_via=["9"], force_exit="9",
    )
    await settings_store.set_node_meta(session, "7", group="Acme")  # только группа
    e = (await settings_store.get_node_meta(session))["7"]
    assert e["group"] == "Acme"
    assert e["muted"] is True and e["exit_gateway"] is True
    assert e["force_exit"] == "9" and e["exit_via"] == ["9"]
    assert e["description"] == "сервер" and e["kind"] == "server"


async def test_explicit_false_still_clears(session):
    """«Слияние» не должно мешать снять галку: явно переданное false стирает."""
    await settings_store.set_node_meta(session, "8", muted=True, exit_gateway=True)
    await settings_store.set_node_meta(session, "8", muted=False)
    e = (await settings_store.get_node_meta(session))["8"]
    assert "muted" not in e and e["exit_gateway"] is True  # снято только muted


async def test_hidden_node_is_marked_and_silenced(session):
    """Скрытая нода - это «пока не нужна», а не «сломалась».

    Её нет в счётчиках списка, и коллектор глушит её наравне с заглушённой:
    алерт о том, что отложенная машина не отвечает, человек уже не ждёт.
    """
    from app import settings_store

    await settings_store.set_node_meta(session, "11", kind="server", hidden=True)
    meta = await settings_store.get_node_meta(session)
    assert meta["11"]["hidden"] is True

    silent = {
        nid
        for nid, e in meta.items()
        if isinstance(e, dict) and (e.get("muted") or e.get("hidden"))
    }
    assert "11" in silent

    # вернули в список - запись чистится, нода снова на виду и алертит
    await settings_store.set_node_meta(session, "11", kind="server", hidden=False)
    meta = await settings_store.get_node_meta(session)
    assert "hidden" not in meta["11"]


async def test_clear_node_meta_wipes_everything(session):
    """Удаление ноды должно уносить и её заметку целиком — иначе при переиспользовании
    id новая нода унаследовала бы чужие флаги."""
    await settings_store.set_node_meta(session, "9", muted=True, exit_gateway=True)
    await settings_store.clear_node_meta(session, "9")
    assert "9" not in await settings_store.get_node_meta(session)


def test_kind_ctx_keeps_stored_kind_when_request_has_none():
    """Галку «шлюз выхода» ставят отдельным запросом, без поля kind. Тип для
    проверки обязан браться из сохранённой меты: сервер, помеченный руками, но
    без тегов и маршрутов, авто-определяется устройством — и панель отказывала
    ровно той ноде, ради которой ручной выбор типа и существует."""
    from app.api.nodes import _kind_ctx

    stored = {"42": {"kind": "server", "description": "прод"}}
    ctx = _kind_ctx(stored, "42", None)
    assert ctx["42"]["kind"] == "server"
    assert ctx["42"]["description"] == "прод"  # чужие поля не теряем
    # явный kind в запросе перекрывает сохранённый
    assert _kind_ctx(stored, "42", "device")["42"]["kind"] == "device"
    # ноды в мете ещё нет — пустая запись, дальше сработает авто-определение
    assert _kind_ctx(stored, "99", None)["99"] == {}


def test_marker_tag_replaces_empty_tag_list():
    """headscale 0.29 не снимает с ноды последний тег («cannot remove all tags»),
    поэтому выключить галку шлюза или убрать последнюю роль было нельзя — панель
    отвечала 502. Пустой список заменяем служебным маркером."""
    from app import exitvia

    assert exitvia.keep_tagged([]) == [exitvia.MARKER_TAG]
    assert exitvia.keep_tagged(["tag:db"]) == ["tag:db"]
    # маркер служебный: в ролях не показывается
    assert exitvia.is_service_tag(exitvia.MARKER_TAG)


def test_marker_tag_does_not_make_a_node_a_server():
    """Маркер остаётся на ноде только из-за ограничения headscale — считать
    ноду сервером он не должен, иначе снятие последней роли молча меняло бы тип."""
    from app.nodekind import guess_kind

    assert guess_kind({"tags": ["tag:noderoost"]}) == "device"
    assert guess_kind({"tags": ["tag:db"]}) == "server"


async def test_reconnect_keeps_panel_notes(session):
    """«Переподключить» удаляет ноду в headscale и заводит заново — с новым id.
    Заметки панели привязаны к id, поэтому нода возвращалась чистой: сервер
    считался устройством, админ-устройство теряло права, описание пропадало."""
    from app import settings_store

    await settings_store.set_node_meta(session, "7", kind="server", admin=True,
                                       description="прод-сервер")
    meta = await settings_store.get_node_meta(session)
    await settings_store.stash_node_meta(session, "db-1", meta["7"], key_id="100")
    await settings_store.clear_node_meta(session, "7")

    # нода вернулась под другим id
    moved = await settings_store.claim_pending_meta(
        session, [{"id": "42", "givenName": "db-1", "preAuthKey": {"id": "100"}}]
    )
    assert moved == 1
    meta = await settings_store.get_node_meta(session)
    assert meta["42"]["kind"] == "server"
    assert meta["42"]["admin"] is True
    assert meta["42"]["description"] == "прод-сервер"

    # повторный вызов ничего не дублирует и не затирает
    assert await settings_store.claim_pending_meta(
        session, [{"id": "42", "givenName": "db-1", "preAuthKey": {"id": "100"}}]
    ) == 0


async def test_reconnect_repoints_rules_and_routes(session):
    """Переподключение меняет id ноды — а на него ссылаются правила доступа.

    Без перевода ссылок правило остаётся в панели видимым, но указывает на
    несуществующую ноду: админ уверен, что доступ есть, а его нет.
    """
    from app import settings_store

    await settings_store.set_acl_rules(session, [
        {"src": {"kind": "node", "value": "7"},
         "dst": {"kind": "node", "value": "9"}, "ports": "22"},
        {"src": {"kind": "node", "value": "9"},
         "dst": {"kind": "node", "value": "7"}, "ports": "443"},
    ])
    await settings_store.set_routing(session, {
        "d1": {"src_kind": "node", "src": ["7", "9"], "dst": "1.2.3.4", "via": "9"},
        "d2": {"src_kind": "node", "src": ["9"], "dst": "1.2.3.5", "via": "7"},
    })
    await settings_store.set_agent_all(session, {"7": {"token": "t", "routes": ["10.0.0.0/24"]}})
    await settings_store.stash_node_meta(session, "db-1", {"kind": "server"}, old_id="7", key_id="100")

    await settings_store.claim_pending_meta(session, [{"id": "42", "givenName": "db-1", "preAuthKey": {"id": "100"}}])

    rules = await settings_store.get_acl_rules(session)
    assert rules[0]["src"]["value"] == "42" and rules[0]["dst"]["value"] == "9"
    assert rules[1]["src"]["value"] == "9" and rules[1]["dst"]["value"] == "42"

    routing = await settings_store.get_routing(session)
    assert routing["d1"]["src"] == ["42", "9"] and routing["d1"]["via"] == "9"
    assert routing["d2"]["via"] == "42"

    agents = await settings_store.get_agent_all(session)
    assert "7" not in agents and agents["42"]["routes"] == ["10.0.0.0/24"]


async def test_claim_pending_meta_reads_old_format(session):
    """Заметки, отложенные до обновления панели, лежат в старом формате."""
    from app import settings_store

    await settings_store._set_raw(
        session, settings_store.PENDING_META_KEY,
        '{"db-1": {"description": "server", "kind": "server", "admin": true}}',
    )
    assert await settings_store.claim_pending_meta(
        session, [{"id": "42", "givenName": "db-1"}]
    ) == 1
    meta = await settings_store.get_node_meta(session)
    assert meta["42"] == {"description": "server"}


async def test_keyless_reconnect_cannot_move_agent_or_permissions_by_name(session):
    await settings_store.stash_node_meta(
        session, "db-1", {"kind": "server", "admin": True}, old_id="7"
    )
    await settings_store.set_agent_all(session, {"7": {"token": "test-token"}})
    rules = [{"src": {"kind": "node", "value": "7"},
              "dst": {"kind": "servers", "value": ""}, "ports": "22"}]
    await settings_store.set_acl_rules(session, rules)

    assert await settings_store.claim_pending_meta(session, [
        {"id": "42", "givenName": "db-1", "preAuthKey": {"id": "999"}},
    ]) == 0
    assert "42" not in await settings_store.get_node_meta(session)
    assert await settings_store.get_agent_all(session) == {"7": {"token": "test-token"}}
    assert await settings_store.get_acl_rules(session) == rules


async def test_forget_node_refs_clears_rules_routes_agent(session):
    """Удалённая нода не возвращается — её следы только вводят в заблуждение."""
    from app import settings_store

    await settings_store.set_acl_rules(session, [
        {"src": {"kind": "node", "value": "7"}, "dst": {"kind": "tag", "value": "prod"},
         "ports": "22"},
        {"src": {"kind": "node", "value": "9"}, "dst": {"kind": "node", "value": "7"},
         "ports": "443"},
        {"src": {"kind": "node", "value": "9"}, "dst": {"kind": "tag", "value": "prod"},
         "ports": "80"},                                   # чужое — не трогать
    ])
    await settings_store.set_routing(session, {
        "d1": {"src_kind": "node", "src": ["7"], "dst": "1.2.3.4", "via": "9"},
        "d2": {"src_kind": "node", "src": ["7", "9"], "dst": "1.2.3.5", "via": "9"},
        "d3": {"src_kind": "node", "src": ["9"], "dst": "1.2.3.6", "via": "7"},
        "d4": {"src_kind": "devices", "src": [], "dst": "1.2.3.7", "via": "9"},
    })
    await settings_store.set_agent_all(session, {"7": {"token": "t"}, "9": {"token": "u"}})

    await settings_store.forget_node_refs(session, "7")

    rules = await settings_store.get_acl_rules(session)
    assert len(rules) == 1 and rules[0]["ports"] == "80"

    routing = await settings_store.get_routing(session)
    assert "d1" not in routing        # источников не осталось
    assert routing["d2"]["src"] == ["9"]
    assert "d3" not in routing        # выход был через удалённую ноду
    assert "d4" in routing            # группа целиком — ноду не перечисляет

    agents = await settings_store.get_agent_all(session)
    assert "7" not in agents and "9" in agents


async def test_agent_alert_skips_deleted_node(session):
    """Агент удалённой ноды молчит по уважительной причине."""
    from datetime import datetime, timedelta, timezone

    from app import alerts
    from app.config import Settings

    alerts._agent_silent.clear()
    long_ago = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    agents = {"7": {"last_poll": long_ago}}
    st = Settings(agent_silent_minutes=10)
    assert await alerts.reconcile_agents(session, st, agents, {}, {}, set()) == []


def test_node_name_normalized_to_one_dns_label():
    """Имя ноды — то, из чего собирается имя в MagicDNS."""
    import pytest
    from app.schemas import NodeRenameIn

    assert NodeRenameIn(name="  WEB-Fra ").name == "web-fra"   # регистр не значим
    for bad in ("srv.prod", "web_1", "-web", "web-", "плохо", "a b", "x" * 64):
        with pytest.raises(Exception):
            NodeRenameIn(name=bad)


async def test_rename_refuses_name_taken_in_other_case(client, monkeypatch):
    """«WEB-FRA» рядом с «web-fra» — в MagicDNS это одно имя на двоих."""
    from app.api import nodes as api_nodes

    class _HS:
        async def get_nodes(self):
            return [{"id": "2", "givenName": "web-fra"}, {"id": "3", "givenName": "db"}]

        async def rename_node(self, node_id, name):  # не должен быть вызван
            raise AssertionError("переименование не должно дойти до headscale")

    monkeypatch.setattr(api_nodes, "get_client", lambda _s: _HS())
    monkeypatch.setattr(api_nodes, "require_hs", lambda _s: None)
    r = await client.post("/api/auth/login",
                          json={"username": "admin", "password": ADMIN_PASSWORD})
    tok = r.json()["access_token"]
    resp = await client.post("/api/nodes/3/rename", json={"name": "WEB-FRA"},
                             headers={"Authorization": f"Bearer {tok}"})
    assert resp.status_code == 409
    assert "занято" in resp.json()["detail"]


async def test_headscale_input_errors_are_not_502():
    """Жалоба headscale на ввод — это отказ пользователю, а не поломка шлюза."""
    import pytest
    from fastapi import HTTPException

    from app.hs_client import HeadscaleError
    from app.hs_util import hs_call

    async def boom(text):
        raise HeadscaleError(text)

    with pytest.raises(HTTPException) as e:
        await hs_call(boom("headscale 500: node name is not unique: web-fra"))
    assert e.value.status_code == 409

    with pytest.raises(HTTPException) as e:
        await hs_call(boom('headscale 500: "a.b" is not a valid DNS label'))
    assert e.value.status_code == 400

    with pytest.raises(HTTPException) as e:
        await hs_call(boom("headscale 500: internal database failure"))
    assert e.value.status_code == 502   # настоящая поломка остаётся 502


def test_roles_are_lowercase():
    """headscale принимает только теги в нижнем регистре, и «PROD» с «prod» —
    одна и та же роль, а не две."""
    assert _normalize_tags([" PROD ", "prod", "tag:Web"]) == ["tag:prod", "tag:web"]


def test_active_routes_do_not_depend_on_headscale_field():
    """headscale 0.29 отдаёт subnetRoutes пустым, когда спрашиваешь ОДНУ ноду.

    Карточка ноды показывала работающий маршрут как неработающий. Считаем сами:
    действует то, что одобрено И анонсируется.
    """
    n = {
        "id": "3", "name": "db", "givenName": "db", "ipAddresses": ["100.64.0.3"],
        "availableRoutes": ["10.88.0.0/24", "10.99.0.0/24"],
        "approvedRoutes": ["10.88.0.0/24", "0.0.0.0/0"],
        "subnetRoutes": [],                      # как отвечает headscale
    }
    out = _map_node(n)
    assert out.subnet_routes == ["10.88.0.0/24"]  # 10.99 не одобрен, exit не в счёт
    assert out.is_exit_node is True


def test_expired_key_means_offline():
    """headscale держит online у ноды, которую сам отключил по истечении ключа.

    Админ отзывал доступ, а панель показывала машину в сети — и алерт «сервер
    упал» не приходил, потому что для него нода оставалась живой.
    """
    from datetime import datetime, timedelta, timezone

    from app.nodekind import is_online

    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat().replace("+00:00", "Z")

    assert is_online({"online": True, "expiry": future}) is True
    assert is_online({"online": True, "expiry": past}) is False
    assert is_online({"online": True, "expiry": "0001-01-01T00:00:00Z"}) is True  # без срока
    assert is_online({"online": False, "expiry": future}) is False

    out = _map_node({"id": "3", "name": "db", "givenName": "db",
                     "ipAddresses": ["100.64.0.3"], "online": True, "expiry": past})
    assert out.online is False and out.key_expired is True


async def test_dropping_gateway_clears_devices_choice(session):
    """Снятая галка «шлюз выхода» не должна оставлять за собой связей.

    Устройство показывало «через шлюзы: web-fra», хотя грант исчез, а с
    принудительным выходом агент гнал туда весь трафик — и устройство теряло
    сеть молча.
    """
    from app import settings_store

    await settings_store.set_node_meta(session, "2", kind="server", exit_gateway=True)
    await settings_store.set_node_meta(session, "1", kind="device", exit_via=["2", "5"])
    await settings_store.set_node_meta(session, "4", kind="device", exit_via=["2"],
                                       force_exit="2")
    await settings_store.set_node_meta(session, "6", kind="device", exit_via=["5"])

    unforced = await settings_store.drop_gateway(session, "2")

    meta = await settings_store.get_node_meta(session)
    assert meta["1"]["exit_via"] == ["5"]          # другой шлюз остался
    assert "exit_via" not in meta.get("4", {})     # выбор был только этот
    assert "force_exit" not in meta.get("4", {})
    assert meta["6"]["exit_via"] == ["5"]          # чужие связи не тронуты
    assert unforced == ["4"]                        # агенту сбросить use_exit

