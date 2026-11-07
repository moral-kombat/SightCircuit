"""Run fixed agent tasks with timeouts, a lock and a JSON result per run.

Commands (see ``scripts/frame_agent_run.py``):

- ``list``: the task table as JSON.
- ``run TASK [TASK ...]`` / ``run-tier tier-a``: one new run directory under
  ``artifacts/agent-runs/``, tasks in order, each with its own timeout and the
  whole run within a budget.
- ``update REVISION``: detach a clean checkout at a full commit already on
  ``origin/main`` whose agent guard is at least as strict as this one. A git
  command that fails or times out refuses the update (exit 2), also one that
  records the checkout after the switch: the checkout has then moved, and the
  refusal says "switched to REVISION, but recording the checkout failed". A
  timed-out or interrupted git gets SIGTERM, so that it removes its lock files,
  and SIGKILL ``GIT_TERM_GRACE`` seconds later. A ``.git/index.lock`` still
  there after a timed-out or interrupted git, or after a failed checkout
  record, is named for a person to check, never deleted.
- ``report latest``: print the newest run's ``result.json``.

Exit codes: 0 every task passed, 1 a task failed, timed out or was skipped,
2 refused (unknown/disabled task or bad argument), 3 another run holds the lock,
128 + signal number when SIGINT, SIGTERM or SIGHUP interrupted the run or the
update (the running task's or git's process group is terminated before the
lock is released; ``result.json``, or for ``update`` a JSON line on stderr,
records the interruption). A signal the runner was started with ignored, as
``nohup`` ignores SIGHUP, stays ignored.
"""
import argparse
import contextlib
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import platform
import re
import signal
import subprocess
import sys
import threading
import time

from sightcircuit.agent.guard import GUARD_VERSION
from sightcircuit.agent.tasks import TASKS, TASKS_BY_ID, TIER_A, VENV_PYTHON

RUNS = Path("artifacts/agent-runs")
DEFAULT_BUDGET = 5400
KILL_GRACE = 10
# Seconds git's group has after SIGTERM before SIGKILL; ending git then takes at most GIT_TERM_GRACE + 2 * KILL_GRACE.
GIT_TERM_GRACE = 5
FETCH_TIMEOUT = 180
REVISION = re.compile(r"^[0-9a-f]{40}$")
RUN_DIR = re.compile(r"^\d{8}T\d{6}Z-")
AGENT_FILES = ("sightcircuit/agent/guard.py", "scripts/frame_agent_guard.py", "scripts/frame_agent_run.py")
INTERRUPTING = tuple(getattr(signal, name) for name in ("SIGINT", "SIGTERM", "SIGHUP") if hasattr(signal, name))
PROVENANCE = ("frame_agent_run.py output: command exit codes and reports from the machine named in "
              "'host'. On Frame this is agent-run device evidence, not a user report or a cloud check. "
              "Exit codes do not establish headset recovery or what the wearer saw.")


class Refused(Exception):
    pass


class Interrupted(BaseException):
    """SIGINT, SIGTERM or SIGHUP reached the runner during a run or an update."""

    def __init__(self, number):
        super().__init__(number)
        self.number = number


