"""Фоновый коллектор: периодически опрашивает headscale, пишет историю числа
нод онлайн, шлёт алерты о падении/восстановлении нод и о скором истечении ключей."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from math import ceil

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import alerts, policy_apply, routing, settings_store
from app.config import Settings
from app.hs_client import HeadscaleError, get_client
from app.models import NodeMetricSample, NodeStatus
from app.nodekind import effective_kind

log = logging.getLogger("noderoost.collector")

# Как часто подчищать pre-auth-ключи. Порог отбора — дни, так что сметать их
# каждый цикл сбора (раз в минуту) смысла нет: это лишний запрос к headscale.
KEY_PRUNE_INTERVAL = timedelta(hours=1)
_last_key_prune: datetime | None = None


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _fields(n: dict) -> dict:
    return {
        "id": str(n.get("id", "")),
        "name": n.get("givenName") or n.get("name", ""),
        "online": bool(n.get("online", False)),
        "expiry": n.get("expiry"),
    }


async def collect_once(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> int:
    if not settings.headscale_api_key:
        return 0
    client = get_client(settings)
    raw = await client.get_nodes()
    nodes = [_fields(n) for n in raw]
    total = len(nodes)
    online = sum(1 for n in nodes if n["online"])

    async with session_factory() as session:
        session.add(NodeMetricSample(total=total, online=online))
        await session.commit()

    online_map = {n["id"]: n["online"] for n in nodes}
    names = {n["id"]: n["name"] for n in nodes}
    # тип ноды (server/device) — алертим только серверы (устройства гасят на ночь)
    async with session_factory() as session:
        # Нода после переподключения приходит с НОВЫМ id — забираем отложенную
        # заметку (тип, админ, описание) обратно, иначе она вернётся чистой.
        await settings_store.claim_pending_meta(session, raw)
        meta = await settings_store.get_node_meta(session)
    kinds = {str(n.get("id", "")): effective_kind(n, meta) for n in raw}
    # «не беспокоить»: помеченные ноды наблюдаем как обычно, но не уведомляем
    muted = {
        nid for nid, e in meta.items() if isinstance(e, dict) and e.get("muted")
    }
    async with session_factory() as session:
        await alerts.reconcile_nodes(session, settings, online_map, names, kinds, muted)
    # агент на ноде умирает молча (при недоступности панели выходит с кодом 0),
    # поэтому единственный признак — давность его последнего опроса
    async with session_factory() as session:
        agents = await settings_store.get_agent_all(session)
        await alerts.reconcile_agents(session, settings, agents, names, online_map, muted)
    async with session_factory() as session:
        await _check_key_expiry(session, settings, nodes, muted)
    # маршруты, заказанные в панели, одобряем без участия человека
    try:
        await _auto_approve_requested(session_factory, settings, raw)
    except Exception:  # noqa: BLE001 — не роняем цикл сбора
        log.exception("не удалось одобрить заказанные маршруты")
    # одноразовые ключи enroll-флоу копятся вечно — сметаем отработавшие
    global _last_key_prune
    now = datetime.now(timezone.utc)
    if _last_key_prune is None or now - _last_key_prune >= KEY_PRUNE_INTERVAL:
        _last_key_prune = now
        try:
            await _prune_preauthkeys(settings, raw)
        except Exception:  # noqa: BLE001 — не роняем цикл сбора
            log.exception("не удалось подчистить pre-auth-ключи")
    # Самоисцеление ACL: если набор нод изменился (нода удалена/истекла любым путём),
    # перепушить политику, чтобы литеральный IP исчезнувшей ноды не завис в ACL и не
    # достался узлу, переиспользовавшему адрес. Пуш только при реальном изменении.
    async with session_factory() as session:
        if await policy_apply.reconcile_policy(session, client, settings, raw):
            log.info("ACL самоисцелён: набор нод изменился — политика перепушена")
    return total


async def _auto_approve_requested(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    raw: list[dict],
) -> None:
    """Приводит одобренные маршруты к тому, что панель СЕЙЧАС хочет от ноды:
    заказанное админом одобряет, а собственные прошлые одобрения, которые больше
    не нужны, СНИМАЕТ.

    Раньше набор только рос («target = approved | …»), и это было дырой: админ
    удаляет направление — маршрут перестаёт анонсироваться и выпадает из активных,
    но одобрение остаётся навсегда. Скомпрометированной ноде достаточно одной
    команды `tailscale set --advertise-routes=…` (разрешения на анонс не нужно),
    чтобы отозванный маршрут снова стал активным — и трафик к этому адресу со
    всех нод (они подключаются с --accept-routes) пошёл через неё.

    Ручные одобрения админа (модалка «Маршруты») при этом не трогаем: снимаем
    только то, что одобрила сама панель, — для этого и храним panel_approved.
    """
    async with session_factory() as session:
        agents = await settings_store.get_agent_all(session)
        # направления тоже задают маршруты ноды-выхода — без них панель считала бы
        # их «чужими» и никогда не снимала (а при удалении направления — не сняла бы)
        derived = routing.routes_by_node(await settings_store.get_routing(session))
    if not agents:
        return
    client = get_client(settings)
    for n in raw:
        nid = str(n.get("id", ""))
        cfg = agents.get(nid)
        if not cfg:
            continue
        want = set(cfg.get("routes") or []) | set(derived.get(nid, []))
        if cfg.get("exit"):
            want |= {"0.0.0.0/0", "::/0"}
        avail = set(n.get("availableRoutes") or [])
        approved = set(n.get("approvedRoutes") or [])
        mine = set(cfg.get("panel_approved") or [])  # что одобряла сама панель
        # снимаем ровно своё и ровно то, чего больше не хотим; чужое (ручное
        # одобрение админа в модалке «Маршруты») остаётся нетронутым
        stale = mine - want
        target = (approved - stale) | (want & avail)
        if target != approved:
            await client.approve_routes(nid, sorted(target))
            added, removed = sorted(target - approved), sorted(approved - target)
            if added:
                log.info("нода %s: одобрены заказанные маршруты %s", nid, added)
            if removed:
                log.info("нода %s: снято одобрение (панель больше не просит) %s", nid, removed)
        new_mine = (mine | (want & avail)) - stale
        if new_mine != mine:
            async with session_factory() as session:
                all_cfg = await settings_store.get_agent_all(session)
                entry = all_cfg.get(nid)
                if entry is not None:  # ноду могли удалить, пока мы ходили в headscale
                    entry["panel_approved"] = sorted(new_mine)
                    await settings_store.set_agent_all(session, all_cfg)


def _keys_to_prune(
    keys: list[dict], in_use: set[str], days: int, now: datetime
) -> list[str]:
    """Отбирает id отработавших pre-auth-ключей: (просрочен ИЛИ использован
    одноразовый) И создан больше `days` дней назад.

    Многоразовый ключ с used=true рабочим быть не перестал — его сметает только
    срок. Ключ без разбираемой даты создания не трогаем: неизвестный возраст —
    не повод удалять.
    """
    cutoff = now - timedelta(days=days)
    out: list[str] = []
    for k in keys:
        kid = str(k.get("id", ""))
        if not kid or kid in in_use:
            continue
        created = _parse(k.get("createdAt"))
        if created is None or created > cutoff:
            continue
        exp = _parse(k.get("expiration"))
        expired = exp is not None and exp <= now
        spent = bool(k.get("used")) and not k.get("reusable")
        if expired or spent:
            out.append(kid)
    return out


async def _prune_preauthkeys(settings: Settings, raw: list[dict]) -> int:
    """Удаляет накопившиеся одноразовые ключи enroll-флоу: каждое подключение
    ноды создаёт ключ, и удалять их некому.

    Ключ, которым зарегистрирована живая нода, не трогаем ни при каком возрасте:
    в БД headscale нода на него ссылается, и это единственный след того, откуда
    она взялась.
    """
    days = settings.preauth_retention_days
    if days <= 0:
        return 0
    client = get_client(settings)
    keys = await client.list_preauthkeys()
    in_use = {str((n.get("preAuthKey") or {}).get("id", "")) for n in raw}
    in_use.discard("")
    removed = 0
    for kid in _keys_to_prune(keys, in_use, days, datetime.now(timezone.utc)):
        try:
            await client.delete_preauthkey(kid)
            removed += 1
        except HeadscaleError as exc:
            # один упрямый ключ (напр. ссылка из БД headscale) не должен
            # останавливать сметание остальных
            log.warning("не удалось удалить pre-auth-ключ %s: %s", kid, exc)
    if removed:
        log.info("подчищено отработавших pre-auth-ключей: %d", removed)
    return removed


async def _check_key_expiry(
    session: AsyncSession,
    settings: Settings,
    nodes: list[dict],
    muted: set[str] | None = None,
) -> None:
    warn = settings.key_expiry_warn_days
    if warn <= 0:
        return
    now = datetime.now(timezone.utc)
    known = {s.node_id: s for s in await session.scalars(select(NodeStatus))}
    cfg: dict | None = None
    changed = False
    for n in nodes:
        st = known.get(n["id"])
        if st is None:
            continue
        expiring, days = False, 0
        dt = _parse(n["expiry"])
        if dt is not None and dt > now:
            left = dt - now
            expiring = left <= timedelta(days=warn)
            # Остаток округляем ВВЕРХ: до смерти ключа 40 минут — это «через 1 дн.»,
            # а не «через 0 дн.», как выходило при обрезании вниз.
            days = max(1, ceil(left.total_seconds() / 86400))
        if n["id"] in (muted or set()):
            continue
        if expiring and not st.key_alerted:
            st.key_alerted = True
            changed = True
            if cfg is None:
                cfg = await settings_store.get_alert_config(session, settings)
            if alerts.alerts_enabled(cfg):
                await alerts.send_alert(
                    cfg,
                    f"🔑 NodeRoost: ключ ноды «{n['name']}» истекает через {days} дн.",
                    settings.panel_url or None,
                )
        elif not expiring and st.key_alerted:
            st.key_alerted = False  # ключ продлили / срок далеко — сбрасываем дедуп
            changed = True
    if changed:
        await session.commit()


async def _prune(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(
        days=settings.metrics_retention_days
    )
    async with session_factory() as session:
        await session.execute(
            delete(NodeMetricSample).where(NodeMetricSample.ts < cutoff)
        )
        await session.commit()


async def collector_loop(
    session_factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    if settings.metrics_interval <= 0:
        log.info("сбор метрик выключен (metrics_interval=0)")
        return
    while True:
        hs_ok = True
        try:
            await collect_once(session_factory, settings)
            await _prune(session_factory, settings)
        except HeadscaleError as exc:
            hs_ok = False
            log.warning("headscale недоступен при сборе метрик: %s", exc)
        except Exception:  # noqa: BLE001 — цикл не должен падать
            log.exception("ошибка сбора метрик")
        # самоконтроль: сообщить, что control-сервер лёг/поднялся. Молчание
        # коллектора иначе неотличимо от «всё в порядке».
        try:
            async with session_factory() as session:
                await alerts.reconcile_selfcheck(session, settings, hs_ok)
        except Exception:  # noqa: BLE001 — самоконтроль не должен ронять цикл
            log.exception("ошибка самоконтроля")
        await asyncio.sleep(settings.metrics_interval)
