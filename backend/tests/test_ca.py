"""Свой центр сертификации панели: что он подписывает и чему верит браузер."""

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app import ca, certs, settings_store
from app.config import Settings
from tests.test_certs import _csr


def test_root_is_a_usable_ca():
    key_pem, cert_pem = ca.build_root("Тестовый корень")
    cert = x509.load_pem_x509_certificate(cert_pem.encode())
    bc = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
    assert bc.ca is True
    # path_length=0: этим корнем можно подписать сертификат сервиса, но не
    # промежуточную CA — незачем, а возможностей у утёкшего ключа меньше
    assert bc.path_length == 0
    ku = cert.extensions.get_extension_for_class(x509.KeyUsage).value
    assert ku.key_cert_sign is True
    assert cert.issuer == cert.subject  # самоподписанный
    # корень живёт долго: его ставят руками на каждое устройство
    assert (cert.not_valid_after_utc - cert.not_valid_before_utc).days > 3000
    assert "BEGIN PRIVATE KEY" in key_pem


async def test_signed_leaf_chains_to_the_root(session):
    await ca.ensure_root(session, "NodeRoost тест")
    chain = await ca.sign_csr(session, "nas.mesh", _csr("nas.mesh"))

    leaf = x509.load_pem_x509_certificate(chain.encode())
    root_pem = await ca.root_cert(session)
    root = x509.load_pem_x509_certificate(root_pem.encode())

    # подпись листа проверяется публичным ключом корня — иначе цепочка не цепочка
    root.public_key().verify(
        leaf.signature, leaf.tbs_certificate_bytes, ec.ECDSA(hashes.SHA256())
    )
    assert leaf.issuer == root.subject
    san = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert san.get_values_for_type(x509.DNSName) == ["nas.mesh"]
    eku = leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    # без serverAuth браузер сертификат не примет, каким бы он ни был правильным
    assert x509.oid.ExtendedKeyUsageOID.SERVER_AUTH in eku
    assert leaf.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is False
    # корень в файл не кладём: он весит два десятка килобайт (список запрещённых
    # доменов), а клиенту, которому его поставили, он в рукопожатии не нужен
    assert chain.count("BEGIN CERTIFICATE") == 1


async def test_root_is_created_once(session):
    first = await ca.ensure_root(session)
    assert await ca.ensure_root(session) == first  # иначе все выданные протухнут разом


async def test_leaf_is_short_lived(session):
    chain = await ca.sign_csr(session, "nas.mesh", _csr("nas.mesh"))
    leaf = x509.load_pem_x509_certificate(chain.encode())
    days = (leaf.not_valid_after_utc - leaf.not_valid_before_utc).days
    assert 80 <= days <= 95  # продлевает панель сама, длинный срок тут ни к чему
async def test_rotate_replaces_the_root(session):
    first = await ca.ensure_root(session)
    second = await ca.rotate(session, 20)
    assert second != first
    # новый корень сразу подписывает имена в любом внутреннем домене
    chain = await ca.sign_csr(session, "router.lan", _csr("router.lan"))
    root = x509.load_pem_x509_certificate(second.encode())
    leaf = x509.load_pem_x509_certificate(chain.encode())
    root.public_key().verify(
        leaf.signature, leaf.tbs_certificate_bytes, ec.ECDSA(hashes.SHA256())
    )


async def test_fingerprint_matches_openssl_form(session):
    pem = await ca.ensure_root(session)
    cert = x509.load_pem_x509_certificate(pem.encode())
    # ровно то, что нода считает через `openssl x509 -fingerprint -sha256`
    assert ca.fingerprint(pem) == cert.fingerprint(hashes.SHA256()).hex()
    assert ca.fingerprint("") == ""


def test_root_lives_two_decades_by_default():
    """Корень ставят руками по ноутбукам и телефонам. Короткий срок здесь — не
    безопасность, а обещание обойти все машины заново через несколько лет."""
    _, cert_pem = ca.build_root("Тест")
    cert = x509.load_pem_x509_certificate(cert_pem.encode())
    years = (cert.not_valid_after_utc - cert.not_valid_before_utc).days / 365
    assert 19 < years < 21


def test_public_domains_are_refused_by_the_root():
    """Корень стоит в доверенных на всех машинах, поэтому вопрос «что он может
    подписать» — это вопрос «что подделает тот, кто получит панель». Настоящие
    домены он не подписывает, и клиент это проверяет сам: у OpenSSL это
    «excluded subtree violation».
    """
    _, cert_pem = ca.build_root("Тест")
    cert = x509.load_pem_x509_certificate(cert_pem.encode())
    nc = cert.extensions.get_extension_for_class(x509.NameConstraints)
    assert nc.critical is True  # некритичное клиент вправе не соблюдать
    assert nc.value.permitted_subtrees is None  # разрешено всё, кроме запрещённого
    blocked = {d.value for d in nc.value.excluded_subtrees if isinstance(d, x509.DNSName)}
    for real in ("com", "ru", "org", "io", "dev", "zip"):
        assert real in blocked
    # и IP: тип имени, не упомянутый в ограничениях, RFC 5280 считает свободным
    assert sum(1 for d in nc.value.excluded_subtrees if isinstance(d, x509.IPAddress)) == 2


def test_invented_domains_stay_free():
    """Ради этого всё и затевалось: новый проект не должен требовать ни
    перевыпуска корня, ни обхода устройств. Его домена нет в интернете — значит
    он и не запрещён, и работает сразу."""
    for name in ("loki.mirabah", "portainer-dev.bironex", "nas.mesh", "router.lan"):
        assert ca.signable(name)
    for name in ("www.google.com", "sberbank.ru", "internal.zip"):
        assert not ca.signable(name)


async def test_public_domain_is_refused_with_a_reason(session):
    await ca.ensure_root(session)
    try:
        await ca.sign_csr(session, "mail.google.com", _csr("mail.google.com"))
    except ValueError as e:
        assert ".com" in str(e)  # сказано, что именно не так
    else:
        raise AssertionError("подписал публичный домен")


async def test_old_root_with_an_allow_list_still_works(session):
    """Корень, выпущенный панелью прошлых версий, разрешал перечисленные домены.
    Такой корень уже стоит на устройствах, поэтому он обязан продолжать работать —
    и подсказывать, что перевыпуск снимет ограничение навсегда."""
    key_pem, cert_pem = _legacy_root(["mesh"])
    await settings_store.set_raw(session, ca.CA_KEY, key_pem)
    await settings_store.set_raw(session, ca.CA_CERT, cert_pem)
    assert (await ca.sign_csr(session, "nas.mesh", _csr("nas.mesh"))).count("CERTIFICATE") == 2
    try:
        await ca.sign_csr(session, "loki.mirabah", _csr("loki.mirabah"))
    except ValueError as e:
        assert "Выпустите корень заново" in str(e)
    else:
        raise AssertionError("старый корень подписал имя вне своего списка")


def _legacy_root(allowed):
    """Корень старого образца: РАЗРЕШАЮЩИЙ список доменов."""
    import datetime

    key = ec.generate_private_key(ec.SECP256R1())
    sub = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "старый корень")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(sub)
        .issuer_name(sub)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.NameConstraints(
                permitted_subtrees=[x509.DNSName(a) for a in allowed],
                excluded_subtrees=None,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    return (
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
        cert.public_bytes(serialization.Encoding.PEM).decode(),
    )
