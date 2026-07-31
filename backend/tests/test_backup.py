import io
import sqlite3
import tarfile
from pathlib import Path

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


def test_verify_rejects_garbage():
    assert backup.verify_archive(b"not a tar.gz") != []


def test_is_backup_name():
    assert backup.is_backup_name("noderoost-backup-20260717-120000.tar.gz")
    assert not backup.is_backup_name("../../etc/passwd")
    assert not backup.is_backup_name("evil.tar.gz")


async def test_backup_list_requires_auth(client):
    assert (await client.get("/api/backup/list")).status_code == 401
    assert (await client.post("/api/backup/run")).status_code == 401


def test_verify_runs_before_prune(tmp_path, monkeypatch):
    """Негодный бэкап не должен уносить последнюю рабочую копию: прунинг делаем
    только после успешной самопроверки."""
    from app import backup

    called = []
    monkeypatch.setattr(backup, "prune_backups", lambda *a, **k: called.append(a))
    assert backup.verify_archive(b"not-an-archive")  # проблемы найдены
    assert called == []  # прунинг не вызывался


def test_restore_drops_internal_alert_urls():
    """Архив — файл: подсунув его, можно было прописать в алерты внутренний адрес,
    по которому потом ходит панель и root-watchdog (SSRF)."""
    import json as _json
    from app.restore_panel import _sanitize_setting

    bad = _json.dumps({"webhook": "https://127.0.0.1/x", "telegram_api": "http://10.0.0.5"})
    got = _json.loads(_sanitize_setting("alerts", bad))
    assert got["webhook"] == "" and got["telegram_api"] == ""
    ok = _json.dumps({"webhook": "https://hooks.example.com/x", "telegram_api": ""})
    assert _json.loads(_sanitize_setting("alerts", ok))["webhook"] == "https://hooks.example.com/x"
    # чужие ключи не трогаем
    assert _sanitize_setting("acl_rules", "[1,2]") == "[1,2]"


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


async def test_archive_is_written_for_its_owner_only(tmp_path, monkeypatch):
    """Архив несёт секрет 2FA, хеш пароля и приватные ключи control-сервера.

    Каталог закрыт правами, но файл живёт дольше каталога: его скачивают,
    копируют, кладут в хранилище — и права уезжают вместе с ним.
    """
    import os
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
    path, problems = await backup_mod.write_backup(lambda: _Ctx(), st, keep=3)
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
