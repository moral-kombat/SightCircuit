"""Loop a test under CPU stress, with an optional probe of a worker the primitive is about to kill (not a test).

The harness behind the held-signal flake's reproduction and acceptance
(docs/reference/development-checks.md#held-signal-flake-root-cause-2026-11-08).
``unittest discover`` ignores it (no ``test_`` prefix); run it by hand:

    python3 tests/stress_supervision.py tests.test_supervision.SupervisedOwnedTests.test_a_signal_during_process_start_is_held_and_raised_once_the_worker_can_be_ended --runs 400 --probe

Each run is one ``python -m unittest <test id>`` (this interpreter) with the
stress running throughout; the output of a red run, and anything the probe
wrote, is kept under ``--out`` (default ``artifacts/stress-supervision/<UTC time>``).
The summary names every red run and prints the red count; the exit status is
0 when no run was red.

``--stress`` is the load. The default, ``stress-ng --cpu 8 --cpu-method matrixprod``,
is what the recorded reproductions used; when ``stress-ng`` is not installed
(the cloud container has no such package by default; ``apt-get install stress-ng``
adds it), ``--stress python`` runs eight Python busy loops instead, and
``--stress none`` runs the loop unloaded. ``--probe`` installs an import hook in
every Python process of the run (through ``PYTHONPATH``): before the primitive's
``OwnedGroup.terminate`` escalates to the group kill, the still-living worker's
``/proc`` signal state and process group are written to ``dump-<supervisor pid>-<worker pid>.txt``
in the run's directory and every member is sent SIGUSR1, on which
``faulthandler`` (registered by the same hook in every process) writes that
process's Python traceback to ``tb-<pid>.txt``. The probe uses no subprocess
of its own, so a test that patches ``subprocess.Popen`` is unaffected. The
hook also adds a read of ``/proc`` per escalation, nothing to a run that
never escalates; the counts it produced are recorded in the checks reference.
These are runner-CPU observations on whatever machine runs them, never Frame
measurements.
"""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
STRESS_NG = ["stress-ng", "--cpu", "8", "--cpu-method", "matrixprod"]
BUSY_LOOPS = 8
PROBE_DIRECTORY_VARIABLE = "SIGHTCIRCUIT_STRESS_PROBE_DIR"

# Installed as sitecustomize.py in a directory put first on PYTHONPATH, so every Python process
# of a run (the test process, the workers it spawns, a guardian) imports it at start-up.
PROBE = r'''
"""Stress probe (tests/stress_supervision.py --probe): dump a worker the primitive is about to kill."""
import faulthandler
import importlib.abc
import os
import signal
import sys
import time

TARGET = "sightcircuit.runtime.supervision"
LOG_DIR = os.environ.get("SIGHTCIRCUIT_STRESS_PROBE_DIR")
STATUS_FIELDS = ("State", "Threads", "SigQ", "SigPnd", "ShdPnd", "SigBlk", "SigIgn", "SigCgt")


def members_of(pgid):
    found = []
    for entry in os.listdir("/proc"):
        if entry.isdigit():
            try:
                with open(f"/proc/{entry}/stat") as handle:
                    fields = handle.read().rsplit(")", 1)[1].split()
                if int(fields[2]) == pgid:  # stat field 5, the process group
                    found.append(entry)
            except (OSError, ValueError, IndexError):
                pass
    return found


def dump(pid, why):
    lines = [f"--- {why} pid={pid} at {time.time():.6f} in process {os.getpid()} ({sys.argv[:3]})"]
    for name in ("status", "wchan", "syscall"):
        try:
            with open(f"/proc/{pid}/{name}") as handle:
                text = handle.read()
        except OSError as error:
            text = f"<{error}>"
        if name == "status":
            text = "\n".join(line for line in text.splitlines() if line.split(":")[0] in STATUS_FIELDS)
        lines.append(f"[{name}] {text.strip()}")
    members = members_of(pid)
    lines.append(f"[group members] {members}")
    for member in members:
        try:
            os.kill(int(member), signal.SIGUSR1)  # faulthandler below: the member writes its own traceback.
        except (OSError, ValueError) as error:
            lines.append(f"[faulthandler {member}] <{error}>")
    time.sleep(0.3)
    with open(os.path.join(LOG_DIR, f"dump-{os.getpid()}-{pid}.txt"), "a") as handle:
        handle.write("\n".join(lines) + "\n")


def patch(module):
    original = module.OwnedGroup.terminate

    def terminate(self, grace=module.GRACE):
        if self.process.poll() is None:
            dump(self.process.pid, "before OwnedGroup.terminate (cooperative grace spent, worker still alive)")
        return original(self, grace)
    module.OwnedGroup.terminate = terminate


class Loader(importlib.abc.Loader):
    def __init__(self, inner):
        self.inner = inner

    def create_module(self, spec):
        return self.inner.create_module(spec) if hasattr(self.inner, "create_module") else None

    def exec_module(self, module):
        self.inner.exec_module(module)
        patch(module)


class Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if name != TARGET:
            return None
        for finder in sys.meta_path:
            if finder is self or not hasattr(finder, "find_spec"):
                continue
            spec = finder.find_spec(name, path, target)
            if spec is not None:
                spec.loader = Loader(spec.loader)
                return spec
        return None


if LOG_DIR:
    sys.meta_path.insert(0, Finder())
    _traceback_file = open(os.path.join(LOG_DIR, f"tb-{os.getpid()}.txt"), "w")
    faulthandler.register(signal.SIGUSR1, file=_traceback_file, all_threads=True, chain=False)
'''


