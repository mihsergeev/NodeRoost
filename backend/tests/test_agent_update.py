"""Самообновление агента исполняется настоящим sh: подпись, подстановки, токен.

Раньше блок обновления тестами не исполнялся вовсе - проверялось только наличие
строк в шаблоне. Здесь apply.sh собирается так же, как на ноде, панель
подменяется функцией curl, а релиз подписывается тестовым ключом.
"""

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app import agent

pytestmark = pytest.mark.skipif(
    not all(shutil.which(t) for t in ("sh", "openssl", "base64", "sha256sum")),
    reason="нужны sh, openssl, base64, sha256sum",
)

TOKEN = "fake-update-token"
# символы | и & в адресе ломали sed-подстановку - проверяем и их
STATE_URL = f"https://panel.example.test/a&b|c/agent/{TOKEN}"
FAKE_TEMPLATE = (
    "#!/bin/sh\n"
    'printf "%s\\n" "url=@@STATE_URL@@" "v=@@SCRIPT_V@@" "rel=@@RELEASE@@" "pub=@@PUBKEY@@" > "$UPDATE_OUT"\n'
)


def _build_apply(tmp_path, monkeypatch, pub_b64, release):
    monkeypatch.setattr(agent, "pubkey_b64", lambda: pub_b64)
    monkeypatch.setattr(agent, "release", lambda: release)
    setup = agent.build_setup(STATE_URL)
    body = setup.split('cat > "$DIR/apply.sh.new" <<EOF\n', 1)[1].split("\nEOF\n", 1)[0]
    node = tmp_path / "node"
    node.mkdir()
    builder = tmp_path / "build.sh"
    builder.write_text(
        'DIR="$1"\n'
        f"STATE_URL='{STATE_URL}'\n"
        "API_URL='https://panel.example.test/a&b|c/agent/-'\n"
        f"AGENT_TOKEN='{TOKEN}'\n"
        'cat > "$DIR/apply.sh" <<EOF\n' + body + "\nEOF\n",
        encoding="utf-8", newline="\n",
    )
    subprocess.run(["sh", str(builder), str(node)], check=True, capture_output=True)
    return node / "apply.sh"


def _release_files(served, key, release, template):
    manifest = json.dumps(
        {"release": release, "script_sha256": hashlib.sha256(template.encode()).hexdigest(),
         "released_at": "2026-11-23T00:00:00+00:00"},
        sort_keys=True, separators=(",", ":"),
    ).encode()
    (served / "manifest").write_bytes(manifest)
    (served / "manifest.sig").write_bytes(key.sign(manifest, ec.ECDSA(hashes.SHA256())))


