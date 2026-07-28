"""Пересборка и пуш ACL-политики в headscale из хранимых правил + авто-правил
админ-нод. Вызывается как побочный эффект при изменениях, влияющих на политику
(тип ноды, флаг «админ», удаление ноды), чтобы они сразу вступали в силу, и
периодически из коллектора как самоисцеление (см. reconcile_policy)."""

from sqlalchemy.ext.asyncio import AsyncSession

from app import aclgen, exitvia, routing, settings_store
from app.config import Settings
from app.hs_client import HeadscaleClient
from app.nodekind import effective_kind, node_tags

# Последняя успешно запушенная политика (HuJSON-строка). Нужна, чтобы коллектор
# перепушивал ТОЛЬКО при изменении, а не каждый цикл. Сбрасывается на рестарте
# процесса → после старта один раз сверяется с реальностью (лечит дрейф за простой).
_last_pushed: str | None = None


async def build_policy(
    session: AsyncSession,
    client: HeadscaleClient,
    settings: Settings,
    nodes: list[dict] | None = None,
    extra_tags: list[str] | None = None,
    rules_override: list[dict] | None = None,
) -> str | None:
    """Собрать HuJSON-политику из хранимых правил + синтетических правил админ-нод.
    Возвращает строку политики или None при ошибке/недоступности headscale.

    extra_tags — теги, которые надо объявить в tagOwners сверх использованных в
    правилах. Нужно при СОЗДАНИИ роли: headscale не даст навесить на ноду тег,
    которого нет в политике.

    rules_override — собрать политику для ЕЩЁ НЕ СОХРАНЁННОГО набора правил
    (предпросмотр и пуш из раздела «Доступы»). Раньше там был свой, второй
    сборщик, и он разошёлся с этим: терял exit_via-гранты и tagOwners служебных
    тегов, из-за чего простое сохранение доступов сносило весь выход через шлюзы,
    а _last_pushed при этом не обновлялся — и самоисцеление расхождения не
    замечало. Сборщик должен быть один."""
    try:
        rules = (
            rules_override
            if rules_override is not None
            else await settings_store.get_acl_rules(session)
        )
        if nodes is None:
            nodes = await client.get_nodes()
        meta = await settings_store.get_node_meta(session)
        server_ips = [
            ip
            for n in nodes
            if effective_kind(n, meta) == "server" and (ip := aclgen._ipv4(n))
        ]
        # устройства не могут быть назначением — их IP отдаём генератору,
        # чтобы он вычистил их из dst любого правила (см. aclgen.generate_policy)
        device_ips = [
            ip
            for n in nodes
            if effective_kind(n, meta) != "server" and (ip := aclgen._ipv4(n))
        ]
        admin_rules = [
            {
                "src": {"kind": "node", "value": nid},
                "dst": {"kind": "servers", "value": ""},
                "ports": "*",
            }
            for nid, e in meta.items()
            if isinstance(e, dict) and e.get("admin")
        ]
        # правила направлений («кто → куда через ноду») тоже синтетические:
        # хранится намерение, правило выводится из него (см. app/routing.py).
        # id нод нужны, чтобы раскрыть групповые источники «все устройства» и
        # «все серверы» — они хранятся группой, а не снимком списка
        device_ids = [
            str(n.get("id", "")) for n in nodes if effective_kind(n, meta) != "server"
        ]
        server_node_ids = [
            str(n.get("id", "")) for n in nodes if effective_kind(n, meta) == "server"
        ]
        route_rules = routing.acl_rules(
            await settings_store.get_routing(session), device_ids, server_node_ids
        )
        # Теги, УЖЕ висящие на нодах, объявляем всегда — иначе следующий пуш
        # выбросил бы из tagOwners роль, которая ни в одном правиле не участвует,
        # и созданная роль тихо перестала бы существовать для headscale.
        on_nodes = [t for n in nodes for t in node_tags(n)]
        # выход в интернет через разрешённые шлюзы (via). id → IP для устройств,
        # плюс служебные теги шлюзов надо объявить, даже если ни одно устройство
        # их пока не использует (иначе тег «повиснет» и via сломается на следующем
        # устройстве).
        ip_by_id = {str(n.get("id", "")): ip for n in nodes if (ip := aclgen._ipv4(n))}
        exit_via = exitvia.exit_via_grants(meta, ip_by_id)
        gw_tags = [exitvia.gateway_tag(i) for i in exitvia.gateways(meta)]
        return aclgen.generate_policy(
            rules + admin_rules + route_rules,
            nodes,
            settings.default_user,
            server_ips,
            device_ips,
            on_nodes + gw_tags + list(extra_tags or []),
            exit_via,
        )
    except Exception:  # noqa: BLE001 — сборка политики best-effort
        return None


