"""The approval-gate table is the only source of gate checks and gate claims."""
import ast
from pathlib import Path
import re
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

    def test_the_table_is_the_reviewed_one_and_every_claim_is_false(self):
        # Work-order item L10 (edit 1): the camera input gate is new, real HUD output is lifted by the camera
        # rows, the mirror's gate keeps its rows, and nothing is open.
        self.assertEqual([(gate.name, gate.claim, gate.lifted_by) for gate in gates.GATES], [
            ("live_image_input", "production_live_input_enabled", ("G4", "G5", "G6")),
            ("live_camera_input", "production_camera_input_enabled", ("G7", "G8", "G10")),
            ("hardware_mapping", "mapping_qualified", ("G4",)),
            ("live_hud_output", "production_hud_enabled", ("G8", "G9", "G10"))])
        self.assertEqual(gates.claims(), {"production_live_input_enabled": False,
                                          "production_camera_input_enabled": False, "mapping_qualified": False,
                                          "production_hud_enabled": False})
        self.assertEqual(gates.claims(gates.LIVE_CAMERA_INPUT), {"production_camera_input_enabled": False})
        # The two descriptions are true of the code as it stands: run_pipeline refuses every non-synthetic source,
        # the camera's included, through LIVE_IMAGE_INPUT; the camera's way to the HUD is LIVE_CAMERA_INPUT's.
        self.assertEqual(gates.LIVE_IMAGE_INPUT.closes,
                         "Non-synthetic images from any source feeding the generic live pipeline (run_pipeline), and "
                         "from any source other than the SDK camera (the SteamVR mirror) feeding the HUD path")
        self.assertEqual(gates.LIVE_CAMERA_INPUT.closes, "Arcturus SDK camera frames feeding the HUD path")
        for gate in gates.GATES:  # A gate is a frozen record with no state to flip.
            with self.subTest(gate=gate.name), self.assertRaises(Exception):
                gate.claim = "open"
        self.assertEqual({name for name in vars(gates.Gate) if not name.startswith("_")}, {"require"})

    def test_every_g_number_named_in_the_gate_table_is_a_row_of_the_acceptance_table(self):
        document, _ = gates.ACCEPTANCE.split("#")
        section = (ROOT / document).read_text().split("\n## Edge HUD acceptance gates\n", 1)[1].split("\n## ", 1)[0]
        table = [line for line in section.splitlines() if re.match(r"\| G\d", line)]
        self.assertGreaterEqual(len(table), 10)
        rows = [re.match(r"\| (G\d+)\. ", line) for line in table]
        self.assertTrue(all(rows), table)
        rows = [match.group(1) for match in rows]
        self.assertEqual(rows, [f"G{number}" for number in range(1, len(rows) + 1)])  # One row each, in order.
        named = set(re.findall(r"\bG\d+\b", (ROOT / "sightcircuit/gates.py").read_text()))
        self.assertEqual(named, {row for gate in gates.GATES for row in gate.lifted_by})
        self.assertLessEqual(named, set(rows))
        self.assertEqual(named, {"G4", "G5", "G6", "G7", "G8", "G9", "G10"})
        # The paragraph under the table states the same rows per gate as the code.
        for gate, phrase in ((gates.LIVE_IMAGE_INPUT, "G4, G5 and G6"), (gates.LIVE_CAMERA_INPUT, "G7, G8 and G10"),
                             (gates.LIVE_HUD_OUTPUT, "G8, G9 and G10"), (gates.HARDWARE_MAPPING, "requires G4")):
            with self.subTest(gate=gate.name):
                self.assertIn(phrase, " ".join(section.split()))
        for phrase in ("the generic live pipeline (`run_pipeline`", "SDK camera frames feeding the HUD path"):
            self.assertIn(phrase, " ".join(section.split()))

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
