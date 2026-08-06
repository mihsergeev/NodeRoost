"""ACME-клиент панели (RFC 8555): сертификаты Let's Encrypt для имён внутри сети.

Зачем свой, а не «пусть рядом стоит Caddy». Боевые установки панели живут за
ЧУЖИМ обратным прокси — caddy-docker-proxy, nginx, Traefik, — и читать его
хранилище сертификатов панель не может и не должна. От прокси нам нужно ровно
одно: пропустить `/.well-known/acme-challenge/*` на панель. Это одна строчка в
любом из них, и дальше всё делает панель.

**Приватного ключа сертификата панель не видит.** Ключ генерит сама нода, панель
получает от неё CSR и финализирует им заказ. Поэтому взлом панели не отдаёт ключи
сервисов — в отличие от схемы «панель выпустила и разослала».

Проверка владения именем — только `http-01`: Let's Encrypt приходит по имени на
80-й порт, а имя (одной wildcard-записью) ведёт на панель. `dns-01` требовал бы
API-ключей от DNS-хостинга у каждого пользователя — ровно то, от чего уходим.

Хранение (ключ аккаунта, выданные сертификаты, ответы на челленджи) живёт
снаружи: клиент получает готовый ключ и колбэки. Так его можно прогнать тестами
без базы и без сети.
"""

import asyncio
import base64
import hashlib
import json
import logging
from typing import Awaitable, Callable

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils as asym_utils

log = logging.getLogger("noderoost.acme")

# Боевой Let's Encrypt. Тестовый (staging) — те же ручки, но сертификаты не
# доверенные и лимиты мягче; переключается настройкой панели.
LE_PRODUCTION = "https://acme-v02.api.letsencrypt.org/directory"
LE_STAGING = "https://acme-staging-v02.api.letsencrypt.org/directory"


class AcmeError(Exception):
    """Ошибка выдачи. Текст показывается администратору как есть."""


def _b64(data: bytes) -> str:
    """base64url без выравнивания — единственная кодировка во всём ACME."""
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def new_account_key() -> str:
    """Ключ ACME-аккаунта (P-256). Хранится в панели и переживает перевыпуски:
    новый ключ = новый аккаунт, а у Let's Encrypt на создание аккаунтов лимит."""
    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def _jwk(key: ec.EllipticCurvePrivateKey) -> dict:
    nums = key.public_key().public_numbers()
    size = 32  # P-256: координата всегда 32 байта, ведущие нули значимы
    return {
        "crv": "P-256",
        "kty": "EC",
        "x": _b64(nums.x.to_bytes(size, "big")),
        "y": _b64(nums.y.to_bytes(size, "big")),
    }


def _thumbprint(key: ec.EllipticCurvePrivateKey) -> str:
    """Отпечаток ключа аккаунта — вторая половина ответа на челлендж.
    Поля JWK обязаны идти в лексикографическом порядке и без пробелов (RFC 7638)."""
    raw = json.dumps(_jwk(key), sort_keys=True, separators=(",", ":")).encode()
    return _b64(hashlib.sha256(raw).digest())


def key_authorization(token: str, account_key_pem: str) -> str:
    """Что панель должна отдать по адресу челленджа: «<токен>.<отпечаток ключа>»."""
    key = serialization.load_pem_private_key(account_key_pem.encode(), password=None)
    return f"{token}.{_thumbprint(key)}"


