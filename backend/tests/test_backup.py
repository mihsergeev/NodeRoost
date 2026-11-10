import asyncio
import io
import json
import os
import sqlite3
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import backup
from app.config import Settings
from tests.conftest import ADMIN_PASSWORD


def _fake_headscale(root: Path) -> None:
    lib = root / "headscale" / "lib"
    cfg = root / "headscale" / "config"
    lib.mkdir(parents=True)
    cfg.mkdir(parents=True)
    con = sqlite3.connect(lib / "db.sqlite")
    con.execute("CREATE TABLE node(x)")
    con.execute("INSERT INTO node VALUES (1)")
    con.commit()
    con.close()
    (cfg / "config.yaml").write_text("server_url: https://hs.test\n")
    (lib / "noise_private.key").write_bytes(b"secretnoisekey")


async def test_build_and_verify(session, tmp_path):
    _fake_headscale(tmp_path)
    s = Settings(data_dir=str(tmp_path))
    archive = await backup.build_archive(session, s)
    assert backup.verify_archive(archive) == []
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        names = set(tar.getnames())
    assert {"panel.json", "headscale/db.sqlite", "headscale/config.yaml"} <= names
    assert "headscale/lib/noise_private.key" in names
    assert backup.verify_archive(archive[:-1])  # gzip-архив без полного трейлера


def test_verify_rejects_garbage():
    assert backup.verify_archive(b"not a tar.gz") != []


def test_is_backup_name():
    assert backup.is_backup_name("noderoost-backup-20260717-120000.tar.gz")
    assert backup.is_backup_name("noderoost-backup-20260717-120000-123456-0123456789abcdef.tar.gz")
    assert not backup.is_backup_name("../../etc/passwd")
    assert not backup.is_backup_name("evil.tar.gz")


async def test_backup_list_requires_auth(client):
    assert (await client.get("/api/backup/list")).status_code == 401
    assert (await client.post("/api/backup/run")).status_code == 401


async def test_bad_backup_does_not_replace_or_prune_a_good_copy(session_factory, tmp_path, monkeypatch):
    _fake_headscale(tmp_path)
    settings = Settings(data_dir=str(tmp_path))
    good, size, problems = await backup.write_backup(session_factory, settings, keep=1)
    assert problems == []
    good_data = Path(good).read_bytes()
    assert size == len(good_data)

    monkeypatch.setattr(backup, "_snapshot_sqlite", lambda _: b"SQLite format 3\x00")
    rejected, size, problems = await backup.write_backup(session_factory, settings, keep=1)
    assert problems and rejected == "" and size == 0
    assert Path(good).read_bytes() == good_data
    assert [row["filename"] for row in backup.list_backups(str(tmp_path))] == [Path(good).name]


async def test_concurrent_backups_in_the_same_second_keep_both_archives(
    session_factory, tmp_path, monkeypatch,
):
    class FrozenTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 17, 12, tzinfo=timezone.utc)

    monkeypatch.setattr(backup, "datetime", FrozenTime)
    _fake_headscale(tmp_path)
    settings = Settings(data_dir=str(tmp_path))
    results = await asyncio.gather(*(
        backup.write_backup(session_factory, settings, keep=2) for _ in range(2)
    ))
    assert len({path for path, _, _ in results}) == 2
    for path, size, problems in results:
        data = Path(path).read_bytes()
        assert size == len(data)
        assert not problems and not backup.verify_archive(data)
    assert {row["filename"] for row in backup.list_backups(str(tmp_path))} == {
        Path(path).name for path, _, _ in results
    }
    latest, _, problems = await backup.write_backup(session_factory, settings, keep=1)
    assert not problems
    assert latest not in {path for path, _, _ in results}
    assert [row["filename"] for row in backup.list_backups(str(tmp_path))] == [Path(latest).name]


def test_restore_drops_internal_alert_urls():
    """Архив — файл: подсунув его, можно было прописать в алерты внутренний адрес,
    по которому потом ходит панель и root-watchdog (SSRF)."""
    import json as _json
    from app.restore_panel import _sanitize_setting

    bad = _json.dumps({"webhook": "https://127.0.0.1/x", "telegram_api": "http://10.0.0.5"})
    got = _json.loads(_sanitize_setting("alerts", bad))
    assert got["webhook"] == "" and got["telegram_api"] == ""
    mesh = _json.dumps({"webhook": "https://100.64.0.42/hook"})
    assert _json.loads(_sanitize_setting("alerts", mesh))["webhook"] == ""
    ok = _json.dumps({"webhook": "https://hooks.example.com/x", "telegram_api": ""})
    assert _json.loads(_sanitize_setting("alerts", ok))["webhook"] == "https://hooks.example.com/x"
    # чужие ключи не трогаем
    assert _sanitize_setting("acl_rules", "[1,2]") == "[1,2]"


