import json

from app.aclgen import generate_policy

NODES = [
    {"id": "1", "ipAddresses": ["100.64.0.2", "fd7a::2"]},
    {"id": "2", "ipAddresses": ["100.64.0.3"]},
]


def test_node_to_node_port():
    rules = [
        {"src": {"kind": "node", "value": "1"}, "dst": {"kind": "node", "value": "2"}, "ports": "22"}
    ]
    pol = json.loads(generate_policy(rules, NODES))
    assert pol["acls"] == [
        {"action": "accept", "src": ["100.64.0.2"], "dst": ["100.64.0.3:22"]}
    ]


def test_empty_is_deny_all():
    assert json.loads(generate_policy([], NODES))["acls"] == []


def test_any_dst_expands_to_servers_only():
    """«Любой» как назначение — это ВСЕ СЕРВЕРЫ, а не буквальная звёздочка.

    Звёздочка охватила бы и устройства, а грант в Tailscale делает стороны
    взаимно видимыми — то есть устройства увидели бы друг друга в netmap. Это
    ровно тот инвариант, ради которого генератор разворачивает «*» в список
    серверов (см. generate_policy)."""
    rules = [{"src": {"kind": "node", "value": "1"}, "dst": {"kind": "any"}, "ports": "*"}]
    pol = json.loads(generate_policy(rules, NODES, server_ips=["100.64.0.3"]))
    assert pol["acls"][0]["src"] == ["100.64.0.2"]
    assert pol["acls"][0]["dst"] == ["100.64.0.3:*"]


def test_any_dst_without_servers_is_dropped():
    """Серверов нет → разворачивать «*» не во что, и правило отбрасывается целиком.
    Молча пропустить сюда «*:*» было бы худшим из исходов."""
    rules = [{"src": {"kind": "node", "value": "1"}, "dst": {"kind": "any"}, "ports": "*"}]
    assert json.loads(generate_policy(rules, NODES))["acls"] == []


def test_missing_node_skipped():
    rules = [
        {"src": {"kind": "node", "value": "99"}, "dst": {"kind": "node", "value": "2"}, "ports": "22"}
    ]
    assert json.loads(generate_policy(rules, NODES))["acls"] == []


def test_tag_selector_and_owners():
    # роль как назначение: нода → tag:db : 5432; эмитится tagOwners
    rules = [
        {"src": {"kind": "node", "value": "2"}, "dst": {"kind": "tag", "value": "db"}, "ports": "5432"}
    ]
    pol = json.loads(generate_policy(rules, NODES, tag_owner="default"))
    assert pol["acls"][0]["src"] == ["100.64.0.3"]
    assert pol["acls"][0]["dst"] == ["tag:db:5432"]
    assert pol["tagOwners"] == {"tag:db": ["group:noderoost-tags"]}


def test_tag_prefix_normalized_and_deduped():
    # значение с префиксом «tag:» и без — одинаковы; роль в src и dst → один owner-ключ
    rules = [
        {"src": {"kind": "tag", "value": "tag:web"}, "dst": {"kind": "tag", "value": "web"}, "ports": "*"}
    ]
    pol = json.loads(generate_policy(rules, NODES, tag_owner="admin"))
    assert pol["acls"][0]["src"] == ["tag:web"]
    assert pol["acls"][0]["dst"] == ["tag:web:*"]
    assert pol["tagOwners"] == {"tag:web": ["group:noderoost-tags"]}


def test_no_tagowners_without_tags():
    rules = [{"src": {"kind": "any"}, "dst": {"kind": "node", "value": "2"}, "ports": "*"}]
    assert "tagOwners" not in json.loads(generate_policy(rules, NODES))


def test_servers_selector_expands_to_all_server_ips():
    # «все серверы» разворачивается в список IP серверов (не *), устройства не входят
    rules = [{"src": {"kind": "node", "value": "1"}, "dst": {"kind": "servers"}, "ports": "*"}]
    pol = json.loads(
        generate_policy(rules, NODES, server_ips=["100.64.0.2", "100.64.0.3"])
    )
    assert pol["acls"][0]["dst"] == ["100.64.0.2:*", "100.64.0.3:*"]
    # без серверов правило с servers-целью пропускается
    assert json.loads(generate_policy(rules, NODES, server_ips=[]))["acls"] == []


