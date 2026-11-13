"""The camera helper's fake build: every outcome, the refusals, the lock, cancellation and the watchdog.

The fake backend is built through ``scripts/build_camera_helper.sh --fake --out``
into a temporary directory, so CI exercises the script's own flag and never the
checkout's ``artifacts/``. No test touches a camera, the SDK headers or the
Client Library; every record the fake produces says ``backend: "fake"``.
The confirmation prompt is answered through a pseudo-terminal, as an operator
would, and each test gets its own ``XDG_RUNTIME_DIR`` for the lock.
"""
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pty
import shutil
import signal
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/build_camera_helper.sh"
EVENTS = {"describe", "hello", "controls", "frame_set", "outcome", "final"}
OUTCOMES = ("ok", "no_frame", "not_ready", "unavailable", "permission_denied", "protocol_mismatch", "camera_busy",
            "transport_lost", "provider_unreachable", "invalid_metadata", "helper_error", "cancelled")
FALSE_CLAIMS = ("pixels_read", "images_saved", "controls_written", "exclusive_control_requested", "led_written")
CAMERA_KEYS = {"frame_id", "exposure_timestamp_ns", "timestamp_seconds", "width", "height", "drm_format",
               "pixel_format", "drm_modifier", "plane_count", "planes", "texture_bounds_valid", "intrinsics_valid",
               "camera_model", "kb4_valid", "first_camera_from_camera_valid", "aux_json_size", "transfer",
               "primaries", "ycbcr_encoding"}
# The fake's offline calibration payload; only its digest may appear in a record.
FAKE_OFFLINE_CAL = b'{"fake": "calibration"}'
PROBE_CALLS = ["connect", "control_state", "set_reader:true", "acquire", "set_reader:false", "control_state",
               "disconnect"]


def sha256(data):
    return hashlib.sha256(data).hexdigest()


