"""The camera helper's fake build: every outcome, the refusals, the lock, cancellation and the watchdog.

The fake backend is built through ``scripts/build_camera_helper.sh --fake --out``
into a temporary directory, so CI exercises the script's own flag and never the
checkout's ``artifacts/``. No test touches a camera, the SDK headers or the
Client Library; every record the fake produces says ``backend: "fake"``.
The confirmation prompt is answered through a pseudo-terminal, as an operator
would, and each test gets its own ``XDG_RUNTIME_DIR`` for the lock.
"""
import errno
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

    def test_line_limits_hold(self):
        _, records, _ = self.probe("ok_linear_nv12", frame_sets=2)
        for record in records:
            self.assertLessEqual(len(json.dumps(record, separators=(",", ":"))), 8192)


if __name__ == "__main__":
    unittest.main()
