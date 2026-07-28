"""Хранилище редактируемых настроек панели (в БД, поверх env-дефолтов)."""

import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import AppSetting

ALERTS_KEY = "alerts"
BACKUP_KEY = "backup"
ACL_RULES_KEY = "acl_rules"
NODE_META_KEY = "node_meta"  # {node_id: {"description": "..."}}
TS_VERSION_KEY = "tailscale_version"  # пиновая версия клиента (override env)
# желаемое состояние нод для агента: какие маршруты анонсировать, быть ли exit
AGENT_KEY = "node_agent"
# направления «кто → куда → через какую ноду»: {id: {src, dst, via, ports, ips,
# resolved_at, error}}. Маршрут агента и правило ACL ВЫВОДЯТСЯ отсюда, см. app/routing.py
ROUTING_KEY = "routing"


async def get_tailscale_version(session: AsyncSession, settings: Settings) -> str:
    """Эффективная пиновая версия клиента: из БД, иначе — из env-дефолта."""
    raw = await _get_raw(session, TS_VERSION_KEY)
    return (raw or "").strip() or settings.tailscale_version


async def set_tailscale_version(session: AsyncSession, version: str) -> None:
    await _set_raw(session, TS_VERSION_KEY, version.strip())


async def get_node_meta(session: AsyncSession) -> dict:
    raw = await _get_raw(session, NODE_META_KEY)
    return json.loads(raw) if raw else {}


async def set_node_meta(
    session: AsyncSession,
    node_id: str,
    description: str | None = None,
    kind: str | None = None,
    admin: bool | None = None,
    group: str | None = None,
    subgroup: str | None = None,
    muted: bool | None = None,
    exit_gateway: bool | None = None,
    exit_via: list[str] | None = None,
    force_exit: str | None = None,
) -> None:
    """Заметка панели о ноде: описание, тип, флаг «админ» и группировка
    (группа → подгруппа, напр. организация → проект). Пустые поля убираются;
    kind не из {server, device} трактуется как «авто» (запись не хранится).

    exit_gateway — сервер разрешён как шлюз выхода в интернет (per-device).
    exit_via — у устройства: id серверов-шлюзов, через которые ему можно выходить.
    force_exit — id шлюза, через который ПРИНУДИТЕЛЬНО гнать весь трафик этой
    ноды (exit-node на источнике, ставит агент). Пусто = не форсим."""
    meta = await get_node_meta(session)
    # Запись СЛИЯНИЕМ: поля, которых вызывающий не касался (None), сохраняются.
    # Раньше запись перетиралась целиком, и любой частичный вызов молча сбрасывал
    # чужие поля: перетащил карточку ноды в другую группу — и с неё слетели
    # «не слать алерты», «шлюз выхода» и принудительный туннель. Настройка
    # безопасности не должна исчезать как побочный эффект перетаскивания.
    prev = meta.get(node_id) if isinstance(meta.get(node_id), dict) else {}
    entry: dict = dict(prev)

    def put(key: str, value, keep: bool) -> None:
        """None = поле не трогали; иначе пишем (пустое значение = стереть)."""
        if value is None:
            return
        if keep:
            entry[key] = value
        else:
            entry.pop(key, None)

    desc = (description or "").strip() if description is not None else None
    put("description", desc, bool(desc))
    if kind is not None:
        put("kind", kind, kind in ("server", "device"))
    put("admin", True, bool(admin)) if admin is not None else None
    put("exit_gateway", True, bool(exit_gateway)) if exit_gateway is not None else None
    if exit_via is not None:
        via = [str(i) for i in exit_via if str(i)]
        put("exit_via", via, bool(via))
    if force_exit is not None:
        put("force_exit", str(force_exit), bool(force_exit))
    # «не беспокоить»: нода остаётся под наблюдением и её статус виден в панели,
    # молчат только уведомления. Полезно на плановых работах, когда сервер гасят
    # намеренно и алерт про это — чистый шум.
    put("muted", True, bool(muted)) if muted is not None else None
    if group is not None:
        put("group", group.strip(), bool(group.strip()))
    if subgroup is not None:
        put("subgroup", subgroup.strip(), bool(subgroup.strip()))
    if entry:
        meta[node_id] = entry
    else:
        meta.pop(node_id, None)
    await _set_raw(session, NODE_META_KEY, json.dumps(meta, ensure_ascii=False))


