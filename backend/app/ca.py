"""Свой центр сертификации панели: сертификаты для имён, которых нет наружу.

Публичный ЦС тут не годится по устройству: он подписывает только имя, которое
существует в публичном DNS, и публикует каждое в CT-логах — то есть требует
вывести наружу ровно то, что наружу выводить не хотят. Своя CA не требует ни
домена, ни DNS-записи, ни интернета: имя любое (`loki.mirabah`, `nas.mesh`),
выпуск мгновенный, лимитов нет.

Цена — корень надо раздать по машинам. Ноды панель обслуживает сама (скрипт
подключения кладёт корень сразу, агент следит дальше), ноутбуки и телефоны
остаются на администраторе: файл и отпечаток панель отдаёт.

**Чем ограничен корень.** Его приватный ключ лежит в базе панели, а сам он стоит
в системном хранилище каждой машины — то есть вопрос «что он может подписать»
это вопрос «что сможет подделать тот, кто получит панель». Поэтому корень несёт
X.509 Name Constraints, и устроены они ЗАПРЕТОМ, а не разрешением: запрещены все
домены верхнего уровня, реально существующие в интернете (корневая зона IANA,
`app/data/public_tlds.txt`), и все IP-адреса. Что остаётся — выдуманные домены
вроде `mesh`, `bironex`, `mirabah`: как раз то, что живёт внутри сети.

Почему именно так, а не списком разрешённых: разрешающий список зашит в корень, и
каждый новый проект требовал бы выпустить корень заново и обойти с ним все
устройства. Запрет же не нужно менять никогда — домена нового проекта нет в
интернете, значит он и не запрещён.

Ключ самого сертификата генерит нода: сюда приходит только CSR.
"""

import ipaddress
import logging
import os
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from sqlalchemy.ext.asyncio import AsyncSession

from app import settings_store

log = logging.getLogger("noderoost.ca")

_tlds_cache: list[str] | None = None

CA_KEY = "ca_key"  # приватный ключ корневого сертификата (секрет)
CA_CERT = "ca_cert"  # сам корневой сертификат (не секрет, его и раздаём)
CA_AUTO = "ca_auto"  # ставить ли корень на ноды автоматически (агентом)

# Публичные домены верхнего уровня — их корень подписывать не будет.
# Обновляется `python ops/build-tlds.py`; действует с момента выпуска корня.
_TLD_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "public_tlds.txt")

# Корень живёт долго: его ставят руками на каждое устройство, и делать это раз в
# год никто не будет (срок задаётся при перевыпуске, по умолчанию 20 лет).
# Сертификаты сервисов — наоборот, короткие: их продлевает панель сама, и
# короткий срок ограничивает ущерб от утёкшего ключа ноды.
ROOT_YEARS = 20
LEAF_DAYS = 90


def _now() -> datetime:
    return datetime.now(timezone.utc)


def public_tlds() -> list[str]:
    """Домены верхнего уровня, которые корню запрещены (корневая зона IANA)."""
    global _tlds_cache
    if _tlds_cache is None:
        try:
            with open(_TLD_FILE, encoding="utf-8") as f:
                _tlds_cache = [
                    line.strip().lower()
                    for line in f
                    if line.strip() and not line.startswith("#")
                ]
        except OSError:
            log.warning("нет списка публичных доменов (%s) — корень будет без запретов", _TLD_FILE)
            _tlds_cache = []
    return _tlds_cache


def tld_of(name: str) -> str:
    """Домен верхнего уровня имени: loki.mirabah → mirabah, a.b.example.com → com."""
    return name.rstrip(".").rsplit(".", 1)[-1].lower()


def signable(name: str, allowed: list[str] | None = None) -> bool:
    """Подпишет ли корень это имя.

    `allowed` — разрешающий список СТАРОГО корня (панель до перехода на запреты).
    Пусто/None у нового корня: разрешено всё, кроме публичных доменов.
    """
    if allowed:
        return any(name == s or name.endswith("." + s) for s in allowed)
    return tld_of(name) not in set(public_tlds())


