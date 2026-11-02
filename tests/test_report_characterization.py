"""Characterization of the live report runners, the mirror launchers and preview publication.

Written against 136f3ed before package F's refactor and kept unchanged by it:
the report keys and value types, and the status and exit-code mapping for
success, error, cancellation, a refused gate, invalid options and cleanup
failure, of ``runtime.live.run_pipeline`` (synthetic and synthetic HUD),
``scripts/mirror_recognize.py`` and ``scripts/mirror_check.py``. A report may
gain keys; a removed or renamed key, or a changed status or exit code, fails
here. Generated frames and fixture mirrors only: no device is opened, and these
are cloud results, never Frame evidence.
"""
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from framejacker import detection
from framejacker.frame import Detection, DetectionResult
from framejacker.runtime import process
from framejacker.sources import mirror as acquisition
from framejacker.sources import mirror_device as device
from scripts import mirror_check, mirror_recognize

ROOT = Path(__file__).resolve().parents[1]
NUMPY_CV2 = bool(importlib.util.find_spec("numpy") and importlib.util.find_spec("cv2"))
CANDIDATE = {"node": "/dev/fixture", "name": "SteamVR"}
DISCOVERY = {"status": "candidates_found", "candidates": [CANDIDATE]}
NUMBER = (int, float)
FIXTURE_MODEL = "fixture-model"
FOOTPRINT_BOX = (67, 42, 88, 56)  # synthetic_hud_calibration's footprint, in detector input pixels.

# Synthetic run (runtime.live.run_pipeline) after the engine ran.
SYNTHETIC_KEYS = {
    "report_kind": str, "status": str, "exit_code": int, "source_kind": str, "live_input_verified": bool,
    "feedback_exclusion_verified": bool, "hardware_trial_ready": bool, "frames_processed": int,
    "frames_submitted": int, "frames_received": int, "stale_inputs_dropped": int, "stale_results_dropped": int,
    "pending_inputs_replaced": int, "maximum_pending_frames": int, "maximum_processing_fps": int,
    "seconds_requested": NUMBER, "images_saved": bool, "source_cleanup_ok": bool, "sink_cleanup_ok": bool,
    "sink_clear_ok": bool, "sink_close_ok": bool, "cleanup_ok": bool, "maximum_observation_age_ms": NUMBER,
    "labels_seen": list, "total_processing_ms": NUMBER, "total_inference_ms": NUMBER,
    "physical_recovery_confirmed": bool, "cleanup_scope": str, "pipeline": dict, "elapsed_seconds": NUMBER,
}
SYNTHETIC_LAST_RESULT = {
    "source_id": str, "sequence": int, "observed_ns": int, "media_time": type(None), "inference_ms": NUMBER,
    "observation_age_ms": NUMBER, "processing_ms": NUMBER, "expires_ns": int, "native_format": dict,
    "detector_input_size": list, "native_to_inference_scale": list, "box_coordinate_space": str,
}
SYNTHETIC_ALIASES = {
    "frames_processed": "frames_processed", "frames_submitted": "frames_submitted",
    "frames_received": "frames_received", "stale_inputs_dropped": "stale_inputs_skipped",
    "stale_results_dropped": "stale_results_skipped", "pending_inputs_replaced": "pending_replaced",
    "maximum_observation_age_ms": "max_output_age_ms", "total_processing_ms": "total_processing_ms",
    "total_inference_ms": "total_inference_ms", "labels_seen": "labels_seen",
}
HUD_KEYS = {
    "hud_mode": bool, "calibration_scope": str, "hardware_mapping_verified": bool, "mask_stage": str,
    "mask_regions_native": tuple, "output_kind": str, "world_locked": bool, "hud_config_id": str,
    "hud_pixel_size": list, "calibration_provenance": str, "calibration_expires_ns": int,
    "g5_software": dict, "validation_ledger": dict,
}
# Mirror recognition (scripts/mirror_recognize.py) once the engine was constructed.
RECOGNITION_KEYS = {
    "report_kind": str, "schema_version": int, "status": str, "cleanup_ok": bool, "images_saved": bool,
    "frames_processed": int, "labels_seen": list, "stale_inputs_skipped": int, "stale_results_skipped": int,
    "pending_frames_replaced": int, "display_started": bool, "overlay_requested": bool,
    "maximum_pending_frames": int, "maximum_input_observation_age_ms": NUMBER, "physical_camera_identity": str,
    "camera_timestamps_available": bool, "confidence_is_calibrated_probability": bool, "scope": str,
    "candidate": dict, "phase": str, "maximum_processing_fps": int, "threshold": NUMBER,
    "seconds_requested": NUMBER, "capture": dict, "pipeline": dict, "processing_elapsed_ms": NUMBER,
    "observed_processing_rate_hz": NUMBER, "mean_inference_ms": NUMBER, "maximum_inference_ms": NUMBER,
    "maximum_processing_started_age_ms": NUMBER,
}
RECOGNIZED_KEYS = dict(RECOGNITION_KEYS, detector=dict, last_result=dict, last_result_age_after_cleanup_ms=NUMBER)
RECOGNITION_LAST_RESULT = {
    "read_sequence": int, "observation_time_ns": int, "processing_started_age_ms": NUMBER, "inference_ms": NUMBER,
    "labels": list, "detections": list, "width": int, "height": int, "box_coordinate_space": str,
}
RECOGNITION_ALIASES = {
    "frames_processed": "frames_processed", "stale_inputs_skipped": "stale_inputs_skipped",
    "stale_results_skipped": "stale_results_skipped", "pending_frames_replaced": "pending_replaced",
    "labels_seen": "labels_seen", "processing_elapsed_ms": "elapsed_ms",
    "observed_processing_rate_hz": "processed_fps", "mean_inference_ms": "mean_inference_ms",
    "maximum_inference_ms": "max_inference_ms", "maximum_processing_started_age_ms": "max_input_age_ms",
}
# Fields the bounded worker supervisor adds to a launcher's report.
WORKER_KEYS = {"exit_code": int, "worker_pid": int, "worker_exit_code": int, "worker_forced_termination": bool,
               "worker_elapsed_ms": NUMBER}
