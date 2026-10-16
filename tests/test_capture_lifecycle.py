"""Provider callbacks/Unix sockets and the guarded native worker, no hardware."""
import json
from pathlib import Path
import subprocess
import unittest
from scripts import capture_watchdog as watchdog
ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / 'artifacts/capture-import-reference'

@unittest.skipUnless((BUILD / 'framejacker-layer-fixture').is_file(), 'build --import-fixes first')
class ProviderTests(unittest.TestCase):
    def fixture(self, name, mode):
        result = subprocess.run([str(BUILD / name), mode], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['synthetic'])
    def test_existing_and_replaced_endpoints_survive(self): self.fixture('framejacker-registry-fixture','ownership')
    def test_changed_registry_fds_empty_provider_and_disconnect(self): self.fixture('framejacker-registry-fixture','continuity')
    def test_endpoint_restart_changes_identity(self): self.fixture('framejacker-registry-fixture','restart')
    def test_private_directories_are_required_not_repaired(self): self.fixture('framejacker-registry-fixture','private')
    def test_device_scoping_image_retirement_and_fd_cleanup(self): self.fixture('framejacker-layer-fixture','registry')
    def test_callback_finishes_before_device_retirement(self): self.fixture('framejacker-layer-fixture','retirement')
    def test_instance_dispatch_lifetime_and_absent_lookup(self): self.fixture('framejacker-layer-fixture','instance')
    def test_actual_worker_fails_closed_under_watchdog(self):
        report = watchdog.check_guarded_worker([])
        self.assertEqual(report['status'], 'blocked', report)
        self.assertEqual(report['exit_code'], 3)
        self.assertTrue(report['worker_reaped'])
        self.assertFalse(report['termination_requested'])
        self.assertFalse(report['native']['runtime_initialized'])
    def test_native_defaults_and_opt_in_cannot_activate_capture(self):
        for args, code in (([],3), (['--help'],0), (['--unknown'],64),
                           (['--allow-experimental-capture','--seconds','10'],3)):
            result = subprocess.run([str(watchdog.GUARDED_WORKER), *args], capture_output=True,text=True,timeout=3)
            self.assertEqual(result.returncode,code,result.stderr)
            if code == 3:
                self.assertFalse(json.loads(result.stdout)['capture_started'])

if __name__ == '__main__': unittest.main()
