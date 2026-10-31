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
                     "architecture_capabilities": ["An area"]}
        for entry in self.index["entries"]:
            (evidence / entry["file"]).write_text(f"# {entry['id']}: {entry['title']}\n\nUnknown revision.\n")
        (self.root / "docs/architecture.md").write_text(
            "# Untouched before\n\n<!-- BEGIN progress-summary: old -->\nold\n"
            "<!-- END progress-summary -->\n\n## Untouched after\n")
        (self.root / "docs/status.md").write_text("old status\n")
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

    def test_metadata_order_does_not_reorder_historical_headings(self):
        self.index["entries"].reverse()
        self.write_sources()
        output = generate_status.generated_files(self.root)[self.root / "docs/status.md"]
        self.assertLess(output.index("### E1:"), output.index("### E2:"))
        self.assertIn("[Full evidence](evidence/e001.md). Device report.", output)

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


if __name__ == "__main__":
    unittest.main()
