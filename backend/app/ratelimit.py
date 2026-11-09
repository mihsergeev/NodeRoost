"""Простой in-memory лимитер: попытки входа (брутфорс) и публичные эндпоинты.

Панель работает одним процессом uvicorn, поэтому общего хранилища не нужно.
Ключ — обычно IP клиента. Окно и лимит подобраны консервативно; при
превышении — временная блокировка. Сбрасывается при рестарте (приемлемо).
"""

import time
from collections import OrderedDict, deque

WINDOW = 300  # сек: окно подсчёта неудачных попыток
MAX_FAILURES = 10  # неудач в окне до блокировки
# Сек: сколько держится блокировка после превышения. Отсчитывается от последней
# неудачи, а не от окна подсчёта: раньше решение принималось по WINDOW, и через
# 5 минут блокировка снималась сама - получалось 10 попыток на каждые 5 минут
# вместо обещанных 15.
LOCKOUT = 900

# Публичные роуты по токену (/agent/*): доступны из любой точки
# интернета и на КАЖДЫЙ запрос читают настройки из БД — в том числе когда токен
# неизвестен. Лимит на источник, чтобы это нельзя было раскачать. Легальный
# агент ходит раз в минуту, так что даже десяток нод за одним NAT сюда не упрётся.
PUBLIC_WINDOW = 60
PUBLIC_MAX = 60
# Верхняя граница числа отслеживаемых источников: сам счётчик не должен стать
# способом съесть память панели запросами с рандомных адресов.
_MAX_KEYS = 10_000

_failures: OrderedDict[str, deque[float]] = OrderedDict()
_hits: OrderedDict[str, deque[float]] = OrderedDict()


def _make_room(entries: OrderedDict[str, deque[float]], cutoff: float) -> bool:
    # Порядок - по последней принятой попытке. Отказы не продлевают окно.
    while entries and next(iter(entries.values()))[-1] <= cutoff:
        entries.popitem(last=False)
    return len(entries) < _MAX_KEYS


def _expire(times: deque[float], cutoff: float) -> None:
    while times and times[0] <= cutoff:
        times.popleft()


def too_many(
    key: str,
    *,
    limit: int = PUBLIC_MAX,
    window: int = PUBLIC_WINDOW,
    now: float | None = None,
) -> bool:
    """Регистрирует обращение и говорит, превышен ли лимит (скользящее окно)."""
    if limit <= 0:
        return True
    t = _now(now)
    times = _hits.get(key)
    if times is None:
        if not _make_room(_hits, t - window):
            return True
        times = _hits[key] = deque()
    _expire(times, t - window)
    if len(times) >= limit:
        return True
    times.append(t)
    _hits.move_to_end(key)
    return False


def _now(now: float | None) -> float:
    return time.monotonic() if now is None else now


def is_locked(key: str, *, now: float | None = None) -> bool:
    t = _now(now)
    times = _failures.get(key)
    if times is None:
        return not _make_room(_failures, t - LOCKOUT)
    _expire(times, t - LOCKOUT)
    if not times:
        _failures.pop(key)
    # блокируем, пока в пределах LOCKOUT набрано >= MAX_FAILURES неудач
    return len(times) >= MAX_FAILURES


def record_failure(key: str, *, now: float | None = None) -> bool:
    """Регистрирует неудачную попытку. Возвращает True, если ИМЕННО эта попытка
    перевела ключ в состояние блокировки (для однократного алерта)."""
    t = _now(now)
    times = _failures.get(key)
    if times is None:
        if not _make_room(_failures, t - LOCKOUT):
            return False
        times = _failures[key] = deque()
    _expire(times, t - LOCKOUT)
    if len(times) >= MAX_FAILURES:
        return False
    times.append(t)
    _failures.move_to_end(key)
    return len(times) == MAX_FAILURES and t - times[0] < WINDOW


def clear(key: str) -> None:
    _failures.pop(key, None)
