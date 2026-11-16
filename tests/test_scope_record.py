"""The one-run scope record (work-order item L10): what it permits, and everything it refuses.

Every case builds its own temporary git checkout with a checkpoint manifest
(the fixture of ``tests/test_checkpoint_manifest.py``) and points
``scope.ROOT`` at it; the repository's own files are only read. A record that
verifies here is a test fixture record: nothing in these tests is SteamVR, the
SDK, a display or a device, and no gate is open before, during or after.
"""
import ast
import contextlib
import dataclasses
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from sightcircuit import gates, observed_frames
from sightcircuit.errors import SightCircuitError
from sightcircuit.hud.contract import HUD_CONFIG
from sightcircuit.observed_frames import ObservedFrameClient
from sightcircuit.runtime import manifest, scope
from sightcircuit.sources import camera_helper
from sightcircuit.sources.camera_helper import CameraHelperSource
from tests import test_checkpoint_manifest as checkpoint

ROOT = Path(__file__).resolve().parents[1]
# The stage and the live runner need NumPy and OpenCV; the record, the writer and the output site do not.
NUMPY_CV2 = bool(importlib.util.find_spec("numpy") and importlib.util.find_spec("cv2"))
if NUMPY_CV2:
    from sightcircuit.hud import camera_stage
    from sightcircuit.runtime import live, synthetic
LIMITS = {"fps": 5, "max_seconds": 5, "max_frames": 8}
ALL_CLOSED = {"production_live_input_enabled": False, "production_camera_input_enabled": False,
              "mapping_qualified": False, "production_hud_enabled": False}


class ScopeCase(checkpoint.CheckoutCase):
    """A clean checkout with a verified manifest; ``scope.ROOT`` is that checkout for the test alone."""

    def setUp(self):
        super().setUp()
        self.write()  # The checkpoint manifest, at self.path.
        root = mock.patch.object(scope, "ROOT", self.root)
        root.start()
        self.addCleanup(root.stop)
        self.records = 0

    def staged(self, step="L-D4", **options):
        """A record the writer made, as the Frame manager's tool would: ``(path, record)``."""
        self.records += 1
        path = self.out / f"record-{self.records}.json"
        options.setdefault("valid_minutes", 30)
        record = scope.write_record(path, step=step, approval="owner-2026-11-16-a1",
                                    checkpoint_manifest=self.path, **options)
        return path, record

    def forged(self, base="L-D4", **changes):
        """A record with ``changes`` written by hand, as a mistake or an edit would leave it."""
        path, record = self.staged(base)
        record.update(changes)
        path.write_bytes(manifest.encode(record))
        return path

    def run_scope(self, path, *, source=scope.CAMERA_LEFT, seconds=20, runner="camera_hud", **options):
        return scope.RunScope(path, options.pop("checkpoint_manifest", self.path), runner=runner, source=source,
                              seconds=seconds, **options)

    @contextlib.contextmanager
    def launches(self, stop=None):
        """Every process started inside, except the verification's own read-only git queries: ``[(argv, ledger)]``.

        With ``stop`` the launch raises ``RuntimeError(stop)`` instead of starting anything.
        """
        real, seen = subprocess.Popen, []

        def launch(arguments, *more, **options):
            if arguments[0] == "git":
                return real(arguments, *more, **options)
            seen.append((list(map(str, arguments)), self.ledger()))
            raise RuntimeError(stop or "a process was launched")
        with mock.patch.object(subprocess, "Popen", side_effect=launch):
            yield seen

    def ledger(self):
        folder = self.root / scope.LEDGER
        return sorted(entry.name for entry in folder.iterdir()) if folder.is_dir() else []

    def assert_refused(self, run, reason, *, consumed=False):
        self.assertIs(run.permit(scope.HUD_OUTPUT), False)
        self.assertIs(run.permit(scope.CAMERA_INPUT), False)
        self.assertEqual((run.status, run.refusal, run.consumed), ("refused", reason, consumed))
        self.assertEqual(gates.claims(), ALL_CLOSED)