async def test_restore_pre_2fa_backup_preserves_login(tmp_path, session_factory, monkeypatch):
    from sqlalchemy import select

    from app import devices, restore_panel, totp
    from app.models import User

    secret = totp.random_secret()
    async with session_factory() as db:
        db.add(User(username="demo-admin", password_hash="current-test-hash",
                    totp_secret=secret, totp_enabled=True, totp_last_counter=100,
                    token_version=9))
        await db.commit()
        trust_token, _ = await devices.remember(db, "demo-admin", "test-browser", "192.0.2.8")

    archive = tmp_path / "panel.json"
    archive.write_text(json.dumps({"users": [{
        "username": "demo-admin", "password_hash": "backup-test-hash", "token_version": 4,
    }]}), encoding="utf-8")
    monkeypatch.setattr(restore_panel, "create_engine_and_factory",
                        lambda _: (session_factory.kw["bind"], session_factory))
    await restore_panel.restore_panel(str(archive))

    async with session_factory() as db:
        user = await db.scalar(select(User))
        assert user.password_hash == "backup-test-hash"
        assert user.token_version == 10
        assert user.totp_enabled and totp.verify(user.totp_secret, totp._hotp(secret, 101), now=3030)
        assert user.totp_last_counter == 100
        assert not await devices.check(db, user.username, trust_token)


@pytest.mark.parametrize("dump", [
    {"users": [{"username": "demo", "password_hash": "fixture", "totp_enabled": True}]},
    {"users": [{"username": "demo", "password_hash": "fixture", "token_version": "bad"}]},
    {"app_settings": [{"key": "alerts", "value": "[]"}]},
])
def test_restore_check_rejects_corruption_before_stopping_services(tmp_path, dump):
    from app.restore_panel import _read_panel_dump

    path = tmp_path / "panel.json"
    path.write_text(json.dumps(dump), encoding="utf-8")
    with pytest.raises(ValueError):
        _read_panel_dump(str(path))


def test_policy_comparison_ignores_formatting():
    """Самоисцеление сверяется с живой политикой; сравнивать надо по смыслу, иначе
    другой отступ вызывал бы пуш каждую минуту."""
    from app.policy_apply import _same_policy

    assert _same_policy('{"acls": []}', '{\n  "acls": []\n}')
    assert not _same_policy('{"acls": []}', '{"acls": [{"action": "accept"}]}')


async def test_failed_manual_backup_says_why(client, monkeypatch):
    """«500 Internal Server Error» не говорит админу ничего — а бэкап не записан."""
    from app import backup as backup_mod
    from app.api import backup as api_backup

    async def boom(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(api_backup.backup, "write_backup", boom)
    monkeypatch.setattr(backup_mod, "write_backup", boom, raising=False)

    r = await client.post("/api/auth/login",
                          json={"username": "admin", "password": ADMIN_PASSWORD})
    tok = r.json()["access_token"]
    resp = await client.post("/api/backup/run", json={},
                             headers={"Authorization": f"Bearer {tok}"})
    assert resp.status_code == 500
    detail = resp.json()["detail"]
    assert "No space left" in detail and "data/backups" in detail


async def test_manual_backup_does_not_report_a_rejected_archive_as_created(client, tmp_path, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "data_dir", str(tmp_path))
    login = await client.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    response = await client.post("/api/backup/run", json={}, headers=headers)
    assert response.status_code == 500
    assert "self-тест" in response.json()["detail"]
    assert "db.sqlite" in response.json()["detail"]
    assert (await client.get("/api/backup/list", headers=headers)).json() == []


@pytest.mark.skipif(
    os.name != "posix",
    reason="права POSIX; на Windows chmod меняет только флаг «только чтение»",
)
async def test_archive_is_written_for_its_owner_only(tmp_path, monkeypatch):
    """Архив несёт секрет 2FA, хеш пароля и приватные ключи control-сервера.

    Каталог закрыт правами, но файл живёт дольше каталога: его скачивают,
    копируют, кладут в хранилище — и права уезжают вместе с ним.
    """
    import stat

    from app import backup as backup_mod

    async def fake_archive(session, settings):
        return b"x" * 32

    def fake_verify(data):
        return []

    monkeypatch.setattr(backup_mod, "build_archive", fake_archive)
    monkeypatch.setattr(backup_mod, "verify_archive", fake_verify)

    class _Ctx:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *a):
            return False

    st = Settings(data_dir=str(tmp_path))
    path, _, problems = await backup_mod.write_backup(lambda: _Ctx(), st, keep=3)
    assert problems == []
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600, oct(mode)


