"""Свой центр сертификации панели: сертификаты для имён, которых нет наружу.

Зачем он рядом с Let's Encrypt. LE выдаёт сертификат только на имя, которое
существует в публичном DNS, и публикует его в CT-логах — то есть само
существование `nas.int.example.com` становится известно всем. Своя CA этого не
требует вовсе: имя может быть любым (`nas.mesh`, `router.lan`), домен покупать не
нужно, интернет для выпуска не нужен, лимитов нет.

Цена ровно одна: корневой сертификат надо один раз поставить на каждое
устройство, с которого ходите. Панель отдаёт его файлом и объясняет, куда его
класть.

Что важно понимать про доверие: приватный ключ этой CA лежит в базе панели.
Значит захваченная панель сможет выписать сертификат на любое имя — для
внутренней сети это приемлемо (она и так распоряжается тем, кто куда ходит), но
про это надо знать. Ключ уезжает в бэкап вместе с остальными секретами сети.

Ключ самого сертификата, как и с Let's Encrypt, генерит нода: сюда приходит
только CSR.
"""

import logging
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from sqlalchemy.ext.asyncio import AsyncSession

from app import settings_store

log = logging.getLogger("noderoost.ca")

CA_KEY = "ca_key"  # приватный ключ корневого сертификата (секрет)
CA_CERT = "ca_cert"  # сам корневой сертификат (не секрет, его и раздаём)

# Корень живёт долго: его ставят руками на каждое устройство, и делать это раз в
# год никто не будет. Сертификаты сервисов — наоборот, короткие: их продлевает
# панель сама, и короткий срок ограничивает ущерб от утёкшего ключа ноды.
ROOT_DAYS = 3650
LEAF_DAYS = 90


def _now() -> datetime:
    return datetime.now(timezone.utc)


def build_root(name: str) -> tuple[str, str]:
    """Создать корневой сертификат. Возвращает (ключ PEM, сертификат PEM)."""
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, name),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "NodeRoost"),
        ]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(_now() - timedelta(minutes=5))  # запас на разъехавшиеся часы
        .not_valid_after(_now() + timedelta(days=ROOT_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_cert_sign=True,
                crl_sign=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False
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


async def ensure_root(session: AsyncSession, name: str = "NodeRoost internal CA") -> str:
    """Корневой сертификат: создаётся при первом обращении, дальше берётся из базы.

    Пересоздавать его нельзя молча: все выданные сертификаты перестанут быть
    доверенными, а корень придётся заново раскладывать по устройствам.
    """
    cert = await settings_store.get_raw(session, CA_CERT)
    key = await settings_store.get_raw(session, CA_KEY)
    if cert and key:
        return cert
    key_pem, cert_pem = build_root(name)
    await settings_store.set_raw(session, CA_KEY, key_pem)
    await settings_store.set_raw(session, CA_CERT, cert_pem)
    log.info("создан корневой сертификат панели (%s)", name)
    return cert_pem


async def root_cert(session: AsyncSession) -> str:
    """Корневой сертификат, если он уже есть (иначе пусто — CA не заводили)."""
    return await settings_store.get_raw(session, CA_CERT) or ""


def root_info(cert_pem: str) -> dict:
    """Что показать администратору о корне: до какого числа и отпечаток.

    Отпечаток нужен не для красоты: по нему сверяют, что на устройство поставили
    именно тот корень, а не что-то похожее.
    """
    if not cert_pem:
        return {}
    cert = x509.load_pem_x509_certificate(cert_pem.encode())
    fp = cert.fingerprint(hashes.SHA256()).hex().upper()
    return {
        "subject": cert.subject.rfc4514_string(),
        "not_after": cert.not_valid_after_utc.date().isoformat(),
        "fingerprint": ":".join(fp[i : i + 2] for i in range(0, len(fp), 2)),
    }


async def sign_csr(session: AsyncSession, name: str, csr_der: bytes) -> str:
    """Подписать CSR ноды своим корнем. Возвращает PEM: сертификат + корень.

    Отдаём цепочкой, потому что сервис на ноде обычно отдаёт клиенту ровно то, что
    лежит в его файле: без корня в цепочке браузер, у которого корень уже
    установлен, справится, а вот `curl --cacert` и прочие клиенты — не всегда.
    """
    await ensure_root(session)
    key_pem = await settings_store.get_raw(session, CA_KEY) or ""
    cert_pem = await settings_store.get_raw(session, CA_CERT) or ""
    ca_key = serialization.load_pem_private_key(key_pem.encode(), password=None)
    ca_cert = x509.load_pem_x509_certificate(cert_pem.encode())

    csr = x509.load_der_x509_csr(csr_der)
    if not csr.is_signature_valid:
        raise ValueError("подпись CSR неверна")

    leaf = (
        x509.CertificateBuilder()
        .subject_name(
            x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
        )
        .issuer_name(ca_cert.subject)
        .public_key(csr.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(_now() - timedelta(minutes=5))
        .not_valid_after(_now() + timedelta(days=LEAF_DAYS))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(name)]), critical=False
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([x509.ObjectIdentifier("1.3.6.1.5.5.7.3.1")]),
            critical=False,  # serverAuth: браузеры без него сертификат не примут
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    return leaf.public_bytes(serialization.Encoding.PEM).decode() + cert_pem