PREVIEW_KEYS = {"preview_ready": bool, "preview_width": int, "preview_height": int, "preview_path": str}
STILL_KEYS = {"still_ready": bool, "still_width": int, "still_height": int, "still_sha256": str,
              "still_detector": str, "still_source": str, "still_sequence": int, "still_scope": str, "still_path": str}
CAPTURE_KEYS = {
    "report_kind": str, "schema_version": int, "status": str, "images_saved": bool, "device_open_attempted": bool,
    "acquisition_started": bool, "frames_read": int, "grayscale_sample_changes": int, "read_attempts": int,
    "would_block_reads": int, "interrupted_reads": int, "reader_method": str, "unchanged_sample_reads": int,
    "short_reads": int, "maximum_reads_per_second": NUMBER, "cleanup_ok": bool,
    "sample_change_proves_live_camera": bool, "scope": str, "physical_camera_identity": str,
    "camera_timestamps_available": bool, "candidate": dict, "seconds_requested": NUMBER,
}


class FixtureModel:
    """A registered fixture model with no files: one chair box on every frame."""
    runtime, classes = "fixture", ("chair",)

    def __init__(self, name=FIXTURE_MODEL):
        self.name = name

    def detect(self, image, threshold):
        return DetectionResult((Detection("chair", 0.9, (1, 1, 8, 8)),), 0.5)


def registered_fixture():
    return patch.dict(detection.DETECTORS, {FIXTURE_MODEL: FixtureModel})


class Mirror:
    """32 x 24 RGB3 fixture mirror; each read's bytes are its read number."""
    closed = True

    def __init__(self, candidate):
        self.capabilities = {"driver": "fixture", "card": "SteamVR"}
        self.format = device.validate_format(32, 24, "RGB3", 96, 2304)
        self.number = 0

    def open(self):
        type(self).closed = False

    def read_frame(self, timeout):
        self.number += 1
        return bytes([self.number % 256]) * self.format["buffer_bytes"]

    def close(self):
        type(self).closed = True


class EmptyMirror(Mirror):
    def read_frame(self, timeout):
        return None


class FailingMirror(Mirror):
    def read_frame(self, timeout):
        raise device.MirrorError("fixture_read_error", "controlled read failure")


class BadCloseMirror(Mirror):
    def close(self):
        super().close()
        raise RuntimeError("controlled close failure")


class StalledMirror(Mirror):
    def read_frame(self, timeout):
        return super().read_frame(timeout) if not self.number else None


class GreyMirror(Mirror):
    """2 x 2 GREY fixture mirror whose samples alternate."""

    def __init__(self, candidate):
        super().__init__(candidate)
        self.format = device.validate_format(2, 2, "GREY", 2, 4)

    def read_frame(self, timeout):
        self.number += 1
        return bytes([self.number % 2 * 200]) * 4


def recognize_worker(job, seconds, outputs, sender, stop, parent):
    """Spawned: the real recognizer worker over the fixture mirror and the registered fixture model."""
    with registered_fixture():
        mirror_recognize._worker(job, seconds, outputs, sender, stop, parent, backend_factory=Mirror)


