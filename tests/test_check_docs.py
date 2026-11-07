"""Offline checks for actual Markdown navigation and compatibility failures."""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts import check_docs


class MarkdownTests(unittest.TestCase):
    def test_heading_fragments_match_github_and_duplicate_suffixes(self):
        text = "# A **clear** `API`!\n## A clear API!\n## A clear API-1\n## Café  déjà_vu\n"
        self.assertEqual(check_docs.anchors(text), {"a-clear-api", "a-clear-api-1", "a-clear-api-1-1",
                                                    "café--déjà_vu"})

    def test_setext_headings_and_explicit_ids_survive_renaming(self):
        text = 'New title\n=========\n\n<a id="old-title"></a>\n\n<section id="section">\n'
        self.assertEqual(check_docs.anchors(text), {"new-title", "old-title", "section"})

    def test_inline_examples_do_not_supply_real_html_anchors(self):
        text = '`<a id="not-real"></a>` and the field id="also-not-real"\n<a name="actual"></a>\n'
        self.assertEqual(check_docs.anchors(text), {"actual"})

    def test_code_and_comments_do_not_invent_links_or_anchors(self):
        text = ('# Visible\n<!-- # Hidden\n[bad](missing.md) -->\n'
                '```markdown\n# Hidden\n[bad](missing.md)\n````\n'
                '~~~\n# Hidden\n[bad](missing.md)\n~~~\n'
                '`[bad](missing.md)` and ``[bad](missing.md)``\n[good](actual.md)\n')
        self.assertEqual(check_docs.anchors(text), {"visible"})
        self.assertEqual([link.target for link in check_docs.links(text)], ["actual.md"])

    def test_balanced_paths_titles_encoded_spaces_and_nested_images(self):
        text = ('[figure](plots/image_(1).png "A title")\n'
                '[space](<a file.md> "Title")\n'
                '[escaped](plots/image_\\(1\\).png)\n'
                '[![thumbnail](thumb.png)](full.png)\n')
        self.assertEqual([link.target for link in check_docs.links(text)],
                         ["plots/image_(1).png", "a file.md", "plots/image_(1).png", "full.png", "thumb.png"])

    def test_reference_links_are_case_and_whitespace_insensitive(self):
        text = ('[visible][Other Name]\n[short][]\n[short]\n![image][picture]\n'
                '[other name]: target.md#section "Title"\n'
                '[SHORT]: https://example.com/a(b)\n[picture]: <a picture.png>\n')
        found = check_docs.links(text)
        self.assertFalse(any(link.missing_reference for link in found))
        self.assertEqual(sum(link.target == "target.md#section" for link in found), 2)
        self.assertEqual(sum(link.target == "https://example.com/a(b)" for link in found), 3)
        self.assertEqual(sum(link.target == "a picture.png" for link in found), 2)

    def test_undefined_full_reference_fails_but_plain_brackets_do_not(self):
        found = check_docs.links("[ordinary prose] and [link][not defined]\n")
        self.assertEqual(found, [check_docs.Link(1, "not defined", True)])


class NavigationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.write("docs/legacy-anchors.json", json.dumps({"schema_version": 1, "anchors": {}}))

    def write(self, filename, text):
        path = self.root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def check(self, *names):
        return check_docs.check(self.root, [self.root / name for name in names])

    def test_valid_local_links_fragments_and_non_markdown_files(self):
        self.write("docs/a file.md", "# Café\n\n<a id='legacy'></a>\n")
        self.write("image.png", "fixture")
        self.write("script.py", "print('fixture')\n")
        self.write("README.md", "\n".join([
            "# Start", "[space](docs/a%20file.md#caf%C3%A9)", "[old](docs/a%20file.md#legacy)",
            "[self](#start)", "[code](script.py#L1)", "![image](/image.png)",
            "[external](https://example.invalid/missing#fragment)", "[mail](mailto:someone@example.invalid)",
            "[network](//example.invalid/path)", "[dir](docs/)",
        ]))
        self.assertEqual(self.check("README.md", "docs/a file.md"), [])

    def test_missing_target_anchor_and_reference_have_file_and_line(self):
        self.write("other.md", "# Existing\n")
        self.write("README.md", "# Start\n[missing](absent.md)\n[wrong](other.md#absent)\n[ref][missing]\n")
        errors = self.check("README.md")
        self.assertEqual(errors, ["README.md:2: missing target: absent.md",
                                  "README.md:3: missing anchor: other.md#absent",
                                  "README.md:4: undefined link reference [missing]"])

    def test_compatibility_checks_unreferenced_old_fragments(self):
        self.write("docs/legacy-anchors.json", json.dumps({"schema_version": 1,
                                                          "anchors": {"README.md": ["old-title"]}}))
        self.write("README.md", "# Renamed\n")
        self.assertEqual(self.check("README.md"), ["README.md: removed compatibility anchor #old-title"])
        self.write("README.md", '# Renamed\n\n<a id="old-title"></a>\n')
        self.assertEqual(self.check("README.md"), [])

    def test_deleted_compatibility_document_is_not_silently_dropped(self):
        self.write("docs/legacy-anchors.json", json.dumps({"schema_version": 1,
                                                          "anchors": {"gone.md": ["old"]}}))
        self.assertEqual(self.check(), ["gone.md: missing compatibility document"])

    def test_local_link_cannot_escape_checkout(self):
        self.write("README.md", "[outside](../outside.md)\n")
        self.assertEqual(self.check("README.md"), ["README.md:1: local link leaves repository: ../outside.md"])

    def test_malformed_manifest_fails_instead_of_disabling_compatibility(self):
        for data in ([], {"schema_version": 2, "anchors": {}},
                     {"schema_version": 1, "anchors": {"../outside.md": []}},
                     {"schema_version": 1, "anchors": {"README.md": ["same", "same"]}}):
            with self.subTest(data=data):
                self.write("docs/legacy-anchors.json", json.dumps(data))
                with self.assertRaises(ValueError):
                    self.check()

    def test_git_discovery_includes_new_guides_but_excludes_ignored_outputs(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True, capture_output=True)
        self.write("README.md", "# Start\n")
        self.write(".gitignore", ".venv/\nartifacts/\n")
        subprocess.run(["git", "add", "README.md", ".gitignore"], cwd=self.root, check=True)
        self.write("docs/guides/new.md", "# New\n")
        self.write(".venv/README.md", "# Dependency\n")
        self.write("artifacts/README.md", "# Output\n")
        self.write("node_modules/README.md", "# Unignored dependency\n")
        self.assertEqual([path.relative_to(self.root).as_posix() for path in check_docs.markdown_files(self.root)],
                         ["README.md", "docs/guides/new.md"])
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(check_docs.main(["--root", str(self.root)]), 0)
        self.assertIn("valid", output.getvalue())


if __name__ == "__main__":
    unittest.main()
