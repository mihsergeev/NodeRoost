import ipaddress
import logging
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request, status

from app import aclgen, audit, ca, enroll, exitvia, geoip, hostinfo, routing, settings_store
from app.clientip import client_ip
from app.config import get_settings
from app.deps import CurrentUser, SessionDep
from app.hs_client import HeadscaleError, get_client
from app.hs_util import hs_call, norm_ts, require_hs
from app.nodekind import editable_tags, effective_kind, is_online, node_tags
from app.policy_apply import apply_policy, declare_tags, require_policy, write_lock
from app.schemas import (  # noqa: I001
    normalize_node_name,
    EnrollOut,
    ExitClientsIn,
    NodeMetaIn,
    NodeOut,
    NodeRenameIn,
    NodeRoutesIn,
    NodeTagsIn,
    ReconnectIn,
)

router = APIRouter(prefix="/nodes", tags=["nodes"])
log = logging.getLogger(__name__)

# CIDR, обозначающие exit-node (весь трафик)
EXIT_ROUTES = ("0.0.0.0/0", "::/0")


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _map_node(n: dict, meta: dict | None = None, hinfo: dict | None = None) -> NodeOut:
    # владелец ноды наружу не отдаётся: все ноды под одним техническим
    # пользователем headscale, субъект доступа — сама нода
    expiry = norm_ts(n.get("expiry"))
    key_expired = False
    if expiry:
        dt = _parse(expiry)
        key_expired = dt is not None and dt < datetime.now(timezone.utc)
    tags = [t for t in node_tags(n) if not exitvia.is_service_tag(t)]
    forced = [t for t in editable_tags(n) if not exitvia.is_service_tag(t)]
    available = n.get("availableRoutes", []) or []
    approved = n.get("approvedRoutes", []) or []
    # Действующие маршруты считаем сами — «одобрено И анонсируется». Ровно это
    # значит subnetRoutes, но headscale 0.29 отдаёт его пустым в ответе на запрос
    # ОДНОЙ ноды (в списке — правильно). Карточка ноды из-за этого показывала
    # работающий маршрут как неработающий, и админ шёл искать несуществующую
    # поломку.
    subnet = [r for r in approved if r in available]
    is_exit = "0.0.0.0/0" in approved
    nid = str(n.get("id", ""))
    entry = (meta or {}).get(nid) or {}
    hi = (hinfo or {}).get(nid) or {}
    kind = effective_kind(n, meta or {})
    return NodeOut(
        id=str(n.get("id", "")),
        name=n.get("givenName") or n.get("name", ""),
        hostname=n.get("name", ""),
        ip_addresses=n.get("ipAddresses", []) or [],
        # истёкший ключ = нода не на связи, чем бы ни отвечал headscale
        online=is_online(n),
        last_seen=norm_ts(n.get("lastSeen")),
        expiry=expiry,
        key_expired=key_expired,
        forced_tags=forced,
        tags=tags,
        created_at=norm_ts(n.get("createdAt")),
        client_version=hi.get("client_version", ""),
        os=hi.get("os", ""),
        arch=hi.get("arch", ""),
        container=bool(hi.get("container", False)),
        endpoint=hi.get("endpoint", ""),
        # страна — по адресу из endpoint, а не по имени ноды: имя может быть любым
        country=geoip.country_of(str(hi.get("endpoint", "")).rsplit(":", 1)[0]),
        direct_ok=bool(hi.get("direct_ok", False)),
        available_routes=available,
        approved_routes=approved,
        # для отображения «маршруты» показываем активные subnet без exit-CIDR
        subnet_routes=[r for r in subnet if r not in EXIT_ROUTES],
        is_exit_node=is_exit,
        advertises_exit_node="0.0.0.0/0" in available,
        description=entry.get("description", ""),
        kind=kind,
        admin=bool(entry.get("admin", False)),
        muted=bool(entry.get("muted", False)),
        hidden=bool(entry.get("hidden", False)),
        exit_gateway=bool(entry.get("exit_gateway", False)),
        exit_via=[str(i) for i in (entry.get("exit_via") or [])],
        force_exit=str(entry.get("force_exit") or ""),
        group=entry.get("group", ""),
        subgroup=entry.get("subgroup", ""),
    )


