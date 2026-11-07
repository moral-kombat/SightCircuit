"""Frame agent runner, task table, guard hook and settings template; no device access."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from sightcircuit.agent import guard, runner
from sightcircuit.agent.tasks import TASKS, TASKS_BY_ID, TIER_A, Task

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ROOT / "sightcircuit" / "agent" / "frame-settings.json"


def quiet(function, *args, **kwargs):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = function(*args, **kwargs)
    return code, out.getvalue(), err.getvalue()


def fake(task_id, code="", timeout=30, **extra):
    return Task(id=task_id, tier="A", description=task_id, argv=(sys.executable, "-c", code),
                timeout=timeout, **extra)


def alive(pid):
    """True while ``pid`` exists and is not a zombie."""
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


def wait_dead(pid, seconds=5):
    deadline = time.monotonic() + seconds
    while alive(pid):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)
    return True


# Starts a child in the task's own process group, writes its pid to argv[1], then either
# exits at once ("exit") or sleeps. The child ignores SIGTERM when argv[2] is "ignore".
CHILD_CODE = ("import signal, subprocess, sys, time; "
              "child = 'import signal, time; ' + ('signal.signal(signal.SIGTERM, signal.SIG_IGN); ' "
              "if sys.argv[2] == 'ignore' else '') + 'time.sleep(120)'; "
              "p = subprocess.Popen([sys.executable, '-c', child]); open(sys.argv[1], 'w').write(str(p.pid)); "
              "time.sleep(0.5); sys.exit(0) if sys.argv[3] == 'exit' else time.sleep(120)")


# Runs runner.main on one task, 'tier-a/long', in its own process so that signals reach a real runner.
# argv: checkout, run root, pid file, task code, then the task's own arguments. ``{prelude}`` runs first.
DRIVER = ("import os, signal, subprocess, sys; sys.path.insert(0, sys.argv[1]); "
          "from sightcircuit.agent import runner; from sightcircuit.agent.tasks import Task\n"
          "{prelude}\n"
          "task = Task(id='tier-a/long', tier='A', description='long', timeout=100, "
          "argv=(sys.executable, '-c', sys.argv[4], *sys.argv[5:]))\n"
          "sys.exit(runner.main(['run', 'tier-a/long'], root=sys.argv[2], tasks_by_id={{task.id: task}}))")
# A task that writes its pid to argv[1], optionally ignores SIGTERM (argv[2] == "ignore"), then sleeps argv[3] s.
SLEEPER = ("import os, signal, sys, time\n"
           "if sys.argv[2] == 'ignore': signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
           "open(sys.argv[1], 'w').write(str(os.getpid())); time.sleep(float(sys.argv[3]))")


class TaskTableTests(unittest.TestCase):
    def test_ids_are_unique_and_tiered(self):
        self.assertEqual(len(TASKS_BY_ID), len(TASKS))
        for task in TASKS:
            with self.subTest(task=task.id):
                self.assertTrue(task.id.startswith(f"tier-{task.tier.lower()}/"))
                self.assertEqual(task.runnable, task.tier == "A")
                if task.runnable:
                    self.assertGreater(task.timeout, 0)
                    self.assertTrue(task.argv)
                else:
                    self.assertTrue(task.refusal)
                    self.assertEqual(task.argv, ())

    def test_tier_a_commands_exist_and_carry_no_opt_ins(self):
        forbidden = ("--allow-", "--start", "--rollback", "--recover", "--capture", "--overlay",
                     "camera_probe", "session_rehearsal", "metadata_session_trial")
        for task in TIER_A:
            with self.subTest(task=task.id):
                joined = " ".join(task.argv)
                self.assertFalse([word for word in forbidden if word in joined])
                scripts = [part for part in task.argv if part.startswith("scripts/")]
                self.assertEqual(len(scripts), 0 if task.id == "tier-a/unit-tests" else 1, joined)
                for script in scripts:
                    self.assertTrue((ROOT / script).is_file(), script)
                for path in task.requires:
                    self.assertFalse(Path(path).is_absolute())

    def test_no_task_touches_the_dsp_steamvr_cv_drivers_or_the_sensor_sidecar(self):
        # docs/on-device.md "Preflight advice from another Frame project": never a runner task.
        forbidden = ("dsp_service", "/dev/cdsp", "/dev/adsp", "drivers/cv/bin", "steamvr.service",
                     "sample_run_context")
        for task in TASKS:
            text = " ".join((*task.argv, *task.requires, *task.env.values(), task.description))
            with self.subTest(task=task.id):
                self.assertFalse([word for word in forbidden if word in text])

    def test_frozen_track_scripts_appear_only_in_read_only_modes(self):
        allowed = {"scripts/capture_layer_trial.py": {"--runtime-status"},
                   "scripts/service_rehearsal.py": {"--preflight", "--report-latest"}}
        for task in TIER_A:
            for script, modes in allowed.items():
                if script in task.argv:
                    with self.subTest(task=task.id):
                        self.assertEqual(task.argv[task.argv.index(script) + 1:], (next(
                            mode for mode in modes if mode in task.argv),))

    def test_setup_runs_first_and_stops_the_run_on_failure(self):
        self.assertEqual(TIER_A[0].id, "tier-a/setup")
        self.assertTrue(TIER_A[0].stop_on_failure)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def run_main(self, argv, tasks, **kwargs):
        table = {task.id: task for task in tasks}
        return quiet(runner.main, argv, root=self.root, tasks_by_id=table,
                     tier_a=tuple(task for task in tasks if task.runnable), **kwargs)

    def result(self):
        return json.loads(runner.latest_result(self.root).read_text())

    def test_unknown_disabled_and_duplicate_tasks_are_refused_without_a_run(self):
        for argv, reason in ((["run", "tier-a/nope"], "unknown task"),
                             (["run", "tier-b/mirror"], "needs a person wearing"),
                             (["run", "tier-c/camera-probe"], "never run by an agent"),
                             (["run", "tier-a/inventory", "tier-a/inventory"], "twice")):
            with self.subTest(argv=argv):
                code, _, err = quiet(runner.main, argv, root=self.root)
                self.assertEqual(code, 2)
                self.assertIn(reason, err)
        self.assertFalse(list((self.root / runner.RUNS).glob("*/result.json")))

    def test_extra_arguments_and_unknown_tiers_are_usage_errors(self):
        for argv in (["run-tier", "tier-c"], ["report", "all"], ["list", "--allow-x"], ["run"], []):
            with self.subTest(argv=argv):
                self.assertEqual(quiet(runner.main, argv, root=self.root)[0], 2)

    def test_list_reports_every_task(self):
        code, out, _ = quiet(runner.main, ["list"], root=self.root)
        self.assertEqual(code, 0)
        self.assertEqual([entry["id"] for entry in json.loads(out)], [task.id for task in TASKS])

    def test_run_records_output_exit_codes_and_provenance(self):
        tasks = [fake("tier-a/ok", "print('hello')"), fake("tier-a/bad", "import sys; sys.exit(4)")]
        code, out, _ = self.run_main(["run", "tier-a/ok", "tier-a/bad"], tasks)
        self.assertEqual(code, 1)
        result = self.result()
        self.assertEqual([t["status"] for t in result["tasks"]], ["passed", "failed"])
        self.assertEqual(result["tasks"][1]["exit_code"], 4)
        self.assertIn("agent-run device evidence", result["provenance"])
        self.assertFalse(result["all_passed"])
        run_dir = self.root / json.loads(out)["run_dir"]
        self.assertEqual((run_dir / "tier-a--ok" / "stdout.txt").read_text(), "hello\n")

    def test_placeholders_and_task_environment_are_applied(self):
        code_text = ("import os, sys; open(sys.argv[1] + '/marker', 'w').write(os.environ['FJ_TEST'] + ' ' + "
                     "sys.argv[2])")
        task = Task(id="tier-a/env", tier="A", description="env", timeout=30, env={"FJ_TEST": "yes"},
                    argv=(sys.executable, "-c", code_text, "{run_dir}", "{venv_python}"))
        self.assertEqual(self.run_main(["run", "tier-a/env"], [task])[0], 0)
        marker = next((self.root / runner.RUNS).glob("*/tier-a--env/marker")).read_text()
        self.assertEqual(marker, f"yes {self.root / '.venv-replay/bin/python'}")

    def test_missing_prerequisite_skips_and_fails_the_run(self):
        task = fake("tier-a/needs", "pass", requires=(".venv-replay/bin/python",))
        self.assertEqual(self.run_main(["run", "tier-a/needs"], [task])[0], 1)
        record = self.result()["tasks"][0]
        self.assertEqual(record["status"], "skipped_precondition")
        self.assertEqual(record["missing"], [".venv-replay/bin/python"])

    def test_timeout_kills_the_whole_process_group(self):
        child_pid = self.root / "child.pid"
        code_text = ("import subprocess, sys, time; p = subprocess.Popen([sys.executable, '-c', "
                     "'import time; time.sleep(60)']); open(sys.argv[1], 'w').write(str(p.pid)); time.sleep(60)")
        task = Task(id="tier-a/slow", tier="A", description="slow", timeout=2,
                    argv=(sys.executable, "-c", code_text, str(child_pid)))
        started = time.monotonic()
        self.assertEqual(self.run_main(["run", "tier-a/slow"], [task])[0], 1)
        self.assertLess(time.monotonic() - started, 20)
        self.assertEqual(self.result()["tasks"][0]["status"], "timed_out")
        pid = int(child_pid.read_text())
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            if Path(f"/proc/{pid}/stat").read_text().split()[2] == "Z":
                break
            time.sleep(0.1)
        else:
            self.fail("grandchild survived the timeout")

    def test_timeout_kills_group_members_that_ignore_sigterm(self):
        child_pid = self.root / "child.pid"
        task = Task(id="tier-a/stubborn", tier="A", description="stubborn", timeout=2,
                    argv=(sys.executable, "-c", CHILD_CODE, str(child_pid), "ignore", "sleep"))
        with mock.patch.object(runner, "KILL_GRACE", 1):
            code = self.run_main(["run", "tier-a/stubborn"], [task])[0]
        pid = int(child_pid.read_text())
        self.addCleanup(lambda: alive(pid) and os.kill(pid, 9))
        self.assertEqual(code, 1)
        self.assertEqual(self.result()["tasks"][0]["status"], "timed_out")
        self.assertFalse(alive(pid), "a group member that ignores SIGTERM survived the timeout")

    def test_processes_left_running_after_exit_are_killed_and_fail_the_task(self):
        child_pid = self.root / "child.pid"
        task = Task(id="tier-a/leaky", tier="A", description="leaky", timeout=30,
                    argv=(sys.executable, "-c", CHILD_CODE, str(child_pid), "default", "exit"))
        with mock.patch.object(runner, "KILL_GRACE", 1):
            code = self.run_main(["run", "tier-a/leaky"], [task])[0]
        pid = int(child_pid.read_text())
        self.addCleanup(lambda: alive(pid) and os.kill(pid, 9))
        self.assertEqual(code, 1)
        record = self.result()["tasks"][0]
        self.assertFalse(alive(pid), "a background child outlived its task")
        self.assertEqual((record["status"], record["exit_code"]), ("failed", 0))
        self.assertIn("left processes running", record["error"])

    def test_result_records_the_running_task_before_it_finishes(self):
        code_text = ("import json, pathlib, sys; "
                     "print(json.loads((pathlib.Path(sys.argv[1]).parent / 'result.json').read_text())"
                     "['tasks'][-1]['status'])")
        task = Task(id="tier-a/peek", tier="A", description="peek", timeout=30,
                    argv=(sys.executable, "-c", code_text, "{run_dir}"))
        self.assertEqual(self.run_main(["run", "tier-a/peek"], [task])[0], 0)
        stdout = next((self.root / runner.RUNS).glob("*/tier-a--peek/stdout.txt")).read_text()
        self.assertEqual(stdout, "running\n")

    def test_a_signal_to_the_runner_terminates_the_task_and_records_the_interruption(self):
        driver = ("import sys; sys.path.insert(0, sys.argv[1]); "
                  "from sightcircuit.agent import runner; from sightcircuit.agent.tasks import Task; "
                  "task = Task(id='tier-a/long', tier='A', description='long', timeout=100, argv=(sys.executable, "
                  "'-c', 'import os, sys, time; open(sys.argv[1], \"w\").write(str(os.getpid())); time.sleep(100)', "
                  "sys.argv[3])); "
                  "sys.exit(runner.main(['run', 'tier-a/long'], root=sys.argv[2], tasks_by_id={task.id: task}))")
        for number in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            with self.subTest(signal=number.name):
                root = self.root / number.name
                task_pid = root / "task.pid"
                process = subprocess.Popen([sys.executable, "-c", driver, str(ROOT), str(root), str(task_pid)],
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                for _ in range(200):
                    if task_pid.exists() and task_pid.read_text():
                        break
                    time.sleep(0.05)
                pid = int(task_pid.read_text())
                self.addCleanup(lambda pid=pid: alive(pid) and os.kill(pid, 9))
                process.send_signal(number)
                out, err = process.communicate(timeout=30)
                self.assertEqual(process.returncode, 128 + number, err)
                self.assertFalse(alive(pid), "the task kept running after the runner was signalled")
                result = json.loads(runner.latest_result(root).read_text())
                self.assertEqual(result["interrupted"], number.name)
                self.assertEqual(result["tasks"][0]["status"], "interrupted")
                self.assertFalse(result["all_passed"])
                self.assertEqual(json.loads(out)["interrupted"], number.name)

    def start_driver(self, name, task_code, *task_args, prelude=""):
        """The driver process, its run root and the file where the task's pid appears ("{pid}" in ``task_args``)."""
        root = self.root / name
        task_pid = root / "task.pid"
        task_args = [str(task_pid) if arg == "{pid}" else arg for arg in task_args]
        process = subprocess.Popen([sys.executable, "-c", DRIVER.format(prelude=prelude), str(ROOT), str(root),
                                    str(task_pid), task_code, *task_args],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        return process, root, task_pid

    def task_pid(self, path):
        for _ in range(200):
            if path.exists() and path.read_text():
                break
            time.sleep(0.05)
        pid = int(path.read_text())
        self.addCleanup(lambda: alive(pid) and os.kill(pid, 9))
        return pid

    def test_a_signal_while_the_task_starts_still_terminates_it(self):
        # The signal reaches the runner after the task exists but before Popen has returned its process object.
        prelude = ("real = subprocess.Popen\n"
                   "class Starting(real):\n"
                   "    def __init__(self, args, *rest, **kwargs):\n"
                   "        super().__init__(args, *rest, **kwargs)\n"
                   "        if args[-1] == 'injected':\n"
                   "            open(sys.argv[3], 'w').write(str(self.pid))\n"
                   "            os.kill(os.getpid(), signal.SIGTERM)\n"
                   "subprocess.Popen = Starting")
        process, root, task_pid = self.start_driver("starting", "import time; time.sleep(100)", "injected",
                                                    prelude=prelude)
        out, err = process.communicate(timeout=60)
        pid = self.task_pid(task_pid)
        self.assertEqual(process.returncode, 128 + signal.SIGTERM, err)
        self.assertTrue(wait_dead(pid, 1), "the task kept running after a signal that came while it started")
        result = json.loads(runner.latest_result(root).read_text())
        self.assertEqual(result["interrupted"], "SIGTERM")
        self.assertEqual(result["tasks"][0]["status"], "interrupted")
        self.assertIsNotNone(result["tasks"][0]["exit_code"])

    def test_a_second_signal_does_not_cut_short_the_termination(self):
        process, root, task_pid = self.start_driver("twice", SLEEPER, "{pid}", "ignore", "100",
                                                    prelude="runner.KILL_GRACE = 3")
        pid = self.task_pid(task_pid)
        process.send_signal(signal.SIGTERM)  # The task ignores it, so the runner waits out the grace period ...
        time.sleep(0.5)
        process.send_signal(signal.SIGHUP)  # ... and a second signal must not stop it before SIGKILL.
        _, err = process.communicate(timeout=60)
        self.assertEqual(process.returncode, 128 + signal.SIGTERM, err)
        self.assertTrue(wait_dead(pid, 1), "a second signal let a task that ignores SIGTERM outlive the runner")
        self.assertEqual(json.loads(runner.latest_result(root).read_text())["interrupted"], "SIGTERM")

    def test_a_signal_ignored_when_the_runner_starts_stays_ignored(self):
        # nohup starts the runner with SIGHUP ignored; a hangup must not abort the run.
        process, root, task_pid = self.start_driver("nohup", SLEEPER, "{pid}", "default", "1.5",
                                                    prelude="signal.signal(signal.SIGHUP, signal.SIG_IGN)")
        self.task_pid(task_pid)
        process.send_signal(signal.SIGHUP)
        _, err = process.communicate(timeout=60)
        self.assertEqual(process.returncode, 0, err)
        result = json.loads(runner.latest_result(root).read_text())
        self.assertNotIn("interrupted", result)
        self.assertEqual(result["tasks"][0]["status"], "passed")

    def test_failed_setup_stops_later_tasks(self):
        tasks = [fake("tier-a/setup", "import sys; sys.exit(1)", stop_on_failure=True),
                 fake("tier-a/after", "pass")]
        self.assertEqual(self.run_main(["run-tier", "tier-a"], tasks)[0], 1)
        statuses = [t["status"] for t in self.result()["tasks"]]
        self.assertEqual(statuses, ["failed", "not_started"])

    def test_budget_bounds_the_whole_run(self):
        tasks = [fake("tier-a/one", "import time; time.sleep(1.5)", timeout=60), fake("tier-a/two", "pass")]
        self.assertEqual(self.run_main(["run-tier", "tier-a"], tasks, budget=1)[0], 1)
        records = self.result()["tasks"]
        self.assertEqual(records[0]["status"], "timed_out")
        self.assertEqual(records[0]["timeout_s"], 1)
        self.assertEqual(records[1], {"id": "tier-a/two", "status": "not_started",
                                      "reason": "run budget exhausted"})

    def test_a_held_lock_refuses_a_second_run(self):
        with runner.RunLock(self.root):
            code, _, err = self.run_main(["run", "tier-a/ok"], [fake("tier-a/ok", "pass")])
        self.assertEqual(code, 3)
        self.assertIn("locked", err)
        self.assertEqual(self.run_main(["run", "tier-a/ok"], [fake("tier-a/ok", "pass")])[0], 0)

    def test_report_latest(self):
        self.assertEqual(quiet(runner.main, ["report", "latest"], root=self.root)[0], 2)
        self.run_main(["run", "tier-a/ok"], [fake("tier-a/ok", "pass")])
        code, out, _ = quiet(runner.main, ["report", "latest"], root=self.root)
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["all_passed"])

    def test_report_latest_is_the_newest_runner_made_run(self):
        tasks = [fake("tier-a/zz-first", "pass"), fake("tier-a/aa-second", "import sys; sys.exit(1)")]
        stamp = "20261102T120000Z"
        with mock.patch.object(runner, "datetime") as clock:  # Both runs start in the same UTC second.
            clock.now.return_value.strftime.return_value = stamp
            clock.now.return_value.isoformat.return_value = "2026-11-02T12:00:00+00:00"
            for task in tasks:
                self.run_main(["run", task.id], tasks)
        forged = self.root / runner.RUNS / "zz-forged"
        forged.mkdir()
        (forged / "result.json").write_text('{"all_passed": true}\n')
        os.utime(forged / "result.json", ns=(time.time_ns() + 10**9,) * 2)
        result = json.loads(runner.latest_result(self.root).read_text())
        self.assertEqual(result["label"], "tier-a--aa-second")
        self.assertFalse(result["all_passed"])

    def test_checkout_state_is_unknown_outside_a_git_checkout(self):
        self.assertEqual(runner.checkout_state(self.root), {"revision": None, "clean": None, "branch": None})


