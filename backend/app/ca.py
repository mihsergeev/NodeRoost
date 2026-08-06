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

import ipaddress
import json
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
CA_SUFFIXES = "ca_suffixes"  # какие имена корню разрешено подписывать
CA_AUTO = "ca_auto"  # ставить ли корень на ноды автоматически (агентом)

# Корень живёт долго: его ставят руками на каждое устройство, и делать это раз в
# год никто не будет. Сертификаты сервисов — наоборот, короткие: их продлевает
# панель сама, и короткий срок ограничивает ущерб от утёкшего ключа ноды.
ROOT_DAYS = 3650
LEAF_DAYS = 90


def _now() -> datetime:
    return datetime.now(timezone.utc)


def parent_suffix(name: str) -> str:
    """Суффикс, под которым живёт имя: nas.mesh → mesh, a.b.example.com → b.example.com.

    По нему панель предлагает ограничение корня, когда создаёт его под первое имя.
    """
    return name.split(".", 1)[1] if "." in name else name


def covered(name: str, suffixes: list[str]) -> bool:
    """Разрешает ли ограничение корня подписать это имя.

    Пустой список = ограничений нет (корень всевластен) — так выглядят корни,
    созданные до появления этой проверки.
    """
    if not suffixes:
        return True
    return any(name == s or name.endswith("." + s) for s in suffixes)


def build_root(name: str, suffixes: list[str] | None = None) -> tuple[str, str]:
    """Создать корневой сертификат. Возвращает (ключ PEM, сертификат PEM).

    `suffixes` — какие имена этому корню позволено подписывать (X.509 Name
    Constraints). Это главное, что удерживает автоустановку корня на каждую ноду в
    разумных рамках: корень, стоящий в системном хранилище машины, доверяют для
    ЛЮБОГО имени, и без ограничения захваченная панель выписала бы сертификат на
    чужой публичный домен и подменила бы его для ваших же машин. С ограничением её
    власть кончается там, где кончается ваша внутренняя зона. IP-адреса запрещаем
    отдельно: имя-тип, не упомянутый в permitted, RFC 5280 считает неограниченным.
    """
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, name),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "NodeRoost"),
        ]
    )
    builder = (
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
    )
    if suffixes:
        builder = builder.add_extension(
            x509.NameConstraints(
                permitted_subtrees=[x509.DNSName(s) for s in suffixes],
                excluded_subtrees=[
                    x509.IPAddress(ipaddress.IPv4Network("0.0.0.0/0")),
                    x509.IPAddress(ipaddress.IPv6Network("::/0")),
                ],
            ),
            critical=True,  # некритичное ограничение клиент вправе не соблюдать
        )
    cert = builder.sign(key, hashes.SHA256())
    return (
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
        cert.public_bytes(serialization.Encoding.PEM).decode(),
    )


async def suffixes(session: AsyncSession) -> list[str]:
    """Имена, которые разрешено подписывать корню (пусто = ограничений нет)."""
    raw = await settings_store.get_raw(session, CA_SUFFIXES)
    try:
        return [str(s) for s in json.loads(raw)] if raw else []
    except ValueError:
        return []


async def set_suffixes(session: AsyncSession, values: list[str]) -> None:
    await settings_store.set_raw(session, CA_SUFFIXES, json.dumps(values))


async def auto_install(session: AsyncSession) -> bool:
    """Ставить ли корень на ноды самим (агентом и скриптом подключения).

    По умолчанию да: имя внутри сети без доверенного сертификата — это ошибка
    браузера на каждом заходе, то есть ровно та возня, ради избавления от которой
    своя CA и заводится. Ограничение имён выше — то, что делает такую раздачу
    приемлемой.
    """
    raw = await settings_store.get_raw(session, CA_AUTO)
    return raw != "0"


async def set_auto_install(session: AsyncSession, value: bool) -> None:
    await settings_store.set_raw(session, CA_AUTO, "1" if value else "0")


