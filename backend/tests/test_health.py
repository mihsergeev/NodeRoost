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