async def clear_node_meta(session: AsyncSession, node_id: str) -> None:
    """Убрать заметку о ноде целиком (нода удалена). Отдельно от set_node_meta:
    та теперь СЛИВАЕТ поля, и «передать всё пустым» больше не значит «стереть»."""
    meta = await get_node_meta(session)
    if meta.pop(node_id, None) is not None:
        await _set_raw(session, NODE_META_KEY, json.dumps(meta, ensure_ascii=False))


async def set_gateway_clients(
    session: AsyncSession,
    gateway_id: str,
    device_ids: list[str],
    all_device_ids: list[str],
) -> bool:
    """Серверная сторона выбора выхода: задать, каким устройствам разрешён выход
    через шлюз `gateway_id`. Точечно правит ТОЛЬКО поле `exit_via` у каждого
    устройства (добавляет/убирает этот шлюз), не затрагивая описание/тип/группу.

    Это обратная проекция device.exit_via: связь «устройство ↔ шлюз» одна, её
    можно редактировать и со стороны устройства (set_node_meta), и отсюда — со
    стороны шлюза. Возвращает True, если что-то изменилось."""
    gateway_id = str(gateway_id)
    allowed = {str(d) for d in device_ids}
    meta = await get_node_meta(session)
    changed = False
    for did in (str(d) for d in all_device_ids):
        entry = meta.get(did)
        entry = entry if isinstance(entry, dict) else {}
        via = [str(i) for i in (entry.get("exit_via") or [])]
        has, want = gateway_id in via, did in allowed
        if want == has:
            continue
        via = via + [gateway_id] if want else [v for v in via if v != gateway_id]
        changed = True
        if via:
            entry["exit_via"] = via
        else:
            entry.pop("exit_via", None)
        if entry:
            meta[did] = entry
        else:
            meta.pop(did, None)
    if changed:
        await _set_raw(session, NODE_META_KEY, json.dumps(meta, ensure_ascii=False))
    return changed


async def get_routing(session: AsyncSession) -> dict:
    raw = await _get_raw(session, ROUTING_KEY)
    return json.loads(raw) if raw else {}


async def set_routing(session: AsyncSession, data: dict) -> None:
    await _set_raw(session, ROUTING_KEY, json.dumps(data, ensure_ascii=False))


async def get_agent_all(session: AsyncSession) -> dict:
    """{node_id: {token, routes: [], exit: bool, last_poll}} — желаемое состояние
    нод, которое агент забирает и применяет сам."""
    raw = await _get_raw(session, AGENT_KEY)
    return json.loads(raw) if raw else {}


async def set_agent_all(session: AsyncSession, data: dict) -> None:
    await _set_raw(session, AGENT_KEY, json.dumps(data, ensure_ascii=False))


async def get_agent_by_token(session: AsyncSession, token: str) -> tuple[str, dict] | None:
    if not token:
        return None
    for node_id, cfg in (await get_agent_all(session)).items():
        if cfg.get("token") == token:
            return node_id, cfg
    return None


async def touch_agent_poll(session: AsyncSession, token: str) -> None:
    """Отметка «агент жив»: он ходит сюда раз в минуту. Пишем не чаще 30 с —
    эндпоинт публичный."""
    all_cfg = await get_agent_all(session)
    now = datetime.now(timezone.utc)
    for node_id, cfg in all_cfg.items():
        if cfg.get("token") != token:
            continue
        prev = cfg.get("last_poll")
        if prev:
            try:
                if (now - datetime.fromisoformat(prev)).total_seconds() < 30:
                    return
            except (ValueError, TypeError):
                pass
        cfg["last_poll"] = now.isoformat()
        all_cfg[node_id] = cfg
        await set_agent_all(session, all_cfg)
        return