def build_root(name: str, years: int = ROOT_YEARS) -> tuple[str, str]:
    """Создать корневой сертификат. Возвращает (ключ PEM, сертификат PEM).

    Ограничения — запретом: все публичные домены верхнего уровня и все IP-адреса.
    Всё остальное (выдуманные внутренние домены) корень подписывать вправе, и
    добавление нового проекта не требует ни перевыпуска, ни обхода устройств.
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
        .not_valid_after(_now() + timedelta(days=365 * max(1, years)))
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
    excluded = [x509.DNSName(t) for t in public_tlds()] + [
        # Тип имени, не упомянутый в ограничениях, RFC 5280 считает
        # неограниченным — то есть на IP корень подписал бы что угодно.
        x509.IPAddress(ipaddress.IPv4Network("0.0.0.0/0")),
        x509.IPAddress(ipaddress.IPv6Network("::/0")),
    ]
    builder = builder.add_extension(
        x509.NameConstraints(permitted_subtrees=None, excluded_subtrees=excluded),
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
    years: int = ROOT_YEARS,
) -> str:
    """Корневой сертификат: создаётся при первом обращении, дальше берётся из базы.

    Пересоздавать его молча нельзя: все выданные сертификаты перестанут быть
    доверенными, а корень придётся заново раскладывать по устройствам, — для
    этого есть `rotate()`, который зовут кнопкой.
    """
    cert = await settings_store.get_raw(session, CA_CERT)
    key = await settings_store.get_raw(session, CA_KEY)
    if cert and key:
        return cert
    key_pem, cert_pem = build_root(name, years)
    await settings_store.set_raw(session, CA_KEY, key_pem)
    await settings_store.set_raw(session, CA_CERT, cert_pem)
    log.info(
        "создан корневой сертификат панели (%s) на %s лет, запрещено доменов: %s",
        name, years, len(public_tlds()),
    )
    return cert_pem


async def rotate(session: AsyncSession, years: int = ROOT_YEARS) -> str:
    """Выпустить корень заново (например с другим сроком или свежим списком TLD).

    Старый корень после этого бесполезен: сертификаты, им подписанные, перестают
    быть доверенными, а на устройствах нужно заменить файл. Ноды с агентом делают
    это сами (корень едет в состоянии по отпечатку), сертификаты имён панель
    перевыпускает следующим же опросом — а вот ноутбуки и телефоны придётся
    обойти руками, поэтому решение всегда за администратором.
    """
    key_pem, cert_pem = build_root("NodeRoost internal CA", years)
    await settings_store.set_raw(session, CA_KEY, key_pem)
    await settings_store.set_raw(session, CA_CERT, cert_pem)
    log.warning("корневой сертификат панели перевыпущен на %s лет", years)
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
    """Что показать администратору о корне: срок, отпечаток и чем он ограничен.

    Отпечаток нужен не для красоты: по нему сверяют, что на устройство поставили
    именно тот корень, а не что-то похожее.
    """
    if not cert_pem:
        return {}
    cert = x509.load_pem_x509_certificate(cert_pem.encode())
    fp = cert.fingerprint(hashes.SHA256()).hex().upper()
    allowed: list[str] = []
    blocked = 0
    try:
        nc = cert.extensions.get_extension_for_class(x509.NameConstraints).value
        allowed = [str(d.value) for d in (nc.permitted_subtrees or [])]
        blocked = sum(
            1 for d in (nc.excluded_subtrees or []) if isinstance(d, x509.DNSName)
        )
    except x509.ExtensionNotFound:
        pass  # корень без ограничений вовсе — подпишет что угодно
    return {
        "subject": cert.subject.rfc4514_string(),
        "not_after": cert.not_valid_after_utc.date().isoformat(),
        "fingerprint": ":".join(fp[i : i + 2] for i in range(0, len(fp), 2)),
        # старый корень: список РАЗРЕШЁННЫХ доменов (пусто у нового)
        "suffixes": allowed,
        # новый корень: сколько публичных доменов ему запрещено
        "blocked": blocked,
    }


async def sign_csr(session: AsyncSession, name: str, csr_der: bytes) -> str:
    """Подписать CSR ноды своим корнем. Возвращает PEM одного сертификата.

    Корень в файл НЕ кладём: между ним и листом ничего нет, а сам он весит два
    десятка килобайт (в нём список запрещённых доменов) — отправлять их в каждом
    рукопожатии незачем. Клиент, которому корень поставили, соберёт цепочку сам;
    `curl --cacert` тоже берёт корень отдельным файлом.
    """
    cert_pem = await ensure_root(session)
    # Ограничение корня проверяем ЗДЕСЬ, а не оставляем клиентам: сертификат,
    # выходящий за него, они всё равно отвергнут — но администратор увидел бы
    # «выпущен» в панели и необъяснимую ошибку в браузере. Лучше сказать сразу.
    allowed = root_info(cert_pem).get("suffixes") or []
    if not signable(name, allowed):
        if allowed:  # старый корень с разрешающим списком
            raise ValueError(
                f"этому корню разрешены только домены {', '.join(allowed)}. "
                f"Выпустите корень заново (раздел DNS) — новый подписывает любые "
                "внутренние имена, кроме публичных доменов"
            )
        raise ValueError(
            f".{tld_of(name)} — публичный домен верхнего уровня, и корень его не "
            "подписывает: иначе он мог бы подделать настоящий сайт для ваших же "
            "машин. Возьмите внутреннее имя (например loki.mirabah)"
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
    return leaf.public_bytes(serialization.Encoding.PEM).decode()
