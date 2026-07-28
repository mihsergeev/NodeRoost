"""Журнал действий панели + логи headscale + сводка по сети (для меню «Журнал»)."""

import asyncio
import os
import time
from pathlib import Path

from fastapi import APIRouter
from sqlalchemy import desc, select

from app import settings_store
from app.api.nodes import _map_node
from app.api.settings import _read_hs_config
from app.backup import list_backups
from app.config import get_settings
from app.deps import CurrentUser, SessionDep
from app.hs_client import get_client
from app.models import AuditLog
from app.schemas import AuditEntryOut, HeadscaleLogsOut, SummaryOut

router = APIRouter(prefix="/logs", tags=["logs"])

HSLOG_FLAG = ".hslogs-request"  # флаг «собери логи headscale» для хостового помощника
HSLOG_FILE = "_hslogs.txt"  # сюда помощник кладёт свежие логи


@router.get("/audit", response_model=list[AuditEntryOut])
async def audit_log(
    _: CurrentUser, session: SessionDep, limit: int = 200
) -> list[AuditEntryOut]:
    """Журнал действий панели (кто/когда/что), свежие сверху."""
    limit = max(1, min(limit, 1000))
    rows = (
        await session.execute(
            select(AuditLog).order_by(desc(AuditLog.ts)).limit(limit)
        )
    ).scalars().all()
    return [
        AuditEntryOut(
            ts=r.ts.isoformat() if r.ts else None,
            username=r.username,
            action=r.action,
            target=r.target,
            detail=r.detail,
        )
        for r in rows
    ]


@router.get("/headscale", response_model=HeadscaleLogsOut)
async def headscale_logs(_: CurrentUser) -> HeadscaleLogsOut:
    """Логи контейнера headscale. Бэкенд без доступа к Docker, поэтому просит
    хостовый помощник (systemd noderoost-hs-logs) сложить свежие логи в файл."""
    settings = get_settings()
    flag = os.path.join(settings.data_dir, HSLOG_FLAG)
    out = os.path.join(settings.data_dir, HSLOG_FILE)
    req = time.time()
    try:
        Path(flag).touch()
    except OSError:
        pass
    for _i in range(20):  # ждём свежий файл до ~5 c
        await asyncio.sleep(0.25)
        try:
            if os.path.exists(out) and os.path.getmtime(out) >= req - 1:
                text = Path(out).read_text(encoding="utf-8", errors="replace")
                return HeadscaleLogsOut(available=True, text=text[-20000:])
        except OSError:
            pass
    # свежих нет — отдаём что есть (устаревшее) или сообщаем, что помощник не стоит
    if os.path.exists(out):
        text = Path(out).read_text(encoding="utf-8", errors="replace")
        return HeadscaleLogsOut(
            available=True,
            text=text[-20000:],
            note="Логи могут быть устаревшими — хостовый помощник не ответил.",
        )
    return HeadscaleLogsOut(
        available=False,
        note="Логи headscale недоступны: не установлен хостовый помощник "
        "(systemd noderoost-hs-logs.path).",
    )


@router.get("/summary", response_model=SummaryOut)
async def summary(_: CurrentUser, session: SessionDep) -> SummaryOut:
    """Сводка «всё о сети»: версии, статус, DNS/DERP, счётчики, последний бэкап."""
    settings = get_settings()
    out = SummaryOut(
        panel_version=settings.version,
        headscale_url=settings.headscale_server_url,
    )
    cfg = _read_hs_config(settings.headscale_config_path)
    dns = cfg.get("dns") or {}
    ns = dns.get("nameservers") or {}
    global_ns = ns.get("global") if isinstance(ns, dict) else ns if isinstance(ns, list) else []
    out.magic_dns = bool(dns.get("magic_dns", False))
    out.base_domain = str(dns.get("base_domain", "") or "")
    out.nameservers = [str(x) for x in (global_ns or [])]
    out.derp_embedded = bool(((cfg.get("derp") or {}).get("server") or {}).get("enabled", False))

    if settings.headscale_api_key:
        client = get_client(settings)
        try:
            out.headscale_ok = await client.ping()
        except Exception:  # noqa: BLE001
            out.headscale_ok = False
        try:
            nodes = await client.get_nodes()
            meta = await settings_store.get_node_meta(session)
            mapped = [_map_node(n, meta) for n in nodes]
            out.nodes_total = len(mapped)
            out.servers = sum(1 for m in mapped if m.kind == "server")
            out.devices = out.nodes_total - out.servers
            out.online = sum(1 for m in mapped if m.online)
        except Exception:  # noqa: BLE001
            pass
    try:
        bks = list_backups(settings.data_dir)
        if bks:
            out.last_backup = bks[0].get("filename", "")
            out.last_backup_at = bks[0].get("created")
    except Exception:  # noqa: BLE001
        pass
    return out
