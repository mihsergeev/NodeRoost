"""Сертификаты для имён внутри сети: панель заказывает, нода хранит ключ.

Как это устроено целиком:

1. Администратор ставит у имени галочку «сертификат». Имя должно вести на ноду —
   ключ и сертификат нужны именно ей.
2. Агент ноды видит это в своём состоянии, генерит **у себя** ключ и CSR и шлёт
   панели только CSR. Приватный ключ не покидает машину: панель его не видит, в
   базе его нет, в бэкапе его нет.
3. Панель проводит ACME-заказ (`app/acme.py`) и отвечает готовым сертификатом.
   Проверку владения Let's Encrypt делает по 80-му порту самой панели — потому
   что имя (одной wildcard-записью) ведёт на неё.
4. Агент кладёт файлы на ноду и дёргает свой хук перезагрузки сервиса.

Почему выпускает панель, а не сама нода: у ноды снаружи может не быть вообще
ничего — ни 80-го порта, ни публичного адреса. У панели он есть по определению,
она и так публична для нод.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import acme, settings_store
from app.config import Settings
from app.models import Certificate

log = logging.getLogger("noderoost.certs")

ACCOUNT_KEY = "acme_account_key"  # ключ ACME-аккаунта (секрет)
ACCOUNT_KID = "acme_account_kid"  # адрес аккаунта у Let's Encrypt
CHALLENGES = "acme_challenges"  # {токен: ответ} — живут минуты, пока идёт проверка

# Один заказ на имя за раз: агент спрашивает раз в минуту, а заказ идёт секунды —
# без замка два перекрывшихся запроса сожгли бы лимит проверок на ровном месте.
_locks: dict[str, asyncio.Lock] = {}


def _lock(name: str) -> asyncio.Lock:
    return _locks.setdefault(name, asyncio.Lock())


async def account_key(session: AsyncSession) -> str:
    """Ключ ACME-аккаунта; при первом обращении создаётся и сохраняется.

    Терять его нельзя: новый ключ — это новый аккаунт, а на создание аккаунтов у
    Let's Encrypt свой лимит. Поэтому он лежит в базе и уезжает в бэкап.
    """
    raw = await settings_store.get_raw(session, ACCOUNT_KEY)
    if raw:
        return raw
    key = acme.new_account_key()
    await settings_store.set_raw(session, ACCOUNT_KEY, key)
    return key


async def _challenges(session: AsyncSession) -> dict:
    import json

    raw = await settings_store.get_raw(session, CHALLENGES)
    return json.loads(raw) if raw else {}


async def _save_challenges(session: AsyncSession, data: dict) -> None:
    import json

    await settings_store.set_raw(session, CHALLENGES, json.dumps(data))


async def answer_for(session: AsyncSession, token: str) -> str | None:
    """Ответ на челлендж по токену — то, что отдаёт публичная ручка.

    Хранится в базе, а не в памяти процесса: перезапуск бэкенда посреди проверки
    иначе оставлял бы Let's Encrypt перед 404, и имя попадало бы в лимит отказов.
    """
    return (await _challenges(session)).get(token)


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
    когда он скоро истечёт или когда прошлая попытка провалилась и пауза вышла.
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
    session: AsyncSession, settings: Settings, name: str, node_id: str, csr_der: bytes
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
        now = datetime.now(timezone.utc)
        if row.retry_after and row.retry_after > now:
            return row  # ждём паузы после отказа — Let's Encrypt считает попытки
        row.status = "issuing"
        await session.commit()

        key_pem = await account_key(session)
        kid = await settings_store.get_raw(session, ACCOUNT_KID) or ""
        client = acme.AcmeClient(key_pem, settings.acme_directory, kid=kid)

        async def publish(token: str, value: str) -> None:
            data = await _challenges(session)
            data[token] = value
            await _save_challenges(session, data)

        async def unpublish(token: str) -> None:
            data = await _challenges(session)
            if data.pop(token, None) is not None:
                await _save_challenges(session, data)

        try:
            new_kid = await client.ensure_account(settings.acme_email)
            if new_kid != kid:
                await settings_store.set_raw(session, ACCOUNT_KID, new_kid)
            pem = await client.issue(name, csr_der, publish, unpublish)
        except Exception as e:  # noqa: BLE001 — любой отказ показываем как есть
            row.status = "error"
            row.error = str(e)[:500]
            # Пауза после отказа: у Let's Encrypt лимит 5 неудачных проверок в час
            # на имя, а агент спрашивает раз в минуту — без паузы он сожжёт его
            # за пять минут и потом будет ждать час.
            row.retry_after = datetime.now(timezone.utc) + timedelta(minutes=15)
            row.updated_at = datetime.now(timezone.utc)
            await session.commit()
            log.warning("сертификат для %s не выдан: %s", name, e)
            return row

        row.status = "ok"
        row.error = ""
        row.retry_after = None
        row.cert_pem = pem
        row.not_after = cert_not_after(pem)
        row.updated_at = datetime.now(timezone.utc)
        await session.commit()
        log.info("сертификат для %s выдан до %s", name, row.not_after)
        return row


def needs_renewal(cert: Certificate | None, renew_days: int) -> bool:
    """Пора ли просить у ноды новый CSR: сертификата нет, он скоро истечёт или
    прошлая попытка закончилась ошибкой и пауза вышла."""
    now = datetime.now(timezone.utc)
    if cert is None or cert.status != "ok" or not cert.cert_pem:
        return cert is None or not cert.retry_after or cert.retry_after <= now
    if cert.not_after is None:
        return True
    left = cert.not_after - now
    return left <= timedelta(days=renew_days)