_ROLE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def _normalize_tags(tags: list[str]) -> list[str]:
    """Каждый тег headscale должен иметь префикс `tag:` — дописываем, если забыли.

    Заодно проверяем набор символов: тег попадает и в политику, и в аргументы
    headscale, и пробел/двоеточие внутри значения меняли бы её смысл.

    Регистр приводим к нижнему: headscale принимает только такие теги, и роль
    «PROD» уходила отказом «tag should be lowercase» — английским, из чужих
    потрохов, за 502. Заодно «PROD» и «prod» перестают быть двумя разными
    ролями, которыми они выглядели в списке.
    """
    out: list[str] = []
    for t in tags:
        t = t.strip().lower()
        if not t:
            continue
        if not t.startswith("tag:"):
            t = "tag:" + t
        name = t[4:]
        if not _ROLE_RE.match(name):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Недопустимое имя роли «{name}»: латиница, цифры, дефис, точка, _",
            )
        if t not in out:
            out.append(t)
    return out


@router.get("", response_model=list[NodeOut])
async def list_nodes(_: CurrentUser, session: SessionDep) -> list[NodeOut]:
    settings = get_settings()
    require_hs(settings)
    client = get_client(settings)
    nodes = await hs_call(client.get_nodes())
    meta = await settings_store.get_node_meta(session)
    hinfo = await hostinfo.read_all_async(settings.headscale_db_path)
    return [_map_node(n, meta, hinfo) for n in nodes]


@router.get("/{node_id}", response_model=NodeOut)
async def get_node(node_id: str, _: CurrentUser, session: SessionDep) -> NodeOut:
    settings = get_settings()
    require_hs(settings)
    client = get_client(settings)
    node = await hs_call(client.get_node(node_id))
    if not node:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Нода не найдена")
    meta = await settings_store.get_node_meta(session)
    hinfo = await hostinfo.read_all_async(settings.headscale_db_path)
    return _map_node(node, meta, hinfo)


def _kind_ctx(meta: dict | None, node_id, kind: str | None) -> dict:
    """Мета для effective_kind: сохранённое значение + перекрытие из запроса.

    Запрос может касаться только галки шлюза — тогда kind в теле нет (None), и
    брать его надо из БД. Раньше сюда подставлялась пустая мета, тип считался
    авто-определением, и сервер без тегов и маршрутов оказывался «устройством»:
    панель отказывалась делать шлюзом ровно ту ноду, ради которой ручной выбор
    типа и придуман.
    """
    ctx = dict(meta or {})
    nid = str(node_id)
    entry = dict(ctx.get(nid) or {})
    if kind is not None:
        entry["kind"] = kind
    ctx[nid] = entry
    return ctx


async def _set_policy_tags(session, client, settings, node_id, previous, wanted):
    await hs_call(declare_tags(session, client, settings, sorted(set(wanted) - set(previous))))
    node = await hs_call(client.set_node_tags(node_id, wanted))
    if not node or set(editable_tags(node)) != set(wanted):
        node = await hs_call(client.get_node(node_id))
    if not node or set(editable_tags(node)) != set(wanted):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Headscale не подтвердил теги ноды")
    return node


async def _restore_node_policy(session, client, settings, node_id, previous_tags=None):
    if previous_tags is not None:
        try:
            current = await client.get_node(node_id)
            if current and set(editable_tags(current)) != set(previous_tags):
                restored = exitvia.keep_tagged(previous_tags)
                await _set_policy_tags(
                    session, client, settings, node_id, editable_tags(current), restored
                )
        except Exception:
            log.exception("Не удалось восстановить теги ноды %s", node_id)
    await apply_policy(session, client, settings)


