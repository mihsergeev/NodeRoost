async def test_health_ok(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"
    # API-ключ headscale не задан в тестах → unconfigured (health не падает)
    assert body["headscale"] == "unconfigured"
    assert body["version"]


async def test_config_requires_auth(client):
    r = await client.get("/api/config")
    assert r.status_code == 401


async def test_health_separates_a_rejected_key_from_a_dead_headscale(client, monkeypatch):
    """«Не пускает» и «не отвечает» чинятся по-разному.

    С отозванным ключом headscale жив и отвечает 401 — нужен новый ключ, а не
    перезапуск control-сервера. Раньше оба случая выглядели как «down».
    """
    from app.api import health as api_health
    from app.config import Settings, get_settings

    st = Settings(headscale_api_key="hskey-test", headscale_url="http://hs:8080")
    monkeypatch.setattr(api_health, "get_settings", lambda: st)

    class _HS:
        def __init__(self, msg):
            self.msg = msg

        async def ping(self):
            raise RuntimeError(self.msg)

    # Проба кэшируется на несколько секунд (ручка публичная), поэтому между
    # проверками состояние кэша сбрасываем - в жизни эти состояния разделены
    # минутами, а не миллисекундами.
    monkeypatch.setattr(api_health, "_hs_cached", (0.0, ""))
    monkeypatch.setattr(api_health, "HeadscaleClient",
                        lambda *a, **k: _HS("headscale 401: Unauthorized"))
    assert (await client.get("/api/health")).json()["headscale"] == "unauthorized"

    monkeypatch.setattr(api_health, "_hs_cached", (0.0, ""))
    monkeypatch.setattr(api_health, "HeadscaleClient",
                        lambda *a, **k: _HS("headscale недоступен: connect refused"))
    assert (await client.get("/api/health")).json()["headscale"] == "down"


async def test_health_caches_the_control_server_probe(client, monkeypatch):
    """Публичную ручку нельзя превращать в усилитель запросов к control-серверу."""
    from app.api import health as api_health
    from app.config import Settings

    calls = []
    st = Settings(headscale_api_key="hskey-test", headscale_url="http://hs:8080")
    monkeypatch.setattr(api_health, "get_settings", lambda: st)
    monkeypatch.setattr(api_health, "_hs_cached", (0.0, ""))

    class _HS:
        async def ping(self):
            calls.append(1)

    monkeypatch.setattr(api_health, "HeadscaleClient", lambda *a, **k: _HS())
    for _ in range(5):
        assert (await client.get("/api/health")).json()["headscale"] == "ok"
    assert len(calls) == 1
