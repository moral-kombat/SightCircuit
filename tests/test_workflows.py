"""Pin the CI layout that keeps the GitHub Actions minutes down (2026-11-07).

checks.yml (x86_64) runs on every pull request and main push; arm64.yml on
main pushes and on demand only; a newer push cancels a pull request's
superseded run. A documentation-only change skips both and runs docs.yml,
which checks the generated summaries. The three workflows must agree on what
"documentation-only" means, and no path on that list may be an input to a test
module (other than the status generator's tests, which docs.yml runs), or a
documentation change could break a test that CI never ran. The no-skip lists
in check-jobs.yml must name modules that exist, or a renamed module would
leave its gate vacuous.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"
# Fixture strings in these modules name a documentation path without reading the document.
FIXTURE_MENTIONS = {("test_frame_agent.py", "docs/status.md")}


def quoted_lists(text, key):
    """Every list of single-quoted items that follows a ``key:`` line, in file order."""
    lines = text.splitlines()
    lists = []
    for number, line in enumerate(lines):
        match = re.fullmatch(r"(\s*)" + re.escape(key) + r":\s*", line)
        if not match:
            continue
        indent = len(match.group(1))
        items = []
        for following in lines[number + 1:]:
            item = re.fullmatch(r"(\s*)- '([^']+)'\s*(#.*)?", following)
            if item and len(item.group(1)) > indent:
                items.append(item.group(2))
            elif following.strip() and not following.lstrip().startswith("#"):
                break
        lists.append(items)
    return lists


class DocumentationOnlyPaths(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checks = (WORKFLOWS / "checks.yml").read_text()
        cls.arm64 = (WORKFLOWS / "arm64.yml").read_text()
        cls.docs = (WORKFLOWS / "docs.yml").read_text()
        cls.reference = quoted_lists(cls.docs, "paths")[0]

    def test_the_three_workflows_agree_on_the_list(self):
        ignored = quoted_lists(self.checks, "paths-ignore") + quoted_lists(self.arm64, "paths-ignore")
        checked = quoted_lists(self.docs, "paths")
        self.assertEqual(len(ignored), 3)  # checks.yml push and pull_request, arm64.yml push
        self.assertEqual(len(checked), 2)  # docs.yml push and pull_request
        self.assertGreater(len(self.reference), 0)
        self.assertEqual(len(set(self.reference)), len(self.reference))
        for found in ignored + checked:
            self.assertEqual(found, self.reference)

    def test_the_generated_summaries_and_their_inputs_are_on_the_list(self):
        for path in ("docs/evidence/**", "docs/status.md", "docs/status-data.json", "docs/architecture.md"):
            self.assertIn(path, self.reference)

    def test_no_test_module_reads_a_documentation_only_path(self):
        entries = [entry.removesuffix("/**") for entry in self.reference if entry.startswith("docs/")]
        for module in sorted(ROOT.glob("tests/test_*.py")):
            if module.name in ("test_generate_status.py", Path(__file__).name):
                continue
            text = module.read_text()
            for entry in entries:
                if (module.name, entry) in FIXTURE_MENTIONS:
                    continue
                with self.subTest(module=module.name, path=entry):
                    self.assertNotIn(entry, text, f"{module.name} names {entry}, which CI treats as documentation-only")


class Triggers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.texts = {name: (WORKFLOWS / name).read_text() for name in ("checks.yml", "arm64.yml", "docs.yml")}

    def test_arm64_runs_on_main_pushes_and_on_demand_only(self):
        triggers = self.texts["arm64.yml"].split("\njobs:", 1)[0]
        self.assertIn("\n  push:\n    branches: [main]\n", triggers)
        self.assertIn("\n  workflow_dispatch:\n", triggers)
        self.assertNotIn("pull_request", triggers)

    def test_x86_64_runs_on_pull_requests(self):
        triggers = self.texts["checks.yml"].split("\njobs:", 1)[0]
        self.assertIn("\n  pull_request:\n", triggers)
        self.assertIn("\n  push:\n    branches: [main]\n", triggers)

    def test_every_workflow_cancels_a_superseded_run(self):
        for name, text in self.texts.items():
            with self.subTest(workflow=name):
                self.assertIn("\nconcurrency:\n", text)
                self.assertIn("cancel-in-progress: true", text)

    def test_the_architecture_workflows_share_the_check_jobs(self):
        for name in ("checks.yml", "arm64.yml"):
            self.assertIn("uses: ./.github/workflows/check-jobs.yml", self.texts[name])


class NoSkipLists(unittest.TestCase):
    def test_every_pinned_module_exists(self):
        text = (WORKFLOWS / "check-jobs.yml").read_text()
        pinned = sorted(set(re.findall(r"tests/test_[a-z0-9_]+\.py", text)))
        self.assertGreater(len(pinned), 40)
        for module in pinned:
            with self.subTest(module=module):
                self.assertTrue((ROOT / module).is_file(), f"{module} is pinned in check-jobs.yml but absent")


if __name__ == "__main__":
    unittest.main()
