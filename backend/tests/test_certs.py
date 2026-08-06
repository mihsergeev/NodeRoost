"""Сертификаты имён внутри сети: что панель отдаёт ноде и чего не отдаёт никому."""

from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app import certs, settings_store
from app.config import Settings
from app.models import Certificate
from tests.conftest import ADMIN_PASSWORD


def _csr(*names: str) -> bytes:
    key = ec.generate_private_key(ec.SECP256R1())
    builder = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(
            x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, names[0])])
        )
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(n) for n in names]), critical=False
        )
    )
    return builder.sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.DER)


def test_csr_names_reads_cn_and_san():
    assert certs.csr_names(_csr("nas.example.com")) == {"nas.example.com"}
    both = certs.csr_names(_csr("a.example.com", "b.example.com"))
    assert both == {"a.example.com", "b.example.com"}


async def test_a_csr_for_another_name_is_refused(session):
    """Иначе владелец ноды выписывал бы сертификаты на чужие имена сети."""
    import pytest

    with pytest.raises(ValueError):
        await certs.issue(
            session, Settings(), "nas.example.com", "7", _csr("чужое.example.com")
        )


async def test_wanted_lists_only_this_nodes_names(session):
    await settings_store.set_dns_records(
        session,
        [
            {"name": "a.example.com", "node_id": "7", "cert": True},
            {"name": "b.example.com", "node_id": "8", "cert": True},  # чужая нода
            {"name": "c.example.com", "node_id": "7"},  # без сертификата
            {"name": "d.example.com", "ip": "10.0.0.1", "cert": True},  # без ноды
        ],
    )
    wanted = await certs.wanted_for_node(session, Settings(), "7")
    assert [n for n, _, _ in wanted] == ["a.example.com"]
    # сертификата ещё нет — отпечаток пуст, CSR нужен
    assert wanted[0][1] == "" and wanted[0][2] is True


def test_needs_renewal_covers_the_whole_life():
    now = datetime.now(timezone.utc)
    assert certs.needs_renewal(None, 30) is True  # сертификата нет вовсе
    fresh = Certificate(
        name="x", status="ok", cert_pem="pem", not_after=now + timedelta(days=60)
    )
    assert certs.needs_renewal(fresh, 30) is False
    soon = Certificate(
        name="x", status="ok", cert_pem="pem", not_after=now + timedelta(days=5)
    )
    assert certs.needs_renewal(soon, 30) is True
    # после отказа держим паузу: у Let's Encrypt лимит на неудачные проверки
    waiting = Certificate(name="x", status="error", retry_after=now + timedelta(minutes=10))
    assert certs.needs_renewal(waiting, 30) is False
    passed = Certificate(name="x", status="error", retry_after=now - timedelta(minutes=1))
    assert certs.needs_renewal(passed, 30) is True


async def test_challenge_answer_is_stored_and_removed(session):
    from app.certs import _save_challenges

    await _save_challenges(session, {"tok": "tok.thumb"})
    assert await certs.answer_for(session, "tok") == "tok.thumb"
    assert await certs.answer_for(session, "другой") is None


async def test_account_key_is_created_once(session):
    first = await certs.account_key(session)
    assert "BEGIN PRIVATE KEY" in first
    assert await certs.account_key(session) == first  # новый ключ = новый аккаунт


async def test_forget_drops_names_that_are_gone(session):
    session.add(Certificate(name="a.example.com", cert_pem="pem", status="ok"))
    session.add(Certificate(name="b.example.com", cert_pem="pem", status="ok"))
    await session.commit()
    await certs.forget(session, {"a.example.com"})
    assert [c.name for c in await certs.all_certs(session)] == ["a.example.com"]


# --- публичные ручки агента ------------------------------------------------


async def test_challenge_endpoint_answers_only_a_known_token(client):
    """Ручка публичная по замыслу: сюда приходит проверяющий Let's Encrypt, и
    единственный секрет здесь — сам токен."""
    from app.certs import _save_challenges

    app = client._transport.app
    async with app.state.session_factory() as s:
        await _save_challenges(s, {"tok": "tok.thumb"})
    r = await client.get("/.well-known/acme-challenge/tok")
    assert r.status_code == 200 and r.text == "tok.thumb"
    assert (await client.get("/.well-known/acme-challenge/чужой")).status_code == 404


async def test_node_cannot_ask_for_a_name_that_is_not_its_own(client):
    app = client._transport.app
    async with app.state.session_factory() as s:
        await settings_store.set_agent_all(s, {"7": {"token": "tok7"}})
        await settings_store.set_dns_records(
            s, [{"name": "other-node.example.com", "node_id": "8", "cert": True}]
        )
    r = await client.post(
        "/agent/tok7/csr?name=other-node.example.com", content=_csr("other-node.example.com")
    )
    assert r.status_code == 403
    # и неизвестный токен не даёт ничего
    assert (await client.get("/agent/нет-такого/cert?name=x.example.com")).status_code == 404


