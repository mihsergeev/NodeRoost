"""Фоновый автобэкап: периодически пишет архив состояния в data/backups + self-тест."""

import asyncio
import logging

from app import alerts, backup, settings_store
from app.config import Settings

log = logging.getLogger("noderoost.autobackup")

# состояние прошлого автобэкапа — алертим только на переходах ok↔проблема
_last_ok = True
_config_changed = asyncio.Event()
_START_DELAY = 30


def configuration_changed() -> None:
    _config_changed.set()


async def _wait_for_config(seconds: float) -> None:
    try:
        await asyncio.wait_for(_config_changed.wait(), timeout=seconds)
    except TimeoutError:
        pass


async def backup_loop(session_factory, settings: Settings) -> None:
    await asyncio.sleep(_START_DELAY)
    global _last_ok
    loop = asyncio.get_running_loop()
    last_attempt: float | None = None
    while True:
        _config_changed.clear()
        try:
            async with session_factory() as session:
                cfg = await settings_store.get_backup_config(session, settings)
            interval = cfg["interval_hours"]
            keep = cfg["keep"]
            if interval <= 0:
                last_attempt = None
                await _wait_for_config(3600)
                continue
            if last_attempt is not None:
                delay = last_attempt + interval * 3600 - loop.time()
                if delay > 0:
                    await _wait_for_config(min(delay, 3600))
                    continue

            problems: list[str] = []
            exc: Exception | None = None
            try:
                path, _, problems = await backup.write_backup(
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
                        "⚠️ NodeRoost: бэкап не сохранён, self-тест НЕ пройден — "
                        + "; ".join(problems)
                        + "."
                    )
                log.error("проблема с бэкапом: %s", msg)
                await alerts.maybe_alert(session_factory, settings, msg)

            last_attempt = loop.time()
        except Exception:  # noqa: BLE001
            log.exception("ошибка цикла автобэкапа")
            await _wait_for_config(3600)
