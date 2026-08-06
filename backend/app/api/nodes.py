import ipaddress
import json
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request, status

from app import aclgen, audit, ca, enroll, exitvia, geoip, hostinfo, settings_store
from app.clientip import client_ip
from app.config import get_settings
from app.deps import CurrentUser, SessionDep
from app.hs_client import get_client
from app.hs_util import hs_call, norm_ts, require_hs
from app.nodekind import editable_tags, effective_kind, is_online, node_tags
from app.policy_apply import apply_policy, declare_tags
from app.schemas import (
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
    hinfo = hostinfo.read_all(settings.headscale_db_path)
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
    hinfo = hostinfo.read_all(settings.headscale_db_path)
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


@router.post("/{node_id}/meta", response_model=NodeOut)
async def set_meta(
    node_id: str,
    body: NodeMetaIn,
    user: CurrentUser,
    session: SessionDep,
) -> NodeOut:
    """Заметка панели о ноде: описание + тип (сервер/устройство). Хранится в БД
    панели, не в headscale. kind="" — вернуть в авто-определение."""
    settings = get_settings()
    require_hs(settings)
    client = get_client(settings)
    node = await hs_call(client.get_node(node_id))
    if not node:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Нода не найдена")
    # Через устройство трафик ходить не должен, и проверить это только в момент
    # создания направления мало: иначе ноду достаточно сделать выходом, пока она
    # сервер, а потом переобозвать устройством — направление продолжит жить, и
    # чужой трафик пойдёт через машину, помеченную как личная.
    if body.kind == "device":
        used = [
            d
            for d in (await settings_store.get_routing(session)).values()
            if str(d.get("via") or "") == str(node_id)
        ]
        if used:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Нода — выход в {len(used)} направлени{'и' if len(used) == 1 else 'ях'} "
                "(«Маршрутизация»). Устройством её сделать нельзя: через личные машины "
                "чужой трафик не пускаем. Сначала уберите эти направления.",
            )
    # Шлюзом выхода может быть только СЕРВЕР. Через личную машину чужой трафик не
    # пускаем — и, что важнее, шлюз получает служебный тег, который становится
    # назначением в правиле видимости: тег на устройстве открыл бы устройства друг
    # другу в обход изоляции. UI галку устройству не показывает, но запрет должен
    # держаться и при прямом обращении к API.
    # Тип берём из сохранённой меты, а запросом только ПЕРЕКРЫВАЕМ. Иначе запрос
    # без поля kind (галку шлюза ставят отдельно от смены типа) приходил с kind=None,
    # мета подменялась пустой — и сервер, помеченный руками, но без тегов и
    # маршрутов, авто-определялся устройством: панель отказывалась делать шлюзом
    # именно ту ноду, ради которой ручной выбор типа и существует.
    meta_stored = await settings_store.get_node_meta(session)
    if body.exit_gateway and effective_kind(
        node, _kind_ctx(meta_stored, node.get("id", node_id), body.kind)
    ) != "server":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Шлюзом выхода может быть только сервер: через личные устройства чужой "
            "трафик не пускаем.",
        )
    # Принудительный выход через шлюз подразумевает, что этот шлюз ноде РАЗРЕШЁН
    # (иначе headscale отвергнет exit-node): дописываем его в exit_via, чтобы
    # сгенерировался грант autogroup:internet via tag:xgw-<шлюз>.
    #
    # И сам шлюз обязан быть УЖЕ помеченным шлюзом: иначе трафик ноды можно было бы
    # завернуть через произвольный узел (в т.ч. чужое устройство), а агент молча
    # долбился бы в ноду, которая exit не анонсирует.
    meta_now = await settings_store.get_node_meta(session)
    stored = meta_now.get(node_id) if isinstance(meta_now.get(node_id), dict) else {}
    # Поле, которого в запросе НЕТ (None), не меняется — берём хранимое. Иначе
    # частичное обновление (перетаскивание карточки шлёт только группу) сняло бы
    # с сервера тег шлюза и убило выход у зависимых нод как побочный эффект.
    eff_gateway = (
        bool(stored.get("exit_gateway")) if body.exit_gateway is None else body.exit_gateway
    )
    eff_force = (
        str(stored.get("force_exit") or "") if body.force_exit is None else body.force_exit
    )
    if eff_force and eff_force not in exitvia.gateways(meta_now):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Принудительный выход возможен только через сервер, помеченный "
            "«Шлюзом выхода в интернет».",
        )
    eff_via = None
    if body.exit_via is not None or eff_force:
        base = body.exit_via if body.exit_via is not None else [
            str(i) for i in (stored.get("exit_via") or [])
        ]
        eff_via = list(base)
        if eff_force and eff_force not in eff_via:
            eff_via.append(eff_force)
    await settings_store.set_node_meta(
        session, node_id, body.description, body.kind, body.admin,
        body.group, body.subgroup, body.muted, body.exit_gateway, eff_via,
        body.force_exit,
    )
    # Сняли «шлюз выхода» — снимаем и выбор этого шлюза у устройств: иначе связь
    # висит в карточке как рабочая, а принудительный выход гонит трафик на ноду,
    # которой политика выходить наружу уже не разрешает.
    if stored.get("exit_gateway") and not eff_gateway:
        for nid in await settings_store.drop_gateway(session, node_id):
            await _set_agent_use_exit(session, nid, "")
    await audit.record(session, user.username, "node_meta", node_id, body.kind or "")
    # Принудительный выход: кладём тайнет-IP шлюза в конфиг агента этой ноды —
    # агент поставит `tailscale set --exit-node`. Это exit-node, а не subnet-
    # маршруты, поэтому весь трафik ноды идёт через шлюз и НЕ течёт на другие ноды.
    gw_ip = ""
    if eff_force:
        gw = await hs_call(client.get_node(eff_force))
        gw_ip = aclgen._ipv4(gw or {}) or ""
    await _set_agent_use_exit(session, node_id, gw_ip)
    # Сервер-шлюз выхода: должен быть exit-нодой (анонсировать 0.0.0.0/0) И иметь
    # служебный тег, по которому via его находит. Тег объявляем в политике до
    # назначения (declare_tags), иначе headscale его не примет; exit включаем
    # через агента. Снятие галки — убираем и то, и другое.
    tag = exitvia.gateway_tag(node_id)
    if eff_gateway:
        await declare_tags(session, client, settings, [tag])
        cur = [t for t in node_tags(node) if t != tag]
        await hs_call(client.set_node_tags(node_id, cur + [tag]))
        await _set_agent_exit(session, node_id, True)
    else:
        cur = [t for t in node_tags(node) if t != tag]
        if cur != node_tags(node):
            await hs_call(client.set_node_tags(node_id, exitvia.keep_tagged(cur)))
        await _set_agent_exit(session, node_id, False)
        # перестал быть шлюзом — снимаем принудительный выход у зависимых нод,
        # иначе у них остался бы exit-node без гранта (интернет молча умрёт)
        await _clear_dependent_force_exit(session, node_id)
    await apply_policy(session, client, settings)
    node = await hs_call(client.get_node(node_id))  # теги могли смениться
    meta = await settings_store.get_node_meta(session)
    # hinfo обязателен и здесь: интерфейс перерисовывает карточку ЭТИМ ответом,
    # и без него у ноды пропадали ОС, адрес и флаг страны до обновления списка.
    hinfo = hostinfo.read_all(settings.headscale_db_path)
    return _map_node(node or {}, meta, hinfo)


