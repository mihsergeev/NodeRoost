"""Настоящий IP клиента за обратным прокси.

Панель работает за цепочкой caddy → nginx → backend, поэтому у бэкенда
``request.client.host`` — это адрес соседнего контейнера в Docker-сети
(172.20.0.x), а не адрес пользователя. Из-за этого в журнал/алерты/rate-limit
попадал внутренний IP прокси, а не реального клиента.

Реальный адрес берём из ``X-Forwarded-For``: идём справа налево и возвращаем
первый ПУБЛИЧНЫЙ адрес. Внутренние прокси (частные/loopback-сети) пропускаем —
поэтому подделать заголовок нельзя: свой публичный адрес caddy всегда дописывает
правее любого спуфа, присланного клиентом в заголовке.
"""

import ipaddress

from fastapi import Request

# Тайлнет (100.64.0.0/10, shared address space). Для нас это НЕ прокси-хоп, а
# нормальный адрес клиента: панель может открываться по мешу, и такие клиенты
# должны различаться — иначе все они делят один ключ лимитера и могут залочить
# друг друга на входе.
#
# Проверка явная, потому что классификация зависит от версии Python: до 3.13
# этот диапазон считался is_private (и тогда адреса из меша действительно
# схлопывались), с 3.13 — уже нет. Не полагаемся на версию.
#
# Подделать адрес это не позволяет: спуф из заголовка всё равно оказывается
# ЛЕВЕЕ реального адреса, который дописал прокси, а мы идём справа налево.
_TAILNET = ipaddress.ip_network("100.64.0.0/10")


def _is_internal(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True  # мусор в заголовке — не доверяем
    if addr.version == 4 and addr in _TAILNET:
        return False
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_unspecified
    )


def client_ip(request: Request) -> str:
    """IP реального клиента с учётом обратного прокси."""
    xff = getattr(request, "headers", None)
    raw = xff.get("x-forwarded-for", "") if xff is not None else ""
    for part in reversed(raw.split(",")):
        ip = part.strip()
        if ip and not _is_internal(ip):
            return ip
    # заголовка нет или вся цепочка внутренняя (локальный запуск/тесты) —
    # берём прямого соседа
    return request.client.host if request.client else "unknown"
