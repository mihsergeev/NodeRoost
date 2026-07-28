import httpx
import pytest

from app import hs_client
from app.hs_client import HeadscaleClient, HeadscaleError


def _mock_asyncclient(monkeypatch, handler):
    """Подменяет httpx.AsyncClient в hs_client на клиент с MockTransport."""
    transport = httpx.MockTransport(handler)
    orig = httpx.AsyncClient
    monkeypatch.setattr(
        hs_client.httpx,
        "AsyncClient",
        lambda **kw: orig(transport=transport, **kw),
    )


async def test_ping_ok(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/apikey"
        assert request.headers["Authorization"] == "Bearer secret-key"
        return httpx.Response(200, json={"apiKeys": []})

    _mock_asyncclient(monkeypatch, handler)
    client = HeadscaleClient("http://headscale:8080", "secret-key")
    assert await client.ping() is True


async def test_get_nodes(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/node"
        return httpx.Response(200, json={"nodes": [{"id": "1", "name": "laptop"}]})

    _mock_asyncclient(monkeypatch, handler)
    client = HeadscaleClient("http://headscale:8080", "k")
    nodes = await client.get_nodes()
    assert nodes == [{"id": "1", "name": "laptop"}]


async def test_error_on_401(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    _mock_asyncclient(monkeypatch, handler)
    client = HeadscaleClient("http://headscale:8080", "bad")
    with pytest.raises(HeadscaleError):
        await client.ping()


async def test_list_preauthkeys(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/v1/preauthkey"
        return httpx.Response(200, json={"preAuthKeys": [{"id": "3", "used": True}]})

    _mock_asyncclient(monkeypatch, handler)
    client = HeadscaleClient("http://headscale:8080", "k")
    assert await client.list_preauthkeys() == [{"id": "3", "used": True}]


async def test_delete_preauthkey_passes_id_as_query(monkeypatch):
    """У DELETE-биндинга тела нет — id уходит query-параметром."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        assert request.url.path == "/api/v1/preauthkey"
        assert request.url.params["id"] == "3"
        return httpx.Response(200, json={})

    _mock_asyncclient(monkeypatch, handler)
    client = HeadscaleClient("http://headscale:8080", "k")
    assert await client.delete_preauthkey("3") is None


def test_configured_flag():
    assert HeadscaleClient("http://h:8080", "k").configured is True
    assert HeadscaleClient("http://h:8080", "").configured is False