@router.post("/{node_id}/exit-clients", response_model=NodeOut)
async def set_exit_clients(
    node_id: str,
    body: ExitClientsIn,
    user: CurrentUser,
    session: SessionDep,
) -> NodeOut:
    """Серверная сторона выбора выхода: задать, каким устройствам разрешён выход
    в интернет через ЭТОТ шлюз. Правит поле exit_via у указанных устройств —
    та же связь, что редактируется и в карточке устройства, только с другой
    стороны."""
    settings = get_settings()
    require_hs(settings)
    client = get_client(settings)
    node = await hs_call(client.get_node(node_id))
    if not node:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Нода не найдена")
    meta = await settings_store.get_node_meta(session)
    nodes = await hs_call(client.get_nodes()) or []
    # через шлюз ходят только устройства — серверы в список не берём
    device_ids = [
        str(n.get("id", "")) for n in nodes if effective_kind(n, meta) != "server"
    ]
    await settings_store.set_gateway_clients(
        session, node_id, body.devices, device_ids
    )
    await audit.record(
        session, user.username, "node_exit_clients", node_id, str(len(body.devices))
    )
    await apply_policy(session, client, settings)
    node = await hs_call(client.get_node(node_id))
    meta = await settings_store.get_node_meta(session)
    # hinfo обязателен и здесь: интерфейс перерисовывает карточку ЭТИМ ответом,
    # и без него у ноды пропадали ОС, адрес и флаг страны до обновления списка.
    hinfo = hostinfo.read_all(settings.headscale_db_path)
    return _map_node(node or {}, meta, hinfo)


