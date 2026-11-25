"""The one-run scope record (work-order items L10 and B5): the diagnostic tier of decision D3, without the mask.

Every gate in ``sightcircuit/gates.py`` is closed and this module opens none.
The owner decided on 2026-11-16 (decision 15) that each displayed device step
of the camera display work order (L-D3, L-D4, L-D5) runs under one one-run
record, and that a re-run is a new approval with a new record. A record is a
small JSON file that ties one run to that approval. It is bound to:

* the checkout revision (the full commit);
* the checkpoint manifest's digest (the manifest pins the helper, the bridge,
  the presence watcher, the models, every module and the camera HUD's
  scripts, and records each fixed layout's digest, so one digest covers them
  all; the permission sites launch only the bridge and the helper at the
  paths the manifest hashes, ``pinned_binary``);
* a run id, which the ledger makes single-use;
* an absolute UTC deadline, by which the whole run must have ended;
* the duration bound, at most the step's own, which the permission sites tie
  to what the run does: the client ends real output itself once the run's
  seconds are over, and a camera run's stage and helper bounds must lie
  within them;
* the source: ``arcturus_camera_left``, or ``synthetic`` for the expiry and
  soak step, which never opens the camera;
* the layout: the id and the configuration digest of one of the bridge's
  fixed layouts (``hud.contract.FIXED_LAYOUTS``), the one the run shows and
  no other, so a record for the strip never serves the box panel nor the
  reverse (``hud_config_id``, ``hud_config_sha256``);
* the view (schema 2, work-order items B5 and L17): the region of the camera
  frame and the quarter turn the detector is given (``region``,
  ``rotation``; both null for the synthetic step, which has no frame), the
  alignment's five numbers when camera boxes are mapped to a panel
  (``alignment``; the neutral alignment is a value like any other, and the
  numbers are the project's own, never the SDK's calibration), and whether
  the run must hold the presence watch (``presence_watch``);
* the step id, which the run must name and match, and the runner,
  ``camera_hud`` (a name the caller declares: it keeps a record written for
  another runner from being accepted by a caller that names itself
  truthfully, and it cannot identify the calling program);
* a reference to the owner's approval: an identifier, never free text.

What this is, exactly: a procedural control against mistakes, not a
cryptographic one. Nothing here is signed. Anyone who can run the writer on
the Frame can write a record, and anyone who can edit files there can forge
one, and removing the ledger directory makes a spent record usable again
until its deadline. The owner's approval, given in the Frame manager's
session, is the authority; the record ties one run to it and makes the common
mistakes fail closed: the wrong commit, a rebuilt binary, a second run, a run
after the sitting, the wrong source, a longer run, another program.

``RunScope`` is what a runner holds: the record's path, the manifest's path
and what the run is about to do. It permits a use only after the record
verified in full against what is on disk, and it consumes the run id (an
exclusive file in the ledger) as the last step of that verification, before
the caller launches anything. Verification runs read-only queries and hashes
files: ``git`` for the commit and the tree's state and, where no package
database is readable, the manifest's read-only ``pacman -Q``; it starts
nothing else. A use is granted to one holder (the client, the stage), and
each time that holder asks again, at the start itself, the deadline and the
launched binary's digest are checked again without consuming anything. What
remains between that check and the launch is the launch itself; the helper's
digest is compared once more by the launcher. Its answer is the literal ``True`` or the
literal ``False``; the two permission sites
(``observed_frames.fixed_hud_output_permitted`` and
``hud.camera_stage.camera_display_input_permitted``) hand it to their gate's
``require``. No flag, environment variable, configuration value or typed
answer is read here, and none creates a record: ``write_record`` is called
only by ``scripts/stage_scope_record.py``, which is not an operator command.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import time

from ..errors import SightCircuitError
from ..hud.contract import FIXED_LAYOUTS, HUD_CONFIG, config_digest
from . import manifest

ROOT = manifest.ROOT  # The checkout a record is bound to: this one. Not an argument of anything here.
LEDGER = "artifacts/scope-ledger"  # Checkout-relative and ignored by git: one file per consumed run id.
REPORT_KIND = "one_run_scope_record"
# 1 (2026-11-16, item L10): the step, the runner, the source, ``fixed-edge-hud-v1`` and its digest, the revision,
#   the manifest's digest, the run id, the two times, the duration and the approval.
# 2 (2026-11-25, items B5 and L17): ``hud_config_id`` and ``hud_config_sha256`` may name any fixed layout and are
#   compared with what the run shows; new fields ``region``, ``rotation``, ``alignment`` and ``presence_watch``.
#   A record of version 1 is refused by name (``scope_record_schema_unsupported``), never read leniently.
SCHEMA_VERSION = 2
RUNNER = "camera_hud"  # The only runner a record may name.
CAMERA_LEFT, SYNTHETIC = "arcturus_camera_left", "synthetic"
HUD_OUTPUT, CAMERA_INPUT = "hud_output", "camera_input"  # The two uses, one per permission site.
# Step -> (the source its record must name, the longest run it may bound, in seconds). L-D3 is the expiry and
# soak step: 75 s of rows plus three stops; L-D4 the first displayed labels; L-D5 the 60-second acceptance.
STEPS = {"L-D3": (SYNTHETIC, 120), "L-D4": (CAMERA_LEFT, 30), "L-D5": (CAMERA_LEFT, 90)}
USES = {SYNTHETIC: (HUD_OUTPUT,), CAMERA_LEFT: (HUD_OUTPUT, CAMERA_INPUT)}
USE_BINARIES = {HUD_OUTPUT: manifest.BRIDGE, CAMERA_INPUT: manifest.CAMERA_HELPER}  # What each use launches.
MAX_VALID_MINUTES = 240  # A record is written for one sitting.
CLOCK_TOLERANCE_SECONDS = 60  # How far ahead of this clock a record's creation time may lie.
MAX_RECORD_BYTES = 4096
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
RUN_ID = re.compile(r"[0-9a-f]{32}")
APPROVAL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}")  # An identifier of the approval; never prose.
FIELDS = ("report_kind", "schema_version", "step", "runner", "source", "hud_config_id", "hud_config_sha256",
          "region", "rotation", "alignment", "presence_watch",
          "revision", "manifest_sha256", "run_id", "created_utc", "deadline_utc", "max_seconds", "approval",
          "statement")
# The view's value ranges. They are the region's and the alignment's own classes', repeated here because this module
# must load without NumPy and OpenCV, which those modules' packages need; tests/test_scope_record.py keeps them equal.
ROTATIONS = (0, 90, 180, 270)
WHOLE_FRAME = (0.0, 0.0, 1.0, 1.0)
REGION_MIN_FRACTION = 0.05
REGION_MIN_FRACTION_TOLERANCE = 1e-9
ALIGNMENT_FIELDS = ("scale_x", "scale_y", "offset_x", "offset_y", "turn")
ALIGNMENT_SCALE_LIMITS = (0.25, 4.0)
ALIGNMENT_OFFSET_LIMIT = 1.0
STATEMENT = ("One-run scope record: a procedural control, not a cryptographic one. It opens no gate and is not "
             "an approval; the owner's approval in the Frame manager's session is the authority.")


# What the writer says for each refusal of ``view_value``.
VIEW_REFUSALS = {
    "scope_region_not_for_source": "The synthetic step opens no camera: it takes no region and no rotation",
    "scope_region_invalid": "A region is four fractions x0,y0,x1,y1 with 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1, "
                            "each side at least 0.05; a rotation is 0, 90, 180 or 270",
    "scope_alignment_not_for_layout": "An alignment is required for a camera step on a panel layout and belongs "
                                      "to no other record",
    "scope_alignment_invalid": "An alignment is scale_x, scale_y (0.25 to 4), offset_x, offset_y (-1 to 1) and a "
                               "turn of 0, 90, 180 or 270",
    "scope_presence_watch_invalid": "A camera step's record must say whether the presence watch is required",
    "scope_presence_watch_not_for_source": "The synthetic step opens no camera and holds no presence watch",
}


class ScopeError(SightCircuitError):
    """A record cannot be written."""


def _utc(text):
    """The epoch seconds of a ``TIME_FORMAT`` string, or None."""
    if type(text) is not str:
        return None
    try:
        parsed = datetime.strptime(text, TIME_FORMAT)
    except ValueError:
        return None
    return int(parsed.replace(tzinfo=timezone.utc).timestamp())


def _stamp(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).strftime(TIME_FORMAT)


def _file_digest(path):
    """SHA-256 of a regular file's bytes (at most a manifest's size), or None."""
    try:
        path = Path(path)
        if not path.is_file():
            return None
        with path.open("rb") as source:
            data = source.read(manifest.MAX_MANIFEST_BYTES + 1)
    except OSError:
        return None
    return None if len(data) > manifest.MAX_MANIFEST_BYTES else hashlib.sha256(data).hexdigest()


def _file_digest_of(path):
    """SHA-256 of a regular file of any size, or None."""
    try:
        return manifest.assets.checksum(path) if Path(path).is_file() else None
    except OSError:
        return None


def _revision(root):
    try:
        return manifest.git_state(root)[0]
    except (OSError, manifest.ManifestError):
        return None


def _number(value):
    """A finite ``int`` or ``float`` (never a ``bool``) as a ``float``, or None."""
    if type(value) not in (int, float):
        return None
    try:
        number = float(value)
    except OverflowError:  # A whole number too large for a float.
        return None
    return number if math.isfinite(number) else None


def fixed_layout_of(layout_id):
    """The contract's fixed layout named ``layout_id``, or None."""
    return next((layout for layout in FIXED_LAYOUTS if type(layout_id) is str and layout.layout_id == layout_id), None)