def empty_recognize_worker(job, seconds, outputs, sender, stop, parent):
    with registered_fixture():
        mirror_recognize._worker(job, seconds, outputs, sender, stop, parent, backend_factory=EmptyMirror)


def check_worker(job, seconds, outputs, sender, stop, parent):
    """Spawned: the real mirror_check worker; its capture opens the fixture in place of the native mirror."""
    acquisition.NativeMirror = GreyMirror
    mirror_check._worker(job, seconds, outputs, sender, stop, parent)


def stuck_worker(job, seconds, outputs, sender, stop, parent):
    """Spawned: ignores SIGTERM, signals readiness and never reports a result."""
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    Path(job.get("candidate", job)["ready_file"]).write_text("ready")
    time.sleep(30)


def bounded_quickly(worker, seconds=0.3):
    """``process.run_bounded`` with ``worker`` and a short capture, whatever the launcher passes."""
    real = process.run_bounded

    def run(job, _seconds, outputs=None, **options):
        return real(job, seconds, outputs, **dict(options, worker=worker))
    return patch.object(process, "run_bounded", side_effect=run)


def run_main(main, arguments):
    with redirect_stdout(io.StringIO()) as output:
        code = main(arguments)
    return code, json.loads(output.getvalue())


class ShapeAssertions(unittest.TestCase):
    def assert_shape(self, report, shape):
        for key, kind in shape.items():
            with self.subTest(key=key):
                self.assertIn(key, report)
                if kind == NUMBER:
                    self.assertIn(type(report[key]), NUMBER)
                else:
                    self.assertIs(type(report[key]), kind)

    def assert_aliases(self, report, aliases):
        for name, source in aliases.items():
            with self.subTest(alias=name):
                self.assertEqual(report[name], report["pipeline"][source])


