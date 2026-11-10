import asyncio
import json
import socket

import httpx
import pytest

from app import alerts, heartbeat, urlguard
from app.alert_http import alert_request


def _answers(*addresses):
    return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, 1, 6, "", (ip, 443))
            for ip in addresses]


async def test_alert_dns_is_pinned_and_connection_failover_never_replays_post(monkeypatch):
    resolved, sent = [], []

    async def dns(host, port, **kwargs):
        resolved.append((host, port))
        return _answers("2606:4700::1111", "93.184.216.34") if len(resolved) == 1 else _answers("127.0.0.1")

    async def wire(self, request):
        sent.append(request)
        if request.url.host == "2606:4700::1111":
            raise httpx.ConnectError("network unavailable")
        return httpx.Response(204)

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", dns)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", wire)
    errors = await alerts.send_alert({"webhook": "https://hooks.example.com:8443/signed?q=1"}, "test")

    assert errors == []
    assert resolved == [("hooks.example.com", 8443)]
    assert [request.url.host for request in sent] == ["2606:4700::1111", "93.184.216.34"]
    assert all(request.headers["Host"] == "hooks.example.com:8443" for request in sent)
    assert all(request.extensions["sni_hostname"] == "hooks.example.com" for request in sent)
    assert all(request.url.raw_path == b"/signed?q=1" for request in sent)
    assert json.loads(sent[-1].content) == {"text": "test"}

    async def failed_after_send(self, request):
        sent.append(request)
        raise httpx.ReadTimeout("response lost")

    async def dual_dns(*args, **kwargs):
        return _answers("2606:4700::1111", "93.184.216.34")

    sent.clear()
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", dual_dns)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", failed_after_send)
    with pytest.raises(httpx.ReadTimeout):
        await alert_request("POST", "https://hooks.example.com/signed", json={"text": "test"})
    assert len(sent) == 1


async def test_rebound_dns_blocks_delivery_and_telegram_healthcheck(monkeypatch):
    async def public_dns(*args, **kwargs):
        return _answers("93.184.216.34")

    async def rebound_dns(*args, **kwargs):
        return _answers("93.184.216.34", "127.0.0.1")

    async def no_wire(*args):
        pytest.fail("rebound DNS must not reach the HTTP transport")

    cfg = {
        "webhook": "https://hooks.example.com/path",
        "telegram_api": "https://mirror.example.com",
        "telegram_token": "123:synthetic",
        "telegram_chat": "42",
    }
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", public_dns)
    await urlguard.check_outbound_url(cfg["webhook"])
    await urlguard.check_outbound_url(cfg["telegram_api"])
    monkeypatch.setattr(loop, "getaddrinfo", rebound_dns)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", no_wire)
    assert len(await alerts.send_alert(cfg, "test")) == 2
    assert not await heartbeat._self_test(cfg)


async def test_alert_does_not_follow_redirect_or_copy_webhook_error_body(monkeypatch):
    async def wire(self, request):
        assert request.url.host == "93.184.216.34"
        return httpx.Response(
            307, headers={"location": "https://127.0.0.1/"},
            content=b"untrusted response containing a secret",
        )

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", wire)
    assert await alerts.send_alert({"webhook": "https://93.184.216.34/hook"}, "test") == [
        "Webhook: HTTP 307",
    ]


def test_telegram_method_stays_in_path_when_mirror_has_query():
    assert urlguard.telegram_url(
        "https://mirror.example.com/prefix/?name=one#ignore", "123:synthetic", "getMe",
    ) == "https://mirror.example.com/prefix/bot123:synthetic/getMe?name=one"
