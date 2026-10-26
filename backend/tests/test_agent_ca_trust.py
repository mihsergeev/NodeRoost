"""Что нода соглашается положить в СИСТЕМНОЕ хранилище доверенных корней.

Корень панели доверяется на ноде всему: apt, pip, curl, браузеру. Отпечаток в
состоянии тут ничего не доказывает - его называет та же панель, которая присылает
корень. Единственная настоящая защита - ограничения имён в самом корне, и
проверять их надо по существу: раньше хватало слова "Excluded" где угодно в
расширении, то есть корень с одним выдуманным запретом проходил и дальше годился
для любого сайта.

Тест гоняет ТОТ ЖЕ блок кода, который уезжает на ноды: собирает apply.sh ровно
так, как это делает setup.sh, вырезает проверку и скармливает ей четыре корня.
"""

import datetime
import ipaddress
import shutil
import subprocess

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app import agent, ca

pytestmark = pytest.mark.skipif(
    not shutil.which("sh") or not shutil.which("openssl"),
    reason="нужны sh, openssl и awk - есть в CI и на нодах",
)


def _root(name: str, constraints) -> str:
    """Корень, какой могла бы выпустить захваченная панель."""
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    b = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
    )
    if constraints is not None:
        b = b.add_extension(constraints, critical=True)
    return b.sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM).decode()


def _check_block(tmp_path) -> str:
    """Собрать apply.sh как на ноде и вырезать из него проверку ограничений."""
    setup = agent.build_setup("https://hs.example/agent/tok")
    head, rest = setup.split('cat > "$DIR/apply.sh" <<EOF\n', 1)
    body = rest.split("\nEOF\n", 1)[0]
    runner = tmp_path / "runner.sh"
    runner.write_text(
        f'DIR="{tmp_path.as_posix()}"\n'
        'STATE_URL="https://hs.example/agent/tok"\n'
        'cat > "$DIR/apply.sh" <<EOF\n' + body + "\nEOF\n",
        encoding="utf-8",
        newline="\n",
    )
    subprocess.run(["sh", str(runner)], check=True, capture_output=True)
    apply_sh = (tmp_path / "apply.sh").read_text(encoding="utf-8")
    lines = apply_sh.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip().startswith("NCALL="))
    end = next(i for i in range(start, len(lines)) if lines[i] == "      fi")
    return "\n".join(lines[start : end + 1])


def _verdict(tmp_path, block: str, pem: str, tag: str) -> str:
    """Что скажет нода про этот корень: 1 - поставит, иначе откажется."""
    d = tmp_path / tag
    d.mkdir()
    (d / "ca").write_text(pem, encoding="utf-8", newline="\n")
    script = tmp_path / f"run-{tag}.sh"
    script.write_text(
        'TMPD="$1"\n' + block + '\necho "NC=$NC"\n', encoding="utf-8", newline="\n"
    )
    out = subprocess.run(
        ["sh", str(script), str(d)], capture_output=True, text=True
    ).stdout
    return out.strip().splitlines()[-1]


def test_node_takes_the_panel_root_but_not_a_root_that_only_looks_limited(tmp_path):
    block = _check_block(tmp_path)

    _, real = ca.build_root("NodeRoost internal CA", 10)
    assert _verdict(tmp_path, block, real, "real") == "NC=1"

    # так захваченная панель обходила прежнюю проверку: слово "Excluded" в
    # расширении есть, а запрещён один выдуманный домен - корню верят везде
    fake = _root(
        "Totally Legit CA",
        x509.NameConstraints(
            permitted_subtrees=None, excluded_subtrees=[x509.DNSName("nowhere.invalid")]
        ),
    )
    assert _verdict(tmp_path, block, fake, "fake") == "NC=0"

    bare = _root("No Constraints CA", None)
    assert _verdict(tmp_path, block, bare, "bare") == "NC=0"

    # белый список имён - другой способ ограничить корень, и он тоже годится
    allowlist = _root(
        "Old Style CA",
        x509.NameConstraints(
            permitted_subtrees=[x509.DNSName("lab"), x509.DNSName("mesh")],
            excluded_subtrees=[
                x509.IPAddress(ipaddress.IPv4Network("0.0.0.0/0")),
                x509.IPAddress(ipaddress.IPv6Network("::/0")),
            ],
        ),
    )
    assert _verdict(tmp_path, block, allowlist, "allow") == "NC=1"