async def mark_agent_applied(session: AsyncSession, token: str, state_hash: str) -> None:
    """Отметка «агент ПРИМЕНИЛ состояние» + хеш применённого. По ней панель отличает
    работающего агента от ноды, которая только опрашивает URL, и видит отставание."""
    all_cfg = await get_agent_all(session)
    for node_id, cfg in all_cfg.items():
        if cfg.get("token") != token:
            continue
        if cfg.get("applied_hash") == state_hash and cfg.get("last_applied"):
            return  # ничего не изменилось — лишняя запись в БД ни к чему
        cfg["last_applied"] = datetime.now(timezone.utc).isoformat()
        cfg["applied_hash"] = state_hash
        all_cfg[node_id] = cfg
        await set_agent_all(session, all_cfg)
        return


async def get_acl_rules(session: AsyncSession) -> list[dict]:
    raw = await _get_raw(session, ACL_RULES_KEY)
    return json.loads(raw) if raw else []


async def set_acl_rules(session: AsyncSession, rules: list[dict]) -> None:
    await _set_raw(session, ACL_RULES_KEY, json.dumps(rules, ensure_ascii=False))


async def _get_raw(session: AsyncSession, key: str) -> str | None:
    return await session.scalar(
        select(AppSetting.value).where(AppSetting.key == key)
    )


async def _set_raw(session: AsyncSession, key: str, value: str) -> None:
    row = await session.get(AppSetting, key)
    if row is None:
        session.add(AppSetting(key=key, value=value))
    else:
        row.value = value
    await session.commit()


async def get_raw(session: AsyncSession, key: str) -> str | None:
    return await _get_raw(session, key)


async def set_raw(session: AsyncSession, key: str, value: str) -> None:
    await _set_raw(session, key, value)


async def get_alert_config(session: AsyncSession, settings: Settings) -> dict:
    """Эффективная конфигурация алертов: значение из БД, иначе — из env."""
    raw = await _get_raw(session, ALERTS_KEY)
    data = json.loads(raw) if raw else {}
    return {
        "telegram_token": data.get("telegram_token") or settings.alert_telegram_token,
        "telegram_chat": data.get("telegram_chat") or settings.alert_telegram_chat,
        # адрес Telegram Bot API: пусто = дефолт api.telegram.org (см. alerts.py).
        # Можно указать зеркало/прокси для регионов, где телега заблокирована.
        "telegram_api": data.get("telegram_api") or "",
        "webhook": data.get("webhook") or settings.alert_webhook,
    }


async def set_alert_config(
    session: AsyncSession,
    telegram_token: str,
    telegram_chat: str,
    webhook: str,
    telegram_api: str = "",
) -> None:
    await _set_raw(
        session,
        ALERTS_KEY,
        json.dumps(
            {
                "telegram_token": telegram_token.strip(),
                "telegram_chat": telegram_chat.strip(),
                "telegram_api": telegram_api.strip().rstrip("/"),
                "webhook": webhook.strip(),
            }
        ),
    )


async def get_backup_config(session: AsyncSession, settings: Settings) -> dict:
    """Эффективная конфигурация автобэкапа: значение из БД, иначе — из env."""
    raw = await _get_raw(session, BACKUP_KEY)
    data = json.loads(raw) if raw else {}
    return {
        "interval_hours": int(
            data.get("interval_hours", settings.backup_interval_hours)
        ),
        "keep": int(data.get("keep", settings.backup_keep)),
    }


async def set_backup_config(
    session: AsyncSession, interval_hours: int, keep: int
) -> None:
    await _set_raw(
        session,
        BACKUP_KEY,
        json.dumps({"interval_hours": int(interval_hours), "keep": int(keep)}),
    )