@unittest.skipUnless(shutil.which("g++"), "C++ compiler required to build the fake camera helper")
class CameraHelperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory()
        result = subprocess.run(["bash", str(SCRIPT), "--fake", "--out", cls.build.name], cwd=ROOT,
                                capture_output=True, text=True, timeout=600)
        if result.returncode:
            cls.build.cleanup()
            raise AssertionError(result.stdout + result.stderr)
        cls.binary = Path(cls.build.name) / "sightcircuit-camera-helper-fake"
        assert cls.binary.is_file(), result.stdout

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def setUp(self):
        self.runtime = tempfile.TemporaryDirectory()
        self.addCleanup(self.runtime.cleanup)
        self.env = {key: value for key, value in os.environ.items() if key != "XDG_RUNTIME_DIR"}
        self.env["XDG_RUNTIME_DIR"] = self.runtime.name

    # -- running the helper ------------------------------------------------------

    def start(self, args, scenario=None, tty=True, env=None):
        """Start the helper; with ``tty`` its stdin is a pseudo-terminal slave and no controlling terminal."""
        command = [str(self.binary), *map(str, args)]
        if scenario:
            command += ["--fake-scenario", scenario]
        if tty:
            master, slave = pty.openpty()
            process = subprocess.Popen(command, stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       env=env or self.env, start_new_session=True)
            os.close(slave)
            process.pty_master = master
        else:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, env=env or self.env, start_new_session=True)
            process.pty_master = None
        self.addCleanup(self.reap, process)
        return process

    @staticmethod
    def reap(process):
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
        if process.pty_master is not None:
            try:
                os.close(process.pty_master)
            except OSError:
                pass
            process.pty_master = None

    @staticmethod
    def answer(process, text="yes\n"):
        os.write(process.pty_master, text.encode())

    def read_until(self, process, event, timeout=10):
        """Lines from stdout up to and including the first record with ``event``."""
        lines = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = process.stdout.readline()
            if not line:
                break
            lines.append(line)
            if json.loads(line)["event"] == event:
                return lines
        self.fail(f"no {event} record within {timeout}s: {lines}")

    def finish(self, process, timeout=20, prefix=()):
        try:
            out, err = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            self.fail("the helper did not exit")
        text = b"".join(prefix) + out
        records = [json.loads(line) for line in text.decode().splitlines() if line.strip()]
        self.assertTrue(records, err)
        for record in records:
            self.check_record(record)
        self.assertEqual(records[-1]["event"], "final", records)
        self.assertEqual(records[-1]["exit_code"], process.returncode, records[-1])
        return process.returncode, records, err.decode()

    def run_helper(self, args, scenario=None, answer="yes\n", tty=True, env=None, timeout=20):
        process = self.start(args, scenario, tty, env)
        if tty and answer is not None:
            self.answer(process, answer)
        return self.finish(process, timeout)

    def probe(self, scenario, frame_sets=3, max_seconds=5, interval_ms=100, **options):
        args = ["--probe-frames", frame_sets, "--max-seconds", max_seconds, "--interval-ms", interval_ms]
        for key, value in options.items():
            args += ["--" + key.replace("_", "-"), value]
        return self.run_helper(args, scenario)

    # -- schema ------------------------------------------------------------------

    def check_record(self, record):
        self.assertEqual(record["report_kind"], "sightcircuit_camera_helper")
        self.assertEqual(record["schema_version"], 1)
        self.assertEqual(record["backend"], "fake")
        self.assertEqual(record["contract"], "camera-helper/1")
        self.assertIn(record["event"], EVENTS)
        if record["event"] == "final":
            for claim in FALSE_CLAIMS:
                self.assertIs(record[claim], False, claim)
            self.assertEqual(record["camera_data_fields"], [])
            self.assertEqual(record["persistence"], "stdout_only")
            self.assertIsInstance(record["exit_code"], int)
            self.assertIsInstance(record["cleanup_ok"], bool)
            self.assertIn("reason", record)
            if record["reason"] not in ("invalid_arguments", "watchdog_expired"):
                self.assertEqual(set(record["outcome_counts"]), set(OUTCOMES))
                self.assertIsInstance(record["fake_calls"], list)
        elif record["event"] == "outcome":
            for key in ("call", "outcome", "retryable", "status_error", "result", "system_error", "sdk_message"):
                self.assertIn(key, record)
            self.assertIn(record["outcome"], OUTCOMES)
        elif record["event"] == "frame_set":
            self.assertEqual(len(record["cameras"]), record["camera_count"])
            for camera in record["cameras"]:
                self.assertEqual(set(camera), CAMERA_KEYS)
                self.assertEqual(len(camera["planes"]), camera["plane_count"])
                text = json.dumps(camera)
                for forbidden in ("exposure_seconds", "gain", "temperature", "aux_json_sha", "\"fx\"", "matrix", "pose"):
                    self.assertNotIn(forbidden, text)
            for clocks in (record["host_clocks_before"], record["host_clocks_after"]):
                self.assertEqual(set(clocks), {"monotonic_ns", "boottime_ns", "realtime_ns"})
        elif record["event"] == "controls":
            controls = record["controls"]
            self.assertEqual(controls["digest"], sha256(controls["digest_input"].encode()))
            self.assertNotIn("exposure_ms", controls)
            self.assertNotIn("\"gain\"", json.dumps(controls))

    @staticmethod
    def events(records):
        return [record["event"] for record in records]

    # -- describe and ok runs ----------------------------------------------------

    def test_describe_connects_nothing_and_needs_no_runtime_dir(self):
        env = {key: value for key, value in self.env.items() if key != "XDG_RUNTIME_DIR"}
        result = subprocess.run([str(self.binary), "--describe"], stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, env=env, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        records = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(records), 1)
        record = records[0]
        self.check_record(record)
        self.assertEqual(record["event"], "describe")
        self.assertIsNone(record["sdk_release"])
        self.assertTrue(record["compiler"])
        self.assertIn("build_commit", record)
        self.assertEqual(list(Path(self.runtime.name).iterdir()), [])
        self.assertFalse(result.stderr)

    def test_a_closed_protocol_pipe_ends_describe_by_exit_code_not_by_sigpipe(self):
        # A parent that stopped reading: every protocol write fails with EPIPE, which --describe
        # reports as exit 1; the default SIGPIPE action would end the helper without a record.
        read_end, write_end = os.pipe()
        os.close(read_end)
        try:
            result = subprocess.run([str(self.binary), "--describe"], stdin=subprocess.DEVNULL, stdout=write_end,
                                    stderr=subprocess.PIPE, env=self.env, timeout=10)
        finally:
            os.close(write_end)
        self.assertEqual(result.returncode, 1, result.stderr)

    def test_ok_scenarios_report_format_metadata_and_clean_up(self):
        for scenario, pixel_format, modifier, sensor_bits in (("ok_linear_nv12", "nv12", "0x0000000000000000", 8),
                                                              ("ok_ubwc_nv12", "nv12_ubwc", "0x0500000000000001", 8),
                                                              ("ok_p010", "p010", "0x0000000000000000", 10)):
            with self.subTest(scenario=scenario):
                code, records, err = self.probe(scenario, frame_sets=3)
                self.assertEqual(code, 0, err)
                final = records[-1]
                self.assertEqual(self.events(records), ["hello", "controls", "frame_set", "frame_set", "frame_set",
                                                        "controls", "final"])
                self.assertEqual(final["fake_calls"], PROBE_CALLS[:3] + ["acquire"] * 3 + PROBE_CALLS[4:])
                self.assertEqual(final["outcome"], "ok")
                self.assertEqual(final["reason"], "ok")
                self.assertEqual(final["frame_sets"], 3)
                self.assertEqual(final["passes"], 3)
                self.assertEqual(final["outcome_counts"]["ok"], 8)
                self.assertTrue(final["cleanup_ok"])
                self.assertIs(final["reader_activated"], True)
                self.assertIs(final["reader_deactivated"], True)
                self.assertIs(final["controls_changed_during_run"], False)
                self.assertEqual(final["controls_digest_start"], final["controls_digest_end"])
                self.assertEqual(final["sequence_gaps"], {"count": 0, "first": None})
                self.assertIs(final["format_changed"], False)
                self.assertIsNone(final["first_retryable_after_last_frame_set_monotonic_ns"])
                self.assertIsNotNone(final["last_frame_set_monotonic_ns"])
                for record in records[2:5]:
                    self.assertEqual(record["camera_count"], 2)
                    for camera in record["cameras"]:
                        self.assertEqual(camera["pixel_format"], pixel_format)
                        self.assertEqual(camera["drm_modifier"], modifier)
                        self.assertEqual((camera["width"], camera["height"]), (2464, 2464))
                    self.assertLessEqual(record["host_clocks_before"]["monotonic_ns"],
                                         record["host_clocks_after"]["monotonic_ns"])
                self.assertEqual(records[1]["controls"]["sensor_bit_depth"], sensor_bits)
                # The fake stamps exposures on CLOCK_MONOTONIC, a few ms before acquire returns.
                monotonic = final["clock_offsets"]["monotonic"]["after_minus_exposure_timestamp_ns"]
                self.assertGreaterEqual(monotonic["min"], 3_000_000)
                self.assertLess(monotonic["max"], 2_000_000_000)
                self.assertGreater(final["clock_offsets"]["realtime"]["after_minus_exposure_timestamp_ns"]["min"],
                                   10 ** 17)
                self.assertIn("avCam0", err)
                self.assertIn("no images are read", err)

    def test_hello_reads_controls_with_the_reader_off(self):
        code, records, err = self.run_helper(["--hello"], "ok_linear_nv12")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.events(records), ["hello", "controls", "final"])
        hello, controls, final = records
        self.assertEqual(hello["camera_count"], 2)
        self.assertEqual(hello["offline_cal_size"], len(FAKE_OFFLINE_CAL))
        self.assertEqual(hello["offline_cal_sha256"], sha256(FAKE_OFFLINE_CAL))
        self.assertIs(hello["offline_cal_size_within_bound"], True)
        self.assertNotIn("calibration", json.dumps(hello))
        self.assertEqual(controls["phase"], "hello")
        self.assertEqual(controls["controls"]["current_fps_rounded"], 60.0)
        self.assertIn("current_fps=bits:0x42700000;", controls["controls"]["digest_input"])
        self.assertEqual(final["fake_calls"], ["connect", "control_state", "disconnect"])
        self.assertIs(final["reader_activated"], False)
        self.assertEqual(final["controls_digest_start"], controls["controls"]["digest"])
        self.assertEqual(final["mode"], "hello")
        self.assertIn("the reader stays off", err)
        self.assertIn("30 seconds afterwards", err)

    # -- terminal outcomes -------------------------------------------------------

    def test_permission_denied_follows_a_successful_hello(self):
        code, records, _ = self.run_helper(["--hello"], "permission_denied")
        self.assertEqual(code, 2)
        self.assertEqual(self.events(records), ["hello", "outcome", "final"])
        outcome = records[1]
        self.assertEqual((outcome["call"], outcome["outcome"]), ("control_state", "permission_denied"))
        self.assertEqual((outcome["status_error"], outcome["result"]), ("Server", "Result_PermissionDenied"))
        self.assertIn("Camera Switch", outcome["message"])
        self.assertIs(outcome["retryable"], False)
        self.assertEqual(records[-1]["fake_calls"], ["connect", "control_state", "disconnect"])
        self.assertEqual(records[-1]["outcome"], "permission_denied")
        self.assertEqual(records[-1]["outcome_counts"]["permission_denied"], 1)

        code, records, _ = self.probe("permission_denied_at_reader")
        self.assertEqual(code, 2)
        self.assertEqual(records[-1]["fake_calls"], ["connect", "control_state", "set_reader:true", "disconnect"])
        self.assertIs(records[-1]["reader_activated"], False)
        self.assertEqual(records[-2]["call"], "set_reader:true")

    def test_protocol_busy_and_unreachable_outcomes(self):
        for scenario, code, outcome in (("protocol_too_new", 4, "protocol_mismatch"),
                                        ("protocol_too_old", 4, "protocol_mismatch"),
                                        ("already_connected", 3, "camera_busy")):
            with self.subTest(scenario=scenario):
                exit_code, records, _ = self.run_helper(["--hello"], scenario)
                self.assertEqual(exit_code, code)
                self.assertEqual(self.events(records), ["outcome", "final"])
                self.assertEqual(records[0]["call"], "connect")
                self.assertEqual(records[-1]["outcome"], outcome)
                self.assertEqual(records[-1]["fake_calls"], ["connect", "disconnect"])
        exit_code, records, _ = self.run_helper(["--hello"], "already_connected")
        self.assertIn("never takes over", records[0]["message"])
        exit_code, records, _ = self.probe("resource_denied")
        self.assertEqual(exit_code, 3)
        self.assertEqual(records[-1]["fake_calls"], ["connect", "control_state", "set_reader:true", "disconnect"])
        exit_code, records, _ = self.run_helper(["--hello"], "provider_unreachable")
        self.assertEqual(exit_code, 1)
        self.assertEqual(self.events(records), ["outcome", "final"])
        self.assertEqual(records[0]["status_error"], "Transport")
        self.assertEqual(records[0]["system_error"], errno.ECONNREFUSED)
        self.assertEqual(records[-1]["outcome"], "provider_unreachable")

    def test_retryable_outcomes_are_counted_and_do_not_end_the_run(self):
        code, records, _ = self.probe("server_timeout_then_ok", frame_sets=2)
        self.assertEqual(code, 0)
        self.assertEqual(self.events(records), ["hello", "controls", "outcome", "outcome", "frame_set", "frame_set",
                                                "controls", "final"])
        for record in records[2:4]:
            self.assertEqual((record["outcome"], record["retryable"], record["result"]),
                             ("no_frame", True, "Result_Timeout"))
        self.assertEqual(records[-1]["outcome_counts"]["no_frame"], 2)
        self.assertEqual(records[-1]["frame_sets"], 2)
        self.assertEqual(records[-1]["passes"], 4)

        code, records, _ = self.probe("not_ready", frame_sets=1)
        self.assertEqual(code, 0)
        self.assertEqual(records[-1]["outcome_counts"]["not_ready"], 3)
        self.assertEqual(records[-1]["frame_sets"], 1)

        # One frame set, then unavailable until the time bound: still exit 0.
        code, records, _ = self.probe("unavailable", frame_sets=50, max_seconds=1)
        self.assertEqual(code, 0)
        final = records[-1]
        self.assertEqual(final["frame_sets"], 1)
        self.assertGreaterEqual(final["outcome_counts"]["unavailable"], 3)
        self.assertEqual(final["outcome"], "ok")
        self.assertIsNotNone(final["last_frame_set_monotonic_ns"])
        self.assertGreater(final["first_retryable_after_last_frame_set_monotonic_ns"],
                           final["last_frame_set_monotonic_ns"])

    def test_standby_gap_resumes(self):
        code, records, _ = self.probe("standby_gap", frame_sets=3, max_seconds=10)
        self.assertEqual(code, 0)
        final = records[-1]
        self.assertEqual((final["frame_sets"], final["passes"], final["outcome_counts"]["no_frame"]), (3, 7, 4))
        self.assertIsNone(final["first_retryable_after_last_frame_set_monotonic_ns"])

    def test_transport_loss_allows_only_disconnect(self):
        code, records, _ = self.probe("transport_lost_mid_run", frame_sets=5)
        self.assertEqual(code, 1)
        final = records[-1]
        self.assertEqual(final["outcome"], "transport_lost")
        self.assertEqual(final["frame_sets"], 2)
        self.assertEqual(final["fake_calls"], ["connect", "control_state", "set_reader:true", "acquire", "acquire",
                                               "acquire", "disconnect"])
        self.assertEqual(final["reader_deactivated"], "session_closed")
        self.assertIs(final["cleanup_ok"], False)
        self.assertEqual(records[-2]["system_error"], errno.EPIPE)

        code, records, _ = self.probe("release_fails")
        self.assertEqual(code, 1)
        self.assertEqual(records[-1]["outcome"], "transport_lost")
        self.assertEqual(records[-1]["fake_calls"], ["connect", "control_state", "set_reader:true", "acquire",
                                                     "disconnect"])

    def test_format_change_sequence_gap_and_control_change_are_reported(self):
        code, records, _ = self.probe("format_changes", frame_sets=4)
        self.assertEqual(code, 0)
        final = records[-1]
        self.assertIs(final["format_changed"], True)
        self.assertEqual(final["first_format_change"],
                         {"pass": 3, "camera": 0, "field": "pixel_format", "from": "nv12", "to": "nv12_ubwc"})

        code, records, _ = self.probe("sequence_gap", frame_sets=4)
        self.assertEqual(code, 0)
        gaps = records[-1]["sequence_gaps"]
        self.assertEqual(gaps["count"], 1)
        self.assertEqual(gaps["first"]["pass"], 3)
        self.assertEqual(gaps["first"]["to"] - gaps["first"]["from"], 3)

        code, records, _ = self.probe("controls_change_mid_run", frame_sets=2)
        self.assertEqual(code, 0)
        final = records[-1]
        self.assertIs(final["controls_changed_during_run"], True)
        self.assertEqual(final["controls_changed_fields"], ["red_recording_led_enabled", "brightness_ev"])
        self.assertNotEqual(final["controls_digest_start"], final["controls_digest_end"])
        start, end = [record for record in records if record["event"] == "controls"]
        self.assertEqual((start["phase"], end["phase"]), ("start", "end"))
        self.assertEqual(final["controls_digest_end"], end["controls"]["digest"])
        self.assertEqual(end["controls"]["red_recording_led_enabled"], 1)
        self.assertIs(final["led_written"], False)

    def test_bad_counts_are_invalid_metadata_after_release(self):
        for scenario in ("bad_camera_count", "bad_plane_count"):
            with self.subTest(scenario=scenario):
                code, records, _ = self.probe(scenario)
                self.assertEqual(code, 1)
                final = records[-1]
                self.assertEqual(final["outcome"], "invalid_metadata")
                self.assertEqual(final["frame_sets"], 0)
                self.assertEqual(final["fake_calls"], PROBE_CALLS)
                self.assertIs(final["reader_deactivated"], True)

    # -- refusals -----------------------------------------------------------------

    def test_argument_bounds_exit_64_before_any_prompt(self):
        cases = (
            [], ["--probe-frames", 0, "--max-seconds", 2], ["--probe-frames", 51, "--max-seconds", 2],
            ["--probe-frames", 3, "--max-seconds", 0], ["--probe-frames", 3, "--max-seconds", 11],
            ["--probe-frames", 3, "--max-seconds", 2, "--interval-ms", 99],
            ["--probe-frames", 3, "--max-seconds", 2, "--interval-ms", 1001],
            ["--probe-frames", 3], ["--hello", "--max-seconds", 2], ["--hello", "--timeout-ms", 0],
            ["--hello", "--timeout-ms", 10001], ["--hello", "--describe"], ["--unknown"], ["--hello", "--probe-frames"],
            ["--hello", "--fake-scenario", "no_such_scenario"], ["--probe-frames", "3x", "--max-seconds", 2],
        )
        for args in cases:
            with self.subTest(args=args):
                code, records, _ = self.run_helper(args, tty=False)
                self.assertEqual(code, 64)
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]["reason"], "invalid_arguments")
                self.assertIsNone(records[0]["outcome"])
        self.assertEqual(list(Path(self.runtime.name).iterdir()), [])

    def test_confirmation_is_required_and_strict(self):
        code, records, err = self.run_helper(["--hello"], "ok_linear_nv12", tty=False)
        self.assertEqual(code, 5)
        self.assertEqual(records[-1]["reason"], "confirmation_required")
        self.assertEqual(records[-1]["fake_calls"], [])
        self.assertIn("confirmation is required", err)
        for answer in ("no\n", "y\n", "YES\n", "yes please\n", "\n"):
            with self.subTest(answer=answer):
                code, records, _ = self.run_helper(["--probe-frames", 1, "--max-seconds", 1], "ok_linear_nv12",
                                                   answer=answer)
                self.assertEqual(code, 5)
                self.assertEqual(records[-1]["reason"], "confirmation_declined")
                self.assertEqual(records[-1]["fake_calls"], [])
        code, records, _ = self.run_helper(["--hello"], "ok_linear_nv12", answer="yes \r\n")
        self.assertEqual(code, 0)

    def test_runtime_dir_is_required_and_never_tmp(self):
        env = {key: value for key, value in self.env.items() if key != "XDG_RUNTIME_DIR"}
        code, records, _ = self.run_helper(["--hello"], "ok_linear_nv12", env=env)
        self.assertEqual(code, 5)
        self.assertEqual(records[-1]["reason"], "runtime_dir_missing")
        self.assertEqual(records[-1]["fake_calls"], [])
        self.assertFalse(Path("/tmp/sightcircuit-camera-helper.lock").exists())
        code, records, _ = self.run_helper(["--hello"], "ok_linear_nv12", env=dict(env, XDG_RUNTIME_DIR=""))
        self.assertEqual(records[-1]["reason"], "runtime_dir_missing")

    def test_lock_excludes_a_second_instance(self):
        first = self.start(["--probe-frames", 50, "--max-seconds", 5, "--interval-ms", 100], "slow_ok")
        self.answer(first)
        prefix = self.read_until(first, "hello")
        lock = Path(self.runtime.name) / "sightcircuit-camera-helper.lock"
        self.assertTrue(lock.is_file())
        self.assertEqual(lock.stat().st_mode & 0o777, 0o600)
        code, records, err = self.run_helper(["--hello"], "ok_linear_nv12")
        self.assertEqual(code, 5, err)
        self.assertEqual(records[-1]["reason"], "helper_already_running")
        self.assertEqual(records[-1]["fake_calls"], [])
        first.send_signal(signal.SIGTERM)
        code, records, _ = self.finish(first, prefix=prefix)
        self.assertEqual(code, 130)
        self.assertEqual(records[-1]["outcome"], "cancelled")

    def test_sigterm_and_sigint_cancel_cleanly_between_sdk_calls(self):
        for stop in (signal.SIGTERM, signal.SIGINT):
            with self.subTest(signal=stop.name):
                process = self.start(["--probe-frames", 50, "--max-seconds", 8, "--interval-ms", 100], "slow_ok")
                self.answer(process)
                prefix = self.read_until(process, "frame_set")
                process.send_signal(stop)
                started = time.monotonic()
                code, records, _ = self.finish(process, prefix=prefix)
                self.assertLess(time.monotonic() - started, 5)
                self.assertEqual(code, 130)
                final = records[-1]
                self.assertEqual((final["outcome"], final["reason"], final["stop_signal"]),
                                 ("cancelled", "cancelled", stop.name))
                self.assertIs(final["cleanup_ok"], True)
                self.assertIs(final["reader_deactivated"], True)
                calls = final["fake_calls"]
                self.assertEqual(calls[-3:], ["set_reader:false", "control_state", "disconnect"])
                self.assertGreater(calls.index("set_reader:false"), len(calls) - 1 - calls[::-1].index("acquire"))
                self.assertGreaterEqual(final["frame_sets"], 1)

    def test_watchdog_ends_a_hung_sdk_call_with_the_prebuilt_record(self):
        for scenario in ("hang_in_acquire", "hang_in_release"):
            with self.subTest(scenario=scenario):
                started = time.monotonic()
                code, records, _ = self.run_helper(["--probe-frames", 1, "--max-seconds", 5, "--timeout-ms", 1],
                                                   scenario, timeout=15)
                self.assertEqual(code, 124)
                self.assertLess(time.monotonic() - started, 8)
                final = records[-1]
                self.assertEqual(final["reason"], "watchdog_expired")
                self.assertIs(final["cleanup_ok"], False)
                self.assertIsNone(final["outcome"])
                self.assertEqual(self.events(records)[:2], ["hello", "controls"])

    # -- the planner's review of the merged helper (follow-up fixes) ---------------

    def test_a_stalled_consumer_ends_the_run_inside_the_bound(self):
        # Finding 1: with stdout a small pipe nobody reads, the run continued past --max-seconds with
        # the reader active until the pipe was drained. Now a record write waits at most one second,
        # never past the run bound; the stall ends the run with the reader deactivated; the final
        # record waits up to five seconds for a reader that resumes, and is dropped otherwise. The
        # pipe holds two pages, so the stall lands inside the probe loop, after the reader is on
        # (a one-page pipe reads as unwritable after any write at all).
        f_setpipe_sz = 1031
        for drain_after in (5.0, None):
            with self.subTest(drain_after=drain_after):
                master, slave = pty.openpty()
                read_end, write_end = os.pipe()
                fcntl.fcntl(write_end, f_setpipe_sz, 8192)
                started = time.monotonic()
                process = subprocess.Popen([str(self.binary), "--probe-frames", "50", "--max-seconds", "4",
                                            "--interval-ms", "100", "--fake-scenario", "ok_linear_nv12"],
                                           stdin=slave, stdout=write_end, stderr=subprocess.PIPE, env=self.env,
                                           start_new_session=True)
                os.close(slave)
                os.close(write_end)
                process.pty_master = master
                self.addCleanup(self.reap, process)
                self.answer(process)
                if drain_after is None:
                    process.wait(timeout=20)  # the stall at about one second, then the final record's budget
                    self.assertLess(time.monotonic() - started, 12)
                else:
                    time.sleep(drain_after)
                    self.assertIsNone(process.poll())  # past the bound: waiting on the final record, not acquiring
                data = b""
                while True:
                    chunk = os.read(read_end, 65536)
                    if not chunk:
                        break
                    data += chunk
                os.close(read_end)
                process.wait(timeout=20)
                process.stderr.close()
                self.assertEqual(process.returncode, 1)
                records, unparseable = [], 0
                for line in data.decode(errors="replace").splitlines():
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        unparseable += 1
                self.assertLessEqual(unparseable, 1)  # at most the line the stall tore
                self.assertEqual(self.events(records)[:2], ["hello", "controls"])
                self.assertLessEqual(self.events(records).count("frame_set"), 3)
                if drain_after is None:
                    self.assertNotIn("final", self.events(records))
                else:
                    final = records[-1]
                    self.assertEqual(final["event"], "final")
                    self.assertEqual((final["reason"], final["outcome"], final["exit_code"]),
                                     ("protocol_stalled", "helper_error", 1))
                    self.assertIs(final["cleanup_ok"], True)
                    self.assertIs(final["reader_activated"], True)
                    self.assertIs(final["reader_deactivated"], True)
                    self.assertEqual(final["fake_calls"][-3:], ["set_reader:false", "control_state", "disconnect"])
                    self.assertLessEqual(final["frame_sets"], 3)
                    self.assertLess(final["passes"], 10)  # the stall, not the bound, ended the loop

    def test_every_status_class_reaches_helper_error(self):
        # Finding 2: a server InternalError, a server timeout on a call other than acquire, and the
        # client's InvalidArgument and OutOfMemory end as helper_error, exit 1, with the SDK status named.
        cases = (
            ("internal_error_at_controls", ["--hello"], "control_state", "Server", "Result_InternalError",
             ["connect", "control_state", "disconnect"]),
            ("server_timeout_at_reader", None, "set_reader:true", "Server", "Result_Timeout",
             ["connect", "control_state", "set_reader:true", "disconnect"]),
            ("invalid_argument_at_acquire", None, "acquire", "InvalidArgument", "Result_Success",
             ["connect", "control_state", "set_reader:true", "acquire", "set_reader:false", "control_state", "disconnect"]),
            ("out_of_memory_at_connect", ["--hello"], "connect", "OutOfMemory", "Result_Success", ["connect", "disconnect"]),
        )
        for scenario, args, call, status_error, result, calls in cases:
            with self.subTest(scenario=scenario):
                if args:
                    code, records, _ = self.run_helper(args, scenario)
                else:
                    code, records, _ = self.probe(scenario, frame_sets=2)
                self.assertEqual(code, 1)
                outcome = [record for record in records if record["event"] == "outcome"][-1]
                self.assertEqual((outcome["call"], outcome["outcome"], outcome["retryable"]), (call, "helper_error", False))
                self.assertEqual((outcome["status_error"], outcome["result"]), (status_error, result))
                self.assertEqual(records[-1]["outcome"], "helper_error")
                self.assertEqual(records[-1]["outcome_counts"]["helper_error"], 1)
                self.assertEqual(records[-1]["fake_calls"], calls)

    def test_connect_time_invalid_metadata_reports_the_handshake_then_closes(self):
        # Finding 2: a handshake whose facts fail the bounds check (three cameras; a calibration larger
        # than the bound, reported with a null digest) ends invalid_metadata, exit 1, reader never on.
        code, records, _ = self.run_helper(["--hello"], "invalid_metadata_at_connect")
        self.assertEqual(code, 1)
        self.assertEqual(self.events(records), ["hello", "outcome", "final"])
        self.assertEqual(records[0]["camera_count"], 3)
        self.assertEqual((records[1]["call"], records[1]["outcome"]), ("connect", "invalid_metadata"))
        self.assertEqual(records[-1]["outcome"], "invalid_metadata")
        self.assertEqual(records[-1]["fake_calls"], ["connect", "disconnect"])
        code, records, _ = self.probe("calibration_oversized")
        self.assertEqual(code, 1)
        self.assertEqual(self.events(records), ["hello", "outcome", "final"])
        self.assertIs(records[0]["offline_cal_size_within_bound"], False)
        self.assertIsNone(records[0]["offline_cal_sha256"])
        self.assertGreater(records[0]["offline_cal_size"], 8192)
        self.assertIs(records[-1]["reader_activated"], False)

    def test_permission_denied_at_connect(self):
        code, records, _ = self.run_helper(["--hello"], "permission_denied_at_connect")
        self.assertEqual(code, 2)
        self.assertEqual(self.events(records), ["outcome", "final"])
        self.assertEqual((records[0]["call"], records[0]["outcome"], records[0]["result"]),
                         ("connect", "permission_denied", "Result_PermissionDenied"))
        self.assertEqual(records[-1]["fake_calls"], ["connect", "disconnect"])

    def test_bad_timestamps_are_counted_not_computed(self):
        # Finding 4: a NaN timestamp_seconds and an exposure timestamp whose difference from a host clock
        # overflows take no part in the clock offsets; each such camera is counted in invalid_timestamps.
        code, records, _ = self.probe("bad_timestamps", frame_sets=2)
        self.assertEqual(code, 0)
        final = records[-1]
        self.assertEqual(final["clock_offsets"]["invalid_timestamps"], 4)
        for clock in ("monotonic", "boottime", "realtime"):
            self.assertIsNone(final["clock_offsets"][clock]["after_minus_exposure_timestamp_ns"])
            self.assertIsNone(final["clock_offsets"][clock]["after_minus_timestamp_seconds_ns"])
        frame_set = [record for record in records if record["event"] == "frame_set"][0]
        self.assertIsNone(frame_set["cameras"][0]["timestamp_seconds"])
        self.assertEqual(frame_set["cameras"][0]["exposure_timestamp_ns"], -2 ** 63)
        code, records, _ = self.probe("ok_linear_nv12", frame_sets=1)
        self.assertEqual(records[-1]["clock_offsets"]["invalid_timestamps"], 0)

    def test_runtime_dir_must_be_an_absolute_private_directory(self):
        # Finding 5: a relative value put the lock in the working directory; a shared, foreign, absent
        # or non-directory path is no runtime directory either. All refuse before any SDK call.
        relative = "relative-runtime-dir"
        shared = Path(self.runtime.name) / "shared"
        shared.mkdir()
        os.chmod(shared, 0o755)
        regular = Path(self.runtime.name) / "file"
        regular.write_text("")
        link = Path(self.runtime.name) / "link"  # a symlink to a valid directory: the directory is opened O_NOFOLLOW
        link.symlink_to(self.runtime.name, target_is_directory=True)
        for value in (relative, str(shared), str(regular), str(link), str(Path(self.runtime.name) / "absent")):
            with self.subTest(value=value):
                code, records, err = self.run_helper(["--hello"], "ok_linear_nv12",
                                                     env=dict(self.env, XDG_RUNTIME_DIR=value))
                self.assertEqual(code, 5, err)
                self.assertEqual(records[-1]["reason"], "runtime_dir_invalid")
                self.assertEqual(records[-1]["fake_calls"], [])
        self.assertFalse((ROOT / relative).exists())
        self.assertFalse(Path(relative).exists())
        self.assertEqual(sorted(path.name for path in Path(self.runtime.name).iterdir()), ["file", "link", "shared"])

    def test_a_foreign_users_runtime_dir_is_refused(self):
        # A 0700 directory owned by another user is no runtime directory of ours. As root the test makes
        # one (owned by uid 1); otherwise it uses /root when that is 0700 and root's; else it skips.
        foreign = None
        if os.getuid() == 0:
            foreign = Path(self.runtime.name) / "foreign"
            foreign.mkdir(mode=0o700)
            os.chmod(foreign, 0o700)
            os.chown(foreign, 1, 1)
        else:
            try:
                status = os.stat("/root")
                if status.st_uid != os.getuid() and (status.st_mode & 0o7777) == 0o700:
                    foreign = Path("/root")
            except OSError:
                pass
        if foreign is None:
            self.skipTest("no directory owned by another user with mode 0700 is available here")
        code, records, err = self.run_helper(["--hello"], "ok_linear_nv12", env=dict(self.env, XDG_RUNTIME_DIR=str(foreign)))
        self.assertEqual(code, 5, err)
        self.assertEqual(records[-1]["reason"], "runtime_dir_invalid")
        self.assertEqual(records[-1]["fake_calls"], [])
        try:
            created = (foreign / "sightcircuit-camera-helper.lock").exists()
        except PermissionError:
            # The directory is not searchable by this user, so the helper, which ran as the same
            # user, could not have created a file in it either.
            created = False
        self.assertFalse(created)

    def unread_pipe_run(self, args, scenario, pipe_bytes, drain_after):
        """Run the helper with stdout a pipe of pipe_bytes that nobody reads for drain_after seconds."""
        master, slave = pty.openpty()
        read_end, write_end = os.pipe()
        fcntl.fcntl(write_end, 1031, pipe_bytes)  # F_SETPIPE_SZ
        process = subprocess.Popen([str(self.binary), *map(str, args), "--fake-scenario", scenario], stdin=slave,
                                   stdout=write_end, stderr=subprocess.PIPE, env=self.env, start_new_session=True)
        os.close(slave)
        os.close(write_end)
        process.pty_master = master
        self.addCleanup(self.reap, process)
        self.answer(process)
        time.sleep(drain_after)
        data = b""
        while True:
            chunk = os.read(read_end, 65536)
            if not chunk:
                break
            data += chunk
        os.close(read_end)
        process.wait(timeout=20)
        process.stderr.close()
        records = []
        for line in data.decode(errors="replace").splitlines():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return process.returncode, records

    def test_a_stall_keeps_a_specific_outcome_and_records_the_stall_beside_it(self):
        # Planner's note 2: a one-page pipe takes the hello record and nothing more, so the denied
        # control read's outcome record stalls. The run keeps permission_denied and exit 2; the
        # final record says protocol_stalled beside it rather than turning it into helper_error.
        code, records = self.unread_pipe_run(["--hello"], "permission_denied", 4096, 2.5)
        self.assertEqual(code, 2)
        final = records[-1]
        self.assertEqual(final["event"], "final")
        self.assertEqual((final["outcome"], final["reason"], final["exit_code"]), ("permission_denied", "permission_denied", 2))
        self.assertIs(final["protocol_stalled"], True)
        self.assertEqual(final["fake_calls"], ["connect", "control_state", "disconnect"])
        self.assertEqual(self.events(records), ["hello", "final"])  # the outcome record is the one the stall dropped
        # A stall in a healthy run is still helper_error with the stall as its reason, and the flag set.
        code, records = self.unread_pipe_run(["--probe-frames", 50, "--max-seconds", 4, "--interval-ms", 100],
                                             "ok_linear_nv12", 8192, 5.0)
        self.assertEqual(code, 1)
        self.assertEqual((records[-1]["outcome"], records[-1]["reason"]), ("helper_error", "protocol_stalled"))
        self.assertIs(records[-1]["protocol_stalled"], True)
        _, records, _ = self.run_helper(["--hello"], "ok_linear_nv12")
        self.assertIs(records[-1]["protocol_stalled"], False)

    def test_cleanup_records_after_the_bound_keep_their_write_budget(self):
        # Planner's note 3: two passes fill a two-page pipe before the one-second bound ends the loop;
        # the end control-state record is then pending against a full pipe. It gets the normal
        # one-second budget (not zero grace because the bound has passed), so a reader that drains
        # at 1.5 s receives it and the run ends ok, exit 0, no stall.
        code, records = self.unread_pipe_run(["--probe-frames", 50, "--max-seconds", 1, "--interval-ms", 500],
                                             "ok_linear_nv12", 8192, 1.5)
        self.assertEqual(code, 0, records)
        final = records[-1]
        self.assertEqual((final["outcome"], final["reason"]), ("ok", "ok"))
        self.assertIs(final["protocol_stalled"], False)
        self.assertEqual(final["frame_sets"], 2)
        self.assertEqual([record["phase"] for record in records if record["event"] == "controls"], ["start", "end"])
        self.assertEqual(final["controls_digest_end"], [r for r in records if r["event"] == "controls"][-1]["controls"]["digest"])

    def test_utf8_passes_through_the_records_and_bad_bytes_are_replaced(self):
        # Finding 6: the bytes of a UTF-8 sequence are no longer escaped one by one as Latin-1 code
        # points; a byte that is not part of a well-formed sequence becomes U+FFFD, so the line parses.
        code, records, _ = self.run_helper(["--unknown-é"], tty=False)
        self.assertEqual(code, 64)
        self.assertEqual(records[0]["detail"], "unknown argument: --unknown-é")
        result = subprocess.run([str(self.binary), b"--bad-\xff\xfe"], stdin=subprocess.DEVNULL, capture_output=True,
                                env=self.env, timeout=10)
        self.assertEqual(result.returncode, 64)
        self.assertEqual(json.loads(result.stdout)["detail"], "unknown argument: --bad-��")

    def test_line_limits_hold(self):
        _, records, _ = self.probe("ok_linear_nv12", frame_sets=2)
        for record in records:
            self.assertLessEqual(len(json.dumps(record, separators=(",", ":"))), 8192)


if __name__ == "__main__":
    unittest.main()
