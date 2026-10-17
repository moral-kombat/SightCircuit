"""Generated cgroup/proc files and virtual time; no manager, driver or camera."""
import copy
from pathlib import Path
import tempfile
import unittest

from scripts import capture_service_lifecycle as lifecycle


GROUP = "/user.slice/steamvr.service"


class ServiceObservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fj-service-observation-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cgroups, self.proc = self.root / "cgroups", self.root / "proc"
        self.cgroups.mkdir()
        self.proc.mkdir()
        (self.cgroups / "cgroup.controllers").write_text("cpu memory pids\n")
        self.directory = self.cgroups / GROUP.lstrip("/")
        self.directory.mkdir(parents=True)
        (self.directory / "cgroup.events").write_text("populated 1\nfrozen 0\n")
        (self.directory / "cgroup.procs").write_text("44\n")
        self.service = {"ActiveState": "active", "SubState": "running", "MainPID": "33",
                        "ControlGroup": GROUP, "Job": "0", "Result": "success", "InvocationID": "a" * 32}
        self.process(44)

    def process(self, pid, *, name="vrcompositor", group=GROUP, state="S", ticks=123):
        root = self.proc / str(pid)
        root.mkdir(exist_ok=True)
        fields = [state, *(["0"] * 18), str(ticks), "0", "0"]
        (root / "stat").write_text(f"{pid} (name with ) spaces) " + " ".join(fields) + "\n")
        (root / "cgroup").write_text(f"0::{group}\n")
        executable = root / "exe"
        if executable.is_symlink():
            executable.unlink()
        executable.symlink_to("/vendor/runtime/" + name)

    def observe(self):
        return lifecycle.observe(self.service, GROUP, cgroup_root=self.cgroups, proc_root=self.proc)

    def test_nested_cgroup_and_compositor_identity_are_observed_without_arguments(self):
        child = self.directory / "child"
        child.mkdir()
        (child / "cgroup.procs").write_text("77\n")
        self.process(77, group=GROUP + "/child", ticks=456)
        (self.proc / "44" / "cmdline").write_text("SECRET=never-read")
        result = self.observe()
        self.assertTrue(lifecycle.running(result))
        self.assertEqual(result["compositors"], [{"pid": 44, "start_ticks": 123}, {"pid": 77, "start_ticks": 456}])
        self.assertNotIn("SECRET", str(result))

    def test_missing_waiter_with_live_descendants_is_not_a_stopped_runtime(self):
        self.service.update(ActiveState="inactive", SubState="dead", MainPID="0")
        self.assertFalse(lifecycle.stopped(self.observe()))
        (self.directory / "cgroup.events").write_text("populated 0\n")
        self.assertTrue(lifecycle.stopped(self.observe()))

    def test_removed_cgroup_is_empty_but_missing_events_and_unknown_hierarchy_are_not(self):
        self.service.update(ActiveState="inactive", SubState="dead", MainPID="0", ControlGroup="")
        (self.directory / "cgroup.events").unlink()
        with self.assertRaisesRegex(ValueError, "events_missing"):
            self.observe()
        (self.directory / "cgroup.procs").unlink()
        self.directory.rmdir()
        self.assertTrue(lifecycle.stopped(self.observe()))
        (self.cgroups / "cgroup.controllers").unlink()
        with self.assertRaisesRegex(ValueError, "unified_cgroup_unavailable"):
            self.observe()

    def test_pending_job_or_failed_result_prevents_a_readiness_claim(self):
        for key, value in (("Job", "88"), ("Result", "exit-code"), ("InvocationID", ""), ("MainPID", "0")):
            with self.subTest(field=key):
                original = self.service[key]
                self.service[key] = value
                self.assertFalse(lifecycle.running(self.observe()))
                self.service[key] = original
        self.service.update(ActiveState="inactive", MainPID="0", Job="88")
        (self.directory / "cgroup.events").write_text("populated 0\n")
        self.assertFalse(lifecycle.stopped(self.observe()))

    def test_wrong_process_group_waiter_and_nonlive_compositors_are_not_ready(self):
        for change in ({"group": GROUP + "-other"}, {"name": "vrcmd"}, {"state": "Z"}, {"state": "T"}):
            with self.subTest(change=change):
                self.process(44, **change)
                self.assertFalse(lifecycle.running(self.observe()))
        (self.proc / "44" / "exe").unlink()
        self.assertFalse(lifecycle.running(self.observe()))

    def test_changed_group_invalid_paths_and_malformed_or_oversized_metadata_are_refused(self):
        self.service["ControlGroup"] = "/another.service"
        with self.assertRaisesRegex(ValueError, "group_changed"):
            self.observe()
        self.service["ControlGroup"] = GROUP
        for group in ("/", "/user.slice/../other", "relative"):
            with self.subTest(group=group), self.assertRaises(ValueError):
                lifecycle.observe(self.service, group, cgroup_root=self.cgroups, proc_root=self.proc)
        for data in ("populated unknown\n", "x" * 65537):
            (self.directory / "cgroup.events").write_text(data)
            with self.assertRaises(ValueError):
                self.observe()


class ServiceDeadlineTests(unittest.TestCase):
    def test_shared_deadline_bounds_both_commands_and_retains_unconfirmed_state(self):
        now, commands, snapshots = [0.0], [], []
        observation = {"service": {"ActiveState": "deactivating", "MainPID": "0", "Job": "77"},
                       "populated": True, "compositors": []}
        class Manager:
            def action(self, action, timeout):
                commands.append((action, timeout))
            def observe(self, group, timeout):
                return copy.deepcopy(observation)
        def pause(seconds):
            now[0] += seconds
        controller = lifecycle.Controller(Manager(), GROUP, lambda history: snapshots.append(copy.deepcopy(history)),
                                          budget=0.35, clock=lambda: now[0], pause=pause)
        self.assertFalse(controller.move("stop")["confirmed"])
        self.assertFalse(controller.move("start")["confirmed"])
        self.assertEqual(len(commands), 1)
        self.assertLessEqual(now[0], 0.35)
        self.assertEqual(snapshots[0][0]["command_status"], "requested")
        self.assertEqual(snapshots[-1][0]["last_observation"], observation)

    def test_observation_failure_does_not_become_an_empty_group(self):
        class Manager:
            def action(self, action, timeout):
                pass
            def observe(self, group, timeout):
                raise PermissionError("cgroup unavailable")
        controller = lifecycle.Controller(Manager(), GROUP)
        outcome = controller.move("stop")
        self.assertFalse(outcome["confirmed"])
        self.assertIn("observation_error", outcome)

    def test_observation_returned_after_deadline_does_not_confirm_shutdown(self):
        now = [0.0]
        class Manager:
            def action(self, action, timeout):
                pass
            def observe(self, group, timeout):
                now[0] += timeout
                return {"service": {"ActiveState": "inactive", "MainPID": "0", "Job": "0"},
                        "compositors": [], "populated": False}
        controller = lifecycle.Controller(Manager(), GROUP, budget=0.2, clock=lambda: now[0], pause=lambda _: None)
        self.assertFalse(controller.move("stop")["confirmed"])
        self.assertEqual(now[0], 0.2)