async def test_rules_requires_auth(client):
    assert (await client.get("/api/policy/rules")).status_code == 401


# --- инвариант: устройство НИКОГДА не бывает назначением ---
# В Tailscale грант делает стороны взаимно видимыми в netmap, поэтому правило с
# целью-устройством означало бы «устройства видят друг друга». Гарантия должна
# держаться в движке, а не в UI: правило может прийти через API в обход панели.

DEV = "100.64.0.3"  # id=2 — устройство
SRV = "100.64.0.2"  # id=1 — сервер


def _dsts(rules):
    pol = json.loads(generate_policy(rules, NODES, server_ips=[SRV], device_ips=[DEV]))
    return [d for a in pol["acls"] for d in a["dst"]]


def test_device_cannot_be_target_by_node_selector():
    rules = [{"src": {"kind": "node", "value": "1"}, "dst": {"kind": "node", "value": "2"}, "ports": "22"}]
    assert _dsts(rules) == []  # правило целиком отброшено


def test_device_cannot_be_target_by_cidr():
    # обход «впишу tailnet-IP устройства руками в поле IP/подсеть»
    rules = [{"src": {"kind": "node", "value": "1"}, "dst": {"kind": "cidr", "value": DEV}, "ports": "*"}]
    assert _dsts(rules) == []


def test_any_target_expands_to_servers_only():
    # «*» как назначение охватил бы и устройства — подменяем на список серверов
    rules = [{"src": {"kind": "node", "value": "2"}, "dst": {"kind": "any", "value": ""}, "ports": "*"}]
    dsts = _dsts(rules)
    assert dsts == [f"{SRV}:*"]
    assert all(DEV not in d for d in dsts)


def test_server_target_still_works():
    rules = [{"src": {"kind": "node", "value": "2"}, "dst": {"kind": "node", "value": "1"}, "ports": "22"}]
    assert _dsts(rules) == [f"{SRV}:22"]


def test_broad_cidr_cannot_smuggle_devices_into_dst():
    """0.0.0.0/0 содержит 100.64.0.0/10 целиком, то есть открывает разом все
    устройства — при этом не совпадая буквально ни с одним их адресом. Фильтр
    по точному совпадению такое пропускал."""
    rules = [
        {
            "src": {"kind": "node", "value": "1"},
            "dst": {"kind": "cidr", "value": "0.0.0.0/0"},
            "ports": "*",
        }
    ]
    pol = json.loads(
        generate_policy(rules, NODES, "default", ["100.64.0.3"], ["100.64.0.2"])
    )
    assert pol["acls"] == []


def test_cidr_touching_mesh_range_is_dropped():
    for bad in ("100.64.0.0/10", "100.0.0.0/8", "0.0.0.0/0"):
        rules = [
            {
                "src": {"kind": "node", "value": "1"},
                "dst": {"kind": "cidr", "value": bad},
                "ports": "*",
            }
        ]
        assert json.loads(generate_policy(rules, NODES))["acls"] == [], bad


def test_ordinary_subnets_and_hosts_still_pass():
    """Защита не должна мешать легальным целям: офисная сеть за subnet-роутером
    и одиночный адрес сайта."""
    for good in ("10.0.0.0/8", "192.168.1.0/24", "8.8.8.8"):
        rules = [
            {
                "src": {"kind": "node", "value": "1"},
                "dst": {"kind": "cidr", "value": good},
                "ports": "*",
            }
        ]
        pol = json.loads(generate_policy(rules, NODES))
        assert pol["acls"][0]["dst"] == [f"{good}:*"], good


def test_device_cannot_be_target_written_as_slash32():
    """Голый адрес устройства фильтр отбрасывал, а тот же адрес с «/32» — другая
    строка, и он проезжал насквозь. Инвариант должен держаться на СМЫСЛЕ адреса,
    а не на его записи."""
    for form in (DEV, f"{DEV}/32"):
        rules = [
            {
                "src": {"kind": "node", "value": "1"},
                "dst": {"kind": "cidr", "value": form},
                "ports": "*",
            }
        ]
        assert _dsts(rules) == [], form


