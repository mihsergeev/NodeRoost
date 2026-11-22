"""ops/update.sh: что тянется, что собирается и какой версией скрипта.

Headscale можно поставить из своей сборки (deploy/headscale,
NODEROOST_HEADSCALE_IMAGE=local/...). Такого образа нет в реестре, общий
`docker compose pull` на нем падал, и скрипт уходил в ветку «образов нет -
собираю на месте»: backend и frontend собирались из исходников поверх
проверенных релизных образов.

Кроме того, bash продолжал выполнять старую версию update.sh после того, как git
обновил файл, - исправления в самом скрипте доходили только со следующего раза.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

UPDATE = Path(__file__).resolve().parents[2] / "ops" / "update.sh"
NEW_SCRIPT_MARKER = 'echo ">> NEW SCRIPT"'

# Подставной docker: пишет вызовы в журнал. Pull без явного списка сервисов
# тянет и headscale из .env - его в реестре нет, поэтому такой pull падает.
# TEST_REGISTRY_EMPTY=1 - в реестре нет и образов панели (свой форк).
FAKE_DOCKER = """#!/bin/sh
echo "$*" >> "$TEST_DOCKER_LOG"
if [ "$1 $2" = "compose build" ]; then
    echo "attestations-off=${BUILDX_NO_DEFAULT_ATTESTATIONS:-}" >> "$TEST_DOCKER_LOG"
fi
if [ "$1 $2" = "compose pull" ]; then
    [ "${TEST_REGISTRY_EMPTY:-0}" = 1 ] && exit 1
    case " $* " in
        *" backend "*) exit 0 ;;
        *) echo "pull access denied for local/noderoost/headscale" >&2; exit 1 ;;
    esac