class Interruption:
    """Turns the first interrupting signal into ``Interrupted`` while armed; later signals are only noted.

    A task, like git, runs in its own session, so a signal to the runner (or a
    terminal hangup) does not reach it. Raising lets the runner terminate the
    task's or git's group, record the interruption (``result.json``, or for
    ``update`` a JSON line on stderr) and release the lock instead of dying at
    once.
    """

    def __init__(self):
        self.number = None
        self.armed = True

    def handle(self, number, _frame):
        if self.number is None:
            self.number = number
            if self.armed:
                raise Interrupted(number)

    def hold(self):
        """From now on only note a signal; returns whether it was armed, for ``resume``."""
        armed, self.armed = self.armed, False
        return armed

    def resume(self, armed):
        """Re-arm (when ``hold`` found it armed) and raise a signal noted in the meantime."""
        self.armed = armed
        if armed and self.number is not None:
            raise Interrupted(self.number)

    @contextlib.contextmanager
    def installed(self):
        if threading.current_thread() is not threading.main_thread():
            yield self  # Signal handlers can only be set from the main thread.
            return
        # A signal ignored by whoever started the runner (nohup, a background job) stays ignored.
        previous = {number: signal.signal(number, self.handle) for number in INTERRUPTING
                    if signal.getsignal(number) != signal.SIG_IGN}
        try:
            yield self
        finally:
            for number, handler in previous.items():
                signal.signal(number, handler)


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def git(root, *args, timeout=30, interruption=None):
    """Run git in its own session, with no terminal to prompt on; a timeout or an interruption ends git's whole group.

    The group (git and the transport it started: ssh, git-remote-https,
    upload-pack) gets SIGTERM, on which git removes its lock files, then SIGKILL
    after ``GIT_TERM_GRACE``. As in ``run_task``, ``interruption`` only notes a
    signal while git starts and while it is being ended, and raises it after.
    Raises ``subprocess.TimeoutExpired``, ``OSError`` and ``Interrupted``.
    """
    armed = interruption.hold() if interruption else False
    try:
        process = subprocess.Popen(["git", *args], cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, start_new_session=True,
                                   env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    except BaseException:
        if interruption:
            interruption.resume(armed)
        raise
    ending = False
    try:
        try:
            if interruption:
                interruption.resume(armed)  # A signal that came while git started is raised here.
            out, err = process.communicate(timeout=timeout)
        except BaseException:
            armed = interruption.hold() if interruption else False
            ending = True
            end_git(process)
            if interruption:
                interruption.resume(armed)  # A signal while git was ended replaces a timeout.
            raise
    except Interrupted:
        # A signal after communicate() gave up but before hold() took effect is raised inside hold(): end git all
        # the same. Only the first signal raises, so nothing interrupts this.
        if not ending:
            end_git(process)
        raise
    return process.returncode, out.strip(), err.strip()


def end_git(process):
    kill_group(process, GIT_TERM_GRACE)
    for stream in (process.stdout, process.stderr):
        stream.close()  # Unread output; a helper that left the group cannot keep the runner waiting on it.


def index_lock_left(root):
    """After git was ended or failed: why a person must look at the checkout when git's index lock is left, else ""."""
    try:
        git_dir = root / ".git"
        if git_dir.is_file():  # A worktree: ".git" names the git directory.
            text = git_dir.read_text()
            if "gitdir:" not in text:
                raise ValueError(".git is a file that names no gitdir")
            git_dir = root / text.partition("gitdir:")[2].strip()
        lock = git_dir / "index.lock"
        if not lock.exists():
            return ""
    except (OSError, ValueError) as error:  # ValueError: a ".git" file that is not text.
        return f"; whether .git/index.lock was left behind is unknown ({error}): a person must check"
    shown = lock.relative_to(root) if lock.is_relative_to(root) else lock
    return f"; {shown} was left behind: a person must check that no git command is running before removing it"


def checkout_state(root, interruption=None, strict=False):
    """The checkout's revision, clean flag and branch, from three gits; ``Interrupted`` always propagates (it is never caught).

    For a run, a field whose git failed, could not run or timed out is None;
    the fields the other gits gave are kept. ``strict`` (``update``, after the
    switch) raises ``Refused`` naming the git instead.
    """
    outputs = {}
    for field, args in (("revision", ("rev-parse", "HEAD")), ("clean", ("status", "--porcelain")),
                        ("branch", ("rev-parse", "--abbrev-ref", "HEAD"))):
        command = " ".join(("git", *args))
        try:
            code, out, err = git(root, *args, interruption=interruption)
        except subprocess.TimeoutExpired as expired:
            if strict:
                raise Refused(f"{command} timed out after {expired.timeout:g}s") from None
            outputs[field] = None
            continue
        except OSError as failure:
            if strict:
                raise Refused(f"{command} could not run: {failure}") from None
            outputs[field] = None
            continue
        if code != 0 and strict:
            raise Refused(f"{command} failed: {err[-500:]}")
        outputs[field] = out if code == 0 else None
    return {"revision": outputs["revision"], "clean": None if outputs["clean"] is None else outputs["clean"] == "",
            "branch": outputs["branch"] or None}


def host_info():
    return {"system": platform.system(), "release": platform.release(), "machine": platform.machine(),
            "node": platform.node()}


class RunLock:
    def __init__(self, root):
        self.path = root / RUNS / ".lock"
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = open(self.path, "w")
        try:
            fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.handle.close()
            raise
        return self

    def __exit__(self, *_):
        fcntl.flock(self.handle, fcntl.LOCK_UN)
        self.handle.close()


def resolve(tasks_by_id, ids):
    if not ids:
        raise Refused("no task given; use 'list' to see task IDs")
    tasks = []
    for task_id in ids:
        task = tasks_by_id.get(task_id)
        if task is None:
            raise Refused(f"unknown task {task_id!r}; use 'list' to see task IDs")
        if not task.runnable:
            raise Refused(f"{task_id} is refused: {task.refusal}")
        tasks.append(task)
    if len({task.id for task in tasks}) != len(tasks):
        raise Refused("a task is listed twice")
    return tasks


def new_run_dir(root, label):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = root / RUNS / f"{stamp}-{label}"
    for suffix in [""] + [f"-{n}" for n in range(2, 100)]:
        path = Path(str(base) + suffix)
        try:
            path.mkdir(parents=True)
            return path
        except FileExistsError:
            continue
    raise RuntimeError("could not create a new run directory")


def group_alive(pgid):
    """True while process group ``pgid`` has a member that is not a zombie."""
    proc = Path("/proc")
    if not (proc / "self" / "stat").exists():
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return False
        return True
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
        except OSError:
            continue
        state, _, group = stat[stat.rfind(")") + 2:].split()[:3]
        if state != "Z" and int(group) == pgid:
            return True
    return False


def group_gone(process, seconds):
    """Wait up to ``seconds`` for every member of the task's group to exit; True once none is alive."""
    deadline = time.monotonic() + seconds
    while True:
        process.poll()  # Reap the leader so it no longer counts as a member.
        if not group_alive(process.pid):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def kill_group(process, term_grace=None):
    """SIGTERM the whole process group of ``process`` (a task, or git), then SIGKILL whatever is still alive after
    ``term_grace`` (default ``KILL_GRACE``).

    Waits for every member, not just the leader, so a descendant that ignores
    SIGTERM or is slow to clean up cannot outlive the task.
    """
    for number, grace in ((signal.SIGTERM, KILL_GRACE if term_grace is None else term_grace),
                          (signal.SIGKILL, KILL_GRACE)):
        try:
            os.killpg(process.pid, number)
        except ProcessLookupError:
            break
        if group_gone(process, grace):
            break
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=KILL_GRACE)


