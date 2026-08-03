import json

import pytest
import yaml

from app import dnsrecords, settings_store
from app.api import settings as api_settings
from app.config import Settings, get_settings
from tests.conftest import ADMIN_PASSWORD

NODE = {"id": "7", "givenName": "acontrol", "ipAddresses": ["100.100.0.1"]}


def test_entries_follow_the_current_address_of_the_node():
    """Адрес не запоминается: нода после переподключения — другая запись у
    headscale и, вообще говоря, другой адрес."""
    stored = [{"name": "acontrol.example.com", "node_id": "7"}]
    assert dnsrecords.entries_for(stored, [NODE]) == [
        {"name": "acontrol.example.com", "type": "A", "value": "100.100.0.1"}
    ]
    moved = {**NODE, "ipAddresses": ["100.100.0.9"]}
    assert dnsrecords.entries_for(stored, [moved])[0]["value"] == "100.100.0.9"


def test_a_switched_off_name_is_not_handed_out():
    """Снятая галочка оставляет запись в списке, но нодам её не раздаёт: внутри
    сети имя снова ведёт туда же, куда снаружи."""
    stored = [
        {"name": "acontrol.example.com", "node_id": "7", "enabled": False},
        {"name": "nas.example.com", "ip": "192.168.1.10", "enabled": True},
    ]
    assert [e["name"] for e in dnsrecords.entries_for(stored, [NODE])] == [
        "nas.example.com"
    ]


def test_a_record_without_the_field_is_on():
    """Записи, сделанные до появления галочки, поля не знают — читаем как «вкл»."""
    stored = [{"name": "acontrol.example.com", "node_id": "7"}]
    assert len(dnsrecords.entries_for(stored, [NODE])) == 1


def test_a_name_without_its_node_is_not_handed_out():
    """Имя, ведущее на адрес удалённой ноды, хуже отсутствующего: адреса в меше
    переиспользуются, и однажды оно приведёт на чужую машину."""
    stored = [{"name": "gone.example.com", "node_id": "7"}]
    assert dnsrecords.entries_for(stored, []) == []


def test_manual_address_picks_the_record_type():
    stored = [
        {"name": "nas.example.com", "ip": "192.168.1.10"},
        {"name": "six.example.com", "ip": "fd7a:115c::5"},
        {"name": "junk.example.com", "ip": "не адрес"},
    ]
    out = dnsrecords.entries_for(stored, [])
    assert out == [
        {"name": "nas.example.com", "type": "A", "value": "192.168.1.10"},
        {"name": "six.example.com", "type": "AAAA", "value": "fd7a:115c::5"},
    ]


def test_write_file_is_atomic_and_silent_when_nothing_changed(tmp_path):
    path = tmp_path / "extra-records.json"
    entries = [{"name": "a.example.com", "type": "A", "value": "100.100.0.1"}]
    assert dnsrecords.write_file(str(path), entries) is True
    assert json.loads(path.read_text(encoding="utf-8")) == entries
    assert not (tmp_path / "extra-records.json.tmp").exists()  # временный файл убран
    # то же содержимое — файл не трогаем: каждая запись будит следилку headscale
    assert dnsrecords.write_file(str(path), entries) is False
    entries[0]["value"] = "100.100.0.2"
    assert dnsrecords.write_file(str(path), entries) is True
    assert dnsrecords.read_file(str(path)) == entries


def test_read_file_survives_garbage(tmp_path):
    path = tmp_path / "extra-records.json"
    path.write_text("{ это не json", encoding="utf-8")
    assert dnsrecords.read_file(str(path)) == []
    assert dnsrecords.read_file(str(tmp_path / "нет-такого.json")) == []


async def test_sync_does_not_litter_when_the_feature_is_unused(session, tmp_path):
    path = tmp_path / "extra-records.json"
    s = Settings(headscale_extra_records_path=str(path))
    assert await dnsrecords.sync(session, s, [NODE]) is False
    assert not path.exists()  # имён нет и файла не было — незачем его заводить

    await settings_store.set_dns_records(
        session, [{"name": "acontrol.example.com", "node_id": "7"}]
    )
    assert await dnsrecords.sync(session, s, [NODE]) is True
    assert dnsrecords.read_file(str(path))[0]["value"] == "100.100.0.1"
    # нода исчезла — файл чистится сам, без похода в UI
    assert await dnsrecords.sync(session, s, []) is True
    assert dnsrecords.read_file(str(path)) == []