@unittest.skipUnless(shutil.which("git"), "git is required")
class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
               "GIT_COMMITTER_EMAIL": "t@example.invalid", "GIT_CONFIG_GLOBAL": os.devnull,
               "GIT_CONFIG_NOSYSTEM": "1"}
        self.env = dict(os.environ, **env)
        self.origin, self.clone = self.base / "origin", self.base / "clone"
        self.git(self.base, "init", "--quiet", "-b", "main", str(self.origin))
        (self.origin / "a").write_text("0")
        (self.origin / ".gitignore").write_text("/artifacts/\n")
        self.git(self.origin, "add", "a", ".gitignore")
        self.git(self.origin, "commit", "--quiet", "-m", "before the agent runner")
        self.pre_agent = self.git(self.origin, "rev-parse", "HEAD")
        self.write_agent(guard.GUARD_VERSION)
        (self.origin / "a").write_text("1")
        self.git(self.origin, "add", "a", "sightcircuit", "scripts")
        self.git(self.origin, "commit", "--quiet", "-m", "one")
        self.first = self.git(self.origin, "rev-parse", "HEAD")
        self.git(self.base, "clone", "--quiet", str(self.origin), str(self.clone))
        (self.origin / "a").write_text("2")
        self.git(self.origin, "commit", "--quiet", "-am", "two")
        self.second = self.git(self.origin, "rev-parse", "HEAD")
        self.write_agent(guard.GUARD_VERSION - 1)
        self.git(self.origin, "commit", "--quiet", "-am", "weaker guard")
        self.weaker = self.git(self.origin, "rev-parse", "HEAD")
        self.git(self.origin, "switch", "--quiet", "-c", "side", self.second)
        (self.origin / "a").write_text("3")
        self.git(self.origin, "commit", "--quiet", "-am", "unmerged")
        self.unmerged = self.git(self.origin, "rev-parse", "HEAD")
        self.git(self.origin, "switch", "--quiet", "main")
        self.enterContext(mock.patch.dict(os.environ, env))

    def git(self, cwd, *args):
        return subprocess.run(["git", *args], cwd=cwd, env=self.env, check=True, capture_output=True,
                              text=True).stdout.strip()

    def write_agent(self, version):
        for path in runner.AGENT_FILES:
            (self.origin / path).parent.mkdir(parents=True, exist_ok=True)
            (self.origin / path).write_text(f"GUARD_VERSION = {version}\n" if path.endswith("guard.py") else "")

    def update(self, revision):
        return quiet(runner.main, ["update", revision], root=self.clone)

    def test_detaches_at_a_fetched_main_commit(self):
        code, out, _ = self.update(self.second)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.git(self.clone, "rev-parse", "HEAD"), self.second)
        self.assertEqual(json.loads(out)["checkout"]["branch"], "HEAD")

    def test_refuses_short_unmerged_or_dirty(self):
        self.git(self.clone, "fetch", "--quiet", "origin", "side")
        self.assertEqual(self.update("--allow-x")[0], 2)
        for revision, reason in ((self.second[:12], "40-character"), ("A" * 40, "40-character"),
                                 (self.unmerged, "not on origin/main"),
                                 (self.pre_agent, "has no sightcircuit/agent/guard.py"),
                                 (self.weaker, f"older than version {guard.GUARD_VERSION}")):
            with self.subTest(revision=revision):
                code, _, err = self.update(revision)
                self.assertEqual(code, 2)
                self.assertIn(reason, err)
        (self.clone / "a").write_text("local")
        code, _, err = self.update(self.second)
        self.assertEqual(code, 2)
        self.assertIn("local changes", err)
        self.assertEqual(self.git(self.clone, "rev-parse", "HEAD"), self.first)

    def test_a_failing_git_status_refuses_instead_of_counting_as_clean(self):
        (self.clone / ".gitignore").write_text("/artifacts/\nlocal\n")  # A change git switch would carry along.
        self.git(self.clone, "config", "status.showUntrackedFiles", "bogus")  # git status now exits 128.
        code, _, err = self.update(self.second)
        self.assertEqual(code, 2, err)
        self.assertIn("git status failed", err)
        self.assertEqual(self.git(self.clone, "rev-parse", "HEAD"), self.first)

    def test_a_fetch_timeout_refuses_and_ends_the_transport(self):
        marker = f"61.{os.getpid()}"  # A unique sleep, so its process can be found.
        self.git(self.clone, "config", "remote.origin.uploadpack", f"sleep {marker}; git-upload-pack")

        def transport():
            found = []
            for entry in Path("/proc").glob("[0-9]*"):
                with contextlib.suppress(OSError):
                    if f"sleep {marker}".encode() in (entry / "cmdline").read_bytes().replace(b"\0", b" "):
                        found.append(entry.name)
            return found

        def kill_leftovers():
            for pid in transport():
                with contextlib.suppress(OSError):
                    os.kill(int(pid), signal.SIGKILL)
        self.addCleanup(kill_leftovers)
        started = time.monotonic()
        with mock.patch.object(runner, "FETCH_TIMEOUT", 2), mock.patch.object(runner, "KILL_GRACE", 2):
            code, _, err = self.update(self.second)
        self.assertLess(time.monotonic() - started, 20)
        self.assertEqual(code, 2, err)
        self.assertIn("git fetch timed out after 2s", err)
        self.assertEqual(self.git(self.clone, "rev-parse", "HEAD"), self.first)
        deadline = time.monotonic() + 5
        while transport() and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertEqual(transport(), [], "git's transport outlived the timed-out fetch")