class AcmeClient:
    """Минимальный ACME: аккаунт, заказ, http-01, финализация по CSR.

    publish/unpublish — колбэки хранения ответа на челлендж: панель кладёт его
    так, чтобы публичная ручка отдала его без авторизации, и убирает после.
    """

    def __init__(
        self,
        account_key_pem: str,
        directory_url: str = LE_PRODUCTION,
        kid: str = "",
        client: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._key = serialization.load_pem_private_key(
            account_key_pem.encode(), password=None
        )
        self._key_pem = account_key_pem
        self._directory_url = directory_url
        self._kid = kid
        self._client = client
        self._timeout = timeout
        self._dir: dict = {}
        self._nonce: str | None = None

    # --- транспорт -------------------------------------------------------

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout, follow_redirects=True)
        return self._client

    async def _directory(self) -> dict:
        if not self._dir:
            r = await (await self._http()).get(self._directory_url)
            if r.status_code != 200:
                raise AcmeError(f"каталог ACME недоступен: HTTP {r.status_code}")
            self._dir = r.json()
        return self._dir

    async def _take_nonce(self) -> str:
        """Каждый запрос подписывается разовым nonce. Свежий приходит в ответе
        предыдущего — отдельный поход к newNonce нужен только для первого."""
        if self._nonce:
            nonce, self._nonce = self._nonce, None
            return nonce
        url = (await self._directory())["newNonce"]
        r = await (await self._http()).head(url)
        nonce = r.headers.get("Replay-Nonce")
        if not nonce:
            raise AcmeError("ACME не выдал nonce")
        return nonce

    def _sign(self, url: str, payload: bytes | None, nonce: str) -> dict:
        protected: dict = {"alg": "ES256", "nonce": nonce, "url": url}
        if self._kid:
            protected["kid"] = self._kid
        else:
            protected["jwk"] = _jwk(self._key)
        p64 = _b64(json.dumps(protected, separators=(",", ":")).encode())
        # payload=None — «POST-as-GET»: тело пустое, но запрос всё равно подписан
        d64 = "" if payload is None else _b64(payload)
        der = self._key.sign(f"{p64}.{d64}".encode(), ec.ECDSA(hashes.SHA256()))
        r, s = asym_utils.decode_dss_signature(der)
        # JWS хочет r||s фиксированной длины, а cryptography отдаёт DER
        sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
        return {"protected": p64, "payload": d64, "signature": _b64(sig)}

    async def _post(self, url: str, payload: dict | None) -> httpx.Response:
        body = None if payload is None else json.dumps(payload).encode()
        for attempt in range(2):
            jws = self._sign(url, body, await self._take_nonce())
            r = await (await self._http()).post(
                url, json=jws, headers={"Content-Type": "application/jose+json"}
            )
            self._nonce = r.headers.get("Replay-Nonce")
            if r.status_code < 400:
                return r
            detail = _problem(r)
            # Протухший nonce — штатная ситуация, а не ошибка: повторяем со свежим
            if attempt == 0 and "badNonce" in detail:
                continue
            raise AcmeError(detail)
        raise AcmeError("ACME отвечает badNonce и на повтор")

    # --- флоу ------------------------------------------------------------

    async def ensure_account(self, email: str = "") -> str:
        """Создаёт аккаунт или находит существующий по тому же ключу; отдаёт kid."""
        if self._kid:
            return self._kid
        payload: dict = {"termsOfServiceAgreed": True}
        if email:
            payload["contact"] = [f"mailto:{email}"]
        r = await self._post((await self._directory())["newAccount"], payload)
        kid = r.headers.get("Location", "")
        if not kid:
            raise AcmeError("ACME не вернул адрес аккаунта")
        self._kid = kid
        return kid

    async def issue(
        self,
        domain: str,
        csr_der: bytes,
        publish: Callable[[str, str], Awaitable[None]],
        unpublish: Callable[[str], Awaitable[None]],
        poll_seconds: float = 2.0,
        attempts: int = 30,
    ) -> str:
        """Выпускает сертификат для domain по CSR ноды. Отдаёт PEM-цепочку."""
        await self.ensure_account()
        order = await self._post(
            (await self._directory())["newOrder"],
            {"identifiers": [{"type": "dns", "value": domain}]},
        )
        order_url = order.headers.get("Location", "")
        data = order.json()
        # Список заполняется В МОМЕНТ публикации, а не после успешной проверки:
        # иначе провалившийся челлендж оставлял бы свой ответ лежать навсегда.
        tokens: list[str] = []
        try:
            for authz_url in data.get("authorizations", []):
                await self._solve_http01(
                    authz_url, publish, tokens, poll_seconds, attempts
                )
            final = await self._post(
                data["finalize"], {"csr": _b64(csr_der)}
            )
            state = final.json()
            for _ in range(attempts):
                if state.get("status") == "valid":
                    break
                if state.get("status") == "invalid":
                    raise AcmeError(f"Let's Encrypt отклонил заказ: {_reason(state)}")
                await asyncio.sleep(poll_seconds)
                state = (await self._post(order_url, None)).json()
            else:
                raise AcmeError("Let's Encrypt не выдал сертификат за отведённое время")
            cert = await self._post(state["certificate"], None)
            return cert.text
        finally:
            for token in tokens:
                try:
                    await unpublish(token)
                except Exception:  # noqa: BLE001 — уборка не должна ронять выдачу
                    log.warning("не удалось убрать ответ на челлендж %s", token)

    async def _solve_http01(
        self,
        authz_url: str,
        publish: Callable[[str, str], Awaitable[None]],
        published: list[str],
        poll_seconds: float,
        attempts: int,
    ) -> None:
        authz = (await self._post(authz_url, None)).json()
        if authz.get("status") == "valid":
            return  # это имя уже подтверждено раньше — челлендж не нужен
        challenge = next(
            (c for c in authz.get("challenges", []) if c.get("type") == "http-01"), None
        )
        if not challenge:
            raise AcmeError(
                "Let's Encrypt не предложил проверку http-01 — обычно так бывает "
                "для wildcard-имён, они выдаются только через dns-01"
            )
        token = challenge["token"]
        await publish(token, key_authorization(token, self._key_pem))
        published.append(token)
        await self._post(challenge["url"], {})
        for _ in range(attempts):
            await asyncio.sleep(poll_seconds)
            authz = (await self._post(authz_url, None)).json()
            if authz.get("status") == "valid":
                return
            if authz.get("status") in ("invalid", "revoked", "expired"):
                raise AcmeError(f"проверка домена не прошла: {_reason(authz)}")
        raise AcmeError("Let's Encrypt не завершил проверку домена за отведённое время")


def _problem(r: httpx.Response) -> str:
    """Ошибку ACME отдают в формате problem+json — вытаскиваем человеческий текст."""
    try:
        body = r.json()
    except ValueError:
        return f"HTTP {r.status_code}: {r.text[:200]}"
    detail = str(body.get("detail") or "").strip()
    kind = str(body.get("type") or "")
    return f"{detail or r.text[:200]} ({kind or r.status_code})"


def _reason(obj: dict) -> str:
    """Причина отказа лежит либо в самом объекте, либо в его челленджах."""
    err = obj.get("error") or {}
    if not err:
        for c in obj.get("challenges", []):
            if c.get("error"):
                err = c["error"]
                break
    detail = str(err.get("detail") or "").strip()
    return detail or "причина не указана"
