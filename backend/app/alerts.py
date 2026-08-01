"""Алерты (Telegram / вебхук): security-события + падение/восстановление нод."""

import html
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import settings_store
from app.config import Settings
from app.models import NodeStatus

log = logging.getLogger("noderoost.alerts")

NL = chr(10)  # перевод строки в тексте алерта

# антидребезг: сколько подряд циклов нода наблюдалась офлайн, пока её статус ещё
# подтверждён онлайн (сбрасывается при онлайне/подтверждении падения)
_down_streak: dict[str, int] = {}

# Самоконтроль: состояние control-сервера между циклами коллектора.
# None = ещё не наблюдали (первый замер только фиксирует, не будит).
_hs_up: bool | None = None
_hs_fail_streak = 0

# Ноды, о молчащем агенте которых уже отчитались. Сбрасывается на рестарте
# процесса — после старта всё ещё мёртвый агент напомнит о себе один раз.
_agent_silent: set[str] = set()


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def alerts_enabled(cfg: dict) -> bool:
    return bool(
        (cfg.get("telegram_token") and cfg.get("telegram_chat"))
        or cfg.get("webhook")
    )


def _tg_html(text: str, link: str) -> str:
    """HTML-версия для Telegram: слово «NodeRoost» → кликабельная ссылка на панель."""
    safe = html.escape(text)
    anchor = f'<a href="{html.escape(link, quote=True)}">NodeRoost</a>'
    return safe.replace("NodeRoost", anchor, 1)


def _redact(text: str, *secrets: str) -> str:
    for s in secrets:
        if s and len(s) >= 8:
            text = text.replace(s, "***")
    return text