@router.post("/{node_id}/meta", response_model=NodeOut)
async def set_meta(
    node_id: str,
    body: NodeMetaIn,
    user: CurrentUser,
    session: SessionDep,
) -> NodeOut:
    settings = get_settings()
    require_hs(settings)
    client = get_client(settings)
    username = user.username
    async with write_lock():
        node = await hs_call(client.get_node(node_id))
        if not node:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Нода не найдена")
        previous_tags = editable_tags(node)
        policy_started = tags_started = False
        try:
            async with settings_store.atomic_update(session):
                meta = await settings_store.get_node_meta(session)
                stored = meta.get(node_id) or {}
                was_gateway = bool(stored.get("exit_gateway"))
                gateway = was_gateway if body.exit_gateway is None else body.exit_gateway
                kind = effective_kind(node, _kind_ctx(meta, node_id, body.kind))
                if kind != "server" and gateway:
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST,
                        "Шлюзом выхода в интернет может быть только сервер. "
                        "Сначала снимите «Шлюз выхода в интернет» у этой ноды.",
                    )
                if body.kind is not None and kind != "server":
                    directions = await settings_store.get_routing(session)
                    if any(str(d.get("via") or "") == node_id for d in directions.values()):
                        raise HTTPException(
                            status.HTTP_400_BAD_REQUEST,
                            "Нода служит выходом в разделе «Маршрутизация». "
                            "Сначала уберите эти направления.",
                        )

                via = body.exit_via
                force = body.force_exit
                if via is not None or force is not None:
                    effective_force = str(stored.get("force_exit") or "") if force is None else force
                    via = list(stored.get("exit_via") or []) if via is None else list(via)
                    if effective_force and effective_force not in via:
                        via.append(effective_force)
                    gateways = set(exitvia.gateways(meta))
                    if (effective_force and effective_force not in gateways) or (
                        body.exit_via is not None and set(via) - gateways
                    ):
                        raise HTTPException(
                            status.HTTP_400_BAD_REQUEST,
                            "Выход возможен только через сервер, помеченный «Шлюз выхода в интернет».",
                        )

                await settings_store.set_node_meta(
                    session, node_id, body.description, body.kind, body.admin,
                    body.group, body.subgroup, body.muted, body.hidden,
                    body.exit_gateway, via, force,
                )
                if was_gateway and not gateway:
                    for nid in await settings_store.drop_gateway(session, node_id):
                        await settings_store.set_agent_use_exit(session, nid, "")
                if force is not None:
                    gw = await hs_call(client.get_node(force)) if force else {}
                    gw_ip = aclgen._ipv4(gw or {}) or ""
                    if force:
                        try:
                            ready = bool(gw_ip and ipaddress.ip_address(gw_ip) in aclgen.MESH_RANGE)
                        except ValueError:
                            ready = False
                        if not ready:
                            raise HTTPException(
                                status.HTTP_400_BAD_REQUEST, "У шлюза выхода нет IPv4-адреса в сети"
                            )
                    await settings_store.set_agent_use_exit(session, node_id, gw_ip)

                tag = exitvia.gateway_tag(node_id)
                if gateway:
                    wanted = list(dict.fromkeys([*previous_tags, tag]))
                    await _set_agent_exit(session, node_id, True)
                elif tag in previous_tags:
                    wanted = exitvia.keep_tagged([t for t in previous_tags if t != tag])
                    await _set_agent_exit(session, node_id, False)
                else:
                    wanted = previous_tags
                    if was_gateway:
                        await _set_agent_exit(session, node_id, False)

                if set(wanted) != set(previous_tags):
                    policy_started = tags_started = True
                    node = await _set_policy_tags(
                        session, client, settings, node_id, previous_tags, wanted
                    )
                meta = await settings_store.get_node_meta(session)
                updated = meta.get(node_id) or {}
                if tags_started or any(
                    updated.get(field) != stored.get(field)
                    for field in ("kind", "admin", "exit_gateway", "exit_via")
                ):
                    policy_started = True
                    await hs_call(require_policy(session, client, settings))
        except BaseException:
            if policy_started:
                restored_tags = None
                if tags_started:
                    restored_tags = previous_tags
                    try:
                        saved = (await settings_store.get_node_meta(session)).get(node_id) or {}
                        if bool(saved.get("exit_gateway")) == gateway:
                            restored_tags = None
                    except Exception:
                        log.exception("Не удалось проверить сохранённую мету ноды %s", node_id)
                await _restore_node_policy(
                    session, client, settings, node_id, restored_tags
                )
            raise
    await audit.record(session, username, "node_meta", node_id, body.kind or "")
    hinfo = await hostinfo.read_all_async(settings.headscale_db_path)
    return _map_node(node, meta, hinfo)


