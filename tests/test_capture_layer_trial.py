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


class Clock:
    def __init__(self):
        self.value = 0

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


class Manager:
    def __init__(self):
        self.properties = dict(trial.EXPECTED, MainPID="123", ControlGroup="/user.slice/steamvr.service",
                               Result="success", Job="0", InvocationID="1" * 32)
        self.calls = []
        self.flags = []
        self.pid = 0
        self.fail = None

    def show(self):
        return dict(self.properties)

    def observe(self, group, timeout=5):
        return {"service": {key: self.properties[key] for key in trial.lifecycle.FIELDS},
                "group": group, "populated": self.properties["ActiveState"] not in ("inactive", "failed"),
                "compositors": [{"pid": 789, "start_ticks": 100}] if self.properties["MainPID"] != "0" else []}

    def environment_flags(self):
        return self.flags

    def worker_pid(self):
        return self.pid

    def hit(self, name):
        self.calls.append(name)
        if self.fail == name:
            self.fail = None
            raise ValueError("generated_service_failure:" + name)

    def action(self, action, timeout=25):
        self.hit(action)
        self.properties.update(ActiveState="inactive" if action == "stop" else "active",
                               SubState="dead" if action == "stop" else "running",
                               MainPID="0" if action == "stop" else "456")

    def reload(self, timeout=5):
        self.hit("reload")


