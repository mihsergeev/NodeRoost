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
    setup = await client.post(
        "/api/auth/2fa/setup", json={"password": ADMIN_PASSWORD}, headers=h
    )
    secret = setup.json()["secret"]

    used = code_at(secret)
    assert (await client.post(
        "/api/auth/2fa/enable",
        json={"otp": used, "password": ADMIN_PASSWORD},
        headers=h,
    )).status_code == 200
    r = await client.post(
        "/api/auth/login",
        json={"username": "admin", "password": ADMIN_PASSWORD, "otp": used},
    )
    assert r.status_code == 401          # тот же код на вход больше не годится

    # возвращаем пользователя как было: база у тестов общая
    off = await client.post(
        "/api/auth/2fa/disable",
        json={"otp": code_at(secret, 1), "password": ADMIN_PASSWORD},
        headers=h,
    )
    assert off.status_code == 200 and off.json()["enabled"] is False


async def test_break_glass_reset_unlocks_a_locked_out_admin(session):
    """Потерян и пароль, и второй фактор — вернуть панель можно только с сервера.

    Механизм есть с самого начала, но о нём нигде не было написано: запертый
    администратор о нём не узнавал.
    """
    from app.bootstrap import ensure_admin
    from app.config import Settings
    from app.models import User
    from app.security import hash_password, verify_password
    from sqlalchemy import select

    st = Settings(admin_user="admin", admin_password="FromEnvPass123",
                  admin_password_reset=True)

    user = await session.scalar(select(User).where(User.username == "admin"))
    if user is None:                      # фикстура session заводит пустую базу
        user = User(username="admin", password_hash=hash_password("forgotten"))
        session.add(user)
        await session.commit()
    user.password_hash = hash_password("forgotten")
    user.totp_enabled = True
    user.totp_secret = "ABCDEFGHIJKLMNOP"
    before = user.token_version
    await session.commit()

    class _Factory:
        def __call__(self):
            return _Ctx(session)

    class _Ctx:
        def __init__(self, s):
            self.s = s

        async def __aenter__(self):
            return self.s

        async def __aexit__(self, *a):
            return False

    await ensure_admin(_Factory(), st)

    await session.refresh(user)
    assert verify_password("FromEnvPass123", user.password_hash)
    assert user.totp_enabled is False and user.totp_secret == ""
    assert user.token_version == before + 1      # старые сессии больше не годятся


async def test_2fa_setup_needs_the_password(client):
    """Токена мало: иначе угнанная сессия привязывает свой второй фактор.

    Владелец панели после этого не войдёт ничем - ни паролем, ни кодом,
    которого у него нет, - и останется только аварийный сброс с доступом к
    хосту. Поэтому включение и выключение 2FA подтверждается паролем, как и
    смена пароля.
    """
    r = await client.post("/api/auth/login",
                          json={"username": "admin", "password": ADMIN_PASSWORD})
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}

    assert (await client.post("/api/auth/2fa/setup", json={}, headers=h)).status_code == 422
    wrong = await client.post(
        "/api/auth/2fa/setup", json={"password": "not-the-password"}, headers=h
    )
    assert wrong.status_code == 400
    assert (await client.post(
        "/api/auth/2fa/setup", json={"password": ADMIN_PASSWORD}, headers=h
    )).status_code == 200


async def test_logout_kills_the_token_server_side(client):
    """Кнопка «Выйти» завершает сессию, а не только стирает её в браузере."""
    r = await client.post("/api/auth/login",
                          json={"username": "admin", "password": ADMIN_PASSWORD})
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}

    assert (await client.get("/api/auth/me", headers=h)).status_code == 200
    assert (await client.post("/api/auth/logout", headers=h)).status_code == 204
    assert (await client.get("/api/auth/me", headers=h)).status_code == 401


async def test_long_username_still_lands_in_the_journal(client):
    """Логин длиннее колонки журнала не должен стирать след попытки входа.

    Колонка varchar(64): на Postgres запись падала целиком и откатывалась,
    то есть один длинный логин выключал журналирование брутфорса.
    """
    long_name = "a" * 64
    assert (await client.post(
        "/api/auth/login", json={"username": long_name, "password": "nope"}
    )).status_code == 401

    r = await client.post("/api/auth/login",
                          json={"username": "admin", "password": ADMIN_PASSWORD})
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    rows = (await client.get("/api/logs/audit", headers=h)).json()
    fails = [x for x in rows if x["action"] == "login_fail"]
    assert any(x["username"] == long_name for x in fails)

    # и слишком длинный логин отвергается схемой, не доходя до базы
    assert (await client.post(
        "/api/auth/login", json={"username": "a" * 65, "password": "nope"}
    )).status_code == 422
