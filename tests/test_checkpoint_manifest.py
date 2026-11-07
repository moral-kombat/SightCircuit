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
                 manifest.ASSET_MANIFEST: json.dumps({"assets": [{
                     "name": "model.onnx", "url": "https://example.invalid/model.onnx",
                     "sha256": hashlib.sha256(ASSET).hexdigest()}]})}
        files.update({path: f"# {path}\n" for path in manifest.CHECKPOINT_SCRIPTS + manifest.NATIVE_SOURCES})
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
        self.path = self.out / "checkpoint-manifest.json"

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
        self.assertEqual((data["schema_version"], data["report_kind"]), (1, "checkpoint_artifact_manifest"))
        self.assertIn("software identity only", data["scope"])
        self.assertIn("not device", data["scope"])
        self.assertEqual(data["git"], {"commit": self.git("rev-parse", "HEAD").strip(), "dirty": False})
        self.assertEqual(len(data["git"]["commit"]), 40)
        self.assertEqual(data["python"], {"implementation": platform.python_implementation(),
                                          "version": platform.python_version()})
        self.assertEqual(data["platform"], {"machine": platform.machine(), "system": platform.system(),
                                            "kernel_release": platform.release(),
                                            "os_release": manifest.os_release()})
        self.assertEqual(set(data["platform"]["os_release"]), {"id", "version_id", "build_id"})
        self.assertEqual(data["bridge"], {manifest.BRIDGE: hashlib.sha256(self.bridge.read_bytes()).hexdigest()})
        self.assertEqual(sorted(data["modules"]), ["sightcircuit/__init__.py", "sightcircuit/gates.py",
                                                   "sightcircuit/hud/__init__.py", "sightcircuit/hud/contract.py"])
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
        cases = {"schema": {**data, "schema_version": 2}, "kind": {**data, "report_kind": "other"},
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


def section(path, heading):
    """The text under ``heading`` up to the next heading of the same level."""
    marker = heading.split(" ", 1)[0]
    text = (ROOT / path).read_text()
    return text.split(f"\n{heading}\n", 1)[1].split(f"\n{marker} ", 1)[0]


def code_blocks(text):
    return re.findall(r"```[^\n]*\n(.*?)```", text, re.S)


PROCEDURES = (("docs/hud-qualification.md", "## Next device checkpoint"),
              ("docs/on-device.md", "### Bounded edge HUD calibration checkpoint"))
BRIDGE_RUNS = ("scripts/hud_marker_check.py", "scripts/hud_marker_observer.py")


def procedure_blocks():
    """Code blocks of both documented checkpoint procedures."""
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

    def test_every_local_header_the_bridge_includes_is_pinned(self):
        source = (ROOT / "tools/overlay_bridge.cpp").read_text()
        included = {f"tools/{name}" for name in re.findall(r'^#include "([\w.]+)"', source, re.MULTILINE)
                    if (ROOT / "tools" / name).is_file()}
        self.assertIn("tools/overlay_lifecycle.hpp", included)
        self.assertLessEqual(included, set(manifest.NATIVE_SOURCES))

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