@unittest.skipUnless(NUMPY_CV2, "NumPy and OpenCV required for the synthetic pipeline")
class SyntheticRunCharacterizationTests(ShapeAssertions):
    """``runtime.live.run_pipeline``: one gated synthetic run as a report."""

    def run_synthetic(self, detector=None, sink=None, seconds=0.25, source=None, **options):
        from framejacker.runtime import live, synthetic
        return live.run_pipeline(source or synthetic.SyntheticSource(), detector or synthetic.FixtureDetector(),
                                 sink or synthetic.HeadlessSink(), seconds, **options)

    def test_completed_run_report(self):
        report = self.run_synthetic()
        self.assert_shape(report, SYNTHETIC_KEYS)
        self.assert_shape(report["last_result"], SYNTHETIC_LAST_RESULT)
        self.assertEqual((report["status"], report["exit_code"]), ("completed", 0), report)
        self.assertEqual((report["report_kind"], report["source_kind"], report["cleanup_scope"]),
                         ("synthetic_recognition_pipeline", "synthetic", "owned_software_resources_only"))
        for claim in ("live_input_verified", "feedback_exclusion_verified", "hardware_trial_ready",
                      "physical_recovery_confirmed", "images_saved"):
            self.assertIs(report[claim], False, claim)
        for flag in ("source_cleanup_ok", "sink_cleanup_ok", "sink_clear_ok", "sink_close_ok", "cleanup_ok"):
            self.assertIs(report[flag], True, flag)
        self.assertEqual((report["maximum_pending_frames"], report["maximum_processing_fps"]), (1, 5))
        self.assertEqual(report["seconds_requested"], 0.25)
        self.assertEqual(report["last_result"]["box_coordinate_space"], "detector_input_pixels")
        self.assertNotIn("hud_mode", report)
        self.assertNotIn("g5_software", report)
        self.assert_aliases(report, SYNTHETIC_ALIASES)

    def test_empty_failed_and_unclean_runs(self):
        from framejacker.runtime import synthetic

        class Stale(synthetic.SyntheticSource):
            def read(self, stop, timeout=0.05):
                frame = super().read(stop, timeout)
                return frame if frame is None else replace(frame, observed_ns=frame.observed_ns - 2_000_000_000)

        class Failing(synthetic.FixtureDetector):
            def detect(self, image, threshold):
                raise RuntimeError("controlled detector failure")

        class Unclean(synthetic.HeadlessSink):
            def clear(self):
                raise OSError("controlled clear failure")

        report = self.run_synthetic(source=Stale())
        self.assertEqual((report["status"], report["exit_code"], report["cleanup_ok"]), ("no_frames", 1, True))
        self.assert_shape(report, SYNTHETIC_KEYS)
        report = self.run_synthetic(Failing())
        self.assertEqual((report["status"], report["exit_code"], report["reason"]),
                         ("error", 1, "controlled detector failure"))
        self.assertTrue(report["cleanup_ok"])
        self.assert_shape(report, SYNTHETIC_KEYS)
        report = self.run_synthetic(sink=Unclean())
        self.assertEqual((report["status"], report["exit_code"]), ("cleanup_failed", 1))
        self.assertEqual((report["sink_clear_ok"], report["sink_close_ok"], report["sink_cleanup_ok"],
                          report["cleanup_ok"]), (False, True, False, False))
        self.assertEqual(report["sink_clear_error"], "controlled clear failure")
        self.assert_shape(report, SYNTHETIC_KEYS)

    def test_cancelled_before_and_during_the_run(self):
        from framejacker.runtime import synthetic

        stop = threading.Event()
        stop.set()
        report = self.run_synthetic(stop=stop)
        self.assertEqual((report["status"], report["exit_code"]), ("cancelled", 130))
        self.assertEqual((report["source_cleanup_ok"], report["sink_cleanup_ok"], report["cleanup_ok"]),
                         (True, True, True))
        self.assertNotIn("pipeline", report)
        self.assertNotIn("last_result", report)
        stop = threading.Event()

        class Stopping(synthetic.HeadlessSink):
            def submit(self, processed):
                super().submit(processed)
                stop.set()

        report = self.run_synthetic(sink=Stopping(), stop=stop, seconds=2)
        self.assertEqual((report["status"], report["exit_code"], report["cleanup_ok"]), ("cancelled", 130, True))
        self.assert_shape(report, SYNTHETIC_KEYS)

    def test_refused_gate_and_invalid_limits_raise_before_the_source_opens(self):
        from framejacker.runtime import synthetic

        source = synthetic.SyntheticSource()
        source.kind = "live_camera"
        with patch.object(source, "open") as opened, self.assertRaisesRegex(ValueError, "^live_image_source_not_approved$"):
            self.run_synthetic(source=source)
        opened.assert_not_called()
        for options in ({"seconds": 0.01}, {"seconds": 61}, {"max_fps": 0}, {"max_fps": 11}, {"max_age_ms": 0},
                        {"max_age_ms": 1001}, {"threshold": -0.1}, {"threshold": 1.5}):
            source = synthetic.SyntheticSource()
            with self.subTest(options=options), patch.object(source, "open") as opened, \
                    self.assertRaisesRegex(ValueError, "^pipeline_limits_invalid$"):
                self.run_synthetic(source=source, **options)
            opened.assert_not_called()

    def test_hud_run_reports_the_stage_and_validation(self):
        from framejacker.hud import HudStage
        from framejacker.runtime import synthetic

        source = synthetic.SyntheticSource()
        report = self.run_synthetic(source=source, hud=HudStage(source, synthetic.synthetic_hud_calibration()))
        self.assertEqual((report["status"], report["exit_code"], report["cleanup_ok"]), ("completed", 0, True), report)
        self.assert_shape(report, dict(SYNTHETIC_KEYS, **HUD_KEYS))
        self.assertEqual((report["hud_mode"], report["calibration_scope"], report["hardware_mapping_verified"],
                          report["world_locked"]), (True, "synthetic_fixture_only", False, False))
        self.assertEqual(report["last_result"]["box_coordinate_space"], "approximate_image_view_direction")
        self.assertIs(type(report["last_result"]["hud_rows"]), list)
        self.assertEqual(set(report["last_result"]["hud_checks"]),
                         {"mask_overlap_rejections", "eye_crossing_rejections", "unmasked_footprint_px"})
        self.assertEqual(report["g5_software"]["status"], "checked_unqualified")
        self.assertIs(type(report["pipeline"]["hud_checks"]), dict)

    def test_hud_validation_failure_and_refused_calibration(self):
        from framejacker.hud import HudStage
        from framejacker.runtime import synthetic

        class Footprint(synthetic.FixtureDetector):
            def detect(self, image, threshold):
                return DetectionResult((Detection("hud", 0.9, FOOTPRINT_BOX),), 0.0)

        source = synthetic.SyntheticSource()
        report = self.run_synthetic(Footprint(), source=source,
                                    hud=HudStage(source, synthetic.synthetic_hud_calibration()))
        self.assertEqual((report["status"], report["exit_code"], report["reason"]),
                         ("validation_failed", 1, "hud_validation_mask_overlap"))
        self.assertTrue(report["cleanup_ok"])
        self.assertEqual(report["g5_software"]["status"], "failed")
        source, sink = synthetic.SyntheticSource(), synthetic.HeadlessSink()
        with patch.object(source, "open") as opened, patch.object(sink, "open") as shown:
            report = self.run_synthetic(source=source, sink=sink, hud=HudStage(source, None))
        opened.assert_not_called()
        shown.assert_not_called()
        self.assertEqual((report["status"], report["exit_code"]), ("error", 1))
        self.assertIn("reason", report)