def region_value(source, region, rotation):
    """``((region, rotation), None)`` as a record states them for ``source``, or ``(None, refusal)``.

    The synthetic source has no frame: both are None, and anything else is
    refused. For the left camera both are always stated: ``region`` is four
    fractions of the native frame ``[x0, y0, x1, y1]`` as floats (None is the
    whole frame) and ``rotation`` the clockwise quarter turn in degrees
    (None is 0); the rules are ``camera.region.Region``'s.
    """
    if source == SYNTHETIC:
        return ((None, None), None) if region is None and rotation is None else (None, "scope_region_not_for_source")
    if rotation is None:
        rotation = 0
    if type(rotation) is not int or rotation not in ROTATIONS:
        return None, "scope_region_invalid"
    if region is None:
        region = WHOLE_FRAME
    if type(region) not in (list, tuple) or len(region) != 4:
        return None, "scope_region_invalid"
    values = [_number(value) for value in region]
    if None in values:
        return None, "scope_region_invalid"
    x0, y0, x1, y1 = values
    if (not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1)
            or x1 - x0 < REGION_MIN_FRACTION - REGION_MIN_FRACTION_TOLERANCE
            or y1 - y0 < REGION_MIN_FRACTION - REGION_MIN_FRACTION_TOLERANCE):
        return None, "scope_region_invalid"
    return (values, rotation), None


