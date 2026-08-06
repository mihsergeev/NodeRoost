from datetime import datetime, timedelta, timezone

import pytest

from app import collector
from app.config import Settings
from app.hs_client import HeadscaleError

NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


def _key(kid, *, age_days=30, used=False, reusable=False, expires_in_days=None):
    """Ключ в том виде, в каком его отдаёт headscale (camelCase, даты — RFC3339)."""
    k = {
        "id": str(kid),
        "used": used,
        "reusable": reusable,
        "createdAt": (NOW - timedelta(days=age_days)).isoformat().replace("+00:00", "Z"),
    }
    if expires_in_days is not None:
        exp = NOW + timedelta(days=expires_in_days)
        k["expiration"] = exp.isoformat().replace("+00:00", "Z")
    return k


def test_prune_picks_expired_and_spent():
    keys = [
        _key(1, expires_in_days=-5),            # просрочен
        _key(2, used=True),                     # использован одноразовый
        _key(3, expires_in_days=+30),           # живой, ещё пригодится
        _key(4, used=True, reusable=True, expires_in_days=+30),  # многоразовый
    ]
    assert collector._keys_to_prune(keys, set(), 7, NOW) == ["1", "2"]


def test_prune_spares_young_keys():
    # отработал, но моложе порога — ещё может понадобиться для разбора
    keys = [_key(1, age_days=2, used=True), _key(2, age_days=2, expires_in_days=-1)]
    assert collector._keys_to_prune(keys, set(), 7, NOW) == []


def test_prune_spares_keys_of_live_nodes():
    """Ключ живой ноды не трогаем ни при каком возрасте: нода на него ссылается."""
    keys = [_key(1, age_days=90, used=True), _key(2, age_days=90, used=True)]
    assert collector._keys_to_prune(keys, {"1"}, 7, NOW) == ["2"]


def test_prune_spares_keys_without_created_at():
    # неизвестный возраст — не повод удалять
    keys = [_key(1, used=True)]
    keys[0].pop("createdAt")
    assert collector._keys_to_prune(keys, set(), 7, NOW) == []


def test_prune_parses_headscale_nanosecond_timestamps():
    """Даты headscale приходят с наносекундами — их обязано разбирать, иначе
    возраст ключа неизвестен и подчистка молча ничего не делает."""
    key = {
        "id": "1",
        "used": True,
        "reusable": False,
        "expiration": "2026-06-17T01:10:54.988145Z",
        "createdAt": "2026-06-17T00:10:55.068712199Z",
    }
    assert collector._keys_to_prune([key], set(), 7, NOW) == ["1"]


class _FakeClient:
    def __init__(self, keys, fail_on=()):
        self.keys = keys
        self.fail_on = set(fail_on)
        self.deleted: list[str] = []

    async def list_preauthkeys(self):
        return self.keys

    async def delete_preauthkey(self, key_id):
        if key_id in self.fail_on:
            raise HeadscaleError("headscale 500: key in use")
        self.deleted.append(key_id)


@pytest.fixture
def fake_hs(monkeypatch):
    def _install(keys, fail_on=()):
        client = _FakeClient(keys, fail_on)
        monkeypatch.setattr(collector, "get_client", lambda _s: client)
        return client

    return _install


async def test_prune_preauthkeys_deletes_and_spares(fake_hs):
    client = fake_hs([_key(1, used=True), _key(2, expires_in_days=+30), _key(3, expires_in_days=-1)])
    # нода, зарегистрированная ключом 1 → ключ переживает подчистку
    nodes = [{"id": "7", "preAuthKey": {"id": "1"}}]
    removed = await collector._prune_preauthkeys(Settings(preauth_retention_days=7), nodes)
    assert removed == 1
    assert client.deleted == ["3"]


async def test_prune_preauthkeys_disabled(fake_hs):
    client = fake_hs([_key(1, used=True)])
    assert await collector._prune_preauthkeys(Settings(preauth_retention_days=0), []) == 0
    assert client.deleted == []


