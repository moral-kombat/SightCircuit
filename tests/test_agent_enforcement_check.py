"""The offline enforcement check: its matrix covers every guard refusal, passes both ways, and reports counts only."""
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sightcircuit.agent import guard  # noqa: E402
from sightcircuit.agent.tasks import TASKS  # noqa: E402
from sightcircuit.commands import agent_enforcement_check as check  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs/guides/agent-enforcement-test.md"
# Every refusal reason the guard can give, as the fragment the matrix must expect at least once.
GUARD_REASONS = ("empty or malformed command", "shell syntax", "only allowed command", "plain task IDs or words",
                 "missing or malformed path", "unresolvable path", "outside the checkout", "stay on Frame",
                 "images and videos", "missing file path", "may only write", "missing or malformed pattern",
                 "contain '..'", "may not contain '\\' or end in '/'", "network tools", "malformed hook input",
                 *(reason for _, reason in guard.KNOWN_UNSAFE))


class MatrixTests(unittest.TestCase):
    def test_every_refusal_reason_and_every_refused_task_id_has_a_case(self):
        fragments = [case.reason for case in check.MATRIX if case.expect == check.BLOCK]
        for reason in GUARD_REASONS:
            with self.subTest(reason=reason):
                self.assertTrue(any(fragment and fragment in reason for fragment in fragments), reason)
        refused = {case.task for case in check.MATRIX if case.task and case.expect == check.BLOCK}
        self.assertEqual(refused - {"tier-c/not-a-task"}, {task.id for task in TASKS if not task.runnable})
        self.assertTrue({case.group for case in check.MATRIX} == set(check.GROUPS))

    def test_ids_are_unique_and_every_tool_the_hook_matches_is_exercised(self):
        ids = [case.id for case in check.MATRIX]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(re.fullmatch(r"[a-z0-9-]+", case_id) for case_id in ids), "ids carry no paths")
        tools = {case.tool for case in check.MATRIX if case.tool}
        self.assertEqual(tools, guard.SHELL_TOOLS | guard.READ_TOOLS | guard.WRITE_TOOLS | guard.BLOCKED_TOOLS)
        self.assertGreaterEqual(sum(case.expect == check.ALLOW for case in check.MATRIX), 10)

    def test_the_matrix_passes_through_the_imported_guard_and_through_the_hook(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, outside = check.stage(Path(temporary))
            lock = root / "artifacts/agent-runs/.lock"
            before = (lock.read_text(), lock.stat().st_mtime_ns)
            for hook in (False, True):
                with self.subTest(through_hook=hook):
                    outcomes = check.evaluate(root, outside, hook=hook)
                    self.assertEqual([outcome["id"] for outcome in outcomes if not outcome["ok"]], [])
                    self.assertEqual(len(outcomes), len(check.MATRIX))
            # The runner refusals made no run and never took the lock: the staged lock file (a Write case names it)
            # is untouched, and no listing file appeared in the staged checkout.
            self.assertEqual(sorted(path.name for path in (root / "artifacts/agent-runs").iterdir()),
                             [".lock", "20261102T120000Z-tier-a", "zz-forged"])
            self.assertEqual((lock.read_text(), lock.stat().st_mtime_ns), before)

    def test_a_laxer_guard_is_caught(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, outside = check.stage(Path(temporary))
            original = guard.check_bash
            try:
                guard.check_bash = lambda command: (0, "")  # A guard that lets every command through.
                failed = [outcome["id"] for outcome in check.evaluate(root, outside) if not outcome["ok"]]
            finally:
                guard.check_bash = original
        self.assertIn("shell-chain-semicolon", failed)
        self.assertIn("unsafe-git-push", failed)
        self.assertNotIn("allow-runner-list", failed)


class ReportTests(unittest.TestCase):
    def run_main(self, *argv):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = check.main(list(argv))
        return code, stdout.getvalue()

    def test_the_report_is_counts_and_fixed_ids_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "report.json"
            code, text = self.run_main("--json", str(target))
            self.assertEqual(code, 0, text)
            report = json.loads(text)
            self.assertEqual(json.loads(target.read_text()), report)
        self.assertEqual((report["report_kind"], report["schema_version"], report["mode"], report["all_passed"]),
                         (check.REPORT_KIND, check.SCHEMA_VERSION, "decide", True))
        self.assertEqual(report["guard_version"], guard.GUARD_VERSION)
        self.assertEqual((report["cases"], report["failed"], report["failed_case_ids"]), (len(check.MATRIX), 0, []))
        self.assertEqual(report["positive_control_task"], "tier-a/inventory")
        self.assertEqual(sum(group["cases"] for group in report["groups"].values()), report["cases"])
        for group in report["groups"].values():
            self.assertEqual(set(group), {"cases", "expected_block", "observed_block", "passed"})
        # No staged path, home directory, message or tool input reaches the report.
        self.assertNotIn("/", json.dumps({key: value for key, value in report.items() if key != "positive_control_task"}))
        self.assertNotIn("Blocked by", text)

    def test_a_failing_case_is_named_and_exits_one(self):
        original = guard.check_glob
        try:
            guard.check_glob = lambda pattern, path, root: (0, "")
            code, text = self.run_main()
        finally:
            guard.check_glob = original
        report = json.loads(text)
        self.assertEqual(code, 1)
        self.assertFalse(report["all_passed"])
        self.assertIn("glob-dotdot", report["failed_case_ids"])
        self.assertEqual(report["groups"]["glob"]["passed"], 0)

    def test_the_matrix_listing_and_the_usage_error(self):
        code, text = self.run_main("--matrix")
        self.assertEqual(code, 0)
        listing = json.loads(text)
        self.assertEqual([entry["id"] for entry in listing], [case.id for case in check.MATRIX])
        self.assertTrue(all("tool" in entry or "task" in entry for entry in listing))
        with redirect_stderr(io.StringIO()):
            self.assertEqual(check.main(["--bogus"]), 2)

    def test_the_wrapper_runs_from_the_checkout(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/agent_enforcement_check.py"), "--matrix"],
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)), len(check.MATRIX))


class GuideTests(unittest.TestCase):
    """The guide names what the check reports: every group, the positive control and the precondition."""

    def test_the_guide_names_every_group_and_the_fixed_strings_the_operator_looks_for(self):
        text = GUIDE.read_text()
        for group in check.GROUPS:
            with self.subTest(group=group):
                self.assertTrue(f"`{group}`" in text, f"the guide no longer names the group {group}")
        for needle in (check.POSITIVE_CONTROL_TASK, "Ignoring", "permissions.allow", "claude doctor",
                       "scripts/agent_enforcement_check.py --through-hook", "Blocked by sightcircuit agent guard",
                       '"status": "refused"', check.REPORT_KIND, "summary.md", "No transcript text"):
            with self.subTest(needle=needle):
                self.assertTrue(needle in text, f"the guide no longer says {needle!r}")
        for task in TASKS:
            if not task.runnable:
                with self.subTest(task=task.id):
                    self.assertTrue(task.id in text, f"the guide no longer names {task.id}")


if __name__ == "__main__":
    unittest.main()
