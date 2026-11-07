"""Behavioral checks for evidence integrity and generated-summary maintenance."""
from __future__ import annotations

import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest

from scripts import generate_status


class EvidenceGenerationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        evidence = self.root / "docs/evidence"
        evidence.mkdir(parents=True)
        self.index = {"schema_version": 1, "entries": [
            {"id": "E1", "title": "A reported failure", "file": "e001.md",
             "kind": "device_report", "reconstructed": False},
            {"id": "E2", "title": "An offline check", "file": "e002.md",
             "kind": "offline", "reconstructed": False},
        ]}
        self.data = {"schema_version": 1, "review_date": "2026-10-31",
                     "capabilities": [{"capability": "An area", "state": "Blocked",
                                       "evidence": "[E1](#e1-a-reported-failure)"}],
                     "capability_groups": [{"title": "Active POC", "capabilities": ["An area"]}],
                     "architecture_capabilities": ["An area"]}
        for entry in self.index["entries"]:
            (evidence / entry["file"]).write_text(f"# {entry['id']}: {entry['title']}\n\nUnknown revision.\n")
        (self.root / "docs/architecture.md").write_text(
            "# Untouched before\n\n<!-- BEGIN progress-summary: old -->\nold\n"
            "<!-- END progress-summary -->\n\n## Untouched after\n")
        (self.root / "docs/status.md").write_text("old status\n")
        (evidence / "README.md").write_text(
            "# Evidence guidance before\n\n<!-- BEGIN evidence-catalog: old -->\nold\n"
            "<!-- END evidence-catalog -->\n\n## Maintenance after\n")
        self.write_sources()

    def write_sources(self):
        (self.root / "docs/evidence/index.json").write_text(json.dumps(self.index))
        (self.root / "docs/status-data.json").write_text(json.dumps(self.data))

    def test_check_detects_stale_outputs_and_generation_is_repeatable(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(generate_status.main(["--root", str(self.root), "--check"]), 1)
            self.assertEqual(generate_status.main(["--root", str(self.root)]), 0)
            first = {path: path.read_bytes() for path in generate_status.generated_files(self.root)}
            self.assertEqual(generate_status.main(["--root", str(self.root), "--check"]), 0)
            self.assertEqual(generate_status.main(["--root", str(self.root)]), 0)
        self.assertEqual(first, {path: path.read_bytes() for path in first})
        architecture = (self.root / "docs/architecture.md").read_text()
        self.assertTrue(architecture.startswith("# Untouched before\n\n"))
        self.assertTrue(architecture.endswith("\n\n## Untouched after\n"))
        self.assertIn("](status.md#e1-a-reported-failure)", architecture)
        catalog = (self.root / "docs/evidence/README.md").read_text()
        self.assertTrue(catalog.startswith("# Evidence guidance before\n\n"))
        self.assertTrue(catalog.endswith("\n\n## Maintenance after\n"))

    def test_metadata_order_does_not_reorder_historical_headings(self):
        self.index["entries"].reverse()
        self.write_sources()
        output = generate_status.generated_files(self.root)[self.root / "docs/status.md"]
        self.assertLess(output.index("### E1:"), output.index("### E2:"))
        self.assertIn("[Full evidence](evidence/e001.md). Device report.", output)
        catalog = generate_status.generated_files(self.root)[self.root / "docs/evidence/README.md"]
        self.assertLess(catalog.index("[E2](e002.md)"), catalog.index("[E1](e001.md)"))
        self.assertIn("| [E1](e001.md) | A reported failure | Device report |", catalog)

    def test_group_order_controls_tables_without_losing_or_duplicating_capabilities(self):
        self.data["capabilities"].append({"capability": "A second area", "state": "Deferred",
                                          "evidence": "[E2](#e2-an-offline-check)"})
        self.data["capability_groups"] = [
            {"title": "First group", "capabilities": ["A second area"]},
            {"title": "Second group", "capabilities": ["An area"]},
        ]
        self.write_sources()
        output = generate_status.generated_files(self.root)[self.root / "docs/status.md"]
        self.assertLess(output.index("### First group"), output.index("| A second area |"))
        self.assertLess(output.index("| A second area |"), output.index("### Second group"))
        self.assertLess(output.index("### Second group"), output.index("| An area |"))
        self.assertEqual(output.count("| A second area |"), 1)
        self.assertEqual(output.count("| An area |"), 1)

    def test_groups_reject_missing_duplicate_and_unknown_members(self):
        self.data["capabilities"].append({"capability": "A second area", "state": "Deferred",
                                          "evidence": "[E2](#e2-an-offline-check)"})
        invalid_groups = [
            [],
            [{"title": "Active", "capabilities": ["An area"]}],
            [{"title": "Active", "capabilities": ["An area", "A second area", "An area"]}],
            [{"title": "Active", "capabilities": ["An area", "A second area"]},
             {"title": "Other", "capabilities": ["An area"]}],
            [{"title": "Active", "capabilities": ["An area", "Unknown"]}],
            [{"title": "Repeated", "capabilities": ["An area"]},
             {"title": "Repeated", "capabilities": ["A second area"]}],
            [{"title": "Active", "capabilities": []}],
            [{"title": "Active\nInjected heading", "capabilities": ["An area", "A second area"]}],
        ]
        for groups in invalid_groups:
            with self.subTest(groups=groups):
                self.data["capability_groups"] = groups
                self.write_sources()
                with self.assertRaisesRegex(ValueError, "capability group"):
                    generate_status.generated_files(self.root)

    def test_catalog_freshness_is_checked_and_provenance_is_preserved(self):
        self.index["entries"][1].update(kind="mixed", reconstructed=True)
        self.write_sources()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(generate_status.main(["--root", str(self.root)]), 0)
        path = self.root / "docs/evidence/README.md"
        self.assertIn("Mixed source record; reconstructed, original unknowns preserved", path.read_text())
        path.write_text(path.read_text().replace("[E2](e002.md)", "[E2](wrong.md)"))
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(generate_status.main(["--root", str(self.root), "--check"]), 1)
        self.assertEqual(output.getvalue(), "stale generated summary: docs/evidence/README.md\n")

    def test_invalid_identity_paths_and_unknown_references_are_rejected(self):
        original = copy.deepcopy(self.index)
        for key, value in (("id", "E1"), ("file", "../outside.md"), ("reconstructed", "yes")):
            with self.subTest(key=key):
                self.index = copy.deepcopy(original)
                self.index["entries"][1][key] = value
                self.write_sources()
                with self.assertRaises(ValueError):
                    generate_status.generated_files(self.root)
        self.index = original
        self.data["capabilities"][0]["evidence"] = "[E99](#e99-unknown)"
        self.write_sources()
        with self.assertRaisesRegex(ValueError, "unknown evidence"):
            generate_status.generated_files(self.root)

    def test_missing_history_and_changed_record_heading_fail_closed(self):
        self.index["entries"].pop(0)
        self.write_sources()
        with self.assertRaisesRegex(ValueError, "contiguous"):
            generate_status.generated_files(self.root)
        self.index["entries"][0]["id"] = "E1"
        self.write_sources()
        with self.assertRaisesRegex(ValueError, "heading mismatch"):
            generate_status.generated_files(self.root)

    def test_reconstructed_records_cannot_be_promoted_by_metadata(self):
        entry = self.index["entries"][1]
        entry["id"] = "E63"
        self.write_sources()
        (self.root / "docs/evidence/e002.md").write_text("# E63: An offline check\n")
        with self.assertRaisesRegex(ValueError, "reconstructed provenance"):
            generate_status.generated_files(self.root)

    def test_removing_last_index_entry_cannot_hide_a_record(self):
        self.index["entries"].pop()
        self.write_sources()
        with self.assertRaisesRegex(ValueError, "orphaned records"):
            generate_status.generated_files(self.root)

    def test_multiple_generated_blocks_are_rejected_before_any_write(self):
        path = self.root / "docs/architecture.md"
        path.write_text(path.read_text() + "<!-- BEGIN progress-summary: duplicate -->")
        before = (self.root / "docs/status.md").read_bytes()
        with self.assertRaisesRegex(ValueError, "exactly one"):
            generate_status.generated_files(self.root)
        self.assertEqual((self.root / "docs/status.md").read_bytes(), before)

    def test_catalog_missing_duplicate_and_reversed_markers_fail_before_any_write(self):
        path = self.root / "docs/evidence/README.md"
        original = path.read_text()
        malformed = [
            original.replace("<!-- END evidence-catalog -->", ""),
            original + "<!-- BEGIN evidence-catalog: duplicate -->",
            "<!-- END evidence-catalog -->\n<!-- BEGIN evidence-catalog: reversed -->",
        ]
        before = (self.root / "docs/status.md").read_bytes()
        for content in malformed:
            with self.subTest(content=content):
                path.write_text(content)
                with self.assertRaisesRegex(ValueError, "evidence catalog"):
                    generate_status.generated_files(self.root)
                self.assertEqual((self.root / "docs/status.md").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
