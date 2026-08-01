import httpx

from app import alerts
from app.config import Settings
from tests.conftest import ADMIN_PASSWORD


def test_err_text_hides_telegram_token():
    """httpx кладёт полный URL (с токеном бота) в текст HTTPStatusError, а он
    уходил в ответ /api/alerts/test, в audit_log и в логи. Токена быть не должно."""
    token = "123456:AAHsecretTOKENvalue"
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    req = httpx.Request("POST", url)
    resp = httpx.Response(400, text='{"description":"chat not found"}', request=req)
    exc = httpx.HTTPStatusError(f"Client error for url '{url}'", request=req, response=resp)

    out = alerts._err_text(exc, token, url)
    assert token not in out and "AAHsecret" not in out
    assert "400" in out and "chat not found" in out  # диагностика сохранена


def test_err_text_hides_webhook_url():
    hook = "https://hooks.example.com/services/T000/B000/XXXXsecretXXXX"
    exc = RuntimeError(f"connection failed for {hook}")
    out = alerts._err_text(exc, hook)
    assert hook not in out and "XXXXsecret" not in out
    assert "RuntimeError" in out


async def test_reconcile_first_down_recovery(session):
    alerts._down_streak.clear()
    st = Settings(node_down_misses=2)
    # первое наблюдение — фиксируем статус, но НЕ алертим
    assert await alerts.reconcile_nodes(session, st, {"1": True}, {"1": "a"}) == []
    # 1-й офлайн — дебаунс, ещё не «упала»
    assert await alerts.reconcile_nodes(session, st, {"1": False}, {"1": "a"}) == []
    # 2-й офлайн подряд — объявляем падение
    assert await alerts.reconcile_nodes(session, st, {"1": False}, {"1": "a"}) == [
        ("1", False)
    ]
    # снова онлайн — восстановление сразу
    assert await alerts.reconcile_nodes(session, st, {"1": True}, {"1": "a"}) == [
        ("1", True)
    ]


async def test_reconcile_servers_only_grouped(session, monkeypatch):
    from app import settings_store

    alerts._down_streak.clear()
    st = Settings(node_down_misses=1, panel_url="https://panel.example")

    async def fake_cfg(*a, **k):
        return {"webhook": "https://hook.example"}

    monkeypatch.setattr(settings_store, "get_alert_config", fake_cfg)
    sent: list[tuple[str, str | None]] = []

    async def fake_send(cfg, text, link=None):
        sent.append((text, link))
        return []

    monkeypatch.setattr(alerts, "send_alert", fake_send)

    names = {"s1": "server-1", "s2": "server-2", "d1": "laptop"}
    kinds = {"s1": "server", "s2": "server", "d1": "device"}
    # первое наблюдение — онлайн, фиксируем
    await alerts.reconcile_nodes(
        session, st, {"s1": True, "s2": True, "d1": True}, names, kinds
    )
    sent.clear()
    # 2 сервера + 1 устройство падают в одном цикле (misses=1 → сразу)
    await alerts.reconcile_nodes(
        session, st, {"s1": False, "s2": False, "d1": False}, names, kinds
    )
    # одно сгруппированное сообщение, только серверы, устройство пропущено, ссылка — параметром
    assert len(sent) == 1
    text, link = sent[0]
    assert "2 сервера офлайн" in text
    assert "server-1" in text and "server-2" in text and "laptop" not in text
    assert link == "https://panel.example"


def test_tg_html_link():
    out = alerts._tg_html("🔴 NodeRoost: сервер «a» офлайн", "https://p.example")
    assert '<a href="https://p.example">NodeRoost</a>' in out
    assert out.startswith("🔴 <a href=")


async def test_reconcile_removes_gone_node(session):
    alerts._down_streak.clear()
    st = Settings()
    await alerts.reconcile_nodes(session, st, {"9": True}, {"9": "x"})
    # ноды больше нет в тайлнете → статус удаляется, переходов нет
    assert await alerts.reconcile_nodes(session, st, {}, {}) == []
    from app.models import NodeStatus
    from sqlalchemy import select

    assert list(await session.scalars(select(NodeStatus))) == []


async def _login(client):
    r = await client.post(
        "/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD}
    )
    return r.json()["access_token"]


async def test_alerts_endpoints(client):
    token = await _login(client)
    h = {"Authorization": f"Bearer {token}"}
    r = await client.get("/api/alerts", headers=h)
    assert r.status_code == 200 and r.json()["enabled"] is False
    # тест без настроенных каналов → 400
    r = await client.post("/api/alerts/test", headers=h)
    assert r.status_code == 400


