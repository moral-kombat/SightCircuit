"""Real temporary configuration with simulated systemd; no live VR changes."""
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import capture_layer_trial as trial
from scripts import capture_layer_config as config


class Manager:
    def __init__(self):
        self.properties = dict(trial.EXPECTED, MainPID="123", ControlGroup="/user.slice/steamvr.service")
        self.calls = []
        self.flags = []
        self.pid = 0
        self.fail = None

    def show(self):
        return dict(self.properties)

    def environment_flags(self):
        return self.flags

    def worker_pid(self):
        return self.pid

    def hit(self, name):
        self.calls.append(name)
        if self.fail == name:
            self.fail = None
            raise ValueError("generated_service_failure:" + name)

    def action(self, action):
        self.hit(action)
        self.properties.update(ActiveState="inactive" if action == "stop" else "active",
                               SubState="dead" if action == "stop" else "running",
                               MainPID="0" if action == "stop" else "456")

    def reload(self):
        self.hit("reload")


class MetadataLayerTrialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fj-metadata-trial-")
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.runtime = self.root / "runtime"
        self.build = self.root / "build"
        for path in (self.home, self.runtime, self.build):
            path.mkdir(mode=0o700)
        self.source = self.build / "reference-libVkLayer_mrhand.so"
        self.source.write_bytes(b"\x7fELFgenerated-metadata-library")
        self.inspector = self.build / "framejacker-provider-inspect"
        self.inspector.write_bytes(b"\x7fELFgenerated-inspector")
        self.inspector.chmod(0o700)
        self.patches = [patch.object(config, "SOURCE", self.source), patch.object(trial.metadata, "BINARY", self.inspector)]
        for item in self.patches:
            item.start()
        def digest(path):
            return {"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "size_bytes": path.stat().st_size}
        self.build_report = {"provider_protocol_version": 3, "image_fd_export_enabled": False,
                             "layer_retains_image_fds": False, "deployment_ready": False,
                             "sources": [digest(trial.ROOT / "tools" / name) for name in
                                         ("capture_layer_impl.hpp", "capture_provider.hpp", "capture_layer_gate.hpp", "capture_provider_inspect.cpp")],
                             "artifacts": [digest(self.source)], "metadata_inspector": digest(self.inspector)}
        self.save_build()
        self.bundle = self.root / "bundle"
        self.plan = config.prepare(self.home, self.source, self.bundle)
        self.manager = Manager()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def save_build(self):
        (self.build / "build-report.json").write_text(json.dumps(self.build_report))

    def create(self):
        return trial.create_run(self.bundle, self.runtime, self.home, self.manager, self.root / "runs")

    def inspect(self, **kwargs):
        self.assertEqual(kwargs, {"runtime": self.runtime, "seconds": 2})
        self.assertEqual(config.read_entry(self.runtime, trial.DROPIN), config.describe(trial.ENABLE, 0o600))
        self.assertTrue(all(config.read_entry(self.home, name) == self.plan["after"][name] for name in config.TARGETS))
        return {"report_kind": "capture_provider_check", "status": "inspected", "exit_code": 0,
                "record_count": 2, "reviewed_profile_candidates": 1, "native": {"peer_pid": 42}}

    def execute(self, run, **kwargs):
        return trial.execute(run, self.manager, inspect=self.inspect,
                             verify_peer=lambda *_: True, wait_seconds=0, **kwargs)

    def assert_restored(self, run):
        report = trial.rollback(run, self.manager)
        self.assertEqual(report["status"], "restored", report)
        self.assertTrue(report["configuration_restored"])
        self.assertTrue(report["normal_service_started"])
        for name in config.TARGETS:
            self.assertIsNone(config.read_entry(self.home, name))
        self.assertIsNone(config.read_entry(self.runtime, trial.DROPIN))
        return report

    def test_default_help_and_invalid_cli_do_not_use_systemd(self):
        with patch.object(trial, "command", side_effect=AssertionError("no command")), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(trial.main([]), 3)
        for args, code in ((["--help"], 0), (["--unknown"], 2)):
            result = subprocess.run([sys.executable, "scripts/capture_layer_trial.py", *args],
                                    cwd=trial.ROOT, capture_output=True, timeout=3)
            self.assertEqual(result.returncode, code)

    def test_preflight_is_read_only_and_matches_reported_service(self):
        plan, properties = trial.preflight(self.bundle, self.runtime, self.home, self.manager)
        self.assertEqual(plan, self.plan)
        self.assertEqual(properties, self.manager.properties)
        self.assertEqual(self.manager.calls, [])
        self.assertEqual(list(self.home.iterdir()), [])
        self.assertEqual(list(self.runtime.iterdir()), [])

    def test_changed_service_properties_and_existing_enable_flags_are_refused(self):
        for key, value in (("Type", "notify"), ("KillMode", "process"), ("TimeoutStopUSec", "90s"),
                           ("DropInPaths", "/other.conf"), ("ActiveState", "inactive"), ("MainPID", "0")):
            with self.subTest(key=key):
                original = self.manager.properties[key]
                self.manager.properties[key] = value
                with self.assertRaisesRegex(ValueError, "properties_changed"):
                    trial.preflight(self.bundle, self.runtime, self.home, self.manager)
                self.manager.properties[key] = original
        self.manager.flags = ["FRAMEJACKER_CAPTURE_LAYER"]
        with self.assertRaisesRegex(ValueError, "environment_flag"):
            self.create()
        self.assertEqual(list(self.home.iterdir()), [])

    def test_legacy_stale_missing_and_mismatched_builds_are_refused(self):
        original = copy.deepcopy(self.build_report)
        for change in (lambda report: report.update(provider_protocol_version=2),
                       lambda report: report.update(image_fd_export_enabled=True),
                       lambda report: report.update(sources=[]),
                       lambda report: report["sources"][0].update(sha256="0" * 64),
                       lambda report: report["artifacts"][0].update(sha256="0" * 64),
                       lambda report: report["metadata_inspector"].update(sha256="0" * 64)):
            self.build_report = copy.deepcopy(original)
            change(self.build_report)
            self.save_build()
            with self.assertRaises(ValueError):
                self.create()
        self.assertEqual(list(self.home.iterdir()), [])

    def test_existing_endpoint_dropin_and_foreign_target_are_preserved(self):
        endpoint = self.runtime / "framejacker-camera-provider/camera.sock"
        endpoint.parent.mkdir(mode=0o700)
        endpoint.symlink_to("/dev/null")
        with self.assertRaisesRegex(ValueError, "existing_provider_endpoint"):
            self.create()
        endpoint.unlink()
        for root, name in ((self.home, config.MANIFEST), (self.runtime, trial.DROPIN)):
            config.write_entry(root, name, config.describe(b"foreign", 0o600))
            with self.assertRaises(ValueError):
                self.create()
            self.assertEqual(config.payload(config.read_entry(root, name)), b"foreign")
            config.write_entry(root, name, None)

    def test_launch_is_independent_and_has_systemd_owned_recovery(self):
        run = self.create()
        command = trial.launch_command(run)
        self.assertIn("--collect", command)
        self.assertIn("--property=RuntimeMaxSec=60", command)
        self.assertIn("--property=TimeoutStopSec=60", command)
        self.assertIn("--property=Restart=no", command)
        self.assertTrue(any(item.startswith("--property=ExecStopPost=") and "--rollback " in item for item in command))
        self.assertEqual(command[-2:], ["--worker", str(run)])
        self.assertNotIn("--pipe", command)
        self.assertNotIn("--pty", command)
        self.assertEqual(self.manager.calls, [])

    def test_recovery_rehearsal_requires_both_started_worker_and_timed_out_cleanup(self):
        for valid in (True, False):
            with self.subTest(valid=valid):
                commands = []
                def run(command, timeout):
                    commands.append(command)
                    if command[0] == "systemd-run":
                        path = Path(command[-1])
                        trial.save(path, "started.json", {"started": True})
                        trial.save(path, "cleanup.json", {"service_result": "timeout" if valid else "success"})
                if valid:
                    self.assertEqual(trial.check_recovery(self.root / "checks", runner=run)["status"], "passed")
                else:
                    with self.assertRaisesRegex(ValueError, "post_exit_recovery_check_failed"):
                        trial.check_recovery(self.root / "checks", runner=run)
                self.assertIn("--property=RuntimeMaxSec=1", commands[0])
                self.assertIn("--recovery-hold", commands[0])
                self.assertTrue(all(trial.SERVICE not in command for command in commands))

    def test_ambiguous_launch_reports_the_attempt_and_recovery_path(self):
        output = io.StringIO()
        with patch.object(trial, "ACTIVATION_BLOCKED_REASON", None), \
             patch.object(trial, "Systemd", return_value=self.manager), \
             patch.object(Path, "home", return_value=self.home), \
             patch.object(trial, "RUNS", self.root / "runs"), \
             patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(self.runtime)}), \
             patch.object(trial, "check_recovery", return_value={"status": "passed"}), \
             patch.object(trial, "command", side_effect=subprocess.TimeoutExpired("systemd-run", 10)), \
             contextlib.redirect_stdout(output):
            self.assertEqual(trial.main(["--start-metadata-test", str(self.bundle)]), 1)
        report = json.loads(output.getvalue())
        self.assertTrue(report["activation_requested"])
        self.assertEqual(report["status"], "launch_unconfirmed")
        self.assertTrue(Path(report["trial_directory"]).is_dir())
        self.assertEqual(self.manager.calls, [])

    def test_device_failure_pause_blocks_activation_before_runtime_or_file_calls(self):
        output = io.StringIO()
        with patch.object(trial, "Systemd", side_effect=AssertionError("must not query manager")), \
             patch.object(trial, "create_run", side_effect=AssertionError("must not write files")), \
             patch.object(trial, "check_recovery", side_effect=AssertionError("must not start rehearsal")), \
             contextlib.redirect_stdout(output):
            self.assertEqual(trial.main(["--start-metadata-test", str(self.bundle)]), 3)
        report = json.loads(output.getvalue())
        self.assertEqual(report["status"], "blocked")
        self.assertFalse(report["activation_requested"])
        self.assertEqual(report["reason"], "device_restart_console_recovery_unverified")
        self.assertEqual(list(self.home.iterdir()), [])

    def test_successful_inspection_and_recovery_restore_exact_absence(self):
        run = self.create()
        result = self.execute(run)
        self.assertEqual(result["status"], "inspected", result)
        self.assertEqual(self.manager.calls, ["stop", "reload", "start"])
        self.assert_restored(run)
        self.assertEqual(self.manager.calls, ["stop", "reload", "start", "stop", "reload", "start"])
        calls = list(self.manager.calls)
        self.assertEqual(trial.rollback(run, self.manager)["status"], "restored")
        self.assertEqual(self.manager.calls, calls)
        report = trial.latest_report(run.parent)
        self.assertEqual(report["status"], "completed")
        self.assertTrue(report["passthrough_observation_required"])

    def test_failed_inspection_still_restores_and_does_not_claim_success(self):
        run = self.create()
        result = trial.execute(run, self.manager, inspect=lambda **_: {"status": "inspection_failed", "exit_code": 1}, wait_seconds=0)
        self.assertEqual(result["reason"], "metadata_inspection_failed")
        self.assert_restored(run)
        self.assertEqual(trial.latest_report(run.parent)["status"], "failed")

    def test_peer_must_be_the_service_compositor(self):
        run = self.create()
        result = trial.execute(run, self.manager, inspect=self.inspect, verify_peer=lambda *_: False, wait_seconds=0)
        self.assertEqual(result["reason"], "metadata_peer_not_service_compositor")
        self.assert_restored(run)
        service = {"ControlGroup": "/user.slice/steamvr.service"}
        native = {"peer_pid": os.getpid(), "peer_uid": os.geteuid()}
        with patch.object(trial.os, "readlink", return_value="/runtime/bin/vrcompositor"), \
             patch.object(Path, "read_text", return_value="0::/user.slice/steamvr.service/child\n"):
            self.assertTrue(trial.peer_is_compositor(native, service))
            self.assertFalse(trial.peer_is_compositor(native, {"ControlGroup": "/another.service"}))
        self.assertFalse(trial.peer_is_compositor(native, service))

    def test_service_failures_at_each_phase_allow_post_exit_recovery(self):
        for phase in ("stop", "reload", "start"):
            with self.subTest(phase=phase):
                self.manager = Manager()
                run = self.create()
                self.manager.fail = phase
                self.assertEqual(self.execute(run)["status"], "failed")
                self.assert_restored(run)

    def test_partial_installation_is_recovered_after_write_failure(self):
        run = self.create()
        write = config.write_entry
        def fail_manifest(home, name, entry):
            if home == self.home and name == config.MANIFEST and entry is not None:
                raise OSError("generated_manifest_write_failure")
            return write(home, name, entry)
        with patch.object(config, "write_entry", side_effect=fail_manifest):
            self.assertEqual(self.execute(run)["status"], "failed")
        self.assertIsNotNone(config.read_entry(self.home, config.LIBRARY))
        self.assert_restored(run)

    def test_persisted_armed_state_survives_abrupt_worker_exit(self):
        class AbruptExit(BaseException):
            pass
        run = self.create()
        with patch.object(self.manager, "reload", side_effect=AbruptExit):
            with self.assertRaises(AbruptExit):
                self.execute(run)
        self.assertTrue(trial.read_json(run, "state.json")["changes_started"])
        self.assert_restored(run)

    def test_recovery_preserves_later_edits_and_removes_only_own_enable_flag(self):
        for name in (config.MANIFEST, trial.DROPIN):
            with self.subTest(name=name):
                run = self.create()
                self.execute(run)
                root = self.home if name == config.MANIFEST else self.runtime
                config.write_entry(root, name, config.describe(b"later edit", 0o600))
                report = trial.rollback(run, self.manager)
                self.assertEqual(report["status"], "recovery_failed")
                self.assertEqual(config.payload(config.read_entry(root, name)), b"later edit")
                if name == config.MANIFEST:
                    self.assertIsNone(config.read_entry(self.runtime, trial.DROPIN))
                # Reset only this generated fixture before the next subcase.
                for item in config.TARGETS:
                    if config.read_entry(self.home, item) is not None:
                        config.write_entry(self.home, item, None)
                if config.read_entry(self.runtime, trial.DROPIN) is not None:
                    config.write_entry(self.runtime, trial.DROPIN, None)
                self.manager = Manager()

    def test_unconfirmed_stop_and_baseline_start_failures_are_not_success(self):
        for failure in ("stop", "start"):
            with self.subTest(failure=failure):
                run = self.create()
                self.execute(run)
                self.manager.fail = failure
                report = trial.rollback(run, self.manager)
                self.assertEqual(report["status"], "recovery_failed")
                self.assertIsNone(config.read_entry(self.runtime, trial.DROPIN))
                self.assert_restored(run)
                self.manager = Manager()

    def test_living_worker_lock_contention_and_symlink_lock_are_refused(self):
        run = self.create()
        self.manager.pid = os.getpid()
        self.assertEqual(trial.rollback(run, self.manager)["reason"], "trial_worker_still_running")
        self.assertEqual(self.manager.calls, [])
        self.manager.pid = 0
        fd = trial.locked_run(run)
        try:
            with self.assertRaises(BlockingIOError):
                trial.locked_run(run)
        finally:
            os.close(fd)
        (run / "lock").unlink()
        (run / "lock").symlink_to("/dev/null")
        with self.assertRaises(OSError):
            trial.locked_run(run)

    def test_unarmed_trial_recovery_and_default_report_make_no_service_changes(self):
        run = self.create()
        self.assertEqual(trial.latest_report(run.parent)["status"], "incomplete")
        report = trial.rollback(run, self.manager)
        self.assertEqual(report["status"], "no_changes")
        self.assertEqual(self.manager.calls, [])
        with self.assertRaisesRegex(ValueError, "no_metadata_trial"):
            trial.latest_report(self.root / "absent")

    def test_collected_unit_is_not_mistaken_for_a_living_worker(self):
        result = subprocess.CompletedProcess([], 4, "LoadState=not-found\nMainPID=0\n", "")
        with patch.object(trial.subprocess, "run", return_value=result):
            self.assertEqual(trial.Systemd().worker_pid(), 0)
        result = subprocess.CompletedProcess([], 1, "", "manager unavailable")
        with patch.object(trial.subprocess, "run", return_value=result), self.assertRaises(ValueError):
            trial.Systemd().worker_pid()

    def test_direct_worker_and_unsafe_paths_are_refused(self):
        output = io.StringIO()
        with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(output):
            self.assertEqual(trial.main(["--worker", str(self.root)]), 1)
        self.assertEqual(json.loads(output.getvalue())["reason"], "worker_requires_own_systemd_service")
        for value in ("/tmp/has space", "/tmp/$HOME", "/tmp/%h", "/tmp/a/../b"):
            with self.assertRaises(ValueError):
                trial.safe_path(value)
