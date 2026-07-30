"""Направления «кто → куда → через какую ноду»."""

from datetime import datetime, timedelta, timezone

import pytest

from app import routing, settings_store


async def test_resolve_passes_literal_ip_through():
    assert await routing.resolve_dst("203.0.113.7") == (["203.0.113.7"], "")
    assert await routing.resolve_dst("10.0.0.0/24") == (["10.0.0.0/24"], "")


async def test_resolve_rejects_ipv6():
    ips, err = await routing.resolve_dst("2a03:b0c0::1")
    assert ips == [] and "IPv6" in err


def test_routes_grouped_by_exit_node_as_cidr():
    directions = {
        "a": {"src": "1", "dst": "x", "via": "21", "ips": ["203.0.113.7"]},
        "b": {"src": "2", "dst": "y", "via": "21", "ips": ["1.2.3.4"]},
        "c": {"src": "1", "dst": "z", "via": "20", "ips": ["10.0.0.0/24"]},
    }
    assert routing.routes_by_node(directions) == {
        "21": ["1.2.3.4/32", "203.0.113.7/32"],
        "20": ["10.0.0.0/24"],
    }


def test_acl_rules_are_per_source_and_address():
    directions = {
        "a": {"src": "19", "dst": "x", "via": "21", "ips": ["1.2.3.4", "5.6.7.8"], "ports": "443"}
    }
    rules = routing.acl_rules(directions)
    assert [r["dst"]["value"] for r in rules] == ["1.2.3.4", "5.6.7.8"]
    assert {r["src"]["value"] for r in rules} == {"19"}
    assert {r["ports"] for r in rules} == {"443"}


def test_direction_without_via_or_src_is_skipped():
    """Битая запись не должна ни ломать сборку политики, ни давать доступ."""
    assert routing.routes_by_node({"a": {"src": "1", "ips": ["1.2.3.4"]}}) == {}
    assert routing.acl_rules({"a": {"via": "21", "ips": ["1.2.3.4"]}}) == []


async def test_refresh_updates_changed_address(session, monkeypatch):
    async def fake(dst):
        return ["9.9.9.9"], ""

    monkeypatch.setattr(routing, "resolve_dst", fake)
    await settings_store.set_routing(
        session, {"a": {"src": "1", "dst": "site.example", "via": "21", "ips": ["1.1.1.1"]}}
    )
    assert await routing.refresh(session) == ["a"]
    assert (await settings_store.get_routing(session))["a"]["ips"] == ["9.9.9.9"]


async def test_refresh_keeps_addresses_when_dns_fails(session, monkeypatch):
    """Временный отказ резолвера не повод рвать рабочий маршрут — иначе разовая
    сетевая икота обрубала бы доступ до следующего успешного резолва."""

    async def boom(dst):
        return [], "Temporary failure in name resolution"

    monkeypatch.setattr(routing, "resolve_dst", boom)
    await settings_store.set_routing(
        session, {"a": {"src": "1", "dst": "site.example", "via": "21", "ips": ["1.1.1.1"]}}
    )
    assert await routing.refresh(session) == []
    saved = (await settings_store.get_routing(session))["a"]
    assert saved["ips"] == ["1.1.1.1"]
    assert "resolution" in saved["error"]


async def test_refresh_skips_fresh_entries(session, monkeypatch):
    calls = []

    async def fake(dst):
        calls.append(dst)
        return ["9.9.9.9"], ""

    monkeypatch.setattr(routing, "resolve_dst", fake)
    fresh = datetime.now(timezone.utc).isoformat()
    await settings_store.set_routing(
        session,
        {"a": {"src": "1", "dst": "site.example", "via": "21", "ips": ["1.1.1.1"], "resolved_at": fresh}},
    )
    assert await routing.refresh(session) == []
    assert calls == []
    # ...но по явному запросу проверяем всё равно
    assert await routing.refresh(session, force=True) == ["a"]
    assert calls == ["site.example"]


