#!/usr/bin/env python3
"""Создать ключ подписи релизов агента. Делается ОДИН раз.

    python agent-signing/keygen.py

Приватный ключ (`noderoost-agent.key`) остаётся здесь и в git не попадает — он в
.gitignore. Публичный (`noderoost-agent.pub`) наоборот, коммитится и уезжает в
образ панели: его она вшивает в скрипт при установке агента, и дальше нода
принимает обновления только с подписью этим ключом.

Потеря приватного ключа не ломает уже установленных агентов, но новые релизы
подписать будет нечем: придётся выпустить новую пару и переустановить агентов
руками. Держите копию там же, где ключи от серверов.
"""

import os
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _signing import PRIV, PUB, ask_passphrase, pub_pem  # noqa: E402


def main() -> None:
    if os.path.exists(PRIV):
        sys.exit(
            f"{PRIV} уже есть. Перевыпуск ключа ОТРЕЖЕТ все установленные агенты "
            "от обновлений — они проверяют подпись прежним ключом. Уверены — "
            "уберите файл руками."
        )
    pw = ask_passphrase("Пасфраза для ключа подписи (пусто — без шифрования): ", confirm=True)
    key = ec.generate_private_key(ec.SECP256R1())
    enc = (
        serialization.BestAvailableEncryption(pw)
        if pw
        else serialization.NoEncryption()
    )
    with open(PRIV, "wb") as f:
        f.write(
            key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, enc
            )
        )
    os.chmod(PRIV, 0o600)
    with open(PUB, "wb") as f:
        f.write(pub_pem(key.public_key()))

    print(f"Приватный ключ: {PRIV}  (в git НЕ попадает, права 600)")
    print(f"Публичный ключ: {PUB}  (коммитится, едет в образ панели)")
    if not pw:
        print("\n! Ключ не зашифрован. Закройте пасфразой: python agent-signing/protect_key.py")
    print("\nДальше: python agent-signing/release.py <номер релиза агента>")


if __name__ == "__main__":
    main()
