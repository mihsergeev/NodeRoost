import yaml

import pytest

from app.api.settings import (
    _is_panel_key,
    _key_prefix,
    _read_hs_config,
    _restart_flag_path,
    _write_dns_config,
    _write_network_config,
)
from app.config import get_settings
from app.schemas import NetworkUpdateIn
from tests.conftest import ADMIN_PASSWORD


def test_network_validation():
    # внутри CGNAT — ок, нормализуется
    assert NetworkUpdateIn(ipv4_prefix="100.100.0.0/16").ipv4_prefix == "100.100.0.0/16"
    # вне 100.64.0.0/10 — ошибка
    with pytest.raises(ValueError):
        NetworkUpdateIn(ipv4_prefix="10.0.0.0/24")
    with pytest.raises(ValueError):
        NetworkUpdateIn(ipv4_prefix="192.168.0.0/16")


def test_write_network_config(tmp_path):
    cfg_dir = tmp_path / "headscale" / "config"
    cfg_dir.mkdir(parents=True)
    cfg = cfg_dir / "config.yaml"
    cfg.write_text(
        "server_url: https://hs.example\n"
        "# коммент\n"
        "prefixes:\n"
        "  v4: 100.64.0.0/10\n"
        "  v6: fd7a:115c:a1e0::/48\n"
        "  allocation: sequential\n"
        "dns:\n  magic_dns: true\n",
        encoding="utf-8",
    )
    _write_network_config(str(cfg), "100.100.0.0/16", "random")
    data = _read_hs_config(str(cfg))
    assert data["prefixes"]["v4"] == "100.100.0.0/16"
    assert "v6" not in data["prefixes"]  # v6 всегда вычищается — тайлнет только IPv4
    assert data["prefixes"]["allocation"] == "random"
    assert data["dns"]["magic_dns"] is True  # прочее сохранено
    assert "# коммент" in cfg.read_text(encoding="utf-8")
    assert (cfg_dir / "config.yaml.bak").exists()
    assert (tmp_path / "headscale" / ".restart-request").exists()


def test_write_dns_config(tmp_path):
    cfg_dir = tmp_path / "headscale" / "config"
    cfg_dir.mkdir(parents=True)
    cfg = cfg_dir / "config.yaml"
    cfg.write_text(
        "server_url: https://hs.example\n"
        "# важный комментарий\n"
        "dns:\n"
        "  magic_dns: true\n"
        "  base_domain: old.internal\n"
        "  nameservers:\n"
        "    global:\n"
        "      - 8.8.8.8\n"
        "  search_domains: [keepme]\n"
        "unix_socket: /run/hs.sock\n",
        encoding="utf-8",
    )
    _write_dns_config(str(cfg), False, "new.local", ["1.1.1.1", "1.0.0.1"])

    data = _read_hs_config(str(cfg))
    assert data["dns"]["magic_dns"] is False
    assert data["dns"]["base_domain"] == "new.local"
    assert data["dns"]["nameservers"]["global"] == ["1.1.1.1", "1.0.0.1"]
    # прочее сохранено (комментарии, чужие ключи)
    assert data["dns"]["search_domains"] == ["keepme"]
    assert data["unix_socket"] == "/run/hs.sock"
    text = cfg.read_text(encoding="utf-8")
    assert "# важный комментарий" in text
    # бэкап и флаг рестарта созданы
    assert (cfg_dir / "config.yaml.bak").exists()
    assert (tmp_path / "headscale" / ".restart-request").exists()
    assert _restart_flag_path(str(cfg)).endswith(".restart-request")