def start_stress(kind):
    """The load's processes, or none."""
    if kind == "none":
        return []
    if kind == "stress-ng":
        if shutil.which("stress-ng") is None:
            sys.exit("stress-ng is not installed: install it (apt-get install stress-ng) or pass --stress python")
        return [subprocess.Popen(STRESS_NG, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)]
    busy = "while True:\n    pass\n"
    return [subprocess.Popen([sys.executable, "-c", busy]) for _ in range(BUSY_LOOPS)]


def stop_stress(processes):
    for process in processes:
        process.kill()
    for process in processes:
        process.wait()


def one_run(test_ids, run_dir, probe_dir, timeout):
    """``(returncode, seconds)``; the run's output is kept only when it is red or the probe wrote something."""
    run_dir.mkdir(parents=True)
    environment = dict(os.environ)
    if probe_dir is not None:
        environment["PYTHONPATH"] = os.pathsep.join([str(probe_dir), *filter(None, [environment.get("PYTHONPATH")])])
        environment[PROBE_DIRECTORY_VARIABLE] = str(run_dir)
    started = time.monotonic()
    with open(run_dir / "unittest.txt", "w") as output:
        try:
            code = subprocess.run([sys.executable, "-m", "unittest", *test_ids], cwd=ROOT, env=environment,
                                  stdout=output, stderr=subprocess.STDOUT, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            code = 124
    seconds = time.monotonic() - started
    for traceback in run_dir.glob("tb-*.txt"):
        if traceback.stat().st_size == 0:
            traceback.unlink()
    if code == 0 and not any(run_dir.glob("dump-*.txt")):
        shutil.rmtree(run_dir)
    return code, seconds


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("test_id", nargs="+", help="unittest ids, as 'python -m unittest' takes them")
    parser.add_argument("--runs", type=int, default=50, help="how many times to run the tests (default 50)")
    parser.add_argument("--stress", choices=("stress-ng", "python", "none"), default="stress-ng",
                        help="the load: stress-ng --cpu 8 --cpu-method matrixprod (default), 8 Python busy loops, or none")
    parser.add_argument("--probe", action="store_true", help="dump a worker the primitive is about to kill")
    parser.add_argument("--timeout", type=float, default=300, help="seconds one run may take (default 300)")
    parser.add_argument("--out", type=Path, help="where to keep red runs and probe output")
    args = parser.parse_args(argv)
    out = args.out or ROOT / "artifacts" / "stress-supervision" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out.mkdir(parents=True, exist_ok=True)
    probe_dir = None
    if args.probe:
        probe_dir = Path(tempfile.mkdtemp(prefix="sightcircuit-stress-probe-"))
        (probe_dir / "sitecustomize.py").write_text(PROBE)
    stress = start_stress(args.stress)
    red = []
    try:
        time.sleep(1 if stress else 0)
        with open(out / "summary.txt", "a") as summary:
            for number in range(1, args.runs + 1):
                code, seconds = one_run(args.test_id, out / f"run-{number}", probe_dir, args.timeout)
                line = f"run {number} rc={code} seconds={seconds:.2f}"
                summary.write(line + "\n")
                summary.flush()
                if code != 0:
                    red.append(number)
                    print(line, "RED", flush=True)
    finally:
        stop_stress(stress)
        if probe_dir is not None:
            shutil.rmtree(probe_dir, ignore_errors=True)
    verdict = (f"{len(red)} red in {args.runs} runs under {args.stress}"
               f"{' with probe' if args.probe else ''}: {red if red else 'none'}; kept under {out}")
    print(verdict)
    with open(out / "summary.txt", "a") as summary:
        summary.write(verdict + "\n")
    return 1 if red else 0


if __name__ == "__main__":
    sys.exit(main())
