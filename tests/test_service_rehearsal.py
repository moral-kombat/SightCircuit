"""Service-only rehearsal against generated systemd states and real private files."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import service_rehearsal as rehearsal

trial, lifecycle, config = rehearsal.trial, rehearsal.lifecycle, rehearsal.config


class Manager:
    def __init__(self):
        self.properties = dict(trial.EXPECTED, MainPID="123", ControlGroup="/user.slice/steamvr.service",
                               Result="success", Job="0", InvocationID="1" * 32)
        self.calls, self.flags, self.units = [], [], {}
        self.failure = self.after_action = None
        self.populated = True
        self.policy = {"LoadState": "loaded", "PartOf": "graphical-session.target", "RefuseManualStart": "yes"}

    def show(self):
        return dict(self.properties)

    def observe(self, group, timeout=5):
        return {"service": {key: self.properties[key] for key in lifecycle.FIELDS},
                "group": group, "populated": self.populated,
                "compositors": [{"pid": 789, "start_ticks": 100}] if self.populated else []}

    def environment_flags(self):
        return self.flags

    def unit_state(self, unit):
        return self.units.get(unit, {"LoadState": "not-found", "MainPID": "0"})

    def session_policy(self):
        return dict(self.policy)

    def action(self, action, timeout=25):
        self.calls.append(action)
        if self.failure == action:
            self.failure = None
            raise trial.ServiceCommandError(["systemctl", "--user", action, trial.SERVICE],
                subprocess.CompletedProcess([], 1, "", "generated hook failure"))
        self.properties.update(ActiveState="inactive" if action == "stop" else "active",
                               SubState="dead" if action == "stop" else "running", Job="0", Result="success",
                               MainPID="0" if action == "stop" else "456")
        self.populated = action == "start"
        if action == "start":
            self.properties["InvocationID"] = "2" * 32
        if self.after_action:
            self.after_action(action)


class ServiceRehearsalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fj-service-")
        self.root = Path(self.temp.name)
        self.home, self.runtime = self.root / "home", self.root / "runtime"
        self.home.mkdir(mode=0o700)
        self.runtime.mkdir(mode=0o700)
        self.manager = Manager()
        self.clock = 0
        controller = lifecycle.Controller
        def pause(seconds):
            self.clock += seconds
        self.patches = [patch.object(Path, "home", return_value=self.home),
            patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(self.runtime)}),
            patch.object(lifecycle, "Controller", side_effect=lambda *a, **kw:
                controller(*a, **kw, clock=lambda: self.clock, pause=pause))]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def create(self):
        return rehearsal.create_run(self.home, self.runtime, self.manager, self.root / "runs")

    def assert_no_capture(self):
        self.assertEqual(list(self.home.iterdir()), [])
        self.assertEqual(list(self.runtime.iterdir()), [])

    def test_default_does_not_query_systemd_or_write_files(self):
        with patch.object(rehearsal, "Systemd") as manager, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(rehearsal.main([]), 3)
        self.assertEqual(manager.return_value.method_calls, [])
        self.assert_no_capture()

    def test_independent_ssh_requires_another_host(self):
        for value in (None, "", "127.0.0.1 123 127.0.0.1 22", "::1 12 ::1 22",
                      "192.168.0.32 123 192.168.0.32 22", "bad 12 host 22", "10.1.1.1 0 10.1.1.2 22"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "independent_inbound_ssh"):
                rehearsal.independent_ssh(value)
        rehearsal.independent_ssh("192.168.0.167 54321 192.168.0.32 22")

    def test_preflight_is_read_only_and_requires_live_compositor(self):
        self.assertEqual(rehearsal.preflight(self.home, self.runtime, self.manager), self.manager.show())
        self.assertEqual(self.manager.calls, [])
        self.assert_no_capture()
        self.manager.populated = False
        with self.assertRaisesRegex(ValueError, "baseline_runtime_unconfirmed"):
            rehearsal.preflight(self.home, self.runtime, self.manager)

    def test_preflight_refuses_existing_capture_targets_dropins_and_flags(self):
        targets = [(self.home, name) for name in config.TARGETS]
        targets += [(self.runtime, trial.DROPIN), (self.home / ".config", trial.DROPIN)]
        for root, name in targets:
            with self.subTest(name=name, root=root):
                root.mkdir(mode=0o700, exist_ok=True)
                config.write_entry(root, name, config.describe(b"foreign", 0o600))
                with self.assertRaisesRegex(ValueError, "existing_capture"):
                    rehearsal.preflight(self.home, self.runtime, self.manager)
                self.assertEqual(config.payload(config.read_entry(root, name)), b"foreign")
                config.write_entry(root, name, None)
        self.manager.flags = ["SIGHTCIRCUIT_CAPTURE_LAYER"]
        with self.assertRaisesRegex(ValueError, "existing_layer_environment"):
            rehearsal.preflight(self.home, self.runtime, self.manager)
        self.assertEqual(self.manager.calls, [])

    def test_preflight_refuses_dangling_provider_socket_and_pending_job(self):
        endpoint = self.runtime / "sightcircuit-camera-provider/camera.sock"
        endpoint.parent.mkdir(mode=0o700)
        endpoint.symlink_to(self.root / "missing")
        with self.assertRaisesRegex(ValueError, "existing_provider"):
            rehearsal.preflight(self.home, self.runtime, self.manager)
        endpoint.unlink()
        self.manager.properties["Job"] = "22"
        with self.assertRaisesRegex(ValueError, "baseline_runtime_unconfirmed"):
            rehearsal.preflight(self.home, self.runtime, self.manager)

    def test_frame_session_stop_propagation_blocks_launch_without_writing_or_restarting(self):
        self.manager.policy["PartOf"] = "graphical-session.target steamvr.service"
        output = io.StringIO()
        with patch.object(rehearsal, "Systemd", return_value=self.manager), \
             patch.dict(os.environ, {"SSH_CONNECTION": "10.1.1.1 1234 10.1.1.2 22"}), \
             patch.object(trial, "check_recovery") as dummy, contextlib.redirect_stdout(output):
            self.assertEqual(rehearsal.main(["--start"]), 1)
        self.assertEqual(json.loads(output.getvalue())["reason"], "steamvr_stop_propagates_to_graphical_session")
        dummy.assert_not_called()
        self.assertEqual(self.manager.calls, [])
        self.assert_no_capture()

    def test_unknown_session_policy_is_not_a_safe_stop(self):
        for policy in ({}, {"LoadState": "loaded"}, {"LoadState": "error"}):
            self.manager.policy = policy
            with self.assertRaisesRegex(ValueError, "gamescope_session_policy_unavailable"):
                rehearsal.preflight(self.home, self.runtime, self.manager)

    def test_session_policy_query_does_not_read_environment_or_command_arguments(self):
        with patch.object(trial, "command", return_value="LoadState=loaded\nPartOf=steamvr.service\nRefuseManualStart=yes\n") as query:
            policy = rehearsal.Systemd().session_policy()
        self.assertEqual(policy["PartOf"], "steamvr.service")
        args = query.call_args.args[0]
        self.assertEqual(args[-1], "--property=LoadState,PartOf,RefuseManualStart")

    def test_source_snapshot_and_systemd_cleanup_are_independent_of_checkout(self):
        run = self.create()
        for name in rehearsal.SOURCES:
            self.assertEqual((run / "code" / name).read_bytes(), Path(rehearsal.__file__).with_name(name).read_bytes())
        command = rehearsal.launch_command(run)
        self.assertIn("--property=RuntimeMaxSec=60", command)
        self.assertIn("--property=TimeoutStopSec=60", command)
        cleanup = next(value for value in command if value.startswith("--property=ExecStopPost="))
        self.assertIn(str(run / "code/service_rehearsal.py"), cleanup)
        self.assertIn("--recover " + str(run), cleanup)
        self.assertFalse(any("SIGHTCIRCUIT_CAPTURE_LAYER=" in arg for arg in command))
        self.assert_no_capture()

    def test_success_stops_once_starts_once_and_recovery_only_observes(self):
        run = self.create()
        self.assertEqual(rehearsal.execute(run, self.manager)["status"], "rehearsed")
        recovery = rehearsal.recover(run, self.manager)
        self.assertEqual(recovery["status"], "restored")
        self.assertTrue(recovery["normal_service_already_running"])
        self.assertEqual(self.manager.calls, ["stop", "start"])
        result = rehearsal.result(run)
        self.assertEqual(result["status"], "completed")
        self.assertFalse(result["rendering_readiness_verified"])
        self.assertTrue(result["passthrough_observation_required"])
        self.assertEqual(rehearsal.latest(self.root / "runs"), result)
        self.assert_no_capture()

    def test_state_is_armed_before_stop_and_cancellation_still_recovers(self):
        run = self.create()
        def interrupt(action):
            self.assertTrue(trial.read_json(run, "state.json")["changes_started"])
            raise KeyboardInterrupt()
        self.manager.after_action = interrupt
        with self.assertRaises(KeyboardInterrupt):
            rehearsal.execute(run, self.manager)
        self.manager.after_action = None
        self.assertEqual(rehearsal.recover(run, self.manager)["status"], "restored")
        self.assertEqual(self.manager.calls, ["stop", "start"])
        self.assertEqual(rehearsal.result(run)["status"], "failed")

    def test_stop_command_failure_records_phase_and_does_not_restart_healthy_runtime(self):
        run = self.create()
        self.manager.failure = "stop"
        result = rehearsal.execute(run, self.manager)
        self.assertEqual(result["phase"], "stopping_steamvr")
        self.assertEqual(result["command_error"]["operation"], "stop")
        self.assertEqual(result["command_error"]["stderr"], "generated hook failure")
        self.assertEqual(rehearsal.recover(run, self.manager)["status"], "restored")
        self.assertEqual(self.manager.calls, ["stop"])
        self.assertEqual(rehearsal.result(run)["status"], "failed")

    def test_failed_stop_that_eventually_stops_is_recovered_without_another_stop(self):
        run = self.create()
        def fail_after_stop(action):
            if action == "stop":
                raise ValueError("late_client_failure")
        self.manager.after_action = fail_after_stop
        self.assertEqual(rehearsal.execute(run, self.manager)["status"], "failed")
        self.assertEqual(rehearsal.recover(run, self.manager)["status"], "restored")
        self.assertEqual(self.manager.calls, ["stop", "start"])
        self.assertEqual(rehearsal.result(run)["status"], "failed")

    def test_start_failure_gets_one_independent_recovery_attempt(self):
        run = self.create()
        self.manager.failure = "start"
        result = rehearsal.execute(run, self.manager)
        self.assertEqual(result["phase"], "starting_normal_steamvr")
        self.assertEqual(result["command_error"]["operation"], "start")
        self.assertEqual(rehearsal.recover(run, self.manager)["status"], "restored")
        self.assertEqual(self.manager.calls, ["stop", "start", "start"])
        self.assertEqual(rehearsal.result(run)["status"], "failed")

    def test_unsettled_job_and_remaining_compositor_prevent_competing_start(self):
        for pending in (True, False):
            with self.subTest(pending=pending):
                self.manager = Manager()
                run = self.create()
                state = trial.read_json(run, "state.json")
                state["changes_started"] = True
                trial.save(run, "state.json", state)
                self.manager.properties.update(ActiveState="deactivating" if pending else "inactive",
                                               MainPID="0", Job="22" if pending else "0")
                self.assertEqual(rehearsal.recover(run, self.manager)["status"], "failed")
                self.assertEqual(self.manager.calls, [])

    def test_recovery_refuses_live_worker(self):
        run = self.create()
        self.manager.units[rehearsal.UNIT] = {"LoadState": "loaded", "MainPID": "777"}
        self.assertEqual(rehearsal.recover(run, self.manager)["reason"], "rehearsal_worker_still_alive_or_unknown")
        self.assertEqual(self.manager.calls, [])

    def test_pre_start_failure_needs_no_recovery_actions(self):
        run = self.create()
        self.manager.properties["InvocationID"] = "3" * 32
        self.assertEqual(rehearsal.execute(run, self.manager)["reason"], "baseline_runtime_changed")
        self.assertEqual(rehearsal.recover(run, self.manager)["status"], "not_needed")
        self.assertEqual(self.manager.calls, [])

    def test_repeated_worker_cannot_stop_again_and_completed_run_cannot_revive_later_stop(self):
        run = self.create()
        rehearsal.execute(run, self.manager)
        rehearsal.recover(run, self.manager)
        self.assertEqual(rehearsal.execute(run, self.manager)["reason"], "rehearsal_already_started")
        rehearsal.recover(run, self.manager)
        self.assertEqual(self.manager.calls, ["stop", "start"])
        self.manager.action("stop")
        self.assertEqual(rehearsal.recover(run, self.manager)["reason"], "completed_rehearsal_runtime_changed")
        self.assertEqual(self.manager.calls, ["stop", "start", "stop"])

    def test_configuration_changed_during_stop_is_preserved_and_prevents_start(self):
        run = self.create()
        def add_capture(action):
            if action == "stop":
                config.write_entry(self.home, config.MANIFEST, config.describe(b"foreign", 0o600))
        self.manager.after_action = add_capture
        self.assertEqual(rehearsal.execute(run, self.manager)["status"], "failed")
        self.assertEqual(rehearsal.recover(run, self.manager)["status"], "failed")
        self.assertEqual(self.manager.calls, ["stop"])
        self.assertEqual(config.payload(config.read_entry(self.home, config.MANIFEST)), b"foreign")

    def test_wrong_home_or_runtime_refuses_recovery(self):
        run = self.create()
        with patch.dict(os.environ, {"XDG_RUNTIME_DIR": "/wrong"}):
            self.assertEqual(rehearsal.recover(run, self.manager)["reason"], "rehearsal_runtime_mismatch")
        with patch.object(Path, "home", return_value=self.root):
            self.assertEqual(rehearsal.recover(run, self.manager)["reason"], "invalid_rehearsal_state")
        self.assertEqual(self.manager.calls, [])

    def test_launch_refuses_existing_trial_and_missing_ssh_before_any_rehearsal(self):
        for ssh, units, expected in (("", {}, "independent_inbound_ssh"),
                ("10.1.1.1 1234 10.1.1.2 22", {trial.UNIT: {"LoadState": "loaded"}}, "existing_trial_unit")):
            self.manager.units = units
            output = io.StringIO()
            with patch.object(rehearsal, "Systemd", return_value=self.manager), \
                 patch.dict(os.environ, {"SSH_CONNECTION": ssh}), \
                 patch.object(trial, "check_recovery") as dummy, contextlib.redirect_stdout(output):
                self.assertEqual(rehearsal.main(["--start"]), 1)
            self.assertIn(expected, json.loads(output.getvalue())["reason"])
            dummy.assert_not_called()
            self.assertEqual(self.manager.calls, [])

    def test_launch_timeout_retains_known_run_and_does_not_retry(self):
        run = self.create()
        output = io.StringIO()
        with patch.object(rehearsal, "Systemd", return_value=self.manager), \
             patch.dict(os.environ, {"SSH_CONNECTION": "10.1.1.1 1234 10.1.1.2 22"}), \
             patch.object(trial, "check_recovery", return_value={"status": "passed"}), \
             patch.object(rehearsal, "create_run", return_value=run), \
             patch.object(trial, "command", side_effect=subprocess.TimeoutExpired("systemd-run", 10)) as launch, \
             contextlib.redirect_stdout(output):
            self.assertEqual(rehearsal.main(["--start"]), 1)
        report = json.loads(output.getvalue())
        self.assertEqual(report["status"], "launch_unconfirmed")
        self.assertEqual(report["run_directory"], str(run))
        self.assertTrue(report["restart_requested"])
        self.assertEqual(launch.call_count, 1)

    def test_report_requires_both_worker_and_recovery_success_and_reads_no_runtime(self):
        run = self.create()
        self.assertEqual(rehearsal.result(run)["status"], "incomplete")
        rehearsal.execute(run, self.manager)
        self.assertEqual(rehearsal.result(run)["status"], "incomplete")
        rehearsal.recover(run, self.manager)
        with patch.object(rehearsal, "Systemd") as manager, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(rehearsal.main(["--report", str(run)]), 0)
        self.assertEqual(manager.return_value.method_calls, [])

    def test_state_checkpoint_precedes_service_command_and_diagnostics_are_persistent(self):
        run = self.create()
        observed = []
        def record(action):
            report = trial.read_json(run, "rehearsal.json")
            observed.append(copy.deepcopy(report["service_control"][-1]))
        self.manager.after_action = record
        rehearsal.execute(run, self.manager)
        self.assertEqual([item["operation"] for item in observed], ["stop", "start"])
        self.assertTrue(all(item["command_status"] == "requested" for item in observed))
        self.assertEqual(observed[0]["phase"], "stopping_steamvr")
        self.assertEqual(observed[1]["phase"], "starting_normal_steamvr")

    def test_worker_cli_requires_own_service(self):
        output = io.StringIO()
        with patch.object(rehearsal, "Systemd", return_value=self.manager), \
             patch.object(rehearsal, "execute") as execute, contextlib.redirect_stdout(output):
            self.assertEqual(rehearsal.main(["--worker", str(self.root)]), 1)
        execute.assert_not_called()
        self.assertIn("worker_requires_own_systemd_service", output.getvalue())