@unittest.skipUnless(NUMPY_CV2, "NumPy and OpenCV required for mirror recognition")
class MirrorRecognitionCharacterizationTests(ShapeAssertions):
    """``scripts/mirror_recognize.run_recognition``: the worker's recognition report and its last result."""

    def recognize(self, backend=Mirror, seconds=0.4, stop=None, **options):
        snapshots = []
        with registered_fixture():
            report, last = mirror_recognize.run_recognition(CANDIDATE, seconds, stop or threading.Event(),
                                                            snapshots.append, detector=FIXTURE_MODEL,
                                                            backend_factory=backend, **options)
        return report, last, snapshots

    def test_recognized_report_progress_and_last_result(self):
        report, last, snapshots = self.recognize(seconds=1.3)
        self.assertEqual((report["status"], report["cleanup_ok"], report["phase"]), ("recognized", True, "closed"), report)
        self.assert_shape(report, RECOGNIZED_KEYS)
        self.assert_shape(report["last_result"], RECOGNITION_LAST_RESULT)
        self.assert_shape(report["capture"], CAPTURE_KEYS)
        self.assertEqual((report["capture"]["status"], report["capture"]["cleanup_ok"]), ("captured", True))
        self.assertEqual((report["report_kind"], report["schema_version"]), ("steamvr_mirror_recognition", 1))
        self.assertEqual(report["detector"], {"name": FIXTURE_MODEL, "runtime": "fixture", "classes": 1})
        for claim in ("display_started", "overlay_requested", "camera_timestamps_available",
                      "confidence_is_calibrated_probability", "images_saved"):
            self.assertIs(report[claim], False, claim)
        self.assertEqual((report["maximum_pending_frames"], report["maximum_input_observation_age_ms"],
                          report["physical_camera_identity"]), (1, 1000, "unverified"))
        self.assertEqual((report["candidate"], report["maximum_processing_fps"], report["threshold"],
                          report["seconds_requested"]), (CANDIDATE, 5, 0.5, 1.3))
        self.assertEqual(report["last_result"]["labels"], ["chair"])
        self.assertEqual(report["last_result"]["box_coordinate_space"], "resized_mirror_pixels")
        self.assertEqual((report["last_result"]["width"], report["last_result"]["height"]), (32, 24))
        self.assertEqual(report["labels_seen"], ["chair"])
        self.assert_aliases(report, RECOGNITION_ALIASES)
        self.assertEqual(last.frame.sequence, report["last_result"]["read_sequence"])
        self.assertEqual(last.image.shape, (24, 32, 3))
        self.assertEqual((snapshots[0]["status"], snapshots[0]["phase"]), ("not_started", "loading_detector"))
        progress = snapshots[1:]
        self.assertTrue(progress, snapshots)
        for snapshot in progress:
            self.assertEqual(snapshot["phase"], "recognizing")
            self.assertTrue({"pipeline", "capture", "last_result"} <= set(snapshot), sorted(snapshot))

    def test_empty_failed_unclean_and_stale_sources(self):
        cases = ((EmptyMirror, 0.3, "no_frames", None, True), (FailingMirror, 0.3, "error", "fixture_read_error", True),
                 (BadCloseMirror, 0.3, "error", "close_failed", False),
                 (StalledMirror, 1.2, "stale_input", "last_observed_input_too_old", True))
        for backend, seconds, status, reason, clean in cases:
            with self.subTest(backend=backend.__name__):
                report, last, _ = self.recognize(backend, seconds)
                self.assertEqual((report["status"], report.get("reason"), report["cleanup_ok"], report["phase"]),
                                 (status, reason, clean, "closed"), report)
                self.assertIsNone(last)
                self.assertTrue(backend.closed)
                self.assert_shape(report, dict(RECOGNITION_KEYS, detector=dict))
        report, _, _ = self.recognize(FailingMirror, 0.3)
        self.assertEqual(report["error"], "controlled read failure")
        self.assertEqual(report["capture"]["status"], "error")

    def test_model_failure_and_cancellation_never_open_the_source(self):
        backend = Mock(side_effect=AssertionError("source must not open"))
        model = Mock(side_effect=RuntimeError("model missing"))
        report, last, _ = self.recognize(backend, 1, detector_factory=model)
        self.assertEqual((report["status"], report["reason"], report["error"], report["phase"]),
                         ("error", "recognition_failed", "model missing", "closed"))
        self.assertEqual(report["capture"], {})
        self.assertNotIn("pipeline", report)
        self.assertIsNone(last)
        backend.assert_not_called()
        stop = threading.Event()
        stop.set()
        report, last, _ = self.recognize(backend, 1, stop=stop)
        self.assertEqual((report["status"], report["phase"], report["capture"]), ("cancelled", "closed", {}))
        self.assertEqual(report["detector"], {"name": FIXTURE_MODEL, "runtime": "fixture", "classes": 1})
        self.assertNotIn("pipeline", report)
        self.assertIsNone(last)
        backend.assert_not_called()

    def test_hud_mode_gate_refuses_a_non_synthetic_backend_before_the_model(self):
        model = Mock()
        report, last, _ = self.recognize(Mirror, 0.3, hud_mode=True, detector_factory=model)
        self.assertEqual((report["status"], report["reason"], report["phase"], report["capture"]),
                         ("error", "recognition_failed", "closed", {}))
        self.assertIn("hud_live_mirror_not_approved", report["error"])
        self.assertIsNone(last)
        model.assert_not_called()