def test_server_slash32_is_still_a_valid_target():
    """Поблажка для одиночного адреса нужна ради серверов — её нельзя убирать
    целиком, иначе сервер перестанет быть достижимой целью."""
    rules = [
        {
            "src": {"kind": "node", "value": "2"},
            "dst": {"kind": "cidr", "value": f"{SRV}/32"},
            "ports": "22",
        }
    ]
    assert _dsts(rules) == [f"{SRV}/32:22"]


def test_touches_mesh_has_no_prefix_exemption():
    """Для маршрутов и направлений поблажки на /32 быть не должно: точечный
    перехват трафика к соседу — самый опасный случай, а не самый безобидный."""
    from app.aclgen import touches_mesh

    for inside in ("100.64.0.3", "100.64.0.3/32", "100.64.0.0/10", "0.0.0.0/0"):
        assert touches_mesh(inside) is True, inside
    for outside in ("203.0.113.7", "203.0.113.7/32", "10.0.0.0/8"):
        assert touches_mesh(outside) is False, outside


def test_node_to_itself_is_dropped_by_the_engine():
    """ACL не фильтрует трафик ноды к её собственному адресу — такое правило
    инертно и лишь засоряет политику. Проверка должна жить в движке: через API
    и через выдачу внутри карточки ноды модалка массовой выдачи не участвует."""
    rules = [
        {
            "src": {"kind": "node", "value": "1"},
            "dst": {"kind": "node", "value": "1"},
            "ports": "22",
        }
    ]
    assert json.loads(generate_policy(rules, NODES))["acls"] == []


def test_role_to_itself_is_kept():
    """«Роль → та же роль» — это «серверы роли видят друг друга». Правило
    законное и оживает, как только в роли появится второй сервер; отбрасывать
    его значило бы решить за администратора."""
    rules = [
        {
            "src": {"kind": "tag", "value": "web"},
            "dst": {"kind": "tag", "value": "web"},
            "ports": "22",
        }
    ]
    pol = json.loads(generate_policy(rules, NODES, tag_owner="default"))
    assert pol["acls"][0]["src"] == ["tag:web"]
    assert pol["acls"][0]["dst"] == ["tag:web:22"]


def test_exit_via_emits_grants_with_via():
    """Выход в интернет через РАЗРЕШЁННЫЕ шлюзы — grants с via по служебным тегам."""
    exit_via = [{"src": "100.64.0.2", "via": ["tag:xgw-5", "tag:xgw-6"]}]
    pol = json.loads(
        generate_policy([], NODES, "default", exit_via=exit_via)
    )
    g = pol["grants"][0]
    assert g["src"] == ["100.64.0.2"]
    assert g["dst"] == ["autogroup:internet"]
    assert g["via"] == ["tag:xgw-5", "tag:xgw-6"]
    # via-теги объявлены в tagOwners
    assert "tag:xgw-5" in pol["tagOwners"] and "tag:xgw-6" in pol["tagOwners"]


def test_exit_via_skipped_without_via():
    pol = json.loads(generate_policy([], NODES, "default", exit_via=[{"src": "1.2.3.4", "via": []}]))
    assert "grants" not in pol


def test_exit_via_also_emits_visibility_acl():
    """Кроме via-гранта, источник должен получить acl-доступ к ноде-шлюзу по её
    служебному тегу — иначе шлюз не попадёт в netmap источника и
    `tailscale --exit-node` упадёт с «no node found in netmap» (всплыло вживую)."""
    exit_via = [{"src": "100.64.0.2", "via": ["tag:xgw-5"]}]
    pol = json.loads(generate_policy([], NODES, "default", exit_via=exit_via))
    # грант на интернет через шлюз
    assert pol["grants"][0]["via"] == ["tag:xgw-5"]
    # + видимость: acl src → tag:xgw-5 на глухом порту (не «*», см. тест ниже)
    vis = [a for a in pol.get("acls", []) if a["src"] == ["100.64.0.2"]]
    assert any(d.startswith("tag:xgw-5:") for a in vis for d in a["dst"]), pol.get("acls")


