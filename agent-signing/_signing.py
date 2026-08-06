"""Общее для подписи релизов агента (keygen.py, protect_key.py, release.py).

Секретов здесь нет — только логика. Приватный ключ лежит в
`noderoost-agent.key`, он в .gitignore и **никогда не покидает эту машину**:
именно поэтому захваченная панель не может подсунуть нодам свой скрипт.

Алгоритм — ECDSA P-256 + SHA-256, а не Ed25519, как у Go-агентов. Причина
прикладная: агент здесь — POSIX sh, подпись он проверяет через `openssl dgst`, а
Ed25519 тому же openssl доступен только с 3.0 (`pkeyutl -rawin`). На Ubuntu 20.04
стоит 1.1.1 — и весь механизм проверки там бы просто не работал.
"""

import getpass
import hashlib
import os
import sys

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001 — старый Python/консоль: не повод падать
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PRIV = os.path.join(HERE, "noderoost-agent.key")
PUB = os.path.join(HERE, "noderoost-agent.pub")
# Подписанное едет в образе панели: манифест крошечный, и так он всегда совпадает
# с той версией панели, которая его раздаёт.
DIST = os.path.join(ROOT, "backend", "app", "agent_dist")


def ask_passphrase(prompt: str, confirm: bool = False) -> bytes:
    """Пасфраза: из NODEROOST_SIGN_PASSPHRASE (для неинтерактивного запуска) или
    с клавиатуры. Ввод руками безопаснее — не оседает в истории и окружении."""
    env = os.environ.get("NODEROOST_SIGN_PASSPHRASE")
    if env is not None:
        return env.encode()
    p = getpass.getpass(prompt)
    if confirm and p != getpass.getpass("Повтори пасфразу: "):
        sys.exit("Пасфразы не совпали.")
    return p.encode()


def load_signing_key() -> ec.EllipticCurvePrivateKey:
    """Приватный ключ: зашифрованный PEM (спросит пасфразу) или открытый PEM."""
    if not os.path.exists(PRIV):
        sys.exit(f"Нет приватного ключа {PRIV} — сначала keygen.py")
    data = open(PRIV, "rb").read()
    if b"ENCRYPTED" in data:
        key = serialization.load_pem_private_key(
            data, password=ask_passphrase("Пасфраза ключа подписи: ")
        )
    else:
        sys.stderr.write(
            "! ВНИМАНИЕ: ключ подписи НЕ зашифрован. Закройте его пасфразой: "
            "python agent-signing/protect_key.py\n"
        )
        key = serialization.load_pem_private_key(data, password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        sys.exit("Ключ не ECDSA — перевыпустите keygen.py")
    return key


def pub_pem(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )


def read_pub() -> bytes:
    if not os.path.exists(PUB):
        sys.exit(f"Нет {PUB} — сначала keygen.py")
    return open(PUB, "rb").read()


def agent_template() -> str:
    """Тот самый текст скрипта агента, который подписывается и едет на ноды.

    Берём его из кода панели, а не из отдельной копии: две копии одного скрипта
    рано или поздно разъезжаются, и подписанным окажется не то, что раздаётся.
    """
    sys.path.insert(0, os.path.join(ROOT, "backend"))
    from app import agent  # noqa: PLC0415 — импорт после правки sys.path

    return agent.TEMPLATE


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sign(key: ec.EllipticCurvePrivateKey, blob: bytes) -> bytes:
    return key.sign(blob, ec.ECDSA(hashes.SHA256()))
