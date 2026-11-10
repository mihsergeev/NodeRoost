"""Бэкап: tar.gz со снимком состояния headscale (консистентная копия db.sqlite +
config + ключи) и настройками панели (app_settings + учётки). Метрики/история в
бэкап НЕ идут — они восстанавливаются сами."""

import asyncio
import gzip
import io
import json
import os
import re
import secrets
import shutil
import sqlite3
import tarfile
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import BinaryIO

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import AppSetting, User

BACKUP_RE = re.compile(r"noderoost-backup-\d{8}-\d{6}(?:-\d{6}-[0-9a-f]{16})?\.tar\.gz")
_SQLITE_MAGIC = b"SQLite format 3\x00"
_publish_lock = Lock()


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
    return bool(BACKUP_RE.fullmatch(name))


def _snapshot_sqlite(src: Path) -> bytes:
    """Консистентный снимок sqlite (online backup API) даже при WAL и записи."""
    if not src.exists():
        return b""
    fd, tmp_path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    try:
        uri = f"{src.resolve().as_uri()}?mode=ro"
        with (
            closing(sqlite3.connect(uri, uri=True)) as srcconn,
            closing(sqlite3.connect(tmp_path)) as dstconn,
        ):
            srcconn.backup(dstconn)
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
    return await asyncio.to_thread(_pack_archive, panel, settings, now)


def _pack_archive(panel: dict, settings: Settings, now: datetime) -> bytes:
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


def _verify_sqlite(snapshot: BinaryIO) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "db.sqlite"
        head = snapshot.read(len(_SQLITE_MAGIC))
        if head != _SQLITE_MAGIC:
            raise ValueError("db.sqlite не похож на базу sqlite")
        with path.open("wb") as out:
            out.write(head)
            shutil.copyfileobj(snapshot, out)
        with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro&immutable=1", uri=True)) as db:
            if db.execute("PRAGMA quick_check(1)").fetchall() != [("ok",)]:
                raise ValueError("db.sqlite не прошёл SQLite quick_check")


def verify_archive(data: bytes | BinaryIO) -> list[str]:
    """Перечитывает архив и проверяет целостность. Пустой список = бэкап годен."""
    problems: list[str] = []
    try:
        source = io.BytesIO(data) if isinstance(data, bytes) else data
        with gzip.GzipFile(fileobj=source) as stream:
            required = {"panel.json", "headscale/db.sqlite"}
            with tarfile.open(fileobj=stream, mode="r|") as tar:
                for member in tar:
                    if member.name not in required:
                        continue
                    required.remove(member.name)
                    if not member.isfile():
                        problems.append(f"{member.name} не является обычным файлом")
                        continue
                    with tar.extractfile(member) as entry:
                        if member.name == "panel.json":
                            try:
                                if not isinstance(json.load(entry), dict):
                                    raise ValueError
                            except ValueError:
                                problems.append("panel.json не парсится как объект")
                        else:
                            try:
                                _verify_sqlite(entry)
                            except (sqlite3.Error, ValueError) as exc:
                                problems.append(f"непригодный db.sqlite: {exc}")
            problems.extend(f"нет {name}" for name in sorted(required))
            # tar останавливается на конце списка файлов; CRC проверяется в конце gzip.
            while stream.read(64 * 1024):
                pass
    except Exception as exc:  # noqa: BLE001
        problems.append(f"архив не читается: {exc}")
    return problems


def list_backups(data_dir: str) -> list[dict]:
    d = backups_dir(data_dir)
    if not os.path.isdir(d):
        return []
    out: list[dict] = []
    for fn in os.listdir(d):
        if not is_backup_name(fn):
            continue
        try:
            st = os.stat(os.path.join(d, fn))
        except FileNotFoundError:
            continue  # архив мог исчезнуть при одновременной ротации
        out.append(
            {
                "filename": fn,
                "size": st.st_size,
                "created": datetime.fromtimestamp(
                    st.st_mtime, timezone.utc
                ).isoformat(),
            }
        )
    out.sort(key=lambda x: x["filename"].removesuffix(".tar.gz"), reverse=True)
    return out


def prune_backups(d: str, keep: int, current: str) -> None:
    files = sorted(
        (fn for fn in os.listdir(d) if fn != current and is_backup_name(fn)),
        key=lambda fn: fn.removesuffix(".tar.gz"), reverse=True,
    )
    for fn in files[max(keep - 1, 0):]:
        try:
            os.remove(os.path.join(d, fn))
        except OSError:
            pass


def _publish_backup(d: str, archive: bytes, stamp: str, keep: int) -> tuple[str, int, list[str]]:
    # Временный файл вне каталога, который читают панель и offsite. mkstemp
    # сразу закрывает права до 0600. Публикация через link не заменяет существующий архив.
    fd, staged = tempfile.mkstemp(prefix=".noderoost-backup-", dir=os.path.dirname(d))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(archive)
            fh.flush()
            os.fsync(fh.fileno())
        with open(staged, "rb") as fh:
            problems = verify_archive(fh)
        if problems:
            return "", 0, problems
        with _publish_lock:
            while True:
                filename = f"noderoost-backup-{stamp}-{secrets.token_hex(8)}.tar.gz"
                path = os.path.join(d, filename)
                try:
                    os.link(staged, path)
                except FileExistsError:
                    continue
                break
            prune_backups(d, keep, filename)
        return path, len(archive), []
    finally:
        os.unlink(staged)


async def write_backup(
    session_factory, settings: Settings, keep: int
) -> tuple[str, int, list[str]]:
    """Публикует архив и запускает ротацию после проверки записанных байтов."""
    d = ensure_backups_dir(settings.data_dir)
    async with session_factory() as session:
        archive = await build_archive(session, settings)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    return await asyncio.to_thread(_publish_backup, d, archive, stamp, keep)
