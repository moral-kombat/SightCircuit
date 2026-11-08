"""Checkpoint artifact manifest: ``--write`` pins, ``--verify`` fails on any difference.

Every write and verify case builds its own temporary git checkout; the
repository's own files are only read, never modified. These are cloud checks
of a software-identity tool, never Frame or device evidence.
"""
import ast
import contextlib
import hashlib
from importlib import metadata
import io
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from sightcircuit.runtime import manifest
from scripts import checkpoint_manifest as command

ROOT = Path(__file__).resolve().parents[1]
GIT = ("git", "-c", "user.name=SightCircuit Test", "-c", "user.email=test@example.invalid",
       "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main", "-c", "core.hooksPath=/dev/null")
VERSIONS = {"numpy": "2.2.6", "opencv-python-headless": "4.12.0.88"}
ASSET = b"detector model bytes"


def fake_version(name):
    if name not in VERSIONS:
        raise metadata.PackageNotFoundError(name)
    return VERSIONS[name]


def strict_json(text):
    """Parse ``text`` as strict JSON: NaN and Infinity are not JSON."""
    def reject(name):
        raise ValueError(f"not strict JSON: {name}")
    return json.loads(text, parse_constant=reject)


def tree_state(*roots):
    """Every file and directory under ``roots`` with its mtime, size and inode."""
    state = {}
    for root in roots:
        for directory, names, files in os.walk(root):
            for name in [".", *names, *files]:
                path = Path(directory, name)
                info = path.lstat()
                state[str(path)] = (info.st_mtime_ns, info.st_size, info.st_ino, stat.S_IFMT(info.st_mode))
    return state


