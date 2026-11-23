"""ops/check-consistency.py - сверка подписи агента тем ключом, что идет в узлы."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FILES = (
    "ops/check-consistency.py",
    "backend/pyproject.toml",
    "frontend/package.json",
    ".env.example",
    "agent-signing/noderoost-agent.pub",
)


def _copy(tmp: Path) -> None:
    for rel in FILES:
        if not (REPO / rel).exists():
            pytest.skip(f"нет {rel} в рабочем дереве")
        (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / rel, tmp / rel)
    shutil.copytree(
        REPO / "backend" / "app", tmp / "backend" / "app",
        ignore=shutil.ignore_patterns("__pycache__"),
    )


def _run(tmp: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(tmp / "ops" / "check-consistency.py")],
        capture_output=True, text=True, encoding="utf-8", check=False,
    )


def test_repository_passes(tmp_path):
    _copy(tmp_path)
    result = _run(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_node_key_must_match_the_signing_key(tmp_path):
    """Регрессия: подпись сверялась с agent-signing/noderoost-agent.pub, а в узлы
    вшивается agent_dist/agent.pub. Подмена одного agent_dist/agent.pub проходила
    CI - и новые узлы получали ключ, владелец которого обновлял бы на них агента."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    _copy(tmp_path)
    stranger = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    (tmp_path / "backend" / "app" / "agent_dist" / "agent.pub").write_bytes(stranger)
    result = _run(tmp_path)
    assert result.returncode == 1
    assert "agent_dist/agent.pub" in result.stdout
