"""Направления: «кто → куда → через какую ноду».

Зачем отдельная сущность. Чтобы устройство ходило на конкретный адрес через
конкретную ноду, нужны ДВЕ несвязанные вещи: нода-выход должна анонсировать
маршрут до этого адреса, а ACL — разрешить источнику до него ходить. Раньше это
настраивалось в двух разных местах, и домен резолвился дважды; стоило адресам
разойтись — не работало ничего, причём молча.

Здесь хранится НАМЕРЕНИЕ («ноутбук → api.example через edge-1»), а маршрут и правило
из него выводятся. Рассинхронизации быть не может: обе стороны считаются из
одной записи. Побочная выгода — домен можно перерезолвивать: адрес сайта сменится,
и панель сама обновит и маршрут, и правило, вместо того чтобы тихо сломаться.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import secrets
import socket
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app import settings_store

# Как часто перепроверять адреса доменов. Час — компромисс: TTL у сайтов обычно
# меньше, но дёргать резолвер каждую минуту ради записи, которая меняется раз в
# полгода, ни к чему.
REFRESH_AFTER = timedelta(hours=1)


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw))
    except (ValueError, TypeError):
        return None


def normalize_dst(raw: str) -> str:
    """То, что вставил человек → то, что можно резолвить.

    Адрес почти всегда копируют из адресной строки браузера целиком: со схемой,
    путём, иногда с портом. Резолверу нужно голое имя, и «https://ifconfig.me»
    он честно не находит.

    ВАЖЕН ПОРЯДОК: подсеть разбираем ДО обрезки по слешу, иначе «10.0.0.0/24»
    превратилось бы в одинокий «10.0.0.0» — то есть в другую цель.
    """
    s = (raw or "").strip()
    if not s:
        return ""
    try:
        ipaddress.ip_network(s, strict=False)
    except ValueError:
        pass
    else:
        return s
    if "://" in s:
        s = s.split("://", 1)[1]
    for sep in ("/", "?", "#"):
        s = s.split(sep, 1)[0]
    if "@" in s:  # user:pass@host
        s = s.rsplit("@", 1)[1]
    if s.startswith("["):  # [2a03::1]:443 — IPv6-литерал в скобках
        s = s[1:].split("]", 1)[0]
    elif s.count(":") == 1:  # host:443 (у голого IPv6 двоеточий больше)
        s = s.split(":", 1)[0]
    return s.strip(". ").lower()


async def resolve_dst(dst: str) -> tuple[list[str], str]:
    """Адрес назначения → список IPv4. Возвращает (адреса, текст ошибки).

    ТОЛЬКО IPv4: тайлнет v4-only, и AAAA в маршруте — мёртвый груз, который
    вдобавок создаёт впечатление, будто IPv6 в сети всё-таки есть.
    """
    host = normalize_dst(dst)
    if not host:
        return [], "пустой адрес"
    try:
        net = ipaddress.ip_network(host, strict=False)
    except ValueError:
        pass
    else:
        if net.version != 4:
            return [], "IPv6 не используется"
        return [host], ""
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, None, socket.AF_INET)
    except Exception as exc:  # noqa: BLE001 — резолв не должен ронять запрос
        return [], str(exc)[:100]
    raw = sorted({i[4][0] for i in infos})
    # Адрес меша из DNS — это не «сайт», а попытка увести маршрут внутрь тайлнета
    # (перехват трафика к соседней ноде). Отбрасываем сразу и говорим почему;
    # последним рубежом та же проверка стоит в _dst_ips.
    from app.aclgen import touches_mesh

    ips = [ip for ip in raw if not touches_mesh(ip)]
    if raw and not ips:
        return [], "адрес указывает внутрь меша — так маршрут не заводим"
    return ips, "" if ips else "нет адресов"


def _as_cidr(ip: str) -> str:
    return ip if "/" in ip else f"{ip}/32"


# Минимальная длина префикса для ПУБЛИЧНОЙ сети в направлении. Направление
# превращается в анонсируемый subnet-маршрут, а широкий публичный префикс —
# ровно тот механизм утечки, из-за которого убран полный туннель: такой маршрут
# любая нода с accept-routes забирает САМА и уводит через шлюз свой интернет.
# «0.0.0.0/0» и «0.0.0.0/1» отсекает touches_mesh (они накрывают меш), но
# «128.0.0.0/1» — пол-интернета — проходил насквозь.
MIN_PUBLIC_PREFIX = 16


def too_broad(dst: str) -> bool:
    """Слишком широкая ПУБЛИЧНАЯ сеть для направления (см. MIN_PUBLIC_PREFIX).

    Приватные диапазоны (10/8, 172.16/12, 192.168/16) не ограничиваем: офисная
    сеть за subnet-роутером — законная и частая цель, и чужой интернет она не
    уводит. Ограничение — только для публичных сетей.
    """
    try:
        net = ipaddress.ip_network(dst, strict=False)
    except ValueError:
        return False
    if net.version != 4 or net.is_private:
        return False
    return net.prefixlen < MIN_PUBLIC_PREFIX


def _dst_ips(d: dict) -> list[str]:
    """Адреса назначения направления — сохранённые (резолв домена / литеральный IP).

    Полный туннель через subnet-маршруты («весь интернет минус меш» набором широких
    префиксов) УБРАН как опасный: такие маршруты любая нода с accept-routes и правом
    в интернет забирает САМА, без спроса, — и заворачивает через ноду-выход ВЕСЬ свой
    трафик (однажды так утащило в туннель и панель). «Весь трафик сервера через
    другой» безопасно делается exit-node'ом на источнике (см. «Шлюз выхода»), а не
    subnet-маршрутами. Старые направления с full становятся инертными (ips пуст).

    Широкие публичные сети И адреса меша отбрасываем и здесь, а не только при
    создании: запись могла быть сохранена до появления проверки (или прийти в
    обход API), а цена ошибки — утечка чужого интернета через шлюз.

    Про меш отдельно. При создании проверяется СТРОКА, которую ввёл админ, а для
    домена она не адрес — `touches_mesh("myip.ru")` всегда False. Настоящий адрес
    появляется только после резолва и меняется при каждом обновлении: сменилась
    A-запись (истёк домен, угнали, подделали ответ резолверу) на 100.64.0.x — и
    нода-выход начинала бы анонсировать маршрут к tailnet-адресу СОСЕДА, а панель
    сама бы его одобрила. Это перехват трафика внутри меша, поэтому фильтр стоит
    на выдаче — общей для маршрутов агента, ACL-правил и одобрения."""
    from app.aclgen import touches_mesh

    return [
        ip
        for ip in (d.get("ips") or [])
        if not too_broad(str(ip)) and not touches_mesh(str(ip))
    ]


def routes_by_node(directions: dict) -> dict[str, list[str]]:
    """Какие маршруты обязана анонсировать каждая нода-выход.

    Это НЕ то же, что маршруты, заданные руками в модалке ноды: те живут в
    конфиге агента. Итоговое состояние агента — объединение (см. api/agent.py),
    поэтому направление и ручной маршрут не затирают друг друга.
    """
    out: dict[str, set[str]] = {}
    for d in directions.values():
        via = str(d.get("via") or "")
        if not via:
            continue
        out.setdefault(via, set()).update(_as_cidr(ip) for ip in _dst_ips(d))
    return {node_id: sorted(ips) for node_id, ips in out.items() if ips}


def sources(d: dict, device_ids: list[str], server_ids: list[str]) -> list[str]:
    """Кто ходит по этому направлению — с раскрытием групп.

    «Все устройства»/«все серверы» держим ГРУППОЙ, а не снимком списка: смысл
    настройки в том, чтобы новая нода подхватывалась сама, иначе каждое новое
    устройство пришлось бы вписывать руками — ровно та работа, ради ухода от
    которой групповой выбор и заводится.

    Нода-выход из источников ВСЕГДА исключается: правило «ходи на этот адрес
    через саму себя» смысла не имеет, а маршрут к собственному адресу через
    себя же — верный способ запутать таблицу маршрутизации на ноде.
    """
    via = str(d.get("via") or "")
    kind = str(d.get("src_kind") or "node")
    if kind == "devices":
        ids = list(device_ids)
    elif kind == "servers":
        ids = list(server_ids)
    else:
        raw = d.get("src")
        ids = [raw] if isinstance(raw, str) else list(raw or [])
    return [str(i) for i in ids if str(i) and str(i) != via]


def acl_rules(
    directions: dict,
    device_ids: list[str] | None = None,
    server_ids: list[str] | None = None,
) -> list[dict]:
    """Синтетические правила доступа. В acl_rules НЕ хранятся — добавляются при
    сборке политики, как и правила админ-нод."""
    rules: list[dict] = []
    for d in directions.values():
        for src in sources(d, device_ids or [], server_ids or []):
            for ip in _dst_ips(d):
                rules.append(
                    {
                        "src": {"kind": "node", "value": src},
                        "dst": {"kind": "cidr", "value": ip},
                        "ports": d.get("ports") or "*",
                    }
                )
    return rules


async def refresh(session: AsyncSession, *, force: bool = False) -> list[str]:
    """Перерезолвивает адреса направлений, у которых истёк срок свежести.

    Возвращает id направлений, у которых набор адресов ИЗМЕНИЛСЯ — по ним
    вызывающий должен перепушить политику и обновить состояние агентов.
    Направление с литеральным IP не резолвится вовсе.
    """
    directions = await settings_store.get_routing(session)
    if not directions:
        return []
    now = datetime.now(timezone.utc)
    changed: list[str] = []
    dirty = False
    for did, d in directions.items():
        last = _parse_ts(d.get("resolved_at"))
        if not force and last is not None and now - last < REFRESH_AFTER:
            continue
        ips, err = await resolve_dst(d.get("dst", ""))
        d["resolved_at"] = now.isoformat()
        # Резолв упал — держим ПРЕЖНИЕ адреса: временная недоступность DNS не
        # повод рвать рабочий маршрут. Ошибку показываем в UI.
        if not ips and err:
            if d.get("error") != err:
                d["error"] = err
                dirty = True
            continue
        if d.get("error"):
            d["error"] = ""
            dirty = True
        if sorted(ips) != sorted(d.get("ips") or []):
            d["ips"] = ips
            changed.append(did)
            dirty = True
    if dirty:
        # Записываем НЕ снимок, прочитанный час назад, а свежее состояние с
        # наложенными результатами резолва. Между чтением и записью мы висим в
        # getaddrinfo, и за это время админ мог удалить направление (то есть
        # отозвать доступ) или завести новое: запись снимка целиком воскресила бы
        # удалённое и стёрла созданное — причём молча, доступ вернулся бы сам.
        # Обновляем только те записи, которые ЕЩЁ существуют, и только их поля.
        fresh = await settings_store.get_routing(session)
        touched = False
        for did in {*changed, *(d for d in directions if directions[d].get("resolved_at"))}:
            src, dst = directions.get(did), fresh.get(did)
            if not src or dst is None:
                continue  # направление удалили, пока мы резолвили — не воскрешаем
            for field in ("ips", "resolved_at", "error"):
                if field in src and dst.get(field) != src[field]:
                    dst[field] = src[field]
                    touched = True
        if touched:
            await settings_store.set_routing(session, fresh)
        changed = [did for did in changed if did in fresh]
    return changed


def new_id() -> str:
    return secrets.token_urlsafe(8)


async def routing_loop(session_factory, settings) -> None:
    """Фоновый перерезолв адресов направлений.

    Отдельным циклом, а не в коллекторе метрик: это другая задача с другим
    ритмом (раз в час против раза в минуту), и её падение не должно задевать
    сбор метрик и алерты о нодах.

    Если адрес сайта сменился — обновляем маршрут ноды-выхода и правило доступа.
    Иначе связка ломалась бы молча: пользователь узнавал бы об этом, когда сайт
    перестал открываться.
    """
    from app import policy_apply
    from app.hs_client import get_client

    log = logging.getLogger("noderoost.routing")
    while True:
        try:
            async with session_factory() as session:
                changed = await refresh(session)
                if changed and settings.headscale_api_key:
                    client = get_client(settings)
                    await policy_apply.apply_policy(session, client, settings)
                    await approve_for(client, await settings_store.get_routing(session))
                    log.info("адреса направлений обновились: %s", ", ".join(changed))
        except Exception:  # noqa: BLE001 — цикл не должен падать
            log.exception("ошибка обновления направлений")
        await asyncio.sleep(REFRESH_AFTER.total_seconds())


async def approve_for(client, directions: dict) -> None:
    """Дотянуть одобрение маршрутов на нодах-выходах после смены адресов."""
    wanted = routes_by_node(directions)
    if not wanted:
        return
    nodes = {str(n.get("id", "")): n for n in await client.get_nodes()}
    for node_id, routes in wanted.items():
        node = nodes.get(node_id)
        if node is None:
            continue
        approved = set(node.get("approvedRoutes") or [])
        target = approved | set(routes)
        if target != approved:
            await client.approve_routes(node_id, sorted(target))
