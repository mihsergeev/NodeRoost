"""Простой in-memory лимитер: попытки входа (брутфорс) и публичные эндпоинты.

Панель работает одним процессом uvicorn, поэтому общего хранилища не нужно.
Ключ — обычно IP клиента. Окно и лимит подобраны консервативно; при
превышении — временная блокировка. Сбрасывается при рестарте (приемлемо).
"""

import time

WINDOW = 300  # сек: окно подсчёта неудачных попыток
MAX_FAILURES = 10  # неудач в окне до блокировки
LOCKOUT = 900  # сек: длительность блокировки после превышения

# Публичные роуты по токену (/agent/*): доступны из любой точки
# интернета и на КАЖДЫЙ запрос читают настройки из БД — в том числе когда токен
# неизвестен. Лимит на источник, чтобы это нельзя было раскачать. Легальный
# агент ходит раз в минуту, так что даже десяток нод за одним NAT сюда не упрётся.
PUBLIC_WINDOW = 60
PUBLIC_MAX = 60
# Верхняя граница числа отслеживаемых источников: сам счётчик не должен стать
# способом съесть память панели запросами с рандомных адресов.
_MAX_KEYS = 10_000

_failures: dict[str, list[float]] = {}
_hits: dict[str, list[float]] = {}


def too_many(
    key: str,
    *,
    limit: int = PUBLIC_MAX,
    window: int = PUBLIC_WINDOW,
    now: float | None = None,
) -> bool:
    """Регистрирует обращение и говорит, превышен ли лимит (скользящее окно)."""
    t = _now(now)
    if len(_hits) > _MAX_KEYS:
        for k in [k for k, v in _hits.items() if not v or t - v[-1] > window]:
            _hits.pop(k, None)
    times = [ts for ts in _hits.get(key, []) if t - ts < window]
    times.append(t)
    _hits[key] = times
    return len(times) > limit


def _now(now: float | None) -> float:
    return time.time() if now is None else now


def is_locked(key: str, *, now: float | None = None) -> bool:
    t = _now(now)
    times = [ts for ts in _failures.get(key, []) if t - ts < LOCKOUT]
    if times:
        _failures[key] = times
    else:
        _failures.pop(key, None)
    recent = [ts for ts in times if t - ts < WINDOW]
    # блокируем, если за окно набралось >= MAX_FAILURES и последняя ещё «свежая»
    return len(recent) >= MAX_FAILURES


def record_failure(key: str, *, now: float | None = None) -> bool:
    """Регистрирует неудачную попытку. Возвращает True, если ИМЕННО эта попытка
    перевела ключ в состояние блокировки (для однократного алерта)."""
    t = _now(now)
    times = [ts for ts in _failures.get(key, []) if t - ts < LOCKOUT]
    times.append(t)
    _failures[key] = times
    recent = [ts for ts in times if t - ts < WINDOW]
    return len(recent) == MAX_FAILURES


def clear(key: str) -> None:
    _failures.pop(key, None)
