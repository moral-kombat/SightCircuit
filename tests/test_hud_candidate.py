"""Observer report to mapping candidate from generated frames; never qualification or device evidence."""
import contextlib
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

try:
    import cv2  # noqa: F401
except ImportError as error:
    raise unittest.SkipTest("OpenCV is required for generated observer reports") from error

from framejacker.gates import claims
from framejacker.hud import HUD_CONFIG, HudMask, config_digest
from framejacker.hud import candidate as hud_candidate
from framejacker.hud import contract
from framejacker.hud.geometry import expand
from framejacker.hud.mask import mask_regions
from framejacker.hud.qualification import MappingCandidate
from scripts import hud_marker_observer as observer
from scripts import hud_validation_report
from tests.test_hud_marker_observer import FakeClock, FakeStop, clean_bridge_fixture, scene, tag_mismatch_template

ROOT = Path(__file__).resolve().parents[1]
NATIVE_FORMAT = dict(width=1000, height=600, fourcc="BGR3", stride_bytes=3000,
                     minimum_buffer_bytes=1800000, buffer_bytes=1800000)
COPIES = ((70, 80), (570, 270))  # One copy in each half of a declared side-by-side capture.
SHIFTS = (0, 2, -2)  # Horizontal movement between marker reads.
# Tight corner bounds of COPIES under SHIFTS, without margin; the observer adds 16 px.
TIGHT_RECTS = [[68, 80, 392, 208], [568, 270, 892, 398]]


def observer_report(*, seconds=1, template=None):
    """A complete report from the real observer and observation algorithm on generated frames."""
    clock = FakeClock()
    stop = FakeStop(clock)
    state = {"marker": False, "reads": 0, "marker_reads": 0}

    class Source:
        kind = "synthetic"

        def __init__(self, candidate):
            self.capabilities = {"driver": "synthetic"}
            self.format = dict(NATIVE_FORMAT)

        def open(self):
            pass

        def read_frame(self, timeout):
            state["reads"] += 1
            positions = ()
            if state["marker"]:
                shift = SHIFTS[state["marker_reads"] % len(SHIFTS)]
                state["marker_reads"] += 1
                positions = tuple((x + shift, y) for x, y in COPIES)
            image = scene(positions, template=template)
            image[0, 0] = state["reads"] % 100  # Distinct clear baseline frames.
            return image.tobytes()

        def close(self):
            pass

    class Marker:
        validation_only = True
        process = None

        def start_marker(self, seconds, stop, **kwargs):
            state["marker"] = True
            self.end = clock.now + seconds
            self.process = Mock()
            self.process.poll.side_effect = lambda: 0 if clock.now >= self.end else None

        def close(self):
            return clean_bridge_fixture(seconds)

    with patch.object(observer.time, "monotonic", clock.monotonic), \
            patch.object(observer.time, "monotonic_ns", clock.monotonic_ns):
        return observer.observe({"node": "synthetic:fixture"}, seconds, stop, backend_factory=Source,
                                marker_client=Marker())


def declared_context(**changes):
    context = {"source_id": "synthetic:mirror", "session_id": "generated-session", "runtime_id": "fixture-runtime",
               "timebase_id": "generated-boot:monotonic", "tracking_origin": "seated", "recenter_epoch": 0,
               "native_format": dict(NATIVE_FORMAT), "crop_xyxy": [0, 0, 1000, 600], "orientation_degrees": 0,
               "capture_layout": "stereo_sbs", "hud_config_sha256": config_digest(HUD_CONFIG)}
    return dict(context, **changes)


def encode(report):
    """The observer command prints its report this way."""
    return json.dumps(report, indent=2).encode()


def convert(report, context=None, lifetime=60):
    data = report if isinstance(report, bytes) else encode(report)
    return hud_candidate.observer_candidate(data, artifact_id="observer-report.json",
                                            declared_context=declared_context() if context is None else context,
                                            lifetime_seconds=lifetime)