class CheckoutCase(unittest.TestCase):
    """A minimal clean checkout with a built bridge and one checksum-pinned asset."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        self.root, self.out = base / "checkout", base / "out"
        self.out.mkdir()
        files = {".gitignore": "/artifacts/\n/.cache/\n__pycache__/\n", "README.md": "Checkout\n",
                 "sightcircuit/__init__.py": "", "sightcircuit/gates.py": "OPEN = False\n",
                 "sightcircuit/hud/__init__.py": "", "sightcircuit/hud/contract.py": "MARKER = 1\n",
                 "sightcircuit/agent/__init__.py": "", "sightcircuit/agent/runner.py": "KILL_GRACE = 10\n",
                 manifest.ASSET_MANIFEST: json.dumps({"assets": [{
                     "name": "model.onnx", "url": "https://example.invalid/model.onnx",
                     "sha256": hashlib.sha256(ASSET).hexdigest()}]})}
        files.update({path: f"# {path}\n" for path in manifest.CHECKPOINT_SCRIPTS + manifest.NATIVE_SOURCES +
                      manifest.AGENT_FILES})
        for path, text in files.items():
            (self.root / path).parent.mkdir(parents=True, exist_ok=True)
            (self.root / path).write_text(text)
        self.bridge = self.root / manifest.BRIDGE
        self.bridge.parent.mkdir(parents=True)
        self.bridge.write_bytes(b"bridge built on this machine")
        self.asset = self.root / manifest.ASSET_DIRECTORY / "model.onnx"
        self.asset.parent.mkdir(parents=True)
        self.asset.write_bytes(ASSET)
        self.git("init", "-q")
        self.git("add", "--", *files)
        self.git("commit", "-q", "-m", "checkpoint")
        versions = patch.object(manifest.metadata, "version", side_effect=fake_version)
        versions.start()
        self.addCleanup(versions.stop)
        # A fake pacman local database, empty unless a test installs a package; never the host's, and no
        # pacman command runs: the query fallback is a stub that answers "unavailable".
        self.pacman = base / "pacman-local"
        self.pacman.mkdir()
        for target, value in (("pacman_local_databases", lambda config=None: (self.pacman,)),
                              ("pacman_query", lambda name, timeout=None: (None, "pacman_unavailable"))):
            patcher = patch.object(manifest, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.path = self.out / "checkpoint-manifest.json"

    def install_package(self, name, version, *, entry=None, desc=None):
        """One pacman local-database entry; ``desc`` replaces the generated description text."""
        folder = self.pacman / (entry or f"{name}-{version}")
        folder.mkdir()
        if desc is None:
            desc = f"%NAME%\n{name}\n\n%VERSION%\n{version}\n\n%DESC%\nSteamVR for Frame\n\n"
        (folder / "desc").write_text(desc)
        return folder

    def git(self, *arguments):
        return subprocess.run([*GIT, "-C", str(self.root), *arguments], check=True, capture_output=True,
                              text=True).stdout

    def run_command(self, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = command.main(["--root", str(self.root), *arguments])
        return code, stdout.getvalue(), stderr.getvalue()

    def write(self, *extra):
        code, out, err = self.run_command("--write", str(self.path), *extra)
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def verify(self):
        code, out, _ = self.run_command("--verify", str(self.path))
        report = strict_json(out)
        self.assertEqual((report["report_kind"], report["exit_code"]), (manifest.VERIFICATION_KIND, code))
        return code, report

    def assertVerifies(self):
        code, report = self.verify()
        self.assertEqual((code, report["status"], report["differences"]), (0, "verified", []))
        return report

    def assertDiffers(self, *expected):
        """Verification fails and names each (field, reason) in ``expected``."""
        code, report = self.verify()
        self.assertEqual((code, report["status"]), (1, "differs"))
        found = {(item["field"], item["reason"]) for item in report["differences"]}
        self.assertLessEqual(set(expected), found, report["differences"])
        return report


class WriteTests(CheckoutCase):
    def test_manifest_pins_identity_with_relative_paths_and_sorted_keys(self):
        result = self.write()
        text = self.path.read_text()
        data = json.loads(text)
        self.assertEqual(text, json.dumps(data, sort_keys=True, indent=2) + "\n")
        self.assertEqual((data["schema_version"], data["report_kind"]), (2, "checkpoint_artifact_manifest"))
        self.assertIn("software identity only", data["scope"])
        self.assertIn("not device", data["scope"])
        self.assertEqual(data["git"], {"commit": self.git("rev-parse", "HEAD").strip(), "dirty": False})
        self.assertEqual(len(data["git"]["commit"]), 40)
        self.assertEqual(data["python"], {"implementation": platform.python_implementation(),
                                          "version": platform.python_version()})
        self.assertEqual(data["platform"], {"machine": platform.machine(), "system": platform.system(),
                                            "kernel_release": platform.release(),
                                            "os_release": manifest.os_release(),
                                            "steamvr_package": {"name": "deckard-steamvr-rel",
                                                                "source": "pacman_local_database",
                                                                "database": str(self.pacman),
                                                                "databases": {str(self.pacman): "read"},
                                                                "version": None, "reason": "package_not_installed",
                                                                "query": None}})
        self.assertEqual(set(data["platform"]["os_release"]), {"id", "version_id", "build_id"})
        self.assertEqual(data["bridge"], {manifest.BRIDGE: hashlib.sha256(self.bridge.read_bytes()).hexdigest()})
        self.assertEqual(sorted(data["modules"]), ["sightcircuit/__init__.py", "sightcircuit/gates.py",
                                                   "sightcircuit/hud/__init__.py", "sightcircuit/hud/contract.py"])
        # The agent is operations tooling outside the gates (D7): its own group, and nothing of it in modules.
        self.assertEqual(sorted(data["agent"]), ["scripts/frame_agent_guard.py", "scripts/frame_agent_run.py",
                                                 "sightcircuit/agent/__init__.py",
                                                 "sightcircuit/agent/frame-settings.json",
                                                 "sightcircuit/agent/runner.py"])
        self.assertEqual(data["agent"]["sightcircuit/agent/runner.py"], hashlib.sha256(b"KILL_GRACE = 10\n").hexdigest())
        self.assertEqual(sorted(data["scripts"]), sorted(manifest.CHECKPOINT_SCRIPTS))
        self.assertIn("tools/hud_contract.hpp", data["native_sources"])
        self.assertEqual(data["assets"], {f"{manifest.ASSET_DIRECTORY}/model.onnx": hashlib.sha256(ASSET).hexdigest()})
        self.assertEqual(data["packages"], VERSIONS)
        self.assertNotIn(str(self.root), text)
        self.assertEqual((result["status"], result["problems"]), ("written", []))
        self.assertEqual(result["manifest_sha256"], hashlib.sha256(text.encode()).hexdigest())

    def test_the_same_state_writes_identical_bytes(self):
        self.write()
        first = self.path.read_bytes()
        self.path = self.out / "again.json"
        self.write()
        self.assertEqual(self.path.read_bytes(), first)

    def test_write_refuses_an_existing_path_and_leaves_it_unchanged(self):
        for existing in ("file", "symlink"):
            with self.subTest(existing=existing):
                self.path = self.out / f"{existing}.json"
                if existing == "file":
                    self.path.write_text("keep\n")
                else:
                    self.path.symlink_to(self.out / "missing-target.json")
                before = tree_state(self.out)
                code, out, err = self.run_command("--write", str(self.path))
                self.assertEqual((code, out), (1, ""))
                self.assertIn("exists", err)
                self.assertEqual(tree_state(self.out), before)

    def test_write_never_replaces_a_path_created_after_its_check(self):
        observe, created = manifest.observe, {}

        def observe_then_race(*arguments):
            self.path.write_text("keep\n")  # Another writer wins after the existence check.
            created["inode"] = self.path.stat().st_ino
            return observe(*arguments)

        with patch.object(manifest, "observe", side_effect=observe_then_race):
            code, out, err = self.run_command("--write", str(self.path))
        self.assertEqual((code, out), (1, ""))
        self.assertIn("exists", err)
        self.assertEqual((self.path.read_text(), self.path.stat().st_ino), ("keep\n", created["inode"]))
        self.assertEqual([path.name for path in self.out.iterdir()], [self.path.name])  # No partial file.

    def test_write_refuses_a_dirty_tree_unless_it_records_dirty(self):
        (self.root / "sightcircuit/gates.py").write_text("OPEN = True\n")
        code, out, err = self.run_command("--write", str(self.path))
        self.assertEqual((code, out), (1, ""))
        self.assertIn("sightcircuit/gates.py", err)
        self.assertFalse(self.path.exists())
        self.assertTrue(self.write("--allow-dirty")["dirty"])
        self.assertTrue(json.loads(self.path.read_text())["git"]["dirty"])
        # A manifest of a dirty tree never verifies, even with the tree unchanged.
        self.assertDiffers(("git.dirty", "dirty_tree"))

    def test_write_refuses_a_path_that_would_dirty_the_tree(self):
        self.path = self.root / "checkpoint-manifest.json"
        code, _, err = self.run_command("--write", str(self.path))
        self.assertEqual(code, 1)
        self.assertIn("would dirty", err)
        self.assertFalse(self.path.exists())
        self.path = self.root / "artifacts/checkpoint-manifest.json"  # Ignored, as in the procedure.
        self.write()
        self.assertVerifies()

    def test_allow_dirty_applies_only_to_write(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            command.main(["--root", str(self.root), "--verify", str(self.path), "--allow-dirty"])
        self.assertEqual(raised.exception.code, 2)

    def test_missing_model_and_package_are_recorded_not_a_crash(self):
        self.asset.unlink()
        expected = {(f"assets.{manifest.ASSET_DIRECTORY}/model.onnx", "file_missing"),
                    ("packages.numpy", "package_missing")}
        with patch.dict(VERSIONS, clear=False):
            del VERSIONS["numpy"]
            result = self.write()
            data = json.loads(self.path.read_text())
            self.assertIsNone(data["assets"][f"{manifest.ASSET_DIRECTORY}/model.onnx"])
            self.assertIsNone(data["packages"]["numpy"])
            self.assertEqual({(item["field"], item["reason"]) for item in result["problems"]}, expected)
            report = self.assertDiffers(*expected)
        self.assertEqual(len(report["differences"]), 2)

    def test_outside_a_checkout_nothing_is_written_or_verified(self):
        self.write()
        target = self.out / "outside.json"
        for mode, path in (("--write", target), ("--verify", self.path)):
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(command.main(["--root", str(self.out), mode, str(path)]), 1)
        self.assertFalse(target.exists())
        report = json.loads(stdout.getvalue())
        self.assertEqual((report["status"], [item["reason"] for item in report["differences"]]),
                         ("error", ["not_a_checkout"]))


class VerifyTests(CheckoutCase):
    def test_passes_when_nothing_changed(self):
        self.write()
        report = self.assertVerifies()
        self.assertEqual(report["manifest_sha256"], hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertIn("not device", report["scope"])

    def test_fails_for_a_changed_sightcircuit_module(self):
        self.write()
        (self.root / "sightcircuit/gates.py").write_text("OPEN = True\n")
        self.git("commit", "-q", "-am", "change a module")
        self.assertDiffers(("modules.sightcircuit/gates.py", "changed"), ("git.commit", "changed"))

    def test_an_agent_change_differs_in_the_agent_group_and_leaves_the_hud_modules_unchanged(self):
        self.write()
        (self.root / "sightcircuit/agent/runner.py").write_text("KILL_GRACE = 20\n")
        (self.root / "sightcircuit/agent/tasks.py").write_text("")
        self.git("add", "sightcircuit/agent/tasks.py")
        self.git("commit", "-q", "-am", "change the agent")
        report = self.assertDiffers(("agent.sightcircuit/agent/runner.py", "changed"),
                                    ("agent.sightcircuit/agent/tasks.py", "unexpected"), ("git.commit", "changed"))
        self.assertFalse([item for item in report["differences"] if item["field"].startswith("modules.")],
                         "an agent change altered the HUD modules group")

    def test_the_real_checkout_keeps_the_agent_out_of_the_hud_modules(self):
        current, _ = manifest.observe(ROOT)
        self.assertFalse([path for path in current["modules"] if path.startswith(manifest.AGENT_PACKAGE + "/")])
        self.assertTrue(all(path.startswith(manifest.AGENT_PACKAGE + "/") or path in manifest.AGENT_FILES
                            for path in current["agent"]))
        for path in ("sightcircuit/agent/runner.py", "sightcircuit/agent/guard.py", *manifest.AGENT_FILES):
            self.assertIn(path, current["agent"])
            self.assertTrue((ROOT / path).is_file(), path)

    def test_fails_for_an_added_or_removed_module(self):
        self.write()
        (self.root / "sightcircuit/extra.py").write_text("")
        self.git("add", "sightcircuit/extra.py")
        self.git("rm", "-q", "sightcircuit/hud/contract.py")
        self.git("commit", "-q", "-m", "add and remove modules")
        self.assertDiffers(("modules.sightcircuit/extra.py", "unexpected"),
                           ("modules.sightcircuit/hud/contract.py", "missing"))

    def test_fails_for_a_rebuilt_or_missing_bridge(self):
        self.write()
        self.bridge.write_bytes(b"bridge rebuilt with another compiler")
        report = self.assertDiffers((f"bridge.{manifest.BRIDGE}", "changed"))
        self.assertEqual(len(report["differences"]), 1)  # The ignored binary leaves the tree clean.
        self.bridge.unlink()
        self.assertDiffers((f"bridge.{manifest.BRIDGE}", "changed"))

    def test_fails_for_a_modified_tracked_file(self):
        self.write()
        (self.root / "README.md").write_text("Edited\n")  # Tracked, outside the hashed groups.
        report = self.assertDiffers(("git.dirty", "changed"))
        self.assertEqual(len(report["differences"]), 1)
        self.assertIn(" M README.md", report["differences"][0]["paths"])

    def test_fails_for_an_untracked_file(self):
        self.write()
        (self.root / "notes.txt").write_text("not ignored\n")
        report = self.assertDiffers(("git.dirty", "changed"))
        self.assertEqual(len(report["differences"]), 1)
        self.assertEqual(report["differences"][0]["paths"], ["?? notes.txt"])
        for number in range(manifest.MAX_REPORTED_PATHS + 5):
            (self.root / f"notes-{number:02}.txt").write_text("")
        _, report = self.verify()
        self.assertEqual(report["differences"][0]["path_count"], manifest.MAX_REPORTED_PATHS + 6)
        self.assertEqual(len(report["differences"][0]["paths"]), manifest.MAX_REPORTED_PATHS)

    def test_a_staged_rename_counts_both_paths_with_their_status(self):
        self.write()
        self.git("mv", "README.md", "NOTES.md")
        report = self.assertDiffers(("git.dirty", "changed"))
        self.assertEqual((report["differences"][0]["path_count"], report["differences"][0]["paths"]),
                         (2, ["A  NOTES.md", "D  README.md"]))

    def test_ignored_files_do_not_dirty_the_tree(self):
        self.write()
        (self.root / "artifacts/hud-calibration").mkdir(parents=True)
        (self.root / "artifacts/hud-calibration/observer.json").write_text("{}\n")
        (self.root / "sightcircuit/__pycache__").mkdir()
        (self.root / "sightcircuit/__pycache__/gates.cpython-312.pyc").write_bytes(b"cache")
        self.assertVerifies()

    def test_fails_for_a_different_architecture(self):
        self.write()
        other = "aarch64" if platform.machine() != "aarch64" else "x86_64"
        with patch("platform.machine", return_value=other):
            report = self.assertDiffers(("platform.machine", "changed"))
        self.assertEqual([(item["expected"], item["actual"]) for item in report["differences"]],
                         [(platform.machine(), other)])

    def test_fails_for_a_different_os(self):
        self.write()
        with patch("platform.freedesktop_os_release", return_value={"ID": "other-os", "VERSION_ID": "1"}):
            self.assertDiffers(("platform.os_release.id", "changed"))
        with patch("platform.release", return_value="0.0.0-other"):
            self.assertDiffers(("platform.kernel_release", "changed"))

    def test_steamvr_package_is_read_from_the_local_database_best_effort_and_pinned(self):
        """A3 addendum (2026-11-07): the manifest records the Frame's SteamVR package identity, unknown when absent."""
        name = manifest.STEAMVR_PACKAGE
        self.assertEqual(name, "deckard-steamvr-rel")  # As E85 read it with pacman -Q.
        path = str(self.pacman)
        readable = {"name": name, "source": "pacman_local_database", "database": path, "databases": {path: "read"},
                    "version": None, "reason": None, "query": None}
        # An empty database, a look-alike name, an unreadable or invalid description.
        self.assertEqual(manifest.steamvr_package(), dict(readable, reason="package_not_installed"))
        other = self.install_package(f"{name}-tools", "1.0-1")
        self.assertEqual(manifest.steamvr_package(), dict(readable, reason="package_not_installed"))
        broken = self.install_package(name, "r1-1", entry=f"{name}-r1-1", desc="%NAME%\n" + name + "\n\n%VERSION%\nbad version\n")
        self.assertEqual(manifest.steamvr_package(), dict(readable, reason="package_version_invalid"))
        (broken / "desc").unlink()
        (broken / "desc").mkdir()  # A directory where the description should be: unreadable.
        self.assertEqual(manifest.steamvr_package(), dict(readable, reason="package_description_unreadable"))
        shutil.rmtree(broken)
        shutil.rmtree(other)
        # The installed package, as E85 recorded it after the SteamOS 0.5.8 update; no command runs.
        installed = self.install_package(name, "r26496310+24cb4748-1")
        with patch("subprocess.run", side_effect=AssertionError("steamvr_package must not run a command")), \
                patch.object(manifest, "pacman_query", side_effect=AssertionError("no query while a database reads")):
            self.assertEqual(manifest.steamvr_package(), dict(readable, version="r26496310+24cb4748-1"))
        previous = self.install_package(name, "r25765468+0ab5c24d-1")
        self.assertEqual(manifest.steamvr_package(), dict(readable, reason="package_ambiguous"))
        shutil.rmtree(previous)
        # Pinned by the manifest: an updated or vanished package is a difference, and unknown is not a problem.
        data = self.write()
        self.assertEqual(data["problems"], [])
        self.assertEqual(json.loads(self.path.read_text())["platform"]["steamvr_package"]["version"],
                         "r26496310+24cb4748-1")
        self.assertVerifies()
        (installed / "desc").write_text(f"%NAME%\n{name}\n\n%VERSION%\nr26496311+ffffffff-1\n")
        report = self.assertDiffers(("platform.steamvr_package.version", "changed"))
        self.assertEqual([(item["expected"], item["actual"]) for item in report["differences"]
                          if item["field"] == "platform.steamvr_package.version"],
                         [("r26496310+24cb4748-1", "r26496311+ffffffff-1")])
        shutil.rmtree(installed)
        self.assertDiffers(("platform.steamvr_package.version", "changed"), ("platform.steamvr_package.reason", "changed"))

    def test_steamvr_package_names_a_permission_problem_and_falls_back_to_a_read_only_query(self):
        """A3b (2026-11-07): the A4 preparation found the database unreadable as the steamos user while
        ``pacman -Q`` answered; the reason names the permission problem apart from an absent database, and
        the query runs only when no database could be read."""
        name = manifest.STEAMVR_PACKAGE
        absent, denied = self.pacman / "absent", self.pacman / "denied"
        denied.mkdir()
        unknown = {"name": name, "source": None, "database": None, "version": None, "reason": None, "query": None}
        real_iterdir = Path.iterdir

        def iterdir(path):
            if path == denied:
                raise PermissionError(13, "Permission denied", str(path))
            return real_iterdir(path)

        unavailable = lambda name, timeout=None: (None, "pacman_unavailable")  # noqa: E731
        answers = lambda name, timeout=None: ("r26496310+24cb4748-1", None)  # noqa: E731
        with patch.object(Path, "iterdir", iterdir):
            # No readable database at all: absent is "unavailable", a permission failure is named as such.
            self.assertEqual(manifest.steamvr_package(databases=(absent,), query=unavailable),
                             dict(unknown, databases={str(absent): "absent"}, reason="package_database_unavailable",
                                  query="pacman_unavailable"))
            self.assertEqual(manifest.steamvr_package(databases=(absent, denied), query=unavailable),
                             dict(unknown, databases={str(absent): "absent", str(denied): "permission_denied"},
                                  reason="package_database_permission_denied", query="pacman_unavailable"))
            # The read-only query answers where no database could be read, and the record says so.
            self.assertEqual(manifest.steamvr_package(databases=(denied,), query=answers),
                             dict(unknown, databases={str(denied): "permission_denied"}, source="pacman_query",
                                  version="r26496310+24cb4748-1"))
            # A later readable database answers without any query, and a denied one is still recorded.
            self.install_package(name, "r26496310+24cb4748-1")
            with patch.object(manifest, "pacman_query", side_effect=AssertionError("no query while a database reads")):
                self.assertEqual(manifest.steamvr_package(databases=(denied, self.pacman)),
                                 dict(unknown, source="pacman_local_database", database=str(self.pacman),
                                      databases={str(denied): "permission_denied", str(self.pacman): "read"},
                                      version="r26496310+24cb4748-1"))
            # Written and verified with the permission reason: a self-consistent unknown is not a problem.
            with patch.object(manifest, "pacman_local_databases", lambda config=None: (denied,)):
                data = self.write()
                self.assertEqual(data["problems"], [])
                recorded = json.loads(self.path.read_text())["platform"]["steamvr_package"]
                self.assertEqual((recorded["reason"], recorded["version"]), ("package_database_permission_denied", None))
                self.assertVerifies()

    def test_fails_for_a_different_python(self):
        self.write()
        with patch("platform.python_version", return_value="3.13.0"):
            self.assertDiffers(("python.version", "changed"))
        with patch("platform.python_implementation", return_value="PyPy"):
            self.assertDiffers(("python.implementation", "changed"))

    def test_fails_for_a_missing_or_different_package(self):
        self.write()
        with patch.dict(VERSIONS, {"numpy": "2.3.0"}):
            self.assertDiffers(("packages.numpy", "changed"))
        with patch.dict(VERSIONS, clear=False):
            del VERSIONS["opencv-python-headless"]
            self.assertDiffers(("packages.opencv-python-headless", "changed"))

    def test_fails_for_a_changed_script_or_native_source(self):
        self.write()
        (self.root / "scripts/hud_marker_observer.py").write_text("# changed\n")
        (self.root / "tools/hud_contract.hpp").write_text("// changed\n")
        self.git("commit", "-q", "-am", "change the checkpoint")
        self.assertDiffers(("scripts.scripts/hud_marker_observer.py", "changed"),
                           ("native_sources.tools/hud_contract.hpp", "changed"))

    def test_fails_for_a_changed_model(self):
        self.write()
        self.asset.write_bytes(b"another model")
        self.assertDiffers((f"assets.{manifest.ASSET_DIRECTORY}/model.onnx", "changed"))

    def test_fails_for_an_edited_or_foreign_manifest(self):
        self.write()
        data = json.loads(self.path.read_text())
        cases = {"schema": {**data, "schema_version": manifest.SCHEMA_VERSION + 1},
                 "kind": {**data, "report_kind": "other"},
                 "scope": {**data, "scope": "device evidence"}, "extra": {**data, "extra": 1},
                 "type": {**data, "git": {**data["git"], "dirty": 0}}}
        fields = {"schema": "schema_version", "kind": "report_kind", "scope": "scope", "extra": "extra",
                  "type": "git.dirty"}
        for name, edited in cases.items():
            with self.subTest(name=name):
                self.path = self.out / f"{name}.json"
                self.path.write_text(json.dumps(edited))
                self.assertDiffers((fields[name], "missing" if name == "extra" else "changed"))

    def test_unreadable_manifest_fails_without_a_traceback(self):
        deep = 1200  # Parses, but deeper than the report encoder can recurse.
        for name, content in (("missing", None), ("invalid", "{"), ("list", "[]"), ("large", " " * (2 << 20)),
                              ("non-finite", '{"schema_version": NaN}'), ("overflow", '{"schema_version": 1e400}'),
                              ("negative-overflow", '{"git": {"dirty": [-1e999]}}'),
                              ("deep-array", '{"git": ' + "[" * deep + "]" * deep + "}"),
                              ("deep-object", '{"git": ' + '{"a": ' * deep + "1" + "}" * deep + "}")):
            with self.subTest(name=name):
                self.path = self.out / f"{name}.json"
                if content is not None:
                    self.path.write_text(content)
                code, report = self.verify()
                self.assertEqual((code, report["status"]), (1, "error"))
                self.assertEqual([item["reason"] for item in report["differences"]], ["unreadable_manifest"])

    def test_verify_never_writes(self):
        self.write()
        self.out.chmod(0o555)
        self.path.chmod(0o444)
        self.addCleanup(self.out.chmod, 0o755)
        for change in (None, "module"):
            with self.subTest(change=change):
                if change == "module":
                    (self.root / "sightcircuit/gates.py").write_text("OPEN = True\n")
                before = tree_state(self.root, self.out)
                code, _ = self.verify()
                self.assertEqual(code, 1 if change else 0)
                self.assertEqual(tree_state(self.root, self.out), before)


