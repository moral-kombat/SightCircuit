"""Run test modules and fail if a test in a pinned module is skipped.

CI uses this for jobs whose environment is meant to satisfy every prerequisite,
so a broken setup step cannot turn into a green run of skips.

    run_tests_no_skips.py tests/test_x.py [...]
        Run only the named modules; no skip is allowed.
    run_tests_no_skips.py --all tests/test_x.py [...]
        Run every module under tests/ once; the named modules allow no skip,
        the others may skip (a bare environment skips what it cannot run).

``--all`` lets a job run the whole suite one time and still pin the modules
its setup steps are supposed to satisfy completely.
"""
import argparse
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


def module_name(test):
    """The file stem of the module a test case, subtest or loader placeholder came from."""
    case = getattr(test, "test_case", test)  # a subtest wraps its case
    return type(case).__module__.rsplit(".", 1)[-1]


def run(modules, all_tests=False, tests_dir=ROOT / "tests", stream=None):
    """Run the suite and return the exit code: 0 only if every test passed and no pinned module skipped."""
    loader = unittest.TestLoader()
    if all_tests:
        suite = loader.discover(str(tests_dir))
    else:
        suite = unittest.TestSuite()
        for module in modules:
            suite.addTests(loader.discover(str(tests_dir), pattern=Path(module).name))
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    pinned = {Path(module).stem for module in modules}
    disallowed = [(test, reason) for test, reason in result.skipped if module_name(test) in pinned]
    errors = stream or sys.stderr
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
    parser.add_argument("modules", nargs="+", metavar="tests/test_x.py", help="modules in which no test may skip")
    arguments = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    return run(arguments.modules, all_tests=arguments.all)


if __name__ == "__main__":
    sys.exit(main())
