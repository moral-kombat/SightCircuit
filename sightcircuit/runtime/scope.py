"""The one-run scope record (work-order item L10): the diagnostic tier of decision D3, without the mask.

Every gate in ``sightcircuit/gates.py`` is closed and this module opens none.
The owner decided on 2026-11-16 (decision 15) that each displayed device step
of the camera display work order (L-D3, L-D4, L-D5) runs under one one-run
record, and that a re-run is a new approval with a new record. A record is a
small JSON file that ties one run to that approval. It is bound to:

* the checkout revision (the full commit);
* the checkpoint manifest's digest (the manifest pins the helper, the bridge,
  the models, every module and the camera HUD's scripts, so one digest covers
  them all; the permission sites launch only the bridge and the helper at
  the paths the manifest hashes, ``pinned_binary``);
* a run id, which the ledger makes single-use;
* an absolute UTC deadline, by which the whole run must have ended;
* the duration bound, at most the step's own, which the permission sites tie
  to what the run does: the client ends real output itself once the run's
  seconds are over, and a camera run's stage and helper bounds must lie
  within them;
* the source: ``arcturus_camera_left``, or ``synthetic`` for the expiry and
  soak step, which never opens the camera;
* the HUD configuration, ``fixed-edge-hud-v1`` and its digest;
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
import os
from pathlib import Path
import re
import secrets
import time

from ..errors import SightCircuitError
from ..hud.contract import HUD_CONFIG, config_digest
from . import manifest

ROOT = manifest.ROOT  # The checkout a record is bound to: this one. Not an argument of anything here.
LEDGER = "artifacts/scope-ledger"  # Checkout-relative and ignored by git: one file per consumed run id.
REPORT_KIND = "one_run_scope_record"
SCHEMA_VERSION = 1
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
          "revision", "manifest_sha256", "run_id", "created_utc", "deadline_utc", "max_seconds", "approval",
          "statement")
STATEMENT = ("One-run scope record: a procedural control, not a cryptographic one. It opens no gate and is not "
             "an approval; the owner's approval in the Frame manager's session is the authority.")


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


def check_record(record):
    """The refusal code for a record that is not well formed on its own, else None; nothing on disk is read.

    A record is exactly ``FIELDS``, with the values this checkout can serve:
    a known step, that step's source, ``camera_hud``, the fixed HUD
    configuration and its digest, a duration within the step's bound and a
    deadline at most ``MAX_VALID_MINUTES`` after its creation.
    """
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
    if record["hud_config_id"] != HUD_CONFIG.layout_id or record["hud_config_sha256"] != config_digest(HUD_CONFIG):
        return "scope_geometry_mismatch"
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


def read_pins(path):
    """``(groups, {checkout-relative path: digest})`` of the manifest at ``path``: what it carries and what it
    recorded for the bridge and the camera helper; ``(None, {})`` when it cannot be read."""
    try:
        with Path(path).open("rb") as source:
            data = json.loads(source.read(manifest.MAX_MANIFEST_BYTES + 1))
        pins = {manifest.BRIDGE: data["bridge"][manifest.BRIDGE]}
        camera = data.get(manifest.CAMERA_GROUP)
        if isinstance(camera, dict):
            pins[manifest.CAMERA_HELPER] = camera.get(manifest.CAMERA_HELPER)
        return list(data.get("groups") or ()), pins
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        return None, {}


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
    bound and ``hud_config`` the tile it draws. ``permit(use, holder)``
    answers the literal ``True`` or ``False``.

    The first ``permit`` verifies everything, in this order, and stops at the
    first refusal (``refusal`` names it):

    1. the request itself: a known runner, step and source that belong
       together, a positive finite duration;
    2. the record: present, readable, without a repeated key, well formed
       (``check_record``);
    3. the request against the record: the same step, runner, source and
       configuration, a duration within the record's;
    4. the clock: the record was not created in the future (beyond
       ``CLOCK_TOLERANCE_SECONDS``), its deadline is not further ahead than
       ``MAX_VALID_MINUTES``, and now plus the run's duration is not after it;
    5. the revision: the checkout's commit is the record's;
    6. the manifest: the file's digest is the record's, it carries the camera
       HUD's group with the helper's digest (for every step), and
       ``manifest.verify_manifest`` reports ``verified`` (a clean tree and
       every pinned file, binary, model and package as recorded);
    7. the run id: unused, and marked used in the ledger now.

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

    def __init__(self, record, checkpoint_manifest, *, runner, step, source, seconds, hud_config=HUD_CONFIG):
        self.record_path, self.manifest_path = record, checkpoint_manifest
        self.runner, self.step, self.source, self.seconds = runner, step, source, seconds
        self.hud_config = hud_config
        self.record = None
        self.status, self.refusal = "not_consulted", None
        self.consumed = False
        self.deadline = None
        self.granted_monotonic = None  # When the scope was granted: the run's seconds count from here.
        self.pins = {}  # Checkout-relative path -> the digest the manifest recorded, for the bridge and the helper.
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
        record, refusal = read_record(self.record_path)
        refusal = refusal or check_record(record)
        if refusal is not None:
            return refusal
        self.record = record
        if record["step"] != self.step:
            return "scope_step_mismatch"
        if record["source"] != self.source:
            return "scope_source_mismatch"
        if self.hud_config != HUD_CONFIG:
            return "scope_geometry_mismatch"
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
        groups, pins = read_pins(self.manifest_path)
        if (groups is None or manifest.CAMERA_GROUP not in groups
                or not all(type(pins.get(path)) is str for path in (manifest.BRIDGE, manifest.CAMERA_HELPER))):
            return "scope_manifest_lacks_camera_group"
        verification = manifest.verify_manifest(self.manifest_path, ROOT)
        if verification["status"] != "verified" or verification["commit"] != record["revision"]:
            return "scope_manifest_not_verified"
        self.pins = pins
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


def write_record(path, *, step, approval, checkpoint_manifest, valid_minutes, max_seconds=None):
    """Create a record for one approved step at a new ``path``; return it. The Frame manager's staging tool only.

    Writing a record is not an approval and opens nothing: it records, for
    one run, the approval whose identifier is ``approval``. The source and
    the default duration come from the step; the revision, the manifest's
    digest and the configuration's digest are read from this checkout, and
    the manifest must verify now, so a record is never written for a dirty
    tree or a manifest of another build. The run id is new random text.
    Refuses (``ScopeError``) an unknown step, a malformed approval
    identifier, a duration over the step's bound, a validity outside 1 to
    ``MAX_VALID_MINUTES`` minutes, an existing path and a path that would
    dirty the checkout.
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
    path = Path(path)
    if path.suffix != ".json" or os.path.lexists(path) or not path.parent.is_dir():
        raise ScopeError("scope_record_path_invalid", f"{path} must be a new .json path in an existing directory")
    groups, pins = read_pins(checkpoint_manifest)
    if groups is not None and (manifest.CAMERA_GROUP not in groups or type(pins.get(manifest.CAMERA_HELPER)) is not str):
        raise ScopeError("scope_manifest_lacks_camera_group",
                         "The manifest does not carry the camera HUD's group; write it with --camera-hud after the builds")
    verification = manifest.verify_manifest(checkpoint_manifest, ROOT)
    if verification["status"] != "verified":
        raise ScopeError("scope_manifest_not_verified", "The checkpoint manifest does not verify against this checkout")
    if manifest._would_dirty(ROOT, path):
        raise ScopeError("scope_record_would_dirty_tree", f"{path} is inside the checkout and not ignored")
    created = int(time.time())
    record = {"report_kind": REPORT_KIND, "schema_version": SCHEMA_VERSION, "step": step, "runner": RUNNER,
              "source": source, "hud_config_id": HUD_CONFIG.layout_id, "hud_config_sha256": config_digest(HUD_CONFIG),
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