class DigestTests(unittest.TestCase):
    def test_verify_file_digest_checks_content_not_format(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "report.json")
            path.write_bytes(b"evidence")
            digest = hashlib.sha256(b"evidence").hexdigest()
            self.assertTrue(manifest.verify_file_digest(path, digest))
            self.assertFalse(manifest.verify_file_digest(path, hashlib.sha256(b"other").hexdigest()))
            self.assertFalse(manifest.verify_file_digest(Path(directory, "absent"), digest))
            self.assertFalse(manifest.verify_file_digest(Path(directory), digest))
            for invalid in (digest.upper(), digest[:-1], None, b"0" * 64):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    manifest.verify_file_digest(path, invalid)

    def test_real_package_lookup_records_an_absent_distribution_as_null(self):
        self.assertEqual(manifest.package_versions({"absent": "sightcircuit-absent-distribution"}),
                         {"sightcircuit-absent-distribution": None})


class PacmanLookupTests(unittest.TestCase):
    """The real candidate list and the real read-only query, on temporary files and a fake pacman."""

    def test_pacman_database_candidates_follow_the_configured_dbpath_then_the_known_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "pacman.conf"
            config.write_text("[options]\n#DBPath = /var/lib/pacman/\nDBPath = /usr/lib/holo/pacmandb/  # SteamOS\n"
                              "HoldPkg = pacman glibc\n")
            self.assertEqual(manifest.pacman_local_databases(config),
                             (Path("/usr/lib/holo/pacmandb/local"), Path("/var/lib/pacman/local")))
            config.write_text("[options]\nDBPath = relative/path\n")
            self.assertEqual(manifest.pacman_local_databases(config),
                             (Path("/var/lib/pacman/local"), Path("/usr/lib/holo/pacmandb/local")))
            self.assertEqual(manifest.pacman_local_databases(Path(directory) / "missing.conf"),
                             (Path("/var/lib/pacman/local"), Path("/usr/lib/holo/pacmandb/local")))

    def test_pacman_query_is_read_only_bounded_and_names_its_failures(self):
        name = manifest.STEAMVR_PACKAGE
        with tempfile.TemporaryDirectory() as directory:
            pacman = Path(directory) / "pacman"
            cases = ((f"#!/bin/sh\n[ \"$1\" = -Q ] && [ \"$2\" = {name} ] || exit 2\necho '{name} r26496310+24cb4748-1'\n",
                      ("r26496310+24cb4748-1", None)),
                     ("#!/bin/sh\necho 'error: package was not found' >&2\nexit 1\n", (None, "package_not_installed")),
                     ("#!/bin/sh\necho 'other-package 1.0-1'\n", (None, "pacman_query_invalid")),
                     (f"#!/bin/sh\necho '{name} 1.0-1 extra'\n", (None, "pacman_query_invalid")))
            for script, expected in cases:
                pacman.write_text(script)
                pacman.chmod(0o700)
                with self.subTest(expected=expected), patch.dict(os.environ, {"PATH": directory}):
                    self.assertEqual(manifest.pacman_query(name), expected)
            with patch.dict(os.environ, {"PATH": str(Path(directory) / "empty")}):
                self.assertEqual(manifest.pacman_query(name), (None, "pacman_unavailable"))
            pacman.write_text("#!/bin/sh\n/bin/sleep 5\n")
            pacman.chmod(0o700)
            with patch.dict(os.environ, {"PATH": directory}):
                self.assertEqual(manifest.pacman_query(name, timeout=0.2), (None, "pacman_query_failed"))


