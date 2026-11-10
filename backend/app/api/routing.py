"""API направлений: «кто → куда → через какую ноду».

Панель хранит намерение, а маршрут ноды-выхода и правило доступа выводит из него
(см. app/routing.py). Поэтому здесь при каждом изменении делаются три вещи:
резолв адреса, пуш политики и одобрение маршрута на ноде-выходе.
"""

import logging

from anyio import CancelScope
from fastapi import APIRouter, HTTPException, status

from app import aclgen, audit, policy_apply, routing, settings_store
from app.config import get_settings
from app.deps import CurrentUser, SessionDep
from app.hs_client import HeadscaleError, get_client
from app.hs_util import hs_call, require_hs
from app.nodekind import effective_kind
from app.schemas import DirectionIn, DirectionOut, DirectionsOut

router = APIRouter(prefix="/routing", tags=["routing"])
log = logging.getLogger("noderoost.routing")


async def _nodes_map() -> dict[str, dict]:
    settings = get_settings()
    if not settings.headscale_api_key:
        return {}
    try:
        return {str(n.get("id", "")): n for n in await get_client(settings).get_nodes()}
    except HeadscaleError:
        return {}


def _out(did: str, d: dict, nodes: dict, agents: dict) -> DirectionOut:
    via = str(d.get("via") or "")
    raw_src = d.get("src")
    src_list = [raw_src] if isinstance(raw_src, str) else list(raw_src or [])
    node = nodes.get(via) or {}
    serving = set(node.get("subnetRoutes") or [])
    # full-туннель через subnet-маршруты УБРАН как небезопасный (см. routing._dst_ips):
    # старые записи с full теперь инертны (ips пуст), в UI помечены «устарело».
    full = bool(d.get("full"))
    ips = list(d.get("ips") or [])
    wanted = {ip if "/" in ip else f"{ip}/32" for ip in ips}
    return DirectionOut(
        id=did,
        src_kind=str(d.get("src_kind") or "node"),
        src=[str(i) for i in src_list],
        full=full,
        dst="весь трафик" if full else str(d.get("dst") or ""),
        via=via,
        ports=str(d.get("ports") or "*"),
        ips=ips,
        resolved_at=d.get("resolved_at"),
        error=str(d.get("error") or ""),
        # «активно» = нода-выход реально раздаёт ВСЕ адреса направления
        active=bool(wanted) and wanted <= serving,
        via_agent=bool((agents.get(via) or {}).get("last_poll")),
    )


async def _sync_approvals(session, client, settings) -> None:
    try:
        if await routing.reconcile_approvals(session, client):
            await policy_apply.apply_policy(session, client, settings)
    except HeadscaleError as exc:
        # Коллектор повторит одобрение или отзыв по сохраненному намерению.
        log.warning("не удалось синхронизировать маршруты направлений: %s", exc)


async def _save_directions(session, directions, client, settings) -> None:
    try:
        async with settings_store.atomic_update(session):
            await settings_store.set_routing(session, directions)
            await hs_call(policy_apply.require_policy(session, client, settings))
    except BaseException:
        # Сверяем политику с БД, если подтверждение от Headscale или commit потерялось.
        with CancelScope(shield=True):
            await policy_apply.apply_policy(session, client, settings)
        raise
    await _sync_approvals(session, client, settings)


@router.get("", response_model=DirectionsOut)
async def list_directions(_: CurrentUser, session: SessionDep) -> DirectionsOut:
    directions = await settings_store.get_routing(session)
    nodes = await _nodes_map()
    agents = await settings_store.get_agent_all(session)
    return DirectionsOut(
        directions=[_out(did, d, nodes, agents) for did, d in directions.items()]
    )