async def _set_agent_exit(session, node_id: str, on: bool) -> None:
    """Включить/выключить exit у агента ноды (шлюз выхода = exit-нода)."""
    agents = await settings_store.get_agent_all(session)
    cfg = agents.get(node_id) or {}
    if bool(cfg.get("exit")) == on:
        return
    cfg["exit"] = on
    agents[node_id] = cfg
    await settings_store.set_agent_all(session, agents)


async def _clear_dependent_force_exit(session, gateway_id: str) -> list[str]:
    """Снять принудительный выход у нод, которые ходили через ЭТОТ шлюз.

    Нужно, когда шлюз перестаёт быть шлюзом (снята галка) или удаляется: иначе у
    зависимой ноды остаётся exit-node на узел, которого больше нет в разрешённых, —
    грант на выход пропадает, а `--exit-node` остаётся, и интернет на ней молча
    умирает. Возвращает id затронутых нод."""
    meta = await settings_store.get_node_meta(session)
    hit = [
        nid
        for nid, e in meta.items()
        if isinstance(e, dict) and str(e.get("force_exit") or "") == str(gateway_id)
    ]
    for nid in hit:
        meta[nid].pop("force_exit", None)
        if not meta[nid]:
            meta.pop(nid, None)
        await _set_agent_use_exit(session, nid, "")
    if hit:
        await settings_store.set_raw(
            session, settings_store.NODE_META_KEY, json.dumps(meta, ensure_ascii=False)
        )
    return hit