def test_key_prefix_never_leaks_secret():
    """Префикс идёт в audit_log и в UI. Ключ целиком туда попадать НЕ должен —
    у нового формата headscale точки нет, и старый split('.') отдавал весь ключ."""
    # старый формат «<pfx>.<секрет>»
    assert _key_prefix("4NhEwvT.tYTUwVgg4LPyJNf15Ib1n67M5WpL2xQR") == "4NhEwvT"
    # новый формат «hskey-api-<pfx>-<секрет>» — секрет обязан быть отрезан
    new = "hskey-api-iMyrQ2-SECRETSECRETSECRETvalue4K_Ita"
    assert _key_prefix(new) == "hskey-api-iMyrQ2"
    assert "SECRET" not in _key_prefix(new)
    # неизвестный формат — режем жёстко, целиком не отдаём
    weird = "totallyUnknownKeyFormatWithSecret"
    assert _key_prefix(weird) != weird
    assert len(_key_prefix(weird)) <= 8


def test_is_panel_key():
    # headscale маскирует префикс как «<реальный>***»
    assert _is_panel_key("4NhEwvT***", "4NhEwvT.tYTUwVgg") is True  # старый формат
    assert _is_panel_key("hskey-api-abc-***", "hskey-api-abc-secret") is True  # новый
    assert _is_panel_key("hskey-api-OTHER-***", "4NhEwvT.tYTU") is False  # чужой
    assert _is_panel_key("4NhEwvT***", "") is False  # ключ панели не задан


def test_read_hs_config(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "server_url": "https://hs.test",
                "dns": {
                    "magic_dns": True,
                    "base_domain": "nr.internal",
                    "nameservers": {"global": ["1.1.1.1", "1.0.0.1"]},
                },
                "derp": {
                    "server": {"enabled": False},
                    "urls": ["https://x/derpmap"],
                    "auto_update_enabled": True,
                },
            }
        )
    )
    cfg = _read_hs_config(str(p))
    assert cfg["server_url"] == "https://hs.test"
    assert cfg["dns"]["magic_dns"] is True
    assert cfg["dns"]["nameservers"]["global"] == ["1.1.1.1", "1.0.0.1"]


def test_read_hs_config_missing():
    assert _read_hs_config("/nonexistent/xyz.yaml") == {}


async def _login(client):
    r = await client.post(
        "/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD}
    )
    return r.json()["access_token"]


async def test_apikeys_requires_auth(client):
    assert (await client.get("/api/apikeys")).status_code == 401


