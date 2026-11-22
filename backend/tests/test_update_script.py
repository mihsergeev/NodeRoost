"""ops/update.sh: локальный образ headscale не должен подменять релизные образы панели.

Headscale можно поставить из своей сборки (deploy/headscale,
NODEROOST_HEADSCALE_IMAGE=local/...). Такого образа нет в реестре, общий
`docker compose pull` на нем падал, и скрипт уходил в ветку «образов нет -
собираю на месте»: backend и frontend собирались из исходников поверх
проверенных релизных образов.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

UPDATE = Path(__file__).resolve().parents[2] / "ops" / "update.sh"

# Подставной docker: пишет вызовы в журнал. Pull без явного списка сервисов
# тянет и headscale из .env - его в реестре нет, поэтому такой pull падает.
# TEST_REGISTRY_EMPTY=1 - в реестре нет и образов панели (свой форк).
FAKE_DOCKER = """#!/bin/sh
echo "$*" >> "$TEST_DOCKER_LOG"
if [ "$1 $2" = "compose pull" ]; then
    [ "${TEST_REGISTRY_EMPTY:-0}" = 1 ] && exit 1
    case " $* " in
        *" backend "*) exit 0 ;;
        *) echo "pull access denied for local/noderoost/headscale" >&2; exit 1 ;;
    esac
fi
exit 0
"""


@unittest.skipUnless(sys.platform == "linux" and shutil.which("bash"), "requires Linux bash")
class TestUpdateScript(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        app = root / "app"
        (app / "ops").mkdir(parents=True)
        (app / "data").mkdir()
        # хостовые помощники вне каталога панели в тесте не ставим
        script = UPDATE.read_text(encoding="utf-8").replace(
            "if mkdir -p /lib65/noderoost 2>/dev/null; then", "if false; then"
        )
        self.assertNotEqual(script, UPDATE.read_text(encoding="utf-8"))
        (app / "ops" / "update.sh").write_text(script, encoding="utf-8")
        owner = app / "ops" / "data-owner.sh"
        owner.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        owner.chmod(0o755)
        (app / "compose.yml").write_text("services: {}\n", encoding="utf-8")
        (app / ".env").write_text(
            "NODEROOST_VERSION=0.1.0\n"
            "NODEROOST_HEADSCALE_IMAGE=local/noderoost/headscale:test\n",
            encoding="utf-8",
        )
        (app / ".env.example").write_text("NODEROOST_VERSION=0.2.0\n", encoding="utf-8")
        bin_dir = root / "bin"
        bin_dir.mkdir()
        docker = bin_dir / "docker"
        docker.write_text(FAKE_DOCKER, encoding="utf-8")
        docker.chmod(0o755)
        self.app = app
        self.log = root / "docker.log"
        self.env = dict(
            os.environ,
            PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            TEST_DOCKER_LOG=str(self.log),
        )

    def run_update(self, **env):
        result = subprocess.run(
            ["bash", str(self.app / "ops" / "update.sh")],
            env={**self.env, **env},
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return self.log.read_text(encoding="utf-8").splitlines()

    def test_local_headscale_image_keeps_release_images(self):
        calls = self.run_update()
        self.assertIn("compose pull --quiet backend frontend", calls)
        self.assertIn("compose build db", calls)
        self.assertNotIn("compose build", calls)  # полная сборка на месте
        self.assertIn("compose up -d", calls)
        self.assertIn(
            "NODEROOST_VERSION=0.2.0",
            (self.app / ".env").read_text(encoding="utf-8").splitlines(),
        )

    def test_no_release_images_builds_locally(self):
        calls = self.run_update(TEST_REGISTRY_EMPTY="1")
        self.assertIn("compose build", calls)
        self.assertIn("compose up -d", calls)