def section(path, heading):
    """The text under ``heading`` up to the next heading of the same level."""
    marker = heading.split(" ", 1)[0]
    text = (ROOT / path).read_text()
    return text.split(f"\n{heading}\n", 1)[1].split(f"\n{marker} ", 1)[0]


def code_blocks(text):
    return re.findall(r"```[^\n]*\n(.*?)```", text, re.S)


PROCEDURES = (("docs/guides/hud-checkpoint.md", "## Bounded edge HUD calibration checkpoint"),)
BRIDGE_RUNS = ("scripts/hud_marker_check.py", "scripts/hud_marker_observer.py")
BRIDGE_UNITS = tuple(path for path in manifest.NATIVE_SOURCES if path.endswith(".cpp"))


def local_includes(path, seen=None):
    """The pinned-style paths of every local header ``path`` includes, transitively."""
    seen = set() if seen is None else seen
    for name in re.findall(r'^#include "([\w.]+)"', path.read_text(), re.MULTILINE):
        header = path.parent / name
        pinned = f"tools/{name}"
        if header.is_file() and pinned not in seen:
            seen.add(pinned)
            local_includes(header, seen)
    return seen


def procedure_blocks():
    """Code blocks of the canonical documented checkpoint procedure."""
    return [block for path, heading in PROCEDURES for block in code_blocks(section(path, heading))]


