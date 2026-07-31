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
    assert "tailscale up --reset --login-server=" in s
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


def test_scripts_reset_previous_tailscale_settings():
    """Без --reset `tailscale up` отказывается менять настройки на машине, где
    уже был задан хоть один неявный флаг (например --exit-node-allow-lan-access
    после выхода через шлюз): «requires mentioning all non-default flags».
    Подключение и переподключение молча падали."""
    from app.config import Settings
    from app import enroll

    st = Settings(headscale_server_url="https://hs.example")
    for os_name in ("linux", "windows"):
        script = enroll.build_script(os_name, st, "hskey-auth-x", "node-1")
        assert "--reset" in script, os_name


class _ReuseClient:
    """headscale, который узнал машину и отдал её СТАРУЮ запись."""

    def __init__(self):
        self.renamed: tuple[str, str] | None = None

    async def get_nodes(self):
        return [
            {"id": "4", "name": "new-net", "givenName": "laptop",
             "ipAddresses": ["100.64.0.4"], "preAuthKey": {"id": "5"}},
        ]

    async def rename_node(self, node_id, new_name):
        self.renamed = (node_id, new_name)
        return {"node": {"id": node_id, "name": "new-net", "givenName": new_name,
                         "ipAddresses": ["100.64.0.4"], "preAuthKey": {"id": "5"}}}


async def _status(client, monkeypatch, hostname):
    from app.api import enroll as api_enroll

    hs = _ReuseClient()
    monkeypatch.setattr(api_enroll, "get_client", lambda _s: hs)
    monkeypatch.setattr(api_enroll, "require_hs", lambda _s: None)
    r = await client.post("/api/auth/login",
                          json={"username": "admin", "password": ADMIN_PASSWORD})
    tok = r.json()["access_token"]
    resp = await client.get(f"/api/enroll/status?key_id=5&hostname={hostname}",
                            headers={"Authorization": f"Bearer {tok}"})
    return resp.json(), hs


async def test_enroll_renames_reused_record(client, monkeypatch):
    """Машина уже была в сети: запись переиспользована — переименовать и сказать."""
    body, hs = await _status(client, monkeypatch, "new-net")
    assert body["connected"] is True
    assert body["reused_from"] == "laptop"       # чью запись заняли
    assert hs.renamed == ("4", "new-net")        # имя стало запрошенным
    assert body["node"]["name"] == "new-net"


async def test_enroll_quiet_when_name_matches(client, monkeypatch):
    body, hs = await _status(client, monkeypatch, "laptop")
    assert body["connected"] is True and body["reused_from"] is None
    assert hs.renamed is None                    # переименовывать нечего


def test_scripts_report_the_name_the_control_server_gave():
    """Скрипт должен докладывать то, что есть, а не то, что просили.

    На машине, уже подключённой к сети, `tailscale up` возвращает 0 с любым
    ключом — даже просроченным, — и скрипт бодро писал «нода подключена», хотя
    в панели не появлялось ничего.
    """
    for os_name in ("linux", "macos"):
        s = enroll.build_script(os_name, _S, "key", "web-1")
        assert '"DNSName"' in s, os_name          # имя от control-сервера
        assert '"HostName"' not in s              # не то, как машина назвала себя
        assert "уже была в этой сети" in s        # честный отчёт о переиспользовании
        assert "не подтвердилось" in s            # и о неподтверждённом подключении
