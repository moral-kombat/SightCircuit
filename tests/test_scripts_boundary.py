"""``scripts/`` holds command entry points; shared code lives in ``framejacker/``."""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
# The paused capture-provider track keeps its internal imports while it is
# frozen (see docs/design-review.md). Shrink this set when the track is revisited.
FROZEN_CAPTURE_TRACK = frozenset((
    "capture_layer_config", "capture_layer_trial", "capture_provider_check", "capture_service_lifecycle",
    "capture_session", "capture_supervisor", "metadata_session_trial", "service_rehearsal", "session_rehearsal",
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
                    self.assertEqual(imported, set(), "move shared code into framejacker/")

    def test_library_never_imports_commands(self):
        for path in sorted((ROOT / "framejacker").rglob("*.py")):
            with self.subTest(module=str(path.relative_to(ROOT))):
                tree = ast.parse(path.read_text(), str(path))
                modules = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                           for alias in node.names]
                modules += [node.module or "" for node in ast.walk(tree)
                            if isinstance(node, ast.ImportFrom) and not node.level]
                self.assertFalse([name for name in modules if name.split(".")[0] == "scripts"])


if __name__ == "__main__":
    unittest.main()
