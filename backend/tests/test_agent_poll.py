"""Опрос исполняется настоящим sh после той же подстановки heredoc, что на ноде."""

import os
import shutil
import subprocess

import pytest

from app import agent


pytestmark = pytest.mark.skipif(not shutil.which("sh"), reason="нужен POSIX sh")


@pytest.fixture
def poll_script(tmp_path):
    setup = agent.build_setup("https://panel.example.test/agent/fake-poll-token")
    body = setup.split('cat > "$DIR/apply.sh" <<EOF\n', 1)[1].split("\nEOF\n", 1)[0]
    builder = tmp_path / "build.sh"
    builder.write_text(
        'DIR="$1"\n'
        'STATE_URL="https://panel.example.test/agent/fake-poll-token"\n'
        'API_URL="https://panel.example.test/agent/-"\n'
        'AGENT_TOKEN="fake-poll-token"\n'
        'cat > "$DIR/apply.sh" <<EOF\n' + body + "\nEOF\n",
        encoding="utf-8", newline="\n",
    )
    subprocess.run(["sh", str(builder), str(tmp_path)], check=True, capture_output=True)
    generated = tmp_path / "apply.sh"
    subprocess.run(["sh", "-n", str(generated)], check=True, capture_output=True)
    # Исполняем только опрос: после него идут изменения сети и хранилища CA.
    poll = generated.read_text(encoding="utf-8").split("# --- обновление агента:", 1)[0]
    script = tmp_path / "poll.sh"
    script.write_text(
        '''systemctl() { return "${MOCK_SYSTEMCTL_RC:-0}"; }
curl() {
  cat >/dev/null
  mock_out=
  mock_format=
  while [ "$#" -gt 0 ]; do
    case "$1" in
      -o) mock_out=$2; shift;;
      -w) mock_format=$2; shift;;
    esac
    shift
  done
  [ "$mock_format" = '%{http_code} %{time_total}' ] || return 99
  printf '%s' "$MOCK_BODY" > "$mock_out"
  printf '%s' "$MOCK_META"
  echo 'curl: https://panel.example.test/agent/fake-poll-token X-NodeRoost-Agent: fake-poll-token raw-error-canary' >&2
  return "$MOCK_RC"
}
''' + poll + '\nprintf "poll-accepted\\n"\n',
        encoding="utf-8", newline="\n",
    )
    return script


def run_poll(script, *, rc=0, meta="200 0.125000", body=None, active=True):
    return subprocess.run(
        ["sh", str(script)], capture_output=True, text=True, encoding="utf-8",
        env={
            **os.environ,
            "MOCK_RC": str(rc), "MOCK_META": meta,
            "MOCK_BODY": body if body is not None else (
                "routes=\nexit=false\nuse_exit=\nmesh_dns=false\nsensitive-body-canary\n"
            ),
            "MOCK_SYSTEMCTL_RC": "0" if active else "3",
            "TMPDIR": script.parent.as_posix(),
        },
        timeout=10,
    )


@pytest.mark.parametrize(
    ("rc", "status", "duration"),
    [(6, "000", "0.012000"), (7, "000", "0.030000"), (28, "000", "15.001000"),
     (60, "000", "0.040000"), (18, "200", "0.250000"), (63, "200", "0.125000"),
     (0, "401", "0.125000"), (0, "403", "0.125000"), (0, "503", "0.125000")],
)
def test_poll_reports_only_safe_failure_metadata_and_can_retry(poll_script, rc, status, duration):
    for _ in range(2):
        result = run_poll(poll_script, rc=rc, meta=f"{status} {duration}")
        # Временные ошибки не прерывают setup под set -e. Таймер повторит опрос.
        assert result.returncode == 0, result.stderr
        assert result.stdout == ""
        assert result.stderr == (
            "NodeRoost: опрос панели не выполнен "
            f"(curl_rc={rc} http_status={status} duration_s={duration})\n"
        )
    assert not list(poll_script.parent.glob("tmp.*")), "временное состояние удалено"
    recovered = run_poll(poll_script)
    assert recovered.returncode == 0 and recovered.stdout == "poll-accepted\n"
    assert recovered.stderr == ""


def test_poll_success_is_quiet_and_accepts_state(poll_script):
    result = run_poll(poll_script)
    assert result.returncode == 0
    assert result.stdout == "poll-accepted\n"
    assert result.stderr == ""


def test_poll_missing_node_preserves_removal_advice(poll_script):
    result = run_poll(poll_script, meta="404 0.125000")
    assert result.returncode == 0 and result.stdout == ""
    assert "curl_rc=0 http_status=404 duration_s=0.125000" in result.stderr
    assert "панель больше не знает этот узел" in result.stderr
    assert "systemctl disable --now noderoost-agent.timer" in result.stderr
    for secret in ("example.test", "fake-poll-token", "raw-error-canary", "sensitive-body-canary"):
        assert secret not in result.stderr


@pytest.mark.parametrize("meta", ["", "200 unsafe-metadata-canary", "unsafe-metadata-canary 0.1"])
def test_poll_does_not_log_unexpected_metadata(poll_script, meta):
    result = run_poll(poll_script, rc=7, meta=meta)
    assert result.returncode == 0 and result.stdout == ""
    assert "unsafe-metadata-canary" not in result.stderr
    assert "http_status=000" in result.stderr or "duration_s=unknown" in result.stderr


def test_poll_rejects_invalid_body_and_leaves_stopped_daemon_alone(poll_script):
    invalid = run_poll(poll_script, body="sensitive-body-canary\n")
    stopped = run_poll(poll_script, active=False)
    for result in (invalid, stopped):
        assert result.returncode == 0
        assert result.stdout == result.stderr == ""


def test_poll_keeps_timer_retry_schedule():
    setup = agent.build_setup("https://panel.example.test/agent/fake-poll-token")
    assert "OnBootSec=20\nOnUnitActiveSec=60\n" in setup
