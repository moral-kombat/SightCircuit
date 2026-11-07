"""The no-skip test runner CI uses: named modules may not skip; --all runs the rest too."""
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import uuid

from scripts import run_tests_no_skips as runner

CLEAN = """\
import unittest


class Clean(unittest.TestCase):
    def test_passes(self):
        self.assertTrue(True)
"""

SKIPPING = """\
import unittest


class Skipping(unittest.TestCase):
    def test_passes(self):
        self.assertTrue(True)

    @unittest.skip("needs a headset")
    def test_skipped(self):
        self.fail("never runs")
"""

FAILING = """\
import unittest


class Failing(unittest.TestCase):
    def test_fails(self):
        self.fail("broken")
"""


class NoSkipRunner(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.tests_dir = Path(self.directory.name)
        tag = uuid.uuid4().hex[:8]
        self.clean = f"test_nsk_clean_{tag}.py"
        self.skipping = f"test_nsk_skipping_{tag}.py"
        (self.tests_dir / self.clean).write_text(CLEAN)
        (self.tests_dir / self.skipping).write_text(SKIPPING)
        self.written = [self.clean, self.skipping]

    def tearDown(self):
        for name in self.written:
            sys.modules.pop(name[:-3], None)
        while str(self.tests_dir) in sys.path:
            sys.path.remove(str(self.tests_dir))
        self.directory.cleanup()

    def run_runner(self, modules, all_tests=False):
        stream = io.StringIO()
        code = runner.run(modules, all_tests=all_tests, tests_dir=self.tests_dir, stream=stream)
        return code, stream.getvalue()

    def test_a_named_module_without_skips_passes(self):
        code, output = self.run_runner([f"tests/{self.clean}"])
        self.assertEqual(code, 0, output)
        self.assertIn("test_passes", output)
        self.assertNotIn("test_skipped", output)  # Only the named module ran.

    def test_a_named_module_with_a_skip_fails_and_names_it(self):
        code, output = self.run_runner([f"tests/{self.skipping}"])
        self.assertEqual(code, 1, output)
        self.assertIn("1 test(s) skipped where none are allowed", output)
        self.assertIn("test_skipped", output)
        self.assertIn("needs a headset", output)

    def test_all_runs_every_module_and_tolerates_skips_outside_the_pinned_ones(self):
        code, output = self.run_runner([f"tests/{self.clean}"], all_tests=True)
        self.assertEqual(code, 0, output)
        self.assertIn("test_skipped", output)  # The unpinned module ran too...
        self.assertIn("1 test(s) skipped outside the pinned modules", output)  # ...and its skip is reported.
        self.assertNotIn("where none are allowed", output)

    def test_all_still_fails_a_skip_inside_a_pinned_module(self):
        code, output = self.run_runner([f"tests/{self.clean}", f"tests/{self.skipping}"], all_tests=True)
        self.assertEqual(code, 1, output)
        self.assertIn("1 test(s) skipped where none are allowed", output)
        self.assertNotIn("outside the pinned modules", output)

    def test_all_fails_on_a_failure_in_an_unpinned_module(self):
        failing = f"test_nsk_failing_{uuid.uuid4().hex[:8]}.py"
        (self.tests_dir / failing).write_text(FAILING)
        self.written.append(failing)
        code, output = self.run_runner([f"tests/{self.clean}"], all_tests=True)
        self.assertEqual(code, 1, output)
        self.assertIn("broken", output)

    def test_a_module_is_required(self):
        with self.assertRaises(SystemExit) as stop:
            with mock.patch("sys.stderr", io.StringIO()):
                runner.main([])
        self.assertEqual(stop.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