@router.post("/{node_id}/exit-clients", response_model=NodeOut)
async def set_exit_clients(
    node_id: str,
    body: ExitClientsIn,
    user: CurrentUser,
    session: SessionDep,
) -> NodeOut:
    """Обновить разрешённые устройства и сбросить принудительный выход у исключённых."""
    settings = get_settings()
    require_hs(settings)
    client = get_client(settings)
    username = user.username
    async with write_lock():
        node = await hs_call(client.get_node(node_id))
        if not node:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Нода не найдена")
        nodes = await hs_call(client.get_nodes()) or []
        policy_started = False
        try:
            async with settings_store.atomic_update(session):
                meta = await settings_store.get_node_meta(session)
                if node_id not in exitvia.gateways(meta):
                    raise HTTPException(status.HTTP_400_BAD_REQUEST, "Сервер не помечен шлюзом выхода")
                device_ids = [
                    str(n.get("id", "")) for n in nodes if effective_kind(n, meta) != "server"
                ]
                if await settings_store.set_gateway_clients(session, node_id, body.devices, device_ids):
                    policy_started = True
                    await hs_call(require_policy(session, client, settings))
                meta = await settings_store.get_node_meta(session)
        except BaseException:
            if policy_started:
                await apply_policy(session, client, settings)
            raise
    await audit.record(session, username, "node_exit_clients", node_id, str(len(body.devices)))
    hinfo = await hostinfo.read_all_async(settings.headscale_db_path)
    return _map_node(node, meta, hinfo)


async def _set_agent_exit(session, node_id: str, on: bool) -> None:
    """Включить/выключить exit у агента ноды (шлюз выхода = exit-нода)."""
    agents = await settings_store.get_agent_all(session)
    cfg = agents.get(node_id) or {}
    if bool(cfg.get("exit")) == on:
        return
    cfg["exit"] = on
    agents[node_id] = cfg
    await settings_store.set_agent_all(session, agents)


@router.post("/{node_id}/reconnect", response_model=EnrollOut)
async def reconnect_node(
    node_id: str,
    body: ReconnectIn,
    request: Request,
    user: CurrentUser,
    session: SessionDep,
) -> EnrollOut:
    """Переподключить ноду: удаляет её запись в headscale (освобождает IP) и отдаёт
    скрипт для запуска на самой ноде (logout+up с новым ключом → новый IP из текущего
    диапазона). Имя ноды сохраняется."""
    async with write_lock():
        return await _reconnect_node(node_id, body, user, session)


async def _reconnect_node(
    node_id: str, body: ReconnectIn, user: CurrentUser, session: SessionDep
) -> EnrollOut:
    settings = get_settings()
    require_hs(settings)
    client = get_client(settings)
    node = await hs_call(client.get_node(node_id))
    if not node:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Нода не найдена")
    # Имя приходит из headscale, а уходит в root-скрипт, который человек
    # запускает на машине. Через обычное подключение оно валидируется схемой, и
    # тут проверка стоит одну строку: своё имя нода выбирает сама
    # (tailscale up --hostname), а headscale её выбор нормализует не до конца.
    raw_name = str(node.get("givenName") or node.get("name", "") or "node")
    try:
        name = normalize_node_name(raw_name)
    except ValueError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Имя ноды «{raw_name}» не годится для скрипта подключения: "
            "переименуйте ноду и повторите.",
        ) from None

    # все ноды под одним техническим владельцем headscale (сущности «пользователь»
    # в панели нет); ключ без тегов → владельцем станет именно он
    hs_user = await hs_call(client.ensure_user(settings.default_user))
    exp = datetime.now(timezone.utc) + timedelta(minutes=settings.enroll_key_ttl_minutes)
    exp_iso = exp.isoformat().replace("+00:00", "Z")
    key = await hs_call(
        client.create_preauthkey(
            str(hs_user.get("id", "")),
            reusable=False,
            ephemeral=False,
            expiration=exp_iso,
        )
    )
    key_id = str(key.get("id") or "")
    if not key_id or not key.get("key"):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Headscale не вернул ключ переподключения")
    version = await settings_store.get_tailscale_version(session, settings)
    ca_pem = await ca.root_cert(session) if await ca.auto_install(session) else ""
    script = enroll.build_script(
        body.os, settings, key.get("key", ""), name, version=version,
        force_reauth=True, ca_pem=ca_pem,
    )
    from app.api.enroll import _join_link

    # Готовим ссылку и перенос до удаления старой ноды. При ошибке записи она
    # останется на связи. Старая мета удаляется при подтверждении нового id.
    async with settings_store.atomic_update(session):
        meta = await settings_store.get_node_meta(session)
        await settings_store.stash_node_meta(
            session, name, dict(meta.get(node_id) or {}), old_id=node_id, key_id=key_id
        )
        url, cmd = await _join_link(session, settings, script, body.os, exp_iso)
    await hs_call(client.delete_node(node_id))
    await audit.record(session, user.username, "node_reconnect", node_id, name)
    await apply_policy(session, client, settings)
    return EnrollOut(
        os=body.os,
        hostname=name,
        login_server=settings.headscale_server_url,
        script=script,
        key_id=key_id,
        expires_at=exp_iso,
        join_url=url,
        join_cmd=cmd,
    )


