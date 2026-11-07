"""Run test modules and fail if a test in a pinned module is skipped.

CI uses this for jobs whose environment is meant to satisfy every prerequisite,
so a broken setup step cannot turn into a green run of skips.

    run_tests_no_skips.py tests/test_x.py [...]
        Run only the named modules; no skip is allowed.
    run_tests_no_skips.py --all tests/test_x.py [...]
        Run every module under tests/ once; the named modules allow no skip,
        the others may skip (a bare environment skips what it cannot run).
    run_tests_no_skips.py --durations FILE ...
        Also record the wall time of every test module in FILE (JSON) and
        print the modules, slowest first, so a suite time budget has data.

``--all`` lets a job run the whole suite one time and still pin the modules
its setup steps are supposed to satisfy completely.

This is the suite's entry point, so it also normalises what the suite's
children inherit: a test process started with SIGINT ignored (a background
job of a shell without job control) would pass that ignore on to every
subprocess the tests signal, and the subtests that send SIGINT would then
depend on how the suite was started. Before the run, an ignored SIGINT is
replaced by Python's default handler, which children do not inherit, so each
child starts with the default disposition as it would from a terminal.
"""
import argparse
import json
from pathlib import Path
import signal
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


def module_name(test):
    """The file stem of the module a test case, subtest or loader placeholder came from."""
    case = getattr(test, "test_case", test)  # a subtest wraps its case
    return type(case).__module__.rsplit(".", 1)[-1]


def normalise_sigint():
    """Give the suite's children the default SIGINT disposition; True if SIGINT was ignored before."""
    if signal.getsignal(signal.SIGINT) is not signal.SIG_IGN:
        return False
    signal.signal(signal.SIGINT, signal.default_int_handler)
    return True


class ModuleDurations:
    """Wall time per test module, measured between consecutive test ends.

    A module's time is the sum, over its tests, of the time from the previous
    test's end (or the run's start) to the test's end, so class and module
    fixtures count with the tests they serve; what follows the last test goes
    to the last module. ``tests`` counts tests run (subtests are not tests).
    """

    def __init__(self):
        self.modules = {}
        self.started = self.last = time.monotonic()
        self.current = None

    def test_ended(self, test):
        now = time.monotonic()
        entry = self.modules.setdefault(module_name(test), {"seconds": 0.0, "tests": 0})
        entry["seconds"] += now - self.last
        entry["tests"] += 1
        self.current, self.last = module_name(test), now

    def run_ended(self):
        now = time.monotonic()
        if self.current is not None:
            self.modules[self.current]["seconds"] += now - self.last
        self.last = now

    def report(self):
        ordered = sorted(self.modules.items(), key=lambda item: (-item[1]["seconds"], item[0]))
        return {"total_seconds": round(self.last - self.started, 3),
                "modules": {name: {"seconds": round(entry["seconds"], 3), "tests": entry["tests"]}
                            for name, entry in ordered}}

    def write(self, path, stream):
        report = self.report()
        Path(path).write_text(json.dumps(report, indent=2) + "\n")
        print(f"Module wall times, slowest first ({report['total_seconds']} s in all), written to {path}:",
              file=stream)
        for name, entry in report["modules"].items():
            print(f"  {entry['seconds']:9.3f} s  {entry['tests']:4d} tests  {name}", file=stream)


def timed_result_class(durations):
    class TimedResult(unittest.TextTestResult):
        def stopTest(self, test):
            super().stopTest(test)
            durations.test_ended(test)

        def stopTestRun(self):
            super().stopTestRun()
            durations.run_ended()
    return TimedResult


def run(modules, all_tests=False, tests_dir=ROOT / "tests", stream=None, durations=None):
    """Run the suite and return the exit code: 0 only if every test passed and no pinned module skipped.

    ``durations`` names a file for the per-module wall times (JSON); they are
    also printed to the error stream.
    """
    errors = stream or sys.stderr
    if normalise_sigint():
        print("SIGINT was ignored when the suite started; children now start with the default handler.",
              file=errors)
    loader = unittest.TestLoader()
    if all_tests:
        suite = loader.discover(str(tests_dir))
    else:
        suite = unittest.TestSuite()
        for module in modules:
            suite.addTests(loader.discover(str(tests_dir), pattern=Path(module).name))
    timings = ModuleDurations() if durations is not None else None
    runner = unittest.TextTestRunner(stream=stream, verbosity=2,
                                     resultclass=timed_result_class(timings) if timings else None)
    result = runner.run(suite)
    if timings is not None:
        timings.write(durations, errors)
    pinned = {Path(module).stem for module in modules}
    disallowed = [(test, reason) for test, reason in result.skipped if module_name(test) in pinned]
    if all_tests and len(result.skipped) > len(disallowed):
        print(f"{len(result.skipped) - len(disallowed)} test(s) skipped outside the pinned modules.", file=errors)
    if disallowed:
        print(f"{len(disallowed)} test(s) skipped where none are allowed:", file=errors)
        for test, reason in disallowed:
            print(f"  {test}: {reason}", file=errors)
    return 0 if result.wasSuccessful() and not disallowed else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--all", action="store_true", help="run every test module; only the named ones may not skip")
    parser.add_argument("--durations", metavar="FILE", help="write each module's wall time (JSON) and print them")
    parser.add_argument("modules", nargs="+", metavar="tests/test_x.py", help="modules in which no test may skip")
    arguments = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    return run(arguments.modules, all_tests=arguments.all, durations=arguments.durations)


if __name__ == "__main__":
    sys.exit(main())