class GuardTests(unittest.TestCase):
    root = Path("/srv/sightcircuit")

    def decide(self, tool, **tool_input):
        return guard.decide({"tool_name": tool, "tool_input": tool_input}, self.root)

    def test_allows_only_plain_runner_invocations(self):
        for command in ("python3 scripts/frame_agent_run.py list",
                        "python3 scripts/frame_agent_run.py run tier-a/inventory tier-a/unit-tests",
                        "python3 scripts/frame_agent_run.py run-tier tier-a",
                        "python3 scripts/frame_agent_run.py update " + "a" * 40,
                        "  python3   scripts/frame_agent_run.py report latest"):
            with self.subTest(command=command):
                self.assertEqual(self.decide("Bash", command=command), (0, ""))

    def test_blocks_everything_else(self):
        cases = {
            "python3 scripts/frame_agent_run.py list; rm -rf ~": "shell syntax",
            "python3 scripts/frame_agent_run.py list && reboot": "system changes",
            "python3 scripts/frame_agent_run.py list | tee x": "shell syntax",
            "python3 scripts/frame_agent_run.py run $(cat x)": "shell syntax",
            "python3 scripts/frame_agent_run.py run `id`": "shell syntax",
            "python3 scripts/frame_agent_run.py list > x": "shell syntax",
            "python3 scripts/frame_agent_run.py list\nid": "shell syntax",
            "python3 scripts/frame_agent_run.py run 'tier-a/x y'": "shell syntax",
            "python3 scripts/frame_agent_run.py run tier-a/*": "shell syntax",
            "python3 scripts/frame_agent_run.py run --budget=1": "plain task IDs",
            "FOO=1 python3 scripts/frame_agent_run.py list": "only allowed command",
            "python3 scripts/device_check.py": "only allowed command",
            "python scripts/frame_agent_run.py list": "only allowed command",
            "bash scripts/frame_session.sh mirror": "only allowed command",
            "python3 scripts/session_rehearsal.py --start x --allow-session-logout": "--allow-",
            "python3 scripts/session_rehearsal.py --preflight": "log out",
            "python3 scripts/service_rehearsal.py --start": "stopped the session",
            "python3 scripts/capture_layer_trial.py --rollback x": "capture layer",
            "artifacts/camera-probe/camera_probe --allow-passthrough-interruption": "--allow-",
            "./camera_probe": "camera probe",
            "systemctl --user restart steamvr.service": "system changes",
            "sudo steamos-readonly disable": "system changes",
            "steamosctl switch-to-game-mode": "system changes",
            "git push origin main": "does not push",
            "": "empty",
        }
        for command, reason in cases.items():
            with self.subTest(command=command):
                code, message = self.decide("Bash", command=command)
                self.assertEqual(code, 2)
                self.assertIn(reason, message)

    def test_writes_are_confined_to_a_run_summary(self):
        root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        runs = root / "artifacts/agent-runs"
        (runs / "20261102T120000Z-tier-a/tier-a--inventory").mkdir(parents=True)
        for name in ("r1", "zz-forged", "20261102T120000Z-tier-a\n"):
            (runs / name).mkdir()

        def decide(tool, **tool_input):
            return guard.decide({"tool_name": tool, "tool_input": tool_input}, root)
        run = "artifacts/agent-runs/20261102T120000Z-tier-a"
        self.assertEqual(decide("Write", file_path=str(root / run / "summary.md"), content="x"), (0, ""))
        self.assertEqual(decide("Edit", file_path=f"{run}/summary.md"), (0, ""))
        for path in (str(root / "docs/status.md"), str(root / "artifacts/agent-runs/../x"),
                     str(root / ".claude/settings.local.json"), "/home/steamos/.bashrc",
                     "artifacts/agent-runs-evil/x", "", None,
                     # The runner's own output is evidence, not agent-writable.
                     f"{run}/result.json", "artifacts/agent-runs/zz-forged/result.json",
                     f"{run}/tier-a--inventory/stdout.txt", "artifacts/agent-runs/.lock",
                     "artifacts/agent-runs/summary.md", f"{run}/tier-a--inventory/summary.md",
                     # Only a run directory the runner made: a summary there must not create the lock path as
                     # a directory, start a run directory, or use a name the runner never makes.
                     "artifacts/agent-runs/.lock/summary.md", "artifacts/agent-runs/20261102T130000Z-tier-a/summary.md",
                     "artifacts/agent-runs/r1/summary.md", "artifacts/agent-runs/zz-forged/summary.md",
                     "artifacts/agent-runs/20261102T120000Z-tier-a\n/summary.md",
                     # Claude Code trims and expands "~"; the guard refuses such paths instead of guessing.
                     f" {run}/summary.md", f"{run}/summary.md ", f"~/{run}/summary.md"):
            with self.subTest(path=path):
                self.assertEqual(decide("Write", file_path=path)[0], 2)
        self.assertEqual(decide("NotebookEdit", notebook_path=str(root / "x.ipynb"))[0], 2)
        self.assertFalse((runs / ".lock").exists())

    def test_reads_stay_in_the_checkout_and_away_from_room_images(self):
        for tool, tool_input in (("Read", {"file_path": str(self.root / "docs/status.md")}),
                                 ("Read", {"file_path": "artifacts/agent-runs/r1/result.json"}),
                                 ("Read", {"file_path": "docs/a file with spaces.md"}),
                                 ("Grep", {"pattern": "passed"}), ("Glob", {"pattern": "**/*.json"}),
                                 ("Grep", {"pattern": "x", "path": "artifacts/agent-runs"}),
                                 ("Glob", {"pattern": "docs/*.md"}), ("Glob", {"pattern": f"{self.root}/docs/*.md"}),
                                 ("Glob", {"pattern": "*.json", "path": "artifacts/agent-runs"})):
            with self.subTest(tool=tool, tool_input=tool_input):
                self.assertEqual(self.decide(tool, **tool_input), (0, ""))
        for tool, tool_input, reason in (
                ("Read", {"file_path": "/etc/hostname"}, "outside the checkout"),
                ("Read", {"file_path": "/home/steamos/.claude/.credentials.json"}, "outside the checkout"),
                ("Read", {"file_path": str(self.root / "../.git-credentials")}, "outside the checkout"),
                ("Grep", {"pattern": "token", "path": "/home/steamos"}, "outside the checkout"),
                ("Read", {"file_path": "evaluation/local/frame.json"}, "stay on Frame"),
                ("Glob", {"pattern": "*", "path": "evaluation/local"}, "stay on Frame"),
                ("Read", {"file_path": "recordings/x.json"}, "stay on Frame"),
                ("Read", {"file_path": "artifacts/frame-session/1/window-in-mirror.png"}, "images and videos"),
                ("Read", {"file_path": "artifacts/agent-runs/r1/still.JPG"}, "images and videos"),
                ("Read", {}, "missing"),
                # Claude Code trims a Grep/Glob path and expands "~" and "~/" before searching, but passes the raw
                # string to the hook; the guard refuses those spellings instead of resolving them differently.
                ("Grep", {"pattern": "x", "path": " evaluation/local", "output_mode": "content"}, "unresolvable"),
                ("Grep", {"pattern": "x", "path": "evaluation/local ", "output_mode": "content"}, "unresolvable"),
                ("Grep", {"pattern": "x", "path": "evaluation/local\t"}, "unresolvable"),
                ("Grep", {"pattern": "x", "path": "\ufeffevaluation/local"}, "unresolvable"),
                ("Grep", {"pattern": "key", "path": "~"}, "unresolvable"),
                ("Grep", {"pattern": "key", "path": "~/.ssh"}, "unresolvable"),
                ("Glob", {"pattern": "*", "path": " recordings"}, "unresolvable"),
                ("Glob", {"pattern": "*", "path": "~/.claude"}, "unresolvable"),
                ("Read", {"file_path": "~/.ssh/id_ed25519"}, "unresolvable"),
                ("Read", {"file_path": " evaluation/local/frame.json"}, "unresolvable"),
                # Glob searches the leading directories of an absolute pattern; a relative pattern's leading
                # directories are inside the searched directory.
                ("Glob", {"pattern": "/home/steamos/.ssh/*"}, "outside the checkout"),
                ("Glob", {"pattern": "/*"}, "outside the checkout"),
                ("Glob", {"pattern": f"{self.root}/evaluation/local/*"}, "stay on Frame"),
                ("Glob", {"pattern": "evaluation/local/*"}, "stay on Frame"),
                ("Glob", {"pattern": "local/*.json", "path": "evaluation"}, "stay on Frame"),
                ("Glob", {"pattern": "recordings/**"}, "stay on Frame"),
                ("Glob", {"pattern": "*", "path": "docs/../evaluation/local"}, "stay on Frame"),
                ("Glob", {"pattern": "../../*"}, "'..'"),
                ("Glob", {"pattern": "docs/*/../../../x/*"}, "'..'"),
                ("Glob", {"pattern": "~/.ssh/*"}, "'~'"),
                ("Glob", {"pattern": " /etc/*"}, "'~'"),
                ("Glob", {}, "missing")):
            with self.subTest(tool=tool, tool_input=tool_input):
                code, message = self.decide(tool, **tool_input)
                self.assertEqual(code, 2)
                self.assertIn(reason, message)

    def test_monitor_commands_get_the_bash_check(self):
        self.assertEqual(self.decide("Monitor", command="python3 scripts/frame_agent_run.py list"), (0, ""))
        for command in ("id; curl http://x", "python3 scripts/frame_agent_run.py list > scripts/x.py"):
            with self.subTest(command=command):
                self.assertEqual(self.decide("Monitor", command=command)[0], 2)

    def test_unresolvable_paths_block_instead_of_crashing(self):
        for tool, key in (("Write", "file_path"), ("Read", "file_path"), ("NotebookEdit", "notebook_path")):
            for path in (str(self.root / "scripts/x\x00.py"), str(self.root / "scripts/x\ud800.py"),
                         str(self.root / "artifacts/agent-runs/r1/\ud800/summary.md")):
                with self.subTest(tool=tool, path=ascii(path)):
                    self.assertEqual(self.decide(tool, **{key: path})[0], 2)

    def test_any_guard_error_blocks(self):
        with mock.patch.object(guard, "decide", side_effect=RuntimeError("boom")):
            code, _, err = quiet(guard.main, io.StringIO('{"tool_name": "Read", "tool_input": {}}'), self.root)
        self.assertEqual(code, 2)
        self.assertIn("guard error (RuntimeError)", err)

    def test_symlink_escape_is_blocked(self):
        # Each path below is allowed by name; only resolving the symlink shows where it leads.
        root, outside = (Path(tempfile.mkdtemp()).resolve() for _ in range(2))
        for path in (root, outside):
            self.addCleanup(shutil.rmtree, path, ignore_errors=True)
        (root / "docs").mkdir()
        (root / "artifacts/agent-runs").mkdir(parents=True)
        (root / "artifacts/agent-runs/20261102T120000Z-tier-a").symlink_to(root / "docs")  # A summary lands in docs/.
        (outside / "secret.txt").write_text("secret")
        (root / "notes.txt").symlink_to(outside / "secret.txt")
        (root / "notes-dir").symlink_to(outside)
        for tool, key, path in (("Write", "file_path", "artifacts/agent-runs/20261102T120000Z-tier-a/summary.md"),
                                ("Read", "file_path", "notes.txt"), ("Grep", "path", "notes-dir"),
                                ("Glob", "path", "notes-dir")):
            with self.subTest(tool=tool, path=path):
                self.assertEqual(guard.decide({"tool_name": tool, "tool_input": {key: path, "pattern": "*"}},
                                              root)[0], 2)

    def test_network_tools_and_malformed_input_are_blocked(self):
        self.assertEqual(self.decide("WebFetch", url="https://example.com")[0], 2)
        for payload in (None, [], {"tool_name": "Bash"}, {"tool_input": {}}):
            with self.subTest(payload=payload):
                self.assertEqual(guard.decide(payload, self.root)[0], 2)

    def test_hook_script_reads_stdin_and_exits_two_with_a_reason(self):
        script = ROOT / "scripts" / "frame_agent_guard.py"
        nested = '{"tool_name": "Write", "tool_input": {"file_path": ' + "[" * 100000 + "]" * 100000 + "}}"
        for payload, expected in (({"tool_name": "Bash", "tool_input": {"command": "id"}}, 2),
                                  ({"tool_name": "Bash", "tool_input": {
                                      "command": "python3 scripts/frame_agent_run.py list"}}, 0),
                                  ({"tool_name": "Write", "tool_input": {
                                      "file_path": str(ROOT / "scripts/x\x00.py")}}, 2),
                                  ({"tool_name": "Write", "tool_input": {
                                      "file_path": str(ROOT / "scripts/x\ud800.py")}}, 2),
                                  (nested, 2), ("not json", 2)):
            with self.subTest(payload=payload):
                text = payload if isinstance(payload, str) else json.dumps(payload)
                result = subprocess.run([sys.executable, str(script)], input=text, capture_output=True,
                                        text=True, timeout=30, env=dict(os.environ, CLAUDE_PROJECT_DIR=str(ROOT)))
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertEqual(bool(result.stderr.strip()), expected == 2)


