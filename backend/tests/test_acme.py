"""Тесты ACME-клиента на игрушечном сервере (httpx.MockTransport).

Настоящий Let's Encrypt в тестах не нужен и вреден: у него лимиты и он отвечает
минутами. Зато проверить надо ровно то, что ломается молча, — подпись JWS и
последовательность шагов: ошибись в кодировке подписи, и панель будет получать
«unauthorized» без единого намёка почему.
"""

import base64
import json

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils as asym_utils

from app import acme

CERT_PEM = "-----BEGIN CERTIFICATE-----\nтело\n-----END CERTIFICATE-----\n"


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class FakeAcme:
    """Минимальный ACME-сервер: каталог, аккаунт, заказ, http-01, выдача."""

    def __init__(self, *, authz_status: str = "valid", bad_nonce_once: bool = False):
        self.authz_status = authz_status
        self.bad_nonce_once = bad_nonce_once
        self.seen: list[tuple[str, dict]] = []  # (url, распакованный JWS)
        self.challenge_done = False
        self.finalize_csr = ""
        self.nonces = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        path = request.url.path
        if path == "/directory":
            base = f"{request.url.scheme}://{request.url.host}"
            return httpx.Response(
                200,
                json={
                    "newNonce": f"{base}/nonce",
                    "newAccount": f"{base}/new-account",
                    "newOrder": f"{base}/new-order",
                },
            )
        if path == "/nonce":
            self.nonces += 1
            return httpx.Response(200, headers={"Replay-Nonce": f"nonce-{self.nonces}"})

        body = json.loads(request.content)
        protected = json.loads(_unb64(body["protected"]))
        payload = json.loads(_unb64(body["payload"])) if body["payload"] else None
        self.seen.append((url, {"protected": protected, "payload": payload, "jws": body}))
        headers = {"Replay-Nonce": f"nonce-{len(self.seen) + 100}"}

        if self.bad_nonce_once and len(self.seen) == 1:
            return httpx.Response(
                400,
                headers=headers,
                json={"type": "urn:ietf:params:acme:error:badNonce", "detail": "устарел"},
            )
        base = f"{request.url.scheme}://{request.url.host}"
        if path == "/new-account":
            return httpx.Response(
                201, headers={**headers, "Location": f"{base}/acct/1"}, json={"status": "valid"}
            )
        if path == "/new-order":
            return httpx.Response(
                201,
                headers={**headers, "Location": f"{base}/order/1"},
                json={
                    "status": "pending",
                    "authorizations": [f"{base}/authz/1"],
                    "finalize": f"{base}/finalize/1",
                },
            )
        if path == "/authz/1":
            if not self.challenge_done:
                return httpx.Response(
                    200,
                    headers=headers,
                    json={
                        "status": "pending",
                        "challenges": [
                            {"type": "dns-01", "token": "нет", "url": f"{base}/chall/dns"},
                            {"type": "http-01", "token": "TOK", "url": f"{base}/chall/1"},
                        ],
                    },
                )
            if self.authz_status == "valid":
                return httpx.Response(200, headers=headers, json={"status": "valid"})
            return httpx.Response(
                200,
                headers=headers,
                json={
                    "status": self.authz_status,
                    "challenges": [
                        {"type": "http-01", "error": {"detail": "404 на адресе проверки"}}
                    ],
                },
            )
        if path == "/chall/1":
            self.challenge_done = True
            return httpx.Response(200, headers=headers, json={"status": "processing"})
        if path == "/finalize/1":
            self.finalize_csr = payload["csr"]
            return httpx.Response(
                200, headers=headers, json={"status": "valid", "certificate": f"{base}/cert/1"}
            )
        if path == "/cert/1":
            return httpx.Response(200, headers=headers, text=CERT_PEM)
        return httpx.Response(404, headers=headers, json={"detail": f"нет ручки {path}"})


def _client(fake: FakeAcme, key_pem: str) -> acme.AcmeClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    return acme.AcmeClient(key_pem, "https://acme.test/directory", client=http)


async def _issue(fake: FakeAcme, key_pem: str, published: dict) -> str:
    async def publish(token: str, value: str) -> None:
        published[token] = value

    async def unpublish(token: str) -> None:
        published.pop(token, None)

    return await _client(fake, key_pem).issue(
        "nas.example.com", b"CSR-DER", publish, unpublish, poll_seconds=0
    )


