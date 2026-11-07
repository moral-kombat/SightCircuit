"""The no-skip test runner CI uses: named modules may not skip; --all runs the rest too."""
import io
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import uuid

from scripts import run_tests_no_skips as runner

ROOT = Path(__file__).resolve().parents[1]

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

    def test_durations_record_every_module_with_its_test_count_slowest_first(self):
        stream = io.StringIO()
        path = self.tests_dir / "durations.json"
        code = runner.run([f"tests/{self.clean}"], all_tests=True, tests_dir=self.tests_dir, stream=stream,
                          durations=str(path))
        self.assertEqual(code, 0, stream.getvalue())
        report = json.loads(path.read_text())
        modules = report["modules"]
        self.assertEqual(set(modules), {self.clean[:-3], self.skipping[:-3]})
        self.assertEqual(modules[self.clean[:-3]]["tests"], 1)
        self.assertEqual(modules[self.skipping[:-3]]["tests"], 2)  # A skipped test still ran through the result.
        seconds = [entry["seconds"] for entry in modules.values()]
        self.assertEqual(seconds, sorted(seconds, reverse=True))
        self.assertTrue(all(value >= 0 for value in seconds))
        self.assertGreaterEqual(report["total_seconds"], round(sum(seconds), 3) - 0.002)
        output = stream.getvalue()
        self.assertIn("slowest first", output)
        for name in modules:
            self.assertRegex(output, rf"\d+\.\d{{3}} s +\d+ tests +{name}")

    def test_without_durations_nothing_is_written(self):
        code, output = self.run_runner([f"tests/{self.clean}"])
        self.assertEqual(code, 0, output)
        self.assertNotIn("slowest first", output)
        self.assertEqual(sorted(path.name for path in self.tests_dir.iterdir() if path.is_file()),
                         sorted(self.written))

    def test_an_ignored_sigint_is_replaced_by_the_default_handler(self):
        previous = signal.getsignal(signal.SIGINT)
        self.addCleanup(signal.signal, signal.SIGINT, previous)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        self.assertTrue(runner.normalise_sigint())
        self.assertIs(signal.getsignal(signal.SIGINT), signal.default_int_handler)
        self.assertFalse(runner.normalise_sigint())  # Nothing to normalise a second time.
        self.assertIs(signal.getsignal(signal.SIGINT), signal.default_int_handler)

    def test_a_suite_started_with_sigint_ignored_gives_its_children_the_default_disposition(self):
        # A background job of a shell without job control starts with SIGINT ignored, and ignored
        # signals survive exec: without the entry point's normalisation, every child the tests
        # signal would inherit the ignore and the SIGINT subtests would depend on how the suite started.
        child = "import signal, sys; sys.exit(0 if signal.getsignal(signal.SIGINT) is signal.default_int_handler else 3)"
        probe = (
            "import io, signal, subprocess, sys\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "from scripts import run_tests_no_skips as runner\n"
            "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
            f"before = subprocess.run([sys.executable, '-c', {child!r}]).returncode\n"
            "stream = io.StringIO()\n"
            "code = runner.run(['tests/' + sys.argv[3]], tests_dir=sys.argv[2], stream=stream)\n"
            f"after = subprocess.run([sys.executable, '-c', {child!r}]).returncode\n"
            "print(before, code, after, 'SIGINT was ignored when the suite started' in stream.getvalue())\n"
        )
        completed = subprocess.run([sys.executable, "-c", probe, str(ROOT), str(self.tests_dir), self.clean],
                                   capture_output=True, text=True, timeout=60)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        # The child inherited the ignore before the run (exit 3) and the default handler after it (exit 0).
        self.assertEqual(completed.stdout.split(), ["3", "0", "0", "True"])

    def test_a_module_is_required(self):
        with self.assertRaises(SystemExit) as stop:
            with mock.patch("sys.stderr", io.StringIO()):
                runner.main([])
        self.assertEqual(stop.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
