"""Frame agent runner, task table, guard hook and settings template; no device access."""
import contextlib
import errno
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


def wait_for(condition, seconds=60):
    """Poll ``condition`` until it is true or a generous deadline passes (under full-suite load); its last value."""
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.02)
    return condition()


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
# A task that writes its pid to argv[1], then sleeps argv[3] s. With argv[2] == "ignore" it ignores SIGTERM; with
# "note" it keeps running too, but creates argv[1] + ".term" when SIGTERM arrives.
SLEEPER = ("import os, signal, sys, time\n"
           "if sys.argv[2] == 'ignore': signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
           "if sys.argv[2] == 'note': signal.signal(signal.SIGTERM, lambda *_: open(sys.argv[1] + '.term', 'w').close())\n"
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
        wait_for(lambda: path.exists() and path.read_text())
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
        process, root, task_pid = self.start_driver("twice", SLEEPER, "{pid}", "note", "100",
                                                    prelude="runner.KILL_GRACE = 5")
        pid = self.task_pid(task_pid)
        process.send_signal(signal.SIGTERM)  # The task only notes it, so the runner waits out the grace period ...
        # ... which has begun once the runner's SIGTERM reached the task (not after a fixed delay) ...
        self.assertTrue(wait_for(lambda: Path(f"{task_pid}.term").exists() or process.poll() is not None))
        self.assertTrue(alive(pid), "the task did not outlive SIGTERM, so the grace period was not tested")
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

    def test_checkout_state_keeps_the_fields_whose_git_succeeded(self):
        def fake_git(root, *args, interruption=None):
            if args[0] == "status":
                raise subprocess.TimeoutExpired("git status", 30)
            return 0, "abc123" if args == ("rev-parse", "HEAD") else "main", ""
        with unittest.mock.patch.object(runner, "git", side_effect=fake_git):
            state = runner.checkout_state(self.root)
        self.assertEqual(state, {"revision": "abc123", "clean": None, "branch": "main"})

    def test_a_git_file_that_names_no_gitdir_leaves_the_lock_question_unknown(self):
        (self.root / ".git").write_text("GITDIR: elsewhere\n")
        self.assertIn("is unknown", runner.index_lock_left(self.root))


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


# Stands in for git in "update": a clean status, then a fetch that runs until it is ended; anything else fails, so
# update stops at merge-base. With FAKE_GIT_GUARD set, every step of update succeeds instead (show prints that
# GUARD_VERSION) and the git that runs until it is ended is the first one after the switch, "rev-parse HEAD", which is
# also the first git a run starts. Each call's arguments go to $FAKE_GIT_PID.calls. The blocking git's pid (which is
# also its process group, as the runner starts git in its own session) goes to $FAKE_GIT_PID once its SIGTERM trap is
# set. FAKE_GIT=clean behaves like git, which removes its lock files on SIGTERM; FAKE_GIT=stubborn holds
# .git/index.lock, appends to $FAKE_GIT_PID.log on SIGTERM and keeps running; FAKE_GIT=pass exits 0 once
# $FAKE_GIT_PID.release exists; FAKE_GIT=fail instead prints an error and exits 128 at once, taking no lock. What each
# call could prompt with (GIT_TERMINAL_PROMPT, stdin, its pid and session id) goes to $FAKE_GIT_PID.env.
FAKE_GIT = """#!/bin/sh
session() { stat=$(cat /proc/$$/stat); set -- ${stat##*") "}; echo "$4"; }
echo "prompt=${GIT_TERMINAL_PROMPT-unset} stdin=$(readlink /proc/$$/fd/0) pid=$$ sid=$(session)" >> "$FAKE_GIT_PID.env"
echo "$*" >> "$FAKE_GIT_PID.calls"
if [ -n "${FAKE_GIT_GUARD:-}" ]; then
  case "$*" in
    "rev-parse HEAD") ;;
    "show "*) echo "GUARD_VERSION = $FAKE_GIT_GUARD"; exit 0 ;;
    *) exit 0 ;;
  esac
else
  case "$1" in
    status) exit 0 ;;
    fetch) ;;
    *) exit 1 ;;
  esac
fi
if [ "$FAKE_GIT" = fail ]; then echo "fatal: cannot read HEAD" >&2; exit 128; fi
started() { echo $$ > "$FAKE_GIT_PID.tmp" && mv "$FAKE_GIT_PID.tmp" "$FAKE_GIT_PID"; }
if [ "$FAKE_GIT" = pass ]; then
  started
  while [ ! -e "$FAKE_GIT_PID.release" ]; do sleep 0.1; done
  exit 0
fi
: > .git/index.lock
if [ "$FAKE_GIT" = clean ]; then trap 'rm -f .git/index.lock; exit 143' TERM
else trap 'echo TERM >> "$FAKE_GIT_PID.log"' TERM; fi
started
while :; do sleep 1; done
"""
# Runs runner.main(["update", <a full hash>]) in its own process, so that signals reach a real runner.
# argv: checkout, checkout root. ``{prelude}`` runs first.
UPDATE_DRIVER = ("import os, signal, subprocess, sys; sys.path.insert(0, sys.argv[1]); "
                 "from sightcircuit.agent import runner\n"
                 "{prelude}\n"
                 "sys.exit(runner.main(['update', 'a' * 40], root=sys.argv[2]))")


class UpdateInterruptionTests(unittest.TestCase):
    """A timed-out or interrupted git, in ``update`` or a run's checkout record, is ended with its whole group (SIGTERM,
    then SIGKILL) before the lock is released; git has no terminal to prompt on. A checkout record that fails or times
    out after ``update``'s switch refuses the update."""

    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        (self.base / "bin").mkdir()
        (self.base / "bin/git").write_text(FAKE_GIT)
        (self.base / "bin/git").chmod(0o755)

    def checkout(self, name, mode, switched=False):
        """A checkout root with a .git directory, and the environment that puts the fake git first on PATH. With
        ``switched``, update gets through the switch and git blocks in the first checkout record."""
        root = self.base / name
        (root / ".git").mkdir(parents=True)
        pid_file = self.base / f"{name}.pid"
        env = {"PATH": f"{self.base / 'bin'}:{os.environ.get('PATH', '/usr/bin:/bin')}", "FAKE_GIT": mode,
               "FAKE_GIT_PID": str(pid_file), **({"FAKE_GIT_GUARD": str(guard.GUARD_VERSION)} if switched else {})}
        self.addCleanup(self.end_group, pid_file)
        return root, pid_file, env

    @staticmethod
    def end_group(pid_file):
        with contextlib.suppress(OSError, ValueError):
            os.killpg(int(pid_file.read_text()), signal.SIGKILL)

    def git_group(self, pid_file, process=None):
        """The fake git's process group, once it runs (and while ``process``, the driver, has not exited)."""
        wait_for(lambda: pid_file.exists() or (process is not None and process.poll() is not None))
        if not pid_file.exists():
            self.fail(f"the fake git did not start: {process.communicate(timeout=30) if process else ''}")
        return int(pid_file.read_text())

    def driver(self, root, env, prelude="", stdin=None):
        process = subprocess.Popen([sys.executable, "-c", UPDATE_DRIVER.format(prelude=prelude), str(ROOT), str(root)],
                                   stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   env=dict(os.environ, **env))
        self.addCleanup(lambda: process.poll() is None and process.kill())
        return process

    def run_driver(self, root, env, task_pid, prelude=""):
        """A run of one task, SLEEPER, which writes ``task_pid`` once it has started."""
        process = subprocess.Popen([sys.executable, "-c", DRIVER.format(prelude=prelude), str(ROOT), str(root),
                                    str(task_pid), SLEEPER, str(task_pid), "default", "100"],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   env=dict(os.environ, **env))
        self.addCleanup(lambda: process.poll() is None and process.kill())
        return process

    def lock_waits_for(self, root, group):
        """Take the run lock as soon as it is free: git's group must be gone by then. True if git ran while it was held."""
        held_while_running = False
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                with runner.RunLock(root):
                    self.assertFalse(runner.group_alive(group), "the run lock was released while git was still running")
                    return held_while_running
            except BlockingIOError:
                held_while_running = held_while_running or runner.group_alive(group)
            time.sleep(0.02)
        self.fail("the run lock was never released")

    @staticmethod
    def report(err):
        return json.loads(err.strip().splitlines()[-1])

    def test_a_signal_during_the_fetch_ends_git_before_the_lock_is_released(self):
        for number in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            with self.subTest(signal=number.name):
                root, pid_file, env = self.checkout(number.name, "stubborn")
                process = self.driver(root, env, prelude="runner.GIT_TERM_GRACE = 2")
                group = self.git_group(pid_file, process)
                process.send_signal(number)
                self.assertTrue(self.lock_waits_for(root, group), "git ended before it could be seen holding the lock")
                _, err = process.communicate(timeout=60)
                self.assertEqual(process.returncode, 128 + number, err)
                report = self.report(err)
                self.assertEqual((report["status"], report["signal"]), ("interrupted", number.name))
                self.assertTrue(Path(f"{pid_file}.log").exists(), "git got SIGKILL without SIGTERM first")
                # The stubborn git was killed holding its lock: reported for a person, never deleted.
                self.assertIn(".git/index.lock", report["reason"])
                self.assertTrue((root / ".git/index.lock").exists())

    def test_a_signal_after_the_switch_is_reported_and_ends_git_before_the_lock_is_released(self):
        # The checkout has moved, so a signal from here on is still reported as an interruption, never as "updated".
        for number in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            with self.subTest(signal=number.name):
                root, pid_file, env = self.checkout(f"switched-{number.name}", "stubborn", switched=True)
                process = self.driver(root, env, prelude="runner.GIT_TERM_GRACE = 2")
                group = self.git_group(pid_file, process)
                calls = Path(f"{pid_file}.calls").read_text().splitlines()
                self.assertEqual(calls[-2:], [f"switch --quiet --detach {'a' * 40}", "rev-parse HEAD"])
                process.send_signal(number)
                self.assertTrue(self.lock_waits_for(root, group), "git ended before it could be seen holding the lock")
                out, err = process.communicate(timeout=60)
                self.assertEqual(process.returncode, 128 + number, out + err)
                self.assertEqual(out, "", "an interrupted update printed an outcome")
                report = self.report(err)
                self.assertEqual((report["status"], report["signal"]), ("interrupted", number.name))
                self.assertIn(f"update interrupted by {number.name}; any running git was ended", report["reason"])
                self.assertTrue(Path(f"{pid_file}.log").exists(), "git got SIGKILL without SIGTERM first")

    def test_a_signal_while_a_run_records_the_checkout_ends_git_and_records_the_interruption(self):
        # A run records the checkout with git, under the run lock, before its first task.
        root, pid_file, env = self.checkout("run", "stubborn", switched=True)
        task_pid = self.base / "run-task.pid"
        process = self.run_driver(root, env, task_pid, prelude="runner.GIT_TERM_GRACE = 2")
        group = self.git_group(pid_file, process)
        process.send_signal(signal.SIGTERM)
        self.assertTrue(self.lock_waits_for(root, group), "git ended before it could be seen holding the lock")
        _, err = process.communicate(timeout=60)
        self.assertEqual(process.returncode, 128 + signal.SIGTERM, err)
        self.assertTrue(Path(f"{pid_file}.log").exists(), "git got SIGKILL without SIGTERM first")
        result = json.loads(runner.latest_result(root).read_text())
        self.assertEqual(result["interrupted"], "SIGTERM")
        self.assertEqual(result["checkout"], {"revision": None, "clean": None, "branch": None})
        self.assertEqual(result["tasks"], [{"id": "tier-a/long", "status": "not_started",
                                            "reason": "run interrupted by SIGTERM"}])
        self.assertFalse(task_pid.exists(), "the task started after the run was interrupted")

    def test_a_second_signal_does_not_cut_short_ending_git(self):
        root, pid_file, env = self.checkout("twice", "stubborn")
        process = self.driver(root, env, prelude="runner.GIT_TERM_GRACE = 5")
        group = self.git_group(pid_file, process)
        process.send_signal(signal.SIGTERM)
        # git has its SIGTERM once it noted it; the runner now waits out the grace period before SIGKILL.
        self.assertTrue(wait_for(lambda: Path(f"{pid_file}.log").exists() or process.poll() is not None))
        self.assertTrue(runner.group_alive(group), "git did not outlive SIGTERM, so the grace period was not tested")
        process.send_signal(signal.SIGHUP)
        self.lock_waits_for(root, group)
        _, err = process.communicate(timeout=60)
        self.assertEqual(process.returncode, 128 + signal.SIGTERM, err)
        self.assertEqual(self.report(err)["signal"], "SIGTERM")

    def test_a_signal_while_a_timed_out_git_is_ended_does_not_cut_it_short(self):
        # The fetch timed out and git has its SIGTERM; a signal in the grace period must wait for SIGKILL, not skip it.
        root, pid_file, env = self.checkout("timed-out", "stubborn")
        process = self.driver(root, env, prelude="runner.FETCH_TIMEOUT = 1; runner.GIT_TERM_GRACE = 4")
        group = self.git_group(pid_file, process)
        self.assertTrue(wait_for(lambda: Path(f"{pid_file}.log").exists() or process.poll() is not None))
        self.assertTrue(runner.group_alive(group), "git did not outlive SIGTERM, so the grace period was not tested")
        process.send_signal(signal.SIGTERM)
        self.lock_waits_for(root, group)
        _, err = process.communicate(timeout=60)
        self.assertEqual(process.returncode, 128 + signal.SIGTERM, err)
        self.assertEqual(self.report(err)["status"], "interrupted")

    def test_a_signal_as_the_fetch_times_out_still_ends_git(self):
        # The signal lands after communicate() raised TimeoutExpired but before hold() took effect: the still-armed
        # handler raises at hold()'s first line, where CPython runs signal handlers.
        prelude = ("real = runner.Interruption.hold\n"
                   "def hold(self):\n"
                   "    if isinstance(sys.exc_info()[1], subprocess.TimeoutExpired) and not getattr(self, 'fired', 0):\n"
                   "        self.fired = 1\n"
                   "        self.handle(signal.SIGTERM, None)\n"
                   "    return real(self)\n"
                   "runner.Interruption.hold = hold\n"
                   "runner.FETCH_TIMEOUT = 1\n"
                   "runner.GIT_TERM_GRACE = 2")
        root, pid_file, env = self.checkout("timing-out", "stubborn")
        process = self.driver(root, env, prelude=prelude)
        group = self.git_group(pid_file, process)
        self.lock_waits_for(root, group)
        _, err = process.communicate(timeout=60)
        self.assertEqual(process.returncode, 128 + signal.SIGTERM, err)
        self.assertFalse(runner.group_alive(group), "git kept running after a signal that came as its fetch timed out")
        self.assertTrue(Path(f"{pid_file}.log").exists(), "git got SIGKILL without SIGTERM first")
        self.assertEqual(self.report(err)["status"], "interrupted")

    # The runner signals itself right after Popen has started the git named in $STARTING, before Popen has returned
    # its process object; that git's pid (its process group) goes to $FAKE_GIT_PID.started.
    STARTING = ("starting = ['git', *os.environ['STARTING'].split()]\n"
                "real = subprocess.Popen\n"
                "class Starting(real):\n"
                "    def __init__(self, args, *rest, **kwargs):\n"
                "        super().__init__(args, *rest, **kwargs)\n"
                "        if args[:len(starting)] == starting:\n"
                "            open(os.environ['FAKE_GIT_PID'] + '.started', 'w').write(str(self.pid))\n"
                "            os.kill(os.getpid(), signal.SIGTERM)\n"
                "subprocess.Popen = Starting")

    def test_a_signal_while_git_starts_still_ends_it(self):
        # The signal reaches the runner after git exists but before Popen has returned its process object: in the
        # fetch, and in the first git after the switch.
        for starting, switched in (("fetch", False), ("rev-parse HEAD", True)):
            with self.subTest(git=starting):
                root, pid_file, env = self.checkout(f"starting-{starting.split()[0]}", "clean", switched)
                self.addCleanup(self.end_group, Path(f"{pid_file}.started"))
                process = self.driver(root, dict(env, STARTING=starting), prelude=self.STARTING)
                _, err = process.communicate(timeout=60)
                group = int(Path(f"{pid_file}.started").read_text())
                self.assertEqual(process.returncode, 128 + signal.SIGTERM, err)
                self.assertFalse(runner.group_alive(group),
                                 "git kept running after a signal that came while it started")
                self.assertEqual(self.report(err)["status"], "interrupted")

    def test_a_signal_while_a_runs_checkout_git_starts_still_ends_it(self):
        # The same moment in the first git a run starts, to record the checkout: the run's handler must reach git(),
        # which holds the signal until git's group can be ended. Without it, git keeps running after the run.
        root, pid_file, env = self.checkout("starting-run", "clean", switched=True)
        self.addCleanup(self.end_group, Path(f"{pid_file}.started"))
        task_pid = self.base / "starting-run-task.pid"
        process = self.run_driver(root, dict(env, STARTING="rev-parse HEAD"), task_pid, prelude=self.STARTING)
        _, err = process.communicate(timeout=60)
        group = int(Path(f"{pid_file}.started").read_text())
        self.assertEqual(process.returncode, 128 + signal.SIGTERM, err)
        self.assertFalse(runner.group_alive(group), "the run's git kept running after a signal that came while it started")
        result = json.loads(runner.latest_result(root).read_text())
        self.assertEqual(result["interrupted"], "SIGTERM")
        self.assertEqual(result["checkout"], {"revision": None, "clean": None, "branch": None})
        self.assertEqual(result["tasks"], [{"id": "tier-a/long", "status": "not_started",
                                            "reason": "run interrupted by SIGTERM"}])
        self.assertFalse(task_pid.exists(), "the task started after the run was interrupted")

    def test_a_git_that_cannot_start_re_arms_the_handler(self):
        # git() holds the handler while Popen starts git. When git cannot start (no git on PATH) it must re-arm it: a
        # run goes on after its checkout record failed, and a held handler would only note SIGINT, SIGTERM and SIGHUP
        # until its last task ended.
        interruption = runner.Interruption()
        with mock.patch.object(runner.subprocess, "Popen", side_effect=FileNotFoundError(2, "No such file", "git")):
            with self.assertRaises(FileNotFoundError):
                runner.git(self.base, "status", interruption=interruption)
        self.assertTrue(interruption.armed, "git() left the handler held after git could not start")
        self.assertIsNone(interruption.number)

    def test_a_signal_while_git_fails_to_start_is_raised(self):
        interruption = runner.Interruption()

        def failing(*_args, **_kwargs):
            interruption.handle(signal.SIGTERM, None)  # Held while git starts, so only noted.
            raise FileNotFoundError(2, "No such file", "git")
        with mock.patch.object(runner.subprocess, "Popen", side_effect=failing):
            with self.assertRaises(runner.Interrupted) as raised:
                runner.git(self.base, "status", interruption=interruption)
        self.assertEqual(raised.exception.number, signal.SIGTERM)
        self.assertTrue(interruption.armed)

    # With no git on PATH, the runner signals itself while Popen tries to start git. signal.raise_signal runs the
    # handler before it returns, so the signal always comes while git() holds it.
    NO_GIT = ("real = subprocess.Popen\n"
              "def starting(args, *rest, **kwargs):\n"
              "    if args[0] == 'git':\n"
              "        signal.raise_signal(signal.SIGTERM)\n"
              "    return real(args, *rest, **kwargs)\n"
              "subprocess.Popen = starting")

    def no_git(self):
        """An environment whose PATH has no git (the runner and its task use absolute paths)."""
        empty = self.base / "empty"
        empty.mkdir(exist_ok=True)
        return {"PATH": str(empty)}

    def test_a_signal_while_updates_git_cannot_start_is_reported_as_an_interruption(self):
        # Not as the refusal "git status could not run".
        root = self.base / "no-git-update"
        root.mkdir()
        process = self.driver(root, self.no_git(), prelude=self.NO_GIT)
        _, err = process.communicate(timeout=60)
        self.assertEqual(process.returncode, 128 + signal.SIGTERM, err)
        report = self.report(err)
        self.assertEqual((report["status"], report["signal"]), ("interrupted", "SIGTERM"))

    def test_a_run_without_git_on_path_still_stops_on_a_signal(self):
        # The run's checkout record cannot start git, and the run goes on: SIGTERM must still end it (and its task) at
        # once, not be noted until the task ends.
        root = self.base / "no-git-run"
        task_pid = self.base / "no-git-run-task.pid"
        process = self.run_driver(root, self.no_git(), task_pid)
        wait_for(lambda: (task_pid.exists() and task_pid.read_text()) or process.poll() is not None)
        if not (task_pid.exists() and task_pid.read_text()):
            self.fail(f"the task did not start: {process.communicate(timeout=30)}")
        pid = int(task_pid.read_text())
        self.addCleanup(lambda: alive(pid) and os.kill(pid, signal.SIGKILL))
        process.send_signal(signal.SIGTERM)
        _, err = process.communicate(timeout=60)
        self.assertEqual(process.returncode, 128 + signal.SIGTERM, err)
        self.assertTrue(wait_dead(pid, 1), "the task kept running after the runner was signalled")
        result = json.loads(runner.latest_result(root).read_text())
        self.assertEqual(result["interrupted"], "SIGTERM")
        self.assertEqual(result["checkout"], {"revision": None, "clean": None, "branch": None})
        self.assertEqual(result["tasks"][0]["status"], "interrupted")

    @staticmethod
    def timing_out_once_running(pid_file):
        """Popen whose "git rev-parse HEAD" times out 1 s after the fake git wrote ``pid_file``, by which time it holds
        its lock and has set its SIGTERM trap, rather than 1 s after it was started, however loaded the machine."""
        real = subprocess.Popen

        class Running(real):
            def communicate(self, input=None, timeout=None):
                if self.args == ["git", "rev-parse", "HEAD"]:
                    wait_for(lambda: pid_file.exists() or self.poll() is not None)
                    timeout = 1
                return super().communicate(input, timeout)
        return mock.patch.object(subprocess, "Popen", Running)

    def test_a_checkout_record_that_times_out_after_the_switch_refuses_and_names_the_lock(self):
        # The switch has moved the checkout, so a timed-out record of it refuses (exit 2) instead of printing "updated".
        # A git killed holding .git/index.lock leaves it, named for a person and never deleted; a git that removes its
        # lock on SIGTERM leaves nothing to name.
        for mode, grace in (("stubborn", 1), ("clean", 10)):
            with self.subTest(mode=mode):
                root, pid_file, env = self.checkout(f"record-{mode}", mode, switched=True)
                with mock.patch.dict(os.environ, env), mock.patch.object(runner, "GIT_TERM_GRACE", grace), \
                        self.timing_out_once_running(pid_file):
                    code, out, err = quiet(runner.main, ["update", "a" * 40], root=root)
                self.assertEqual(code, 2, err)
                self.assertEqual(out, "", "a failed checkout record was reported as updated")
                calls = Path(f"{pid_file}.calls").read_text().splitlines()
                self.assertEqual(calls[-2:], [f"switch --quiet --detach {'a' * 40}", "rev-parse HEAD"])
                report = self.report(err)
                self.assertEqual(report["status"], "refused")
                self.assertIn(f"switched to {'a' * 40}, but recording the checkout failed: git rev-parse HEAD timed "
                              "out after 1s; check the checkout (git status) before using it", report["reason"])
                self.assertFalse(runner.group_alive(int(pid_file.read_text())), "git outlived the timed-out record")
                if mode == "stubborn":
                    self.assertIn(".git/index.lock was left behind: a person must check", report["reason"])
                    self.assertTrue((root / ".git/index.lock").exists(), "the runner deleted git's lock file")
                else:
                    self.assertNotIn("index.lock", report["reason"])
                    self.assertFalse((root / ".git/index.lock").exists())

    def test_a_checkout_record_that_fails_after_the_switch_refuses(self):
        # A record git that exits nonzero or cannot start refuses too; a .git/index.lock there is named, never deleted.
        real = subprocess.Popen

        def no_rev_parse(args, *rest, **kwargs):
            if args == ["git", "rev-parse", "HEAD"]:
                raise FileNotFoundError(2, "No such file or directory", "git")
            return real(args, *rest, **kwargs)
        for case, failure, lock in (("exits nonzero", "git rev-parse HEAD failed: fatal: cannot read HEAD", False),
                                    ("cannot start", "git rev-parse HEAD could not run: [Errno 2]", False),
                                    ("exits nonzero with a lock", "git rev-parse HEAD failed", True)):
            with self.subTest(case=case):
                root, pid_file, env = self.checkout(f"record-{case.replace(' ', '-')}", "fail", switched=True)
                if lock:
                    (root / ".git/index.lock").touch()
                with mock.patch.dict(os.environ, env), mock.patch.object(
                        subprocess, "Popen", no_rev_parse if case == "cannot start" else real):
                    code, out, err = quiet(runner.main, ["update", "a" * 40], root=root)
                self.assertEqual(code, 2, err)
                self.assertEqual(out, "", "a failed checkout record was reported as updated")
                calls = Path(f"{pid_file}.calls").read_text().splitlines()
                self.assertEqual(calls[-1], f"switch --quiet --detach {'a' * 40}" if case == "cannot start"
                                 else "rev-parse HEAD")
                report = self.report(err)
                self.assertEqual(report["status"], "refused")
                self.assertIn(f"switched to {'a' * 40}, but recording the checkout failed: {failure}",
                              report["reason"])
                self.assertIn("check the checkout (git status) before using it", report["reason"])
                if lock:
                    self.assertIn(".git/index.lock was left behind", report["reason"])
                    self.assertTrue((root / ".git/index.lock").exists(), "the runner deleted git's lock file")
                else:
                    self.assertNotIn("index.lock", report["reason"])

    def test_an_interruption_report_without_a_terminal_still_exits_with_the_signal(self):
        # After a hangup there may be no terminal: a report that cannot be written must not replace the exit code.
        root = self.base / "no-terminal"
        root.mkdir()

        class Gone(io.StringIO):
            def write(self, _text):
                raise OSError(errno.EIO, "Input/output error")
        with mock.patch.object(runner, "update", side_effect=runner.Interrupted(signal.SIGHUP)), \
                mock.patch.object(sys, "stderr", Gone()):
            code = runner.main(["update", "a" * 40], root=root)
        self.assertEqual(code, 128 + signal.SIGHUP)

    def test_git_has_no_terminal_to_prompt_on(self):
        # docs/on-device.md: git runs with GIT_TERMINAL_PROMPT=0, stdin from /dev/null and in its own session (so
        # without a controlling terminal). Here the runner's own stdin is a pipe and its environment allows prompts, so
        # git inheriting either would show.
        root, pid_file, env = self.checkout("no-prompt", "pass")
        Path(f"{pid_file}.release").touch()  # The fetch returns at once; update then stops at merge-base.
        process = self.driver(root, dict(env, GIT_TERMINAL_PROMPT="1"), stdin=subprocess.PIPE)
        _, err = process.communicate(timeout=60)
        self.assertEqual(process.returncode, 2, err)
        self.assertIn("not on origin/main", err)
        calls = Path(f"{pid_file}.env").read_text().splitlines()
        self.assertEqual(len(calls), len(Path(f"{pid_file}.calls").read_text().splitlines()))
        self.assertGreaterEqual(len(calls), 3, calls)  # status, fetch, merge-base
        for call in calls:
            fields = dict(field.split("=", 1) for field in call.split())
            self.assertEqual((fields["prompt"], fields["stdin"]), ("0", "/dev/null"), call)
            self.assertEqual(fields["sid"], fields["pid"], f"git did not run in a session of its own: {call}")

    def test_a_hangup_ignored_when_update_starts_stays_ignored(self):
        root, pid_file, env = self.checkout("nohup", "pass")
        process = self.driver(root, env, prelude="signal.signal(signal.SIGHUP, signal.SIG_IGN)")
        self.git_group(pid_file, process)
        process.send_signal(signal.SIGHUP)
        Path(f"{pid_file}.release").touch()
        _, err = process.communicate(timeout=60)
        # The fetch finished and update went on: the fake git knows no merge-base, so the revision is refused.
        self.assertEqual(process.returncode, 2, err)
        self.assertIn("not on origin/main", err)

    def update(self, env, root, timeout, grace):
        with mock.patch.dict(os.environ, env), mock.patch.object(runner, "FETCH_TIMEOUT", timeout), \
                mock.patch.object(runner, "GIT_TERM_GRACE", grace):
            started = time.monotonic()
            code, _, err = quiet(runner.main, ["update", "a" * 40], root=root)
        return code, err, time.monotonic() - started

    def test_a_timed_out_git_gets_sigterm_first_and_removes_its_lock(self):
        root, pid_file, env = self.checkout("clean", "clean")
        code, err, _ = self.update(env, root, timeout=1, grace=10)
        self.assertEqual(code, 2, err)
        self.assertIn("git fetch timed out after 1s", err)
        self.assertFalse((root / ".git/index.lock").exists(), "git was killed before it could remove its lock")
        self.assertNotIn("index.lock", err)
        self.assertFalse(runner.group_alive(int(pid_file.read_text())))

    def test_a_git_that_ignores_sigterm_is_killed_after_the_grace_and_its_lock_reported(self):
        root, pid_file, env = self.checkout("stubborn", "stubborn")
        with mock.patch.object(runner, "KILL_GRACE", 20):  # Far from GIT_TERM_GRACE, so using it instead shows.
            code, err, elapsed = self.update(env, root, timeout=1, grace=2)
        self.assertEqual(code, 2, err)
        self.assertIn("git fetch timed out after 1s", err)
        self.assertTrue(Path(f"{pid_file}.log").exists(), "git got SIGKILL without SIGTERM first")
        self.assertGreaterEqual(elapsed, 2.9, "git got SIGKILL before the grace period was over")
        self.assertLess(elapsed, 1 + 2 + 6, "git's SIGTERM grace was not GIT_TERM_GRACE")
        self.assertFalse(runner.group_alive(int(pid_file.read_text())), "git outlived SIGKILL")
        self.assertIn(".git/index.lock", self.report(err)["reason"])
        self.assertIn("a person must check", err)
        self.assertTrue((root / ".git/index.lock").exists(), "the runner deleted git's lock file")

    def test_the_documented_grace_and_bound_match_the_code(self):
        # docs/on-device.md gives both numbers, docs/agent-on-frame.md the grace.
        self.assertEqual((runner.GIT_TERM_GRACE, runner.GIT_TERM_GRACE + 2 * runner.KILL_GRACE), (5, 25))
        on_device = " ".join((ROOT / "docs/on-device.md").read_text().split())
        self.assertIn("SIGKILL 5 seconds later; ending git takes at most 25 seconds", on_device)
        self.assertIn("SIGKILL 5 seconds later", " ".join((ROOT / "docs/agent-on-frame.md").read_text().split()))

    def test_a_git_directory_that_cannot_be_read_is_reported_as_unknown(self):
        root = self.base / "unreadable"
        root.mkdir()
        (root / ".git").write_bytes(b"gitdir: \xff\n")  # Not text.
        self.assertIn("whether .git/index.lock was left behind is unknown", runner.index_lock_left(root))
        with mock.patch.object(Path, "read_text", side_effect=PermissionError(13, "Permission denied")):
            self.assertIn("is unknown ([Errno 13] Permission denied): a person must check",
                          runner.index_lock_left(root))

    def test_a_lock_left_in_a_worktrees_git_directory_is_reported(self):
        # In a worktree (or with a relative gitdir), ".git" is a file that names the git directory holding index.lock.
        for gitdir in (self.base / "gitdirs/wt", Path("gd")):
            with self.subTest(gitdir=str(gitdir)):
                root = self.base / f"worktree-{gitdir.name}"
                root.mkdir()
                (root / ".git").write_text(f"gitdir: {gitdir}\n")
                lock = root / gitdir / "index.lock"
                lock.parent.mkdir(parents=True)
                self.assertEqual(runner.index_lock_left(root), "")
                lock.touch()
                self.assertIn(f"{lock if gitdir.is_absolute() else gitdir / 'index.lock'} was left behind",
                              runner.index_lock_left(root))
                self.assertTrue(lock.exists(), "the runner deleted git's lock file")


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
                ("Glob", {"pattern": "{..}/*"}, "contain '..'"),
                ("Glob", {"pattern": "{..,docs}/*.md"}, "contain '..'"),
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

    def test_ordinary_glob_patterns_are_allowed(self):
        for pattern, path in (("**/*.json", None), ("*.py", None), ("*", None), ("**", None), ("**/*", None),
                              ("docs/*.md", None), ("docs/**/*.md", None), ("sightcircuit/**/*.py", None),
                              ("tests/test_frame_*.py", None), ("artifacts/agent-runs/*/result.json", None),
                              ("*.{json,md}", None), ("{docs,scripts}/*.md", None), ("docs/[a-c]*.md", None),
                              ("docs/?tatus.md", None), ("README.md", None), ("docs/status.md", None),
                              (f"{self.root}/docs/*.md", None), (f"{self.root}/**/*.py", None),
                              (f"{self.root}/docs/status.md", None), ("*.json", "artifacts/agent-runs"),
                              ("**/*.py", "sightcircuit"), ("*.md", f"{self.root}/docs")):
            with self.subTest(pattern=pattern, path=path):
                self.assertEqual(self.decide("Glob", pattern=pattern, **({"path": path} if path else {})), (0, ""))

    def test_glob_patterns_with_an_unclear_search_directory_are_refused(self):
        # Claude Code reads "\" as an escape and drops a trailing "/" (it searches /srv for "/srv/sightcircuit/"), so
        # the guard would check another directory than the one searched; it refuses them rather than parse them.
        for pattern in ("docs/", "evaluation/", f"{self.root}/", f"{self.root}/docs/", f"{self.root}/docs/*/",
                        "/srv/", "docs\\*.md", "docs\\status.md", "\\*.json", "docs/\\[x\\]/*.md",
                        f"{self.root}\\docs\\*.md", f"{self.root}/evaluation/lo\\*cal/x/*", "docs/a\\/b/*"):
            with self.subTest(pattern=pattern):
                code, message = self.decide("Glob", pattern=pattern)
                self.assertEqual(code, 2)
                self.assertIn("may not contain '\\' or end in '/'", message)

    def test_the_stricter_glob_check_raised_the_guard_version(self):
        # update refuses a revision whose guard is older, so an agent cannot move back to the laxer glob check.
        self.assertGreaterEqual(guard.GUARD_VERSION, 4)

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
