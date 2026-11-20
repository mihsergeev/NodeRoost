import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


WATCHDOG = Path(__file__).resolve().parents[2] / "ops" / "panel-watchdog.sh"


@unittest.skipUnless(sys.platform == "linux" and shutil.which("flock"), "requires Linux flock")
class TestPanelWatchdog(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.heartbeat = root / "heartbeat"
        self.state = root / "watchdog.state"
        self.deliveries = root / "deliveries"
        self.script = root / WATCHDOG.name
        self.script.write_text(
            WATCHDOG.read_text(encoding="utf-8").replace(
                "STATE=/lib65/noderoost/watchdog.state\n", f'STATE="{self.state}"\n'
            ),
            encoding="utf-8",
        )
        (root / "send-host-alert.py").write_text(
            "import os, sys\n"
            "with open(os.environ['TEST_DELIVERIES'], 'a', encoding='utf-8') as stream:\n"
            "    stream.write(sys.stdin.read() + '\\n')\n"
            "raise SystemExit(int(os.environ['TEST_SEND_RESULT']))\n",
            encoding="utf-8",
        )
        self.env = dict(
            os.environ,
            NODEROOST_APP=str(root),
            NODEROOST_HEARTBEAT=str(self.heartbeat),
            NODEROOST_WD_STRIKES="2",
            NODEROOST_HB_MAX_AGE="600",
            TEST_DELIVERIES=str(self.deliveries),
        )

    def tick(self, alerts_ok, *, send_result=1, age=0):
        self.heartbeat.write_text(
            f"ts={int(time.time()) - age}\npanel=example.invalid\nalerts_ok={alerts_ok}\n",
            encoding="utf-8",
        )
        subprocess.run(
            ["bash", str(self.script)],
            env=dict(self.env, TEST_SEND_RESULT=str(send_result)),
            capture_output=True,
            check=True,
            timeout=5,
        )

    def messages(self):
        return self.deliveries.read_text(encoding="utf-8").splitlines() if self.deliveries.exists() else []

    def test_channel_failure_and_recovery_are_sent_once_even_without_ack(self):
        self.tick(0)
        self.assertEqual(self.messages(), [])
        for _ in range(100):
            self.tick(0)
        self.assertEqual(len(self.messages()), 1)
        for _ in range(4):
            self.tick(1)
        self.assertEqual(len(self.messages()), 2)
        self.assertIn("канал алертов снова работает", self.messages()[1])
        self.tick(0)
        self.tick(0)
        self.assertEqual(len(self.messages()), 3)

    def test_channel_sends_one_notice_and_recovery_when_accepted(self):
        for _ in range(6):
            self.tick(0, send_result=0)
        self.assertEqual(len(self.messages()), 1)
        self.assertIn("проверка канала алертов не прошла", self.messages()[0])
        for _ in range(4):
            self.tick(1, send_result=0)
        self.assertEqual(len(self.messages()), 2)
        self.assertIn("канал алертов снова работает", self.messages()[1])

    def test_panel_down_still_retries_after_channel_problem(self):
        self.tick(0)
        self.tick(0)
        self.tick(0, age=700)
        self.tick(0, age=700, send_result=0)
        self.tick(0, age=700, send_result=0)
        self.assertEqual(len(self.messages()), 3)
        self.assertIn("панель молчит", self.messages()[1])
        self.tick(1, send_result=0)
        self.assertIn("панель снова в норме", self.messages()[-1])
        self.assertEqual(len(self.messages()), 4)

    def test_overlapping_run_leaves_pending_transition_to_lock_owner(self):
        import fcntl

        self.tick(0)
        with self.state.with_suffix(".state.lock").open("wb") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.tick(0)
        self.assertEqual(self.messages(), [])
        self.tick(0)
        self.assertEqual(len(self.messages()), 1)
