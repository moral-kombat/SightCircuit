"""Exercise the real import helper with fake Vulkan dispatch and synthetic memfds."""
import json
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "artifacts/capture-import-reference/framejacker-import-fixture"


@unittest.skipUnless(FIXTURE.is_file(), "build scripts/build_capture_reference.sh --import-fixes first")
class CaptureImportTests(unittest.TestCase):
    def run_case(self, name):
        result = subprocess.run([str(FIXTURE), "--case", name], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["report_kind"], "capture_import_fixture")
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["tests_run"], 1)
        self.assertFalse(report["descriptor_leak_detected"])
        self.assertFalse(report["runtime_initialized"])
        self.assertFalse(report["capture_started"])

    def test_default_and_bad_arguments_do_not_run_imports(self):
        for arguments, code in (([], 3), (["--unknown"], 64), (["--case"], 64)):
            result = subprocess.run([str(FIXTURE), *arguments], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, code, result.stderr)
            self.assertEqual(json.loads(result.stdout)["status"], "not_run")

    def test_metadata_rejected_before_driver_calls(self):
        self.run_case("profile")

    def test_modifier_planes_external_features_and_image_limits(self):
        self.run_case("capabilities")

    def test_requirement_sizes_alignment_and_memory_type_intersection(self):
        self.run_case("requirements")

    def test_successful_fd_transfer_and_ordered_idempotent_cleanup(self):
        self.run_case("ownership")

    def test_driver_failures_rollback_without_fd_or_resource_leaks(self):
        self.run_case("failures")

    def test_move_ownership_vector_relocation_and_batch_rollback(self):
        self.run_case("moves")

    def test_pending_work_defers_destruction_to_process_teardown(self):
        self.run_case("pending")

    def test_invalid_context_fd_and_memory_bounds_including_bit_31(self):
        self.run_case("context")

    def test_submission_reset_submit_and_wait_failures_gate_cleanup(self):
        self.run_case("submission")


if __name__ == "__main__":
    unittest.main()
