import io
import sqlite3
import tarfile
from pathlib import Path

from app import backup
from app.config import Settings


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
