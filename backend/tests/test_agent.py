from app import agent
from tests.conftest import ADMIN_PASSWORD


def test_state_body_has_use_exit():
    s = agent.state_body(["10.0.0.0/24"], False, "100.100.0.3")
    assert "routes=10.0.0.0/24" in s
    assert "exit=false" in s
    assert "use_exit=100.100.0.3" in s


def test_state_body_empty_use_exit_by_default():
    s = agent.state_body([], True)
    assert "exit=true" in s
    assert "use_exit=\n" in s  # пусто = не форсим


def test_apply_script_sets_exit_node_from_use_exit():
    """Агент должен ставить --exit-node из use_exit (принудительный выход) и
    снимать его, когда пусто. Это exit-node, а НЕ advertise-routes."""
    setup = agent.build_setup("https://hs.example/agent/tok")
    assert "USE_EXIT=" in setup
    assert 'tailscale set --exit-node="\\$USE_EXIT" --exit-node-allow-lan-access' in setup
    assert "tailscale set --exit-node=" in setup  # ветка снятия


def test_apply_script_preserves_public_inbound_with_connmark():
    """При принудительном выходе агент ставит connmark-правила, чтобы сервер
    оставался доступен по внешнему IP (ответы входящих — мимо exit, в main)."""
    setup = agent.build_setup("https://hs.example/agent/tok")
    assert "CONNMARK --set-mark" in setup
    assert "CONNMARK --restore-mark" in setup
    assert "ip rule add fwmark" in setup and "table main priority 5200" in setup
    # снятие агента чистит эти правила
    remove = agent.build_remove()
    assert "ip rule del fwmark" in remove
    assert "iptables -t mangle -D" in remove


def test_apply_script_reports_applied_state_hash():
    """Агент обязан подтверждать ПРИМЕНЕНИЕ, а не факт запроса: иначе ноде хватает
    дёргать свой URL, чтобы панель считала агента работающим, ничего не применяя."""
    setup = agent.build_setup("https://hs.example/agent/tok")
    assert "sha256sum" in setup
    assert "/applied?h=" in setup
    # подтверждение идёт ПОСЛЕ переноса состояния (то есть после успешного apply)
    assert setup.index(r'mv "\$TMP.core"') < setup.index("/applied?h=")


def test_state_hash_matches_what_the_agent_hashes():
    """Панель сверяет отчёт с хешем ТОГО ЖЕ текста, который агент кладёт в файл."""
    import hashlib

    body = agent.state_body(["10.0.0.0/24"], False, "100.100.0.4")
    assert hashlib.sha256(body.encode()).hexdigest()  # тот же вход, что и у sha256sum
    assert body.endswith("\n") and body.count("\n") == 3


def test_agent_explains_a_deleted_node():
    """Ноду удалили — агент должен сказать это словами, а не сыпать curl-ошибками.

    Раньше в журнал машины каждую минуту падало «curl: (22) The requested URL
    returned error: 404», и по этой строке нельзя понять ни что случилось, ни что
    с этим делать.
    """
    setup = agent.build_setup("https://hs.example/agent/tok")
    assert "404" in setup
    assert "панель больше не знает этот узел" in setup
    assert "/remove | sh" in setup           # готовая команда, чтобы убрать агента


def test_agent_does_not_start_tailscaled_behind_the_admin():
    """Остановленный демон — решение владельца машины, а не повод его поднять.

    С `Wants=tailscaled.service` systemd поднимал демона каждый раз, когда
    срабатывал таймер агента: админ останавливал VPN и через минуту находил его
    снова запущенным.
    """
    setup = agent.build_setup("https://hs.example/agent/tok")
    assert "After=tailscaled.service" in setup      # порядок сохраняем
    assert "Wants=tailscaled.service" not in setup  # а поднимать не наше дело
    assert "systemctl is-active --quiet tailscaled" in setup


async def test_agent_change_is_written_to_the_audit_log(client, monkeypatch):
    """Смена маршрутов ноды — изменение того, что она объявляет ВСЕЙ сети.

    Это была единственная правка в панели, не оставлявшая следа в журнале.
    """
    from app.api import agent as api_agent

    async def fake_routes(session, node_id, cfg):
        return list(cfg.get("routes") or [])

    monkeypatch.setattr(api_agent, "_wanted_routes", fake_routes)

    r = await client.post("/api/auth/login",
                          json={"username": "admin", "password": ADMIN_PASSWORD})
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    resp = await client.put("/api/agent/7",
                            json={"routes": ["10.9.0.0/24"], "exit_node": True},
                            headers=h)
    assert resp.status_code == 200

    log = (await client.get("/api/logs/audit?limit=5", headers=h)).json()
    entry = next((e for e in log if e["action"] == "agent_set"), None)
    assert entry is not None, log
    assert entry["target"] == "7" and "10.9.0.0/24" in entry["detail"]


def test_certificates_do_not_enter_the_state_hash():
    """Строка cert= меняется на каждый выпуск (нет сертификата → есть → продлён).
    Попади она в состояние — панель показывала бы «агент отстал» ровно тогда,
    когда он как раз отработал."""
    assert "cert=" not in agent.state_body(["10.0.0.0/24"], False)
    setup = agent.build_setup("https://hs.example/agent/tok")
    assert "grep -v '^cert='" in setup
    # состояние обрезается ДО сравнения, иначе выпуск выглядел бы как правка
    assert setup.index("grep -v '^cert='") < setup.index(r'cmp -s "\$TMP.core"')


def test_cert_line_format():
    nl = chr(10)
    assert agent.cert_lines([("nas.example.com", "abc123", True)]) == (
        "cert=nas.example.com|abc123|1" + nl
    )
    # сертификата ещё нет — отпечаток пустой, CSR не нужен (ждём паузы после отказа)
    assert agent.cert_lines([("x.example.com", "", False)]) == "cert=x.example.com||0" + nl
    assert agent.cert_lines([]) == ""


def test_key_stays_on_the_node():
    """Панель не должна получать приватный ключ: агент шлёт CSR, а ключ у него."""
    setup = agent.build_setup("https://hs.example/agent/tok")
    assert "openssl req -new -key" in setup and "/csr?name=" in setup
    # ключ никуда не отправляется — единственное, что уходит наверх, это CSR
    assert r'--data-binary @"\$TMP.csr"' in setup
    assert ".key" in setup and "curl" in setup
    for line in setup.splitlines():
        if "curl" in line and "--data-binary" in line:
            assert ".csr" in line and ".key" not in line