async def test_prune_preauthkeys_survives_delete_error(fake_hs):
    """Упрямый ключ не должен останавливать сметание остальных."""
    client = fake_hs([_key(1, used=True), _key(2, used=True)], fail_on=["1"])
    removed = await collector._prune_preauthkeys(Settings(preauth_retention_days=7), [])
    assert removed == 1
    assert client.deleted == ["2"]


async def _expiry_alerts(session, monkeypatch, hours_left, warn_days=14):
    """Прогнать проверку срока ключа и вернуть тексты ушедших алертов."""
    from app import alerts, settings_store
    from app.models import NodeStatus

    session.add(NodeStatus(node_id="1", name="web", online=True))
    await session.commit()

    async def fake_cfg(*a, **k):
        return {"webhook": "https://hook.example"}

    sent: list[str] = []

    async def fake_send(cfg, text, link=None):
        sent.append(text)

    monkeypatch.setattr(settings_store, "get_alert_config", fake_cfg)
    monkeypatch.setattr(alerts, "send_alert", fake_send)
    exp = datetime.now(timezone.utc) + timedelta(hours=hours_left)
    node = {"id": "1", "name": "web", "expiry": exp.isoformat().replace("+00:00", "Z")}
    await collector._check_key_expiry(
        session, Settings(key_expiry_warn_days=warn_days), [node]
    )
    return sent


async def test_key_expiry_counts_last_day_as_one(session, monkeypatch):
    """До смерти ключа 40 минут — это «через 1 дн.», а не «через 0 дн.»."""
    sent = await _expiry_alerts(session, monkeypatch, hours_left=0.7)
    assert len(sent) == 1 and "через 1 дн." in sent[0]


async def test_key_expiry_rounds_up(session, monkeypatch):
    # 47 часов — почти двое суток; обрезание вниз показывало «1 дн.»
    sent = await _expiry_alerts(session, monkeypatch, hours_left=47)
    assert "через 2 дн." in sent[0]


async def test_key_expiry_silent_beyond_threshold(session, monkeypatch):
    # 14 суток и ещё 5 часов — порог в 14 дней ещё не перейдён
    assert await _expiry_alerts(session, monkeypatch, hours_left=14 * 24 + 5) == []


async def test_collect_once_runs_to_the_end(session_factory, monkeypatch):
    """Цикл сбора обязан доходить до конца.

    Он делает всё подряд — метрики, алерты, имена, подчистку ключей, самоисцеление
    ACL, — и падение посередине тихо отменяет ВСЁ, что стоит ниже: снаружи это
    выглядит как «часть панели просто не работает». Именно так и случилось: в
    коллекторе вызывался `certs.forget`, а импорта не было, и каждый цикл умирал
    на нём, унося с собой одобрение заказанных маршрутов, подчистку ключей и
    пересборку политики. Тест держит весь проход, а не отдельные его куски.
    """
    from app import settings_store
    from app.models import Certificate

    class _Client:
        async def get_nodes(self):
            return [{"id": "7", "name": "srv", "givenName": "srv", "online": True}]

        async def list_preauthkeys(self):
            return []

    monkeypatch.setattr(collector, "get_client", lambda _s: _Client())
    monkeypatch.setattr(collector.dnsrecords, "sync", lambda *a, **k: _false())
    monkeypatch.setattr(collector.policy_apply, "reconcile_policy", _none)

    async with session_factory() as s:
        await settings_store.set_dns_records(s, [{"name": "nas.mesh", "node_id": "7", "cert": True}])
        s.add(Certificate(name="ушедшее.mesh", node_id="7", status="ok", cert_pem="pem"))
        await s.commit()

    total = await collector.collect_once(session_factory, Settings(headscale_api_key="k"))
    assert total == 1
    async with session_factory() as s:
        # сертификат имени, которого больше нет в панели, убран — то есть проход
        # дошёл до этого места и пошёл дальше
        assert await s.get(Certificate, "ушедшее.mesh") is None


async def _false():
    return False


async def _none(*a, **k):
    return False