async def test_apikeys_503_without_key(client):
    token = await _login(client)
    r = await client.get("/api/apikeys", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 503


async def test_hsinfo_requires_auth(client):
    assert (await client.get("/api/hs-info")).status_code == 401


async def test_hsinfo_ok_defaults(client, monkeypatch):
    # Проверяем поведение БЕЗ конфига headscale → дефолты (эндпоинт не требует
    # headscale API-ключ). Путь заводим заведомо несуществующий явно: полагаться
    # на отсутствие файла по умолчанию нельзя — если тесты гонять там, где
    # примонтирован реальный ./data (например, внутри прод-контейнера), тест
    # прочитает боевой config.yaml и упадёт на ровном месте.
    monkeypatch.setenv("NODEROOST_HEADSCALE_CONFIG_PATH", "/nonexistent/config.yaml")
    get_settings.cache_clear()
    token = await _login(client)
    r = await client.get("/api/hs-info", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["server_url"] == ""
    assert body["dns"]["magic_dns"] is False


def test_empty_nameservers_turn_off_override(tmp_path):
    """headscale не стартует, если список DNS-серверов пуст, а override_local_dns
    включён. Очистка списка в панели роняла control-сервер: контейнер уходил в
    рестарт-луп, панель теряла с ним связь. Пустой список = «клиенты пользуются
    своим DNS», флаг обязан выключиться, даже если галочка осталась стоять."""
    from app.api.settings import _write_dns_config
    import yaml

    cfg = tmp_path / "config.yaml"
    cfg.write_text("server_url: https://hs.example\ndns:\n  magic_dns: true\n", encoding="utf-8")

    _write_dns_config(str(cfg), True, "mesh.internal", ["1.1.1.1"], override_local=True)
    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert data["dns"]["nameservers"]["global"] == ["1.1.1.1"]
    assert data["dns"]["override_local_dns"] is True

    # админ стёр список серверов, галочку снять забыл — флаг гаснет сам
    _write_dns_config(str(cfg), True, "mesh.internal", [], override_local=True)
    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert data["dns"]["nameservers"]["global"] == []
    assert data["dns"]["override_local_dns"] is False


def test_dns_override_is_not_a_side_effect_of_naming_servers(tmp_path):
    """Подмена резолвера на нодах — отдельное решение, а не следствие.

    Раньше флаг включался сам, стоило вписать DNS-серверы: сервер, ходивший во
    внутренний DNS компании, молча переставал его видеть.
    """
    from app.api.settings import _write_dns_config
    import yaml

    cfg = tmp_path / "config.yaml"
    cfg.write_text("server_url: https://hs.example\n", encoding="utf-8")

    _write_dns_config(str(cfg), True, "example.internal", ["1.1.1.1"])
    assert yaml.safe_load(cfg.read_text(encoding="utf-8"))["dns"]["override_local_dns"] is False

    _write_dns_config(str(cfg), True, "example.internal", ["1.1.1.1"], override_local=True)
    assert yaml.safe_load(cfg.read_text(encoding="utf-8"))["dns"]["override_local_dns"] is True

    # без списка серверов флаг обязан гаснуть: с ним headscale не стартует
    _write_dns_config(str(cfg), True, "example.internal", [], override_local=True)
    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert data["dns"]["override_local_dns"] is False
    assert data["dns"]["nameservers"]["global"] == []


async def test_hs_info_admits_a_restart_that_never_happened(client, monkeypatch, tmp_path):
    """Правку записали, а перезапустить headscale некому — панель должна сказать.

    Перезапускает его хостовый помощник; на установках 0.1.x его не существовало,
    и после обновления панель бодро писала «headscale перезапускается», хотя
    правка DNS так и не вступала в силу.
    """
    from app.api import settings as api_settings
    from app.config import Settings, get_settings

    cfg = tmp_path / "config" / "config.yaml"
    cfg.parent.mkdir()
    cfg.write_text("server_url: https://hs.example\n", encoding="utf-8")
    st = Settings(headscale_config_path=str(cfg))
    monkeypatch.setattr(api_settings, "get_settings", lambda: st)

    r = await client.post("/api/auth/login",
                          json={"username": "admin", "password": ADMIN_PASSWORD})
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}

    assert (await client.get("/api/hs-info", headers=h)).json()["restart_pending"] is False
    # помощник снимает этот файл, перезапустив headscale; пока он лежит — не снял
    (tmp_path / ".restart-request").touch()
    assert (await client.get("/api/hs-info", headers=h)).json()["restart_pending"] is True


async def test_unknown_field_in_a_request_is_an_error(client):
    """Опечатка в имени поля не должна выглядеть как успешная настройка.

    pydantic по умолчанию молча выбрасывает лишнее: запрос отвечал 200, а
    настройка оставалась прежней. Так «node_offline_minutes» в алертах,
    которого в коде нет вовсе, годами принимался бы за рабочую опцию.
    """
    r = await client.post("/api/auth/login",
                          json={"username": "admin", "password": ADMIN_PASSWORD})
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}

    good = {"telegram_token": "", "telegram_chat": "",
            "telegram_api": "", "webhook": ""}
    assert (await client.put("/api/alerts", json=good, headers=h)).status_code == 200

    # ни «node_offline_minutes», ни «enabled» во входной модели нет: первого не
    # существует вовсе, второе панель вычисляет сама по настроенным каналам
    bad = dict(good, node_offline_minutes=5)
    resp = await client.put("/api/alerts", json=bad, headers=h)
    assert resp.status_code == 422
    assert "node_offline_minutes" in resp.text