async def test_refresh_rechecks_stale_entries(session, monkeypatch):
    async def fake(dst):
        return ["9.9.9.9"], ""

    monkeypatch.setattr(routing, "resolve_dst", fake)
    stale = (datetime.now(timezone.utc) - routing.REFRESH_AFTER - timedelta(minutes=1)).isoformat()
    await settings_store.set_routing(
        session,
        {"a": {"src": "1", "dst": "site.example", "via": "21", "ips": ["1.1.1.1"], "resolved_at": stale}},
    )
    assert await routing.refresh(session) == ["a"]


@pytest.mark.parametrize(
    "raw,expect",
    [
        ("https://ifconfig.me", "ifconfig.me"),
        ("http://ifconfig.me/", "ifconfig.me"),
        ("https://ifconfig.me/all.json?x=1#top", "ifconfig.me"),
        ("  MyIP.RU  ", "myip.ru"),
        ("myip.ru.", "myip.ru"),
        ("example.com:8443", "example.com"),
        ("https://user:pass@example.com/path", "example.com"),
        ("[2a03::1]:443", "2a03::1"),
    ],
)
def test_normalize_strips_everything_but_the_host(raw, expect):
    """Адрес копируют из строки браузера целиком — со схемой, путём и портом."""
    assert routing.normalize_dst(raw) == expect


@pytest.mark.parametrize("raw", ["10.0.0.0/24", "192.168.1.0/255.255.255.0", "1.2.3.4"])
def test_normalize_keeps_networks_intact(raw):
    """Подсеть разбирается ДО обрезки по слешу: иначе «10.0.0.0/24» стало бы
    «10.0.0.0», то есть совсем другой целью."""
    assert routing.normalize_dst(raw) == raw