def test_restore_script_unpacks_a_real_archive(tmp_path):
    """Восстановление обязано разворачивать настоящий архив.

    Строка распаковки однажды склеилась при правке («\» + перенос превратились
    в лишний аргумент «n»), и tar молча падал на ЛЮБОМ архиве: инструмент
    аварийного восстановления был мёртв, а проверяли до этого только путь с
    заведомо битым файлом — там он «работал» по неверной причине.
    """
    import re
    import subprocess
    import shutil

    script = Path(__file__).resolve().parents[2] / "ops" / "restore.sh"
    if not script.exists():                 # тесты гоняют и без каталога ops
        return
    body = script.read_text(encoding="utf-8")
    line = next(l for l in body.split("\n") if "tar -xzf" in l)
    assert chr(92) + "n" not in line, line   # склеенный перенос строки

    if shutil.which("tar") is None:         # на Windows проверяем только текст
        return
    src = tmp_path / "data"
    src.mkdir()
    (src / "panel.json").write_text("{}", encoding="utf-8")
    arc = tmp_path / "a.tar.gz"
    subprocess.run(["tar", "-czf", str(arc), "-C", str(src), "panel.json"], check=True)
    out = tmp_path / "out"
    out.mkdir()
    # ровно та команда, которую выполняет скрипт
    r = subprocess.run(["tar", "-xzf", str(arc), "-C", str(out)], capture_output=True)
    assert r.returncode == 0, r.stderr
    assert (out / "panel.json").exists()


@pytest.mark.skipif(os.name != "posix", reason="restore runs on a Linux host")
@pytest.mark.parametrize("snapshot", [b"", b"SQLite format 3\0"])
def test_restore_rejects_broken_sqlite_before_docker(tmp_path, monkeypatch, snapshot):
    import subprocess

    source = tmp_path / "source"
    (source / "headscale").mkdir(parents=True)
    (source / "headscale" / "db.sqlite").write_bytes(snapshot)
    (source / "panel.json").write_text("{}", encoding="utf-8")
    archive = tmp_path / "broken.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(source / "headscale", arcname="headscale")
        tar.add(source / "panel.json", arcname="panel.json")

    fake_docker = tmp_path / "docker"
    fake_docker.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    fake_docker.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    script = Path(__file__).resolve().parents[2] / "ops" / "restore.sh"
    result = subprocess.run(
        ["sh", str(script), str(archive), str(tmp_path)], capture_output=True, check=False,
    )
    assert result.returncode == 1
    assert "db.sqlite в архиве повреждён" in result.stderr.decode("utf-8")


async def test_backup_loop_reloads_config_while_waiting(session_factory, monkeypatch):
    from contextlib import suppress

    from app import autobackup, settings_store

    config = {"interval_hours": 0, "keep": 3}
    read = asyncio.Event()
    written = asyncio.Event()
    writes = []

    async def get_config(*_):
        read.set()
        return config.copy()

    async def write(*args):
        writes.append(args[-1])
        written.set()
        return "backup.tar.gz", 1, []

    monkeypatch.setattr(settings_store, "get_backup_config", get_config)
    monkeypatch.setattr(backup, "write_backup", write)
    monkeypatch.setattr(autobackup, "_config_changed", asyncio.Event())
    monkeypatch.setattr(autobackup, "_START_DELAY", 0)
    task = asyncio.create_task(autobackup.backup_loop(session_factory, Settings()))
    try:
        await asyncio.wait_for(read.wait(), timeout=1)
        config.update(interval_hours=24)
        autobackup.configuration_changed()
        await asyncio.wait_for(written.wait(), timeout=1)
        await asyncio.sleep(0)

        read.clear()
        config.update(interval_hours=48, keep=2)
        autobackup.configuration_changed()
        await asyncio.wait_for(read.wait(), timeout=1)
        assert writes == [3]
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
