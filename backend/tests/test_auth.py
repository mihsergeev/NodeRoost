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


async def test_totp_code_used_to_enable_cannot_log_in(client):
    """Код одноразовый везде: включение 2FA тоже его тратит.

    Иначе код, введённый при включении, ещё полминуты открывал вход — а его
    видно и через плечо, и на скриншоте вместе с QR.
    """
    import base64
    import hashlib
    import hmac
    import struct
    import time

    def code_at(secret: str, step_offset: int = 0) -> str:
        key = base64.b32decode(secret + "=" * (-len(secret) % 8))
        digest = hmac.new(
            key, struct.pack(">Q", int(time.time()) // 30 + step_offset), hashlib.sha1
        ).digest()
        off = digest[19] & 15
        return "%06d" % (
            (struct.unpack(">I", digest[off:off + 4])[0] & 0x7FFFFFFF) % 10**6
        )

    r = await client.post("/api/auth/login",
                          json={"username": "admin", "password": ADMIN_PASSWORD})
    tok = r.json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    secret = (await client.post("/api/auth/2fa/setup", json={}, headers=h)).json()["secret"]

    used = code_at(secret)
    assert (await client.post("/api/auth/2fa/enable", json={"otp": used},
                              headers=h)).status_code == 200
    r = await client.post(
        "/api/auth/login",
        json={"username": "admin", "password": ADMIN_PASSWORD, "otp": used},
    )
    assert r.status_code == 401          # тот же код на вход больше не годится

    # возвращаем пользователя как было: база у тестов общая
    off = await client.post("/api/auth/2fa/disable",
                            json={"otp": code_at(secret, 1)}, headers=h)
    assert off.status_code == 200 and off.json()["enabled"] is False