class WriterTests(ScopeCase):
    def test_a_record_names_the_step_the_run_and_what_is_on_disk(self):
        before = int(time.time())
        path, record = self.staged("L-D5", valid_minutes=45)
        self.assertEqual(json.loads(path.read_text()), record)
        self.assertEqual(path.read_bytes(), manifest.encode(record))
        self.assertEqual(set(record), set(scope.FIELDS))
        self.assertEqual((record["report_kind"], record["schema_version"], record["step"], record["runner"],
                          record["source"], record["hud_config_id"], record["max_seconds"], record["approval"]),
                         ("one_run_scope_record", 1, "L-D5", "camera_hud", "arcturus_camera_left",
                          "fixed-edge-hud-v1", 90, "owner-2026-11-16-a1"))
        self.assertEqual(record["revision"], self.git("rev-parse", "HEAD").strip())
        self.assertEqual(record["manifest_sha256"], scope._file_digest(self.path))
        self.assertRegex(record["run_id"], r"\A[0-9a-f]{32}\Z")
        created, deadline = scope._utc(record["created_utc"]), scope._utc(record["deadline_utc"])
        self.assertEqual(deadline - created, 45 * 60)
        self.assertLessEqual(before, created)
        self.assertIn("not a cryptographic one", record["statement"])
        self.assertIn("opens no gate", record["statement"])
        self.assertIsNone(scope.check_record(record))
        self.assertEqual(self.ledger(), [])  # Writing consumes nothing.
        self.assertNotEqual(self.staged("L-D5")[1]["run_id"], record["run_id"])

    def test_the_steps_sources_and_bounds_are_the_work_orders(self):
        self.assertEqual(scope.STEPS, {"L-D3": ("synthetic", 120), "L-D4": ("arcturus_camera_left", 30),
                                       "L-D5": ("arcturus_camera_left", 90)})
        self.assertEqual(scope.USES, {"synthetic": ("hud_output",), "arcturus_camera_left": ("hud_output", "camera_input")})
        self.assertEqual((scope.RUNNER, scope.MAX_VALID_MINUTES), ("camera_hud", 240))
        self.assertEqual(self.staged("L-D3")[1]["source"], "synthetic")
        self.assertEqual(self.staged("L-D4", max_seconds=10)[1]["max_seconds"], 10)

    def test_the_writer_refuses_what_it_must_not_record(self):
        target = self.out / "refused.json"
        cases = (
            ("scope_step_unknown", dict(step="L-D1")),
            ("scope_step_unknown", dict(step="L-D2")),
            ("scope_approval_invalid", dict(approval="the owner said yes on the phone")),
            ("scope_approval_invalid", dict(approval="")),
            ("scope_approval_invalid", dict(approval=True)),
            ("scope_duration_over_bound", dict(step="L-D4", max_seconds=31)),
            ("scope_duration_over_bound", dict(step="L-D5", max_seconds=91)),
            ("scope_duration_over_bound", dict(step="L-D3", max_seconds=0)),
            ("scope_duration_over_bound", dict(max_seconds=True)),
            ("scope_validity_invalid", dict(valid_minutes=0)),
            ("scope_validity_invalid", dict(valid_minutes=241)),
            ("scope_validity_invalid", dict(valid_minutes=30.0)),
            ("scope_record_path_invalid", dict(path=self.out / "record.txt")),
            ("scope_record_path_invalid", dict(path=self.path)),  # An existing file is never replaced.
            ("scope_record_path_invalid", dict(path=self.out / "missing" / "record.json")),
            ("scope_record_would_dirty_tree", dict(path=self.root / "record.json")),
            ("scope_manifest_not_verified", dict(checkpoint_manifest=self.out / "absent.json")),
        )
        for reason, changes in cases:
            options = {"path": target, "step": "L-D4", "approval": "owner-2026-11-16-a1",
                       "checkpoint_manifest": self.path, "valid_minutes": 30, **changes}
            with self.subTest(reason=reason, changes=sorted(changes)):
                with self.assertRaises(scope.ScopeError) as caught:
                    scope.write_record(options.pop("path"), **options)
                self.assertEqual(caught.exception.reason, reason)
        self.assertFalse(target.exists())
        self.assertFalse((self.root / "record.json").exists())
        self.assertEqual(self.path.read_bytes(), manifest.encode(json.loads(self.path.read_text())))

    def test_the_writer_refuses_a_checkout_the_manifest_does_not_verify(self):
        (self.root / "README.md").write_text("Edited\n")  # A dirty tree.
        with self.assertRaises(scope.ScopeError) as caught:
            self.staged()
        self.assertEqual(caught.exception.reason, "scope_manifest_not_verified")
        self.git("checkout", "--", "README.md")
        self.helper.write_bytes(b"camera helper rebuilt")  # Not the helper the manifest pinned.
        with self.assertRaises(scope.ScopeError) as caught:
            self.staged()
        self.assertEqual(caught.exception.reason, "scope_manifest_not_verified")

    def test_the_staging_tool_writes_through_the_library_and_is_not_a_command(self):
        from sightcircuit.commands import COMMANDS
        import tomllib
        self.assertNotIn("stage_scope_record", COMMANDS)
        self.assertFalse((ROOT / "sightcircuit/commands/stage_scope_record.py").exists())
        entry_points = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["scripts"]
        self.assertFalse([name for name, target in entry_points.items() if "scope" in name or "scope" in target])
        self.assertIn("scripts/stage_scope_record.py", manifest.CAMERA_HUD_FILES)
        target = self.out / "tool.json"
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts/stage_scope_record.py"), "--step", "L-D3", "--approval", "a1",
             "--checkpoint-manifest", str(self.path), "--valid-minutes", "10", "--out", str(target)],
            capture_output=True, text=True, timeout=60)
        # The tool is bound to its own checkout (the repository under development), where this fixture's
        # manifest does not verify: nothing is written, and it says why.
        self.assertEqual((completed.returncode, target.exists()), (1, False), completed.stderr)
        self.assertIn("scope_manifest_not_verified", completed.stderr)
        from scripts import stage_scope_record
        with mock.patch("sys.stdout"):
            code = stage_scope_record.main(["--step", "L-D3", "--approval", "a1", "--checkpoint-manifest",
                                            str(self.path), "--valid-minutes", "10", "--out", str(target)])
        self.assertEqual(code, 0)
        self.assertIsNone(scope.check_record(json.loads(target.read_text())))

    def test_nothing_but_the_staging_tool_calls_the_writer(self):
        callers = []
        for path in sorted([*(ROOT / "sightcircuit").rglob("*.py"), *(ROOT / "scripts").glob("*.py")]):
            names = {node.attr if isinstance(node, ast.Attribute) else getattr(node, "id", None)
                     for node in ast.walk(ast.parse(path.read_text(), str(path)))}
            if "write_record" in names:
                callers.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(callers, ["scripts/stage_scope_record.py"])  # The definition itself is in scope.py.

    def test_the_scope_reads_no_environment_and_no_terminal(self):
        for name in ("sightcircuit/runtime/scope.py", "sightcircuit/gates.py"):
            tree = ast.parse((ROOT / name).read_text())
            used = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
            used |= {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
            with self.subTest(module=name):
                self.assertEqual(used & {"environ", "getenv", "environb", "input", "stdin", "argv", "isatty"}, set())


class GrantTests(ScopeCase):
    def test_a_verifying_record_permits_each_of_its_uses_once_and_spends_the_run_id(self):
        path, record = self.staged("L-D4")
        run = self.run_scope(path)
        self.assertEqual((run.status, self.ledger()), ("not_consulted", []))
        self.assertIs(run.permit(scope.HUD_OUTPUT), True)
        self.assertEqual(self.ledger(), [f"{record['run_id']}.json"])
        entry = json.loads((self.root / scope.LEDGER / f"{record['run_id']}.json").read_text())
        self.assertEqual((entry["run_id"], entry["step"], entry["process_id"]), (record["run_id"], "L-D4", os.getpid()))
        self.assertIs(run.permit(scope.CAMERA_INPUT), True)
        for use in (scope.HUD_OUTPUT, scope.CAMERA_INPUT, "anything", None):
            self.assertIs(run.permit(use), False)  # Once each, and nothing else.
        report = run.report()
        self.assertEqual((report["status"], report["refusal"], report["consumed"], report["step"], report["run_id"],
                          report["approval"], report["requested_source"]),
                         ("granted", None, True, "L-D4", record["run_id"], "owner-2026-11-16-a1", "arcturus_camera_left"))
        self.assertEqual(gates.claims(), ALL_CLOSED)  # A granted scope is not an open gate.
        self.assertEqual(self.git("status", "--porcelain"), "")  # The ledger is ignored: the tree stays clean.

    def test_the_synthetic_step_permits_output_and_never_camera_input(self):
        path, _ = self.staged("L-D3")
        run = self.run_scope(path, source=scope.SYNTHETIC, seconds=100)
        self.assertIs(run.permit(scope.CAMERA_INPUT), False)
        self.assertEqual(run.status, "granted")
        self.assertIs(run.permit(scope.HUD_OUTPUT), True)
        self.assertIs(run.permit(scope.CAMERA_INPUT), False)

    def test_a_granted_scope_stops_permitting_at_its_deadline(self):
        path, record = self.staged()
        run = self.run_scope(path)
        self.assertIs(run.permit(scope.HUD_OUTPUT), True)
        with mock.patch.object(scope.time, "time", return_value=scope._utc(record["deadline_utc"]) + 1):
            self.assertIs(run.permit(scope.CAMERA_INPUT), False)


class RefusalTests(ScopeCase):
    def test_no_record(self):
        for path in (self.out / "absent.json", self.out, None, ""):
            with self.subTest(path=path):
                self.assert_refused(self.run_scope(path), "scope_record_missing")
        self.assertEqual(self.ledger(), [])

    def test_an_expired_record_and_a_run_that_would_pass_the_deadline(self):
        past = scope._stamp(int(time.time()) - 60)
        self.assert_refused(self.run_scope(self.forged(created_utc=scope._stamp(int(time.time()) - 600),
                                                       deadline_utc=past)), "scope_record_expired")
        soon = scope._stamp(int(time.time()) + 10)
        self.assert_refused(self.run_scope(self.forged(deadline_utc=soon), seconds=20), "scope_run_would_pass_deadline")
        path, record = self.staged()
        with mock.patch.object(scope.time, "time", return_value=scope._utc(record["deadline_utc"])):
            self.assert_refused(self.run_scope(path), "scope_record_expired")
        self.assertEqual(self.ledger(), [])

    def test_a_wrong_revision(self):
        self.assert_refused(self.run_scope(self.forged(revision="0" * 40)), "scope_revision_mismatch")
        path, _ = self.staged()
        (self.root / "README.md").write_text("Another commit\n")
        self.git("commit", "-q", "-am", "the checkout moved after staging")
        self.assert_refused(self.run_scope(path), "scope_revision_mismatch")
        self.assertEqual(self.ledger(), [])

    def test_a_wrong_manifest_digest_and_a_manifest_that_no_longer_verifies(self):
        self.assert_refused(self.run_scope(self.forged(manifest_sha256="0" * 64)), "scope_manifest_digest_mismatch")
        path, _ = self.staged()
        other = self.out / "other-manifest.json"
        other.write_bytes(self.path.read_bytes() + b"\n")
        self.assert_refused(self.run_scope(path, checkpoint_manifest=other), "scope_manifest_digest_mismatch")
        self.assert_refused(self.run_scope(path, checkpoint_manifest=self.out / "absent.json"),
                            "scope_manifest_digest_mismatch")
        for name, change in (("helper", lambda: self.helper.write_bytes(b"camera helper rebuilt")),
                             ("bridge", lambda: self.bridge.write_bytes(b"bridge rebuilt")),
                             ("model", lambda: self.asset.write_bytes(b"another model")),
                             ("dirty", lambda: (self.root / "untracked.txt").write_text("left behind\n"))):
            with self.subTest(changed=name):
                change()
                self.assert_refused(self.run_scope(path), "scope_manifest_not_verified")
        self.assertEqual(self.ledger(), [])

    def test_a_reused_run_id(self):
        path, record = self.staged()
        self.assertIs(self.run_scope(path).permit(scope.HUD_OUTPUT), True)
        again = self.run_scope(path)
        self.assert_refused(again, "scope_run_id_used")
        copy = self.out / "copy.json"
        copy.write_bytes(path.read_bytes())
        self.assert_refused(self.run_scope(copy), "scope_run_id_used")
        self.assertEqual(self.ledger(), [f"{record['run_id']}.json"])

    def test_two_scopes_racing_for_one_record_grant_once(self):
        path, _ = self.staged()
        runs = [self.run_scope(path) for _ in range(4)]
        answers = []
        threads = [threading.Thread(target=lambda run=run: answers.append(run.permit(scope.HUD_OUTPUT))) for run in runs]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual(sorted(answers, key=bool), [False, False, False, True])
        self.assertEqual(len(self.ledger()), 1)

    def test_a_ledger_that_cannot_be_written_refuses(self):
        path, _ = self.staged()
        (self.root / "artifacts").mkdir(exist_ok=True)
        (self.root / scope.LEDGER).write_text("not a directory\n")
        self.assert_refused(self.run_scope(path), "scope_ledger_unavailable")

    def test_the_mirror_both_cameras_and_any_other_source(self):
        for source in ("steamvr_mirror", "mirror:observed", "arcturus_camera_both", "arcturus_camera_right",
                       "arcturus_camera", "replay", "", None, ["arcturus_camera_left"]):
            with self.subTest(recorded=source):
                self.assert_refused(self.run_scope(self.forged(source=source)), "scope_source_not_permitted")
            with self.subTest(requested=source):
                self.assert_refused(self.run_scope(self.staged()[0], source=source), "scope_source_not_permitted")
        self.assertEqual(self.ledger(), [])

    def test_a_record_for_another_steps_source(self):
        # The synthetic step's record never serves a camera run, nor a camera step's record the synthetic run.
        self.assert_refused(self.run_scope(self.staged("L-D3")[0], source=scope.CAMERA_LEFT), "scope_source_mismatch")
        self.assert_refused(self.run_scope(self.staged("L-D5")[0], source=scope.SYNTHETIC), "scope_source_mismatch")
        # A record whose step and source disagree is refused whatever the run asks for.
        for step, source in (("L-D3", scope.CAMERA_LEFT), ("L-D4", scope.SYNTHETIC), ("L-D5", scope.SYNTHETIC)):
            with self.subTest(step=step):
                self.assert_refused(self.run_scope(self.forged(step, source=source), source=source),
                                    "scope_step_source_mismatch")
        self.assert_refused(self.run_scope(self.forged(step="L-D1")), "scope_step_unknown")
        self.assertEqual(self.ledger(), [])

    def test_another_geometry(self):
        self.assert_refused(self.run_scope(self.forged(hud_config_id="validation-layout-v1")), "scope_geometry_mismatch")
        self.assert_refused(self.run_scope(self.forged(hud_config_sha256="0" * 64)), "scope_geometry_mismatch")
        other = dataclasses.replace(HUD_CONFIG, width_px=HUD_CONFIG.width_px * 2)
        self.assert_refused(self.run_scope(self.staged()[0], hud_config=other), "scope_geometry_mismatch")
        self.assert_refused(self.run_scope(self.staged()[0], hud_config=None), "scope_geometry_mismatch")
        self.assertEqual(self.ledger(), [])

    def test_another_runner(self):
        for runner in ("mirror_recognize", "live_pipeline", "camera_recognize", "replay_window", "hud_marker_observer",
                       "", None, True):
            with self.subTest(requested=runner):
                self.assert_refused(self.run_scope(self.staged()[0], runner=runner), "scope_runner_not_permitted")
            with self.subTest(recorded=runner):
                self.assert_refused(self.run_scope(self.forged(runner=runner)), "scope_runner_not_permitted")
        self.assertEqual(self.ledger(), [])

    def test_a_duration_over_the_bound(self):
        for step, source, over in (("L-D3", scope.SYNTHETIC, 121), ("L-D4", scope.CAMERA_LEFT, 31),
                                   ("L-D5", scope.CAMERA_LEFT, 91)):
            with self.subTest(step=step):
                self.assert_refused(self.run_scope(self.forged(step, max_seconds=over), source=source, seconds=1),
                                    "scope_duration_over_bound")
        self.assert_refused(self.run_scope(self.staged("L-D4")[0], seconds=30.5), "scope_duration_over_record")
        self.assert_refused(self.run_scope(self.staged("L-D4", max_seconds=10)[0], seconds=11), "scope_duration_over_record")
        for seconds in (0, -1, float("inf"), float("nan"), True, "20", None):
            with self.subTest(seconds=seconds):
                self.assert_refused(self.run_scope(self.staged()[0], seconds=seconds), "scope_duration_invalid")
        self.assertEqual(self.ledger(), [])

    def test_a_malformed_record(self):
        path, record = self.staged()
        cases = [{**record, "extra": True}, {name: value for name, value in record.items() if name != "approval"},
                 {**record, "approval": "free text is not a reference"}, {**record, "approval": 7},
                 {**record, "run_id": "A" * 32}, {**record, "run_id": "../" + "a" * 29},
                 {**record, "schema_version": 2}, {**record, "schema_version": True},
                 {**record, "report_kind": "checkpoint_artifact_manifest"}, {**record, "statement": "approved"},
                 {**record, "max_seconds": 20.0}, {**record, "max_seconds": True}, {**record, "max_seconds": 0},
                 {**record, "deadline_utc": "tomorrow"}, {**record, "deadline_utc": 1 << 40},
                 {**record, "deadline_utc": record["created_utc"]},
                 {**record, "deadline_utc": scope._stamp(scope._utc(record["created_utc"]) + 241 * 60)},
                 {**record, "created_utc": None}, {**record, "revision": "main"}, {**record, "manifest_sha256": None},
                 [record], "yes", True, 1, None]
        for index, case in enumerate(cases):
            with self.subTest(case=index):
                path.write_text(json.dumps(case))
                self.assert_refused(self.run_scope(path), "scope_record_invalid")
        for text in ("", "{", "true" * 2000):
            path.write_text(text)
            self.assert_refused(self.run_scope(path), "scope_record_invalid")
        self.assertEqual(self.ledger(), [])

    def test_an_error_inside_the_verification_is_a_refusal(self):
        path, _ = self.staged()
        with mock.patch.object(manifest, "verify_manifest", side_effect=RuntimeError("boom")):
            self.assert_refused(self.run_scope(path), "scope_verification_failed")
        self.assertEqual(self.ledger(), [])


class OutputSiteTests(ScopeCase):
    """The output site: only the literal True of a verifying record's scope permits, and before any launch."""

    def client(self, run=None, **options):
        return ObservedFrameClient(Path(sys.executable), validation_only=False, scope=run, **options)

    def test_real_output_is_permitted_only_by_a_verifying_scope_and_the_id_is_spent_before_the_launch(self):
        path, record = self.staged("L-D3")
        client = self.client(self.run_scope(path, source=scope.SYNTHETIC, seconds=100))
        with self.launches("the test stops at the launch") as seen, \
                mock.patch.object(gates.Gate, "require", autospec=True, side_effect=gates.Gate.require) as required:
            client.check_start()
            with self.assertRaisesRegex(RuntimeError, "the test stops at the launch"):
                client.start()
        self.assertEqual(seen, [([sys.executable, "--fixed-hud"], [f"{record['run_id']}.json"])])
        self.assertEqual([(call.args[0], call.args[1]) for call in required.call_args_list],
                         [(gates.LIVE_HUD_OUTPUT, True)] * 2)
        with self.launches() as seen, self.assertRaisesRegex(ValueError, "observed_frame_client_already_started"):
            client.start()  # One permitted start per client.
        self.assertEqual(seen, [])
        # The same scope on another client, and the same record in another scope: both refused, nothing launched.
        for other in (self.client(client.scope), self.client(self.run_scope(path, source=scope.SYNTHETIC, seconds=100))):
            with self.launches() as seen, self.assertRaisesRegex(ValueError, "^fixed_hud_output_not_approved$"):
                other.start()
            self.assertEqual(seen, [])
        self.assertEqual(gates.claims(), ALL_CLOSED)

    def test_real_output_refuses_everything_that_is_not_a_verifying_scope(self):
        path, _ = self.staged("L-D3")
        granted = self.run_scope(path, source=scope.SYNTHETIC)

        class Agreeable(scope.RunScope):
            def permit(self, use):
                return True
        truthy = mock.Mock()
        truthy.permit.return_value = True
        stand_ins = (None, True, 1, "yes", str(path), {"record": str(path)}, truthy, SimpleNamespace(permit=lambda use: True),
                     Agreeable(path, self.path, runner="camera_hud", source=scope.SYNTHETIC, seconds=20),
                     self.run_scope(self.out / "absent.json", source=scope.SYNTHETIC),
                     self.run_scope(path, source=scope.CAMERA_LEFT))
        for stand_in in stand_ins:
            client = self.client(stand_in)
            with self.subTest(scope=type(stand_in).__name__), self.launches() as seen:
                self.assertIs(observed_frames.fixed_hud_output_permitted(client), False)
                for start in (client.check_start, client.start):
                    with self.assertRaisesRegex(ValueError, "^fixed_hud_output_not_approved$"):
                        start()
                self.assertEqual(seen, [])
        self.assertIs(observed_frames.fixed_hud_output_permitted(SimpleNamespace(scope=granted)), False)  # Not a client.
        self.assertEqual((granted.status, self.ledger()), ("not_consulted", []))

    def test_the_output_gate_has_one_require_call_and_one_permission_function(self):
        self.assertEqual(require_calls("sightcircuit/observed_frames.py"), {"LIVE_HUD_OUTPUT": ["self._output_permitted"]})
        self.assertEqual(users_of("fixed_hud_output_permitted("), ["sightcircuit/observed_frames.py"])
        self.assertEqual(users_of(".permit("), ["sightcircuit/hud/camera_stage.py", "sightcircuit/observed_frames.py"])
        self.assertEqual(users_of("RunScope("), [])  # No library code builds a scope: the command will (L9).


@unittest.skipUnless(NUMPY_CV2, "The camera HUD stage needs NumPy and OpenCV")
class CameraSiteTests(ScopeCase):
    """The camera-input site and the stage's order: both checks, then the first launch."""

    def source(self):
        return CameraHelperSource(Path(self.out / "no-helper-is-launched"), LIMITS, confirmed=True)

    def client(self, run=None, **options):
        return ObservedFrameClient(Path(sys.executable), validation_only=False, scope=run, **options)

    def stage(self, client):
        return camera_stage.CameraHudStage(self.source(), synthetic.FixtureDetector(), client)

    def test_a_truthy_answer_that_is_not_true_is_refused_at_both_sites(self):
        path, _ = self.staged("L-D4")
        for answer in (1, "true", [True], mock.Mock(), object()):
            client = self.client(self.run_scope(path))
            stage = self.stage(client)
            with self.subTest(answer=repr(answer)[:20]), self.launches() as seen, \
                    mock.patch.object(camera_helper, "run_export") as exported:
                with mock.patch.object(observed_frames, "fixed_hud_output_permitted", return_value=answer), \
                        self.assertRaisesRegex(ValueError, "^fixed_hud_output_not_approved$"):
                    client.check_start()
                with mock.patch.object(observed_frames, "fixed_hud_output_permitted", return_value=True), \
                        mock.patch.object(camera_stage, "camera_display_input_permitted", return_value=answer), \
                        self.assertRaisesRegex(ValueError, "^camera_display_input_not_approved$"):
                    stage.run()
                self.assertEqual(seen, [])
                exported.assert_not_called()
        self.assertEqual(self.ledger(), [])

    def test_no_environment_variable_permits_either_use(self):
        path, _ = self.staged("L-D4")
        names = ("SIGHTCIRCUIT_SCOPE_RECORD", "SIGHTCIRCUIT_SCOPE", "SIGHTCIRCUIT_ALLOW_FIXED_HUD",
                 "SIGHTCIRCUIT_ALLOW_CAMERA_DISPLAY", "SIGHTCIRCUIT_CHECKPOINT_MANIFEST", "SIGHTCIRCUIT_RUN_ID",
                 "SIGHTCIRCUIT_DIAGNOSTIC_TIER", "SIGHTCIRCUIT_GATES_OPEN")
        values = dict.fromkeys(names, "1")
        values.update(SIGHTCIRCUIT_SCOPE_RECORD=str(path), SIGHTCIRCUIT_CHECKPOINT_MANIFEST=str(self.path))
        with mock.patch.dict(os.environ, values):
            client = self.client(env={**os.environ})
            stage = self.stage(client)
            with self.launches() as seen, mock.patch.object(camera_helper, "run_export") as exported:
                self.assertIs(observed_frames.fixed_hud_output_permitted(client), False)
                self.assertIs(camera_stage.camera_display_input_permitted(stage), False)
                with self.assertRaisesRegex(ValueError, "^fixed_hud_output_not_approved$"):
                    stage.run()
                self.assertEqual(seen, [])
                exported.assert_not_called()
            self.assertEqual(gates.claims(), ALL_CLOSED)
        self.assertEqual(self.ledger(), [])

    def test_camera_input_to_the_display_needs_the_camera_record_the_left_camera_and_this_runner(self):
        path, record = self.staged("L-D4")
        client = self.client(self.run_scope(path))
        stage = self.stage(client)
        order = []

        def drive(pipeline, stop):
            order.append(("drive", self.ledger()))
            raise RuntimeError("the test stops before the helper")
        with mock.patch.object(stage.source, "drive", side_effect=drive), \
                mock.patch.object(camera_helper, "run_export") as exported, self.launches() as seen, \
                mock.patch.object(gates.Gate, "require", autospec=True, side_effect=gates.Gate.require) as required:
            report = stage.run()
        self.assertEqual([(call.args[0], call.args[1]) for call in required.call_args_list],
                         [(gates.LIVE_HUD_OUTPUT, True), (gates.LIVE_CAMERA_INPUT, True)])
        self.assertEqual(order, [("drive", [f"{record['run_id']}.json"])])  # Both checks, then the first launch.
        exported.assert_not_called()
        self.assertEqual(seen, [])
        self.assertEqual((report["status"], report["reason"]), ("error", "camera_hud_run_failed"))
        self.assertEqual({name: report[name] for name in gates.claims()}, ALL_CLOSED)
        self.assertIs(camera_stage.camera_display_input_permitted(stage), False)  # Granted once.

    def test_camera_input_is_refused_for_the_synthetic_record_other_sources_and_other_scopes(self):
        synthetic_path, _ = self.staged("L-D3")
        # The synthetic step's record: real output is permitted, camera input is not, and nothing is launched.
        client = self.client(self.run_scope(synthetic_path, source=scope.SYNTHETIC))
        stage = self.stage(client)
        with mock.patch.object(camera_helper, "run_export") as exported, self.launches() as seen, \
                mock.patch.object(gates.Gate, "require", autospec=True, side_effect=gates.Gate.require) as required:
            with self.assertRaisesRegex(ValueError, "^camera_display_input_not_approved$"):
                stage.run()
        self.assertEqual([(call.args[0], call.args[1]) for call in required.call_args_list],
                         [(gates.LIVE_HUD_OUTPUT, True), (gates.LIVE_CAMERA_INPUT, False)])
        exported.assert_not_called()
        self.assertEqual(seen, [])
        self.assertEqual((stage.status, stage.source.status, client.process), ("not_run", "not_started", None))
        # A granted camera scope still refuses a source that is not the left camera's helper source.
        for label, make in (("both", lambda source: setattr(source, "source_id", "arcturus_camera:0+1")),
                            ("right", lambda source: setattr(source, "source_id", "arcturus_camera:1")),
                            ("mirror", lambda source: setattr(source, "source_id", "mirror:observed"))):
            run = self.run_scope(self.staged("L-D4")[0])
            self.assertIs(run.permit(scope.HUD_OUTPUT), True)
            source = self.source()
            make(source)
            with self.subTest(source=label):
                self.assertIs(camera_stage.camera_display_input_permitted(
                    SimpleNamespace(client=SimpleNamespace(scope=run), source=source)), False)
                self.assertIs(run.permit(scope.CAMERA_INPUT), True)  # It was never asked: the site refused first.
        run = self.run_scope(self.staged("L-D4")[0])
        self.assertIs(run.permit(scope.HUD_OUTPUT), True)
        declared = synthetic.SyntheticSource()
        declared.source_id = self.source().source_id
        for source in (declared, SimpleNamespace(source_id=self.source().source_id, kind="synthetic"), None):
            self.assertIs(camera_stage.camera_display_input_permitted(
                SimpleNamespace(client=SimpleNamespace(scope=run), source=source)), False)
        for stand_in in (None, True, mock.Mock(), SimpleNamespace(source=scope.CAMERA_LEFT, permit=lambda use: True)):
            self.assertIs(camera_stage.camera_display_input_permitted(
                SimpleNamespace(client=SimpleNamespace(scope=stand_in), source=self.source())), False)
        for cameras in ((0, 1), (1,)):
            with self.assertRaises(SightCircuitError) as caught:  # Both cameras never reach a stage at all.
                CameraHelperSource(Path("unused"), LIMITS, cameras=cameras)
            self.assertEqual(caught.exception.reason, "camera_source_left_only")

    def test_a_validation_stage_asks_neither_site(self):
        client = ObservedFrameClient()
        stage = self.stage(client)
        with mock.patch.object(stage.source, "drive", side_effect=RuntimeError("stop")), \
                mock.patch.object(observed_frames, "fixed_hud_output_permitted") as output, \
                mock.patch.object(camera_stage, "camera_display_input_permitted") as camera:
            stage.run()
        output.assert_not_called()
        camera.assert_not_called()

    def test_the_synthetic_path_is_no_bypass_and_the_other_runners_still_refuse(self):
        path, _ = self.staged("L-D4")
        run = self.run_scope(path)
        # The live runner: a camera source is refused as before, whatever record exists or is attached to it.
        source = self.source()
        source.scope = run
        with mock.patch.object(camera_helper, "run_export") as exported:
            with self.assertRaisesRegex(ValueError, "^live_image_source_not_approved$"):
                live.run_pipeline(source, synthetic.FixtureDetector(), synthetic.HeadlessSink(), 0.1)
            with mock.patch.object(gates.Gate, "require", autospec=True, side_effect=gates.Gate.require) as required, \
                    self.assertRaisesRegex(ValueError, "^live_image_source_not_approved$"):
                live.require_synthetic(source, "live_image_source_not_approved")
            exported.assert_not_called()
        self.assertEqual([call.args for call in required.call_args_list],
                         [(gates.LIVE_IMAGE_INPUT, False, "live_image_source_not_approved")])
        live.require_synthetic(synthetic.SyntheticSource(), "unused")  # Unchanged: the synthetic kind passes.
        # The mirror HUD mode: a backend that is not a synthetic fixture is refused before anything loads.
        detector = mock.Mock()
        report, last = live.run_recognition({}, 0.2, threading.Event(), hud_mode=True, calibration=None,
                                            backend_factory=None, detector_factory=detector)
        detector.assert_not_called()
        self.assertEqual((report["status"], last), ("error", None))
        self.assertIn("hud_live_mirror_not_approved", report["error"])
        # A source that calls itself synthetic gets nothing at the display sites.
        source.kind = "synthetic"
        client = self.client()
        with self.launches() as seen, self.assertRaisesRegex(ValueError, "^fixed_hud_output_not_approved$"):
            camera_stage.CameraHudStage(source, synthetic.FixtureDetector(), client).run()
        self.assertEqual(seen, [])
        self.assertEqual((run.status, self.ledger()), ("not_consulted", []))
        self.assertEqual(gates.claims(), ALL_CLOSED)

    def test_the_camera_gate_has_one_require_call_and_one_permission_function(self):
        self.assertEqual(require_calls("sightcircuit/hud/camera_stage.py"),
                         {"LIVE_CAMERA_INPUT": ["camera_display_input_permitted"]})
        self.assertEqual(users_of("LIVE_CAMERA_INPUT"), ["sightcircuit/hud/camera_stage.py"])
        self.assertEqual(users_of("camera_display_input_permitted("), ["sightcircuit/hud/camera_stage.py"])


def require_calls(name):
    """Gate name -> what each ``<GATE>.require(...)`` call in the module passes as the permission."""
    calls = {}
    for node in ast.walk(ast.parse((ROOT / name).read_text())):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "require"
                and isinstance(node.func.value, ast.Name)):
            inner = node.args[0]
            calls.setdefault(node.func.value.id, []).append(
                inner.func.id if isinstance(inner, ast.Call) else ast.unparse(inner))
    return calls


def users_of(text):
    """The library modules, other than the gate table and the scope module, whose source contains ``text``."""
    return [path.relative_to(ROOT).as_posix() for path in sorted((ROOT / "sightcircuit").rglob("*.py"))
            if path.name not in ("gates.py", "scope.py") and text in path.read_text()]


if __name__ == "__main__":
    unittest.main()
