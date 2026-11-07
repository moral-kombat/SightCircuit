"""``scripts/`` holds command entry points; shared code lives in ``sightcircuit/``."""
import ast
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
# The paused capture-provider track is quarantined in place while it is frozen
# (docs/development.md#paused-capture-provider-track). Shrink these sets when
# the track resumes and moves.
FROZEN_CAPTURE_TRACK = frozenset((
    "capture_layer_config", "capture_layer_trial", "capture_provider_check", "capture_service_lifecycle",
    "capture_session", "capture_supervisor", "metadata_session_trial", "service_rehearsal", "session_rehearsal",
))
FROZEN_CAPTURE_TESTS = frozenset((
    "test_capture_layer_config", "test_capture_layer_trial", "test_capture_provider_check",
    "test_capture_service_lifecycle", "test_capture_supervisor", "test_metadata_session_trial",
    "test_service_rehearsal", "test_session_rehearsal",
))


def imported_scripts(path, names):
    """Sibling scripts a module imports, however it spells the import."""
    found = set()
    for node in ast.walk(ast.parse(path.read_text(), str(path))):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] in names:
                    found.add(parts[0])
                elif parts[0] == "scripts" and len(parts) > 1:
                    found.add(parts[1])
        elif isinstance(node, ast.ImportFrom):
            parts = (node.module or "").split(".") if node.module else []
            if node.level:
                found.update([parts[0]] if parts else [alias.name for alias in node.names])
            elif parts and parts[0] in names:
                found.add(parts[0])
            elif parts and parts[0] == "scripts":
                found.update([parts[1]] if len(parts) > 1 else [alias.name for alias in node.names])
    return found & names - {path.stem}


class ScriptsBoundaryTests(unittest.TestCase):
    def test_active_commands_import_only_the_library(self):
        names = {path.stem for path in SCRIPTS.glob("*.py")}
        self.assertTrue(FROZEN_CAPTURE_TRACK <= names, sorted(FROZEN_CAPTURE_TRACK - names))
        for path in sorted(SCRIPTS.glob("*.py")):
            imported = imported_scripts(path, names)
            with self.subTest(script=path.name):
                if path.stem in FROZEN_CAPTURE_TRACK:
                    self.assertLessEqual(imported, FROZEN_CAPTURE_TRACK)
                else:
                    self.assertEqual(imported, set(), "move shared code into sightcircuit/")

    def test_library_never_imports_commands(self):
        for path in sorted((ROOT / "sightcircuit").rglob("*.py")):
            with self.subTest(module=str(path.relative_to(ROOT))):
                tree = ast.parse(path.read_text(), str(path))
                modules = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                           for alias in node.names]
                modules += [node.module or "" for node in ast.walk(tree)
                            if isinstance(node, ast.ImportFrom) and not node.level]
                self.assertFalse([name for name in modules if name.split(".")[0] == "scripts"])


class CaptureTrackQuarantineTests(unittest.TestCase):
    """The frozen cluster is a closed unit: self-contained, unused elsewhere, documented."""

    def test_frozen_modules_import_only_the_standard_library_and_each_other(self):
        for name in sorted(FROZEN_CAPTURE_TRACK):
            tree = ast.parse((SCRIPTS / f"{name}.py").read_text())
            imported = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import)
                        for alias in node.names}
            imported |= {(node.module or "").split(".")[0] for node in ast.walk(tree)
                         if isinstance(node, ast.ImportFrom) and not node.level}
            imported |= {(node.module or "").split(".")[0] for node in ast.walk(tree)
                         if isinstance(node, ast.ImportFrom) and node.level and node.module}
            with self.subTest(module=name):
                self.assertEqual(imported - set(sys.stdlib_module_names) - FROZEN_CAPTURE_TRACK, set())

    def test_only_the_cluster_tests_import_frozen_modules(self):
        tests = Path(__file__).resolve().parent
        self.assertTrue(all((tests / f"{name}.py").is_file() for name in FROZEN_CAPTURE_TESTS))
        for path in sorted(tests.glob("*.py")):
            if imported_scripts(path, FROZEN_CAPTURE_TRACK):
                with self.subTest(test=path.name):
                    self.assertIn(path.stem, FROZEN_CAPTURE_TESTS)

    def test_device_source_snapshots_name_only_cluster_files(self):
        listed = []
        for name in sorted(FROZEN_CAPTURE_TRACK):
            for node in ast.walk(ast.parse((SCRIPTS / f"{name}.py").read_text())):
                if isinstance(node, ast.Assign) and any(getattr(target, "id", None) == "SOURCES"
                                                        for target in node.targets):
                    listed += [item.value for item in ast.walk(node.value)
                               if isinstance(item, ast.Constant) and isinstance(item.value, str)]
        self.assertGreaterEqual(len(listed), 7)
        for filename in listed:
            with self.subTest(source=filename):
                self.assertTrue(filename.endswith(".py"))
                self.assertIn(filename[:-3], FROZEN_CAPTURE_TRACK)

    def test_development_guide_lists_every_cluster_module_and_test(self):
        guide = (ROOT / "docs/development.md").read_text()
        section = guide.split("## Paused capture-provider track", 1)[1].split("\n## ", 1)[0]
        for name in sorted(FROZEN_CAPTURE_TRACK | FROZEN_CAPTURE_TESTS):
            with self.subTest(name=name):
                self.assertIn(f"{name}.py`", section)


if __name__ == "__main__":
    unittest.main()