def _run(apply_sh, tmp_path, served, want_release):
    (served / "state").write_text(
        f"routes=\nexit=false\nuse_exit=\nmesh_dns=\nupdate={want_release}\n", encoding="utf-8"
    )
    argv_log = tmp_path / "argv.log"
    temp = tmp_path / "temp"
    temp.mkdir(exist_ok=True)
    out = tmp_path / "setup.out"
    script = tmp_path / "run.sh"
    script.write_text(
        '''systemctl() { return 0; }
sed() { printf '%s\\n' "sed $*" >> "$ARGV_LOG"; command sed "$@"; }
curl() {
  cat >/dev/null
  mock_out=; mock_format=; mock_url=
  while [ "$#" -gt 0 ]; do
    case "$1" in
      -o) mock_out=$2; shift;;
      -w) mock_format=$2; shift;;
      -K|--max-time|--max-filesize|-X|--data-binary) shift;;
      -*) ;;
      *) mock_url=$1;;
    esac
    shift
  done
  case "$mock_url" in
    */-) f=state;;
    */-/manifest) f=manifest;;
    */-/manifest.sig) f=manifest.sig;;
    */-/setup.tmpl) f=setup.tmpl;;
    *) f=;;
  esac
  [ -n "$f" ] && [ -n "$mock_out" ] && cat "$SERVED/$f" > "$mock_out"
  [ -n "$mock_format" ] && printf '200 0.100000'
  return 0
}
''' + apply_sh.read_text(encoding="utf-8"),
        encoding="utf-8", newline="\n",
    )
    result = subprocess.run(
        ["sh", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=30,
        env={**os.environ, "SERVED": str(served), "ARGV_LOG": str(argv_log),
             "UPDATE_OUT": str(out), "TMPDIR": str(temp)},
    )
    return result, out, argv_log, temp


@pytest.fixture
def signing_key():
    return ec.generate_private_key(ec.SECP256R1())


def _pub_b64(key):
    pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return base64.b64encode(pem).decode()


def test_signed_update_installs_with_own_values_and_hides_the_token(tmp_path, monkeypatch, signing_key):
    """Регрессия: адрес состояния с токеном уходил в аргументы sed и был виден
    любому пользователю ноды через ps; каталог обновления с копией адреса
    оставался после установки."""
    pub = _pub_b64(signing_key)
    apply_sh = _build_apply(tmp_path, monkeypatch, pub, release=5)
    served = tmp_path / "served"
    served.mkdir()
    (served / "setup.tmpl").write_text(FAKE_TEMPLATE, encoding="utf-8", newline="\n")
    _release_files(served, signing_key, 6, FAKE_TEMPLATE)

    result, out, argv_log, temp = _run(apply_sh, tmp_path, served, 6)
    assert result.returncode == 0, result.stderr
    assert "подпись проверена" in result.stderr
    lines = out.read_text(encoding="utf-8").splitlines()
    digest = hashlib.sha256(FAKE_TEMPLATE.encode()).hexdigest()
    # значения узел подставил свои: адрес (с | и &), ключ и номер из манифеста
    assert lines == [f"url={STATE_URL}", f"v={digest[:8]}", "rel=6", f"pub={pub}"]
    assert TOKEN not in argv_log.read_text(encoding="utf-8")
    assert not any(temp.iterdir()), "временные каталоги обновления удалены"


def test_tampered_template_is_refused(tmp_path, monkeypatch, signing_key):
    apply_sh = _build_apply(tmp_path, monkeypatch, _pub_b64(signing_key), release=5)
    served = tmp_path / "served"
    served.mkdir()
    _release_files(served, signing_key, 6, FAKE_TEMPLATE)
    (served / "setup.tmpl").write_text(FAKE_TEMPLATE + "id\n", encoding="utf-8", newline="\n")

    result, out, _, temp = _run(apply_sh, tmp_path, served, 6)
    assert not out.exists()
    assert "манифест не сошёлся" in result.stderr
    assert not any(temp.iterdir())


def test_foreign_signature_is_refused(tmp_path, monkeypatch, signing_key):
    apply_sh = _build_apply(tmp_path, monkeypatch, _pub_b64(signing_key), release=5)
    served = tmp_path / "served"
    served.mkdir()
    (served / "setup.tmpl").write_text(FAKE_TEMPLATE, encoding="utf-8", newline="\n")
    _release_files(served, ec.generate_private_key(ec.SECP256R1()), 6, FAKE_TEMPLATE)

    result, out, _, _ = _run(apply_sh, tmp_path, served, 6)
    assert not out.exists()
    assert "ПОДПИСЬ ОБНОВЛЕНИЯ НЕВЕРНА" in result.stderr


def test_key_without_a_release_number_is_not_embedded(monkeypatch):
    """Регрессия: ключ в образе есть, а манифест потерян - узел получал ключ и
    AGENT_RELEASE=0 и принял бы любой подписанный релиз, то есть откат на самый
    старый."""
    monkeypatch.setattr(agent, "pubkey_b64", lambda: "c29tZS1rZXk=")
    monkeypatch.setattr(agent, "release", lambda: 0)
    setup = agent.build_setup("https://hs.example/agent/tok")
    assert 'AGENT_PUB_B64=""' in setup
    assert "нет ключа подписи агента" in setup


@pytest.mark.parametrize("name,ok", [
    ("nas.lab", True), ("-x", False), ("--hook-option", False), ("../etc", False), ("a..b", False),
])
def test_certificate_names_with_a_leading_dash_are_refused(name, ok):
    """Имя уходит в cert-hook.sh аргументом: ведущий «-» выглядел бы опцией."""
    m = re.search(r'case "\\\$NAME" in\n\s*(.+?)\)\n', agent.TEMPLATE)
    assert m, "в шаблоне нет проверки имени сертификата"
    pattern = m.group(1)
    script = f'case "$1" in {pattern}) echo bad;; *) echo good;; esac'
    out = subprocess.run(["sh", "-c", script, "_", name], capture_output=True, text=True).stdout.strip()
    assert out == ("good" if ok else "bad")
