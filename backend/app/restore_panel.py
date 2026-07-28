"""Импорт panel.json (из бэкапа) обратно в БД панели (Postgres): app_settings +
users. Файлы headscale (db.sqlite/config/ключи) кладёт на место хостовый
ops/restore.sh — у бэкенда нет доступа к их рестарту by design.

Запуск (внутри backend-образа, БД должна быть доступна):
    python -m app.restore_panel /path/to/panel.json

Идемпотентен: upsert по ключу настройки и по имени пользователя. Существующие
настройки/юзеры, которых нет в бэкапе, НЕ удаляются (безопасно для частичного
восстановления). totp_last_counter сбрасывается в 0 (в бэкапе не хранится)."""

import asyncio
import json
import sys

from sqlalchemy import select

from app.config import get_settings
from app.db import create_engine_and_factory
from app.models import AppSetting, User


def _sanitize_setting(key: str, value: str) -> str:
    """Настройки из архива проходят те же проверки, что и при сохранении в UI.

    Архив — файл: его может подсунуть тот, кто добрался до каталога бэкапов или до
    offsite-репозитория. Без проверки восстановление вписывало бы в конфиг алертов
    произвольный адрес, а по нему потом ходит и панель, и хостовый watchdog под
    root — то есть внутренние адреса запрашивались бы от их имени (SSRF). Проверка
    синхронная и офлайновая: резолв здесь недоступен, поэтому отсекаем схему и
    литеральные приватные/локальные адреса, а полную проверку с резолвом делает
    urlguard при следующем сохранении из UI.
    """
    if key != "alerts":
        return value
    try:
        data = json.loads(value or "{}")
    except (ValueError, TypeError):
        return value
    for field in ("webhook", "telegram_api"):
        url = str(data.get(field) or "")
        if url and not _looks_public(url):
            data[field] = ""  # молча не оставляем: пусть админ задаст заново
    return json.dumps(data, ensure_ascii=False)


def _looks_public(url: str) -> bool:
    from urllib.parse import urlparse

    try:
        u = urlparse(url)
    except ValueError:
        return False
    if u.scheme != "https" or not u.hostname:
        return False
    host = u.hostname.lower()
    if host in ("localhost",) or host.endswith(".localhost") or host.endswith(".internal"):
        return False
    try:
        import ipaddress

        addr = ipaddress.ip_address(host)
    except ValueError:
        return True  # имя — проверит urlguard с резолвом при сохранении
    return not (
        addr.is_private or addr.is_loopback or addr.is_link_local
        or addr.is_reserved or addr.is_unspecified or addr.is_multicast
    )


async def restore_panel(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    settings = get_settings()
    engine, Session = create_engine_and_factory(settings.db_url)
    try:
        async with Session() as session:
            for row in data.get("app_settings", []):
                value = _sanitize_setting(row["key"], row.get("value", ""))
                existing = await session.get(AppSetting, row["key"])
                if existing is not None:
                    existing.value = value
                else:
                    session.add(AppSetting(key=row["key"], value=value))
            for u in data.get("users", []):
                existing = await session.scalar(
                    select(User).where(User.username == u["username"])
                )
                if existing is not None:
                    existing.password_hash = u["password_hash"]
                    existing.totp_secret = u.get("totp_secret", "") or ""
                    existing.totp_enabled = bool(u.get("totp_enabled", False))
                    existing.token_version = int(u.get("token_version", 0))
                else:
                    session.add(
                        User(
                            username=u["username"],
                            password_hash=u["password_hash"],
                            totp_secret=u.get("totp_secret", "") or "",
                            totp_enabled=bool(u.get("totp_enabled", False)),
                            token_version=int(u.get("token_version", 0)),
                        )
                    )
            await session.commit()
    finally:
        await engine.dispose()
    return {
        "settings": len(data.get("app_settings", [])),
        "users": len(data.get("users", [])),
    }


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python -m app.restore_panel <panel.json>", file=sys.stderr)
        raise SystemExit(2)
    res = asyncio.run(restore_panel(sys.argv[1]))
    print(f"restored: {res['settings']} настроек, {res['users']} юзеров")