async def test_a_name_with_traversal_is_never_written_by_the_agent():
    """Имя приходит от панели, а файлы кладутся под root. Без проверки «имя» вида
    ../../ssl/certs/ca-certificates заставило бы агента переписать системное
    хранилище корней — то есть отдать машину тому, кто захватил панель."""
    from app import agent as agent_mod

    setup = agent_mod.build_setup("https://hs.example/agent/tok")
    assert "имя сертификата отвергнуто" in setup
    # пропускаем только то, что бывает DNS-именем: без слешей, без «..», без прочего
    assert r"''|.*|*..*|*[!a-z0-9.-]*)" in setup


async def test_a_huge_csr_is_refused(client):
    """Тело запроса приходит с чужой машины: без предела один запрос съел бы память."""
    app = client._transport.app
    async with app.state.session_factory() as s:
        await settings_store.set_agent_all(s, {"7": {"token": "tokbig"}})
        await settings_store.set_dns_records(
            s, [{"name": "big.example.com", "node_id": "7", "cert": True}]
        )
    r = await client.post(
        "/agent/tokbig/csr?name=big.example.com", content=b"x" * (32 * 1024)
    )
    assert r.status_code == 413


async def test_a_valid_certificate_is_not_reissued(session):
    """Повторные запросы не должны жечь недельный лимит Let's Encrypt на весь
    домен: пока сертификат действует, отдаём тот же самый."""
    first = await certs.issue(
        session, Settings(), "nas.mesh", "7", _csr("nas.mesh"), issuer="ca"
    )
    pem = first.cert_pem
    again = await certs.issue(
        session, Settings(), "nas.mesh", "7", _csr("nas.mesh"), issuer="ca"
    )
    assert again.cert_pem == pem  # тот же, а не новый выпуск


def test_a_csr_with_a_broken_signature_is_refused():
    """Подпись CSR доказывает владение ключом. Битую отвергаем сами, не ходя к LE."""
    import pytest

    good = _csr("nas.mesh")
    broken = bytearray(good)
    broken[-1] ^= 0xFF  # портим подпись
    with pytest.raises(ValueError):
        certs.csr_names(bytes(broken))


async def test_node_gets_the_root_and_its_fingerprint(client):
    """Нода узнаёт из состояния, какой корень должен у неё лежать, и берёт его
    по той же ручке — со своим токеном, потому что состав внутренних зон
    посторонним знать незачем."""
    from app import ca

    app = client._transport.app
    async with app.state.session_factory() as s:
        await settings_store.set_agent_all(s, {"7": {"token": "tok7"}})
        pem = await ca.ensure_root(s, for_name="nas.mesh")
        fp = ca.fingerprint(pem)

    state = await client.get("/agent/tok7")
    assert f"ca={fp}" in state.text
    root = await client.get("/agent/tok7/ca")
    assert root.status_code == 200 and "BEGIN CERTIFICATE" in root.text
    assert (await client.get("/agent/чужой-токен/ca")).status_code == 404


async def test_trust_is_withdrawn_when_the_tick_is_cleared(client):
    """Выключенная автоустановка обязана УБИРАТЬ корень с нод, а не просто
    переставать его обновлять: иначе «выключил» ничего не значит."""
    from app import ca

    app = client._transport.app
    async with app.state.session_factory() as s:
        await settings_store.set_agent_all(s, {"7": {"token": "tok7"}})
        await ca.ensure_root(s, for_name="nas.mesh")
        await ca.set_auto_install(s, False)

    state = await client.get("/agent/tok7")
    assert "ca=" not in state.text  # пусто в состоянии = агент снимает корень


async def test_rotation_reorders_the_certificates(client):
    """Перевыпуск корня обесценивает подписанное им. Панель обязана забыть эти
    сертификаты, иначе нода осталась бы с бумагами от корня, которому больше
    никто не верит, и без единого повода их обновить."""
    from app import ca

    app = client._transport.app
    async with app.state.session_factory() as s:
        await settings_store.set_dns_records(
            s, [{"name": "nas.mesh", "node_id": "7", "cert": True, "issuer": "ca"}]
        )
        await ca.ensure_root(s, for_name="nas.mesh")
        await certs.issue(s, Settings(), "nas.mesh", "7", _csr("nas.mesh"), issuer="ca")
        assert (await s.get(Certificate, "nas.mesh")).status == "ok"

    login = await client.post(
        "/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD}
    )
    r = await client.put(
        "/api/ca",
        json={"auto": True, "rotate_suffixes": ["mesh", "lan"]},
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )
    assert r.status_code == 200
    assert r.json()["suffixes"] == ["mesh", "lan"]
    async with app.state.session_factory() as s:
        assert await s.get(Certificate, "nas.mesh") is None  # закажется заново
