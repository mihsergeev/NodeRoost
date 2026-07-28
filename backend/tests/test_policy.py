from types import SimpleNamespace

from app import policy_apply, settings_store
from app.api.policy import DEFAULT_POLICY
from tests.conftest import ADMIN_PASSWORD


class _FakeClient:
    """Мини-headscale для теста reconcile: отдаёт заданные ноды, копит пуши."""

    def __init__(self, nodes):
        self.nodes = nodes
        self.pushed: list[str] = []

    async def get_nodes(self):
        return self.nodes

    async def set_policy(self, policy: str):
        self.pushed.append(policy)

    async def get_policy(self):
        # как headscale: отдаёт то, что в нём сейчас лежит. Самоисцеление сверяется
        # именно с этим, а не с тем, что панель помнит о своих пушах
        if not self.pushed:
            raise RuntimeError("acl policy not found")
        return {"policy": self.pushed[-1]}


def _node(nid: str, ip: str) -> dict:
    return {"id": nid, "ipAddresses": [ip]}


async def _login(client):
    r = await client.post(
        "/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD}
    )
    return r.json()["access_token"]


def test_default_policy_has_acls():
    assert '"acls"' in DEFAULT_POLICY
    assert "accept" in DEFAULT_POLICY


async def test_policy_requires_auth(client):
    assert (await client.get("/api/policy")).status_code == 401
    r = await client.put("/api/policy", json={"policy": "{}"})
    assert r.status_code == 401


async def test_policy_503_without_key(client):
    token = await _login(client)
    r = await client.get("/api/policy", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 503


async def test_reconcile_policy_self_heals_on_node_change(monkeypatch):
    """reconcile_policy пушит политику только при изменении набора нод/правил, и при
    удалении ноды её литеральный IP выпадает из ACL (защита от переиспользования IP)."""
    policy_apply._last_pushed = None  # изолируем от других тестов
    rule = {
        "src": {"kind": "node", "value": "11"},
        "dst": {"kind": "servers", "value": ""},
        "ports": "22",
    }

    async def fake_rules(_s):
        return [rule]

    async def fake_meta(_s):
        return {"10": {"kind": "server"}}

    async def fake_routing(_s):
        return {}

    monkeypatch.setattr(settings_store, "get_acl_rules", fake_rules)
    monkeypatch.setattr(settings_store, "get_node_meta", fake_meta)
    # направления тоже попадают в политику синтетикой — тест гоняет build_policy
    # с session=None, так что хранилище должно быть подменено целиком
    monkeypatch.setattr(settings_store, "get_routing", fake_routing)

    st = SimpleNamespace(default_user="default")
    # server-1 (id10, .1 = сервер) + доверенная нода id11 на .2
    client = _FakeClient([_node("10", "100.100.0.1"), _node("11", "100.100.0.2")])

    # первый прогон — политика ещё не пушилась → пушим, в ACL есть 100.100.0.2
    assert await policy_apply.reconcile_policy(None, client, st) is True
    assert len(client.pushed) == 1
    assert "100.100.0.2" in client.pushed[-1]

    # без изменений — повторно НЕ пушим
    assert await policy_apply.reconcile_policy(None, client, st) is False
    assert len(client.pushed) == 1

    # нода id11 (.2) исчезла (удалена/истекла) → политика меняется, перепушиваем,
    # и её IP больше НЕ фигурирует в ACL — узел, занявший .2, ничего не унаследует
    client.nodes = [_node("10", "100.100.0.1")]
    assert await policy_apply.reconcile_policy(None, client, st) is True
    assert len(client.pushed) == 2
    assert "100.100.0.2" not in client.pushed[-1]


async def test_reconcile_repairs_a_policy_changed_behind_the_panel(monkeypatch):
    """Кэш «что мы пушили» знает только про наши пуши. Если политику подменили в
    обход панели (headscale CLI, чужой API-ключ, оборванный пуш) — а подменённая
    вполне может оказаться ШИРЕ, — самоисцеление обязано это увидеть и починить."""
    from types import SimpleNamespace

    from app import policy_apply, settings_store

    async def none(_s):
        return {}

    async def rules(_s):
        return []

    monkeypatch.setattr(settings_store, "get_acl_rules", rules)
    monkeypatch.setattr(settings_store, "get_node_meta", none)
    monkeypatch.setattr(settings_store, "get_routing", none)
    monkeypatch.setattr(settings_store, "get_agent_all", none)

    st = SimpleNamespace(default_user="default")
    client = _FakeClient([_node("10", "100.100.0.1")])
    assert await policy_apply.reconcile_policy(None, client, st) is True  # первый пуш
    assert await policy_apply.reconcile_policy(None, client, st) is False  # нет расхождений

    # кто-то подменил живую политику на «разрешено всё», панель об этом не знает
    client.pushed.append('{"acls": [{"action": "accept", "src": ["*"], "dst": ["*:*"]}]}')
    assert await policy_apply.reconcile_policy(None, client, st) is True
    assert '"*:*"' not in client.pushed[-1]  # вернули свою, узкую


async def test_auto_approve_revokes_only_what_the_panel_approved(monkeypatch, tmp_path):
    """Отзыв должен отзывать: убрали заказ — снимаем СВОЁ одобрение, а ручное
    одобрение админа не трогаем. Заодно покрываем сам путь: он ходит в несколько
    модулей, и опечатка в импорте раньше всплывала только в проде."""
    from types import SimpleNamespace

    from app import collector, settings_store

    approved_calls = []

    class C:
        async def approve_routes(self, nid, routes):
            approved_calls.append((nid, list(routes)))

    monkeypatch.setattr(collector, "get_client", lambda s: C())
    store = {"agents": {"5": {"routes": [], "panel_approved": ["10.1.0.0/24"]}}, "routing": {}}

    class Sess:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    monkeypatch.setattr(settings_store, "get_agent_all", lambda s: _async(store["agents"]))
    monkeypatch.setattr(settings_store, "get_routing", lambda s: _async(store["routing"]))
    monkeypatch.setattr(settings_store, "set_agent_all", lambda s, d: _async(None))

    node = {"id": "5", "availableRoutes": ["10.1.0.0/24", "10.9.9.0/24"],
            "approvedRoutes": ["10.1.0.0/24", "192.168.5.0/24"]}
    await collector._auto_approve_requested(lambda: Sess(), SimpleNamespace(), [node])
    assert approved_calls, "одобрения не пересчитаны"
    got = set(approved_calls[-1][1])
    assert "10.1.0.0/24" not in got, "своё устаревшее одобрение должно сниматься"
    assert "192.168.5.0/24" in got, "ручное одобрение админа трогать нельзя"


async def _async(v):
    return v