class PlatformManager(Manager):
    """Generated hook/state events, not an emulator or executable vendor code."""
    START = (("first_init", False), ("cache", True), ("apply_path", True),
             ("merge_overlays", True), ("health_started", False),
             ("vrstartup", False), ("waiter", False), ("charger_running", True))
    STOP = (("shutdown", False), ("health_stopped", False),
            ("display_cleanup", False), ("charger_stopped", True))

    def __init__(self, clock):
        super().__init__()
        self.clock = clock
        self.populated, self.compositor = True, True
        self.events, self.failed_hooks = [], set()
        self.overrides, self.pending = {}, None
        self.before_observe = None

    def action(self, action, timeout=25):
        self.hit(action)
        self.properties.update(Job="22", ActiveState="activating" if action == "start" else "deactivating")
        for name, ignored in self.START if action == "start" else self.STOP:
            self.events.append((name, name not in self.failed_hooks, ignored))
            if name in self.failed_hooks and not ignored:
                self.failed_hooks.remove(name)
                self.properties.update(ActiveState="failed", SubState="failed", MainPID="0", Job="0", Result="exit-code")
                self.populated = self.compositor = False
                raise trial.ServiceCommandError(["systemctl", "--user", action, trial.SERVICE],
                    subprocess.CompletedProcess([], 1, "", "A required service hook failed"))
            if name == "vrstartup":
                self.populated = self.compositor = True
            elif name == "shutdown":
                self.populated = self.compositor = False
        self.properties.update(ActiveState="active" if action == "start" else "inactive",
                               SubState="running" if action == "start" else "dead",
                               MainPID="456" if action == "start" else "0", Job="0", Result="success")
        self.populated = self.compositor = action == "start"
        if action == "start":
            self.properties["InvocationID"] = f"{len(self.events):032x}"
        callbacks = self.overrides.get(action, [])
        if callbacks:
            callbacks.pop(0)(self)

    def observe(self, group, timeout=5):
        if self.pending is not None:
            self.pending(self)
        if self.before_observe is not None:
            self.before_observe(self)
        return {"service": {key: self.properties[key] for key in trial.lifecycle.FIELDS},
                "group": group, "populated": self.populated,
                "compositors": [{"pid": 789, "start_ticks": 100}] if self.compositor else []}


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
        self.clock = Clock()
        controller = trial.lifecycle.Controller
        self.controller_patch = patch.object(trial.lifecycle, "Controller", side_effect=lambda *args, **kwargs:
            controller(*args, **kwargs, clock=self.clock.now, pause=self.clock.sleep))
        self.controller_patch.start()
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
        self.controller_patch.stop()
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
        self.assertTrue(report["normal_service_started"] or report["normal_service_already_running"])
        self.assertTrue(report["normal_runtime_observed"])
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
        self.assertEqual(report["reason"], "service_control_failure_unresolved")
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
                report = self.execute(run)
                self.assertEqual(report["status"], "failed")
                self.assertEqual(report["phase"], {"stop": "stopping_steamvr", "reload": "reloading_manager", "start": "starting_steamvr"}[phase])
                self.assert_restored(run)

    def test_service_errors_keep_operation_exit_and_bounded_stderr_without_stdout(self):
        result = subprocess.CompletedProcess([], 1, "private stdout", "Job failed: access denied\n" + "x" * 5000)
        with patch.object(trial.subprocess, "run", return_value=result), self.assertRaises(trial.ServiceCommandError) as caught:
            trial.Systemd().action("start")
        error = caught.exception
        self.assertEqual(error.details["operation"], "start")
        self.assertEqual(error.details["return_code"], 1)
        self.assertEqual(len(error.details["stderr"]), 4096)
        self.assertNotIn("private stdout", str(error.details))
        run = self.create()
        with patch.object(self.manager, "action", side_effect=error):
            report = self.execute(run)
        self.assertEqual(report["command_error"], error.details)
        self.assertEqual(report["phase"], "stopping_steamvr")
        self.assert_restored(run)
        run = self.create()
        self.execute(run)
        action = self.manager.action
        def fail_start(operation, **kwargs):
            if operation == "start":
                raise error
            return action(operation, **kwargs)
        with patch.object(self.manager, "action", side_effect=fail_start):
            report = trial.rollback(run, self.manager)
        self.assertTrue(report["configuration_restored"])
        self.assertFalse(report["normal_service_started"])
        self.assertEqual(report["command_errors"][0]["phase"], "starting_normal_steamvr")
        self.assertEqual(report["command_errors"][0]["operation"], "start")
        self.assert_restored(run)

    def test_environment_query_failures_never_retain_environment_output(self):
        result = subprocess.CompletedProcess([], 1, "SECRET=private-value", "private-error-value")
        for args in (["systemctl", "--user", "show-environment"],
                     ["systemctl", "--user", "show", trial.SERVICE, "--property=Environment", "--value"]):
            with self.subTest(args=args), patch.object(trial.subprocess, "run", return_value=result), \
                 self.assertRaises(trial.ServiceCommandError) as caught:
                trial.command(args)
            self.assertNotIn("stderr", caught.exception.details)
            self.assertNotIn("private", str(caught.exception.details))

    def test_service_details_only_reads_status_and_redacts_command_arguments(self):
        text = ("Id=steamvr.service\nRestart=always\nAfter=graphical-session.target\n"
                "ExecStart={ path=/usr/bin/launcher ; argv[]=/usr/bin/launcher private-argument ; ignore_errors=no ; }\n"
                "ExecStop=\nEnvironment=SECRET=private-environment\nUnrequested=private-unknown\n")
        completed = subprocess.CompletedProcess([], 0, text, "")
        output = io.StringIO()
        with patch.object(trial.subprocess, "run", return_value=completed) as run, \
             contextlib.redirect_stdout(output):
            self.assertEqual(trial.main(["--service-details"]), 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report["status"], "service_details")
        self.assertFalse(report["activation_requested"])
        self.assertEqual(report["service"]["ExecStart"], {"executables": ["/usr/bin/launcher"], "arguments_included": False})
        self.assertNotIn("private", output.getvalue())
        args = run.call_args.args[0]
        self.assertEqual(args[:4], ["systemctl", "--user", "show", trial.SERVICE])
        self.assertFalse(any("Environment" in value for value in args))
        self.assertEqual(run.call_count, 1)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_service_details_preserves_every_repeated_hook_without_argument_paths(self):
        # systemctl prints each Exec* array element on a separate property line.
        # Match the shipped Frame hook order, including repeated Bash commands.
        hooks = {
            "ExecStartPre": ["/usr/share/deckard/steamvr_first_init.sh",
                             "/bin/bash", "/bin/bash", "/bin/bash",
                             "/usr/share/deckard/steamvr-health-check",
                             "/usr/share/deckard/select_steamvr.sh"],
            "ExecStart": ["/usr/share/deckard/select_steamvr.sh"],
            "ExecStartPost": ["/bin/bash"],
            "ExecStop": ["/usr/share/deckard/select_steamvr.sh"],
            "ExecStopPost": ["/usr/share/deckard/steamvr-health-check",
                             "/usr/bin/displays_turn_off_drm_master", "/bin/bash"],
        }
        lines = ["Id=steamvr.service", "ExecCondition="]
        for name, paths in hooks.items():
            for path in paths:
                lines.append(f"{name}={{ path={path} ; argv[]={path} private-argument "
                             "; path=/private-argument-path ; { path=/private-fake-record ; "
                             "argv[]=/private-nested-command } ; "
                             "ignore_errors=yes ; start_time=[n/a] ; stop_time=[n/a] ; "
                             "pid=0 ; code=(null) ; status=0 }")
        lines.extend(["Environment=SECRET=private-environment", "Unrequested=private-field"])
        with patch.object(trial, "command", return_value="\n".join(lines)) as query:
            result = trial.Systemd().details()
        self.assertEqual(result, {"Id": "steamvr.service",
                                 "ExecCondition": {"executables": [], "arguments_included": False},
                                 **{name: {"executables": paths, "arguments_included": False}
                                    for name, paths in hooks.items()}})
        self.assertNotIn("private", json.dumps(result))
        query.assert_called_once()

    def test_service_details_rejects_unrecognized_hook_output_without_echoing_it(self):
        completed = subprocess.CompletedProcess([], 0, "ExecStop=private-unrecognized-record\n", "")
        output = io.StringIO()
        with patch.object(trial.subprocess, "run", return_value=completed), \
             contextlib.redirect_stdout(output):
            self.assertEqual(trial.main(["--service-details"]), 1)
        report = json.loads(output.getvalue())
        self.assertFalse(report["activation_requested"])
        self.assertEqual(report["reason"], "service_exec_output_invalid:ExecStop")
        self.assertNotIn("private", output.getvalue())

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

    def platform_run(self):
        self.manager = PlatformManager(self.clock)
        return self.create()

    def test_platform_ignored_hook_failures_preserve_order_and_allow_recovery(self):
        run = self.platform_run()
        self.manager.failed_hooks = {name for name, ignored in (*self.manager.START, *self.manager.STOP) if ignored}
        self.assertEqual(self.execute(run)["status"], "inspected")
        self.assert_restored(run)
        expected = [name for sequence in (self.manager.STOP, self.manager.START) * 2 for name, _ in sequence]
        self.assertEqual([name for name, _, _ in self.manager.events], expected)
        self.assertTrue(any(not success and ignored for _, success, ignored in self.manager.events))

    def test_platform_required_start_hook_failure_does_not_run_the_waiter(self):
        run = self.platform_run()
        self.manager.failed_hooks = {"health_started"}
        report = self.execute(run)
        self.assertEqual(report["phase"], "starting_steamvr")
        self.assertEqual(report["command_error"]["operation"], "start")
        self.assertNotIn("waiter", [name for name, _, _ in self.manager.events])
        self.assert_restored(run)

    def test_waiter_without_compositor_does_not_complete_inspection(self):
        run = self.platform_run()
        self.manager.overrides["start"] = [lambda manager: setattr(manager, "compositor", False)]
        report = self.execute(run)
        self.assertEqual(report["reason"], "normal_runtime_unconfirmed")
        self.assertEqual(report["phase"], "starting_steamvr")
        self.assertIsNone(config.read_entry(run, "inspection.json"))
        self.assert_restored(run)

    def test_delayed_compositor_is_observed_after_the_waiter_starts(self):
        run = self.platform_run()
        def delay(manager):
            manager.compositor = False
            ready = self.clock.now() + 0.4
            def later(current):
                if self.clock.now() >= ready:
                    current.compositor, current.pending = True, None
            manager.pending = later
        self.manager.overrides["start"] = [delay]
        self.assertEqual(self.execute(run)["status"], "inspected")
        self.assertGreaterEqual(self.clock.now(), 0.5)
        self.assert_restored(run)

    def test_surviving_compositor_blocks_staging_and_restoration(self):
        run = self.platform_run()
        def survivor(manager):
            manager.populated = manager.compositor = True
        self.manager.overrides["stop"] = [survivor]
        report = self.execute(run)
        self.assertEqual(report["reason"], "steamvr_stop_unconfirmed")
        self.assertEqual(list(self.home.iterdir()), [])
        self.assert_restored(run)
        run = self.create()
        self.execute(run)
        self.manager.overrides["stop"] = [survivor]
        report = trial.rollback(run, self.manager)
        self.assertFalse(report["configuration_restored"])
        self.assertFalse(report["normal_service_started"])
        self.assertEqual(config.read_entry(self.home, config.LIBRARY), self.plan["after"][config.LIBRARY])
        self.assertIsNone(config.read_entry(self.runtime, trial.DROPIN))
        self.assert_restored(run)

    def test_canceled_stop_is_reconciled_without_a_second_stop_or_hidden_error(self):
        run = self.platform_run()
        self.execute(run)
        def canceled(manager):
            manager.properties.update(ActiveState="deactivating", SubState="stop", Job="33")
            manager.populated = manager.compositor = True
            ready = self.clock.now() + 0.3
            def finish(current):
                if self.clock.now() >= ready:
                    current.properties.update(ActiveState="inactive", SubState="dead", MainPID="0", Job="0")
                    current.populated = current.compositor = False
                    current.pending = None
            manager.pending = finish
            raise trial.ServiceCommandError(["systemctl", "--user", "stop", trial.SERVICE],
                subprocess.CompletedProcess([], 1, "", "Job canceled"))
        self.manager.overrides["stop"] = [canceled]
        calls = len(self.manager.calls)
        report = trial.rollback(run, self.manager)
        self.assertEqual(self.manager.calls[calls:], ["stop", "reload", "start"])
        self.assertEqual(report["status"], "recovery_failed")
        self.assertTrue(report["configuration_restored"])
        self.assertTrue(report["normal_runtime_observed"])
        self.assertEqual(report["command_errors"][0]["stderr"], "Job canceled")
        calls = list(self.manager.calls)
        again = trial.rollback(run, self.manager)
        self.assertTrue(again["runtime_rechecked_without_restart"])
        self.assertEqual(again["status"], "recovery_failed")
        self.assertEqual(self.manager.calls, calls)

    def test_required_post_stop_failure_is_retained_even_when_the_group_is_empty(self):
        for name in ("health_stopped", "display_cleanup"):
            with self.subTest(hook=name):
                run = self.platform_run()
                self.execute(run)
                self.manager.failed_hooks = {name}
                report = trial.rollback(run, self.manager)
                self.assertEqual(report["status"], "recovery_failed")
                self.assertTrue(report["configuration_restored"])
                self.assertTrue(report["normal_runtime_observed"])
                self.assertEqual(report["command_errors"][0]["operation"], "stop")

    def test_competing_start_during_restore_preserves_configuration_and_does_not_retry(self):
        run = self.platform_run()
        self.execute(run)
        observations = 0
        def compete(manager):
            nonlocal observations
            observations += 1
            if observations == 4:  # Initial observation, two empty observations, then competing startup.
                manager.properties.update(ActiveState="activating", SubState="start-pre", Job="44")
                manager.populated = True
        self.manager.before_observe = compete
        calls = len(self.manager.calls)
        report = trial.rollback(run, self.manager)
        self.assertFalse(report["configuration_restored"])
        self.assertEqual(self.manager.calls[calls:], ["stop", "reload"])
        for name in config.TARGETS:
            self.assertEqual(config.read_entry(self.home, name), self.plan["after"][name])
        self.assertIsNone(config.read_entry(self.runtime, trial.DROPIN))

    def test_foreign_configuration_is_not_followed_by_an_automatic_start(self):
        run = self.platform_run()
        self.execute(run)
        config.write_entry(self.home, config.MANIFEST, config.describe(b"foreign edit", 0o600))
        calls = len(self.manager.calls)
        report = trial.rollback(run, self.manager)
        self.assertFalse(report["configuration_restored"])
        self.assertEqual(self.manager.calls[calls:], ["stop", "reload"])
        self.assertEqual(config.payload(config.read_entry(self.home, config.MANIFEST)), b"foreign edit")

    def test_transition_intent_and_recovery_phase_are_saved_before_commands(self):
        run = self.platform_run()
        action = self.manager.action
        def check(operation, **kwargs):
            state = trial.read_json(run, "state.json")
            history = state.get("recovery_service_control", state.get("service_control"))
            self.assertEqual(history[-1]["operation"], operation)
            self.assertEqual(history[-1]["command_status"], "requested")
            return action(operation, **kwargs)
        with patch.object(self.manager, "action", side_effect=check):
            self.execute(run)
            self.assert_restored(run)
        self.assertEqual(trial.read_json(run, "state.json")["recovery_phase"], "starting_normal_steamvr")

    def test_timeout_diagnostics_are_bounded_and_exclude_environment_queries(self):
        error = subprocess.TimeoutExpired("hidden command", 0.2, output=b"private-stdout", stderr=b"timed out" * 1000)
        for operation in ("stop", "show-environment"):
            with patch.object(trial.subprocess, "run", side_effect=error), self.assertRaises(trial.ServiceCommandError) as caught:
                trial.command(["systemctl", "--user", operation, *([trial.SERVICE] if operation == "stop" else [])])
            details = caught.exception.details
            self.assertTrue(details["timed_out"])
            self.assertIsNone(details["return_code"])
            self.assertNotIn("private-stdout", str(details))
            if operation == "stop":
                self.assertEqual(len(details["stderr"]), 4096)
            else:
                self.assertNotIn("stderr", details)

    def test_read_only_runtime_status_has_no_configuration_or_service_actions(self):
        output = io.StringIO()
        with patch.object(trial, "Systemd", return_value=self.manager), contextlib.redirect_stdout(output):
            self.assertEqual(trial.main(["--runtime-status"]), 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report["status"], "runtime_observed")
        self.assertTrue(report["normal_runtime_observed"])
        self.assertFalse(report["rendering_readiness_verified"])
        self.assertFalse(report["activation_requested"])
        self.assertEqual(self.manager.calls, [])
        self.assertEqual(list(self.home.iterdir()), [])
        self.assertEqual(list(self.runtime.iterdir()), [])

    def test_pending_baseline_job_or_changed_invocation_refuses_before_stop(self):
        self.manager.properties["Job"] = "42"
        with self.assertRaisesRegex(ValueError, "baseline_runtime_unconfirmed"):
            self.create()
        self.manager.properties["Job"] = "0"
        run = self.create()
        self.manager.properties["InvocationID"] = "f" * 32
        report = self.execute(run)
        self.assertEqual(report["reason"], "steamvr_service_properties_changed")
        self.assertFalse(trial.read_json(run, "state.json")["changes_started"])
        self.assertEqual(self.manager.calls, [])

    def test_competing_start_between_restored_files_preserves_the_remaining_library(self):
        run = self.platform_run()
        self.execute(run)
        write = config.write_entry
        def intervene(root, name, entry):
            result = write(root, name, entry)
            if root == self.home and name == config.MANIFEST and entry is None:
                self.manager.properties.update(ActiveState="activating", SubState="start-pre", Job="99")
                self.manager.populated = True
            return result
        calls = len(self.manager.calls)
        with patch.object(config, "write_entry", side_effect=intervene):
            report = trial.rollback(run, self.manager)
        self.assertFalse(report["configuration_restored"])
        self.assertIsNone(config.read_entry(self.home, config.MANIFEST))
        self.assertEqual(config.read_entry(self.home, config.LIBRARY), self.plan["after"][config.LIBRARY])
        self.assertEqual(self.manager.calls[calls:], ["stop", "reload"])

    def test_start_timeout_retains_liveness_evidence_without_claiming_a_successful_command(self):
        run = self.platform_run()
        self.execute(run)
        def timeout_after_start(manager):
            raise trial.ServiceCommandError(["systemctl", "--user", "start", trial.SERVICE],
                subprocess.TimeoutExpired("systemctl", 20, stderr=b"timed out"))
        self.manager.overrides["start"] = [timeout_after_start]
        report = trial.rollback(run, self.manager)
        self.assertEqual(report["status"], "recovery_failed")
        self.assertTrue(report["configuration_restored"])
        self.assertFalse(report["normal_service_started"])
        self.assertTrue(report["normal_runtime_observed"])
        self.assertTrue(report["command_errors"][0]["timed_out"])
        calls = list(self.manager.calls)
        self.assertTrue(trial.rollback(run, self.manager)["runtime_rechecked_without_restart"])
        self.assertEqual(self.manager.calls, calls)

    def test_lost_compositor_after_restoration_is_reported_without_restarting_a_healthy_service(self):
        run = self.platform_run()
        self.execute(run)
        self.assert_restored(run)
        self.manager.compositor = False
        calls = list(self.manager.calls)
        report = trial.rollback(run, self.manager)
        self.assertFalse(report["normal_runtime_observed"])
        self.assertTrue(report["runtime_rechecked_without_restart"])
        self.assertEqual(report["status"], "recovery_failed")
        self.assertEqual(self.manager.calls, calls)

    def test_existing_shutdown_job_is_observed_without_a_competing_stop(self):
        run = self.platform_run()
        self.execute(run)
        self.manager.properties.update(ActiveState="deactivating", SubState="stop", MainPID="0", Job="99")
        ready = self.clock.now() + 0.3
        def finish(manager):
            if self.clock.now() >= ready:
                manager.properties.update(ActiveState="inactive", SubState="dead", MainPID="0", Job="0")
                manager.compositor = manager.populated = False
                manager.pending = None
        self.manager.pending = finish
        calls = len(self.manager.calls)
        report = trial.rollback(run, self.manager)
        self.assertEqual(report["status"], "restored", report)
        self.assertEqual(self.manager.calls[calls:], ["reload", "start"])
        self.assertEqual(report["service_control"][0]["operation"], "observe_existing_job")

    def test_failed_initial_stop_with_unchanged_healthy_configuration_needs_no_restart(self):
        run = self.platform_run()
        self.manager.fail = "stop"
        self.assertEqual(self.execute(run)["status"], "failed")
        calls = list(self.manager.calls)
        report = trial.rollback(run, self.manager)
        self.assertEqual(report["status"], "restored", report)
        self.assertTrue(report["normal_service_already_running"])
        self.assertFalse(report["normal_service_started"])
        self.assertTrue(report["normal_runtime_observed"])
        self.assertEqual(self.manager.calls, calls)

    def test_unsettled_existing_job_preserves_files_without_any_stop_or_start_request(self):
        run = self.platform_run()
        self.execute(run)
        self.manager.properties.update(ActiveState="activating", SubState="start-pre", Job="99")
        calls = len(self.manager.calls)
        report = trial.rollback(run, self.manager)
        self.assertEqual(report["status"], "recovery_failed")
        self.assertFalse(report["configuration_restored"])
        self.assertEqual(self.manager.calls[calls:], ["reload"])
        self.assertEqual(config.read_entry(self.home, config.LIBRARY), self.plan["after"][config.LIBRARY])
        self.assertIsNone(config.read_entry(self.runtime, trial.DROPIN))

    def test_explicit_empty_systemd_job_is_idle_but_missing_job_is_unknown(self):
        for output, expected in (("MainPID=10\nJob=\n", "0"), ("MainPID=10\n", None), ("Job=12\n", "12")):
            with self.subTest(output=output), patch.object(trial, "command", return_value=output) as query:
                service = trial.Systemd().show()
            self.assertEqual(service.get("Job"), expected)
            self.assertIn("--all", query.call_args.args[0])

    def test_latest_report_requires_runtime_evidence_before_claiming_completion(self):
        run = self.create()
        self.execute(run)
        self.assert_restored(run)
        report = trial.read_json(run, "rollback.json")
        report.pop("normal_runtime_observed")
        trial.save(run, "rollback.json", report)
        self.assertEqual(trial.latest_report(run.parent)["status"], "failed")

    def test_files_restored_externally_do_not_prove_the_loaded_layer_is_unloaded(self):
        run = self.platform_run()
        self.execute(run)
        config.restore(run, expected_home=self.home)
        config.write_entry(self.runtime, trial.DROPIN, None)
        calls = len(self.manager.calls)
        report = trial.rollback(run, self.manager)
        self.assertEqual(report["status"], "restored", report)
        self.assertFalse(report["normal_service_already_running"])
        self.assertEqual(self.manager.calls[calls:], ["stop", "reload", "start"])
