#!/usr/bin/env python3
"""Подписать текущий скрипт агента. Одна команда:

    python agent-signing/release.py 4

Что делает:

1. Берёт ТОТ ЖЕ текст скрипта, который панель раздаёт нодам (`app/agent.TEMPLATE`)
   — не отдельную копию: две копии одного скрипта разъезжаются, и подписанным
   оказывается не то, что выполняется.
2. Складывает канонический манифест: номер релиза, sha256 скрипта, дата.
3. Подписывает офлайн-ключом, тут же проверяет подпись сам и кладёт манифест с
   подписью в `backend/app/agent_dist/` — оттуда их раздаёт панель.

Номер релиза — целое и **только вверх**: агент отказывается ставить релиз не
новее своего. Это защита от отката, когда захваченная панель пытается вернуть
ноды на старую (уязвимую, но подписанную) версию.

Сама панель подписать ничего не может: приватного ключа на её сервере нет.
"""

import json
import os
import sys
from datetime import datetime, timezone

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _signing import (  # noqa: E402
    DIST,
    PUB,
    agent_template,
    load_signing_key,
    pub_pem,
    read_pub,
    sha256,
    sign,
)


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if len(args) != 1 or not args[0].isdigit():
        sys.exit("Использование: release.py <номер релиза>  (целое, только вверх)")
    release = int(args[0])

    key = load_signing_key()
    if pub_pem(key.public_key()) != read_pub():
        sys.exit(f"Ключ подписи не соответствует {PUB} — прерываю.")

    template = agent_template().encode()
    digest = sha256(template)

    os.makedirs(DIST, exist_ok=True)
    prev = os.path.join(DIST, "manifest.json")
    if os.path.exists(prev):
        old = json.loads(open(prev, encoding="utf-8").read())
        if release <= old.get("release", 0):
            sys.exit(
                f"Прошлый релиз {old['release']}, а этот {release}. "
                "Номер должен расти: агенты не принимают релиз не новее своего."
            )
        if old.get("script_sha256") == digest:
            sys.exit("Скрипт агента не менялся — подписывать нечего.")

    manifest = {
        "release": release,
        "script_sha256": digest,
        "released_at": datetime.now(timezone.utc).isoformat(),
    }
    # канонический вид: подписывается ровно та последовательность байтов, которую
    # потом проверит нода, — без «красивого» форматирования и перестановок ключей
    blob = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    sig = sign(key, blob)
    # Само-проверка: подписали ровно те байты, которые сейчас положим в файл.
    key.public_key().verify(sig, blob, ec.ECDSA(hashes.SHA256()))

    with open(os.path.join(DIST, "manifest.json"), "wb") as f:
        f.write(blob)
    with open(os.path.join(DIST, "manifest.sig"), "wb") as f:
        f.write(sig)
    with open(os.path.join(DIST, "agent.pub"), "wb") as f:
        f.write(read_pub())

    print(f"Релиз агента {release} подписан и само-проверен ✓")
    print(f"  скрипт  sha256={digest[:16]}…  ({len(template)} байт)")
    print(f"  {os.path.join(DIST, 'manifest.json')}")
    print(f"  {os.path.join(DIST, 'manifest.sig')}")
    print("\nДальше: закоммитить, выпустить панель, и в карточке ноды — «Обновить агента».")


if __name__ == "__main__":
    main()
