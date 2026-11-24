"""Кто получает имя внутри сети, но до цели не дойдет.

Случай с рабочей панели: имя панели мониторинга вело внутрь сети, доступ к ней
по правилам был у трех устройств. Новый сервер подключили к мешу - он взял DNS
из меша, имя вместо внешнего адреса стало вести на адрес в меше, пира с ним у
сервера нет, и агент мониторинга перестал доходить до панели. Панель молчала.
"""

from app import aclgen, dnsreach
from app.api import settings as api_settings
from tests.test_dnsrecords import _login, _put, hs_env  # noqa: F401 - фикстура


def _node(nid, ip, name, tags=()):
    return {"id": nid, "givenName": name, "ipAddresses": [ip], "tags": list(tags)}


MON = _node("1", "100.100.0.16", "mon")              # цель имени
AGENT = _node("2", "100.100.0.17", "agent-ok")       # сервер с правилом до цели
NEW = _node("3", "100.100.0.19", "newcomer")         # новый сервер без правила
ADMIN = _node("4", "100.100.0.2", "admin-laptop")    # админ-устройство
OFF = _node("5", "100.100.0.20", "no-mesh-dns")      # DNS из меша выключен
GW = _node("6", "100.100.0.4", "gateway", ["tag:xgw-6"])  # шлюз выхода
PHONE = _node("7", "100.100.0.3", "phone")            # устройство с выходом через шлюз
NODES = [MON, AGENT, NEW, ADMIN, OFF, GW, PHONE]
SERVERS = ["100.100.0.16", "100.100.0.17", "100.100.0.19", "100.100.0.20", "100.100.0.4"]
DEVICES = ["100.100.0.2", "100.100.0.3"]


def _policy():
    rules = [
        {"src": {"kind": "node", "value": "2"}, "dst": {"kind": "node", "value": "1"}, "ports": "443"},
        # админ-устройство: как его собирает build_policy - синтетическое правило на все серверы
        {"src": {"kind": "node", "value": "4"}, "dst": {"kind": "servers", "value": ""}, "ports": "*"},
    ]
    return aclgen.generate_policy(
        rules, NODES, server_ips=SERVERS, device_ips=DEVICES,
        exit_via=[{"src": "100.100.0.3", "via": ["xgw-6"]}],
    )


META = {"5": {"mesh_dns": False}, "2": {"mesh_dns": True}}


def _names(lost, name):
    return [n["name"] for n in lost.get(name, [])]


def test_a_node_without_a_rule_to_the_target_is_reported():
    lost = dnsreach.unreachable(_policy(), [{"name": "mon.example.com", "node_id": "1"}], NODES, META)
    got = _names(lost, "mon.example.com")
    assert "newcomer" in got  # тот самый новый сервер
    assert "agent-ok" not in got  # правило до цели на 443 есть
    assert "admin-laptop" not in got  # админ ходит на все серверы
    assert "no-mesh-dns" not in got  # имя ему не раздается
    assert "mon" not in got  # цель сама себе


def test_gateway_visibility_is_not_access():
    """Правило на «глухой» порт 9 только показывает шлюз в карте, сервисы шлюза
    оно не открывает: имя, ведущее на шлюз, устройству все равно недоступно."""
    lost = dnsreach.unreachable(_policy(), [{"name": "nas.mesh", "node_id": "6"}], NODES, META)
    assert "phone" in _names(lost, "nas.mesh")


def test_a_manual_address_of_a_mesh_node_counts_as_that_node():
    lost = dnsreach.unreachable(_policy(), [{"name": "mon.example.com", "ip": "100.100.0.16"}], NODES, META)
    assert "newcomer" in _names(lost, "mon.example.com")


def test_what_cannot_be_judged_or_is_not_handed_out_is_quiet():
    recs = [
        {"name": "lan.example.com", "ip": "192.168.1.10"},  # адрес за роутером
        {"name": "off.example.com", "node_id": "1", "enabled": False},  # выключено
        {"name": "gone.example.com", "node_id": "99"},  # нода удалена
    ]
    assert dnsreach.unreachable(_policy(), recs, NODES, META) == {}
    assert dnsreach.unreachable(None, [{"name": "x", "node_id": "1"}], NODES, META) == {}


def test_a_rule_to_all_servers_counts():
    rules = [{"src": {"kind": "servers", "value": ""}, "dst": {"kind": "node", "value": "1"}, "ports": "443"}]
    policy = aclgen.generate_policy(rules, NODES, server_ips=SERVERS, device_ips=DEVICES)
    lost = dnsreach.unreachable(policy, [{"name": "mon.example.com", "node_id": "1"}], NODES, META)
    got = _names(lost, "mon.example.com")
    assert "newcomer" not in got and "agent-ok" not in got
    assert "phone" in got and "admin-laptop" in got  # устройства в «все серверы» не входят


class _HS:
    async def get_nodes(self):
        return [MON, NEW]


async def test_dns_page_lists_nodes_that_will_not_reach_the_target(client, hs_env, monkeypatch):  # noqa: F811
    monkeypatch.setattr(api_settings, "get_client", lambda _s: _HS())
    token = await _login(client)
    r = await _put(client, token, [{"name": "mon.example.com", "node_id": "1"}])
    assert r.status_code == 200, r.text
    rec = r.json()["records"][0]
    assert rec["unreachable"] == [{"id": "3", "name": "newcomer"}]
