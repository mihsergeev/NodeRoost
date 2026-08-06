#!/usr/bin/env python3
"""Закрыть существующий ключ подписи пасфразой (или сменить её).

    python agent-signing/protect_key.py

Ключ подписи — единственное, что мешает захваченной панели раздать нодам свой
скрипт. Открытым текстом на диске он это свойство наполовину теряет.
"""

import os
import sys

from cryptography.hazmat.primitives import serialization

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _signing import PRIV, ask_passphrase, load_signing_key  # noqa: E402


def main() -> None:
    key = load_signing_key()  # спросит старую пасфразу, если ключ уже зашифрован
    pw = ask_passphrase("Новая пасфраза: ", confirm=True)
    if not pw:
        sys.exit("Пустая пасфраза — ничего не меняю.")
    with open(PRIV, "wb") as f:
        f.write(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.BestAvailableEncryption(pw),
            )
        )
    os.chmod(PRIV, 0o600)
    print(f"Готово: {PRIV} зашифрован. Публичный ключ и подписи не менялись.")


if __name__ == "__main__":
    main()