class MirrorRecognizeCommandCharacterizationTests(ShapeAssertions):
    """``scripts/mirror_recognize.py`` main: discovery, launch, publication and the exit code."""

    def test_discovery_only_and_invalid_options(self):
        with patch.object(mirror_recognize, "discover", return_value=DISCOVERY), \
                patch.object(process, "run_bounded", side_effect=AssertionError("worker must not start")):
            code, report = run_main(mirror_recognize.main, [])
        self.assertEqual((code, report["status"], report["exit_code"], report["discovery"]), (0, "discovery_only", 0, DISCOVERY))
        self.assertEqual((report["report_kind"], report["images_saved"], report["display_started"]),
                         ("steamvr_mirror_recognition", False, False))
        for arguments in (["--preview", "x.png"], ["--still", "x.png"], ["--device", "/dev/video0"], ["--max-fps", "6"],
                          ["--seconds", "0"], ["--seconds", "11"], ["--threshold", "nan"], ["--threshold", "2"],
                          ["--detector", "unknown"], ["--capture", "--preview", "x.png", "--still", "x.png"]):
            with self.subTest(arguments=arguments), redirect_stderr(io.StringIO()), \
                    patch.object(mirror_recognize, "discover", side_effect=AssertionError("parsed first")):
                with self.assertRaises(SystemExit) as error:
                    mirror_recognize.main(arguments)
                self.assertEqual(error.exception.code, 2)

    def test_unavailable_mirror_and_existing_outputs_stop_before_the_worker(self):
        with patch.object(mirror_recognize, "discover", return_value={"status": "unavailable", "candidates": []}), \
                patch.object(process, "run_bounded", side_effect=AssertionError("worker must not start")):
            code, report = run_main(mirror_recognize.main, ["--capture"])
        self.assertEqual((code, report["status"], report["reason"], report["exit_code"]),
                         (1, "unavailable", "mirror_unavailable", 1))
        with tempfile.TemporaryDirectory() as directory:
            taken = Path(directory) / "taken.png"
            taken.write_bytes(b"keep")
            for kind in ("preview", "still"):
                with self.subTest(kind=kind), patch.object(mirror_recognize, "discover", return_value=DISCOVERY), \
                        patch.object(process, "run_bounded", side_effect=AssertionError("worker must not start")):
                    code, report = run_main(mirror_recognize.main, ["--capture", f"--{kind}", str(taken)])
                self.assertEqual((code, report["status"], report["reason"], report["exit_code"]),
                                 (1, "error", f"invalid_{kind}_path", 1))
            self.assertEqual(taken.read_bytes(), b"keep")
            self.assertEqual(sorted(path.name for path in Path(directory).iterdir()), ["taken.png"])

    @unittest.skipUnless(NUMPY_CV2, "NumPy and OpenCV required for mirror recognition")
    def test_spawned_recognition_publishes_preview_and_still(self):
        with tempfile.TemporaryDirectory() as directory:
            preview, still = Path(directory) / "boxes.png", Path(directory) / "input.png"
            with registered_fixture(), patch.object(mirror_recognize, "discover", return_value=DISCOVERY), \
                    patch.object(mirror_recognize, "_worker", recognize_worker):
                code, report = run_main(mirror_recognize.main, ["--capture", "--seconds", "1", "--detector", FIXTURE_MODEL,
                                                                "--preview", str(preview), "--still", str(still)])
            self.assertEqual((code, report["status"], report["exit_code"], report["cleanup_ok"], report["images_saved"]),
                             (0, "recognized", 0, True, True), report)
            self.assert_shape(report, dict(RECOGNIZED_KEYS, **WORKER_KEYS, **PREVIEW_KEYS, **STILL_KEYS,
                                           discovery=dict, detector_requested=str))
            self.assertEqual((report["preview_path"], report["still_path"]), (str(preview), str(still)))
            self.assertEqual((report["detector_requested"], report["still_detector"]), (FIXTURE_MODEL, FIXTURE_MODEL))
            self.assertEqual((report["still_width"], report["still_height"], report["still_source"]),
                             (32, 24, "mirror:observed"))
            self.assertEqual(report["still_sha256"], hashlib.sha256(still.read_bytes()).hexdigest())
            self.assertEqual(report["still_sequence"], report["last_result"]["read_sequence"])
            self.assertEqual((report["worker_exit_code"], report["worker_forced_termination"]), (0, False))
            self.assertTrue(preview.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertEqual(sorted(path.name for path in Path(directory).iterdir()), ["boxes.png", "input.png"])

    @unittest.skipUnless(NUMPY_CV2, "NumPy and OpenCV required for mirror recognition")
    def test_spawned_empty_run_publishes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            preview = Path(directory) / "boxes.png"
            with registered_fixture(), patch.object(mirror_recognize, "discover", return_value=DISCOVERY), \
                    patch.object(mirror_recognize, "_worker", empty_recognize_worker):
                code, report = run_main(mirror_recognize.main, ["--capture", "--seconds", "1", "--detector", FIXTURE_MODEL,
                                                                "--preview", str(preview)])
            self.assertEqual((code, report["status"], report["exit_code"], report["images_saved"]),
                             (1, "no_frames", 1, False), report)
            self.assert_shape(report, dict(RECOGNITION_KEYS, **WORKER_KEYS, discovery=dict, detector_requested=str))
            self.assertNotIn("preview_path", report)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_reservation_cleanup_failure_fails_the_run(self):
        def failed(*_args, report_factory, **_options):
            return dict(report_factory(), status="no_frames", cleanup_ok=True, exit_code=1)

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(mirror_recognize, "discover", return_value=DISCOVERY), \
                    patch.object(process, "run_bounded", side_effect=failed), \
                    patch.object(Path, "unlink", side_effect=OSError("controlled cleanup failure")):
                code, report = run_main(mirror_recognize.main, ["--capture", "--preview", str(Path(directory) / "p.png")])
        self.assertEqual((code, report["status"], report["reason"], report["error"], report["exit_code"]),
                         (1, "error", "preview_cleanup_failed", "controlled cleanup failure", 1))


class MirrorCheckCommandCharacterizationTests(ShapeAssertions):
    """``scripts/mirror_check.py`` main: discovery, launch and grayscale preview publication."""

    def test_discovery_only_and_invalid_options(self):
        with patch.object(mirror_check, "discover", return_value=DISCOVERY), \
                patch.object(process, "run_bounded", side_effect=AssertionError("worker must not start")):
            code, report = run_main(mirror_check.main, [])
        self.assertEqual((code, report["status"], report["exit_code"], report["discovery"]), (0, "discovery_only", 0, DISCOVERY))
        self.assertEqual((report["report_kind"], report["images_saved"], report["device_open_attempted"]),
                         ("steamvr_mirror_check", False, False))
        for arguments in (["--preview", "x.png"], ["--device", "/dev/video0"], ["--seconds", "0"], ["--seconds", "11"]):
            with self.subTest(arguments=arguments), redirect_stderr(io.StringIO()), \
                    patch.object(mirror_check, "discover", side_effect=AssertionError("parsed first")):
                with self.assertRaises(SystemExit) as error:
                    mirror_check.main(arguments)
                self.assertEqual(error.exception.code, 2)

    def test_unavailable_mirror_and_existing_preview_stop_before_the_worker(self):
        with patch.object(mirror_check, "discover", return_value={"status": "unavailable", "candidates": []}), \
                patch.object(process, "run_bounded", side_effect=AssertionError("worker must not start")):
            code, report = run_main(mirror_check.main, ["--capture"])
        self.assertEqual((code, report["status"], report["reason"], report["exit_code"]),
                         (1, "unavailable", "mirror_unavailable", 1))
        with tempfile.TemporaryDirectory() as directory:
            taken = Path(directory) / "taken.png"
            taken.write_bytes(b"keep")
            with patch.object(mirror_check, "discover", return_value=DISCOVERY), \
                    patch.object(process, "run_bounded", side_effect=AssertionError("worker must not start")):
                code, report = run_main(mirror_check.main, ["--capture", "--preview", str(taken)])
            self.assertEqual((code, report["status"], report["reason"], report["exit_code"]),
                             (1, "error", "invalid_preview_path", 1))
            self.assertEqual(taken.read_bytes(), b"keep")
            self.assertEqual(sorted(path.name for path in Path(directory).iterdir()), ["taken.png"])

    def test_captured_run_publishes_the_grayscale_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "mirror.png"
            with patch.object(mirror_check, "discover", return_value=DISCOVERY), bounded_quickly(check_worker):
                code, report = run_main(mirror_check.main, ["--capture", "--preview", str(target)])
            self.assertEqual((code, report["status"], report["exit_code"], report["cleanup_ok"], report["images_saved"]),
                             (0, "captured", 0, True, True), report)
            self.assert_shape(report, dict(CAPTURE_KEYS, **WORKER_KEYS, **PREVIEW_KEYS, discovery=dict,
                                           preview_pixel_format=str))
            self.assertEqual((report["preview_path"], report["preview_width"], report["preview_height"],
                              report["preview_pixel_format"]), (str(target), 2, 2, "gray8"))
            self.assertEqual(report["candidate"], CANDIDATE)
            png = target.read_bytes()
            self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertEqual(struct.unpack_from(">II", png, 16), (2, 2))
            self.assertEqual(sorted(path.name for path in Path(directory).iterdir()), ["mirror.png"])

    def test_destination_created_during_the_run_is_kept(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "mirror.png"
            real = process.run_bounded

            def raced(job, seconds, outputs=None, **options):
                report = real(job, 0.3, outputs, **dict(options, worker=check_worker))
                target.write_bytes(b"keep me")
                return report

            with patch.object(mirror_check, "discover", return_value=DISCOVERY), \
                    patch.object(process, "run_bounded", side_effect=raced):
                code, report = run_main(mirror_check.main, ["--capture", "--preview", str(target)])
            self.assertEqual((code, report["status"], report["exit_code"], report["images_saved"]),
                             (1, "error", 1, False), report)
            self.assertNotIn("preview_path", report)
            self.assertEqual(target.read_bytes(), b"keep me")
            self.assertEqual(sorted(path.name for path in Path(directory).iterdir()), ["mirror.png"])

    def test_reservation_cleanup_failure_fails_the_run(self):
        def failed(*_args, report_factory, **_options):
            return dict(report_factory(), status="no_frames", cleanup_ok=True, exit_code=1)

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(mirror_check, "discover", return_value=DISCOVERY), \
                    patch.object(process, "run_bounded", side_effect=failed), \
                    patch.object(Path, "unlink", side_effect=OSError("controlled cleanup failure")):
                code, report = run_main(mirror_check.main, ["--capture", "--preview", str(Path(directory) / "p.png")])
        self.assertEqual((code, report["status"], report["reason"], report["error"], report["exit_code"]),
                         (1, "error", "preview_cleanup_failed", "controlled cleanup failure", 1))


@unittest.skipUnless(os.name == "posix", "POSIX signals required")
class LauncherCancellationCharacterizationTests(unittest.TestCase):
    """SIGTERM to a mirror launcher: a cancelled report, exit 143 and a reaped worker."""

    PROGRAM = '''import sys
from unittest.mock import patch
sys.path[:0] = [sys.argv[1]]
from framejacker.runtime import process
from scripts import {script} as command
from tests.test_report_characterization import stuck_worker
real = process.run_bounded
def run(job, seconds, outputs=None, **options):
    return real(job, seconds, outputs, **dict(options, worker=stuck_worker))
candidate = {{"node": "/dev/fixture", "name": "SteamVR", "ready_file": sys.argv[2]}}
with patch.object(command, "discover", return_value={{"candidates": [candidate]}}), \\
        patch.object(process, "run_bounded", run):
    sys.exit(command.main(["--capture", "--seconds", "10"]))
'''

    def test_sigterm_cancels_each_launcher(self):
        for script in ("mirror_check", "mirror_recognize"):
            with self.subTest(script=script), tempfile.TemporaryDirectory() as folder:
                ready = Path(folder) / "ready"
                with subprocess.Popen([sys.executable, "-c", self.PROGRAM.format(script=script), str(ROOT), str(ready)],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as command:
                    try:
                        deadline = time.monotonic() + 15
                        while not ready.exists() and command.poll() is None and time.monotonic() < deadline:
                            time.sleep(0.01)
                        started = ready.exists()
                        command.terminate()
                        output, error = command.communicate(timeout=10)
                    finally:
                        if command.poll() is None:
                            command.kill()
                self.assertTrue(started, error)
                self.assertEqual(command.returncode, 143, error)
                report = json.loads(output)
                self.assertEqual((report["status"], report["reason"], report["signal"], report["exit_code"]),
                                 ("cancelled", "signal", 15, 143))
                self.assertFalse(report["cleanup_ok"])
                with self.assertRaises(ProcessLookupError):
                    os.kill(report["worker_pid"], 0)


if __name__ == "__main__":
    unittest.main()
