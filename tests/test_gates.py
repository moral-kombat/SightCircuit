"""The approval-gate table is the only source of gate checks and gate claims."""
import ast
from pathlib import Path
import unittest
from unittest.mock import Mock

from sightcircuit import gates

ROOT = Path(__file__).resolve().parents[1]
SOURCES = sorted([*(ROOT / "sightcircuit").rglob("*.py"), *(ROOT / "scripts").glob("*.py")])
CLAIMS = frozenset(gate.claim for gate in gates.GATES)
GATE_NAMES = {name for name, value in vars(gates).items() if isinstance(value, gates.Gate)}


def parsed():
    for path in SOURCES:
        yield path, ast.parse(path.read_text(), str(path))


class GateTableTests(unittest.TestCase):
    def test_every_gate_is_closed_named_and_tied_to_acceptance_rows(self):
        self.assertEqual(len({gate.name for gate in gates.GATES}), len(gates.GATES))
        self.assertEqual(len(CLAIMS), len(gates.GATES))
        self.assertEqual(gates.claims(), {claim: False for claim in CLAIMS})
        self.assertEqual(gates.claims(gates.LIVE_HUD_OUTPUT), {"production_hud_enabled": False})
        document, anchor = gates.ACCEPTANCE.split("#")
        text = (ROOT / document).read_text()
        self.assertIn("## Edge HUD acceptance gates", text)
        self.assertEqual(anchor, "edge-hud-acceptance-gates")
        for gate in gates.GATES:
            with self.subTest(gate=gate.name):
                self.assertTrue(gate.lifted_by)
                for row in gate.lifted_by:
                    self.assertIn(f"| {row}. ", text)

    def test_require_permits_only_the_literal_true(self):
        gates.LIVE_IMAGE_INPUT.require(True, "unused")
        for permitted in (False, None, 0, 1, "true", [True], Mock()):
            with self.subTest(permitted=permitted), self.assertRaisesRegex(ValueError, "^fixture_not_approved$"):
                gates.LIVE_IMAGE_INPUT.require(permitted, "fixture_not_approved")


class GateUsageTests(unittest.TestCase):
    def test_every_gate_guards_at_least_one_boundary(self):
        used = set()
        for _path, tree in parsed():
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and
                        node.func.attr == "require" and isinstance(node.func.value, ast.Name)):
                    used.add(node.func.value.id)
        self.assertEqual(GATE_NAMES - used, set())

    def test_no_approval_refusal_bypasses_the_gate_table(self):
        for path, tree in parsed():
            for node in ast.walk(tree):
                if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and node.exc.args:
                    reason = node.exc.args[0]
                    with self.subTest(source=str(path.relative_to(ROOT)), line=node.lineno):
                        self.assertFalse(isinstance(reason, ast.Constant) and isinstance(reason.value, str) and
                                         "not_approved" in reason.value, "use the gate's require()")

    def test_gate_claims_are_never_written_by_hand(self):
        for path, tree in parsed():
            if path.name == "gates.py":
                continue
            for node in ast.walk(tree):
                written = []
                if isinstance(node, ast.Dict):
                    written = [key.value for key, value in zip(node.keys, node.values)
                               if isinstance(key, ast.Constant) and isinstance(value, ast.Constant)]
                elif isinstance(node, ast.Call):
                    written = [keyword.arg for keyword in node.keywords if isinstance(keyword.value, ast.Constant)]
                elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                    written = [target.slice.value for target in node.targets
                               if isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant)]
                with self.subTest(source=str(path.relative_to(ROOT)), line=getattr(node, "lineno", 0)):
                    self.assertFalse(CLAIMS & set(written), "use gates.claims()")


if __name__ == "__main__":
    unittest.main()