def alignment_value(source, layout, alignment):
    """``(alignment, None)`` as a record states it for ``source`` and ``layout``, or ``(None, refusal)``.

    An alignment belongs to a run that maps camera boxes onto a panel: the
    left camera with any fixed layout but the strip, whose rows take none.
    There it is required, as exactly ``ALIGNMENT_FIELDS`` (the shape of
    the alignment class's ``report()``), within that class's ranges; the
    neutral alignment is stated like any other. Everywhere else it is None.
    """
    expected = source == CAMERA_LEFT and layout is not HUD_CONFIG
    if not expected or alignment is None:
        return (None, None) if not expected and alignment is None else (None, "scope_alignment_not_for_layout")
    if type(alignment) is not dict or set(alignment) != set(ALIGNMENT_FIELDS):
        return None, "scope_alignment_invalid"
    scales = [_number(alignment[name]) for name in ALIGNMENT_FIELDS[:2]]
    offsets = [_number(alignment[name]) for name in ALIGNMENT_FIELDS[2:4]]
    turn = alignment["turn"]
    if (None in scales or None in offsets or type(turn) is not int or turn not in ROTATIONS
            or not all(ALIGNMENT_SCALE_LIMITS[0] <= value <= ALIGNMENT_SCALE_LIMITS[1] for value in scales)
            or not all(-ALIGNMENT_OFFSET_LIMIT <= value <= ALIGNMENT_OFFSET_LIMIT for value in offsets)):
        return None, "scope_alignment_invalid"
    return dict(zip(ALIGNMENT_FIELDS, (*scales, *offsets, turn))), None


def presence_value(source, presence_watch):
    """``(presence_watch, None)`` as a record states it for ``source``, or ``(None, refusal)``: a ``bool``, and
    never True for the synthetic source, whose run opens no camera and holds no watch."""
    if type(presence_watch) is not bool:
        return None, "scope_presence_watch_invalid"
    if presence_watch and source != CAMERA_LEFT:
        return None, "scope_presence_watch_not_for_source"
    return presence_watch, None


def view_value(source, layout, region, rotation, alignment, presence_watch):
    """``(view, None)`` for the four view fields as a record states them, or ``(None, refusal)``.

    ``view`` is ``{"region", "rotation", "alignment", "presence_watch"}``.
    The writer builds a record's fields with it, the check requires a
    record's fields to be exactly what it returns for them, and a run's
    request goes through it before it is compared with the record, so the
    three cannot disagree about what a value means.
    """
    (pair, refusal) = region_value(source, region, rotation)
    if refusal is None:
        aligned, refusal = alignment_value(source, layout, alignment)
    if refusal is None:
        watch, refusal = presence_value(source, presence_watch)
    if refusal is not None:
        return None, refusal
    return {"region": pair[0], "rotation": pair[1], "alignment": aligned, "presence_watch": watch}, None


