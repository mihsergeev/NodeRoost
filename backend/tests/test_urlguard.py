"""Защита от SSRF в адресах, по которым панель ходит сама."""

import pytest

from app import urlguard


async def test_empty_is_allowed():
    """Пустая строка — это «канал выключен», а не плохой адрес."""
    await urlguard.check_outbound_url("")


async def test_http_rejected():
    with pytest.raises(urlguard.UrlNotAllowed, match="https"):
        await urlguard.check_outbound_url("http://hooks.example.com/x")


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/x",  # петля
        "https://10.0.0.5/x",  # приватная сеть
        "https://172.18.0.3:8080/api/v1",  # соседний контейнер (headscale)
        "https://169.254.169.254/latest/meta-data/",  # облачные метаданные
        "https://[::1]/x",
    ],
)
async def test_internal_literals_rejected(url):
    with pytest.raises(urlguard.UrlNotAllowed):
        await urlguard.check_outbound_url(url)


async def test_public_literal_allowed():
    # не RFC 5737: Python считает документационные диапазоны приватными
    await urlguard.check_outbound_url("https://93.184.216.34/hook")


async def test_name_resolving_inside_is_rejected(monkeypatch):
    """Имя может указывать внутрь — одной проверки схемы мало."""

    async def fake_getaddrinfo(host, port, *a, **k):
        return [(2, 1, 6, "", ("127.0.0.1", port))]

    import asyncio

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(urlguard.UrlNotAllowed, match="внутренний адрес"):
        await urlguard.check_outbound_url("https://evil.example.com/hook")


async def test_name_with_mixed_answers_is_rejected(monkeypatch):
    """Если имя отдаёт и публичный, и внутренний адрес — пропускать нельзя:
    иначе проверка проходила бы по случайности порядка ответов."""

    async def fake_getaddrinfo(host, port, *a, **k):
        return [
            (2, 1, 6, "", ("93.184.216.34", port)),
            (2, 1, 6, "", ("10.1.2.3", port)),
        ]

    import asyncio

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(urlguard.UrlNotAllowed):
        await urlguard.check_outbound_url("https://mixed.example.com/hook")


async def test_unresolvable_name_is_rejected(monkeypatch):
    async def boom(host, port, *a, **k):
        raise OSError(-2, "Name or service not known")

    import asyncio

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", boom)
    with pytest.raises(urlguard.UrlNotAllowed, match="резолвится"):
        await urlguard.check_outbound_url("https://nope.example.invalid/hook")
