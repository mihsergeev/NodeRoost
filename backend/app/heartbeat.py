"""Heartbeat панели + self-тест канала алертов.

Раз в интервал пишем «пульс» в data/heartbeat: метку времени, какая это панель и
креды алертов + результат self-теста канала. Хостовый watchdog (dead-man's-switch,
вне docker) читает этот файл и НЕЗАВИСИМО шлёт тревогу, если пульс протух (панель/
БД мертвы) или self-тест канала не прошёл — используя те же креды из пульса."""

import asyncio
import logging
import os
import tempfile
import time
from pathlib import Path

from app import settings_store
from app.alert_http import alert_request
from app.config import Settings
from app.urlguard import telegram_url

log = logging.getLogger("noderoost.heartbeat")

HEARTBEAT_INTERVAL = 60  # сек


def _one_line(v: object) -> str:
    """Значение для файла пульса: ровно одна строка, без управляющих символов.

    Пульс читает хостовый сторож (ops/panel-watchdog.sh) построчно, первым
    совпадением grep. Перевод строки в значении дописал бы туда лишнюю пару
    ключ=значение ВЫШЕ настоящей и подменил бы адрес, куда root с хоста шлёт
    алерты вместе с токеном бота. Схема такие значения уже не пропускает, но
    сюда они приходят ещё и из переменных окружения, которые валидацию не
    проходят, - поэтому режем на месте записи.
    """
    return "".join(ch for ch in str(v or "") if ch >= " " and ch != "\x7f")


async def _self_test(cfg: dict) -> bool:
    """Проверяет канал алертов. Telegram → getMe (токен валиден + API доступен).
    Только webhook/ничего → безопасно протестировать нечем, считаем ок."""
    token, chat = cfg.get("telegram_token"), cfg.get("telegram_chat")
    if token and chat:
        try:
            url = telegram_url(cfg.get("telegram_api"), token, "getMe")
            response = await alert_request("GET", url, read_body=True)
            return response.status_code == 200 and bool(response.json().get("ok"))
        except Exception:  # noqa: BLE001
            return False
    return True


def _write_heartbeat(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, filename = tempfile.mkstemp(dir=path.parent, prefix=".heartbeat-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write("\n".join(lines) + "\n")
        os.replace(filename, path)
    finally:
        Path(filename).unlink(missing_ok=True)


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
                f"panel={_one_line(panel)}",
                f"tg_token={_one_line(cfg.get('telegram_token'))}",
                f"tg_chat={_one_line(cfg.get('telegram_chat'))}",
                f"tg_api={_one_line(cfg.get('telegram_api'))}",
                f"webhook={_one_line(cfg.get('webhook'))}",
                f"alerts_ok={1 if ok else 0}",
            ]
            _write_heartbeat(path, lines)
        except Exception:  # noqa: BLE001 — heartbeat не критичен
            log.warning("не удалось записать heartbeat", exc_info=True)
        await asyncio.sleep(HEARTBEAT_INTERVAL)
