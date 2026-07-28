"""Фоновый автобэкап: периодически пишет архив состояния в data/backups + self-тест."""

import asyncio
import logging

from app import alerts, backup, settings_store
from app.config import Settings

log = logging.getLogger("noderoost.autobackup")

# состояние прошлого автобэкапа — алертим только на переходах ok↔проблема
_last_ok = True


async def backup_loop(session_factory, settings: Settings) -> None:
    await asyncio.sleep(30)  # не блокируем старт
    global _last_ok
    while True:
        try:
            async with session_factory() as session:
                cfg = await settings_store.get_backup_config(session, settings)
            interval = cfg["interval_hours"]
            keep = cfg["keep"]
            if interval <= 0:
                await asyncio.sleep(3600)  # выключено — ждём час и перечитываем
                continue

            problems: list[str] = []
            exc: Exception | None = None
            try:
                path, problems = await backup.write_backup(
                    session_factory, settings, keep
                )
            except Exception as e:  # noqa: BLE001 — цикл не должен падать
                exc = e
                log.exception("ошибка автобэкапа")

            ok = exc is None and not problems
            if ok:
                log.info("автобэкап записан, self-тест пройден: %s", path)
                if not _last_ok:
                    _last_ok = True
                    await alerts.maybe_alert(
                        session_factory, settings,
                        "✅ NodeRoost: автобэкап снова проходит (self-тест ок).",
                    )
            elif _last_ok:
                _last_ok = False
                if exc is not None:
                    msg = (
                        f"❌ NodeRoost: автобэкап НЕ создан — {type(exc).__name__}: "
                        f"{exc}. Проверьте место на диске и логи backend."
                    )
                else:
                    msg = (
                        "⚠️ NodeRoost: бэкап создан, но self-тест НЕ пройден — "
                        + "; ".join(problems)
                        + ". Копия может быть непригодна."
                    )
                log.error("проблема с бэкапом: %s", msg)
                await alerts.maybe_alert(session_factory, settings, msg)

            await asyncio.sleep(interval * 3600)
        except Exception:  # noqa: BLE001
            log.exception("ошибка цикла автобэкапа")
            await asyncio.sleep(3600)