# --- инварианты выхода через шлюз (безопасность) ---


def test_visibility_rule_is_not_a_free_pass_to_the_gateway():
    """Правило видимости шлюза даёт ровно видимость: порт заведомо глухой, а не «*».
    Иначе разрешение «выходить в интернет» тихо открывало бы ещё и все сервисы
    шлюза (SSH, БД) — причём невидимо в «Доступах», т.к. правило синтетическое."""
    pol = json.loads(
        generate_policy([], NODES, "default", exit_via=[{"src": "100.64.0.2", "via": ["tag:xgw-5"]}])
    )
    vis = [a for a in pol["acls"] if a["src"] == ["100.64.0.2"]]
    assert vis and vis[0]["dst"] == ["tag:xgw-5:9"]
    assert all("tag:xgw-5:*" not in a["dst"] for a in pol["acls"])


def test_device_cannot_be_an_exit_gateway_in_the_engine():
    """Тег шлюза на УСТРОЙСТВЕ не должен становиться назначением: иначе через
    правило видимости устройства увидели бы друг друга в обход изоляции. Через API
    ноду можно пометить шлюзом мимо UI, поэтому инвариант держим в движке."""
    nodes = [
        {"id": "1", "ipAddresses": ["100.64.0.2"]},
        {"id": "2", "ipAddresses": ["100.64.0.3"], "tags": ["tag:xgw-2"]},
    ]
    pol = json.loads(
        generate_policy(
            [],
            nodes,
            "default",
            server_ips=[],
            device_ips=["100.64.0.2", "100.64.0.3"],
            exit_via=[{"src": "100.64.0.2", "via": ["tag:xgw-2"]}],
        )
    )
    assert pol["acls"] == []       # ни видимости…
    assert "grants" not in pol      # …ни гранта на выход через устройство


def test_tags_are_owned_by_an_empty_group_not_by_the_node_owner():
    """tagOwners = «кто вправе навесить тег». Пока владельцем был пользователь,
    которому принадлежат ноды, ЛЮБАЯ нода могла присвоить себе тег сама через
    `tailscale up --advertise-tags`: взять чужую роль или выдать себя за шлюз
    выхода и принимать чужой интернет-трафик. Владелец — пустая группа."""
    rules = [{"src": {"kind": "node", "value": "2"}, "dst": {"kind": "tag", "value": "db"}, "ports": "5432"}]
    pol = json.loads(generate_policy(rules, NODES, tag_owner="default"))
    assert pol["tagOwners"] == {"tag:db": ["group:noderoost-tags"]}
    assert pol["groups"] == {"group:noderoost-tags": []}  # без участников
    # ни один пользователь больше не владеет тегами
    assert all("@" not in o for owners in pol["tagOwners"].values() for o in owners)


def test_rule_cannot_reference_a_gateway_service_tag():
    """Служебный тег шлюза — не роль. Правило на него выдавало бы доступ к шлюзу
    (или права от его имени) в обход галки «Шлюз выхода»."""
    import pytest
    from app.schemas import AclSelector

    for v in ("xgw-20", "tag:xgw-20"):
        with pytest.raises(ValueError, match="служебный тег"):
            AclSelector(kind="tag", value=v)


def test_rule_role_name_charset_is_enforced():
    """Двоеточие/пробел в имени роли меняют смысл правила или (чаще) заставляют
    headscale отвергнуть политику целиком — и тогда замирают ВСЕ последующие пуши,
    потому что негодное правило уже сохранено."""
    import pytest
    from app.schemas import AclSelector

    for bad in ("web:*", "web 1", "", "-web", "tag:", "wéb"):
        with pytest.raises(ValueError):
            AclSelector(kind="tag", value=bad)
    # законные имена по-прежнему проходят
    for good in ("web", "db-1", "web.prod", "a_b"):
        assert AclSelector(kind="tag", value=good).value == good