def imported_modules(path):
    """Absolute module names a file imports, however deeply nested."""
    package = list(path.relative_to(ROOT).parts[:-1])
    names = set()
    for node in ast.walk(ast.parse(path.read_text(), str(path))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = ".".join(package[:len(package) - node.level + 1] + [node.module or ""]).strip(".") \
                if node.level else node.module
            names.add(base)
            names.update(f"{base}.{alias.name}" for alias in node.names)
    return names


class ProcedureDriftTests(unittest.TestCase):
    """The pinned lists stay in step with the documented checkpoint and its imports."""

    def test_every_script_the_procedure_runs_is_pinned(self):
        named = {match for block in procedure_blocks() for match in re.findall(r"scripts/[\w.-]+\.(?:py|sh)", block)}
        self.assertLessEqual({"scripts/hud_marker_check.py", "scripts/hud_marker_observer.py",
                              "scripts/build_overlay_bridge.sh", "scripts/checkpoint_manifest.py"}, named)
        self.assertLessEqual(named, set(manifest.CHECKPOINT_SCRIPTS))

    def test_pinned_files_exist_and_are_listed_once(self):
        listed = manifest.CHECKPOINT_SCRIPTS + manifest.NATIVE_SOURCES + (manifest.ASSET_MANIFEST,)
        self.assertEqual(len(listed), len(set(listed)))
        for path in listed:
            with self.subTest(path=path):
                self.assertTrue((ROOT / path).is_file())

    def test_every_bridge_unit_and_every_local_header_it_includes_is_pinned(self):
        # The pinned sources are exactly the bridge's translation units plus the local headers they reach;
        # a new unit or header must be pinned, and a pinned header must still be included somewhere.
        self.assertIn("tools/overlay_bridge.cpp", BRIDGE_UNITS)
        included = set()
        for unit in BRIDGE_UNITS:
            included |= local_includes(ROOT / unit)
        self.assertLessEqual({"tools/overlay_bridge.hpp", "tools/overlay_lifecycle.hpp", "tools/overlay_frame.hpp",
                              "tools/hud_contract.hpp"}, included)
        self.assertEqual(included, set(manifest.NATIVE_SOURCES) - set(BRIDGE_UNITS))

    def test_the_build_and_the_ci_syntax_check_compile_exactly_the_pinned_units(self):
        build = (ROOT / "scripts/build_openvr_tool.sh").read_text()
        case = build.split("overlay-bridge)", 1)[1].split(";;", 1)[0]
        self.assertEqual({f"tools/{name}" for name in re.findall(r"\b(\w+\.cpp)\b", case)}, set(BRIDGE_UNITS))
        workflow = (ROOT / ".github/workflows/check-jobs.yml").read_text()
        syntax_checks = [line for line in workflow.splitlines() if "-fsyntax-only" in line]
        self.assertEqual(len(syntax_checks), 1)
        self.assertIn("-Werror", syntax_checks[0])
        self.assertEqual(set(re.findall(r"tools/\w+\.cpp", syntax_checks[0])), set(BRIDGE_UNITS))

    def test_each_procedure_verifies_before_it_first_runs_the_bridge(self):
        for path, heading in PROCEDURES:
            with self.subTest(path=path):
                lines = "\n".join(code_blocks(section(path, heading))).replace("\\\n", " ").splitlines()
                verify = [index for index, line in enumerate(lines)
                          if "scripts/checkpoint_manifest.py" in line and "--verify" in line]
                runs = [index for index, line in enumerate(lines) if any(run in line for run in BRIDGE_RUNS)]
                self.assertTrue(verify and runs, path)
                self.assertLess(verify[0], runs[0], lines[runs[0]].strip())

    def test_documented_manifest_commands_parse_with_the_real_parser(self):
        modes = set()
        for block in procedure_blocks():
            for line in block.replace("\\\n", " ").splitlines():
                if "scripts/checkpoint_manifest.py" not in line:
                    continue
                tokens = shlex.split(line)
                arguments = tokens[tokens.index("scripts/checkpoint_manifest.py") + 1:]
                for index, token in enumerate(arguments):
                    if token[:1] in (">", "|", "&", ";"):
                        arguments = arguments[:index]
                        break
                with self.subTest(line=line.strip()), contextlib.redirect_stderr(io.StringIO()):
                    args = command.build_parser().parse_args(arguments)
                    modes.update(mode for mode in ("write", "verify") if getattr(args, mode) is not None)
        self.assertEqual(modes, {"write", "verify"})

    def test_pinned_packages_are_the_third_party_imports_of_the_checkpoint(self):
        seen, third_party = set(), set()
        pending = [ROOT / path for path in manifest.CHECKPOINT_SCRIPTS if path.endswith(".py")]
        while pending:
            path = pending.pop()
            if path in seen:
                continue
            seen.add(path)
            for name in imported_modules(path):
                parts = name.split(".")
                if parts[0] == "sightcircuit":
                    for end in range(1, len(parts) + 1):
                        base = ROOT.joinpath(*parts[:end])
                        pending += [candidate for candidate in (base / "__init__.py", base.with_suffix(".py"))
                                    if candidate.is_file()]
                elif parts[0] not in sys.stdlib_module_names:
                    third_party.add(parts[0])
        self.assertIn(ROOT / "sightcircuit/hud/observation.py", seen)
        self.assertEqual(third_party, set(manifest.CHECKPOINT_PACKAGES))
        installed = metadata.packages_distributions()
        for module, distribution in manifest.CHECKPOINT_PACKAGES.items():
            with self.subTest(module=module):
                self.assertIn(distribution, installed.get(module, [distribution]))


if __name__ == "__main__":
    unittest.main()
