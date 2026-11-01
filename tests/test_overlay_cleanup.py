"""Helper exit evidence must agree with its cleanup report."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from framejacker.overlay import OverlayClient


@unittest.skipUnless(os.name == "posix", "Executable local helper fixtures")
class OverlayCleanupTests(unittest.TestCase):
    def client(self, ending):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        binary = Path(temporary.name) / "fixture"
        ready = {"event": "ready", "validation_only": True, "runtime_initialized": False}
        closed = {"event": "closed", "exit_code": 0, "cleanup_ok": True,
                  "runtime_initialized": False, "mode": "--validate-stdin"}
        binary.write_text(f"#!{sys.executable}\nimport signal, sys, time\n"
            "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))\n"
            f"print({json.dumps(ready)!r}, flush=True)\nprint({json.dumps(closed)!r}, flush=True)\n{ending}\n")
        binary.chmod(0o700)
        client = OverlayClient(binary, validation_only=True).start()
        self.addCleanup(client.close)
        return client

    def test_nonzero_exit_overrides_successful_final_report(self):
        client = self.client("sys.exit(7)")
        report = client.close()
        self.assertEqual(report["worker_exit_code"], 7)
        self.assertTrue(report["worker_reaped"])
        self.assertTrue(report["error"])

    def test_sigterm_escalation_is_forced_even_if_helper_reports_success(self):
        client = self.client("time.sleep(60)")
        report = client.close()
        self.assertTrue(report["worker_reaped"])
        self.assertTrue(report["transport_threads_stopped"])
        self.assertTrue(report["forced_termination"])
        self.assertTrue(report["error"])
