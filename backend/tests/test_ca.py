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
    # в ответе идёт и корень: клиентам вроде curl --cacert нужна вся цепочка
    assert chain.count("BEGIN CERTIFICATE") == 2


async def test_root_is_created_once(session):
    first = await ca.ensure_root(session)
    assert await ca.ensure_root(session) == first  # иначе все выданные протухнут разом


async def test_leaf_is_short_lived(session):
    chain = await ca.sign_csr(session, "nas.mesh", _csr("nas.mesh"))
    leaf = x509.load_pem_x509_certificate(chain.encode())
    days = (leaf.not_valid_after_utc - leaf.not_valid_before_utc).days
    assert 80 <= days <= 95  # продлевает панель сама, длинный срок тут ни к чему


async def test_key_never_leaves_the_node(session):
    """Панель подписывает CSR и не видит приватного ключа сервиса."""
    key = ec.generate_private_key(ec.SECP256R1())
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "nas.mesh")]))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("nas.mesh")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    chain = await ca.sign_csr(
        session, "nas.mesh", csr.public_bytes(serialization.Encoding.DER)
    )
    leaf = x509.load_pem_x509_certificate(chain.encode())
    # выданный сертификат про НАШ ключ: панель его не подменила и не сгенерила свой
    assert leaf.public_key().public_numbers() == key.public_key().public_numbers()
    assert "PRIVATE KEY" not in chain


def test_fingerprint_is_shown_for_checking():
    _, cert_pem = ca.build_root("Отпечаток")
    info = ca.root_info(cert_pem)
    assert info["fingerprint"].count(":") == 31  # sha256 парами, как показывает ОС
    assert info["not_after"] and info["subject"]
    assert ca.root_info("") == {}


async def test_issuer_comes_from_the_record(session):
    await settings_store.set_dns_records(
        session,
        [
            {"name": "nas.mesh", "node_id": "7", "cert": True, "issuer": "ca"},
            {"name": "site.example.com", "node_id": "7", "cert": True},
        ],
    )
    assert await certs.issuer_of(session, "nas.mesh") == "ca"
    # запись без поля — это Let's Encrypt: так было до появления своей CA
    assert await certs.issuer_of(session, "site.example.com") == "le"
    assert await certs.issuer_of(session, "нет-такого") == "le"


async def test_own_ca_issues_without_internet(session):
    """Ни DNS, ни 80-го порта, ни интернета: имя может быть каким угодно."""
    row = await certs.issue(
        session, Settings(), "nas.mesh", "7", _csr("nas.mesh"), issuer="ca"
    )
    assert row.status == "ok", row.error
    assert row.not_after is not None
    assert "BEGIN CERTIFICATE" in row.cert_pem
