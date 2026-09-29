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
    for os_name in ("linux", "macos", "windows"):
        s = enroll.build_script(os_name, _S, "key", "web-1")
        assert 'DNSName' in s, os_name            # имя берём у control-сервера
        assert "уже была в этой сети" in s        # честный отчёт о переиспользовании
        assert "не подтвердилось" in s            # и о неподтверждённом подключении


def test_scripts_can_move_a_machine_from_another_control_server():
    """Переезд с другой панели: Tailscale не меняет control-сервер без force-reauth.

    Скрипт падал с его английской строкой «can't change --login-server without
    --force-reauth», не сказав ни что случилось, ни что делать.
    """
    for os_name in ("linux", "macos", "windows"):
        s = enroll.build_script(os_name, _S, "key", "web-1")
        assert "force-reauth" in s, os_name
        assert "другому control-серверу" in s, os_name


def test_join_script_installs_the_root():
    """Скрипт подключения ставит корень сам: на ноутбуке агента не будет никогда,
    а «сразу и без проблем» — это чтобы имя открылось с первой минуты."""
    from app import enroll as e

    pem = "-----BEGIN CERTIFICATE-----\nZm9v\n-----END CERTIFICATE-----\n"
    lin = e.build_script("linux", Settings(), "KEY", "n1", ca_pem=pem)
    assert "update-ca-certificates" in lin and pem in lin
    win = e.build_script("windows", Settings(), "KEY", "n1", ca_pem=pem)
    assert r"Cert:\LocalMachine\Root" in win and pem in win
    mac = e.build_script("macos", Settings(), "KEY", "n1", ca_pem=pem)
    assert "add-trusted-cert" in mac and pem in mac
    # своей CA нет — в скрипте не должно остаться ни блока, ни следа плейсхолдера
    plain = e.build_script("linux", Settings(), "KEY", "n1")
    assert "noderoost-ca.crt" not in plain and "@@" not in plain


def test_windows_script_fails_loudly():
    """Скрипт вставляют в консоль целиком, а PowerShell выполняет вставленное
    построчно: без общего блока падение установщика не останавливало остальное, и
    человек получал каскад вторичных ошибок («tailscale.exe не распознано»,
    «подключение не подтвердилось»), из которых настоящая причина не видна.
    Так и случилось на живой машине.
    """
    from app import enroll as e

    s = e.build_script("windows", _S, "KEY", "ms-noute")
    assert s.count("\n& {\n") == 1 and s.rstrip().endswith("}")  # один блок
    # права проверяются ДО установки: без них msiexec /quiet молча не ставит ничего
    assert "IsInRole(" in s and "ОТ АДМИНИСТРАТОРА" in s
    # код возврата установщика больше не игнорируется, и есть подробный лог
    assert "-PassThru" in s and "$p.ExitCode" in s and "/l*v" in s
    assert "1603" in s and "1618" in s  # частые коды объясняются словами
    # путь к клиенту ищется, а не угадывается (32-битный PowerShell даёт x86)
    assert "ProgramFiles(x86)" in s
    # скачанное проверяется на то, что это вообще установщик
    assert "d0cf11e0a1b11ae1" in s
    # ошибки прерывают блок, а не печатаются в никуда
    assert "Write-Error" not in s


def test_old_roots_are_replaced_not_stacked():
    """Корень, выпущенный заново, старый не отменяет: без уборки в хранилище
    копились бы мёртвые «NodeRoost internal CA», которым машина всё ещё верит."""
    from app import enroll as e

    pem = "-----BEGIN CERTIFICATE-----\nZm9v\n-----END CERTIFICATE-----\n"
    win = e.build_script("windows", _S, "KEY", "n1", ca_pem=pem)
    assert "NodeRoost internal CA" in win and "Remove-Item" in win
    mac = e.build_script("macos", _S, "KEY", "n1", ca_pem=pem)
    assert "delete-certificate" in mac


