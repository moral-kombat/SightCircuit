"""Metadata activation/cleanup with generated sessions and private temporary files."""
import contextlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import metadata_session_trial as metadata
from tests.test_session_rehearsal import Manager as SessionManager, snapshot

session, trial, config = metadata.session, metadata.trial, metadata.config


class Manager(SessionManager):
    def __init__(self, runtime):
        super().__init__()
        self.runtime = runtime
        self.enabled = self.process_enabled = self.provider_alive = False
        self.generation = 1
        self.inspection_error = self.leak = self.keep_provider_alive = False
        self.fail_reload = False

    def reload(self, timeout=5):
        self.calls.append("reload")
        if self.fail_reload:
            self.fail_reload = False
            raise ValueError("generated_reload_error")
        self.enabled = config.read_entry(self.runtime, metadata.DROPIN) == metadata.ENABLE
        self.flags = ["SIGHTCIRCUIT_CAPTURE_LAYER"] if self.enabled else []

    def switch_to_game(self, timeout=10):
        self.calls.append("switch_to_game_mode")
        self.generation += 1
        self.current = snapshot(self.generation)
        self.process_enabled = self.provider_alive = self.enabled
        if self.enabled:
            self.current["units"][trial.SERVICE]["DropInPaths"] = str(self.runtime / metadata.DROPIN)
        if self.after_switch:
            self.after_switch()
        if self.failure:
            failure, self.failure = self.failure, None
            raise failure

    def compositor_flags(self, observed, enabled):
        if self.process_enabled != enabled:
            raise ValueError("unexpected_compositor_capture_environment")

    def mapped(self, peer, library):
        return self.process_enabled or self.leak

    def provider_identity(self, runtime, observed, library):
        if not self.process_enabled:
            return None
        peer = observed["runtime"]["compositors"][0]
        return {"pid": peer["pid"], "start": peer["start_ticks"], "device": 1, "inode": 2}

    def inspect(self, run, runtime, *, timeout=5):
        return {"status": "inspection_failed" if self.inspection_error else "inspected", "exit_code": 1 if self.inspection_error else 0,
            "cleanup_ok": True, "worker_reaped": True, "worker_forced_termination": False,
            "record_count": 0, "reviewed_profile_candidates": 0,
            "native": {"peer_pid": self.current["runtime"]["compositors"][0]["pid"], "peer_uid": os.geteuid(),
                "provider_epoch": "abcdef", "provider_generation": 165, "lease_established": True,
                "registry_unchanged": not self.inspection_error, "reason": "generated_failure" if self.inspection_error else ""}}

    def verify_peer(self, native, observed, library, owner):
        if not owner or native["peer_pid"] != owner["pid"]:
            raise ValueError("metadata_peer_not_trial_compositor")

    def clean_provider(self, runtime, owner):
        if self.provider_alive:
            raise ValueError("provider_alive")

    def provider_dead(self, owner):
        return not (self.provider_alive or self.keep_provider_alive)


class MetadataSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fj-metadata-session-")
        self.root = Path(self.temp.name)
        self.home, self.runtime = self.root / "home", self.root / "runtime"
        self.home.mkdir(mode=0o700)
        self.runtime.mkdir(mode=0o700)
        self.manager = Manager(self.runtime)
        self.clock = 0
        self.library = self.root / "source.so"
        self.library.write_bytes(b"\x7fELFsynthetic-metadata-library")
        self.inspector = self.root / "source-inspector"
        self.inspector.write_bytes(b"\x7fELFsynthetic-inspector")
        self.source = self.root / "metadata-source"
        self.source_plan = config.prepare(self.home, self.library, self.source)
        self.patches = [patch.object(Path, "home", return_value=self.home),
            patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(self.runtime)}),
            patch.object(session.time, "sleep", side_effect=self.pause),
            patch.object(trial, "verify_bundle", return_value=self.source_plan),
            patch.object(metadata.metadata, "BINARY", self.inspector)]
        for item in self.patches:
            item.start()
        session_bundle = self.root / "session-plan"
        session.prepare(session_bundle, self.home, self.runtime, self.manager)
        self.proof = session.create_run(session_bundle, self.manager, self.root / "proofs")
        trial.save(self.proof, "rehearsal.json", {"status": "session_restarted", "session_request_sent": True})
        trial.save(self.proof, "recovery.json", {"phase": "complete", "status": "runtime_observed", "normal_runtime_observed": True})
        self.bundle = self.root / "bundle"
        metadata.prepare(self.bundle, self.source, self.proof, self.manager)

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def pause(self, seconds):
        self.clock += seconds

    def observe(self, *args, **kwargs):
        return session.observe(*args, **kwargs, clock=lambda: self.clock, pause=self.pause)

    def create(self):
        return metadata.create_run(self.bundle, self.manager, self.root / "runs")

    def execute(self, run, **kwargs):
        return metadata.execute(run, self.manager, observer=kwargs.get("observer", self.observe))

    def recover(self, run, **kwargs):
        return metadata.recover(run, self.manager, observer=kwargs.get("observer", self.observe))

    def assert_disabled(self, run):
        self.assertIsNone(config.read_entry(self.home, config.MANIFEST))
        self.assertIsNone(config.read_entry(self.runtime, metadata.DROPIN))
        self.assertFalse((self.home / config.LIBRARY).exists())
        self.assertEqual((run / "library.so").read_bytes(), self.library.read_bytes())

    def test_default_and_missing_acknowledgment_do_not_query_runtime(self):
        for args, code in (([], 3), (["--start", str(self.bundle)], 1), (["--allow-session-logouts"], 1)):
            with patch.object(metadata, "Systemd") as manager, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(metadata.main(args), code)
            self.assertEqual(manager.return_value.method_calls, [])

    def test_preparation_is_inactive_and_requires_completed_matching_session_proof(self):
        self.assertEqual(list(self.home.iterdir()), [])
        self.assertEqual(list(self.runtime.iterdir()), [])
        self.assertEqual(self.manager.calls, [])
        self.assertTrue(metadata.load(self.bundle)["metadata_only"])
        trial.save(self.proof, "recovery.json", {"phase": "complete", "status": "failed"})
        with self.assertRaisesRegex(ValueError, "matching_completed_session_proof_required"):
            metadata.prepare(self.root / "other", self.source, self.proof, self.manager)
        self.assertFalse((self.root / "other").exists())

    def test_changed_helpers_binaries_platform_and_foreign_dropin_refuse_check(self):
        for relative in ("code/metadata_session_trial.py", "library.so", "inspector"):
            entry = config.read_entry(self.bundle, relative)
            config.write_entry(self.bundle, relative, config.describe(b"changed", entry["mode"]))
            with self.subTest(relative=relative), self.assertRaisesRegex(ValueError, "changed"):
                metadata.check(self.bundle, self.manager)
            config.write_entry(self.bundle, relative, entry)
        self.manager.contract["default_login_mode"] = "desktop"
        with self.assertRaisesRegex(ValueError, "platform_changed"):
            metadata.check(self.bundle, self.manager)
        self.manager.contract["default_login_mode"] = "game"
        config.write_entry(self.runtime, metadata.DROPIN, config.describe(b"foreign", 0o600))
        with self.assertRaisesRegex(ValueError, "existing_metadata_session_dropin"):
            metadata.check(self.bundle, self.manager)
        self.assertEqual(self.manager.calls, [])

    def test_manifest_uses_immutable_run_library_and_only_service_flag_enables_it(self):
        run = self.create()
        def inspect_stage():
            state = metadata.state_for(run)
            self.assertTrue(state["startup_may_be_enabled"])
            document = json.loads(config.payload(config.read_entry(self.home, config.MANIFEST)))
            self.assertEqual(document["layer"]["library_path"], str(run / "library.so"))
            self.assertEqual(document["layer"]["enable_environment"], {"SIGHTCIRCUIT_CAPTURE_LAYER": "1"})
            self.assertFalse((self.home / config.LIBRARY).exists())
        self.manager.after_switch = inspect_stage
        self.assertEqual(self.execute(run)["status"], "inspected")
        self.manager.after_switch = None
        self.assertEqual(self.recover(run)["status"], "restored")
        self.assert_disabled(run)

    def test_two_session_cycles_and_zero_metadata_candidates_are_a_valid_trial(self):
        run = self.create()
        self.assertEqual(self.execute(run)["record_count"], 0)
        report = self.recover(run)
        self.assertEqual(report["status"], "restored")
        self.assertTrue(report["layer_absent_from_compositor"])
        self.assertEqual(self.manager.calls, ["reload", "switch_to_game_mode", "reload", "switch_to_game_mode"])
        result = metadata.result(run)
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["activation_requested"])
        self.assertFalse(result["image_data_accessed"])
        self.assertEqual(metadata.latest(self.root / "runs"), result)

    def test_manifest_write_failure_recovers_without_any_session_request(self):
        run = self.create()
        original = config.write_entry
        def fail(root, relative, entry):
            if root == self.home and relative == config.MANIFEST and entry is not None:
                raise OSError("write_failed")
            return original(root, relative, entry)
        with patch.object(config, "write_entry", side_effect=fail):
            self.assertEqual(self.execute(run)["status"], "failed")
        self.assertEqual(self.recover(run)["status"], "restored")
        self.assertEqual(self.manager.calls, ["reload"])
        self.assert_disabled(run)

    def test_partial_enable_failure_is_disarmed_before_recovery_cycle(self):
        run = self.create()
        self.manager.fail_reload = True
        self.assertEqual(self.execute(run)["status"], "failed")
        self.assertEqual(self.recover(run)["status"], "restored")
        self.assertEqual(self.manager.calls, ["reload", "reload", "switch_to_game_mode"])
        self.assert_disabled(run)

    def test_worker_interruption_recovers_from_persisted_intent(self):
        run = self.create()
        def interrupt():
            self.assertTrue(metadata.state_for(run)["activation_requested"])
            raise KeyboardInterrupt()
        self.manager.after_switch = interrupt
        with self.assertRaises(KeyboardInterrupt):
            self.execute(run)
        self.manager.after_switch = None
        self.assertEqual(self.recover(run)["status"], "restored")
        self.assertEqual(self.manager.calls.count("switch_to_game_mode"), 2)
        self.assertEqual(metadata.result(run)["status"], "failed")
        self.assert_disabled(run)

    def test_metadata_inspection_failure_still_restores_and_keeps_overall_failure(self):
        run = self.create()
        self.manager.inspection_error = True
        self.assertEqual(self.execute(run)["reason"], "metadata_inspection_failed")
        self.assertEqual(self.recover(run)["status"], "restored")
        self.assertEqual(metadata.result(run)["status"], "failed")

    def test_foreign_manifest_or_dropin_is_preserved_while_other_own_target_is_removed(self):
        for foreign in ("manifest", "dropin"):
            self.manager = Manager(self.runtime)
            run = self.create()
            self.execute(run)
            root, relative = (self.home, config.MANIFEST) if foreign == "manifest" else (self.runtime, metadata.DROPIN)
            config.write_entry(root, relative, config.describe(b"foreign", 0o600))
            self.assertEqual(self.recover(run)["status"], "failed")
            self.assertEqual(config.payload(config.read_entry(root, relative)), b"foreign")
            other = (self.runtime, metadata.DROPIN) if foreign == "manifest" else (self.home, config.MANIFEST)
            self.assertIsNone(config.read_entry(*other))
            self.assertEqual(self.manager.calls.count("switch_to_game_mode"), 1)
            config.write_entry(root, relative, None)

    def test_recovery_disables_flags_even_if_frozen_binary_changes(self):
        run = self.create()
        self.execute(run)
        (run / "library.so").write_bytes(b"changed")
        self.assertEqual(self.recover(run)["reason"], "retained_trial_library_changed")
        self.assertIsNone(config.read_entry(self.home, config.MANIFEST))
        self.assertIsNone(config.read_entry(self.runtime, metadata.DROPIN))

    def test_changed_platform_disables_config_without_new_platform_request(self):
        run = self.create()
        self.execute(run)
        self.manager.contract["files"]["/example"] = "b" * 64
        self.assertEqual(self.recover(run)["reason"], "platform_changed_before_recovery_request")
        self.assertEqual(self.manager.calls.count("switch_to_game_mode"), 1)
        self.assert_disabled(run)

    def test_failed_cleanup_request_is_never_repeated_or_reclassified_as_success(self):
        run = self.create()
        self.execute(run)
        self.manager.failure = subprocess.TimeoutExpired("steamosctl", 10)
        self.assertEqual(self.recover(run)["status"], "failed")
        self.assertEqual(self.recover(run)["status"], "failed")
        self.assertEqual(self.manager.calls.count("switch_to_game_mode"), 2)
        self.assertTrue(metadata.result(run)["normal_runtime_observed"])
        self.assertEqual(metadata.result(run)["status"], "failed")

    def test_repeat_recovery_only_observes_and_never_deletes_library(self):
        run = self.create()
        self.execute(run)
        self.recover(run)
        self.assertEqual(self.recover(run)["status"], "restored")
        self.assertEqual(self.manager.calls.count("switch_to_game_mode"), 2)
        self.assert_disabled(run)

    def test_pending_session_prevents_competing_cleanup_request_after_disabling(self):
        run = self.create()
        self.execute(run)
        self.manager.current["units"][trial.SERVICE].update(ActiveState="activating", Job="7")
        self.assertEqual(self.recover(run)["reason"], "session_unready_for_recovery_request")
        self.assertEqual(self.manager.calls.count("switch_to_game_mode"), 1)
        self.assert_disabled(run)

    def test_old_provider_or_mapped_layer_prevents_restoration_claim(self):
        for field, reason in (("leak", "metadata_library_still_mapped_in_compositor"),
                              ("keep_provider_alive", "metadata_provider_still_alive")):
            self.manager = Manager(self.runtime)
            run = self.create()
            self.execute(run)
            setattr(self.manager, field, True)
            self.assertEqual(self.recover(run)["reason"], reason)
            self.assertEqual(metadata.result(run)["status"], "failed")

    def test_recovery_refuses_living_worker_and_prechange_abort_requires_no_actions(self):
        run = self.create()
        self.manager.units[metadata.UNIT] = {"LoadState": "loaded", "MainPID": "123"}
        self.assertEqual(self.recover(run)["reason"], "metadata_worker_still_alive_or_unknown")
        self.manager.units.clear()
        self.assertEqual(self.recover(run)["status"], "not_needed")
        self.assertEqual(self.manager.calls, [])

    def test_changed_baseline_refuses_before_visible_files(self):
        run = self.create()
        self.manager.current = snapshot(2)
        self.assertEqual(self.execute(run)["reason"], "metadata_trial_started_or_baseline_changed")
        self.assertEqual(list(self.home.iterdir()), [])
        self.assertEqual(self.manager.calls, [])

    def test_process_environment_only_checks_capture_flags_without_reporting_other_values(self):
        data = b"PRIVATE_TOKEN=never-report\0SIGHTCIRCUIT_CAPTURE_LAYER=1\0"
        observed = snapshot()
        with patch.object(metadata, "proc_bytes", return_value=data), patch.object(metadata, "process_start", return_value=100):
            metadata.Systemd().compositor_flags(observed, True)
            with self.assertRaisesRegex(ValueError, "unexpected_compositor_capture_environment") as error:
                metadata.Systemd().compositor_flags(observed, False)
        self.assertNotIn("never-report", str(error.exception))

    def test_mapping_uses_device_inode_and_checks_process_generation(self):
        info = self.library.stat()
        peer = {"pid": 201, "start_ticks": 100}
        mapping = f"1000-2000 r-xp 0000 {os.major(info.st_dev):x}:{os.minor(info.st_dev):x} {info.st_ino} /different-name\n".encode()
        with patch.object(metadata, "proc_bytes", return_value=mapping), patch.object(metadata, "process_start", return_value=100):
            self.assertTrue(metadata.Systemd().mapped(peer, self.library))
        with patch.object(metadata, "proc_bytes", return_value=b"1000-2000 r-xp 0000 00:00 0 /other\n"), \
             patch.object(metadata, "process_start", return_value=100):
            self.assertFalse(metadata.Systemd().mapped(peer, self.library))
        with patch.object(metadata, "proc_bytes", return_value=mapping), patch.object(metadata, "process_start", return_value=101), \
             self.assertRaisesRegex(ValueError, "compositor_identity_changed"):
            metadata.Systemd().mapped(peer, self.library)

    def test_provider_wait_allows_delayed_startup_and_bounds_missing_provider(self):
        owner = {"pid": 201, "start": 100, "device": 1, "inode": 2}
        with patch.object(self.manager, "provider_identity", side_effect=[None, FileNotFoundError(), owner]):
            self.assertEqual(metadata.wait_provider(self.manager, self.runtime, self.manager.current, self.library,
                clock=lambda: self.clock, pause=self.pause), owner)
        self.clock = 0
        with patch.object(self.manager, "provider_identity", return_value=None), self.assertRaisesRegex(ValueError, "startup_deadline"):
            metadata.wait_provider(self.manager, self.runtime, self.manager.current, self.library,
                seconds=0.3, clock=lambda: self.clock, pause=self.pause)
        self.assertLessEqual(self.clock, 0.3)

    def test_provider_wait_rejects_changed_session_instead_of_inspecting_a_different_peer(self):
        with patch.object(self.manager, "snapshot", return_value=snapshot(2)), \
             self.assertRaisesRegex(ValueError, "session_changed_while_waiting"):
            metadata.wait_provider(self.manager, self.runtime, snapshot(), self.library,
                clock=lambda: self.clock, pause=self.pause)

    def test_unidentified_and_replaced_provider_endpoints_are_preserved(self):
        directory = self.runtime / "sightcircuit-camera-provider"
        directory.mkdir(mode=0o700)
        endpoint = directory / "camera.sock"
        with socket.socket(socket.AF_UNIX) as server:
            server.bind(str(endpoint))
            with self.assertRaisesRegex(ValueError, "unidentified_provider"):
                metadata.Systemd().clean_provider(self.runtime, None)
            owner = {"pid": 1, "start": 2, "device": 3, "inode": 4}
            config.write_entry(directory, "provider.lock", config.describe(json.dumps(dict(owner, pid=99)).encode(), 0o600))
            with self.assertRaisesRegex(ValueError, "provider_lease_replaced"), patch.object(config, "recover_provider") as recover:
                metadata.Systemd().clean_provider(self.runtime, owner)
            recover.assert_not_called()
            self.assertTrue(endpoint.exists())

    def test_launch_and_worker_are_bounded_outside_session_slice(self):
        run = self.create()
        command = metadata.launch_command(run)
        self.assertIn("--property=Slice=app.slice", command)
        self.assertIn("--property=RuntimeMaxSec=150", command)
        self.assertIn("--property=TimeoutStopSec=180", command)
        self.assertTrue(any("--recover " + str(run) in arg for arg in command))
        self.assertFalse(any("PartOf=" in arg for arg in command))
        with patch.object(metadata, "Systemd", return_value=self.manager), patch.object(metadata, "execute") as execute, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(metadata.main(["--worker", str(run)]), 1)
        execute.assert_not_called()

    def test_report_is_incomplete_until_cleanup_and_does_not_query_systemd(self):
        run = self.create()
        self.assertEqual(metadata.result(run)["status"], "incomplete")
        self.execute(run)
        self.assertEqual(metadata.result(run)["status"], "incomplete")
        self.recover(run)
        with patch.object(metadata, "Systemd") as manager, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(metadata.main(["--report", str(run)]), 0)
        self.assertEqual(manager.return_value.method_calls, [])

    def test_start_refuses_missing_ssh_or_existing_trial_before_scheduling(self):
        for ssh, units in (("", {}), ("10.1.1.1 1234 10.1.1.2 22", {session.UNIT: {"LoadState": "loaded"}})):
            self.manager.units = units
            with patch.object(metadata, "Systemd", return_value=self.manager), \
                 patch.dict(os.environ, {"SSH_CONNECTION": ssh}), patch.object(trial, "check_recovery") as dummy, \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(metadata.main(["--start", str(self.bundle), "--allow-session-logouts"]), 1)
            dummy.assert_not_called()
        self.assertEqual(self.manager.calls, [])

    def test_unconfirmed_launch_records_run_and_never_retries(self):
        run = self.create()
        output = io.StringIO()
        with patch.object(metadata, "Systemd", return_value=self.manager), \
             patch.dict(os.environ, {"SSH_CONNECTION": "10.1.1.1 1234 10.1.1.2 22"}), \
             patch.object(trial, "check_recovery", return_value={"status": "passed"}), \
             patch.object(metadata, "create_run", return_value=run), \
             patch.object(trial, "command", side_effect=subprocess.TimeoutExpired("systemd-run", 10)) as launch, \
             contextlib.redirect_stdout(output):
            self.assertEqual(metadata.main(["--start", str(self.bundle), "--allow-session-logouts"]), 1)
        self.assertEqual(json.loads(output.getvalue())["status"], "launch_unconfirmed")
        self.assertEqual(json.loads(output.getvalue())["run_directory"], str(run))
        self.assertEqual(launch.call_count, 1)

    def settling_context(self):
        run = self.create()
        observed = self.manager.current
        self.manager.process_enabled = True
        owner = self.manager.provider_identity(self.runtime, observed, run / "library.so")
        return run, observed, owner

    def settled(self, run, observed, owner):
        return metadata.inspect_settled(run, self.manager, self.runtime, observed, owner,
            clock=lambda: self.clock, pause=self.pause)

    def changed_report(self, run):
        report = self.manager.inspect(run, self.runtime)
        report.update(status="inspection_failed", exit_code=1)
        report["native"].update(reason="provider_registry_changed", registry_unchanged=False)
        return report

    def test_registry_change_then_stable_preserves_both_attempts_and_original_requirement(self):
        run, observed, owner = self.settling_context()
        changed = self.changed_report(run)
        stable = self.manager.inspect(run, self.runtime)
        stable["native"]["provider_generation"] += 1
        with patch.object(self.manager, "inspect", side_effect=[changed, stable]) as inspect:
            result = self.settled(run, observed, owner)
        self.assertEqual(result["status"], "inspected")
        self.assertTrue(result["settling"]["registry_changes_observed"])
        self.assertEqual(result["settling"]["required_stable_seconds"], 2)
        self.assertEqual(inspect.call_count, 2)
        self.assertFalse(trial.read_json(run, "inspection-attempt-1.json")["native"]["registry_unchanged"])
        self.assertTrue(trial.read_json(run, "inspection-attempt-2.json")["native"]["registry_unchanged"])
        self.assertEqual(self.manager.calls, [])

    def test_persistent_registry_changes_stop_after_four_attempts(self):
        run, observed, owner = self.settling_context()
        with patch.object(self.manager, "inspect", return_value=self.changed_report(run)) as inspect:
            result = self.settled(run, observed, owner)
        self.assertEqual(inspect.call_count, 4)
        self.assertEqual(result["status"], "inspection_failed")
        self.assertEqual(result["reason"], "metadata_registry_did_not_settle")
        self.assertFalse(result["native"]["registry_unchanged"])
        self.assertLessEqual(self.clock, 12)

    def test_unrelated_failure_and_unclean_worker_are_not_retried(self):
        for change in ("reason", "cleanup", "forced"):
            run, observed, owner = self.settling_context()
            report = self.changed_report(run)
            if change == "reason":
                report["native"]["reason"] = "provider_disconnected"
            elif change == "cleanup":
                report["cleanup_ok"] = False
            else:
                report["worker_forced_termination"] = True
            with patch.object(self.manager, "inspect", return_value=report) as inspect:
                self.assertEqual(self.settled(run, observed, owner)["status"], "inspection_failed")
            self.assertEqual(inspect.call_count, 1)
            self.manager.process_enabled = False

    def test_settling_rejects_epoch_change_and_generation_regression(self):
        for epoch_change in (True, False):
            run, observed, owner = self.settling_context()
            changed = self.changed_report(run)
            stable = self.manager.inspect(run, self.runtime)
            stable["native"]["provider_epoch" if epoch_change else "provider_generation"] = "abceee" if epoch_change else 164
            with patch.object(self.manager, "inspect", side_effect=[changed, stable]):
                result = self.settled(run, observed, owner)
            self.assertEqual(result["reason"], "provider_generation_changed_during_settling")
            self.assertEqual(result["status"], "inspection_failed")
            self.manager.process_enabled = False

    def test_settling_rejects_replaced_provider_before_another_inspection(self):
        run, observed, owner = self.settling_context()
        with patch.object(self.manager, "provider_identity", side_effect=[owner, dict(owner, inode=99)]), \
             patch.object(self.manager, "inspect", return_value=self.changed_report(run)) as inspect:
            result = self.settled(run, observed, owner)
        self.assertEqual(result["reason"], "provider_identity_changed_during_settling")
        self.assertEqual(inspect.call_count, 1)

    def test_settling_rejects_changed_session_before_another_inspection(self):
        run, observed, owner = self.settling_context()
        with patch.object(self.manager, "snapshot", side_effect=[observed, snapshot(2)]), \
             patch.object(self.manager, "inspect", return_value=self.changed_report(run)) as inspect:
            result = self.settled(run, observed, owner)
        self.assertEqual(result["reason"], "session_changed_during_settling")
        self.assertEqual(inspect.call_count, 1)

    def test_late_stable_result_cannot_pass_settling_deadline(self):
        run, observed, owner = self.settling_context()
        stable = self.manager.inspect(run, self.runtime)
        def late(*args, **kwargs):
            self.clock += 13
            return stable
        with patch.object(self.manager, "inspect", side_effect=late):
            result = self.settled(run, observed, owner)
        self.assertEqual(result["status"], "inspection_failed")
        self.assertEqual(result["reason"], "metadata_settling_deadline")

    def test_settling_does_not_shorten_stability_to_fit_remaining_time(self):
        run, observed, owner = self.settling_context()
        changed = self.changed_report(run)
        timeouts = []
        def slow(*args, **kwargs):
            timeouts.append(kwargs["timeout"])
            self.clock += 4
            return changed
        with patch.object(self.manager, "inspect", side_effect=slow):
            result = self.settled(run, observed, owner)
        self.assertEqual(timeouts, [4, 4])
        self.assertEqual(result["reason"], "metadata_settling_deadline")
        self.assertLessEqual(self.clock, 12)
