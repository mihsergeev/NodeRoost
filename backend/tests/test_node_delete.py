import json
from copy import deepcopy

import pytest

from app import settings_store
from app.api import nodes as api_nodes
from app.bootstrap import ensure_admin
from app.config import get_settings
from app.hs_client import HeadscaleError
from tests.conftest import ADMIN_PASSWORD


@pytest.mark.parametrize("delete_fails", [False, True])
async def test_delete_gateway_cleans_dependents_only_after_headscale_accepts(
    client, session_factory, monkeypatch, delete_fails
):
    class Headscale:
        deleted = []

        async def delete_node(self, node_id):
            if delete_fails:
                raise HeadscaleError("headscale 503: staging failure")
            self.deleted.append(node_id)

    hs = Headscale()
    policy_calls = []

    async def apply_policy(*_args):
        policy_calls.append(True)

    monkeypatch.setattr(api_nodes, "get_client", lambda _settings: hs)
    monkeypatch.setattr(api_nodes, "require_hs", lambda _settings: None)
    monkeypatch.setattr(api_nodes, "apply_policy", apply_policy)
    app = client._transport.app
    monkeypatch.setattr(app.state, "session_factory", session_factory)
    await ensure_admin(session_factory, get_settings())
    meta = {
        "2": {"kind": "server", "exit_gateway": True, "group": "Acme"},
        "5": {"kind": "server", "exit_gateway": True},
        "1": {"kind": "device", "description": "test client", "muted": True,
              "hidden": True, "group": "Lab", "subgroup": "QA",
              "exit_via": ["2", "5"], "force_exit": "2"},
        "4": {"kind": "device", "exit_via": ["2"]},
        "6": {"kind": "device", "exit_via": ["5"], "force_exit": "5"},
    }
    agents = {
        "2": {"token": "test-gateway-token", "exit": True},
        "1": {"token": "test-client-token", "use_exit": "100.64.10.2"},
        "6": {"token": "test-other-token", "use_exit": "100.64.10.5"},
    }
    rules = [
        {"src": {"kind": "node", "value": "1"},
         "dst": {"kind": "node", "value": "2"}, "ports": "443"},
        {"src": {"kind": "node", "value": "1"},
         "dst": {"kind": "node", "value": "5"}, "ports": "22"},
    ]
    routing = {
        "removed": {"src_kind": "node", "src": ["1"], "via": "2"},
        "kept": {"src_kind": "node", "src": ["2", "6"], "via": "5"},
    }
    records = [{"name": "gateway.example.test", "node_id": "2"},
               {"name": "other.example.test", "node_id": "5"}]
    async with app.state.session_factory() as session:
        await settings_store.set_raw(session, settings_store.NODE_META_KEY,
                                     json.dumps(meta))
        await settings_store.set_agent_all(session, agents)
        await settings_store.set_acl_rules(session, rules)
        await settings_store.set_routing(session, routing)
        await settings_store.set_dns_records(session, records)

    login = await client.post("/api/auth/login", json={"username": "admin",
                                                     "password": ADMIN_PASSWORD})
    response = await client.delete(
        "/api/nodes/2", headers={"Authorization": f"Bearer {login.json()['access_token']}"}
    )
    async with app.state.session_factory() as session:
        actual_meta = await settings_store.get_node_meta(session)
        actual_agents = await settings_store.get_agent_all(session)
        actual_rules = await settings_store.get_acl_rules(session)
        actual_routing = await settings_store.get_routing(session)
        actual_records = await settings_store.get_dns_records(session)

    if delete_fails:
        assert response.status_code == 502
        assert hs.deleted == [] and policy_calls == []
        assert actual_meta == meta and actual_agents == agents
        assert actual_rules == rules and actual_routing == routing
        assert actual_records == records
        return

    assert response.status_code == 204
    assert hs.deleted == ["2"] and policy_calls == [True]
    expected_meta = deepcopy(meta)
    expected_meta.pop("2")
    expected_meta["1"]["exit_via"] = ["5"]
    expected_meta["1"].pop("force_exit")
    expected_meta["4"].pop("exit_via")
    assert actual_meta == expected_meta
    expected_agents = deepcopy(agents)
    expected_agents.pop("2")
    expected_agents["1"].pop("use_exit")
    assert actual_agents == expected_agents
    assert actual_rules == rules[1:]
    assert actual_routing == {"kept": {"src_kind": "node", "src": ["6"], "via": "5"}}
    assert actual_records == records[1:]
