"""Inactive preparation and exact conditional restoration in generated homes."""
import json
import os
from pathlib import Path
import subprocess
import selectors
import sys
import tempfile
import unittest
from scripts import capture_layer_config as config


class LayerConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="framejacker-config-tests-")
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.home.mkdir(mode=0o700)
        self.library = self.root / "inspection.so"
        self.library.write_bytes(b"\x7fELFgenerated-test-library")
        self.bundle = self.root / "bundle"

    def tearDown(self):
        self.temp.cleanup()

    def prepare(self):
        return config.prepare(self.home, self.library, self.bundle)

    def install_fixture(self, plan, names=config.TARGETS):
        # Exercise restoration of future staged files in a generated home only.
        for name in names:
            config.write_entry(self.home, name, plan["after"][name])

    def test_default_does_nothing_and_prepare_changes_no_runtime_targets(self):
        result = subprocess.run([sys.executable, "scripts/capture_layer_config.py"], cwd=config.ROOT,
                                capture_output=True, text=True, timeout=2)
        self.assertEqual(result.returncode, 3)
        self.assertFalse(json.loads(result.stdout)["activation_performed"])
        plan = self.prepare()
        self.assertEqual(list(self.home.iterdir()), [])
        self.assertFalse(plan["activation_performed"])
        manifest = json.loads((self.bundle / "framejacker_capture.json").read_text())
        self.assertEqual(manifest["layer"]["enable_environment"], {"FRAMEJACKER_CAPTURE_LAYER": "1"})
        self.assertEqual(manifest["layer"]["library_path"], str(self.home / config.LIBRARY))
        self.assertFalse((self.bundle / "libframejacker_capture.so").stat().st_mode & 0o111)

    def test_restore_new_and_partial_installations(self):
        for names in (config.TARGETS, (config.LIBRARY,)):
            with self.subTest(names=names):
                if self.bundle.exists():
                    import shutil
                    shutil.rmtree(self.bundle)
                plan = self.prepare()
                self.install_fixture(plan, names)
                report = config.restore(self.bundle, expected_home=self.home)
                self.assertEqual(report["status"], "restored")
                self.assertFalse(report["loaded_layer_unloaded"])
                self.assertTrue(all(config.read_entry(self.home, name) is None for name in config.TARGETS))
                config.restore(self.bundle, expected_home=self.home)  # Idempotent.

    def test_existing_bytes_and_modes_are_restored_exactly(self):
        originals = {name: config.describe(b"previous " + name.encode(), 0o640) for name in config.TARGETS}
        for name, entry in originals.items():
            config.write_entry(self.home, name, entry)
        plan = self.prepare()
        self.assertEqual(plan["before"], originals)
        self.install_fixture(plan)
        config.restore(self.bundle, expected_home=self.home)
        self.assertEqual({name: config.read_entry(self.home, name) for name in config.TARGETS}, originals)

    def test_later_edits_are_preserved_before_any_restore_mutation(self):
        plan = self.prepare()
        self.install_fixture(plan)
        modified = self.home / config.MANIFEST
        modified.write_bytes(b"later edit")
        with self.assertRaisesRegex(ValueError, "target_changed"):
            config.restore(self.bundle, expected_home=self.home)
        self.assertEqual(modified.read_bytes(), b"later edit")
        self.assertEqual(config.read_entry(self.home, config.LIBRARY), plan["after"][config.LIBRARY])

    def test_symlink_hardlink_fifo_and_parent_symlink_are_refused(self):
        target = self.home / config.LIBRARY
        target.parent.mkdir(parents=True)
        for kind in ("symlink", "hardlink", "fifo"):
            with self.subTest(kind=kind):
                if kind == "symlink": target.symlink_to(self.library)
                elif kind == "hardlink": os.link(self.library, target)
                else: os.mkfifo(target, 0o600)
                with self.assertRaises((OSError, ValueError)):
                    self.prepare()
                self.assertTrue(target.exists())
                target.unlink()
        other = self.root / "other"
        other.mkdir()
        (self.home / ".local/share/vulkan").symlink_to(other, target_is_directory=True)
        with self.assertRaises(OSError):
            self.prepare()
        self.assertEqual(list(other.iterdir()), [])

    def test_bundle_reuse_checksum_and_home_mismatch_are_refused(self):
        self.prepare()
        with self.assertRaises(FileExistsError):
            self.prepare()
        with self.assertRaisesRegex(ValueError, "home_mismatch"):
            config.restore(self.bundle, expected_home=self.root)
        path = self.bundle / "plan.json"
        plan = json.loads(path.read_text())
        plan["after"][config.LIBRARY]["sha256"] = "0" * 64
        path.write_text(json.dumps(plan))
        with self.assertRaisesRegex(ValueError, "checksum"):
            config.restore(self.bundle, expected_home=self.home)
        self.assertEqual(list(self.home.iterdir()), [])

    @unittest.skipUnless((config.ROOT / "artifacts/capture-provider-reference/framejacker-provider-fixture").is_file(),
                         "build --provider-fixes first")
    def test_provider_crash_recovery_refuses_living_or_replaced_endpoint(self):
        for replace in (False, True):
            with self.subTest(replace=replace), tempfile.TemporaryDirectory(dir=self.root) as runtime:
                process = subprocess.Popen([str(config.ROOT / "artifacts/capture-provider-reference/framejacker-provider-fixture"),
                                            "--serve-generated", runtime], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    with selectors.DefaultSelector() as selector:
                        selector.register(process.stdout, selectors.EVENT_READ)
                        self.assertTrue(selector.select(timeout=2))
                    self.assertEqual(process.stdout.readline().strip(), "ready")
                    with self.assertRaises((ValueError, BlockingIOError)):
                        config.recover_provider(runtime)
                    process.kill()
                    process.wait(timeout=2)
                    endpoint = Path(runtime) / "framejacker-camera-provider/camera.sock"
                    if replace:
                        endpoint.unlink()
                        endpoint.write_bytes(b"foreign")
                        with self.assertRaisesRegex(ValueError, "endpoint_replaced"):
                            config.recover_provider(runtime)
                        self.assertEqual(endpoint.read_bytes(), b"foreign")
                    else:
                        config.recover_provider(runtime)
                        self.assertFalse(endpoint.exists())
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.communicate(timeout=2)
