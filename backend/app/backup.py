"""Бэкап: tar.gz со снимком состояния headscale (консистентная копия db.sqlite +
config + ключи) и настройками панели (app_settings + учётки). Метрики/история в
бэкап НЕ идут — они восстанавливаются сами."""

import io
import json
import os
import re
import sqlite3
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import AppSetting, User

BACKUP_RE = re.compile(r"^noderoost-backup-\d{8}-\d{6}\.tar\.gz$")
_SQLITE_MAGIC = b"SQLite format 3\x00"


def backups_dir(data_dir: str) -> str:
    return os.path.join(data_dir, "backups")


def ensure_backups_dir(data_dir: str) -> str:
    d = backups_dir(data_dir)
    os.makedirs(d, exist_ok=True)
    try:
        os.chmod(d, 0o700)  # внутри секреты (ключи headscale, хэши паролей)
    except OSError:
        pass
    return d


def is_backup_name(name: str) -> bool:
    return bool(BACKUP_RE.match(name))


def _snapshot_sqlite(src: Path) -> bytes:
    """Консистентный снимок sqlite (online backup API) даже при WAL и записи."""
    if not src.exists():
        return b""
    fd, tmp_path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    try:
        srcconn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        dstconn = sqlite3.connect(tmp_path)
        with dstconn:
            srcconn.backup(dstconn)
        srcconn.close()
        dstconn.close()
        return Path(tmp_path).read_bytes()
    finally:
        os.unlink(tmp_path)


async def build_archive(session: AsyncSession, settings: Settings) -> bytes:
    now = datetime.now(timezone.utc)
    panel = {
        "version": settings.version,
        "created": now.isoformat(),
        "app_settings": [
            {"key": r.key, "value": r.value}
            for r in await session.scalars(select(AppSetting))
        ],
        "users": [
            {
                "username": u.username,
                "password_hash": u.password_hash,
                "totp_secret": u.totp_secret,
                "totp_enabled": u.totp_enabled,
                "token_version": u.token_version,
            }
            for u in await session.scalars(select(User))
        ],
    }
    hs = Path(settings.data_dir) / "headscale"
    db_snapshot = _snapshot_sqlite(hs / "lib" / "db.sqlite")

    buf = io.BytesIO()
    mtime = int(now.timestamp())
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        def add(name: str, data: bytes) -> None:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = mtime
            tar.addfile(info, io.BytesIO(data))

        add("panel.json", json.dumps(panel, ensure_ascii=False, indent=2).encode())
        if db_snapshot:
            add("headscale/db.sqlite", db_snapshot)
        cfg = hs / "config" / "config.yaml"
        if cfg.exists():
            add("headscale/config.yaml", cfg.read_bytes())
        # Имена внутри сети. Файл едет вместе с конфигом не для сохранности (сами
        # записи лежат в app_settings), а чтобы восстановленный config.yaml не
        # ссылался в пустоту: с extra_records_path без файла headscale не стартует.
        extra = Path(settings.headscale_extra_records_path)
        if extra.exists():
            add("headscale/extra-records.json", extra.read_bytes())
        libdir = hs / "lib"
        if libdir.is_dir():
            for f in sorted(libdir.iterdir()):
                if f.is_file() and f.name.endswith(".key"):
                    add(f"headscale/lib/{f.name}", f.read_bytes())
    return buf.getvalue()


def verify_archive(data: bytes) -> list[str]:
    """Перечитывает архив и проверяет целостность. Пустой список = бэкап годен."""
    problems: list[str] = []
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            names = set(tar.getnames())
            if "panel.json" not in names:
                problems.append("нет panel.json")
            else:
                try:
                    json.loads(tar.extractfile("panel.json").read())
                except Exception:  # noqa: BLE001
                    problems.append("panel.json не парсится")
            if "headscale/db.sqlite" not in names:
                problems.append("нет снимка headscale db.sqlite")
            else:
                head = tar.extractfile("headscale/db.sqlite").read(16)
                if head != _SQLITE_MAGIC:
                    problems.append("db.sqlite не похож на базу sqlite")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"архив не читается: {exc}")
    return problems


def list_backups(data_dir: str) -> list[dict]:
    d = backups_dir(data_dir)
    if not os.path.isdir(d):
        return []
    out: list[dict] = []
    for fn in os.listdir(d):
        if not BACKUP_RE.match(fn):
            continue
        st = os.stat(os.path.join(d, fn))
        out.append(
            {
                "filename": fn,
                "size": st.st_size,
                "created": datetime.fromtimestamp(
                    st.st_mtime, timezone.utc
                ).isoformat(),
            }
        )
    out.sort(key=lambda x: x["filename"], reverse=True)
    return out


def prune_backups(data_dir: str, keep: int) -> None:
    d = backups_dir(data_dir)
    if not os.path.isdir(d):
        return
    files = sorted(
        (fn for fn in os.listdir(d) if BACKUP_RE.match(fn)), reverse=True
    )
    for fn in files[max(keep, 1):]:
        try:
            os.remove(os.path.join(d, fn))
        except OSError:
            pass


async def write_backup(
    session_factory, settings: Settings, keep: int
) -> tuple[str, list[str]]:
    """Пишет бэкап и тут же перечитывает с диска для self-теста."""
    d = ensure_backups_dir(settings.data_dir)
    async with session_factory() as session:
        archive = await build_archive(session, settings)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = os.path.join(d, f"noderoost-backup-{stamp}.tar.gz")
    with open(path, "wb") as fh:
        fh.write(archive)
    # 0600, а не 0644: внутри архива секрет второго фактора открытым текстом, хеш
    # пароля админа, токены агентов и приватные ключи control-сервера. Каталог
    # закрыт правами, но файл живёт дольше каталога — его скачивают, копируют,
    # кладут в хранилище, и права уезжают вместе с ним.
    os.chmod(path, 0o600)
    # СНАЧАЛА самопроверка, и только потом прунинг. В обратном порядке негодный
    # бэкап удалял последнюю рабочую копию: ретеншн считал его полноценным, хотя
    # проверка ещё не проходила. Битые архивы в счёт хранения не берём — они
    # остаются на диске, но старые копии переживают их.
    with open(path, "rb") as fh:
        problems = verify_archive(fh.read())
    if not problems:
        prune_backups(settings.data_dir, keep)
    return path, problems