fi
exit 0
"""


def _script(extra: str = "") -> str:
    text = UPDATE.read_text(encoding="utf-8")
    # хостовые помощники вне каталога панели в тесте не ставим
    patched = text.replace("if mkdir -p /lib65/noderoost 2>/dev/null; then", "if false; then")
    assert patched != text
    if extra:
        patched = patched.replace("set -euo pipefail\n", f"set -euo pipefail\n{extra}\n", 1)
    return patched


@unittest.skipUnless(sys.platform == "linux" and shutil.which("bash"), "requires Linux bash")
class TestUpdateScript(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        docker = bin_dir / "docker"
        docker.write_text(FAKE_DOCKER, encoding="utf-8")
        docker.chmod(0o755)
        self.log = self.root / "docker.log"
        self.env = dict(
            os.environ,
            PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            TEST_DOCKER_LOG=str(self.log),
            GIT_AUTHOR_NAME="Test",
            GIT_AUTHOR_EMAIL="test@example.invalid",
            GIT_COMMITTER_NAME="Test",
            GIT_COMMITTER_EMAIL="test@example.invalid",
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_CONFIG_NOSYSTEM="1",
        )
        self.env.pop("NODEROOST_UPDATE_REEXEC", None)

    def _write_release(self, app: Path, version: str, extra: str = "") -> None:
        (app / "ops").mkdir(parents=True, exist_ok=True)
        update = app / "ops" / "update.sh"
        update.write_text(_script(extra), encoding="utf-8")
        update.chmod(0o755)
        owner = app / "ops" / "data-owner.sh"
        owner.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        owner.chmod(0o755)
        (app / "compose.yml").write_text("services: {}\n", encoding="utf-8")
        (app / ".env.example").write_text(f"NODEROOST_VERSION={version}\n", encoding="utf-8")

    def _install(self, app: Path) -> None:
        (app / "data").mkdir(exist_ok=True)
        (app / ".env").write_text(
            "NODEROOST_VERSION=0.1.0\n"
            "NODEROOST_HEADSCALE_IMAGE=local/noderoost/headscale:test\n",
            encoding="utf-8",
        )

    def _git(self, cwd: Path, *args: str) -> None:
        subprocess.run(["git", *args], cwd=cwd, env=self.env, check=True,
                       capture_output=True, timeout=30)

    def plain_install(self) -> Path:
        app = self.root / "app"
        self._write_release(app, "0.2.0")
        self._install(app)
        return app

    def run_update(self, app: Path, **env):
        result = subprocess.run(
            ["bash", str(app / "ops" / "update.sh")],
            env={**self.env, **env},
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout, self.log.read_text(encoding="utf-8").splitlines()

    def test_local_headscale_image_keeps_release_images(self):
        app = self.plain_install()
        _, calls = self.run_update(app)
        self.assertIn("compose pull --quiet backend frontend", calls)
        self.assertIn("compose build db", calls)
        self.assertNotIn("compose build", calls)  # полная сборка на месте
        self.assertIn("compose up -d", calls)
        self.assertIn(
            "NODEROOST_VERSION=0.2.0",
            (app / ".env").read_text(encoding="utf-8").splitlines(),
        )

    def test_no_release_images_builds_locally(self):
        app = self.plain_install()
        _, calls = self.run_update(app, TEST_REGISTRY_EMPTY="1")
        self.assertIn("compose build", calls)
        self.assertIn("compose up -d", calls)

    def test_local_builds_without_attestations(self):
        # с provenance-аттестацией ID образа db менялся на каждой сборке, и
        # compose перезапускал Postgres при каждом обновлении
        app = self.plain_install()
        _, calls = self.run_update(app)
        builds = [i for i, c in enumerate(calls) if c.startswith("compose build")]
        self.assertTrue(builds)
        for i in builds:
            self.assertEqual(calls[i + 1], "attestations-off=1")

    @unittest.skipUnless(shutil.which("git"), "requires git")
    def test_updated_script_continues_with_new_version(self):
        origin = self.root / "origin"
        self._write_release(origin, "0.1.0")
        (origin / ".gitignore").write_text(".env\ndata/\n", encoding="utf-8")
        self._git(origin, "init", "-q")
        self._git(origin, "add", "-A")
        self._git(origin, "commit", "-q", "-m", "release 0.1.0")
        self._git(origin, "tag", "v0.1.0")
        self._write_release(origin, "0.2.0", extra=NEW_SCRIPT_MARKER)
        self._git(origin, "commit", "-q", "-am", "release 0.2.0")
        self._git(origin, "tag", "v0.2.0")

        app = self.root / "app"
        self._git(self.root, "clone", "-q", str(origin), str(app))
        self._git(app, "-c", "advice.detachedHead=false", "checkout", "-q", "v0.1.0")
        self._install(app)

        out, calls = self.run_update(app)
        self.assertIn("скрипт обновления изменился", out)
        self.assertEqual(out.count(">> NEW SCRIPT"), 1, out)  # новая версия, ровно один раз
        self.assertIn("compose up -d", calls)
        self.assertIn(
            "NODEROOST_VERSION=0.2.0",
            (app / ".env").read_text(encoding="utf-8").splitlines(),
        )

    @unittest.skipUnless(shutil.which("git"), "requires git")
    def test_unchanged_script_runs_once(self):
        origin = self.root / "origin"
        self._write_release(origin, "0.2.0")
        (origin / ".gitignore").write_text(".env\ndata/\n", encoding="utf-8")
        self._git(origin, "init", "-q")
        self._git(origin, "add", "-A")
        self._git(origin, "commit", "-q", "-m", "release 0.2.0")
        self._git(origin, "tag", "v0.2.0")
        app = self.root / "app"
        self._git(self.root, "clone", "-q", str(origin), str(app))
        self._git(app, "-c", "advice.detachedHead=false", "checkout", "-q", "v0.2.0")
        self._install(app)

        out, _ = self.run_update(app)
        self.assertNotIn("скрипт обновления изменился", out)
        self.assertEqual(out.count(">> забираю свежий код"), 1, out)