def run_task(root, task, task_dir, timeout, record=None, interruption=None):
    """Run one task, filling and returning ``record`` (updated in place, so an interruption keeps it).

    While ``Popen`` starts the task there is no process object to terminate, so
    ``interruption`` only notes a signal then and raises it once the task's
    group can be terminated.
    """
    argv = [part.format(venv_python=str(root / VENV_PYTHON), run_dir=str(task_dir)) for part in task.argv]
    record = {} if record is None else record
    record.update(id=task.id, description=task.description, argv=argv, timeout_s=timeout,
                  started_at=utc_now(), stdout="stdout.txt", stderr="stderr.txt")
    missing = [path for path in task.requires if not (root / path).exists()]
    if missing:
        record.update(status="skipped_precondition", exit_code=None, missing=missing, duration_s=0.0)
        return record
    env = dict(os.environ, **task.env)
    started = time.monotonic()
    with open(task_dir / "stdout.txt", "wb") as out, open(task_dir / "stderr.txt", "wb") as err:
        armed = interruption.hold() if interruption else False
        try:
            process = subprocess.Popen(argv, cwd=root, env=env, stdin=subprocess.DEVNULL, stdout=out,
                                       stderr=err, start_new_session=True)
        except OSError as error:
            record.update(status="failed_to_start", exit_code=None, error=str(error), duration_s=0.0)
            if interruption:
                interruption.resume(armed)
            return record
        timed_out = leftover = False
        try:
            if interruption:
                interruption.resume(armed)  # A signal that came while the task started is raised here.
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                kill_group(process)
                code = process.returncode
            else:
                # The leader exited; anything still running in its group was left behind.
                leftover = not group_gone(process, 0.5)
                if leftover:
                    kill_group(process)
        except BaseException:  # Interrupted (or any error), possibly in the middle of kill_group: finish it.
            kill_group(process)
            record.update(exit_code=process.returncode, duration_s=round(time.monotonic() - started, 3))
            raise
    record.update(exit_code=code, duration_s=round(time.monotonic() - started, 3),
                  status="timed_out" if timed_out else ("passed" if code == 0 and not leftover else "failed"))
    if leftover:
        record["error"] = "exited but left processes running in its process group; they were terminated"
    return record