def json_paths(value, prefix=()):
    """Paths to every object member and to each list's first item."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield prefix + (key,)
            yield from json_paths(item, prefix + (key,))
    elif isinstance(value, list) and value:
        yield prefix + (0,)
        yield from json_paths(value[0], prefix + (0,))


def replaced(report, path, value):
    changed = deepcopy(report)
    target = changed
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return changed


def run_cli(args):
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        code = hud_validation_report.main([str(arg) for arg in args])
    return code, json.loads(stdout.getvalue())


class ObserverCandidateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = observer_report()

    def test_generated_observer_report_is_a_complete_accepted_observation(self):
        self.assertEqual(self.report["status"], "observed_unqualified", self.report)
        self.assertEqual(self.report["exit_code"], 2)
        observer.validate_worker_report(self.report, 2)
        self.assertEqual([sample[0] for sample in self.report["samples"]], [3, 4, 5])
        self.assertEqual(self.report["maximum_corner_drift_px"], 2)

    def test_observer_report_becomes_valid_unqualified_candidate_without_gates(self):
        data = encode(self.report)
        candidate = convert(data)
        summary = hud_candidate.candidate_report(candidate)
        self.assertEqual(summary["status"], "candidate_valid_unqualified")
        self.assertEqual(summary["exit_code"], 2)
        self.assertEqual(summary["report_kind"], "hud_mapping_candidate_from_observer")
        for key in (*claims(), "evidence_contents_verified", "physical_expiry_verified"):
            self.assertIs(summary[key], False, key)
        self.assertEqual(summary["missing_evidence_roles"],
                         ["composition", "copy_coverage", "motion_margin", "physical_expiry"])
        record = candidate.as_dict()
        self.assertEqual(summary["candidate"], record)
        self.assertEqual((record["status"], record["mapping_qualified"]), ("observed_unqualified", False))
        self.assertEqual(record["evidence"], [{"role": "observer", "artifact_id": "observer-report.json",
                                               "sha256": hashlib.sha256(data).hexdigest(),
                                               "kind": "synthetic_fixture"}])
        self.assertEqual(record["mapping_id"], "observer-" + hashlib.sha256(data).hexdigest()[:16])
        samples = self.report["samples"]
        self.assertEqual(record["observed_ns"], samples[-1][1])
        self.assertEqual(record["expires_ns"], samples[0][1] + 60 * 1_000_000_000)
        self.assertEqual(record["context"], declared_context())
        self.assertEqual([copy["declared_eye"] for copy in record["copies"]], [0, 1])
        # The record round-trips through the schema, and nothing it yields lifts a gate.
        self.assertEqual(MappingCandidate.from_mapping(record).as_dict(), record)
        calibration = candidate.calibration()
        self.assertIs(calibration.validated, False)
        self.assertEqual(calibration.provenance, "observed_unqualified")
        with self.assertRaisesRegex(ValueError, "calibration_unvalidated"):
            HudMask(calibration, source_kind="synthetic")
        with self.assertRaisesRegex(ValueError, "hardware_mapping_not_approved"):
            candidate.require_production_qualification()
        self.assertFalse(any(claims().values()))

    def test_no_unknown_is_silently_zero(self):
        record = convert(self.report).as_dict()
        unknowns = record["unknowns"]
        self.assertEqual(unknowns, hud_candidate.OBSERVER_UNKNOWNS)
        for term in ("eye_composition", "unseen_or_occluded_copies", "capture_freshness",
                     "motion_displacement_bound", "localization_error_px", "filter_support_px", "operating_limits"):
            self.assertIsInstance(unknowns.get(term), str, term)
            self.assertTrue(unknowns[term].strip(), term)
        nulls = []

        def walk(value, path):
            if isinstance(value, dict):
                for key, item in value.items():
                    walk(item, path + (key,))
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    walk(item, path + (index,))
            elif value is None:
                nulls.append(path)
        walk(record, ())
        # Every null is a named unknown, and every unknown field is null: none of them is 0.
        for path in nulls:
            term = "operating_limits" if path[0] == "operating_limits" else path[-1]
            self.assertIn(term, unknowns, path)
        for copy in record["copies"]:
            for term in ("localization_error_px", "filter_support_px"):
                self.assertIsNone(copy[term], term)
            # Sampled drift is a measurement (2 px here), not the unknown motion bound.
            self.assertEqual(copy["maximum_drift_px"], 2)
        self.assertEqual(record["operating_limits"], dict.fromkeys(record["operating_limits"]))
        self.assertEqual(len(nulls), 2 * len(record["copies"]) + len(record["operating_limits"]))
        self.assertEqual(hud_candidate.candidate_report(convert(self.report))["unknown_error_terms"], unknowns)
        # Replacing any unknown with 0, or dropping its reason, is a schema rejection.
        for path in nulls:
            for change in ("zero", "no_reason"):
                item = deepcopy(record)
                target = item
                for key in path[:-1]:
                    target = target[key]
                if change == "zero":
                    target[path[-1]] = 0
                else:
                    del item["unknowns"]["operating_limits" if path[0] == "operating_limits" else path[-1]]
                with self.subTest(path=path, change=change), self.assertRaises(ValueError):
                    MappingCandidate.from_mapping(item)

    def test_margin_is_removed_from_the_observer_union_exactly_once(self):
        record = convert(self.report).as_dict()
        margin = self.report["margin_px"]
        self.assertEqual(margin, 16)
        self.assertEqual([copy["rect_native"] for copy in record["copies"]], TIGHT_RECTS)
        for copy, region in zip(record["copies"], self.report["footprint_union_native"]):
            self.assertEqual(copy["margin_px"], margin)
            self.assertEqual(list(expand(copy["rect_native"], copy["margin_px"])), region)
        # The runtime mask region is the observer's union, not that union plus another margin.
        # The mask's own geometry rule checks agreement without approval.
        calibration = MappingCandidate.from_mapping(record).calibration()
        self.assertEqual([list(region) for region in mask_regions(calibration)], self.report["footprint_union_native"])
        # A union that double-counts or omits the margin, or cuts into a sampled corner, disagrees
        # with the samples. (One outward pixel is within the samples' 0.001 px rounding.)
        for grow in (margin, -margin, -1):
            forged = deepcopy(self.report)
            forged["footprint_union_native"] = [list(expand(region, grow)) for region in forged["footprint_union_native"]]
            with self.subTest(grow=grow), self.assertRaisesRegex(ValueError, "footprint_inconsistent"):
                convert(forged)

    def test_observer_margin_follows_the_contract_margin_rule(self):
        self.assertEqual(self.report["margin_px"], 16)
        convert(self.report)
        # The conversion applies the contract's one margin rule, not its own copy of the bounds.
        for low, high in ((17, 128), (8, 15)):
            with self.subTest(bounds=(low, high)), patch.object(contract, "MARGIN_MIN_PX", low), \
                    patch.object(contract, "MARGIN_MAX_PX", high), self.assertRaisesRegex(ValueError, "samples_invalid"):
                convert(self.report)
        for margin in (7, 129, 16.0, True):
            with self.subTest(margin=margin), self.assertRaisesRegex(ValueError, "samples_invalid"):
                convert(dict(self.report, margin_px=margin))

    def rejected_reports(self):
        """Each case and the reason it must be rejected for; None accepts any decoding error."""
        report = self.report
        cases = {"rejected_run": (observer_report(template=tag_mismatch_template()), "not_observed_unqualified"),
                 "cancelled": (dict(report, status="cancelled", exit_code=130), "not_observed_unqualified"),
                 "cleanup_failed": (dict(report, cleanup_ok=False), "invalid_observer_worker_report"),
                 "cleanup_error": (dict(report, cleanup_error="synthetic close failed"), "has_errors"),
                 "other_reason": (dict(report, reason="unexpected_observer_state"), "has_errors"),
                 "forced_worker": (dict(report, worker_forced_termination=True), "has_errors"),
                 "capture_error": (dict(report, capture=dict(report["capture"], status="error", error="x")),
                                   "capture_incomplete"),
                 "capture_cleanup": (dict(report, capture=dict(report["capture"], cleanup_ok=False)),
                                     "capture_incomplete"),
                 "forced_bridge": (dict(report, bridge=dict(report["bridge"], forced_termination=True)),
                                   "inconsistent_marker"),
                 "qualification_claim": (dict(report, mapping_qualified=True), "invalid_observer_worker_report"),
                 "composition_claim": (dict(report, composition_verified=True), "invalid_observer_worker_report"),
                 "wrong_kind": (dict(report, report_kind="hud_calibration_marker"), "invalid_observer_worker_report"),
                 "too_few_samples": (dict(report, samples=report["samples"][:2], sample_count=2),
                                     "invalid_observer_worker_report"),
                 "samples_missing": (dict(report, samples=[]), "samples_invalid"),
                 "samples_absent": ({key: value for key, value in report.items() if key != "samples"},
                                    "samples_invalid"),
                 "margin_absent": ({key: value for key, value in report.items() if key != "margin_px"},
                                   "samples_invalid"),
                 "drift_inconsistent": (dict(report, maximum_corner_drift_px=0.5), "drift_inconsistent"),
                 "baseline_after_marker": (dict(report, baseline_sequence=report["samples"][0][0]), "samples_invalid"),
                 "not_object": ([], "report_invalid"),
                 "empty": (b"", None),
                 "truncated": (encode(report)[:-2], None),
                 "nan_constant": (b"NaN", "nonfinite_number")}
        gap = deepcopy(report)
        gap["baseline_sequence"], gap["samples"][0][0] = 1, 2  # Reads 2, 4, 5: only the gap is wrong.
        cases["sequence_gap"] = (gap, "sequence_gap")
        clock = deepcopy(report)
        clock["samples"][2][1] = clock["samples"][1][1]
        cases["time_not_increasing"] = (clock, "time_not_increasing")
        copies = deepcopy(report)
        copies["samples"][1][2].pop()
        cases["copy_count_changed"] = (copies, "samples_invalid")
        corners = deepcopy(report)
        corners["samples"][0][2][0][0][0] = float("nan")
        cases["nonfinite_corner"] = (corners, "nonfinite_number")
        return cases

    def test_rejected_incomplete_or_erroneous_reports_never_become_candidates(self):
        cases = self.rejected_reports()
        self.assertEqual(cases["rejected_run"][0]["status"], "rejected")
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            context = folder / "context.json"
            context.write_text(json.dumps(declared_context()))
            for name, (report, reason) in cases.items():
                data = report if isinstance(report, bytes) else encode(report)
                with self.subTest(case=name):
                    with self.assertRaisesRegex(ValueError, reason or ""):
                        convert(data)
                    path, output = folder / f"{name}.json", folder / f"{name}-candidate.json"
                    path.write_bytes(data)
                    code, printed = run_cli(["--from-observer", path, "--declared-context", context,
                                             "--lifetime-seconds", "60", "--candidate-output", output])
                    self.assertEqual(code, 1)
                    self.assertEqual(printed["status"], "rejected")
                    self.assertEqual(printed["report_kind"], "hud_mapping_candidate_from_observer")
                    self.assertRegex(printed["reason"], reason or ".")
                    self.assertNotIn("candidate", printed)
                    self.assertFalse(output.exists())
                    self.assertFalse(any(printed[key] for key in claims()))

    def test_a_field_of_another_json_type_is_a_structured_rejection(self):
        """Every object member, and each list's first item, replaced by each other JSON type."""
        structured = (ValueError, TypeError, OverflowError)  # What --from-observer reports as a rejection.
        cases = 0
        for path in json_paths(self.report):
            for substitute in ("x", [1], 7, 1.5, True, None, {}):
                cases += 1
                with self.subTest(path=path, substitute=substitute):
                    try:
                        convert(replaced(self.report, path, substitute))
                    except structured:
                        pass
        self.assertGreater(cases, 1000)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            context = folder / "context.json"
            context.write_text(json.dumps(declared_context()))
            for path in (("baseline",), ("bridge",), ("bridge", "native_report"), ("bridge", "native_report", "marker"),
                         ("bridge", "native_report", "visibility_after_submit")):
                for substitute in ("x", [1], 7, True):
                    with self.subTest(path=path, substitute=substitute):
                        report_path = folder / f"{'-'.join(path)}-{type(substitute).__name__}.json"
                        report_path.write_bytes(encode(replaced(self.report, path, substitute)))
                        code, printed = run_cli(["--from-observer", report_path, "--declared-context", context,
                                                 "--lifetime-seconds", "60"])
                        self.assertEqual((code, printed["status"], printed["exit_code"]), (1, "rejected", 1))
                        self.assertRegex(printed["reason"], "^invalid_(observer|marker)_")
                        self.assertNotIn("candidate", printed)

    def test_declared_context_must_match_the_observed_capture_and_lifetime(self):
        cases = ((declared_context(native_format=dict(NATIVE_FORMAT, stride_bytes=3008, minimum_buffer_bytes=1804800,
                                                      buffer_bytes=1804800)), 60, "format_context_mismatch"),
                 (declared_context(hud_config_sha256="a" * 64), 60, "hud_config_changed"),
                 (declared_context(capture_layout="stereo_tb"), 60, "outside_declared_eye"),
                 (declared_context(crop_xyxy=[0, 0, 600, 600], capture_layout="mono"), 60, "outside_declared_eye"),
                 ({key: value for key, value in declared_context().items() if key != "session_id"}, 60,
                  "context_fields_invalid"),
                 (declared_context(), 0, "lifetime_invalid"),
                 (declared_context(), 301, "lifetime_invalid"),
                 (declared_context(), True, "lifetime_invalid"))
        for context, lifetime, reason in cases:
            with self.subTest(reason=reason, lifetime=lifetime), self.assertRaisesRegex(ValueError, reason):
                convert(self.report, context, lifetime)
        # One declared eye with both copies is a different, still unqualified, declaration.
        mono = convert(self.report, declared_context(capture_layout="mono")).as_dict()
        self.assertEqual([copy["declared_eye"] for copy in mono["copies"]], [0, 0])
        # The lifetime counts from the first sample, so it cannot end before the last one.
        longer = observer_report(seconds=3)
        samples = longer["samples"]
        self.assertGreater(samples[-1][1] - samples[0][1], 2 * 1_000_000_000)
        with self.assertRaisesRegex(ValueError, "lifetime_shorter_than_observation"):
            convert(longer, lifetime=2)
        record = convert(longer, lifetime=3).as_dict()
        self.assertEqual(record["expires_ns"], samples[0][1] + 3 * 1_000_000_000)
        self.assertLess(record["observed_ns"], record["expires_ns"])

    def test_cli_writes_a_new_candidate_that_check_mode_accepts(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            report_path, context, output = (folder / name for name in
                                            ("observer-report.json", "context.json", "candidate.json"))
            report_path.write_bytes(encode(self.report))
            context.write_text(json.dumps(declared_context()))
            args = ["--from-observer", report_path, "--declared-context", context, "--lifetime-seconds", "60",
                    "--candidate-output", output]
            code, printed = run_cli(args)
            self.assertEqual(code, 2, printed)
            self.assertEqual(printed["status"], "candidate_valid_unqualified")
            self.assertEqual(printed["candidate_output"], str(output))
            written = output.read_bytes()
            record = json.loads(written)
            self.assertEqual(printed["candidate"], record)
            self.assertEqual(record["evidence"][0]["sha256"], hashlib.sha256(report_path.read_bytes()).hexdigest())
            code, checked = run_cli(["--check-candidate", output, "--current-context", context,
                                     "--now-ns", record["observed_ns"]])
            self.assertEqual(code, 2, checked)
            self.assertEqual(checked["status"], "candidate_valid_unqualified")
            self.assertEqual(checked["unknown_error_terms"], hud_candidate.OBSERVER_UNKNOWNS)
            self.assertFalse(checked["mapping_qualified"])
            # Exclusive creation: an existing candidate is never replaced.
            code, again = run_cli(args)
            self.assertEqual((code, again["status"]), (1, "rejected"))
            self.assertNotIn("candidate", again)
            self.assertEqual(output.read_bytes(), written)
            # Without an output path the candidate is only reported.
            code, printed = run_cli(args[:-2])
            self.assertEqual(code, 2)
            self.assertIsNone(printed["candidate_output"])

    def test_a_candidate_write_that_fails_part_way_writes_nothing(self):
        """A full disk (here RLIMIT_FSIZE) is a rejection that leaves no partial record, not even a newline short."""
        try:
            import resource
        except ImportError:
            self.skipTest("RLIMIT_FSIZE needs the POSIX resource module")
        record = json.dumps(convert(self.report).as_dict(), indent=2, sort_keys=True) + "\n"
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            report_path, context = folder / "observer-report.json", folder / "context.json"
            report_path.write_bytes(encode(self.report))
            context.write_text(json.dumps(declared_context()))
            for limit in (0, len(record) // 2, len(record) - 1):
                output = folder / f"candidate-{limit}.json"
                command = [sys.executable, "-B", ROOT / "scripts/hud_validation_report.py", "--from-observer",
                           report_path, "--declared-context", context, "--lifetime-seconds", "60",
                           "--candidate-output", output]
                with self.subTest(limit=limit):
                    run = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False,
                                         preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit)))
                    printed = json.loads(run.stdout)
                    self.assertEqual((run.returncode, printed["status"]), (1, "rejected"), run.stderr)
                    self.assertNotIn("candidate", printed)
                    self.assertEqual(sorted(path.name for path in folder.iterdir()),
                                     ["context.json", "observer-report.json"])
            code, printed = run_cli(command[3:])  # Nothing left behind blocks a later write.
            self.assertEqual(code, 2, printed)
            self.assertEqual(output.read_text(), record)

    def test_cli_modes_are_exclusive_and_complete(self):
        misuse = ([], ["--from-observer", "r.json", "--check-candidate", "c.json"],
                  ["--from-observer", "r.json", "--lifetime-seconds", "60"],
                  ["--from-observer", "r.json", "--declared-context", "c.json"],
                  ["--from-observer", "r.json", "--declared-context", "c.json", "--lifetime-seconds", "60",
                   "--now-ns", "1"],
                  ["--check-candidate", "c.json", "--current-context", "c.json"],
                  ["--check-candidate", "c.json", "--current-context", "c.json", "--now-ns", "1",
                   "--candidate-output", "x.json"])
        for args in misuse:
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                hud_validation_report.parse_args(args)

    def test_documented_commands_parse_with_the_real_parser(self):
        found = 0
        for name in ("docs/hud-qualification.md", "docs/development.md"):
            text = (ROOT / name).read_text()
            for block in re.findall(r"```bash\n(.*?)```", text, re.S):
                for command in block.replace("\\\n", " ").splitlines():
                    words = shlex.split(command)
                    if "scripts/hud_validation_report.py" in words:
                        found += 1
                        args = words[words.index("scripts/hud_validation_report.py") + 1:]
                        with self.subTest(document=name, command=command):
                            hud_validation_report.parse_args(args)
        self.assertGreaterEqual(found, 3)


if __name__ == "__main__":
    unittest.main()
