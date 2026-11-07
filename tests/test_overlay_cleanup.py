"""Helper exit evidence must agree with its cleanup report."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from sightcircuit import overlay
from sightcircuit.overlay import OverlayClient, OverlaySink, bridge_closed_cleanly

ROOT = Path(__file__).resolve().parents[1]


def clean_close_report():
    native = {"report_kind": "sightcircuit_overlay_bridge", "exit_code": 0, "cleanup_ok": True}
    return {"worker_reaped": True, "worker_exit_code": 0, "transport_threads_stopped": True,
            "forced_termination": False, "error": None, "native_report": native}


class BridgeClosedCleanlyTests(unittest.TestCase):
    """The one "bridge closed cleanly" check, shared by every overlay and marker caller."""

    def test_clean_close_report_is_confirmed(self):
        self.assertIs(bridge_closed_cleanly(clean_close_report()), True)

    def test_each_failed_or_missing_outcome_is_unclean(self):
        changes = {
            "worker_not_reaped": lambda r: r.update(worker_reaped=False),
            "worker_exit_nonzero": lambda r: r.update(worker_exit_code=1),
            "worker_signalled": lambda r: r.update(worker_exit_code=-15),
            "worker_exit_unknown": lambda r: r.update(worker_exit_code=None),
            "transport_thread_alive": lambda r: r.update(transport_threads_stopped=False),
            "forced_termination": lambda r: r.update(forced_termination=True),
            "forced_termination_missing": lambda r: r.pop("forced_termination"),
            "client_error": lambda r: r.update(error="Overlay bridge disconnected"),
            "empty_client_error": lambda r: r.update(error=""),
            "native_cleanup_failed": lambda r: r["native_report"].update(cleanup_ok=False),
            "native_cleanup_missing": lambda r: r["native_report"].pop("cleanup_ok"),
            "native_exit_nonzero": lambda r: r["native_report"].update(exit_code=1),
            "native_report_missing": lambda r: r.update(native_report=None),
        }
        for name, change in changes.items():
            report = clean_close_report()
            change(report)
            with self.subTest(name):
                self.assertIs(bridge_closed_cleanly(report), False)

    def test_truthy_or_falsy_lookalikes_are_not_confirmation(self):
        for key, value in (("worker_reaped", 1), ("transport_threads_stopped", "yes"),
                           ("forced_termination", 0), ("worker_exit_code", False)):
            with self.subTest(key=key):
                self.assertIs(bridge_closed_cleanly(dict(clean_close_report(), **{key: value})), False)
        report = clean_close_report()
        report["native_report"]["cleanup_ok"] = 1
        self.assertIs(bridge_closed_cleanly(report), False)

    def test_owned_sink_close_consults_the_shared_check(self):
        report = {**clean_close_report(), "validation_only": True}
        report["native_report"].update(mode="--validate-stdin", runtime_initialized=False)

        class Client:
            validation_only = True

            def __init__(self, **_):
                pass

            def start(self):
                return self

            def close(self):
                return report

        for verdict in (True, False):
            sink = OverlaySink(client_factory=Client)
            sink.open()
            with self.subTest(verdict=verdict), \
                    patch.object(overlay, "bridge_closed_cleanly", return_value=verdict) as check:
                self.assertIs(sink.close(), verdict)
                check.assert_called_once_with(report)

    def test_callers_share_this_check_instead_of_repeating_it(self):
        sources = [*ROOT.glob("sightcircuit/**/*.py"), *ROOT.glob("scripts/*.py")]
        repeated = sorted(str(path.relative_to(ROOT)) for path in sources
                          if path != ROOT / "sightcircuit/overlay.py"
                          and '"transport_threads_stopped"' in path.read_text())
        self.assertEqual(repeated, [])


@unittest.skipUnless(os.name == "posix", "Executable local helper fixtures")
class OverlayCleanupTests(unittest.TestCase):
    # A clean helper, like the real bridge, exits only after the client's stop packet and EOF. Exiting at
    # once could break the pipe under the client's first settings heartbeat, which is not a clean close.
    CLEAN = "sys.stdin.buffer.read(); sys.exit(0)"

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

    def test_client_confirmation_is_the_shared_check(self):
        for ending, confirmed in ((self.CLEAN, True), ("sys.exit(7)", False), ("time.sleep(60)", False)):
            with self.subTest(ending=ending):
                report = self.client(ending).close()
                self.assertIs(report["cleanup_confirmed"], confirmed)
                self.assertIs(bridge_closed_cleanly(report), confirmed)

    def test_client_close_reports_the_shared_check_verdict(self):
        verdict = object()
        client = self.client(self.CLEAN)
        with patch.object(overlay, "bridge_closed_cleanly", return_value=verdict) as check:
            report = client.close()
        self.assertIs(report["cleanup_confirmed"], verdict)
        check.assert_called_once_with(report)

    def test_sigterm_escalation_is_forced_even_if_helper_reports_success(self):
        client = self.client("time.sleep(60)")
        report = client.close()
        self.assertTrue(report["worker_reaped"])
        self.assertTrue(report["transport_threads_stopped"])
        self.assertTrue(report["forced_termination"])
        self.assertTrue(report["error"])