def _same_json(first, second):
    """Whether two view values are the same JSON: equal, and number for number of the same type (1 is not 1.0)."""
    if type(first) is not type(second):
        return False
    if type(first) is dict:
        return set(first) == set(second) and all(_same_json(first[name], second[name]) for name in first)
    if type(first) is list:
        return len(first) == len(second) and all(_same_json(a, b) for a, b in zip(first, second))
    return first == second


def check_record(record):
    """The refusal code for a record that is not well formed on its own, else None; nothing on disk is read.

    A record is exactly ``FIELDS``, with the values this checkout can serve:
    this schema version (a record that states another is
    ``scope_record_schema_unsupported``, whatever else it holds), a known
    step, that step's source, ``camera_hud``, one of the bridge's fixed
    layouts with the digest this checkout computes for it, the view fields
    exactly as ``view_value`` states them for that source and layout, a
    duration within the step's bound and a deadline at most
    ``MAX_VALID_MINUTES`` after its creation.
    """
    if (type(record) is dict and record.get("report_kind") == REPORT_KIND
            and type(record.get("schema_version")) is int and record["schema_version"] != SCHEMA_VERSION):
        return "scope_record_schema_unsupported"  # Before the field set: an old record has other fields.
    if type(record) is not dict or set(record) != set(FIELDS):
        return "scope_record_invalid"
    if (record["report_kind"] != REPORT_KIND or type(record["schema_version"]) is not int
            or record["schema_version"] != SCHEMA_VERSION or record["statement"] != STATEMENT):
        return "scope_record_invalid"
    for name, pattern in (("run_id", RUN_ID), ("approval", APPROVAL), ("revision", manifest.COMMIT),
                          ("manifest_sha256", manifest.SHA256)):
        if type(record[name]) is not str or not pattern.fullmatch(record[name]):
            return "scope_record_invalid"
    created, deadline = _utc(record["created_utc"]), _utc(record["deadline_utc"])
    if created is None or deadline is None or not 0 < deadline - created <= MAX_VALID_MINUTES * 60:
        return "scope_record_invalid"
    if type(record["step"]) is not str or record["step"] not in STEPS:
        return "scope_step_unknown"
    source, bound = STEPS[record["step"]]
    if type(record["source"]) is not str or record["source"] not in USES:
        return "scope_source_not_permitted"  # The mirror, both cameras, the right camera, anything else.
    if record["source"] != source:
        return "scope_step_source_mismatch"
    if record["runner"] != RUNNER:
        return "scope_runner_not_permitted"
    layout = fixed_layout_of(record["hud_config_id"])
    if layout is None or record["hud_config_sha256"] != config_digest(layout):
        return "scope_geometry_mismatch"
    view, refusal = view_value(source, layout, record["region"], record["rotation"], record["alignment"],
                               record["presence_watch"])
    if refusal is not None:
        return refusal
    for name, code in (("region", "scope_region_invalid"), ("rotation", "scope_region_invalid"),
                       ("alignment", "scope_alignment_invalid")):
        if not _same_json(view[name], record[name]):  # Stated another way than the writer states it: an int, a default.
            return code
    if type(record["max_seconds"]) is not int or record["max_seconds"] < 1:
        return "scope_record_invalid"
    if record["max_seconds"] > bound:
        return "scope_duration_over_bound"
    return None


def read_record(path):
    """``(record, None)`` for a readable JSON object at ``path``, else ``(None, refusal)``; never raises."""
    try:
        path = Path(path)
        if not path.is_file():
            return None, "scope_record_missing"
        with path.open("rb") as source:
            data = source.read(MAX_RECORD_BYTES + 1)
    except (OSError, TypeError, ValueError):
        return None, "scope_record_missing"
    if len(data) > MAX_RECORD_BYTES:
        return None, "scope_record_invalid"
    try:
        record = json.loads(data, object_pairs_hook=_unique_keys)
    except (ValueError, RecursionError):
        return None, "scope_record_invalid"
    return (record, None) if type(record) is dict else (None, "scope_record_invalid")