async def ensure_root(
    session: AsyncSession,
    name: str = "NodeRoost internal CA",
    for_name: str = "",
) -> str:
    """Корневой сертификат: создаётся при первом обращении, дальше берётся из базы.

    Пересоздавать его нельзя молча: все выданные сертификаты перестанут быть
    доверенными, а корень придётся заново раскладывать по устройствам, — для этого
    есть `rotate()`, который зовут кнопкой.

    `for_name` — имя, ради которого корень создаётся. От него берётся ограничение
    (`nas.mesh` → корню разрешена зона `mesh`): угадать зону в момент создания
    можно только так, а спрашивать администратора о суффиксах до того, как он
    впервые захотел сертификат, — вопрос без контекста.
    """
    cert = await settings_store.get_raw(session, CA_CERT)
    key = await settings_store.get_raw(session, CA_KEY)
    if cert and key:
        return cert
    allowed = await suffixes(session)
    if not allowed and for_name:
        allowed = [parent_suffix(for_name)]
        await set_suffixes(session, allowed)
    key_pem, cert_pem = build_root(name, allowed)
    await settings_store.set_raw(session, CA_KEY, key_pem)
    await settings_store.set_raw(session, CA_CERT, cert_pem)
    log.info("создан корневой сертификат панели (%s), зоны: %s", name, allowed or "любые")
    return cert_pem


async def rotate(session: AsyncSession, allowed: list[str]) -> str:
    """Выпустить корень заново с новым списком разрешённых зон.

    Старый корень после этого бесполезен: сертификаты, им подписанные, перестают
    быть доверенными, а на устройствах нужно заменить файл. Ноды с агентом делают
    это сами (корень едет в состоянии по отпечатку), сертификаты имён панель
    перевыпускает следующим же опросом — а вот ноутбуки и телефоны придётся
    обойти руками, поэтому решение всегда за администратором.
    """
    await set_suffixes(session, allowed)
    key_pem, cert_pem = build_root("NodeRoost internal CA", allowed)
    await settings_store.set_raw(session, CA_KEY, key_pem)
    await settings_store.set_raw(session, CA_CERT, cert_pem)
    log.warning("корневой сертификат панели перевыпущен, зоны: %s", allowed or "любые")
    return cert_pem


async def root_cert(session: AsyncSession) -> str:
    """Корневой сертификат, если он уже есть (иначе пусто — CA не заводили)."""
    return await settings_store.get_raw(session, CA_CERT) or ""


def fingerprint(cert_pem: str) -> str:
    """sha256 корня в нижнем регистре без разделителей — им сверяется нода."""
    if not cert_pem:
        return ""
    cert = x509.load_pem_x509_certificate(cert_pem.encode())
    return cert.fingerprint(hashes.SHA256()).hex()


def root_info(cert_pem: str) -> dict:
    """Что показать администратору о корне: до какого числа и отпечаток.

    Отпечаток нужен не для красоты: по нему сверяют, что на устройство поставили
    именно тот корень, а не что-то похожее.
    """
    if not cert_pem:
        return {}
    cert = x509.load_pem_x509_certificate(cert_pem.encode())
    fp = cert.fingerprint(hashes.SHA256()).hex().upper()
    try:
        nc = cert.extensions.get_extension_for_class(x509.NameConstraints).value
        allowed = [str(d.value) for d in (nc.permitted_subtrees or [])]
    except x509.ExtensionNotFound:
        allowed = []  # корень без ограничений: подпишет любое имя
    return {
        "subject": cert.subject.rfc4514_string(),
        "not_after": cert.not_valid_after_utc.date().isoformat(),
        "fingerprint": ":".join(fp[i : i + 2] for i in range(0, len(fp), 2)),
        "suffixes": allowed,
    }


async def sign_csr(session: AsyncSession, name: str, csr_der: bytes) -> str:
    """Подписать CSR ноды своим корнем. Возвращает PEM: сертификат + корень.

    Отдаём цепочкой, потому что сервис на ноде обычно отдаёт клиенту ровно то, что
    лежит в его файле: без корня в цепочке браузер, у которого корень уже
    установлен, справится, а вот `curl --cacert` и прочие клиенты — не всегда.
    """
    cert_pem = await ensure_root(session, for_name=name)
    # Ограничение корня проверяем ЗДЕСЬ, а не оставляем клиентам: сертификат,
    # выходящий за него, они всё равно отвергнут — но администратор увидел бы
    # «выпущен» в панели и необъяснимую ошибку в браузере. Лучше сказать сразу.
    allowed = root_info(cert_pem).get("suffixes") or []
    if not covered(name, allowed):
        raise ValueError(
            f"корню разрешены только зоны {', '.join(allowed)} — чтобы выпустить "
            f"сертификат на {name}, добавьте зону {parent_suffix(name)} и "
            "перевыпустите корень (раздел DNS)"
        )
    key_pem = await settings_store.get_raw(session, CA_KEY) or ""
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