async def test_a_deleted_node_takes_its_name_with_it(session):
    await settings_store.set_dns_records(
        session,
        [
            {"name": "gone.example.com", "node_id": "7"},
            {"name": "nas.example.com", "ip": "192.168.1.10"},
        ],
    )
    await settings_store.forget_node_refs(session, "7")
    left = await settings_store.get_dns_records(session)
    assert [r["name"] for r in left] == ["nas.example.com"]  # ручной адрес не трогаем


async def test_a_reconnected_node_keeps_its_name(session):
    """Переподключение = новая запись у headscale. Имя должно переехать на неё,
    иначе оно молча перестанет раздаваться."""
    await settings_store.set_dns_records(
        session, [{"name": "acontrol.example.com", "node_id": "7"}]
    )
    await settings_store._repoint_node_id(session, "7", "12")
    assert (await settings_store.get_dns_records(session))[0]["node_id"] == "12"


# --- ручки ------------------------------------------------------------------


class _HS:
    """headscale с одной нодой — ровно то, что нужно ручке имён."""

    async def get_nodes(self):
        return [NODE]


@pytest.fixture
def hs_env(tmp_path, monkeypatch):
    """Конфиг headscale и путь к файлу имён — во временном каталоге."""
    cfg_dir = tmp_path / "headscale" / "config"
    cfg_dir.mkdir(parents=True)
    cfg = cfg_dir / "config.yaml"
    cfg.write_text(
        "server_url: https://hs.example.com\n"
        "# важный комментарий\n"
        "dns:\n"
        "  magic_dns: true\n"
        "  base_domain: mesh.internal\n",
        encoding="utf-8",
    )
    records = cfg_dir / "extra-records.json"
    monkeypatch.setenv("NODEROOST_HEADSCALE_CONFIG_PATH", str(cfg))
    monkeypatch.setenv("NODEROOST_HEADSCALE_EXTRA_RECORDS_PATH", str(records))
    monkeypatch.setenv(
        "NODEROOST_HEADSCALE_EXTRA_RECORDS_PATH_IN_HS", "/etc/headscale/extra-records.json"
    )
    get_settings.cache_clear()
    monkeypatch.setattr(api_settings, "get_client", lambda _s: _HS())
    monkeypatch.setattr(api_settings, "require_hs", lambda _s: None)
    yield cfg, records
    get_settings.cache_clear()


async def _login(client):
    r = await client.post(
        "/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD}
    )
    return r.json()["access_token"]


async def _put(client, token, records):
    return await client.put(
        "/api/hs-info/dns-records",
        json={"records": records},
        headers={"Authorization": f"Bearer {token}"},
    )


async def test_dns_records_require_auth(client):
    assert (await client.get("/api/hs-info/dns-records")).status_code == 401


async def test_first_save_wires_the_config_once(client, hs_env):
    cfg, records = hs_env
    token = await _login(client)
    r = await _put(client, token, [{"name": "acontrol.example.com", "node_id": "7"}])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["records"][0]["addresses"] == ["100.100.0.1"]
    assert body["records"][0]["node_name"] == "acontrol"
    assert body["active"] is True and body["restart_pending"] is True

    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert data["dns"]["extra_records_path"] == "/etc/headscale/extra-records.json"
    assert data["dns"]["magic_dns"] is True  # прочее на месте
    assert "# важный комментарий" in cfg.read_text(encoding="utf-8")
    assert dnsrecords.read_file(str(records))[0]["value"] == "100.100.0.1"

    # вторая правка идёт БЕЗ перезапуска: headscale перечитывает файл сам
    flag = cfg.parent.parent / ".restart-request"
    flag.unlink()
    r = await _put(client, token, [{"name": "nas.example.com", "ip": "192.168.1.10"}])
    assert r.status_code == 200, r.text
    assert not flag.exists()
    assert dnsrecords.read_file(str(records)) == [
        {"name": "nas.example.com", "type": "A", "value": "192.168.1.10"}
    ]


def test_the_path_lands_inside_its_own_block(tmp_path):
    """Ключ должен стоять в блоке dns, а не под чужим заголовком.

    ruamel дописывает новый ключ в КОНЕЦ блока — то есть после комментария,
    который относится уже к следующей секции. YAML при этом валиден, но человек
    читает строку как часть чужой секции и уносит её вместе с ней.
    """
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "dns:\n"
        "  magic_dns: true\n"
        "\n"
        "# Unix-сокет для локального CLI внутри контейнера.\n"
        "unix_socket: /var/run/headscale/headscale.sock\n",
        encoding="utf-8",
    )
    api_settings._write_extra_records_path(str(cfg), "/etc/headscale/extra-records.json")
    text = cfg.read_text(encoding="utf-8")
    assert text.index("extra_records_path") < text.index("# Unix-сокет")
    data = yaml.safe_load(text)
    assert data["dns"]["extra_records_path"] == "/etc/headscale/extra-records.json"
    assert data["unix_socket"] == "/var/run/headscale/headscale.sock"