def _err_text(exc: Exception, *secrets: str) -> str:
    """Короткое описание ошибки БЕЗ секретов.

    httpx кладёт полный URL запроса в текст HTTPStatusError («... for url
    'https://api.telegram.org/bot<ТОКЕН>/sendMessage'»), а URL вебхука сам по себе
    секрет. Такой текст уходил в ответ /api/alerts/test, в audit_log и в логи
    контейнера — поэтому строим сообщение сами и дополнительно затираем секреты.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        body = (exc.response.text or "").strip()[:200]
        msg = f"HTTP {exc.response.status_code}" + (f": {body}" if body else "")
    else:
        msg = f"{type(exc).__name__}: {exc}"
    return _redact(msg, *secrets)


async def send_alert(cfg: dict, text: str, link: str | None = None) -> list[str]:
    """Шлёт текст во все настроенные каналы. link (URL панели) — если задан, в
    Telegram слово «NodeRoost» станет ссылкой (HTML parse mode); вебхуку уходит
    plain text + отдельное поле link. Возвращает список ошибок (пустой = ок)."""
    errors: list[str] = []
    token, chat = cfg.get("telegram_token"), cfg.get("telegram_chat")
    if token and chat:
        # базовый адрес Bot API настраиваемый: для регионов, где api.telegram.org
        # заблокирован, можно указать зеркало/прокси (напр. https://api-tg.example.com)
        base = (cfg.get("telegram_api") or "https://api.telegram.org").rstrip("/")
        url = f"{base}/bot{token}/sendMessage"
        payload: dict = {"chat_id": chat, "disable_web_page_preview": True}
        if link:
            payload["text"] = _tg_html(text, link)
            payload["parse_mode"] = "HTML"
        else:
            payload["text"] = text
        try:
            async with httpx.AsyncClient(timeout=10) as http:
                r = await http.post(url, json=payload)
                r.raise_for_status()
        except Exception as exc:  # noqa: BLE001 — алерт не должен ронять цикл
            msg = _err_text(exc, token, url, base)
            errors.append(f"Telegram: {msg}")
            log.warning("Telegram-алерт не отправлен: %s", msg)

    if cfg.get("webhook"):
        try:
            body: dict = {"text": text}
            if link:
                body["link"] = link
            async with httpx.AsyncClient(timeout=10) as http:
                r = await http.post(cfg["webhook"], json=body)
                r.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            msg = _err_text(exc, cfg["webhook"])
            errors.append(f"Webhook: {msg}")
            log.warning("Вебхук-алерт не отправлен: %s", msg)
    return errors


async def maybe_alert(session_factory, settings: Settings, text: str) -> None:
    """Разовый алерт: открывает свою сессию, шлёт если каналы настроены. Best-effort."""
    try:
        async with session_factory() as session:
            cfg = await settings_store.get_alert_config(session, settings)
        if alerts_enabled(cfg):
            await send_alert(cfg, text)
    except Exception:  # noqa: BLE001 — алерт не должен ронять вызывающий цикл
        log.warning("разовый алерт не отправлен", exc_info=True)


async def security_alert(session: AsyncSession, settings: Settings, text: str) -> None:
    """Шлёт security-событие (брутфорс, смена пароля) во все настроенные каналы.
    Best-effort — не роняет вызывающую операцию."""
    try:
        cfg = await settings_store.get_alert_config(session, settings)
        if alerts_enabled(cfg):
            await send_alert(cfg, text)
    except Exception:  # noqa: BLE001
        log.warning("security-алерт не отправлен", exc_info=True)


def _plural_ru(n: int, one: str, few: str, many: str) -> str:
    """Русская форма слова по числу: 1 сервер, 2 сервера, 5 серверов."""
    m = abs(n) % 100
    if 11 <= m <= 14:
        return many
    d = m % 10
    if d == 1:
        return one
    if 2 <= d <= 4:
        return few
    return many


def _fmt_group(node_names: list[str], online: bool) -> str:
    """Одно сгруппированное сообщение о падении/восстановлении серверов.
    Слово «NodeRoost» станет ссылкой в send_alert (см. link)."""
    icon = "✅" if online else "🔥"
    verb = "снова онлайн" if online else "офлайн"
    if len(node_names) == 1:
        return f"{icon} NodeRoost: сервер «{node_names[0]}» {verb}"
    word = _plural_ru(len(node_names), "сервер", "сервера", "серверов")
    return f"{icon} NodeRoost: {len(node_names)} {word} {verb}: " + ", ".join(node_names)


async def reconcile_selfcheck(
    session: AsyncSession, settings: Settings, ok: bool
) -> str | None:
    """Самоконтроль панели: алерт, когда control-сервер headscale упал/поднялся.

    Это САМЫЙ важный алерт: пока headscale лежит, коллектор вообще не может
    опрашивать ноды — значит и алертов «нода офлайн» не будет, и молчание легко
    принять за «всё хорошо». Раньше падение только писалось в лог.

    Антидребезг тот же, что у нод (node_down_misses): единичный таймаут при
    рестарте headscale (панель сама его перезапускает при смене DNS/сети) — не
    повод будить. Восстановление сообщаем сразу. Возвращает текст отправленного
    алерта или None.
    """
    global _hs_up, _hs_fail_streak
    misses = max(1, settings.node_down_misses)

    if ok:
        _hs_fail_streak = 0
        if _hs_up is False:  # был упавшим → восстановился
            _hs_up = True
            text = "🟢 NodeRoost: control-сервер headscale снова доступен"
        else:
            _hs_up = True
            return None
    else:
        _hs_fail_streak += 1
        if _hs_up is False or _hs_fail_streak < misses:
            return None  # уже отчитались / ещё дребезг
        _hs_up = False
        text = (
            "🔥 NodeRoost: control-сервер headscale недоступен — ноды не "
            "опрашиваются, статусы устарели"
        )

    cfg = await settings_store.get_alert_config(session, settings)
    if not alerts_enabled(cfg):
        return None
    await send_alert(cfg, text, settings.panel_url or None)
    return text


async def reconcile_agents(
    session: AsyncSession,
    settings: Settings,
    agents: dict,
    names: dict[str, str],
    online: dict[str, bool],
    muted: set[str] | None = None,
) -> list[str]:
    """Алерт о том, что агент на ноде перестал забирать настройки из панели.

    Зачем отдельный алерт: агент при недоступности панели выходит с кодом 0 и
    ничего не пишет — снаружи это неотличимо от исправной работы. Ровно так один
    из них однажды простоял мёртвым сутки, и заметили это случайно. Единственный
    надёжный признак — давность последнего опроса, панель её и так хранит.

    Алертим ТОЛЬКО тех, кто хоть раз отзывался и потом замолчал: агент, который
    просто не ставили, и так показан в панели как «не установлен» — это не
    инцидент, а незаконченная настройка. Оффлайновые ноды пропускаем: там уже
    сработал алерт о падении ноды, и второе сообщение о том же событии — шум.

    Возвращает id нод, по которым отправлен алерт (для тестов).
    """
    minutes = settings.agent_silent_minutes
    if minutes <= 0:
        return []
    now = datetime.now(timezone.utc)
    silent, back = [], []
    for nid, cfg in (agents or {}).items():
        last = _parse_ts(cfg.get("last_poll"))
        if last is None:  # ни разу не отзывался — агента просто не ставили
            _agent_silent.discard(nid)
            continue
        if nid in (muted or set()):  # «не беспокоить» по этой ноде
            _agent_silent.discard(nid)
            continue
        if nid not in names:  # ноду удалили (в т.ч. мимо панели) — молчать не о ком
            _agent_silent.discard(nid)
            continue
        if not online.get(nid, True):  # нода лежит — про это уже отчитались
            continue
        quiet_min = int((now - last).total_seconds() // 60)
        if quiet_min > minutes:
            if nid not in _agent_silent:
                _agent_silent.add(nid)
                silent.append((names.get(nid, nid), quiet_min))
        elif nid in _agent_silent:
            _agent_silent.discard(nid)
            back.append(names.get(nid, nid))

    if not silent and not back:
        return []
    cfg_alerts = await settings_store.get_alert_config(session, settings)
    if not alerts_enabled(cfg_alerts):
        return []
    link = settings.panel_url or None
    # Сообщение должно читаться человеком, который открыл телефон и не помнит, что
    # такое агент: сначала ЧТО происходит, потом ЧЕМ это грозит и чем НЕ грозит, в
    # конце — что проверить. Прежний текст («агент молчит, маршруты не доезжают»)
    # понятен только тому, кто и так знает устройство панели.
    for name, quiet_min in silent:
        await send_alert(
            cfg_alerts,
            f"🟠 NodeRoost: сервер «{name}» не забирает настройки из панели." + NL
            + f"Сам он на связи, а его агент молчит уже {quiet_min} мин "
            "(обычно приходит раз в минуту). Пока так — новые маршруты и режим "
            "шлюза выхода на нём не применятся; доступы и соединения работают." + NL
            + "Проверить на сервере: systemctl status noderoost-agent.timer",
            link,
        )
    for name in back:
        await send_alert(
            cfg_alerts,
            f"✅ NodeRoost: сервер «{name}» снова забирает настройки из панели",
            link,
        )
    return [name for name, _ in silent] + back


async def reconcile_nodes(
    session: AsyncSession,
    settings: Settings,
    online_map: dict[str, bool],
    names: dict[str, str],
    kinds: dict[str, str] | None = None,
    muted: set[str] | None = None,
) -> list[tuple[str, bool]]:
    """Сверяет текущий online/offline с сохранённым, шлёт алерты на переходах.

    Первое наблюдение ноды статус фиксирует, но не алертит. Падение объявляется
    только после N подряд офлайн-наблюдений (антидребезг); восстановление —
    сразу. Алертим ТОЛЬКО серверы (kind=='server'): пользовательские устройства
    гасят на ночь — это шум. Переходы одного цикла группируем в одно сообщение
    на падение и одно на восстановление, с ссылкой на панель. Возвращает список
    переходов (node_id, online_now) — по всем нодам, для истории/тестов.
    """
    now = datetime.now(timezone.utc)
    misses = max(1, settings.node_down_misses)
    known = {s.node_id: s for s in await session.scalars(select(NodeStatus))}
    transitions: list[tuple[str, bool]] = []
    for nid, online in online_map.items():
        prev = known.get(nid)
        name = names.get(nid, nid)
        if prev is None:
            session.add(
                NodeStatus(node_id=nid, name=name, online=online, changed_at=now)
            )
            _down_streak.pop(nid, None)
            continue
        prev.name = name  # держим имя актуальным
        if online:
            _down_streak.pop(nid, None)
            if not prev.online:
                prev.online = True
                prev.changed_at = now
                transitions.append((nid, True))
        elif prev.online:
            _down_streak[nid] = _down_streak.get(nid, 0) + 1
            if _down_streak[nid] >= misses:
                prev.online = False
                prev.changed_at = now
                transitions.append((nid, False))
                _down_streak.pop(nid, None)
    # чистим статусы удалённых нод
    for nid in list(known):
        if nid not in online_map:
            await session.delete(known[nid])
            _down_streak.pop(nid, None)
    await session.commit()

    if not transitions:
        return transitions
    cfg = await settings_store.get_alert_config(session, settings)
    if not alerts_enabled(cfg):
        return transitions
    # только серверы; kind неизвестен (None) → считаем сервером (алертим, безопасно)
    km = kinds or {}
    mt = muted or set()
    # Заглушённая нода ОСТАЁТСЯ под наблюдением: статус в панели меняется, история
    # пишется — молчит только уведомление. Иначе на плановых работах пришлось бы
    # выбирать между шумом и слепотой.
    down = [
        names.get(nid, nid)
        for nid, on in transitions
        if not on and km.get(nid, "server") == "server" and nid not in mt
    ]
    up = [
        names.get(nid, nid)
        for nid, on in transitions
        if on and km.get(nid, "server") == "server" and nid not in mt
    ]
    base = settings.panel_url or None

    def _link(ids: list[str]) -> str | None:
        """Одна нода в алерте — ведём сразу в её карточку, а не на список.
        Панель ловит #node-<id> при загрузке (frontend/src/App.tsx)."""
        if base and len(ids) == 1:
            return f"{base.rstrip('/')}/#node-{ids[0]}"
        return base

    down_ids = [nid for nid, on in transitions
                if not on and km.get(nid, "server") == "server" and nid not in mt]
    up_ids = [nid for nid, on in transitions
              if on and km.get(nid, "server") == "server" and nid not in mt]
    if down:
        await send_alert(cfg, _fmt_group(down, False), _link(down_ids))
    if up:
        await send_alert(cfg, _fmt_group(up, True), _link(up_ids))
    return transitions