class SettingsTemplateTests(unittest.TestCase):
    def test_template_allows_only_the_runner_and_wires_the_guard(self):
        settings = json.loads(SETTINGS.read_text())
        permissions = settings["permissions"]
        self.assertEqual(permissions["defaultMode"], "dontAsk")
        bash_rules = [rule for rule in permissions["allow"] if rule.startswith("Bash")]
        self.assertEqual(bash_rules, ["Bash(python3 scripts/frame_agent_run.py:*)"])
        for rule in ("Bash(sudo:*)", "Bash(systemctl:*)", "Bash(steamosctl:*)", "WebFetch"):
            self.assertIn(rule, permissions["deny"])
        hooks = settings["hooks"]["PreToolUse"]
        self.assertEqual(len(hooks), 1)
        self.assertEqual(set(hooks[0]["matcher"].split("|")),
                         guard.SHELL_TOOLS | guard.READ_TOOLS | guard.WRITE_TOOLS | guard.BLOCKED_TOOLS)
        command = hooks[0]["hooks"][0]["command"]
        self.assertIn("scripts/frame_agent_guard.py", command)
        self.assertTrue((ROOT / "scripts/frame_agent_guard.py").is_file())

    def test_template_confines_reads_and_writes(self):
        permissions = json.loads(SETTINGS.read_text())["permissions"]
        # Reads inside the checkout need no rule; a bare Read/Grep/Glob would allow the whole home directory.
        self.assertFalse({"Read", "Grep", "Glob"} & set(permissions["allow"]))
        self.assertEqual(sorted(rule for rule in permissions["allow"] if not rule.startswith("Bash")),
                         ["Edit(artifacts/agent-runs/*/summary.md)", "Write(artifacts/agent-runs/*/summary.md)"])
        for rule in ("Monitor", "Read(evaluation/local/**)", "Read(recordings/**)", "Read(**/*.png)",
                     "Read(**/*.jpg)", "Read(~/.ssh/**)", "Read(~/.claude/**)"):
            self.assertIn(rule, permissions["deny"])

    def test_hook_command_blocks_when_the_guard_cannot_run(self):
        command = json.loads(SETTINGS.read_text())["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        (base / "bin").mkdir()
        (base / "bin/python3").symlink_to(sys.executable)
        broken = base / "broken"
        (broken / "scripts").mkdir(parents=True)
        (broken / "scripts/frame_agent_guard.py").write_text("raise ImportError('broken after an update')\n")
        allowed = json.dumps({"tool_name": "Bash", "tool_input": {"command": "python3 scripts/frame_agent_run.py list"}})
        for project, path, expected in ((ROOT, f"{base / 'bin'}:/usr/bin:/bin", 0),
                                        (broken, f"{base / 'bin'}:/usr/bin:/bin", 2),  # exit 1 from a traceback
                                        (ROOT, str(base / "empty"), 2)):  # no python3: exit 127
            with self.subTest(project=project, path=path):
                result = subprocess.run(["/bin/sh", "-c", command], input=allowed, capture_output=True, text=True,
                                        timeout=30, env={"PATH": path, "CLAUDE_PROJECT_DIR": str(project)})
                self.assertEqual(result.returncode, expected, result.stderr)

    def test_local_settings_stay_out_of_git(self):
        self.assertIn("/.claude/settings.local.json", (ROOT / ".gitignore").read_text().splitlines())


if __name__ == "__main__":
    unittest.main()
