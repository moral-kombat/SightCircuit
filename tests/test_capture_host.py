"""Fake-dispatch host-buffer rollback; no Vulkan runtime or camera."""
import json
from pathlib import Path
import subprocess
import unittest

FIXTURE = Path(__file__).resolve().parents[1] / 'artifacts/capture-import-reference/framejacker-host-fixture'


@unittest.skipUnless(FIXTURE.is_file(), 'build --import-fixes first')
class HostOwnershipTests(unittest.TestCase):
    def check(self, mode):
        result = subprocess.run([str(FIXTURE), mode], capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)['runtime_initialized'])

    def test_success_and_move(self): self.check('success')
    def test_partial_failures_ignore_undefined_outputs(self): self.check('failures')
    def test_size_requirements_and_context_bounds(self): self.check('bounds')
    def test_memory_properties_and_heap_bounds(self): self.check('memory')
    def test_in_flight_work_defers_cleanup(self): self.check('in_flight')
    def test_move_assignment_closes_old_owner(self): self.check('move_assignment')


if __name__ == '__main__':
    unittest.main()