@router.post("", response_model=DirectionOut)
async def create_direction(
    body: DirectionIn, user: CurrentUser, session: SessionDep
) -> DirectionOut:
    # Ноду-выход из источников выкидываем молча: при групповом выборе она почти
    # всегда там окажется, и падать из-за этого было бы вредно. Ошибка нужна лишь
    # тогда, когда после этого не осталось никого.
    src_ids = [i for i in body.src if i != body.via]
    if body.src_kind == "node" and not src_ids:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Источник и нода-выход совпадают — трафику некуда идти",
        )
    # храним ОЧИЩЕННЫЙ адрес, а не то, что вставили: иначе в таблице висело бы
    # «https://ifconfig.me/», а резолвиться при этом стал бы «ifconfig.me»
    dst = routing.normalize_dst(body.dst)
    # Подсеть, накрывающая адреса меша, — это не «направление», а открытие всего
    # тайлнета разом (0.0.0.0/0 содержит и 100.64.0.0/10).
    if aclgen.touches_mesh(dst):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"«{dst}» — адрес внутри самого меша. Ноды и так достижимы напрямую. "
            "Весь трафик сервера через другой узел настраивается exit-нодой (см. «Шлюз выхода»), а не направлением.",
        )
    # Свои-у-каждого адреса: loopback, link-local (там же метаданные облака),
    # мультикаст. Вести их через чужую ноду нельзя — см. routing.is_machine_local.
    if routing.is_machine_local(dst):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"«{dst}» — у каждой машины свой собственный. 127.0.0.1 — это она сама, "
            "169.254.x.x — её провод и служба метаданных облака с токенами доступа. "
            "Через чужой узел такие адреса не ведут: запросы уйдут не туда.",
        )
    # Широкий публичный префикс — это не «направление», а увод чужого интернета:
    # такой маршрут любая нода с accept-routes подхватывает сама (из-за этого убран
    # полный туннель). Приватные сети не ограничиваем — см. routing.too_broad.
    if routing.too_broad(dst):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"«{dst}» — слишком широкая публичная сеть (нужен префикс /"
            f"{routing.MIN_PUBLIC_PREFIX} или уже). Такой маршрут подхватят и другие "
            "ноды, и через шлюз уйдёт их трафик. Весь трафик сервера через другой "
            "узел настраивается exit-нодой («Шлюз выхода»).",
        )
    ips, err = await routing.resolve_dst(dst)
    if not ips:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"Не удалось определить адрес: {err}"
        )
    entry = {
        "src_kind": body.src_kind,
        "src": src_ids,
        "full": False,
        "dst": dst,
        "via": body.via,
        "ports": body.ports or "*",
        "ips": ips,
        "resolved_at": None,
        "error": "",
    }
    # Замок держим до пуша: иначе параллельная правка успеет запушить между
    # нашим сохранением и нашим пушем, и в сети окажется не то, что в панели.
    settings = get_settings()
    require_hs(settings)
    client = get_client(settings)
    async with policy_apply.write_lock():
        nodes = await hs_call(client.get_nodes())
        via_node = next((n for n in nodes if str(n.get("id")) == body.via), None)
        if via_node is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Нода-выход не найдена")
        meta = await settings_store.get_node_meta(session)
        if effective_kind(via_node, meta) != "server":
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Через устройство ходить нельзя — выберите сервер. "
                "Иначе чужой трафик пойдёт через личную машину пользователя.",
            )
        directions = await settings_store.get_routing(session)
        did = routing.new_id()
        directions[did] = entry
        await _save_directions(session, directions, client, settings)
    await audit.record(
        session,
        user.username,
        "direction_add",
        body.dst,
        f"через ноду {body.via}"
    )
    nodes = await _nodes_map()
    agents = await settings_store.get_agent_all(session)
    return _out(did, directions[did], nodes, agents)


@router.delete("/{direction_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_direction(
    direction_id: str, user: CurrentUser, session: SessionDep
) -> None:
    settings = get_settings()
    require_hs(settings)
    client = get_client(settings)
    async with policy_apply.write_lock():
        directions = await settings_store.get_routing(session)
        d = directions.pop(direction_id, None)
        if d is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Направление не найдено")
        await _save_directions(session, directions, client, settings)
    await audit.record(session, user.username, "direction_del", str(d.get("dst", "")), "")


@router.post("/refresh", response_model=DirectionsOut)
async def refresh_directions(user: CurrentUser, session: SessionDep) -> DirectionsOut:
    """Перерезолвить адреса всех направлений прямо сейчас (не дожидаясь фонового
    цикла) — на случай «сайт переехал, а я хочу починить сразу»."""
    changed = await routing.refresh(session, force=True)
    settings = get_settings()
    if changed and settings.headscale_api_key:
        client = get_client(settings)
        async with policy_apply.write_lock():
            await hs_call(policy_apply.require_policy(session, client, settings))
            await _sync_approvals(session, client, settings)
    await audit.record(
        session, user.username, "direction_refresh", "", f"обновлено: {len(changed)}"
    )
    return await list_directions(user, session)
