"""Native provider registry/socket and actual layer hook control flow."""
import json
from pathlib import Path
import subprocess
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "artifacts/capture-provider-reference"


@unittest.skipUnless((BUILD / "sightcircuit-provider-fixture").is_file(), "build --provider-fixes first")
class ProviderTests(unittest.TestCase):
    def run_case(self, name, binary="provider"):
        with tempfile.TemporaryDirectory(prefix="fj-layer-executable-") as temporary:
            executable = BUILD / f"sightcircuit-{binary}-fixture"
            if binary == "layer" and name in ("lifecycle", "failures", "guard_fallback"):
                # /proc/self/exe resolves symlinks; use an actual copy so the
                # real production basename gate is exercised by these cases.
                copied = Path(temporary) / "vrcompositor"
                shutil.copy2(executable, copied)
                executable = copied
            result = subprocess.run([str(executable), "--case", name],
                                    capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["tests_run"], 1)
        self.assertFalse(report["runtime_initialized"])
        self.assertFalse(report["capture_started"])

    def test_exporter_metadata_and_gpu_uuid_contract(self): self.run_case("metadata")
    def test_device_scoped_handles_and_device_teardown(self): self.run_case("device_scope")
    def test_snapshot_fd_ownership_and_nonreused_ids(self): self.run_case("snapshot_ownership")
    def test_generation_and_image_destruction_end_leases(self): self.run_case("generations")
    def test_provider_loss_restart_and_invalidation(self): self.run_case("provider_loss")
    def test_endpoint_ownership_and_foreign_file_preservation(self): self.run_case("endpoint_ownership")
    def test_server_shutdown_interrupts_idle_client(self): self.run_case("idle_shutdown")
    def test_registry_bounds_and_invalidation(self): self.run_case("bounded_registry")
    def test_metadata_has_no_fds_and_capture_requests_are_blocked(self): self.run_case("metadata_only")
    def test_actual_hooks_and_same_dispatch_table_devices(self): self.run_case("lifecycle", "layer")
    def test_failed_driver_calls_and_missing_dispatch(self): self.run_case("failures", "layer")
    def test_opt_in_and_crash_loop_guard(self): self.run_case("gating", "layer")
    def test_legacy_loader_fallback_cannot_bypass_process_opt_in(self): self.run_case("fallback", "layer")
    def test_legacy_loader_fallback_cannot_bypass_exhausted_guard(self): self.run_case("guard_fallback", "layer")
