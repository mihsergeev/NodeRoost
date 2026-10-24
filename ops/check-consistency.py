#!/usr/bin/env python3
"""Сверки, которые нельзя поймать тестами: версия в четырёх файлах и подписанный
релиз агента.

Запускается в CI и руками перед релизом:

    python ops/check-consistency.py

1. Версия. Она лежит в четырёх местах - backend/app/config.py, pyproject.toml,
   frontend/package.json и .env.example. Разъехавшись, они дают самый неприятный
   вид поломки: панель внизу страницы показывает одно, compose тянет образ другой
   версии, и сборка в реестре может вообще не существовать.

2. Релиз агента. Панель раздаёт нодам текст из `app/agent.TEMPLATE`, а подписан
   манифест с sha256 этого текста. Любая правка шаблона без повторной подписи
   означает, что кнопка обновления агента в панели просто не появится (панель
   честно скажет, что релиз не подписан), и узнаётся это уже на проде.
   Подпись тоже проверяем: публичный ключ лежит в репозитории, приватный - нет.
"""

import hashlib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
problems: list[str] = []


def read(*parts: str) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def grab(where: str, pattern: str, text: str) -> str | None:
    m = re.search(pattern, text, re.M)
    if not m:
        problems.append(f"{where}: не нашёл версию по шаблону {pattern!r}")
        return None
    return m.group(1)


def check_versions() -> None:
    found = {
        "backend/app/config.py": grab(
            "backend/app/config.py",
            r'^\s*version: str = "([^"]+)"',
            read("backend", "app", "config.py"),
        ),
        "backend/pyproject.toml": grab(
            "backend/pyproject.toml",
            r'^version = "([^"]+)"',
            read("backend", "pyproject.toml"),
        ),
        "frontend/package.json": json.loads(read("frontend", "package.json")).get("version"),
        ".env.example": grab(
            ".env.example", r"^NODEROOST_VERSION=(.+)$", read(".env.example")
        ),
    }
    if len(set(v for v in found.values() if v)) > 1:
        problems.append(
            "версии разъехались: "
            + ", ".join(f"{k} = {v}" for k, v in found.items())
        )
    else:
        print("версия:", next(iter(set(found.values()))), "- во всех четырёх файлах")


def check_agent_release() -> None:
    sys.path.insert(0, os.path.join(ROOT, "backend"))
    from app import agent  # noqa: PLC0415

    digest = hashlib.sha256(agent.TEMPLATE.encode()).hexdigest()
    dist = os.path.join(ROOT, "backend", "app", "agent_dist")
    mpath = os.path.join(dist, "manifest.json")
    if not os.path.exists(mpath):
        problems.append("нет backend/app/agent_dist/manifest.json - релиз агента не подписан")
        return
    with open(mpath, "rb") as fh:
        raw = fh.read()
    manifest = json.loads(raw.decode("utf-8"))
    if manifest.get("script_sha256") != digest:
        problems.append(
            "шаблон агента правили без повторной подписи: sha256 шаблона "
            f"{digest[:16]}..., в манифесте {str(manifest.get('script_sha256'))[:16]}...\n"
            f"    подпишите: python agent-signing/release.py {manifest.get('release', 0) + 1}"
        )
        return
    print("агент: релиз", manifest.get("release"), "совпадает с шаблоном")

    # Подпись: публичный ключ в репозитории, так что проверить можно и в CI.
    sig = os.path.join(dist, "manifest.sig")
    pub = os.path.join(ROOT, "agent-signing", "noderoost-agent.pub")
    if not (os.path.exists(sig) and os.path.exists(pub)):
        problems.append("нет manifest.sig или noderoost-agent.pub - подпись не проверить")
        return
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
    except ImportError:
        print("подпись не проверена: нет пакета cryptography")
        return
    with open(pub, "rb") as fh:
        key = serialization.load_pem_public_key(fh.read())
    with open(sig, "rb") as fh:
        signature = fh.read()
    try:
        key.verify(signature, raw, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature:
        problems.append("подпись манифеста агента не сходится с agent-signing/noderoost-agent.pub")
    else:
        print("агент: подпись манифеста сходится")


check_versions()
check_agent_release()

if problems:
    print()
    for p in problems:
        print("ОШИБКА:", p)
    sys.exit(1)
print("сверки пройдены")
