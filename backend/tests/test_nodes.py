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
    await settings_store.stash_node_meta(session, "db-1", meta["7"])
    await settings_store.clear_node_meta(session, "7")

    # нода вернулась под другим id
    moved = await settings_store.claim_pending_meta(
        session, [{"id": "42", "givenName": "db-1"}]
    )
    assert moved == 1
    meta = await settings_store.get_node_meta(session)
    assert meta["42"]["kind"] == "server"
    assert meta["42"]["admin"] is True
    assert meta["42"]["description"] == "прод-сервер"

    # повторный вызов ничего не дублирует и не затирает
    assert await settings_store.claim_pending_meta(
        session, [{"id": "42", "givenName": "db-1"}]
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
    await settings_store.stash_node_meta(session, "db-1", {"kind": "server"}, old_id="7")

    await settings_store.claim_pending_meta(session, [{"id": "42", "givenName": "db-1"}])

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
        '{"db-1": {"kind": "server", "admin": true}}',
    )
    assert await settings_store.claim_pending_meta(
        session, [{"id": "42", "givenName": "db-1"}]
    ) == 1
    meta = await settings_store.get_node_meta(session)
    assert meta["42"]["kind"] == "server" and meta["42"]["admin"] is True


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