def _unique_keys(pairs):
    """A JSON object as a dict; a key written twice is refused, so no reader can see another value than this one."""
    record = dict(pairs)
    if len(record) != len(pairs):
        raise ValueError("duplicate key")
    return record


PINNED_BINARIES = (manifest.BRIDGE, *manifest.CAMERA_HUD_BINARIES)  # What a device mode may launch.


def read_manifest(path):
    """What a record needs of the manifest at ``path``, or None when it cannot be read.

    ``{"schema_version", "groups", "pins", "layouts"}``: the version it
    states, the groups it carries, the digest it recorded for the bridge,
    the camera helper and the presence watcher (checkout-relative path ->
    digest), and the digest it recorded for each fixed layout.
    """
    try:
        with Path(path).open("rb") as source:
            data = json.loads(source.read(manifest.MAX_MANIFEST_BYTES + 1))
        pins = {manifest.BRIDGE: data["bridge"][manifest.BRIDGE]}
        camera = data.get(manifest.CAMERA_GROUP)
        if isinstance(camera, dict):
            pins.update({path: camera.get(path) for path in manifest.CAMERA_HUD_BINARIES})
        layouts = data.get("layouts")
        return {"schema_version": data.get("schema_version"), "groups": list(data.get("groups") or ()), "pins": pins,
                "layouts": layouts if isinstance(layouts, dict) else {}}
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        return None


def manifest_refusal(facts, layout_id=None, layout_sha256=None):
    """Why the manifest ``read_manifest`` read cannot serve a record, or None; ``verify_manifest`` comes after.

    In this order: a manifest of another schema version (written before the
    presence watcher and the layouts were pinned) is refused by name; it
    must carry the camera HUD's group with a digest for the helper and the
    presence watcher, and for the bridge; and, when a layout is named, the
    digest it recorded for that layout must be the record's.
    """
    if facts is not None and (type(facts["schema_version"]) is not int
                              or facts["schema_version"] != manifest.SCHEMA_VERSION):
        return "scope_manifest_schema_unsupported"
    if (facts is None or manifest.CAMERA_GROUP not in facts["groups"]
            or not all(type(facts["pins"].get(path)) is str for path in PINNED_BINARIES)):
        return "scope_manifest_lacks_camera_group"
    if layout_id is not None and facts["layouts"].get(layout_id) != layout_sha256:
        return "scope_manifest_layout_mismatch"
    return None


def _consume(root, record):
    """Mark the run id used, exclusively: the refusal code when it already was or the ledger cannot say."""
    ledger = Path(root) / LEDGER
    entry = {"run_id": record["run_id"], "step": record["step"], "consumed_utc": _stamp(int(time.time())),
             "process_id": os.getpid()}
    try:
        ledger.mkdir(parents=True, exist_ok=True)
    except OSError:  # A file where the ledger should be, a read-only checkout: the ledger cannot say.
        return "scope_ledger_unavailable"
    try:
        descriptor = os.open(ledger / f"{record['run_id']}.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
                             0o644)
    except FileExistsError:
        return "scope_run_id_used"
    except OSError:
        return "scope_ledger_unavailable"
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(manifest.encode(entry))
            output.flush()
            os.fsync(output.fileno())
    except OSError:
        return "scope_ledger_unavailable"  # The entry exists, so the id is spent either way.
    return None


def pinned_binary(path, pinned):
    """Whether ``path`` is the checkout's file at checkout-relative ``pinned``, the one the manifest hashes.

    The two permission sites ask this of the bridge and of the camera helper
    they are about to launch, so a record cannot cover a binary the manifest
    never saw. Symbolic links are resolved on both sides.
    """
    try:  # ``~`` expands, as ``camera_helper.helper_path`` expands it: one rule for both.
        return (os.path.realpath(os.path.expanduser(os.fspath(path))) == os.path.realpath(Path(ROOT) / pinned)
                and os.path.isfile(Path(ROOT) / pinned))
    except (TypeError, ValueError, OSError):
        return False


