"""The shared mirror launcher, image-output publication and stop-on-signal helpers, without hardware."""
import hashlib
import os
from pathlib import Path
import signal
import tempfile
import threading
import unittest
from unittest.mock import patch

from sightcircuit.errors import OutputError
from sightcircuit.runtime import guard, output, supervision
from sightcircuit.sources import mirror

CANDIDATE = {"node": "/dev/fixture", "name": "SteamVR"}
DISCOVERY = {"status": "candidates_found", "candidates": [CANDIDATE]}
WORKER = object()  # Never run: these tests replace the supervisor.


def restore_handlers(test, *numbers):
    for number in numbers:
        test.addCleanup(signal.signal, number, signal.getsignal(number))


class StopOnSignalTests(unittest.TestCase):
    def test_sigterm_raises_inside_the_block_and_the_handler_is_restored(self):
        restore_handlers(self, signal.SIGTERM)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        with self.assertRaises(guard.StopRequested) as raised:
            with guard.sigterm_cancels():
                self.assertIs(signal.getsignal(signal.SIGTERM), guard.stop_on_signal)
                signal.raise_signal(signal.SIGTERM)
        self.assertEqual(raised.exception.number, signal.SIGTERM)
        self.assertIs(signal.getsignal(signal.SIGTERM), signal.SIG_IGN)

    def test_a_cancellation_is_a_cancelled_report_with_exit_128_plus_signal(self):
        for error, number in ((guard.StopRequested(signal.SIGTERM), 15), (KeyboardInterrupt(), 2)):
            with self.subTest(error=type(error).__name__):
                self.assertIsInstance(error, guard.CANCELLATION)
                report = guard.cancelled({"status": "running", "exit_code": 0}, error)
                self.assertEqual(report, {"status": "cancelled", "reason": "signal", "signal": number,
                                          "exit_code": 128 + number})

    def test_worker_signals_set_its_stop_event(self):
        restore_handlers(self, signal.SIGINT, signal.SIGTERM)
        for number in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=number):
                given = threading.Event()
                stop = guard.stop_on_signals(given)
                self.assertIs(stop, given)
                signal.raise_signal(number)
                self.assertTrue(stop.wait(2))  # Set by the stop relay's thread, not by the handler itself.
        self.assertFalse(guard.stop_on_signals().is_set())

    def test_the_mirror_worker_runs_on_the_primitive_with_the_bounded_workers_budgets(self):
        # The retired spawned-worker supervisor joined 0.5 s, then terminated and killed with 1 s each.
        self.assertEqual((mirror.WORKER_GRACE.cooperative, mirror.WORKER_GRACE.terminate, mirror.WORKER_GRACE.kill),
                         (0.5, 1.0, 1.0))
        self.assertIsInstance(mirror.WORKER_GRACE, supervision.Grace)
        self.assertIn("run_worker", mirror.WORKER_BOOTSTRAP)


class PublicationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def reserved(self, **data):
        reservations, report = {}, {}
        for kind, payload in data.items():
            reservations[kind] = output.reserve(self.root / f"{kind}.png", f".fixture-{kind}-", kind=kind)
            output.write(reservations[kind], payload)
            report.update({f"{kind}_ready": True, f"{kind}_sha256": hashlib.sha256(payload).hexdigest()})
        self.addCleanup(lambda: [output.discard(item) for item in reservations.values()])
        return reservations, report

    def names(self):
        return sorted(path.name for path in self.root.iterdir() if not path.name.endswith(".part"))

    def test_every_output_is_published(self):
        reservations, report = self.reserved(preview=b"boxes", still=b"frame")
        output.publish_all(reservations, report)
        self.assertEqual((report["preview_path"], report["still_path"], report["images_saved"]),
                         (str(self.root / "preview.png"), str(self.root / "still.png"), True))
        self.assertEqual((self.root / "still.png").read_bytes(), b"frame")

    def test_a_missing_or_changed_output_publishes_nothing(self):
        reservations, report = self.reserved(preview=b"boxes", still=b"frame")
        with self.assertRaises(OutputError) as raised:
            output.publish_all(reservations, dict(report, still_ready=False))
        self.assertEqual(raised.exception.reason, "requested_output_missing")
        Path(reservations["still"]["path"]).write_bytes(b"changed")
        with self.assertRaises(OutputError) as raised:
            output.publish_all(reservations, report)
        self.assertEqual(raised.exception.reason, "still_changed")
        self.assertEqual(self.names(), [])
        self.assertNotIn("images_saved", report)

    def test_a_required_digest_that_is_not_reported_publishes_nothing(self):
        reservations, report = self.reserved(preview=b"boxes", still=b"frame")
        del report["still_sha256"]
        Path(reservations["still"]["path"]).write_bytes(b"changed after the worker reported")
        with self.assertRaises(OutputError) as raised:
            output.publish_all(reservations, report, digests=("still",))
        self.assertEqual(raised.exception.reason, "still_changed")
        self.assertEqual(self.names(), [])
        self.assertNotIn("images_saved", report)
        output.publish_all({"preview": reservations["preview"]}, report, digests=("still",))  # Only reserved kinds.
        self.assertEqual(self.names(), ["preview.png"])

    def test_a_failed_later_link_withdraws_the_earlier_output(self):
        reservations, report = self.reserved(preview=b"boxes", still=b"frame")
        (self.root / "still.png").write_bytes(b"concurrent")
        with self.assertRaises(OutputError) as raised:
            output.publish_all(reservations, report)
        self.assertEqual(raised.exception.reason, "still_publish_failed")
        self.assertEqual((report["images_saved"], "preview_path" in report), (False, False))
        self.assertEqual(self.names(), ["still.png"])
        self.assertEqual((self.root / "still.png").read_bytes(), b"concurrent")

    def test_reservation_names_its_output_kind(self):
        (self.root / "taken.png").write_bytes(b"keep")
        with self.assertRaises(OutputError) as raised:
            output.reserve(self.root / "taken.png", ".fixture-", kind="still")
        self.assertEqual((raised.exception.reason, str(raised.exception)),
                         ("invalid_still_path", "--still needs a new .png path in an existing directory"))

    def test_one_step_publication_never_overwrites_and_leaves_no_temporary_file(self):
        target = self.root / "luma.png"
        self.assertEqual(output.publish_new(target, b"png", ".fixture-shm-"), target)
        self.assertEqual(target.read_bytes(), b"png")
        with self.assertRaises(FileExistsError):
            output.publish_new(target, b"other", ".fixture-shm-")
        self.assertEqual(target.read_bytes(), b"png")
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), ["luma.png"])


class LaunchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        restore_handlers(self, signal.SIGTERM)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)

    def launch(self, run, discovery=DISCOVERY, **options):
        options.setdefault("outputs", {"preview": (self.root / "p.png", ".fixture-")})
        with patch.object(mirror, "supervise_worker", side_effect=run) as bounded:
            report = mirror.launch({"status": "discovery_only", "exit_code": 0}, discovery, None, 2,
                                   worker=WORKER, report_factory=dict, success_status="captured", **options)
        self.assertIs(signal.getsignal(signal.SIGTERM), signal.SIG_DFL)  # Restored after every outcome.
        if report["exit_code"]:
            self.assertEqual(list(self.root.iterdir()), [])  # Nothing published, every reservation discarded.
        return report, bounded

    def test_worker_job_report_and_fields(self):
        seen = {}

        def run(worker, job, seconds, outputs, *, report_factory, **options):
            seen.update(worker=worker, job=job, seconds=seconds, report=report_factory(), options=options)
            output.write(outputs["preview"], b"png")
            return dict(report_factory(), status="captured", preview_ready=True, exit_code=0)

        report, _ = self.launch(run, job=lambda candidate: {"candidate": candidate}, fields={"requested": "x"},
                                timeout=7)
        self.assertEqual((report["status"], report["exit_code"], report["images_saved"]), ("captured", 0, True))
        self.assertEqual((report["candidate"], report["discovery"], report["requested"]), (CANDIDATE, DISCOVERY, "x"))
        self.assertEqual((seen["job"], seen["seconds"], seen["report"]), ({"candidate": CANDIDATE}, 2,
                                                                          {"candidate": CANDIDATE}))
        self.assertEqual((seen["options"]["timeout"], seen["options"]["success_status"], seen["worker"]),
                         (7, "captured", WORKER))
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), ["p.png"])

    def test_no_candidate_stops_before_reserving_or_running(self):
        report, bounded = self.launch(None, {"candidates": []})
        bounded.assert_not_called()
        self.assertEqual((report["status"], report["reason"], report["exit_code"]),
                         ("unavailable", "mirror_unavailable", 1))

    def test_a_clean_run_without_its_output_publishes_nothing(self):
        report, _ = self.launch(lambda *_args, report_factory, **_options:
                                dict(report_factory(), status="captured", exit_code=0))
        self.assertEqual((report["status"], report["reason"], report["exit_code"], report.get("images_saved")),
                         ("error", "requested_output_missing", 1, None))

    def test_a_failed_run_publishes_nothing_its_worker_wrote(self):
        """Only exit code 0 publishes: an output written before a failure, timeout or failed cleanup is withheld."""
        for failure in (dict(status="error", reason="worker_exit_failure", cleanup_ok=False, exit_code=1),
                        dict(status="timed_out", cleanup_ok=False, exit_code=1),
                        dict(status="cleanup_failed", cleanup_ok=False, exit_code=1),
                        dict(status="cancelled", reason="signal", signal=15, exit_code=143)):
            def run(_worker, _job, _seconds, outputs, *, report_factory, **_options):
                output.write(outputs["preview"], b"png")
                return dict(report_factory(), preview_ready=True, **failure)

            with self.subTest(status=failure["status"]):
                report, _ = self.launch(run)
                self.assertEqual((report["status"], report["exit_code"], report.get("images_saved")),
                                 (failure["status"], failure["exit_code"], None))
                self.assertNotIn("preview_path", report)

    def test_a_required_digest_reaches_publication(self):
        def run(_worker, _job, _seconds, outputs, *, report_factory, **_options):
            output.write(outputs["still"], b"png")
            return dict(report_factory(), status="captured", still_ready=True, exit_code=0)

        report, _ = self.launch(run, outputs={"still": (self.root / "s.png", ".fixture-")}, digests=("still",))
        self.assertEqual((report["status"], report["reason"], report["exit_code"]), ("error", "still_changed", 1))
        self.assertNotIn("still_path", report)

    def test_a_cancellation_during_publication_publishes_nothing(self):
        def run(_worker, _job, _seconds, outputs, *, report_factory, **_options):
            output.write(outputs["preview"], b"png")
            return dict(report_factory(), status="captured", preview_ready=True, exit_code=0)

        def terminated(*_args, **_options):
            signal.raise_signal(signal.SIGTERM)

        for link, number in ((KeyboardInterrupt, 2), (terminated, 15)):
            with self.subTest(signal=number), patch.object(os, "link", side_effect=link):
                report, _ = self.launch(run)
            self.assertEqual((report["status"], report["reason"], report["signal"], report["exit_code"]),
                             ("cancelled", "signal", number, 128 + number))
            self.assertNotIn("preview_path", report)

    def test_a_cancellation_between_two_links_withdraws_what_was_linked(self):
        """All or none with two outputs: a signal before or after either link, recorded or not, publishes nothing."""
        def run(_worker, _job, _seconds, outputs, *, report_factory, **_options):
            for reservation in outputs.values():
                output.write(reservation, b"png")
            return dict(report_factory(), status="captured", preview_ready=True, still_ready=True, exit_code=0)

        def interrupted():
            raise KeyboardInterrupt

        def terminated():
            signal.raise_signal(signal.SIGTERM)

        real_link = os.link
        for stop, number in ((interrupted, 2), (terminated, 15)):
            for call, after_link in ((1, False), (1, True), (2, False), (2, True)):
                directory = tempfile.TemporaryDirectory()  # Each case starts empty.
                self.addCleanup(directory.cleanup)
                self.root, links = Path(directory.name), []
                outputs = {"preview": (self.root / "p.png", ".fixture-"), "still": (self.root / "s.png", ".fixture-")}

                def link(source, target, **options):
                    links.append(target)
                    if len(links) == call and not after_link:
                        stop()
                    real_link(source, target, **options)
                    if len(links) == call and after_link:
                        stop()

                with self.subTest(signal=number, link=call, after_link=after_link), \
                        patch.object(os, "link", side_effect=link):
                    report, _ = self.launch(run, outputs=outputs)  # Asserts that the directory is empty.
                    self.assertEqual((report["status"], report["signal"], report["exit_code"]),
                                     ("cancelled", number, 128 + number))
                    self.assertFalse(any(f"{kind}_path" in report for kind in outputs))
                    self.assertFalse(report.get("images_saved"))

    def test_interrupt_and_sigterm_cancel_and_discard_the_reservation(self):
        def interrupted(*_args, **_options):
            raise KeyboardInterrupt

        def terminated(*_args, **_options):
            signal.raise_signal(signal.SIGTERM)

        for run, number in ((interrupted, 2), (terminated, 15)):
            with self.subTest(run=run.__name__):
                report, _ = self.launch(run)
                self.assertEqual((report["status"], report["reason"], report["signal"], report["exit_code"]),
                                 ("cancelled", "signal", number, 128 + number))


if __name__ == "__main__":
    unittest.main()
