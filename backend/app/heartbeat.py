"""Heartbeat панели + self-тест канала алертов.

Раз в интервал пишем «пульс» в data/heartbeat: метку времени, какая это панель и
креды алертов + результат self-теста канала. Хостовый watchdog (dead-man's-switch,
вне docker) читает этот файл и НЕЗАВИСИМО шлёт тревогу, если пульс протух (панель/
БД мертвы) или self-тест канала не прошёл — используя те же креды из пульса."""

import asyncio
import logging
import time
from pathlib import Path

import httpx

from app import settings_store
from app.config import Settings

log = logging.getLogger("noderoost.heartbeat")

HEARTBEAT_INTERVAL = 60  # сек


async def _self_test(cfg: dict) -> bool:
    """Проверяет канал алертов. Telegram → getMe (токен валиден + API доступен).
    Только webhook/ничего → безопасно протестировать нечем, считаем ок."""
    token, chat = cfg.get("telegram_token"), cfg.get("telegram_chat")
    if token and chat:
        base = (cfg.get("telegram_api") or "https://api.telegram.org").rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=8) as http:
                r = await http.get(f"{base}/bot{token}/getMe")
                return r.status_code == 200 and bool(r.json().get("ok"))
        except Exception:  # noqa: BLE001
            return False
    return True


async def heartbeat_loop(session_factory, settings: Settings) -> None:
    path = Path(settings.data_dir) / "heartbeat"
    panel = settings.panel_url or settings.panel_ip or "noderoost"
    while True:
        try:
            async with session_factory() as session:
                cfg = await settings_store.get_alert_config(session, settings)
            ok = await _self_test(cfg)
            lines = [
                f"ts={int(time.time())}",
                f"panel={panel}",
                f"tg_token={cfg.get('telegram_token') or ''}",
                f"tg_chat={cfg.get('telegram_chat') or ''}",
                f"tg_api={cfg.get('telegram_api') or ''}",
                f"webhook={cfg.get('webhook') or ''}",
                f"alerts_ok={1 if ok else 0}",
            ]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(lines) + "\n")
            try:
                path.chmod(0o600)  # содержит токен бота
            except OSError:
                pass
        except Exception:  # noqa: BLE001 — heartbeat не критичен
            log.warning("не удалось записать heartbeat", exc_info=True)
        await asyncio.sleep(HEARTBEAT_INTERVAL)