@router.post("/{node_id}/rename", response_model=NodeOut)
async def rename_node(
    node_id: str,
    body: NodeRenameIn,
    request: Request,
    user: CurrentUser,
    session: SessionDep,
) -> NodeOut:
    settings = get_settings()
    require_hs(settings)
    client = get_client(settings)
    # Занятость имени проверяем сами и БЕЗ оглядки на регистр: headscale
    # различает «WEB-FRA» и «web-fra», а MagicDNS — нет. Такая пара уживалась в
    # списке, но имя в сети доставалось одной из них, и обращение по имени
    # молча уходило на ЧУЖУЮ машину.
    for n in await hs_call(client.get_nodes()):
        if str(n.get("id", "")) == str(node_id):
            continue
        if str(n.get("givenName") or "").lower() == body.name:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Имя уже занято другой нодой"
            )
    node = await hs_call(client.rename_node(node_id, body.name))
    await audit.record(session, user.username, "node_rename", node_id, body.name)
    meta = await settings_store.get_node_meta(session)
    hinfo = await hostinfo.read_all_async(get_settings().headscale_db_path)
    return _map_node(node or {}, meta, hinfo)


@router.post("/{node_id}/tags", response_model=NodeOut)
async def set_tags(
    node_id: str,
    body: NodeTagsIn,
    request: Request,
    user: CurrentUser,
    session: SessionDep,
) -> NodeOut:
    settings = get_settings()
    require_hs(settings)
    client = get_client(settings)
    tags = _normalize_tags(body.tags)
    # Служебные теги (tag:xgw-*) — не роли, их ставит только сама панель по галке
    # «Шлюз выхода». Принять такой тег снаружи означало бы дать выдать ЛЮБУЮ ноду
    # за шлюз выхода: она попала бы под via-грант чужих устройств и принимала бы их
    # интернет-трафик, причём незаметно — в UI служебные теги скрыты.
    if any(exitvia.is_service_tag(t) for t in tags):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Служебные теги шлюза выхода назначаются только галкой «Шлюз выхода в "
            "интернет» — вручную их задавать нельзя.",
        )
    # Уже висящие служебные теги СОХРАНЯЕМ: список тегов перезаписывается целиком,
    # а роли в UI показываются без служебных — иначе правка роли у сервера-шлюза
    # молча срывала бы с него тег, и выход через этот шлюз перестал бы работать.
    async with write_lock():
        current = await hs_call(client.get_node(node_id))
        if not current:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Нода не найдена")
        previous = editable_tags(current)
        keep = [t for t in previous if exitvia.is_service_tag(t)]
        # При снятии последней роли остаётся служебный маркер Headscale.
        want = exitvia.keep_tagged(tags + keep)
        node = current
        if set(want) != set(previous):
            try:
                node = await _set_policy_tags(session, client, settings, node_id, previous, want)
                await hs_call(require_policy(session, client, settings))
            except BaseException:
                await _restore_node_policy(session, client, settings, node_id, previous)
                raise
    await audit.record(session, user.username, "node_tags", node_id, ", ".join(tags))
    meta = await settings_store.get_node_meta(session)
    hinfo = await hostinfo.read_all_async(get_settings().headscale_db_path)
    return _map_node(node or {}, meta, hinfo)


