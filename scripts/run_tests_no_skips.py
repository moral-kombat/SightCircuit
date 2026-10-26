"""Run the named test modules and fail if any test is skipped.

CI uses this for jobs whose environment is meant to satisfy every prerequisite,
so a broken setup step cannot turn into a green run of skips.
"""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


def main(modules):
    if not modules:
        sys.exit("usage: run_tests_no_skips.py tests/test_x.py [...]")
    sys.path.insert(0, str(ROOT))
    suite = unittest.TestSuite()
    for module in modules:
        path = Path(module)
        suite.addTests(unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern=path.name))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.skipped:
        print(f"{len(result.skipped)} test(s) skipped where none are allowed:", file=sys.stderr)
        for test, reason in result.skipped:
            print(f"  {test}: {reason}", file=sys.stderr)
    sys.exit(0 if result.wasSuccessful() and not result.skipped else 1)


if __name__ == "__main__":
    main(sys.argv[1:])
