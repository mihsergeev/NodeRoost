"""Ограничения для адресов оповещений панели и хостовых помощников."""

import asyncio
import ipaddress
import socket
from urllib.parse import SplitResult, urlsplit

# IPv6 transition prefixes can carry IPv4 addresses of otherwise blocked hosts.
_IPV6_TRANSITION = tuple(ipaddress.ip_network(prefix) for prefix in (
    "64:ff9b::/96", "64:ff9b:1::/48", "2001::/32", "2002::/16",
))


class UrlNotAllowed(ValueError):
    """Адрес не годится для исходящего запроса панели."""


def is_public_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.scope_id or addr.is_site_local or any(addr in net for net in _IPV6_TRANSITION):
            return False
        if addr.ipv4_mapped is not None:
            addr = addr.ipv4_mapped
    return addr.is_global and not (addr.is_reserved or addr.is_multicast)


def parse_outbound_url(raw: str) -> tuple[SplitResult, str, int]:
    """Разбирает HTTPS URL и возвращает каноническое имя для DNS и TLS."""
    url = raw.strip()
    if any(ord(char) <= 32 or ord(char) == 127 for char in url):
        raise UrlNotAllowed("Некорректный адрес")
    try:
        parsed = urlsplit(url)
        port = 443 if parsed.port is None else parsed.port
        host = (parsed.hostname or "").encode("idna").decode("ascii").lower()
    except (ValueError, UnicodeError):
        raise UrlNotAllowed("Некорректный адрес") from None
    if parsed.scheme != "https":
        raise UrlNotAllowed("Адрес должен начинаться с https:// — иначе алерты уйдут открытым текстом")
    if not host or not port or "%" in host or "\\" in parsed.netloc:
        raise UrlNotAllowed("Некорректный адрес")
    return parsed, host, port


def telegram_url(base: str | None, token: str, method: str) -> str:
    raw = base or "https://api.telegram.org"
    parsed, _, _ = parse_outbound_url(raw)
    if not token or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:_-" for char in token):
        raise UrlNotAllowed("Некорректный токен Telegram")
    path = f"{parsed.path.rstrip('/')}/bot{token}/{method}"
    return parsed._replace(path=path, fragment="").geturl()


def literal_address(host: str) -> str | None:
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return None
    if not is_public_ip(str(addr)):
        raise UrlNotAllowed("Адреса внутренних сетей недопустимы")
    return str(addr)


def checked_addresses(infos: list) -> tuple[str, ...]:
    addresses = tuple(dict.fromkeys(info[4][0] for info in infos))
    if not addresses:
        raise UrlNotAllowed("Имя не резолвится")
    if any(not is_public_ip(address) for address in addresses):
        raise UrlNotAllowed("Имя указывает на внутренний адрес")
    return addresses


async def resolve_outbound(host: str, port: int) -> tuple[str, ...]:
    if literal := literal_address(host):
        return (literal,)
    try:
        async with asyncio.timeout(5):
            infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, TimeoutError):
        raise UrlNotAllowed("Имя не резолвится") from None
    return checked_addresses(infos)


async def check_outbound_url(raw: str) -> None:
    """Пустой адрес отключает канал, остальные проверяются и перед отправкой."""
    if (raw or "").strip():
        _, host, port = parse_outbound_url(raw)
        await resolve_outbound(host, port)