async def test_the_file_exists_before_the_config_points_at_it(client, hs_env, monkeypatch):
    """С extra_records_path на несуществующий файл headscale НЕ СТАРТУЕТ —
    поэтому файл пишется первым, а конфиг вторым."""
    cfg, records = hs_env
    seen: list[bool] = []
    real = api_settings._write_extra_records_path
    monkeypatch.setattr(
        api_settings,
        "_write_extra_records_path",
        lambda c, p: (seen.append(records.exists()), real(c, p))[1],
    )
    token = await _login(client)
    assert (
        await _put(client, token, [{"name": "acontrol.example.com", "node_id": "7"}])
    ).status_code == 200
    assert seen == [True]


async def test_the_control_server_name_is_refused(client, hs_env):
    """Имя control-сервера, уведённое в меш, отрезает ноды от него навсегда: об
    отмене они узнали бы от него же."""
    token = await _login(client)
    r = await _put(client, token, [{"name": "hs.example.com", "node_id": "7"}])
    assert r.status_code == 400
    assert "control-сервер" in r.json()["detail"]


async def test_a_magicdns_name_is_refused(client, hs_env):
    token = await _login(client)
    r = await _put(client, token, [{"name": "acontrol.mesh.internal", "node_id": "7"}])
    assert r.status_code == 400
    assert "MagicDNS" in r.json()["detail"]


async def test_an_unknown_node_is_refused(client, hs_env):
    token = await _login(client)
    r = await _put(client, token, [{"name": "x.example.com", "node_id": "999"}])
    assert r.status_code == 400


async def test_duplicate_names_are_refused(client, hs_env):
    token = await _login(client)
    r = await _put(
        client,
        token,
        [
            {"name": "dup.example.com", "node_id": "7"},
            {"name": "DUP.example.com", "ip": "10.0.0.1"},
        ],
    )
    assert r.status_code == 422  # какая из двух победит — не угадать, отказ


async def test_a_name_needs_exactly_one_target(client, hs_env):
    token = await _login(client)
    both = await _put(
        client, token, [{"name": "x.example.com", "node_id": "7", "ip": "10.0.0.1"}]
    )
    none = await _put(client, token, [{"name": "x.example.com"}])
    assert both.status_code == 422 and none.status_code == 422


async def test_a_bad_name_is_refused(client, hs_env):
    token = await _login(client)
    r = await _put(client, token, [{"name": "не имя", "node_id": "7"}])
    assert r.status_code == 422


async def test_switching_a_name_off_keeps_it_but_stops_handing_it_out(client, hs_env):
    cfg, records = hs_env
    token = await _login(client)
    await _put(client, token, [{"name": "acontrol.example.com", "node_id": "7"}])
    flag = cfg.parent.parent / ".restart-request"
    flag.unlink()

    r = await _put(
        client,
        token,
        [{"name": "acontrol.example.com", "node_id": "7", "enabled": False}],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["records"][0]["enabled"] is False  # запись на месте
    assert dnsrecords.read_file(str(records)) == []  # но нодам не раздаётся
    assert not flag.exists()  # и без перезапуска headscale

    back = await _put(client, token, [{"name": "acontrol.example.com", "node_id": "7"}])
    assert back.json()["records"][0]["enabled"] is True
    assert dnsrecords.read_file(str(records))[0]["value"] == "100.100.0.1"


async def test_clearing_the_list_leaves_the_file_and_config_alone(client, hs_env):
    """Пустой список — это пустой файл, а не снятый путь: снимать его значит
    трогать конфиг и перезапускать headscale ради ничего."""
    cfg, records = hs_env
    token = await _login(client)
    await _put(client, token, [{"name": "acontrol.example.com", "node_id": "7"}])
    flag = cfg.parent.parent / ".restart-request"
    flag.unlink()
    r = await _put(client, token, [])
    assert r.status_code == 200
    assert dnsrecords.read_file(str(records)) == []
    assert not flag.exists()
    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert data["dns"]["extra_records_path"] == "/etc/headscale/extra-records.json"