def execute(root, tasks, label, budget=DEFAULT_BUDGET, clock=time.monotonic):
    """Run ``tasks`` in a new run directory; returns (exit_code, result, run_dir).

    ``result.json`` is rewritten before and after each task, so a run that is
    interrupted or killed still shows which task was running.
    """
    run_dir = new_run_dir(root, label)
    result = {"schema": "sightcircuit.agent-run.v1", "provenance": PROVENANCE, "label": label,
              "started_at": utc_now(), "host": host_info(),
              "checkout": {"revision": None, "clean": None, "branch": None}, "budget_s": budget, "tasks": []}
    stop_reason = None
    with Interruption().installed() as interruption:
        try:
            result["checkout"] = checkout_state(root, interruption)  # A signal here ends git's group, as in update.
            deadline = clock() + budget
            for task in tasks:
                task_dir = run_dir / task.id.replace("/", "--")
                task_dir.mkdir()
                remaining = math.ceil(deadline - clock())
                if stop_reason or remaining <= 0:
                    reason = stop_reason or "run budget exhausted"
                    result["tasks"].append({"id": task.id, "status": "not_started", "reason": reason})
                    continue
                record = {"id": task.id, "status": "running", "started_at": utc_now()}
                result["tasks"].append(record)
                write_json(run_dir / "result.json", result)
                run_task(root, task, task_dir, min(task.timeout, remaining), record, interruption)
                if task.stop_on_failure and record["status"] != "passed":
                    stop_reason = f"{task.id} did not pass"
                write_json(run_dir / "result.json", result)
            interruption.armed = False  # From here a signal is noted, not raised, so the result is written.
        except Interrupted:
            interruption.armed = False  # Only the first signal raises, so this cannot be interrupted again.
        if interruption.number is not None:
            name = signal.Signals(interruption.number).name
            result["interrupted"] = name
            for record in result["tasks"]:
                if record.get("status") == "running":
                    record["status"] = "interrupted"
            done = {record["id"] for record in result["tasks"]}
            result["tasks"] += [{"id": task.id, "status": "not_started", "reason": f"run interrupted by {name}"}
                                for task in tasks if task.id not in done]
        result["finished_at"] = utc_now()
        result["all_passed"] = all(task.get("status") == "passed" for task in result["tasks"])
        write_json(run_dir / "result.json", result)
    if "interrupted" in result:
        return 128 + signal.Signals[result["interrupted"]], result, run_dir
    return (0 if result["all_passed"] else 1), result, run_dir


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def update(root, revision, interruption=None):
    if not REVISION.match(revision or ""):
        raise Refused("revision must be a full 40-character lowercase commit hash")

    def run(*args, timeout=30):  # Every git failure, including a timeout, refuses the update.
        try:
            return git(root, *args, timeout=timeout, interruption=interruption)
        except subprocess.TimeoutExpired:
            raise Refused(f"git {args[0]} timed out after {timeout}s{index_lock_left(root)}") from None
        except OSError as error:
            raise Refused(f"git {args[0]} could not run: {error}") from None

    code, status, error = run("status", "--porcelain")
    if code != 0:
        raise Refused(f"git status failed, so the checkout is not known to be clean: {error[-500:]}")
    if status:
        raise Refused("checkout has local changes; a person must resolve them")
    code, _, error = run("fetch", "--quiet", "origin", "main", timeout=FETCH_TIMEOUT)
    if code != 0:
        raise Refused(f"git fetch failed: {error[-500:]}")
    code, _, _ = run("merge-base", "--is-ancestor", revision, "origin/main")
    if code != 0:
        raise Refused("revision is not on origin/main; unmerged revisions are checked out by a person")
    for path in AGENT_FILES:
        if run("cat-file", "-e", f"{revision}:{path}")[0] != 0:
            raise Refused(f"revision has no {path}; older revisions are checked out by a person")
    _, source, _ = run("show", f"{revision}:sightcircuit/agent/guard.py")
    found = re.search(r"^GUARD_VERSION = (\d+)$", source, re.MULTILINE)
    if not found or int(found.group(1)) < GUARD_VERSION:
        raise Refused(f"revision's agent guard is older than version {GUARD_VERSION}; "
                      "older revisions are checked out by a person")
    code, _, error = run("switch", "--quiet", "--detach", revision)
    if code != 0:
        raise Refused(f"git switch failed: {error[-500:]}")
    try:
        checkout = checkout_state(root, interruption, strict=True)
    except Refused as failure:  # The checkout has moved, so this is not "updated": a person must look at it.
        raise Refused(f"switched to {revision}, but recording the checkout failed: {failure}; check the checkout "
                      f"(git status) before using it{index_lock_left(root)}") from None
    return {"status": "updated", "revision": revision, "checkout": checkout}


