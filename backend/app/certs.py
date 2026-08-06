"""Сертификаты для имён внутри сети: панель подписывает, ключ живёт на ноде.

Как это устроено целиком:

1. Администратор ставит у имени галочку «сертификат». Имя должно вести на ноду —
   ключ и сертификат нужны именно ей.
2. Агент ноды видит это в своём состоянии, генерит **у себя** ключ и CSR и шлёт
   панели только CSR. Приватный ключ не покидает машину: панель его не видит, в
   базе его нет, в бэкапе его нет.
3. Панель подписывает CSR своим корнем (`app/ca.py`) и отвечает цепочкой.
4. Агент кладёт файлы на ноду и дёргает свой хук перезагрузки сервиса.

Почему подписывает панель, а не каждая нода сама себе: право подписи одно на
сеть, и раздать его по машинам значит раздать возможность выписать сертификат на
любое имя сети. Панель этим правом распоряжается так же, как и маршрутами.

Публичного центра сертификации здесь нет намеренно: он подписывает только имена,
которые видно из интернета, а эти имена наружу не смотрят по определению.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import ca, settings_store
from app.config import Settings
from app.models import Certificate

log = logging.getLogger("noderoost.certs")

# Один выпуск на имя за раз: агент спрашивает раз в минуту, и два перекрывшихся
# запроса завели бы два сертификата на одно имя.
_locks: dict[str, asyncio.Lock] = {}


def _lock(name: str) -> asyncio.Lock:
    return _locks.setdefault(name, asyncio.Lock())


def cert_not_after(pem: str) -> datetime | None:
    """До какого числа действует сертификат (первый в цепочке — наш)."""
    try:
        cert = x509.load_pem_x509_certificate(pem.encode())
    except Exception:  # noqa: BLE001 — битый PEM не должен ронять панель
        return None
    return cert.not_valid_after_utc


def csr_names(csr_der: bytes) -> set[str]:
    """Имена из CSR: CN плюс SAN. Нужны, чтобы нода не заказала чужое имя."""
    csr = x509.load_der_x509_csr(csr_der)
    if not csr.is_signature_valid:
        # Подпись CSR доказывает, что просящий владеет ключом, к которому просит
        # сертификат. Без проверки мы подписали бы чужой ключ по чужой просьбе.
        raise ValueError("подпись CSR неверна")
    names = {
        str(a.value).lower()
        for a in csr.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
    }
    try:
        ext = csr.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        names |= {n.lower() for n in ext.value.get_values_for_type(x509.DNSName)}
    except x509.ExtensionNotFound:
        pass
    return {n for n in names if n}


def fingerprint(pem: str) -> str:
    """Короткий отпечаток сертификата — по нему агент понимает, изменился ли тот,
    что лежит у него на диске, не пересылая сам сертификат каждую минуту."""
    try:
        cert = x509.load_pem_x509_certificate(pem.encode())
    except Exception:  # noqa: BLE001
        return ""
    return cert.fingerprint(hashes.SHA256()).hex()[:12]


async def get(session: AsyncSession, name: str) -> Certificate | None:
    return await session.get(Certificate, name)


async def wanted_for_node(
    session: AsyncSession, settings: Settings, node_id: str
) -> list[tuple[str, str, bool]]:
    """Что панель хочет от ноды по сертификатам: [(имя, отпечаток, нужен CSR)].

    Отпечаток пустой — сертификата ещё нет. «Нужен CSR» ставится, когда его нет,
    когда он скоро истечёт или когда прошлая попытка кончилась ошибкой.
    """
    out: list[tuple[str, str, bool]] = []
    for rec in await settings_store.get_dns_records(session):
        if not rec.get("cert") or str(rec.get("node_id") or "") != str(node_id):
            continue
        name = str(rec.get("name") or "")
        if not name:
            continue
        cert = await get(session, name)
        out.append(
            (
                name,
                fingerprint(cert.cert_pem) if cert and cert.cert_pem else "",
                needs_renewal(cert, settings.cert_renew_days),
            )
        )
    return sorted(out)


async def all_certs(session: AsyncSession) -> list[Certificate]:
    return list(await session.scalars(select(Certificate)))


async def forget(session: AsyncSession, names: set[str]) -> int:
    """Убрать сертификаты имён, которых в панели больше нет (или у которых сняли
    галочку): держать их значит показывать администратору то, чего никто не ждёт.

    Зовётся и из коллектора: имя уходит вместе с удалённой нодой, а не только по
    правке списка в UI, — иначе строка сертификата пережила бы саму запись.
    """
    dropped = 0
    for cert in await all_certs(session):
        if cert.name not in names:
            await session.delete(cert)
            dropped += 1
    if dropped:
        await session.commit()
    return dropped


async def issue(
    session: AsyncSession,
    settings: Settings,
    name: str,
    node_id: str,
    csr_der: bytes,
) -> Certificate:
    """Выпустить (или перевыпустить) сертификат для имени по CSR ноды.

    Ошибку не прячем: она ложится в строку сертификата и показывается в панели —
    «не работает и непонятно почему» тут самое дорогое, что может случиться.
    """
    if name not in csr_names(csr_der):
        raise ValueError(f"CSR выписан не на {name}")

    async with _lock(name):
        row = await session.get(Certificate, name)
        if row is None:
            row = Certificate(name=name, node_id=node_id)
            session.add(row)
        row.node_id = node_id
        if (
            row.status == "ok"
            and row.cert_pem
            and not needs_renewal(row, settings.cert_renew_days)
        ):
            # Действующий сертификат есть — отдаём его, а не выписываем новый:
            # иначе повторный запрос плодил бы сертификаты на одно имя, и никто
            # бы не знал, какой из них где лежит.
            return row
        row.status = "issuing"
        await session.commit()

        try:
            pem = await ca.sign_csr(session, name, csr_der)
        except Exception as e:  # noqa: BLE001 — любой отказ показываем как есть
            row.status = "error"
            row.error = str(e)[:500]
            row.updated_at = datetime.now(timezone.utc)
            await session.commit()
            log.warning("сертификат для %s не выдан: %s", name, e)
            return row

        row.status = "ok"
        row.error = ""
        row.cert_pem = pem
        row.not_after = cert_not_after(pem)
        row.updated_at = datetime.now(timezone.utc)
        await session.commit()
        log.info("сертификат для %s выдан до %s", name, row.not_after)
        return row


# Пауза после отказа. Причина (имя вне разрешённых зон, битый CSR) сама собой не
# исчезает, а агент спрашивает раз в минуту — без паузы панель молотила бы отказ
# шестьдесят раз в час и засыпала бы этим журнал.
RETRY_AFTER_ERROR = timedelta(minutes=15)


def needs_renewal(cert: Certificate | None, renew_days: int) -> bool:
    """Пора ли просить у ноды новый CSR: сертификата нет, он скоро истечёт или
    прошлая попытка закончилась ошибкой и пауза вышла."""
    if cert is None:
        return True
    if cert.status == "error" and cert.updated_at:
        return datetime.now(timezone.utc) - cert.updated_at > RETRY_AFTER_ERROR
    if cert.status != "ok" or not cert.cert_pem:
        return True
    if cert.not_after is None:
        return True
    return cert.not_after - datetime.now(timezone.utc) <= timedelta(days=renew_days)
