"""Generated full-session transitions; no real session or camera calls."""
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

from scripts import session_rehearsal as session

trial, config = session.trial, session.config


def snapshot(generation=1):
    units = {}
    for index, name in enumerate(session.UNITS):
        units[name] = {"Id": name, "LoadState": "loaded", "ActiveState": "active",
            "SubState": "running" if name.endswith(".service") else "active", "MainPID": str(100 + index),
            "ControlGroup": "/user.slice/" + name, "Result": "success", "Job": "0",
            "InvocationID": f"{generation * 10 + index:032x}", "DropInPaths": "",
            "PartOf": "graphical-session.target steamvr.service", "RefuseManualStart": "yes",
            "Requires": "gamescope-session.service"}
    return {"units": units, "display_manager": {"ActiveState": "active", "SubState": "running", "Job": "0"},
            "runtime": {"service": dict(units[trial.SERVICE]), "populated": True,
                        "compositors": [{"pid": 200 + generation, "start_ticks": generation * 100}]}}


class Manager:
    def __init__(self):
        self.current = snapshot()
        self.calls, self.units, self.flags = [], {}, []
        self.contract = {"packages": {"steamos-manager": "26.4.1-3"}, "default_login_mode": "game",
                         "switch_method_available": True, "files": {"/example": "a" * 64}}
        self.failure = self.after_switch = None

    def snapshot(self, timeout=8):
        return copy.deepcopy(self.current)

    def platform_contract(self):
        return copy.deepcopy(self.contract)

    def environment_flags(self):
        return self.flags

    def unit_state(self, unit):
        return self.units.get(unit, {"LoadState": "not-found", "MainPID": "0"})

    def switch_to_game(self, timeout=10):
        self.calls.append("switch_to_game_mode")
        self.current = snapshot(2)
        if self.after_switch:
            self.after_switch()
        if self.failure:
            raise self.failure


class SessionRehearsalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fj-session-")
        self.root = Path(self.temp.name)
        self.home, self.runtime = self.root / "home", self.root / "runtime"
        self.home.mkdir(mode=0o700)
        self.runtime.mkdir(mode=0o700)
        self.manager = Manager()
        self.clock = 0
        self.patches = [patch.object(Path, "home", return_value=self.home),
            patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(self.runtime)}),
            patch.object(session.time, "sleep", side_effect=self.pause)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def pause(self, seconds):
        self.clock += seconds

    def observe(self, *args, **kwargs):
        return session.observe(*args, **kwargs, clock=lambda: self.clock, pause=self.pause)

    def prepare(self):
        bundle = self.root / ("plan-" + str(len(list(self.root.iterdir()))))
        session.prepare(bundle, self.home, self.runtime, self.manager)
        return bundle

    def create(self):
        return session.create_run(self.prepare(), self.manager, self.root / "runs")

    def execute(self, run):
        return session.execute(run, self.manager, observer=self.observe)

    def recover(self, run):
        return session.recover(run, self.manager, observer=self.observe)

    def test_default_and_missing_logout_acknowledgment_make_no_runtime_calls(self):
        for args, expected in (([], 3), (["--start", "/nonexistent"], 1), (["--wait"], 1)):
            with self.subTest(args=args), patch.object(session, "Systemd") as manager, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(session.main(args), expected)
            self.assertEqual(manager.return_value.method_calls, [])

    def test_prepare_is_inactive_exclusive_and_freezes_helpers(self):
        bundle = self.prepare()
        plan = session.load_plan(bundle)
        self.assertFalse(plan["capture_enabled"])
        self.assertEqual(set(plan["helpers"]), set(session.SOURCES))
        self.assertEqual(self.manager.calls, [])
        self.assertEqual(list(self.home.iterdir()), [])
        self.assertEqual(list(self.runtime.iterdir()), [])
        with self.assertRaises(FileExistsError):
            session.prepare(bundle, self.home, self.runtime, self.manager)

    def test_modified_helper_platform_or_home_refuses_start(self):
        bundle = self.prepare()
        config.write_entry(bundle, "code/session_rehearsal.py", config.describe(b"altered", 0o600))
        with self.assertRaisesRegex(ValueError, "prepared_helper_changed"):
            session.check(bundle, self.manager)
        bundle = self.prepare()
        self.manager.contract["files"]["/example"] = "b" * 64
        with self.assertRaisesRegex(ValueError, "platform_changed"):
            session.check(bundle, self.manager)
        with patch.object(Path, "home", return_value=self.root), self.assertRaisesRegex(ValueError, "invalid_session_preparation"):
            session.load_plan(bundle)
        self.assertEqual(self.manager.calls, [])

    def test_preflight_requires_all_units_compositor_and_supported_policy(self):
        for change in (lambda s: s["units"]["gamescope-session.target"].update(Job="7"),
                       lambda s: s["runtime"].update(compositors=[]),
                       lambda s: s["display_manager"].update(ActiveState="inactive"),
                       lambda s: s["units"]["gamescope-session.service"].update(PartOf=""),
                       lambda s: s["units"]["steamvr.service"].update(DropInPaths="/foreign.conf")):
            self.manager.current = snapshot()
            change(self.manager.current)
            with self.subTest(change=change), self.assertRaises(ValueError):
                session.preflight(self.home, self.runtime, self.manager)
        self.assertEqual(self.manager.calls, [])

    def test_prepare_refuses_existing_capture_configuration(self):
        config.write_entry(self.home, config.MANIFEST, config.describe(b"foreign", 0o600))
        with self.assertRaisesRegex(ValueError, "existing_capture"):
            self.prepare()
        self.assertEqual(config.payload(config.read_entry(self.home, config.MANIFEST)), b"foreign")

    def test_stable_new_full_session_succeeds_and_recovery_does_not_request_again(self):
        run = self.create()
        report = self.execute(run)
        self.assertEqual(report["status"], "session_restarted")
        self.assertTrue(report["session_changed"])
        self.assertGreaterEqual(self.clock, 2)
        self.assertEqual(self.recover(run)["status"], "runtime_observed")
        self.assertEqual(self.manager.calls, ["switch_to_game_mode"])
        result = session.result(run)
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["session_request_sent"])
        self.assertTrue(result["visual_confirmation_required"])
        self.assertEqual(session.latest(self.root / "runs"), result)

    def test_unchanged_or_only_steamvr_changed_is_not_a_successful_session_cycle(self):
        for partial in (False, True):
            self.manager = Manager()
            run = self.create()
            def incomplete_cycle():
                current = snapshot()
                if partial:
                    current["units"][trial.SERVICE] = snapshot(2)["units"][trial.SERVICE]
                self.manager.current = current
            self.manager.after_switch = incomplete_cycle
            self.assertEqual(self.execute(run)["reason"], "new_session_unconfirmed")
            self.assertEqual(self.recover(run)["status"], "runtime_observed")
            self.assertEqual(session.result(run)["status"], "failed")
            self.assertEqual(self.manager.calls, ["switch_to_game_mode"])

    def test_request_failure_with_new_healthy_session_remains_failed_and_is_not_retried(self):
        run = self.create()
        self.manager.failure = subprocess.TimeoutExpired(session.SWITCH, 10)
        report = self.execute(run)
        self.assertTrue(report["normal_runtime_observed"])
        self.assertEqual(report["request_status"], "unconfirmed")
        self.assertEqual(report["status"], "failed")
        self.recover(run)
        self.assertEqual(session.result(run)["status"], "failed")
        self.assertEqual(self.manager.calls, ["switch_to_game_mode"])

    def test_worker_interruption_leaves_armed_state_and_independent_observer(self):
        run = self.create()
        def interrupt():
            self.assertTrue(trial.read_json(run, "state.json")["session_request_sent"])
            raise KeyboardInterrupt()
        self.manager.after_switch = interrupt
        with self.assertRaises(KeyboardInterrupt):
            self.execute(run)
        self.assertEqual(self.recover(run)["status"], "runtime_observed")
        self.assertEqual(self.manager.calls, ["switch_to_game_mode"])
        self.assertEqual(session.result(run)["status"], "failed")
        self.assertTrue(session.result(run)["session_request_sent"])

    def test_baseline_drift_and_repeat_worker_cannot_issue_logout(self):
        run = self.create()
        self.manager.current = snapshot(2)
        self.assertEqual(self.execute(run)["reason"], "session_changed_before_request")
        self.assertEqual(self.recover(run)["status"], "not_needed")
        self.assertFalse(session.result(run)["session_request_sent"])
        self.assertEqual(self.manager.calls, [])
        self.manager = Manager()
        run = self.create()
        self.execute(run)
        self.assertEqual(self.execute(run)["reason"], "session_request_already_sent_or_invalid")
        self.assertEqual(self.manager.calls, ["switch_to_game_mode"])

    def test_churning_new_invocations_and_late_observation_cannot_pass(self):
        before, report = snapshot(), {}
        sequence = [2]
        def unstable(timeout=8):
            sequence[0] += 1
            return snapshot(sequence[0])
        with patch.object(self.manager, "snapshot", side_effect=unstable):
            self.assertFalse(self.observe(self.manager, before, report, lambda: None, seconds=4))
        self.assertLessEqual(self.clock, 4)
        self.clock = 0
        def late(timeout=8):
            self.clock += timeout + 1
            return snapshot(2)
        with patch.object(self.manager, "snapshot", side_effect=late):
            self.assertFalse(self.observe(self.manager, before, {}, lambda: None, seconds=1))

    def test_transient_read_failure_resets_stability_and_then_recovers(self):
        values = [snapshot(2), OSError("temporary bus failure"), snapshot(2), snapshot(2), snapshot(2), snapshot(2), snapshot(2)]
        def changing(timeout=8):
            value = values.pop(0) if values else snapshot(2)
            if isinstance(value, Exception):
                raise value
            return value
        with patch.object(self.manager, "snapshot", side_effect=changing):
            report = {}
            self.assertTrue(self.observe(self.manager, snapshot(), report, lambda: None, seconds=10))
        self.assertIn("temporary bus failure", report["last_observation_error"])
        self.assertGreaterEqual(self.clock, 3)

    def test_recovery_refuses_live_worker_and_never_issues_fallback_requests(self):
        run = self.create()
        self.manager.units[session.UNIT] = {"LoadState": "loaded", "MainPID": "123"}
        self.assertEqual(self.recover(run)["reason"], "session_worker_still_alive_or_unknown")
        self.manager.units.clear()
        state = trial.read_json(run, "state.json")
        state["session_request_sent"] = True
        trial.save(run, "state.json", state)
        self.manager.current["runtime"]["compositors"] = []
        self.assertEqual(self.recover(run)["reason"], "platform_recovery_unconfirmed")
        self.assertEqual(self.manager.calls, [])

    def test_foreign_configuration_after_request_is_preserved_and_fails_result(self):
        run = self.create()
        self.manager.after_switch = lambda: config.write_entry(self.home, config.MANIFEST, config.describe(b"foreign", 0o600))
        self.assertEqual(self.execute(run)["status"], "failed")
        self.assertEqual(self.recover(run)["status"], "failed")
        self.assertEqual(config.payload(config.read_entry(self.home, config.MANIFEST)), b"foreign")
        self.assertEqual(self.manager.calls, ["switch_to_game_mode"])

    def test_launch_command_uses_snapshots_and_app_slice_without_session_dependencies(self):
        run = self.create()
        command = session.launch_command(run)
        self.assertIn("--property=Slice=app.slice", command)
        self.assertIn("--property=RuntimeMaxSec=120", command)
        self.assertIn("--property=TimeoutStopSec=45", command)
        self.assertFalse(any("PartOf=" in item or "BindsTo=" in item for item in command))
        self.assertIn(str(run / "code/session_rehearsal.py"), command)
        self.assertTrue(any("--recover " + str(run) in item for item in command))

    def test_unit_parser_requires_complete_known_units_and_normalizes_idle_job(self):
        text = "\n\n".join("\n".join(f"{key}={value}" for key, value in dict(fields, Job="", Environment="PRIVATE").items())
                            for fields in snapshot()["units"].values())
        parsed = session.parse_units(text, session.UNITS)
        self.assertEqual(parsed[trial.SERVICE]["Job"], "0")
        self.assertNotIn("PRIVATE", str(parsed))
        for value in ("", text + "\n\nId=unknown", text + "\n\nId=steamvr.service"):
            with self.assertRaises(ValueError):
                session.parse_units(value, session.UNITS)

    def test_switch_executes_only_vendor_command_once_and_bounds_error_text(self):
        result = subprocess.CompletedProcess([], 1, "private stdout", "E" * 5000)
        with patch.object(session.subprocess, "run", return_value=result) as run, self.assertRaises(ValueError) as caught:
            session.Systemd().switch_to_game()
        self.assertEqual(run.call_args.args[0], ["/usr/bin/steamosctl", "switch-to-game-mode"])
        self.assertEqual(run.call_count, 1)
        self.assertEqual(len(caught.exception.details["stderr"]), 4096)
        self.assertNotIn("private", str(caught.exception.details))

    def test_read_only_report_does_not_query_systemd(self):
        run = self.create()
        self.execute(run)
        self.recover(run)
        with patch.object(session, "Systemd") as manager, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(session.main(["--report", str(run)]), 0)
        self.assertEqual(manager.return_value.method_calls, [])

    def test_start_refuses_missing_ssh_and_existing_other_trial_before_dummy_or_launch(self):
        bundle = self.prepare()
        for ssh, units in (("", {}), ("10.1.1.1 1234 10.1.1.2 22", {trial.UNIT: {"LoadState": "loaded"}})):
            self.manager.units = units
            with patch.object(session, "Systemd", return_value=self.manager), \
                 patch.dict(os.environ, {"SSH_CONNECTION": ssh}), \
                 patch.object(trial, "check_recovery") as dummy, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(session.main(["--start", str(bundle), "--allow-session-logout"]), 1)
            dummy.assert_not_called()
        self.assertEqual(self.manager.calls, [])

    def test_unconfirmed_launch_preserves_exact_run_without_retry(self):
        bundle = self.prepare()
        run = session.create_run(bundle, self.manager, self.root / "runs")
        output = io.StringIO()
        with patch.object(session, "Systemd", return_value=self.manager), \
             patch.dict(os.environ, {"SSH_CONNECTION": "10.1.1.1 1234 10.1.1.2 22"}), \
             patch.object(trial, "check_recovery", return_value={"status": "passed"}), \
             patch.object(session, "create_run", return_value=run), \
             patch.object(trial, "command", side_effect=subprocess.TimeoutExpired("systemd-run", 10)) as launch, \
             contextlib.redirect_stdout(output):
            self.assertEqual(session.main(["--start", str(bundle), "--allow-session-logout"]), 1)
        report = json.loads(output.getvalue())
        self.assertEqual(report["status"], "launch_unconfirmed")
        self.assertEqual(report["run_directory"], str(run))
        self.assertEqual(launch.call_count, 1)

    def test_worker_requires_own_systemd_service(self):
        with patch.object(session, "Systemd", return_value=self.manager), \
             patch.object(session, "execute") as execute, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(session.main(["--worker", str(self.root)]), 1)
        execute.assert_not_called()