def latest_result(root):
    """The newest runner-made run's ``result.json``: by the run directory's UTC second, then by write time."""
    runs = [path for path in (root / RUNS).glob("*/result.json") if RUN_DIR.match(path.parent.name)]
    if not runs:
        raise Refused("no run in artifacts/agent-runs/")
    return max(runs, key=lambda path: (path.parent.name[:16], path.stat().st_mtime_ns))


def task_listing():
    return [{"id": task.id, "tier": task.tier, "runnable": task.runnable, "description": task.description,
             **({"timeout_s": task.timeout} if task.runnable else {"refusal": task.refusal})}
            for task in TASKS]


def parser():
    top = argparse.ArgumentParser(prog="frame_agent_run.py", description=__doc__.split("\n")[0])
    commands = top.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="Print the task table")
    run = commands.add_parser("run", help="Run tasks by ID")
    run.add_argument("task", nargs="+")
    tier = commands.add_parser("run-tier", help="Run every runnable task of a tier")
    tier.add_argument("tier", choices=["tier-a"])
    up = commands.add_parser("update", help="Detach the clean checkout at a commit on origin/main")
    up.add_argument("revision")
    report = commands.add_parser("report", help="Print the newest run result")
    report.add_argument("which", choices=["latest"])
    return top


def main(argv=None, root=None, tasks_by_id=None, tier_a=None, budget=DEFAULT_BUDGET):
    root = Path(root) if root else Path(__file__).resolve().parents[2]
    tasks_by_id = TASKS_BY_ID if tasks_by_id is None else tasks_by_id
    tier_a = TIER_A if tier_a is None else tier_a
    try:
        args = parser().parse_args(argv)
    except SystemExit as error:
        return 2 if error.code else 0
    try:
        if args.command == "list":
            print(json.dumps(task_listing(), indent=2))
            return 0
        if args.command == "report":
            print(latest_result(root).read_text(), end="")
            return 0
        if args.command == "update":
            # As for a run: a signal ends git's group first (git() does), then the lock is released.
            with Interruption().installed() as interruption, RunLock(root):
                outcome = update(root, args.revision, interruption)
                interruption.hold()  # The update is complete: a signal while the lock is released is only noted.
            print(json.dumps(outcome, indent=2))
            return 0
        if args.command == "run-tier":
            tasks, label = list(tier_a), args.tier
        else:
            tasks = resolve(tasks_by_id, args.task)
            label = tasks[0].id.replace("/", "--") if len(tasks) == 1 else "tasks"
        with RunLock(root):
            code, result, run_dir = execute(root, tasks, label, budget=budget)
        summary = {"run_dir": str(run_dir.relative_to(root)), "all_passed": result["all_passed"],
                   **({"interrupted": result["interrupted"]} if "interrupted" in result else {}),
                   "tasks": [{key: task.get(key) for key in ("id", "status", "exit_code", "duration_s")}
                             for task in result["tasks"]]}
        with contextlib.suppress(OSError):  # After a hangup there may be no terminal to print to.
            print(json.dumps(summary, indent=2))
        return code
    except Refused as error:
        print(json.dumps({"status": "refused", "reason": str(error)}), file=sys.stderr)
        return 2
    except BlockingIOError:
        print(json.dumps({"status": "locked", "reason": "another agent run is in progress"}), file=sys.stderr)
        return 3
    except Interrupted as error:  # update; execute records a run's interruption itself once its handler is set.
        name = signal.Signals(error.number).name
        reason = (f"{args.command} interrupted by {name}; any running git was ended before the lock was released. "
                  f"Check the checkout (git status) before using it{index_lock_left(root)}")
        with contextlib.suppress(OSError):  # After a hangup there may be no terminal to print to.
            print(json.dumps({"status": "interrupted", "signal": name, "reason": reason}), file=sys.stderr)
        return 128 + error.number