class RunScope:
    """What one run asks to do under one record, and whether it may.

    ``record`` and ``checkpoint_manifest`` are paths the operator names;
    naming them permits nothing, the record's content does. ``runner`` is the
    calling runner's name, ``step`` the device step it is running, ``source``
    what the run reads (``CAMERA_LEFT`` or ``SYNTHETIC``), ``seconds`` its
    bound and ``hud_config`` the layout it shows: one of the contract's own
    ``FIXED_LAYOUTS`` objects, the strip unless another is named. ``region``
    (four fractions, None for the whole frame), ``rotation`` (None for 0),
    ``alignment`` (a dict of ``ALIGNMENT_FIELDS``, or None where the run has
    none) and ``presence_watch`` are the view the run uses; ``view`` holds
    them as a record states them once the scope was consulted.
    ``permit(use, holder)`` answers the literal ``True`` or ``False``.

    The first ``permit`` verifies everything, in this order, and stops at the
    first refusal (``refusal`` names it):

    1. the request itself: a known runner, step and source that belong
       together, a positive finite duration, a layout that is one of the
       contract's objects, and a view that is well formed for that source
       and layout (``view_value``);
    2. the record: present, readable, without a repeated key, of this
       schema version, well formed (``check_record``);
    3. the request against the record, field by field, each difference with
       its own reason: the same step, runner and source; the same layout,
       by id and by digest (``scope_layout_mismatch``); the same region,
       rotation, alignment and presence-watch requirement
       (``scope_region_mismatch``, ``scope_rotation_mismatch``,
       ``scope_alignment_mismatch``, ``scope_presence_watch_mismatch``); a
       duration within the record's;
    4. the clock: the record was not created in the future (beyond
       ``CLOCK_TOLERANCE_SECONDS``), its deadline is not further ahead than
       ``MAX_VALID_MINUTES``, and now plus the run's duration is not after it;
    5. the revision: the checkout's commit is the record's;
    6. the manifest: the file's digest is the record's; it is of the current
       schema version; it carries the camera HUD's group with a digest for
       the helper and the presence watcher (for every step); the digest it
       recorded for the record's layout is the record's; and
       ``manifest.verify_manifest`` reports ``verified`` (a clean tree and
       every pinned file, binary, model and package as recorded);
    7. the run id: unused, and marked used in the ledger now.

    Steps 1 to 6 consume nothing: a request that differs from its record in
    any bound field is refused with the record unspent, and the same record
    still serves a run that asks for what it binds. Only step 7 spends the
    run id, and from then on it stays spent whatever happens to the run.

    Only then is the scope granted, for the record's source's uses: real HUD
    output, and for the left camera also camera input to the display. Each
    use goes to the first holder that asks for it and to no other. When that
    holder asks again (the client's ``start`` after its ``check_start``) the
    answer is ``True`` only if the scope is still good: not revoked, the
    run's remaining seconds still end before the deadline, and the binary
    that use launches still has the digest the manifest recorded. Nothing is
    consumed again. ``revoke()`` ends the scope for good: a run that was
    refused or failed before its bridge started calls it, so a kept grant
    cannot start anything later. A scope verifies once; a refused scope stays
    refused, and its run id, if step 7 was reached, stays spent.
    """

    def __init__(self, record, checkpoint_manifest, *, runner, step, source, seconds, hud_config=HUD_CONFIG,
                 region=None, rotation=None, alignment=None, presence_watch=False):
        self.record_path, self.manifest_path = record, checkpoint_manifest
        self.runner, self.step, self.source, self.seconds = runner, step, source, seconds
        self.hud_config = hud_config
        self.region, self.rotation, self.alignment, self.presence_watch = region, rotation, alignment, presence_watch
        self.view = None  # The four view fields as a record states them; set when the request is well formed.
        self.record = None
        self.status, self.refusal = "not_consulted", None
        self.consumed = False
        self.deadline = None
        self.granted_monotonic = None  # When the scope was granted: the run's seconds count from here.
        self.pins = {}  # Checkout-relative path -> the digest the manifest recorded, for ``PINNED_BINARIES``.
        self._holders = {}  # use -> the id of the object it was granted to.

    def _verify(self):
        if type(self.runner) is not str or self.runner != RUNNER:
            return "scope_runner_not_permitted"
        if type(self.source) is not str or self.source not in USES:
            return "scope_source_not_permitted"
        if type(self.step) is not str or self.step not in STEPS:
            return "scope_step_unknown"
        if STEPS[self.step][0] != self.source:
            return "scope_step_source_mismatch"
        if type(self.seconds) not in (int, float) or not 0 < self.seconds < float("inf"):
            return "scope_duration_invalid"
        if not any(self.hud_config is known for known in FIXED_LAYOUTS):  # The contract's own object, never a copy.
            return "scope_geometry_mismatch"
        view, refusal = view_value(self.source, self.hud_config, self.region, self.rotation, self.alignment,
                                   self.presence_watch)
        if refusal is not None:
            return refusal
        self.view = view
        record, refusal = read_record(self.record_path)
        refusal = refusal or check_record(record)
        if refusal is not None:
            return refusal
        self.record = record
        if record["step"] != self.step:
            return "scope_step_mismatch"
        if record["source"] != self.source:
            return "scope_source_mismatch"
        if (record["hud_config_id"], record["hud_config_sha256"]) != (self.hud_config.layout_id,
                                                                      config_digest(self.hud_config)):
            return "scope_layout_mismatch"
        for name in ("region", "rotation", "alignment", "presence_watch"):
            if not _same_json(record[name], view[name]):
                return f"scope_{name}_mismatch"
        if self.seconds > record["max_seconds"]:
            return "scope_duration_over_record"
        self.deadline = _utc(record["deadline_utc"])
        now = time.time()
        if _utc(record["created_utc"]) > now + CLOCK_TOLERANCE_SECONDS:
            return "scope_record_from_the_future"
        if now >= self.deadline:
            return "scope_record_expired"
        if self.deadline - now > MAX_VALID_MINUTES * 60:
            return "scope_deadline_too_far"
        if now + self.seconds > self.deadline:
            return "scope_run_would_pass_deadline"
        if _revision(ROOT) != record["revision"]:
            return "scope_revision_mismatch"
        if _file_digest(self.manifest_path) != record["manifest_sha256"]:
            return "scope_manifest_digest_mismatch"
        facts = read_manifest(self.manifest_path)
        refusal = manifest_refusal(facts, record["hud_config_id"], record["hud_config_sha256"])
        if refusal is not None:
            return refusal
        verification = manifest.verify_manifest(self.manifest_path, ROOT)
        if verification["status"] != "verified" or verification["commit"] != record["revision"]:
            return "scope_manifest_not_verified"
        self.pins = facts["pins"]
        refusal = _consume(ROOT, record)
        # Spent when the ledger took the entry, even if writing its text then failed.
        self.consumed = refusal is None or (
            refusal == "scope_ledger_unavailable" and (Path(ROOT) / LEDGER / f"{record['run_id']}.json").exists())
        return refusal

    def remaining_seconds(self):
        """The run's seconds not yet used, counted from the grant; None before it."""
        if self.granted_monotonic is None:
            return None
        return max(0.0, self.seconds - (time.monotonic() - self.granted_monotonic))

    def _still_good(self, use):
        """What can have changed since the grant, checked without consuming: the clock and the launched binary."""
        if time.time() + self.remaining_seconds() > self.deadline:
            return False
        pinned = USE_BINARIES[use]
        return _file_digest_of(Path(ROOT) / pinned) == self.pins.get(pinned)

    def permit(self, use, holder=None):
        """Literal ``True`` if ``holder`` may make ``use`` now, else literal ``False``.

        The first holder to ask for a use gets it; the same holder asking
        again is answered after ``_still_good``; any other holder is refused.
        """
        if self.status == "not_consulted":
            try:
                self.refusal = self._verify()
            except Exception:  # Whatever went wrong, the answer is no.
                self.refusal = "scope_verification_failed"
            self.status = "refused" if self.refusal is not None else "granted"
            if self.status == "granted":
                self.granted_monotonic = time.monotonic()
        try:
            if self.status != "granted" or use not in USES[self.source]:
                return False
            if self._holders.setdefault(use, id(holder)) != id(holder):
                return False
            return self._still_good(use) is True
        except Exception:
            return False

    def revoke(self):
        """End the scope for good; its run id stays spent. Any later ``permit`` answers ``False``."""
        if self.status in ("not_consulted", "granted"):
            self.status, self.refusal = "revoked", self.refusal or "scope_revoked"

    def report(self):
        """Identifiers only: what the record said, whether it was granted, and why not."""
        record = self.record or {}
        return {"status": self.status, "refusal": self.refusal, "consumed": self.consumed,
                "requested_runner": self.runner if type(self.runner) is str and APPROVAL.fullmatch(self.runner) else None,
                "requested_step": self.step if type(self.step) is str and self.step in STEPS else None,
                "requested_source": self.source if type(self.source) is str and self.source in USES else None,
                **{name: record.get(name) for name in ("step", "source", "run_id", "revision", "manifest_sha256",
                                                       "deadline_utc", "max_seconds", "approval")}}


