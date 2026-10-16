"""Actual adapted worker bootstrap/downscaler against fake Vulkan symbols."""
import json
from pathlib import Path
import subprocess
import unittest

FIXTURE = Path(__file__).resolve().parents[1] / 'artifacts/capture-import-reference/framejacker-resource-fixture'

@unittest.skipUnless(FIXTURE.is_file(), 'build --import-fixes first')
class ResourceTests(unittest.TestCase):
    def check(self, mode):
        result = subprocess.run([str(FIXTURE), mode], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)['runtime_initialized'])

    def test_dependency_order_and_idempotent_close(self): self.check('success')
    def test_all_eighteen_acquisition_and_binding_failures(self): self.check('partial')
    def test_pending_submission_abandons_all_api_destruction(self): self.check('pending')
    def test_bootstrap_inventory_and_dispatch_failures(self): self.check('bootstrap')
    def test_move_and_single_destruction(self): self.check('move')
    def test_probe_command_reuse_and_both_creation_failures(self): self.check('probe')

if __name__ == '__main__':
    unittest.main()