async def declare_tags(
    session: AsyncSession,
    client: HeadscaleClient,
    settings: Settings,
    tags: list[str],
) -> None:
    """Объявить теги в политике, чтобы headscale разрешил навесить их на ноду.

    headscale отвергает `set_node_tags` с тегом, которого нет в tagOwners, — а
    туда он попадает, только когда использован в правиле. Новую роль иначе не
    завести вовсе: замкнутый круг.

    Собираем ПОЛНУЮ политику (со всей синтетикой админ-нод и направлений) плюс
    новые теги: если бы мы отправили сюда только хранимые правила, доступы на
    время пропали бы. Best-effort — при неудаче headscale откажет на следующем
    шаге, и ошибка дойдёт до пользователя.
    """
    generated = await build_policy(session, client, settings, extra_tags=tags)
    if generated is None:
        return
    try:
        await client.set_policy(generated)
    except Exception:  # noqa: BLE001 — не роняем назначение тега
        pass


async def push_policy(
    session: AsyncSession, client: HeadscaleClient, settings: Settings
) -> str:
    """Как apply_policy, но НЕ глотает отказ headscale: возвращает его текст
    (пустая строка = успех).

    Нужно там, где вызывающий сохранил что-то новое и может это откатить.
    Молча проглоченный отказ страшнее, чем кажется: политика собирается из ВСЕГО
    хранимого состояния, поэтому одна негодная запись валит не только свой пуш,
    а все последующие — ACL замирает на последней удачной версии, а панель при
    этом продолжает показывать успех.
    """
    global _last_pushed
    generated = await build_policy(session, client, settings)
    if generated is None:
        # Сборка упала (headscale недоступен, битая запись в хранилище) — это НЕ
        # успех. Раньше возвращалась пустая строка, и вызывающий считал, что ACL
        # обновлён: отзыв доступа фиксировался в хранилище, а в силе оставались
        # прежние, более широкие правила. Отказ должен быть виден, чтобы вызывающий
        # откатился.
        return "не удалось собрать политику (headscale недоступен?) — изменение не применено"
    try:
        await client.set_policy(generated)
        _last_pushed = generated
        return ""
    except Exception as exc:  # noqa: BLE001 — текст отдаём вызывающему
        return str(exc)


async def apply_policy(
    session: AsyncSession, client: HeadscaleClient, settings: Settings
) -> None:
    """Best-effort: не роняет вызвавшую операцию, если headscale недоступен/отверг."""
    await push_policy(session, client, settings)


def _same_policy(a: str, b: str) -> bool:
    """Одна ли это политика по СМЫСЛУ. Сравнивать строки нельзя: headscale хранит
    то, что мы прислали, но пробелы/порядок ключей могут отличаться после любой
    правки извне — а лишний пуш каждую минуту нам не нужен."""
    import json as _json

    try:
        return _json.loads(a) == _json.loads(b)
    except (ValueError, TypeError):
        return a.strip() == b.strip()


async def reconcile_policy(
    session: AsyncSession,
    client: HeadscaleClient,
    settings: Settings,
    nodes: list[dict] | None = None,
) -> bool:
    """Самоисцеление ACL: сверяет политику, которая ДЕЙСТВУЕТ в headscale, с той,
    что следует из текущего состояния панели, и перепушивает при расхождении. Так
    литеральный IP удалённой (любым путём — панель/CLI/истечение/сбой) ноды не
    зависает в скомпилированном ACL. Возвращает True, если запушили.

    Сверяемся с ЖИВОЙ политикой, а не с кэшем «что мы пушили последний раз». Кэш
    знает только про наши собственные пуши, поэтому политика, изменённая в обход
    панели (headscale CLI, чужой API-ключ, оборванный пуш), не чинилась никогда:
    сгенерированное совпадало с кэшем, и самоисцеление считало, что всё в порядке.
    А расхождение как раз и опасно тем, что живая политика может оказаться ШИРЕ.
    """
    global _last_pushed
    generated = await build_policy(session, client, settings, nodes)
    if generated is None:
        return False
    try:
        live = (await client.get_policy()).get("policy", "")
    except Exception:  # noqa: BLE001 — политики может не быть вовсе (её и запушим)
        live = ""
    if _same_policy(generated, live):
        _last_pushed = generated
        return False
    try:
        await client.set_policy(generated)
        _last_pushed = generated
        return True
    except Exception:  # noqa: BLE001 — best-effort
        return False