def write_record(path, *, step, approval, checkpoint_manifest, valid_minutes, hud_config, max_seconds=None,
                 region=None, rotation=None, alignment=None, presence_watch=None):
    """Create a record for one approved step at a new ``path``; return it. The Frame manager's staging tool only.

    Writing a record is not an approval and opens nothing: it records, for
    one run, the approval whose identifier is ``approval``. The source and
    the default duration come from the step; the revision, the manifest's
    digest and the layout's digest are read from this checkout, and
    the manifest must verify now, so a record is never written for a dirty
    tree or a manifest of another build. The run id is new random text.
    ``hud_config`` is the layout the run may show, one of the contract's
    ``FIXED_LAYOUTS`` objects; it has no default, so no record names a
    layout nobody chose. ``region``, ``rotation`` and ``alignment`` are the
    view (``view_value``): for a camera step no region is the whole frame
    and no rotation is 0, both then stated in the record, and an alignment
    is required exactly where the layout takes one. ``presence_watch`` must
    be stated (True or False) for a camera step and is False for the
    synthetic one.
    Refuses (``ScopeError``) an unknown step, a malformed approval
    identifier, a duration over the step's bound, a validity outside 1 to
    ``MAX_VALID_MINUTES`` minutes, an unknown layout, a view that is
    malformed or does not belong to the step and layout, a manifest of
    another schema version or without the camera HUD's group, an existing
    path and a path that would dirty the checkout.
    """
    if type(step) is not str or step not in STEPS:
        raise ScopeError("scope_step_unknown", f"The step must be one of {', '.join(STEPS)}")
    source, bound = STEPS[step]
    if type(approval) is not str or not APPROVAL.fullmatch(approval):
        raise ScopeError("scope_approval_invalid", "The approval reference must be an identifier, not free text")
    max_seconds = bound if max_seconds is None else max_seconds
    if type(max_seconds) is not int or not 1 <= max_seconds <= bound:
        raise ScopeError("scope_duration_over_bound", f"{step} runs at most {bound} seconds")
    if type(valid_minutes) is not int or not 1 <= valid_minutes <= MAX_VALID_MINUTES:
        raise ScopeError("scope_validity_invalid", f"A record is valid for 1 to {MAX_VALID_MINUTES} minutes")
    if not any(hud_config is known for known in FIXED_LAYOUTS):
        raise ScopeError("scope_geometry_mismatch", "The layout must be one of the bridge's fixed layouts: "
                         + ", ".join(layout.layout_id for layout in FIXED_LAYOUTS))
    if presence_watch is None and source == SYNTHETIC:
        presence_watch = False
    view, refusal = view_value(source, hud_config, region, rotation, alignment, presence_watch)
    if refusal is not None:
        raise ScopeError(refusal, VIEW_REFUSALS[refusal])
    path = Path(path)
    if path.suffix != ".json" or os.path.lexists(path) or not path.parent.is_dir():
        raise ScopeError("scope_record_path_invalid", f"{path} must be a new .json path in an existing directory")
    facts = read_manifest(checkpoint_manifest)
    refusal = None if facts is None else manifest_refusal(facts, hud_config.layout_id, config_digest(hud_config))
    if refusal == "scope_manifest_schema_unsupported":
        raise ScopeError(refusal, "The manifest was written under another schema version; write a new one with "
                                  "--camera-hud after the builds")
    if refusal == "scope_manifest_lacks_camera_group":
        raise ScopeError(refusal, "The manifest does not carry the camera HUD's group with the helper and the "
                                  "presence watcher; write it with --camera-hud after the builds")
    if refusal is not None:
        raise ScopeError(refusal, "The manifest records another digest for this layout than this checkout computes")
    verification = manifest.verify_manifest(checkpoint_manifest, ROOT)
    if verification["status"] != "verified":
        raise ScopeError("scope_manifest_not_verified", "The checkpoint manifest does not verify against this checkout")
    if manifest._would_dirty(ROOT, path):
        raise ScopeError("scope_record_would_dirty_tree", f"{path} is inside the checkout and not ignored")
    created = int(time.time())
    record = {"report_kind": REPORT_KIND, "schema_version": SCHEMA_VERSION, "step": step, "runner": RUNNER,
              "source": source, "hud_config_id": hud_config.layout_id, "hud_config_sha256": config_digest(hud_config),
              **view,
              "revision": verification["commit"], "manifest_sha256": verification["manifest_sha256"],
              "run_id": secrets.token_hex(16), "created_utc": _stamp(created),
              "deadline_utc": _stamp(created + int(timedelta(minutes=valid_minutes).total_seconds())),
              "max_seconds": max_seconds, "approval": approval, "statement": STATEMENT}
    refusal = check_record(record)
    if refusal is not None:  # Never write what the check would refuse.
        raise ScopeError(refusal, "The record would not verify")
    try:
        manifest._create(path, manifest.encode(record))
    except FileExistsError:
        raise ScopeError("scope_record_path_invalid", f"{path} already exists") from None
    return record