async def test_resolve_accepts_pasted_url(monkeypatch):
    seen = []

    def fake_getaddrinfo(host, *a, **k):
        seen.append(host)
        return [(2, 1, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(routing.socket, "getaddrinfo", fake_getaddrinfo)
    ips, err = await routing.resolve_dst("https://ifconfig.me/all")
    assert ips == ["93.184.216.34"] and not err
    assert seen == ["ifconfig.me"]  # в резолвер ушло голое имя


# --- валидация маршрутов агента (поле «Маршруты» на ноде) ---

from app.schemas import AgentIn  # noqa: E402


def test_agent_routes_reject_exit_route():
    """0.0.0.0/0 валиден как CIDR, но означает exit-ноду. Пропустив его в
    маршруты, мы дали бы сделать ноду exit-нодой МИМО галки с предупреждением."""
    with pytest.raises(ValueError, match="exit-нода"):
        AgentIn(routes=["0.0.0.0/0"])


def test_agent_routes_reject_mesh_covering():
    with pytest.raises(ValueError, match="меша"):
        AgentIn(routes=["100.64.0.0/10"])


def test_agent_routes_reject_garbage_and_ipv6():
    with pytest.raises(ValueError, match="неверный маршрут"):
        AgentIn(routes=["не-адрес"])
    with pytest.raises(ValueError, match="IPv6"):
        AgentIn(routes=["2a03:b0c0::/64"])


def test_agent_routes_reject_whitespace():
    """Перевод строки разорвал бы формат key=value, который парсит агент на ноде,
    и следующая строка выглядела бы как отдельный параметр состояния."""
    with pytest.raises(ValueError, match="пробел"):
        AgentIn(routes=["1.2.3.4/32\nexit=true"])


def test_agent_routes_normalize():
    assert AgentIn(routes=["203.0.113.7", " 10.0.0.0/24 "]).routes == [
        "203.0.113.7/32",
        "10.0.0.0/24",
    ]


# --- групповые источники «все устройства» / «все серверы» ---


def test_group_source_expands_to_all_devices():
    d = {"src_kind": "devices", "via": "21", "ips": ["1.2.3.4"], "ports": "*"}
    assert routing.sources(d, ["19", "22"], ["20", "21"]) == ["19", "22"]


def test_group_source_never_includes_the_exit_node():
    """Нода-выход как источник — это «ходи туда через саму себя»: бессмысленно и
    рискует запутать таблицу маршрутизации на самой ноде."""
    d = {"src_kind": "servers", "via": "21", "ips": ["1.2.3.4"]}
    assert routing.sources(d, [], ["20", "21", "22"]) == ["20", "22"]


def test_explicit_list_also_drops_the_exit_node():
    d = {"src_kind": "node", "src": ["19", "21"], "via": "21", "ips": ["1.2.3.4"]}
    assert routing.sources(d, [], []) == ["19"]


def test_old_single_string_source_still_works():
    """Записи, созданные до появления списков, читаются как раньше."""
    d = {"src": "19", "via": "21", "ips": ["1.2.3.4"]}
    assert routing.sources(d, [], []) == ["19"]


def test_acl_rules_multiply_by_source_and_address():
    directions = {
        "a": {
            "src_kind": "devices",
            "via": "21",
            "ips": ["1.2.3.4", "5.6.7.8"],
            "ports": "80,443",
        }
    }
    rules = routing.acl_rules(directions, ["19", "22"], ["20", "21"])
    assert len(rules) == 4  # 2 источника × 2 адреса
    assert {r["src"]["value"] for r in rules} == {"19", "22"}
    assert {r["ports"] for r in rules} == {"80,443"}


# --- полный туннель через ноду (весь трафик, split-default) ---


def test_full_tunnel_removed_no_split_default():
    """Полный туннель через subnet-маршруты УБРАН как небезопасный: функции
    split_default больше нет, и любая ссылка на неё должна падать явно."""
    assert not hasattr(routing, "split_default")


def test_legacy_full_direction_is_inert():
    """Старая запись с full=True больше НЕ раздаёт широкие маршруты и не эмитит
    ACL — иначе именно она и утекала на чужие ноды. Раздаются только сохранённые
    ips (у full их нет), так что направление становится пустышкой до удаления."""
    d = {"a": {"src_kind": "node", "src": ["19"], "via": "21", "full": True, "ips": []}}
    assert routing.routes_by_node(d) == {}
    assert routing.acl_rules(d) == []


def test_legacy_full_direction_with_stale_ips_uses_only_them():
    """Даже если у full-записи в ips что-то залежалось — раздаётся ровно это, а не
    «весь интернет». Никакого разворачивания в split-default."""
    d = {"a": {"src_kind": "node", "src": ["19"], "via": "21", "full": True, "ips": ["1.2.3.4"]}}
    assert routing.routes_by_node(d) == {"21": ["1.2.3.4/32"]}


# --- выход через разрешённые шлюзы (exitvia) ---

from app import exitvia  # noqa: E402


def test_exitvia_grants_map_devices_to_gateway_tags():
    meta = {
        "5": {"kind": "server", "exit_gateway": True},
        "6": {"kind": "server", "exit_gateway": True},
        "19": {"kind": "device", "exit_via": ["5", "6"]},
        "20": {"kind": "device", "exit_via": ["5"]},
    }
    ip = {"19": "100.64.0.2", "20": "100.64.0.3"}
    g = {r["src"]: r["via"] for r in exitvia.exit_via_grants(meta, ip)}
    assert g["100.64.0.2"] == ["tag:xgw-5", "tag:xgw-6"]
    assert g["100.64.0.3"] == ["tag:xgw-5"]


def test_exitvia_ignores_gateway_that_is_not_marked():
    """Ссылка на шлюз, у которого сняли галку, отбрасывается — иначе via
    указывал бы на несуществующий тег и headscale отверг бы политику."""
    meta = {"5": {"exit_gateway": True}, "19": {"exit_via": ["5", "99"]}}
    g = exitvia.exit_via_grants(meta, {"19": "100.64.0.2"})
    assert g[0]["via"] == ["tag:xgw-5"]  # 99 не помечен → выброшен


def test_service_tag_is_recognised():
    assert exitvia.is_service_tag("tag:xgw-5")
    assert not exitvia.is_service_tag("tag:vpn")


# --- серверная сторона выбора выхода: шлюз → список устройств ---


async def test_gateway_clients_sets_and_clears_exit_via(session):
    """Правка со стороны шлюза добавляет/убирает его id в exit_via устройств,
    не трогая остальные их поля (описание/тип)."""
    await settings_store.set_node_meta(session, "19", "рабочий ноут", "device")
    await settings_store.set_node_meta(session, "20", "", "device")
    # шлюз 5 разрешаем устройствам 19 и 20
    await settings_store.set_gateway_clients(session, "5", ["19", "20"], ["19", "20"])
    meta = await settings_store.get_node_meta(session)
    assert meta["19"]["exit_via"] == ["5"]
    assert meta["19"]["description"] == "рабочий ноут"  # чужие поля целы
    assert meta["20"]["exit_via"] == ["5"]
    # теперь оставляем только 19 — у 20 шлюз снимается
    await settings_store.set_gateway_clients(session, "5", ["19"], ["19", "20"])
    meta = await settings_store.get_node_meta(session)
    assert meta["19"]["exit_via"] == ["5"]
    assert "exit_via" not in meta.get("20", {})


async def test_gateway_clients_is_additive_across_gateways(session):
    """Разные шлюзы копятся в exit_via устройства независимо: правка одного не
    сносит другой."""
    await settings_store.set_gateway_clients(session, "5", ["19"], ["19"])
    await settings_store.set_gateway_clients(session, "6", ["19"], ["19"])
    meta = await settings_store.get_node_meta(session)
    assert sorted(meta["19"]["exit_via"]) == ["5", "6"]
    # снятие 5 не трогает 6
    await settings_store.set_gateway_clients(session, "5", [], ["19"])
    meta = await settings_store.get_node_meta(session)
    assert meta["19"]["exit_via"] == ["6"]


# --- широкие публичные сети в направлениях (та же утечка, что у полного туннеля) ---


def test_broad_public_prefix_is_rejected():
    """«128.0.0.0/1» — пол-интернета; touches_mesh его не ловит (меш не задевает),
    а как анонсируемый маршрут он уводит интернет любой accept-routes ноды."""
    for bad in ("128.0.0.0/1", "128.0.0.0/2", "192.0.0.0/4", "8.0.0.0/8"):
        assert routing.too_broad(bad) is True, bad


def test_normal_and_private_targets_pass():
    """Ограничение не должно мешать законным целям: сайт, публичный /24 своей
    компании, офисная приватная сеть любого размера."""
    for good in ("8.8.8.8", "93.184.216.34/32", "203.0.113.0/24", "185.1.0.0/16",
                 "10.0.0.0/8", "172.16.0.0/12", "192.168.1.0/24"):
        assert routing.too_broad(good) is False, good


def test_stored_broad_route_is_dropped_at_emit_time():
    """Запись могла сохраниться до появления проверки — на выдаче маршрутов
    широкий префикс всё равно отбрасывается."""
    d = {"a": {"src_kind": "node", "src": ["19"], "via": "21", "ips": ["128.0.0.0/1", "8.8.8.8"]}}
    assert routing.routes_by_node(d) == {"21": ["8.8.8.8/32"]}
    assert {r["dst"]["value"] for r in routing.acl_rules(d)} == {"8.8.8.8"}


# --- адрес из DNS не должен уводить маршрут внутрь меша ---


async def test_resolved_mesh_address_is_rejected(monkeypatch):
    """Проверка на меш стоит на строке, которую ввёл админ, — но для домена она
    бессильна. Настоящий адрес приходит из DNS и может смениться на tailnet-адрес
    соседа: тогда нода-выход начала бы анонсировать маршрут к нему (перехват)."""
    def fake(host, *a, **k):
        return [(2, 1, 6, "", ("100.64.0.9", 0))]

    monkeypatch.setattr(routing.socket, "getaddrinfo", fake)
    ips, err = await routing.resolve_dst("evil.example")
    assert ips == [] and "меш" in err


def test_stored_mesh_address_is_dropped_at_emit_time():
    """Последний рубеж: даже если mesh-адрес уже сохранён (запись создана до
    проверки или пришла в обход API), в маршруты и правила он не попадает."""
    d = {"a": {"src_kind": "node", "src": ["19"], "via": "21", "ips": ["100.64.0.9", "8.8.8.8"]}}
    assert routing.routes_by_node(d) == {"21": ["8.8.8.8/32"]}
    assert {r["dst"]["value"] for r in routing.acl_rules(d)} == {"8.8.8.8"}


async def test_refresh_does_not_resurrect_a_deleted_direction(session, monkeypatch):
    """Между чтением и записью refresh висит в резолвере. Если за это время админ
    удалил направление (отозвал доступ), запись снимка целиком вернула бы его —
    доступ восстановился бы сам собой."""
    await settings_store.set_routing(
        session,
        {
            "a": {"src_kind": "node", "src": ["19"], "dst": "site.example", "via": "21", "ips": ["1.1.1.1"]},
            "b": {"src_kind": "node", "src": ["19"], "dst": "gone.example", "via": "21", "ips": ["2.2.2.2"]},
        },
    )

    async def fake_resolve(dst):
        if dst == "site.example":
            # админ удаляет направление «b» ровно в этот момент
            cur = await settings_store.get_routing(session)
            cur.pop("b", None)
            await settings_store.set_routing(session, cur)
        return (["9.9.9.9"], "")

    monkeypatch.setattr(routing, "resolve_dst", fake_resolve)
    changed = await routing.refresh(session)
    saved = await settings_store.get_routing(session)
    assert "b" not in saved, "удалённое направление воскресло"
    assert "b" not in changed
    assert saved["a"]["ips"] == ["9.9.9.9"]  # полезная работа не потеряна


# --- одобрение маршрутов: те же проверки, что у маршрутов агента ---


def test_approved_routes_reject_mesh_but_allow_exit():
    """Список приходит из АНОНСА ноды и показан админу галочками. Без проверки
    один клик делал бы ноду subnet-роутером для адресов соседей."""
    from app.schemas import NodeRoutesIn

    with pytest.raises(ValueError, match="меша"):
        NodeRoutesIn(routes=["100.64.0.0/10"])
    with pytest.raises(ValueError, match="меша"):
        NodeRoutesIn(routes=["100.64.0.7/32"])
    # exit-маршруты этот эндпоинт как раз и одобряет
    assert NodeRoutesIn(routes=["0.0.0.0/0", "::/0"]).routes == ["0.0.0.0/0", "::/0"]
    assert NodeRoutesIn(routes=["10.0.0.0/24"]).routes == ["10.0.0.0/24"]


async def test_direction_approval_is_marked_as_the_panel_s(monkeypatch):
    """Направление одобряет свой маршрут сразу, не дожидаясь агента. Раньше это
    шло мимо panel_approved, и коллектор считал маршрут одобренным вручную —
    после удаления направления /32 оставался на ноде-выходе навсегда."""
    from app import routing

    approved: dict[str, list[str]] = {}

    class FakeClient:
        async def get_nodes(self):
            return [{"id": "2", "approvedRoutes": ["10.0.0.0/24"]}]

        async def approve_routes(self, nid, routes):
            approved[nid] = routes

    directions = {"d1": {"src": ["4"], "dst": "example.test", "via": "2",
                         "ips": ["203.0.113.9"], "ports": "*"}}
    done = await routing.approve_for(FakeClient(), directions)
    # маршрут одобрен в headscale И возвращён вызывающему для отметки
    assert "203.0.113.9/32" in approved["2"]
    assert done == {"2": ["203.0.113.9/32"]}
