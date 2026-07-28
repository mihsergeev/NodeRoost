"""Данные, которые сам клиент Tailscale сообщил о себе (ОС, версия, архитектура).

ВАЖНО: это НЕ публичный контракт headscale. REST API `/api/v1/node` таких полей
не отдаёт вовсе, они лежат в его внутренней SQLite (`nodes.host_info`). Поэтому
здесь всё строго read-only и best-effort: недоступна БД, сменилась схема или
формат в новой версии headscale — вернём пусто, и панель просто не покажет блок.
Ни одна операция панели от этих данных не зависит.
"""

import ipaddress
import json
import logging
import sqlite3

log = logging.getLogger("noderoost.hostinfo")


def _public_endpoint(raw: str | None) -> str:
    """Первый публичный адрес из endpoints ноды (остальные — LAN/докер-мосты)."""
    try:
        eps = json.loads(raw) if raw else []
    except (ValueError, TypeError):
        return ""
    for ep in eps:
        host = str(ep).rsplit(":", 1)[0]
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            continue
        if ip.is_global:  # отсекает 10/8, 172.16/12, 192.168/16, 100.64/10, loopback
            return str(ep)
    return ""


def _os_label(info: dict) -> str:
    """«Ubuntu 24.04» — по возможности дистрибутив, иначе ядро/ОС."""
    distro = (info.get("Distro") or "").strip()
    if distro:
        ver = (info.get("DistroVersion") or "").strip()
        return f"{distro.capitalize()} {ver}".strip()
    # у Windows/macOS Distro не приходит — собираем из OS + OSVersion
    os_name = (info.get("OS") or "").strip()
    os_ver = (info.get("OSVersion") or "").strip()
    pretty = {"windows": "Windows", "macos": "macOS", "ios": "iOS", "android": "Android"}
    return f"{pretty.get(os_name.lower(), os_name.capitalize())} {os_ver}".strip()


def _parse(hi_raw: str | None, eps_raw: str | None) -> dict:
    try:
        info = json.loads(hi_raw) if hi_raw else {}
    except (ValueError, TypeError):
        info = {}
    if not isinstance(info, dict):
        return {}
    net = info.get("NetInfo") if isinstance(info.get("NetInfo"), dict) else {}
    # IPNVersion выглядит как «1.98.8-t1241b225b-g0520dfda5» — берём номер версии
    version = (info.get("IPNVersion") or "").split("-", 1)[0]
    return {
        "client_version": version,
        "os": _os_label(info),
        "arch": (info.get("Machine") or "").strip(),
        "container": bool(info.get("Container", False)),
        "endpoint": _public_endpoint(eps_raw),
        # WorkingUDP=false → прямое соединение не поднимется, трафик пойдёт через DERP
        "direct_ok": bool(net.get("WorkingUDP", False)) if net else False,
    }


def read_all(db_path: str) -> dict[str, dict]:
    """{node_id: {client_version, os, arch, container, endpoint, direct_ok}}.
    Любая проблема (нет файла, другая схема, битый JSON) → пустой словарь."""
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error as exc:
        log.debug("host_info недоступен: %s", exc)
        return {}
    try:
        rows = con.execute(
            "SELECT id, host_info, endpoints FROM nodes WHERE deleted_at IS NULL"
        ).fetchall()
    except sqlite3.Error as exc:  # другая схема в новой версии headscale
        log.debug("host_info не прочитан: %s", exc)
        return {}
    finally:
        con.close()
    return {str(nid): _parse(hi, eps) for nid, hi, eps in rows}
