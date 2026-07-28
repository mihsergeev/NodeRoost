from app import enroll
from app.config import Settings
from tests.conftest import ADMIN_PASSWORD

_S = Settings(headscale_server_url="https://hs.test", tailscale_version="1.98.8")


def test_linux_script():
    s = enroll.build_script("linux", _S, "authkey-abc", "web-1")
    assert "1.98.8" in s
    assert "https://hs.test" in s
    assert "authkey-abc" in s
    assert "web-1" in s
    assert "pkgs.tailscale.com/stable/tailscale_" in s
    assert "tailscale up --login-server=" in s
    # без --accept-routes источник молча игнорирует направления/полный туннель
    assert "--accept-routes" in s


def test_windows_script():
    s = enroll.build_script("windows", _S, "authkey-xyz", "win-1")
    assert "1.98.8" in s
    assert "tailscale-setup-" in s
    assert "PROCESSOR_ARCHITECTURE" in s
    assert "authkey-xyz" in s and "win-1" in s
    assert "msiexec" in s


async def _login(client):
    r = await client.post(
        "/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD}
    )
    return r.json()["access_token"]


async def test_enroll_requires_auth(client):
    r = await client.post("/api/enroll", json={"name": "x", "os": "linux"})
    assert r.status_code == 401


async def test_enroll_503_without_key(client):
    token = await _login(client)
    r = await client.post(
        "/api/enroll",
        json={"name": "web-1", "os": "linux"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 503