@router.post("/{node_id}/routes", response_model=NodeOut)
async def set_routes(
    node_id: str,
    body: NodeRoutesIn,
    request: Request,
    user: CurrentUser,
    session: SessionDep,
) -> NodeOut:
    """Задаёт полный список одобренных маршрутов ноды (subnet + exit)."""
    settings = get_settings()
    require_hs(settings)
    client = get_client(settings)
    routes = [r.strip() for r in body.routes if r.strip()]
    # Снятая галка должна означать «маршрута больше нет», а не «нет до
    # следующего цикла»: коллектор заново одобряет всё, что панель держит в
    # конфиге агента или выводит из направлений. Раньше запрос отвечал 200, в
    # интерфейсе маршрут исчезал, а через минуту возвращался в сеть.
    async with settings_store.write_lock():
        all_cfg = await settings_store.get_agent_all(session)
        cfg = all_cfg.get(node_id) or {}
        derived = routing.routes_by_node(await settings_store.get_routing(session))
        dropped = (set(cfg.get("routes") or []) | set(derived.get(node_id, []))) - set(routes)
        from_directions = dropped & set(derived.get(node_id, []))
        if from_directions:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Маршрут{'ы' if len(from_directions) > 1 else ''} "
                f"{', '.join(sorted(from_directions))} задан"
                f"{'ы' if len(from_directions) > 1 else ''} направлением в разделе "
                "«Маршрутизация» - снимите там, иначе панель одобрит их снова.",
            )
        if dropped and cfg:
            cfg["routes"] = [r for r in (cfg.get("routes") or []) if r not in dropped]
            cfg["panel_approved"] = [
                r for r in (cfg.get("panel_approved") or []) if r not in dropped
            ]
            all_cfg[node_id] = cfg
            await settings_store.set_agent_all(session, all_cfg)
    node = await hs_call(client.approve_routes(node_id, routes))
    await audit.record(session, user.username, "node_routes", node_id, ", ".join(routes))
    meta = await settings_store.get_node_meta(session)
    hinfo = await hostinfo.read_all_async(get_settings().headscale_db_path)
    return _map_node(node or {}, meta, hinfo)


@router.post("/{node_id}/expire", response_model=NodeOut)
async def expire_node(
    node_id: str, request: Request, user: CurrentUser, session: SessionDep
) -> NodeOut:
    settings = get_settings()
    require_hs(settings)
    client = get_client(settings)
    node = await hs_call(client.expire_node(node_id))
    await audit.record(session, user.username, "node_expire", node_id)
    meta = await settings_store.get_node_meta(session)
    hinfo = await hostinfo.read_all_async(get_settings().headscale_db_path)
    return _map_node(node or {}, meta, hinfo)


async def _delete_if_present(client, node_id: str) -> None:
    try:
        await client.delete_node(node_id)
    except HeadscaleError as exc:
        # После сбоя локальной БД ноды уже может не быть в headscale.
        # Только настоящий HTTP 404 разрешает повторить локальную очистку.
        if exc.status_code != status.HTTP_404_NOT_FOUND:
            raise


@router.delete("/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_node(
    node_id: str, request: Request, user: CurrentUser, session: SessionDep
) -> None:
    settings = get_settings()
    require_hs(settings)
    client = get_client(settings)
    async with write_lock():
        await hs_call(_delete_if_present(client, node_id))
        async with settings_store.atomic_update(session):
            # Метаданные, агенты, ACL, маршрутизация и DNS очищаются вместе.
            for nid in await settings_store.drop_gateway(session, node_id):
                await settings_store.set_agent_use_exit(session, nid, "")
            await settings_store.clear_node_meta(session, node_id)
            await settings_store.forget_node_refs(session, node_id)
        await audit.record(session, user.username, "node_delete", node_id, client_ip(request))
        # Набор серверов мог измениться после удаления.
        await apply_policy(session, client, settings)