async def _set_agent_use_exit(session, node_id: str, ip: str) -> None:
    """Принудительный выход: тайнет-IP шлюза в конфиг агента ноды (агент поставит
    `tailscale set --exit-node`). Пусто = снять принудительный выход.

    Значение уходит в команду, которую агент выполняет на ноде под root, поэтому
    пропускаем только настоящий IPv4 из диапазона меша: мусор (или что-то, что
    когда-нибудь придёт из headscale не в том виде) до шелла не доберётся."""
    if ip:
        try:
            if not aclgen.MESH_RANGE.overlaps(
                ipaddress.ip_network(f"{ip}/32", strict=False)
            ):
                ip = ""
        except ValueError:
            ip = ""
    agents = await settings_store.get_agent_all(session)
    cfg = agents.get(node_id) or {}
    if str(cfg.get("use_exit") or "") == ip:
        return
    if ip:
        cfg["use_exit"] = ip
    else:
        cfg.pop("use_exit", None)
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
    settings = get_settings()
    require_hs(settings)
    client = get_client(settings)
    node = await hs_call(client.get_node(node_id))
    if not node:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Нода не найдена")
    name = node.get("givenName") or node.get("name", "") or "node"

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
    # только теперь удаляем старую запись (если создание ключа упало — нода цела)
    # Заметки панели привязаны к id, а переподключение заводит ноду заново —
    # откладываем их по имени, иначе нода вернётся без типа, админ-флага
    # и описания (см. settings_store.claim_pending_meta).
    meta_now = await settings_store.get_node_meta(session)
    await settings_store.stash_node_meta(
        session, str(node.get("givenName") or node.get("name") or ""),
        meta_now.get(str(node_id)) or {}, old_id=str(node_id),
    )
    await hs_call(client.delete_node(node_id))
    # мета была привязана к старому id — чистим (панель перенесёт тип/описание/
    # админ/роли на новую ноду при переподключении)
    await settings_store.clear_node_meta(session, node_id)
    version = await settings_store.get_tailscale_version(session, settings)
    ca_pem = await ca.root_cert(session) if await ca.auto_install(session) else ""
    script = enroll.build_script(
        body.os, settings, key.get("key", ""), name, version=version,
        force_reauth=True, ca_pem=ca_pem,
    )
    from app.api.enroll import _join_link

    url, cmd = await _join_link(session, settings, script, body.os, exp_iso)
    await audit.record(session, user.username, "node_reconnect", node_id, name)
    await apply_policy(session, client, settings)
    return EnrollOut(
        os=body.os,
        hostname=name,
        login_server=settings.headscale_server_url,
        script=script,
        key_id=str(key.get("id", "")),
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
    hinfo = hostinfo.read_all(get_settings().headscale_db_path)
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
    current = await hs_call(client.get_node(node_id))
    if not current:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Нода не найдена")
    keep = [t for t in node_tags(current) if exitvia.is_service_tag(t)]
    # headscale не примет тег, которого нет в tagOwners действующей политики,
    # поэтому СНАЧАЛА объявляем его там, и только потом вешаем на ноду.
    if tags:
        await declare_tags(session, client, settings, tags)
    # Пустой список headscale отвергает («последний тег снять нельзя»), поэтому
    # снятие ПОСЛЕДНЕЙ роли оставляет служебный маркер — в ролях он не виден.
    want = exitvia.keep_tagged(tags + keep)
    if exitvia.MARKER_TAG in want:
        await declare_tags(session, client, settings, [exitvia.MARKER_TAG])
    node = await hs_call(client.set_node_tags(node_id, want))
    await audit.record(session, user.username, "node_tags", node_id, ", ".join(tags))
    meta = await settings_store.get_node_meta(session)
    hinfo = hostinfo.read_all(get_settings().headscale_db_path)
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
    node = await hs_call(client.approve_routes(node_id, routes))
    await audit.record(session, user.username, "node_routes", node_id, ", ".join(routes))
    meta = await settings_store.get_node_meta(session)
    hinfo = hostinfo.read_all(get_settings().headscale_db_path)
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
    hinfo = hostinfo.read_all(get_settings().headscale_db_path)
    return _map_node(node or {}, meta, hinfo)


@router.delete("/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_node(
    node_id: str, request: Request, user: CurrentUser, session: SessionDep
) -> None:
    settings = get_settings()
    require_hs(settings)
    client = get_client(settings)
    await hs_call(client.delete_node(node_id))
    # ноду могли использовать шлюзом выхода — снимаем форс у зависимых, иначе они
    # остались бы с exit-node на удалённый узел (трафик в никуда)
    await _clear_dependent_force_exit(session, node_id)
    await settings_store.clear_node_meta(session, node_id)  # чистим мету целиком
    # правила и направления держат id ноды, а запись агента — её токен: без
    # уборки правило висит в списке как действующее, а агент «молчит» вечно
    await settings_store.forget_node_refs(session, node_id)
    await audit.record(session, user.username, "node_delete", node_id, client_ip(request))
    # набор серверов мог измениться (удалили сервер) — пересобрать политику
    await apply_policy(session, client, settings)
