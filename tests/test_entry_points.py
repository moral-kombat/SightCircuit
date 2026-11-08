"""The active commands: one body, one wrapper and one console entry point each (P2, 2026-11-07).

``sightcircuit/commands/<name>.py`` holds the body, ``scripts/<name>.py`` wraps
it for the uninstalled checkout and ``pyproject.toml`` names the
``sightcircuit-<name>`` entry point. The three lists must agree, the paused
capture-provider scripts get no entry point, and a wrapper imported as
``scripts.<name>`` must be the body module so ``main(argv)`` and the attributes
the tests patch are one object. Standard library only.
"""
import ast
import importlib
from pathlib import Path
import sys
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from sightcircuit.commands import COMMANDS  # noqa: E402
from tests.test_scripts_boundary import FROZEN_CAPTURE_TRACK  # noqa: E402

WRAPPER_MAX_LINES = 25


def entry_points():
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["scripts"]


class EntryPointTests(unittest.TestCase):
    def test_the_three_lists_agree(self):
        bodies = {path.stem for path in (ROOT / "sightcircuit/commands").glob("*.py")} - {"__init__"}
        self.assertEqual(bodies, set(COMMANDS))
        self.assertEqual(len(COMMANDS), len(set(COMMANDS)))
        self.assertEqual(entry_points(), {f"sightcircuit-{name.replace('_', '-')}": f"sightcircuit.commands.{name}:main"
                                          for name in COMMANDS})
        for name in COMMANDS:
            with self.subTest(command=name):
                self.assertTrue((ROOT / "scripts" / f"{name}.py").is_file(), f"scripts/{name}.py wrapper is missing")

    def test_paused_scripts_have_no_entry_point(self):
        self.assertEqual(set(COMMANDS) & FROZEN_CAPTURE_TRACK, set())
        for name in FROZEN_CAPTURE_TRACK:
            self.assertFalse((ROOT / "sightcircuit/commands" / f"{name}.py").exists())

    def test_each_body_defines_main(self):
        for name in COMMANDS:
            with self.subTest(command=name):
                tree = ast.parse((ROOT / "sightcircuit/commands" / f"{name}.py").read_text())
                mains = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main"]
                self.assertEqual(len(mains), 1)
                self.assertGreaterEqual(len(mains[0].args.args), 1, "main takes argv")

    def test_each_wrapper_is_thin_and_imports_only_its_body(self):
        for name in COMMANDS:
            path = ROOT / "scripts" / f"{name}.py"
            text = path.read_text()
            with self.subTest(command=name):
                self.assertLessEqual(len(text.splitlines()), WRAPPER_MAX_LINES, "the wrapper grew a body of its own")
                tree = ast.parse(text)
                imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
                imported |= {f"{node.module}.{alias.name}" for node in ast.walk(tree)
                             if isinstance(node, ast.ImportFrom) for alias in node.names}
                self.assertIn(f"sightcircuit.commands.{name}", imported)
                self.assertEqual(imported - {"sys", "pathlib.Path", f"sightcircuit.commands.{name}"}, set())
                self.assertIn('if __name__ == "__main__":', text)
                self.assertIn("sys.modules[__name__] = _command", text)

    def test_an_imported_wrapper_is_the_body_module(self):
        for name in COMMANDS:
            with self.subTest(command=name):
                body = importlib.import_module(f"sightcircuit.commands.{name}")
                wrapper = importlib.import_module(f"scripts.{name}")
                self.assertIs(wrapper, body)
                self.assertIs(wrapper.main, body.main)
                self.assertEqual(wrapper.__name__, f"sightcircuit.commands.{name}")

    def test_bodies_resolve_the_checkout_root_two_levels_up(self):
        for name in COMMANDS:
            text = (ROOT / "sightcircuit/commands" / f"{name}.py").read_text()
            with self.subTest(command=name):
                self.assertNotIn("parents[1]", text, "a body still assumes the scripts/ location")


if __name__ == "__main__":
    unittest.main()
