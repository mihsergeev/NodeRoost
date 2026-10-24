"""Настоящий IP клиента за обратным прокси.

Панель работает за цепочкой caddy → nginx → backend, поэтому у бэкенда
``request.client.host`` — это адрес соседнего контейнера в Docker-сети
(172.20.0.x), а не адрес пользователя. Из-за этого в журнал/алерты/rate-limit
попадал внутренний IP прокси, а не реального клиента.

Берём адрес из ``X-Forwarded-For`` по ЧИСЛУ своих прокси (``trusted_proxy_hops``),
отсчитывая справа: каждый наш прокси дописывает в хвост адрес того, от кого
принял запрос, поэтому при штатных двух хопах нужный адрес — предпоследний.
Всё, что левее, прислал сам клиент.

Раньше здесь искался первый публичный адрес справа. Это держалось только пока
клиент публичный: из локальной сети (или через прокси, который заголовок не
дописывает, а пробрасывает) реальный адрес считался хопом, скан проезжал его и
брал то, что прислал клиент. Меняя заголовок на каждом запросе, такой клиент
получал чистый ключ лимитера на каждую попытку входа — то есть перебор пароля
без ограничений, и чужой адрес в журнале.
"""

import ipaddress

from fastapi import Request

from app.config import get_settings


def _valid(ip: str) -> bool:
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        return False
    return True


def client_ip(request: Request, hops: int | None = None) -> str:
    """IP реального клиента с учётом обратного прокси."""
    if hops is None:
        hops = get_settings().trusted_proxy_hops
    peer = request.client.host if getattr(request, "client", None) else "unknown"
    if hops <= 0:
        return peer

    headers = getattr(request, "headers", None)
    raw = headers.get("x-forwarded-for", "") if headers is not None else ""
    chain = [p.strip() for p in raw.split(",") if p.strip()]
    if not chain:
        # заголовка нет: прямой запрос к бэкенду (локальный запуск, healthcheck)
        return peer

    # Хоп номер hops с конца — адрес, который дописал наш самый внешний прокси.
    # Цепочка короче ожидаемой (запрос пришёл в обход части прокси) — берём
    # самый левый из того, что дописали мы сами, а не то, что прислал клиент.
    idx = max(0, len(chain) - hops)
    ip = chain[idx]
    return ip if _valid(ip) else peer