async def test_alerts_requires_auth(client):
    assert (await client.get("/api/alerts")).status_code == 401


async def test_selfcheck_debounces_and_reports_recovery(session, monkeypatch):
    """Падение control-сервера объявляем только после N подряд неудач (единичный
    таймаут при рестарте headscale — не повод будить), восстановление — сразу."""
    sent: list[str] = []

    async def fake_cfg(_s, _st):
        return {"telegram_token": "t", "telegram_chat": "c"}

    async def fake_send(_cfg, text, link=None):
        sent.append(text)
        return []

    monkeypatch.setattr(alerts.settings_store, "get_alert_config", fake_cfg)
    monkeypatch.setattr(alerts, "send_alert", fake_send)
    alerts._hs_up = None
    alerts._hs_fail_streak = 0
    st = Settings(node_down_misses=2)

    # первое наблюдение (живой) — фиксируем, молчим
    assert await alerts.reconcile_selfcheck(session, st, True) is None
    # первая неудача — ещё дребезг
    assert await alerts.reconcile_selfcheck(session, st, False) is None
    # вторая подряд — объявляем падение
    assert "недоступен" in (await alerts.reconcile_selfcheck(session, st, False) or "")
    # пока лежит — не спамим
    assert await alerts.reconcile_selfcheck(session, st, False) is None
    # поднялся — сообщаем сразу
    assert "снова доступен" in (await alerts.reconcile_selfcheck(session, st, True) or "")
    # и больше не повторяем
    assert await alerts.reconcile_selfcheck(session, st, True) is None
    assert len(sent) == 2


async def test_reconcile_agents_silent_and_recovery(session, monkeypatch):
    """Агент, который отзывался и замолчал, должен породить ровно один алерт —
    и ровно один при возвращении. Никогда не ставившийся агент и агент на
    упавшей ноде не алертятся вовсе."""
    from datetime import datetime, timedelta, timezone

    sent: list[str] = []

    async def fake_cfg(*a, **k):
        return {"webhook": "https://hook.example"}

    async def fake_send(cfg, text, link=None):
        sent.append(text)
        return []

    monkeypatch.setattr(alerts.settings_store, "get_alert_config", fake_cfg)
    monkeypatch.setattr(alerts, "send_alert", fake_send)
    alerts._agent_silent.clear()
    st = Settings(agent_silent_minutes=10)

    now = datetime.now(timezone.utc)
    fresh = (now - timedelta(minutes=1)).isoformat()
    stale = (now - timedelta(minutes=30)).isoformat()
    names = {"1": "srv-1", "2": "srv-2", "3": "srv-3"}

    # свежий опрос — тихо; агента не ставили (last_poll нет) — тоже тихо
    agents = {"1": {"last_poll": fresh}, "2": {}}
    assert await alerts.reconcile_agents(session, st, agents, names, {"1": True, "2": True}) == []

    # нода лежит — молчание агента объясняется падением, второй алерт был бы шумом
    agents = {"3": {"last_poll": stale}}
    assert await alerts.reconcile_agents(session, st, agents, names, {"3": False}) == []

    # нода жива, а агент замолчал — вот это инцидент
    assert await alerts.reconcile_agents(session, st, agents, names, {"3": True}) == ["srv-3"]
    # повторно не спамим
    assert await alerts.reconcile_agents(session, st, agents, names, {"3": True}) == []

    # снова отзывается — сообщаем один раз
    agents = {"3": {"last_poll": fresh}}
    assert await alerts.reconcile_agents(session, st, agents, names, {"3": True}) == ["srv-3"]
    assert await alerts.reconcile_agents(session, st, agents, names, {"3": True}) == []

    assert len(sent) == 2
    # текст должен читаться человеком, который открыл телефон и не помнит, что
    # такое агент: что случилось, чем грозит, что проверить
    assert "не забирает настройки" in sent[0]
    assert "30 мин" in sent[0]                       # сколько именно молчит
    assert "доступы и соединения работают" in sent[0]
    assert "noderoost-agent.timer" in sent[0]        # что проверить на сервере
    assert "снова забирает настройки" in sent[1]


