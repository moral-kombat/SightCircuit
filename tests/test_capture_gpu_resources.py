"""Real ownership helper, fake Vulkan dispatch; no actual GPU or camera calls."""
import json
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "artifacts/capture-resources-reference/sightcircuit-gpu-resources-fixture"


@unittest.skipUnless(FIXTURE.is_file(), "build scripts/build_capture_reference.sh --resource-fixes first")
class GpuResourcesTests(unittest.TestCase):
    def run_case(self, case):
        result = subprocess.run([str(FIXTURE), "--case", case], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["report_kind"], "capture_gpu_resources_fixture")
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["tests_run"], 1)
        self.assertFalse(report["runtime_initialized"])
        self.assertFalse(report["capture_started"])
        return report

    def test_default_and_invalid_invocations_do_not_run(self):
        for arguments, code in (([], 3), (["--unknown"], 64), (["--case"], 64)):
            result = subprocess.run([str(FIXTURE), *arguments], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, code, result.stderr)
            self.assertEqual(json.loads(result.stdout)["status"], "not_run")

    def test_mapping_and_child_before_parent_cleanup(self):
        self.run_case("lifecycle")

    def test_failure_at_each_fallible_startup_and_resource_operation(self):
        self.assertGreaterEqual(self.run_case("failures")["injected_failures"], 45)

    def test_device_queue_capabilities_ambiguity_and_count_bounds(self):
        self.run_case("selection")

    def test_sizes_memory_requirements_and_aggregate_allocation_budget(self):
        self.run_case("bounds")

    def test_moves_and_child_retaining_its_device(self):
        self.run_case("moves")

    def test_unconfirmed_work_defers_all_api_destruction(self):
        self.run_case("pending")

    def test_noncoherent_host_memory_fallback_and_memory_bit_31(self):
        self.run_case("memory")

    def test_imported_images_keep_device_alive_and_move_between_contexts(self):
        self.run_case("imports")


if __name__ == "__main__":
    unittest.main()
