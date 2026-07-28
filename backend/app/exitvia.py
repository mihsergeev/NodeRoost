"""Выход в интернет через РАЗРЕШЁННЫЕ exit-ноды (per-device), на механизме `via`.

Задача: «юзеру A можно выходить через ноды 1 и 2, юзеру B только через 1, мне —
через любую», причём юзер выбирает exit-ноду сам в трее. Обычный
`autogroup:internet` этого не даёт — он показывает клиенту ВСЕ одобренные exit.

Решение — поле `via` в grants headscale 0.29: `src → autogroup:internet via
[тег]` пускает устройство в интернет ТОЛЬКО через ноды с этим тегом, и headscale
заодно скрывает остальные exit-ноды из трея клиента. Проверено вживую.

Чтобы via сматчил конкретную ноду, у неё должен быть тег. Вешаем служебный
`tag:xgw-<id>` на сервер-шлюз (см. gateway_tag). Он уникален на ноду, поэтому
via по нему — контроль per-конкретная-нода, а не по роли.
"""

from __future__ import annotations

GATEWAY_TAG_PREFIX = "tag:xgw-"


def gateway_tag(node_id: str) -> str:
    """Служебный тег сервера-шлюза. Уникален на ноду."""
    return f"{GATEWAY_TAG_PREFIX}{node_id}"


def is_service_tag(tag: str) -> bool:
    """Служебный ли это тег (его не показываем как роль в UI)."""
    return tag.startswith(GATEWAY_TAG_PREFIX)


def gateways(meta: dict) -> list[str]:
    """id серверов, помеченных шлюзом выхода."""
    return [
        nid
        for nid, e in (meta or {}).items()
        if isinstance(e, dict) and e.get("exit_gateway")
    ]


def exit_via_grants(meta: dict, ip_by_id: dict[str, str]) -> list[dict]:
    """Правила выхода для генератора: [{src: ip_устройства, via: [теги шлюзов]}].

    Берём устройства с непустым exit_via, оставляем только те шлюзы, что реально
    помечены gateway (иначе via ссылался бы на несуществующий тег и headscale
    отверг бы политику), и переводим id устройства в его IP.
    """
    gw = set(gateways(meta))
    out: list[dict] = []
    for nid, e in (meta or {}).items():
        if not isinstance(e, dict):
            continue
        via_ids = [str(i) for i in (e.get("exit_via") or []) if str(i) in gw]
        src_ip = ip_by_id.get(str(nid))
        if src_ip and via_ids:
            out.append({"src": src_ip, "via": [gateway_tag(i) for i in via_ids]})
    return out
