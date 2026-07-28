from tests.conftest import ADMIN_PASSWORD


async def _login(client, password=ADMIN_PASSWORD):
    return await client.post(
        "/api/auth/login", json={"username": "admin", "password": password}
    )


async def test_login_ok(client):
    r = await _login(client)
    assert r.status_code == 200
    assert r.json()["access_token"]


async def test_login_wrong_password(client):
    r = await _login(client, "nope")
    assert r.status_code == 401


async def test_me_requires_token(client):
    r = await client.get("/api/auth/me")
    assert r.status_code == 401


async def test_me_with_token(client):
    token = (await _login(client)).json()["access_token"]
    r = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["username"] == "admin"


async def test_config_with_token(client):
    token = (await _login(client)).json()["access_token"]
    r = await client.get("/api/config", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["headscale_configured"] is False