async def test_join_link_serves_the_script_until_the_key_expires(client):
    """Скрипт по ссылке — чтобы подключение было ОДНОЙ командой: вставленный в
    консоль текст выполняется построчно, и падение в середине не останавливает
    остальное. Ссылка живёт столько же, сколько ключ внутри неё: она ровно
    настолько же секретна, и переживать его ей незачем.
    """
    from datetime import datetime, timedelta, timezone

    from app import settings_store

    app = client._transport.app
    later = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    async with app.state.session_factory() as s:
        await settings_store.save_join_script(s, "tok1", "#!/bin/sh\necho живой", "linux", later)
        await settings_store.save_join_script(s, "tok2", "#!/bin/sh\necho старый", "linux", past)

    r = await client.get("/join/tok1")
    assert r.status_code == 200 and "живой" in r.text
    assert r.headers["content-type"].startswith("text/plain")
    assert (await client.get("/join/tok2")).status_code == 404  # протух вместе с ключом
    assert (await client.get("/join/нет-такого")).status_code == 404


def test_server_joins_without_mesh_dns():
    """Серверу меш-DNS по умолчанию не нужен, и это не косметика.

    При `override_local_dns` клиент с accept-dns забирает у ноды ВЕСЬ DNS, а не
    только зону меша: на Linux это `~.` на tailscale0. Сервер со своим резолвером
    (корпоративный split-horizon, DNS облака) тихо перестаёт видеть внутренние
    имена — сам он при этом жив, поэтому замечают такое не по симптому, а много
    позже и случайно.
    """
    from app import enroll as e

    lin = e.build_script("linux", _S, "KEY", "srv-1")
    assert "--accept-dns=false" in lin
    # личной машине имена меша как раз и нужны — ради них их заводят
    for os_name in ("windows", "macos"):
        assert "--accept-dns=false" not in e.build_script(os_name, _S, "KEY", "n1")
    # но выбор всегда за администратором, в обе стороны
    assert "--accept-dns=false" not in e.build_script("linux", _S, "KEY", "n1", mesh_dns=True)
    assert "--accept-dns=false" in e.build_script("windows", _S, "KEY", "n1", mesh_dns=False)


async def test_reconnect_keeps_the_dns_choice(client, monkeypatch):
    """Скрипт зовёт `tailscale up --reset`, то есть возвращает клиента к
    умолчаниям. Значит выставленный на ноде `--accept-dns=false` переподключение
    сотрёт, и сервер снова молча отдаст весь DNS мешу — если панель не передаст
    флаг заново."""
    from app import settings_store
    from app.api import nodes as api_nodes

    class _HS:
        async def get_node(self, node_id):
            return {"id": node_id, "givenName": "srv-1"}

        async def get_nodes(self):
            return [{"id": "7", "givenName": "srv-1"}]

        async def ensure_user(self, name):
            return {"id": "1", "name": name}

        async def create_preauthkey(self, *a, **k):
            return {"id": "5", "key": "hskey-auth-test"}

        async def delete_node(self, node_id):
            return {}

    monkeypatch.setattr(api_nodes, "get_client", lambda _s: _HS())
    monkeypatch.setattr(api_nodes, "require_hs", lambda _s: None)

    async def _no_policy(*a, **k):
        return None

    monkeypatch.setattr(api_nodes, "apply_policy", _no_policy)

    app = client._transport.app
    async with app.state.session_factory() as s:
        await settings_store.set_node_meta(s, "7", kind="server", mesh_dns=False)

    r = await client.post("/api/auth/login",
                          json={"username": "admin", "password": ADMIN_PASSWORD})
    tok = r.json()["access_token"]
    resp = await client.post("/api/nodes/7/reconnect", json={"os": "linux"},
                             headers={"Authorization": f"Bearer {tok}"})
    assert resp.status_code == 200
    assert "--accept-dns=false" in resp.json()["script"]

    # решение переезжает на новую запись ноды: она вернётся с другим id
    async with app.state.session_factory() as s:
        moved = await settings_store.claim_pending_meta(s, [{"id": "42", "givenName": "srv-1"}])
        assert moved == 1
        meta = await settings_store.get_node_meta(s)
    assert meta["42"]["mesh_dns"] is False


async def test_pending_note_survives_a_meta_written_first(session):
    """Решение про DNS откладывается по имени (ноды ещё нет), а тип панель может
    записать раньше — сливаем, иначе решение потерялось бы."""
    from app import settings_store

    await settings_store.stash_node_meta(session, "srv-2", {"mesh_dns": False})
    await settings_store.set_node_meta(session, "9", kind="server")
    await settings_store.claim_pending_meta(session, [{"id": "9", "givenName": "srv-2"}])
    meta = await settings_store.get_node_meta(session)
    assert meta["9"]["mesh_dns"] is False
    assert meta["9"]["kind"] == "server"  # выставленное руками не затёрто