async def test_issue_walks_the_whole_flow():
    key_pem = acme.new_account_key()
    fake = FakeAcme()
    published: dict = {}
    pem = await _issue(fake, key_pem, published)

    assert pem == CERT_PEM
    urls = [u for u, _ in fake.seen]
    assert any(u.endswith("/new-account") for u in urls)
    assert any(u.endswith("/new-order") for u in urls)
    assert any(u.endswith("/chall/1") for u in urls)
    # CSR уходит в финализацию как есть, в base64url
    assert _unb64(fake.finalize_csr) == b"CSR-DER"
    # ответ на челлендж убран после выдачи — он больше не должен отдаваться
    assert published == {}


async def test_challenge_answer_is_token_plus_thumbprint():
    key_pem = acme.new_account_key()
    fake = FakeAcme()
    seen: dict = {}

    async def publish(token: str, value: str) -> None:
        seen[token] = value

    async def unpublish(token: str) -> None:
        pass  # специально НЕ убираем — хотим посмотреть, что было опубликовано

    await _client(fake, key_pem).issue(
        "nas.example.com", b"CSR", publish, unpublish, poll_seconds=0
    )
    assert list(seen) == ["TOK"]
    assert seen["TOK"].startswith("TOK.")
    assert seen["TOK"] == acme.key_authorization("TOK", key_pem)
    # отпечаток — 43 символа base64url без выравнивания (sha256)
    assert len(seen["TOK"].split(".", 1)[1]) == 43 and "=" not in seen["TOK"]


async def test_requests_are_signed_with_the_account_key():
    """Подпись — самое хрупкое место: ошибись в формате, и ACME отвечает
    «unauthorized», не объясняя, что не так."""
    key_pem = acme.new_account_key()
    fake = FakeAcme()
    await _issue(fake, key_pem, {})

    url, first = fake.seen[0]
    protected, jws = first["protected"], first["jws"]
    assert protected["alg"] == "ES256"
    assert protected["url"] == url  # url внутри подписи обязан совпадать с адресом
    assert protected["nonce"]
    assert "jwk" in protected and "kid" not in protected  # у первого запроса ключ целиком

    key = serialization.load_pem_private_key(key_pem.encode(), password=None)
    sig = _unb64(jws["signature"])
    assert len(sig) == 64  # r||s, а не DER
    der = asym_utils.encode_dss_signature(
        int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")
    )
    key.public_key().verify(
        der,
        f"{jws['protected']}.{jws['payload']}".encode(),
        ec.ECDSA(hashes.SHA256()),
    )

    # дальше клиент обязан ходить по kid, а не слать ключ целиком каждый раз
    later = [p for _, p in fake.seen[1:]]
    assert all("kid" in p["protected"] and "jwk" not in p["protected"] for p in later)


async def test_stale_nonce_is_retried_not_reported():
    """ACME штатно отвечает badNonce — это не ошибка выдачи, а просьба повторить."""
    key_pem = acme.new_account_key()
    fake = FakeAcme(bad_nonce_once=True)
    assert await _issue(fake, key_pem, {}) == CERT_PEM


async def test_failed_validation_says_why():
    key_pem = acme.new_account_key()
    fake = FakeAcme(authz_status="invalid")
    with pytest.raises(acme.AcmeError) as e:
        await _issue(fake, key_pem, {})
    assert "404 на адресе проверки" in str(e.value)


async def test_answer_is_cleaned_up_even_after_a_failure():
    """Ответ на челлендж — секрет одноразовый: остаться лежать он не должен ни при
    успехе, ни при отказе."""
    key_pem = acme.new_account_key()
    fake = FakeAcme(authz_status="invalid")
    published: dict = {}
    with pytest.raises(acme.AcmeError):
        await _issue(fake, key_pem, published)
    assert published == {}


async def test_account_key_is_a_real_p256_key():
    pem = acme.new_account_key()
    key = serialization.load_pem_private_key(pem.encode(), password=None)
    assert isinstance(key, ec.EllipticCurvePrivateKey)
    assert key.curve.name == "secp256r1"
    assert acme.new_account_key() != pem  # каждый вызов — новый ключ