async def test_reconcile_agents_disabled(session, monkeypatch):
    """agent_silent_minutes=0 полностью выключает проверку."""
    alerts._agent_silent.clear()
    agents = {"1": {"last_poll": "2020-01-01T00:00:00+00:00"}}
    st = Settings(agent_silent_minutes=0)
    assert await alerts.reconcile_agents(session, st, agents, {"1": "a"}, {"1": True}) == []


async def test_muted_node_does_not_alert_but_is_still_tracked(session, monkeypatch):
    """«Не беспокоить» глушит уведомление, а НЕ наблюдение: переход всё равно
    фиксируется и возвращается — иначе панель показывала бы устаревший статус."""
    from app import settings_store

    sent: list[str] = []

    async def fake_cfg(*a, **k):
        return {"webhook": "https://hook.example"}

    async def fake_send(cfg, text, link=None):
        sent.append(text)
        return []

    monkeypatch.setattr(settings_store, "get_alert_config", fake_cfg)
    monkeypatch.setattr(alerts, "send_alert", fake_send)
    alerts._down_streak.clear()
    st = Settings(node_down_misses=1)
    names, kinds = {"1": "srv-1"}, {"1": "server"}

    await alerts.reconcile_nodes(session, st, {"1": True}, names, kinds, {"1"})
    # нода упала — переход зафиксирован...
    assert await alerts.reconcile_nodes(session, st, {"1": False}, names, kinds, {"1"}) == [
        ("1", False)
    ]
    # ...но наружу не ушло ничего
    assert sent == []


async def test_unmuted_node_alerts_as_before(session, monkeypatch):
    from app import settings_store

    sent: list[str] = []

    async def fake_cfg(*a, **k):
        return {"webhook": "https://hook.example"}

    async def fake_send(cfg, text, link=None):
        sent.append(text)
        return []

    monkeypatch.setattr(settings_store, "get_alert_config", fake_cfg)
    monkeypatch.setattr(alerts, "send_alert", fake_send)
    alerts._down_streak.clear()
    st = Settings(node_down_misses=1)
    names, kinds = {"2": "srv-2"}, {"2": "server"}

    await alerts.reconcile_nodes(session, st, {"2": True}, names, kinds, set())
    await alerts.reconcile_nodes(session, st, {"2": False}, names, kinds, set())
    assert len(sent) == 1 and "srv-2" in sent[0]


async def test_muted_agent_silence_is_not_reported(session, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app import settings_store

    async def fake_cfg(*a, **k):
        return {"webhook": "https://hook.example"}

    monkeypatch.setattr(settings_store, "get_alert_config", fake_cfg)
    monkeypatch.setattr(alerts, "send_alert", lambda *a, **k: None)
    alerts._agent_silent.clear()
    st = Settings(agent_silent_minutes=10)
    stale = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    agents = {"1": {"last_poll": stale}}
    assert (
        await alerts.reconcile_agents(session, st, agents, {"1": "a"}, {"1": True}, {"1"})
        == []
    )


async def test_single_node_alert_links_to_that_node(session, monkeypatch):
    """Алерт про ОДНУ ноду ведёт в её карточку, а не на общий список: искать
    упавшую ноду глазами в тревоге — лишний шаг. И иконка падения — огонёк."""
    from app import settings_store

    alerts._down_streak.clear()
    st = Settings(node_down_misses=1, panel_url="https://panel.example/")

    async def fake_cfg(*a, **k):
        return {"webhook": "https://hook.example"}

    monkeypatch.setattr(settings_store, "get_alert_config", fake_cfg)
    sent: list[tuple[str, str | None]] = []

    async def fake_send(cfg, text, link=None):
        sent.append((text, link))
        return []

    monkeypatch.setattr(alerts, "send_alert", fake_send)

    names, kinds = {"7": "db-1", "9": "db-2"}, {"7": "server", "9": "server"}
    await alerts.reconcile_nodes(session, st, {"7": True, "9": True}, names, kinds)
    sent.clear()

    await alerts.reconcile_nodes(session, st, {"7": False, "9": True}, names, kinds)
    text, link = sent[-1]
    assert text.startswith("🔥"), text
    assert link == "https://panel.example/#node-7"

    # упали двое разом — одной карточки мало, ведём на список
    pair = {"11": "app-1", "12": "app-2"}
    kinds2 = {"11": "server", "12": "server"}
    await alerts.reconcile_nodes(session, st, {"11": True, "12": True}, pair, kinds2)
    sent.clear()
    await alerts.reconcile_nodes(session, st, {"11": False, "12": False}, pair, kinds2)
    assert sent[-1][1] == "https://panel.example/"
