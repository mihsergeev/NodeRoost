"""Проверка адресов, по которым бэкенд ходит САМ (вебхук алертов, зеркало Bot API).

Эти адреса задаёт администратор в UI, а запрос уходит из контейнера панели —
то есть изнутри периметра, откуда видно и headscale, и базу. Без проверки это
готовый SSRF: достаточно сохранить вебхук вида http://127.0.0.1:8080 или
http://headscale:8080, и панель сама постучится в собственную инфраструктуру,
вернув результат в текст ошибки. Схема http вдобавок отправила бы содержимое
алертов открытым текстом через произвольный хост.

Проверяем при СОХРАНЕНИИ настройки, а не при каждой отправке: так админ получает
внятную ошибку сразу, а доставка алертов не начинает зависеть от резолвера в
момент инцидента — именно тогда, когда алерт нужнее всего. Остаточный риск —
DNS rebinding (имя резолвится в публичный адрес при сохранении и во внутренний
позже); для настройки, доступной только администратору за вайтлистом, это
приемлемо.
"""

import asyncio
import ipaddress
from urllib.parse import urlparse


class UrlNotAllowed(ValueError):
    """Адрес не годится для исходящего запроса панели."""


def _is_global(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


async def check_outbound_url(raw: str) -> None:
    """Бросает UrlNotAllowed, если по этому адресу панели ходить нельзя.
    Пустая строка — это «канал выключен», её пропускаем."""
    url = (raw or "").strip()
    if not url:
        return
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise UrlNotAllowed("Адрес должен начинаться с https:// — иначе алерты уйдут открытым текстом")
    host = parsed.hostname
    if not host:
        raise UrlNotAllowed("В адресе не разобрать имя хоста")

    # литеральный IP в адресе проверяем без резолва
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not _is_global(host):
            raise UrlNotAllowed("Адреса внутренних сетей недопустимы")
        return

    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, parsed.port or 443)
    except OSError as exc:
        raise UrlNotAllowed(f"Имя «{host}» не резолвится: {exc.strerror or exc}") from exc
    if not infos:
        raise UrlNotAllowed(f"Имя «{host}» не резолвится")
    # ВСЕ адреса имени должны быть публичными: иначе имя, отдающее и публичный,
    # и внутренний адрес, проехало бы проверку по счастливой случайности
    for info in infos:
        if not _is_global(info[4][0]):
            raise UrlNotAllowed(
                f"Имя «{host}» указывает на внутренний адрес {info[4][0]}"
            )
