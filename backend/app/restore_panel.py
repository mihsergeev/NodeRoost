"""Импорт panel.json (из бэкапа) обратно в БД панели (Postgres): app_settings +
users. Файлы headscale (db.sqlite/config/ключи) кладёт на место хостовый
ops/restore.sh — у бэкенда нет доступа к их рестарту by design.

Запуск (внутри backend-образа, БД должна быть доступна):
    python -m app.restore_panel /path/to/panel.json
    python -m app.restore_panel - < panel.json

Идемпотентен: upsert по ключу настройки и по имени пользователя. Существующие
настройки/юзеры, которых нет в бэкапе, НЕ удаляются (безопасно для частичного
восстановления). Счётчик TOTP сбрасывается только при смене секрета."""

import asyncio
import base64
import ipaddress
import json
import socket
import sys
from pathlib import Path

from sqlalchemy import delete, select

from app.config import get_settings
from app.db import create_engine_and_factory
from app.models import AppSetting, TrustedDevice, User
from app.urlguard import UrlNotAllowed, is_public_ip, parse_outbound_url


def _sanitize_setting(key: str, value: str) -> str:
    """Настройки из архива проходят те же проверки, что и при сохранении в UI.

    Архив — файл: его может подсунуть тот, кто добрался до каталога бэкапов или до
    offsite-репозитория. Без проверки восстановление вписывало бы в конфиг алертов
    произвольный адрес, а по нему потом ходит и панель, и хостовый watchdog под
    root — то есть внутренние адреса запрашивались бы от их имени (SSRF). Проверка
    синхронная и офлайновая: резолв здесь недоступен, поэтому отсекаем схему и
    литеральные приватные/локальные адреса. При сохранении в UI и каждой
    отправке имена также проходят проверку DNS.
    """
    if key != "alerts":
        return value
    try:
        data = json.loads(value or "{}")
    except (ValueError, TypeError):
        raise ValueError("Неверный формат настроек alerts в panel.json") from None
    if not isinstance(data, dict):
        raise ValueError("Неверный формат настроек alerts в panel.json")
    for field in ("webhook", "telegram_api"):
        url = str(data.get(field) or "")
        if url and not _looks_public(url):
            data[field] = ""  # молча не оставляем: пусть админ задаст заново
    return json.dumps(data, ensure_ascii=False)


def _looks_public(url: str) -> bool:
    try:
        _, host, _ = parse_outbound_url(url)
    except UrlNotAllowed:
        return False
    host = host.rstrip(".")
    if host in ("localhost",) or host.endswith(".localhost") or host.endswith(".internal"):
        return False
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        try:
            # curl и libc допускают также короткие и шестнадцатеричные IPv4.
            addr = ipaddress.IPv4Address(socket.inet_aton(host))
        except OSError:
            return True  # DNS проверяется перед исходящим соединением
    return is_public_ip(str(addr))


def _read_panel_dump(path: str) -> dict:
    data = json.load(sys.stdin) if path == "-" else json.loads(Path(path).read_bytes())
    if not isinstance(data, dict):
        raise ValueError("panel.json должен содержать объект")
    for name, fields in (("app_settings", ("key", "value")),
                         ("users", ("username", "password_hash"))):
        rows = data.get(name, [])
        if not isinstance(rows, list) or any(
            not isinstance(row, dict)
            or any(not isinstance(row.get(field, ""), str) for field in fields)
            or not row.get(fields[0])
            or (name == "users" and not row.get(fields[1]))
            for row in rows
        ):
            raise ValueError(f"Неверный формат записей {name} в panel.json")
        if len({row[fields[0]] for row in rows}) != len(rows):
            raise ValueError(f"Повторяющиеся записи {name} в panel.json")
    for row in data.get("app_settings", []):
        row["value"] = _sanitize_setting(row["key"], row.get("value", ""))
    for user in data.get("users", []):
        enabled = user.get("totp_enabled", False)
        secret = user.get("totp_secret", "")
        version = user.get("token_version", 0)
        if (
            type(enabled) is not bool or not isinstance(secret, str) or len(secret) > 64
            or type(version) is not int or not 0 <= version < 2**31 - 1
            or len(user["username"]) > 64 or len(user["password_hash"]) > 128
        ):
            raise ValueError("Неверный формат учетной записи в panel.json")
        if enabled:
            try:
                key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
            except ValueError:
                key = b""
            if not key:
                raise ValueError("В panel.json включена 2FA без действительного секрета")
    return data


async def restore_panel(path: str) -> dict:
    data = _read_panel_dump(path)
    settings = get_settings()
    engine, Session = create_engine_and_factory(settings.db_url)
    try:
        async with Session() as session:
            for row in data.get("app_settings", []):
                value = row["value"]
                existing = await session.get(AppSetting, row["key"])
                if existing is not None:
                    existing.value = value
                else:
                    session.add(AppSetting(key=row["key"], value=value))
            for u in data.get("users", []):
                enabled = u.get("totp_enabled", False)
                secret = u.get("totp_secret", "")
                existing = await session.scalar(
                    select(User).where(User.username == u["username"])
                )
                if existing is not None:
                    credentials_changed = existing.password_hash != u["password_hash"]
                    existing.password_hash = u["password_hash"]
                    # Архив без 2FA не должен стереть секрет действующего фактора.
                    if enabled or not existing.totp_enabled:
                        credentials_changed |= (existing.totp_enabled != enabled
                                                or existing.totp_secret != secret)
                        if existing.totp_secret != secret:
                            existing.totp_last_counter = 0
                        existing.totp_secret = secret
                        existing.totp_enabled = enabled
                    if credentials_changed:
                        await session.execute(delete(TrustedDevice).where(
                            TrustedDevice.username == u["username"]
                        ))
                    # Версию токенов двигаем только вверх и ещё на шаг: иначе
                    # старый архив вернул бы счётчик назад и оживил токены,
                    # отозванные при смене пароля, до конца их срока.
                    existing.token_version = (
                        max(existing.token_version, u.get("token_version", 0)) + 1
                    )
                else:
                    session.add(
                        User(
                            username=u["username"],
                            password_hash=u["password_hash"],
                            totp_secret=secret,
                            totp_enabled=enabled,
                            token_version=u.get("token_version", 0),
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
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("path", help="panel.json or - for stdin")
    args = parser.parse_args()
    if args.check:
        _read_panel_dump(args.path)
    else:
        res = asyncio.run(restore_panel(args.path))
        print(f"restored: {res['settings']} настроек, {res['users']} юзеров")
