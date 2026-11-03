"""HUD session tokens of a mirror run, from read-only providers; never an invented value.

A bound ``HudMask`` rejects any frame whose session tokens
(``hud.contract.SESSION_TOKENS``) are not declared by its source or reported
by the frame, so a mirror source must say where each one comes from. Each
provider below observes one or two tokens and returns a ``Reading``: the
values it observed and, for every token it could not observe, a named reason.
A token that cannot be observed stays undeclared; no provider substitutes a
default.

* ``HostClock``: ``timebase_id``, the clock behind ``Frame.observed_ns``
  (``time.monotonic_ns``, ``CLOCK_MONOTONIC``) on this boot and in this
  process's time namespace: ``/proc/sys/kernel/random/boot_id``,
  ``/proc/self/ns/time`` and that namespace's offset
  (``/proc/self/timens_offsets``).
* ``RuntimeProcess``: ``session_id`` and ``runtime_id`` of the running SteamVR
  server, found read-only under ``/proc`` by its command name. ``session_id``
  is the boot id, its pid and its start time (``/proc/<pid>/stat`` field 22).
  ``runtime_id`` is the SHA-256 of the binary the process is running, read
  through ``/proc/<pid>/exe``, never through an install path or manifest.
* ``TrackingObserver``: ``tracking_origin`` and ``recenter_epoch`` from
  runtime events fed to ``observe`` (bridge-shaped dictionaries, see
  ``TrackingObserver``). Nothing here talks to a VR runtime.

``MirrorSession`` combines providers for ``sources.mirror.MirrorSource``: it
declares their tokens once, before the source opens, re-reads them for every
frame (the frame's session metadata) and reports what it saw. The declaration
fixes which tokens the run carries: a token first observed later stays
undeclared. A declared token that can no longer be observed is reported as
``None``, so ``CaptureContext`` and ``HudMask.check_session`` fail closed.
Tests pass a fake /proc root (``proc_root``) and hand-written events; nothing
here opens a device or changes a gate.
"""
from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import re
import threading

from ..hud.contract import SESSION_TOKENS, token_valid

__all__ = ("HostClock", "MirrorSession", "Reading", "RuntimeProcess", "TrackingObserver", "TRACKING_CHANGES",
           "process_start_ticks", "process_stat")

PROC_ROOT = "/proc"
RUNTIME_PROCESS = "vrserver"  # The SteamVR server's command name and executable basename.
MAX_PROCESS_ENTRIES = 32768  # /proc entries one runtime scan may visit.
MAX_RUNTIME_EXECUTABLE_BYTES = 256 * 1024 * 1024  # Largest running binary hashed for runtime_id.
MAX_SMALL_FILE_BYTES = 4096  # boot_id, timens_offsets, comm and stat are read up to this size.
TASK_COMM_BYTES = 15  # The kernel keeps this many bytes of a command name (comm, stat field 2).
EXITED_STATES = frozenset("ZXx")  # stat field 3: zombie or dead; the process no longer runs.
PROVIDER_MISSING = "session_token_provider_missing"
TOKEN_CHANGED = "token_changed"
# Bridge-reported tracking changes, each with the OpenVR event a reporter would map to it.
TRACKING_CHANGES = {
    "chaperone_universe_changed": "VREvent_ChaperoneUniverseHasChanged",
    "seated_zero_pose_reset": "VREvent_SeatedZeroPoseReset",
    "standing_zero_pose_reset": "VREvent_StandingZeroPoseReset",
    "chaperone_room_setup_committed": "VREvent_ChaperoneRoomSetupCommitted",
}
BOOT_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\n?")
TIME_NAMESPACE = re.compile(r"time:\[([0-9]{1,20})\]")
TIME_OFFSET = re.compile(r"(monotonic|boottime) +(-?[0-9]{1,19}) +([0-9]{1,9})")
SHA256_HEX = re.compile(r"[0-9a-f]{64}")
DELETED_SUFFIX = " (deleted)"


@dataclass(frozen=True)
class Reading:
    """Observed token values, and a named reason for each token that could not be observed."""
    values: dict = field(default_factory=dict)
    reasons: dict = field(default_factory=dict)


def _small_bytes(path):
    """A small file read whole, or ``None`` when it exceeds ``MAX_SMALL_FILE_BYTES``."""
    with open(path, "rb") as stream:
        data = stream.read(MAX_SMALL_FILE_BYTES + 1)
    return None if len(data) > MAX_SMALL_FILE_BYTES else data


def _small_text(path):
    data = _small_bytes(path)
    return None if data is None else data.decode("utf-8", "replace")


def boot_id(proc_root):
    """``(boot_id, None)`` or ``(None, reason)`` from ``<proc_root>/sys/kernel/random/boot_id``."""
    try:
        text = _small_text(Path(proc_root, "sys/kernel/random/boot_id"))
    except OSError:
        return None, "host_boot_id_unreadable"
    if text is None or not BOOT_ID.fullmatch(text):
        return None, "host_boot_id_invalid"
    return text.rstrip("\n"), None


def process_stat(proc_root, pid):
    """``(comm, state, start)`` from ``<proc_root>/<pid>/stat``: fields 2 (bytes), 3 and 22.

    ``start`` is the start time in clock ticks after boot. The command name
    may hold spaces and parentheses, so it runs from the first opening to the
    last closing parenthesis and the other fields are counted after it.
    Raises ``OSError`` when the process is gone and ``ValueError`` when the
    line is malformed.
    """
    data = _small_bytes(Path(proc_root, str(pid), "stat"))
    head, closing, rest = data.rpartition(b")") if data is not None else (b"", b"", b"")
    opening, comm = head.partition(b"(")[1:]
    fields = rest.split()
    if not (closing and opening and len(fields) >= 20 and len(fields[0]) == 1 and fields[0].isalpha() and
            fields[19].isdigit()):
        raise ValueError("process_stat_invalid")
    return comm, fields[0].decode("ascii"), int(fields[19])


def process_start_ticks(proc_root, pid):
    """Field 22 of ``<proc_root>/<pid>/stat`` (see ``process_stat``)."""
    return process_stat(proc_root, pid)[2]


class HostClock:
    """``timebase_id``: ``CLOCK_MONOTONIC`` of this boot, in this process's time namespace.

    ``Frame.observed_ns`` and calibration lifetimes are ``time.monotonic_ns()``
    values. They compare only within one boot and one time namespace, so both
    identities are part of the token. The kernel reuses a freed namespace's
    inode number, so the namespace's ``CLOCK_MONOTONIC`` offset
    (``/proc/self/timens_offsets``) is part of it too. That file describes the
    namespace of this process's children, so it is used only while that is
    this process's own namespace (``ns/time_for_children``). ``CLOCK_MONOTONIC``
    does not advance while the system is suspended, and a suspend does not
    change the token.
    """
    tokens = ("timebase_id",)
    live = False  # Re-read once per frame, not at every admission.

    def __init__(self, proc_root=PROC_ROOT):
        self.proc_root = proc_root

    def _observe(self):
        boot, reason = boot_id(self.proc_root)
        namespace = offset = None
        if reason is None:
            namespace, reason = self._namespace()
        if reason is None:
            offset, reason = self._offset()
        if reason is not None:
            return Reading(reasons={"timebase_id": reason})
        return Reading({"timebase_id": f"clock_monotonic:boot:{boot}:timens:{namespace}:offset:{offset}"})

    def _namespace(self):
        """``(inode, None)`` of this process's time namespace, or ``(None, reason)``."""
        try:
            links = [os.readlink(Path(self.proc_root, "self/ns", name)) for name in ("time", "time_for_children")]
        except OSError:
            return None, "host_time_namespace_unreadable"
        matches = [TIME_NAMESPACE.fullmatch(link) for link in links]
        if None in matches:
            return None, "host_time_namespace_invalid"
        if links[0] != links[1]:
            # After unshare(CLONE_NEWTIME) the offsets file describes the children's namespace.
            return None, "host_time_namespace_for_children_differs"
        return matches[0].group(1), None

    def _offset(self):
        """``("<seconds>:<nanoseconds>", None)``, the namespace's ``CLOCK_MONOTONIC`` offset, or ``(None, reason)``."""
        try:
            text = _small_text(Path(self.proc_root, "self/timens_offsets"))
        except OSError:
            return None, "host_time_offsets_unreadable"
        offsets = {}
        for line in (text or "").removesuffix("\n").split("\n"):
            match = TIME_OFFSET.fullmatch(line.strip())
            if match is None or match.group(1) in offsets:
                return None, "host_time_offsets_invalid"
            offsets[match.group(1)] = f"{int(match.group(2))}:{int(match.group(3))}"
        if "monotonic" not in offsets:
            return None, "host_time_offsets_invalid"
        return offsets["monotonic"], None

    def declare(self):
        return self._observe()

    def read(self):
        return self._observe()


@dataclass(frozen=True)
class _Process:
    pid: int
    start: int
    executable: tuple | None  # (st_dev, st_ino) of the running binary, when readable.


class RuntimeProcess:
    """``session_id`` and ``runtime_id`` of the running SteamVR server, read-only from ``/proc``.

    ``declare`` scans at most ``MAX_PROCESS_ENTRIES`` entries for exactly one
    process whose command name is ``name`` (as the kernel keeps it: its first
    ``TASK_COMM_BYTES`` bytes); a zombie or dead one is
    ``runtime_process_exited``. ``session_id`` is
    ``steamvr:boot:<boot id>:pid:<pid>:start:<ticks>``, which changes when the
    runtime restarts. ``runtime_id`` is ``steamvr:<name>:sha256:<digest>`` of
    the binary the process runs, opened through ``/proc/<pid>/exe``, so an
    update installed while an older build runs does not change it. That link's file name must be ``name`` in full
    (an emulator or loader would name itself), and reading it needs
    permission to inspect the process. ``read`` re-checks the same process
    (its stat command name, state and start time, and the binary's inode when
    it could be inspected) for every frame and loses both tokens when it
    exited or changed; it never rescans or re-hashes.
    """
    tokens = ("session_id", "runtime_id")
    live = False

    def __init__(self, proc_root=PROC_ROOT, name=RUNTIME_PROCESS):
        self.proc_root, self.name = proc_root, name
        self.comm = name.encode()[:TASK_COMM_BYTES]
        self._process = None
        self._declared = Reading()
        self.details = {}

    def _find(self):
        """``(pid, None)`` of the one process named ``name``, or ``(None, reason)``."""
        found = []
        try:
            with os.scandir(self.proc_root) as entries:
                for count, entry in enumerate(entries, 1):
                    if count > MAX_PROCESS_ENTRIES:
                        return None, "runtime_process_scan_limit"
                    if not (entry.name.isascii() and entry.name.isdigit()):
                        continue
                    try:
                        comm = _small_bytes(Path(entry.path, "comm"))
                    except OSError:
                        continue  # Exited during the scan, or not inspectable.
                    if comm is not None and comm.rstrip(b"\n") == self.comm:
                        found.append(int(entry.name))
        except OSError:
            return None, "runtime_process_unreadable"
        if not found:
            return None, "runtime_process_not_found"
        if len(found) > 1:
            return None, "runtime_process_ambiguous"
        return found[0], None

    def _executable(self, pid):
        """``(st_dev, st_ino)`` of the binary ``pid`` runs, or ``None`` when it cannot be inspected."""
        try:
            status = os.stat(Path(self.proc_root, str(pid), "exe"))
        except OSError:
            return None
        return status.st_dev, status.st_ino

    def _digest(self, pid, identity):
        """``(sha256_hex, None)`` of the running binary or ``(None, reason)``."""
        link = Path(self.proc_root, str(pid), "exe")
        try:
            target = os.readlink(link)
        except OSError:
            return None, "runtime_executable_unreadable"
        deleted = target.endswith(DELETED_SUFFIX)
        path = target.removesuffix(DELETED_SUFFIX) if deleted else target
        self.details.update(executable=path, executable_deleted=deleted)
        if os.path.basename(path) != self.name:
            return None, "runtime_executable_unexpected"
        if identity is None:
            return None, "runtime_executable_unreadable"
        digest = hashlib.sha256()
        try:
            with open(link, "rb") as stream:
                status = os.fstat(stream.fileno())
                if (status.st_dev, status.st_ino) != identity:
                    return None, "runtime_process_changed"
                if status.st_size > MAX_RUNTIME_EXECUTABLE_BYTES:
                    return None, "runtime_executable_too_large"
                remaining = MAX_RUNTIME_EXECUTABLE_BYTES + 1
                while chunk := stream.read(min(1 << 20, remaining)):
                    digest.update(chunk)
                    remaining -= len(chunk)
                    if remaining <= 0:
                        return None, "runtime_executable_too_large"
        except OSError:
            return None, "runtime_executable_unreadable"
        return digest.hexdigest(), None

    def _check(self, process):
        """``None`` while ``process`` still runs the same binary, otherwise why it does not."""
        try:
            comm, state, start = process_stat(self.proc_root, process.pid)
        except OSError:
            return "runtime_process_exited"
        except ValueError:
            return "runtime_process_changed"
        if state in EXITED_STATES:
            return "runtime_process_exited"  # A zombie keeps its stat line until it is reaped.
        # The command name catches an exec even when the binary's inode cannot be inspected.
        if comm != self.comm or start != process.start or self._executable(process.pid) != process.executable:
            return "runtime_process_changed"
        return None

    def _lost(self, reason):
        return Reading(reasons=dict.fromkeys(self.tokens, reason))

    def _declare(self):
        pid, reason = self._find()
        if reason is not None:
            return self._lost(reason)
        boot, reason = boot_id(self.proc_root)
        if reason is not None:
            return self._lost(reason)
        try:
            start = process_start_ticks(self.proc_root, pid)
        except OSError:
            return self._lost("runtime_process_exited")
        except ValueError:
            return self._lost("runtime_process_unreadable")
        process = _Process(pid, start, self._executable(pid))
        self.details = {"pid": pid, "start_ticks": start}
        digest, reason = self._digest(pid, process.executable)
        # The same process must run the same binary before and after the digest. The check reads the
        # stat line again, so a zombie, or a pid reused by another program after the scan, is refused here.
        changed = "runtime_process_changed" if reason == "runtime_process_changed" else self._check(process)
        if changed is not None:
            return self._lost(changed)
        self._process = process
        values = {"session_id": f"steamvr:boot:{boot}:pid:{pid}:start:{start}"}
        if reason is not None:
            return Reading(values, {"runtime_id": reason})
        values["runtime_id"] = f"steamvr:{self.name}:sha256:{digest}"
        return Reading(values)

    def declare(self):
        self._process = None
        self._declared = self._declare()
        return self._declared

    def read(self):
        process = self._process
        if process is None:
            return Reading(reasons=dict(self._declared.reasons))
        reason = self._check(process)
        if reason is not None:
            declared = tuple(self._declared.values)
            return Reading(dict.fromkeys(declared), dict.fromkeys(declared, reason))
        return Reading(dict(self._declared.values), dict(self._declared.reasons))


class TrackingObserver:
    """``tracking_origin`` and ``recenter_epoch`` from injected runtime events.

    ``observe(event)`` takes the dictionaries a runtime reporter (for example
    a future overlay-bridge event line) would emit:

    * ``{"event": "tracking_state", "universe_id": <1 .. 2**64-1>,
      "zero_pose_sha256": <64 lowercase hex>}``: the HMD's
      ``Prop_CurrentUniverseId_Uint64`` and a digest of the runtime's
      zero-pose transforms. It sets ``tracking_origin`` to
      ``openvr:universe:<id>:zero-pose:<first 32 hex>``; the first one starts
      ``recenter_epoch`` at 0.
    * ``{"event": "tracking_changed", "change": <a TRACKING_CHANGES name>}``:
      a recenter or chaperone change. Each one after the first state adds 1 to
      ``recenter_epoch``. The origin stays the last observed state until a new
      ``tracking_state`` replaces it; the epoch never resets.

    Other events (no ``tracking_`` prefix) are ignored. A malformed tracking
    event (``tracking_event_invalid``) or ``end(reason)``
    (``tracking_observer_ended``) loses both tokens for good, because a change
    could then go unseen. ``recenter_epoch`` counts this observer's events only;
    a recenter before it started is visible only through ``tracking_origin``.
    Thread-safe: a reader thread feeds it while capture reads it.
    """
    tokens = ("tracking_origin", "recenter_epoch")
    live = True  # In memory: read at every admission, so a change refuses in-flight results.

    def __init__(self):
        self._lock = threading.Lock()
        self._origin = None
        self._epoch = None
        self._lost = None
        self.events = 0
        self.ended = None

    def observe(self, event):
        with self._lock:
            self.events += 1
            if self._lost is not None:
                return
            kind = event.get("event") if isinstance(event, dict) else None
            if isinstance(kind, str) and not kind.startswith("tracking_"):
                return
            if kind == "tracking_state" and self._state(event):
                return
            if kind == "tracking_changed" and event.get("change") in TRACKING_CHANGES:
                if self._epoch is not None:
                    if self._epoch >= 2**63 - 1:
                        self._lost = "tracking_event_invalid"
                    else:
                        self._epoch += 1
                return
            self._lost = "tracking_event_invalid"

    def _state(self, event):
        universe, pose = event.get("universe_id"), event.get("zero_pose_sha256")
        if (type(universe) is not int or not 1 <= universe < 2**64 or type(pose) is not str or
                not SHA256_HEX.fullmatch(pose)):
            return False
        origin = f"openvr:universe:{universe}:zero-pose:{pose[:32]}"
        if not token_valid(origin):
            return False
        self._origin = origin
        if self._epoch is None:
            self._epoch = 0
        return True

    def end(self, reason="tracking_events_ended"):
        """The event stream ended (``reason`` for the report): both tokens are lost."""
        with self._lock:
            if self._lost is None:
                self._lost = "tracking_observer_ended"
            self.ended = str(reason)[:120]

    def _reading(self):
        with self._lock:
            if self._lost is not None:
                return Reading(dict.fromkeys(self.tokens), dict.fromkeys(self.tokens, self._lost))
            if self._origin is None:
                return Reading(reasons=dict.fromkeys(self.tokens, "tracking_state_not_observed"))
            return Reading({"tracking_origin": self._origin, "recenter_epoch": self._epoch})

    def declare(self):
        reading = self._reading()
        # A lost observer declares nothing; its reasons stay.
        return Reading({key: value for key, value in reading.values.items() if value is not None}, reading.reasons)

    def read(self):
        return self._reading()


class MirrorSession:
    """The session tokens of one mirror source, from providers that each own distinct tokens.

    ``declare`` runs once, before the source opens: a token no provider covers
    is undeclared with ``session_token_provider_missing``. It also fixes the
    run's token set: a token undeclared then stays undeclared, even if a
    provider observes it later (for example a first ``tracking_state`` that
    arrives while the model loads), so a bound mask keeps rejecting with
    ``hud_<token>_undeclared``. ``read`` is one frame's session metadata:
    every declared token as observed now, ``None`` once it can no longer be
    observed. ``current`` is what the source declares from then on: the
    declaration until a read observes a change, then the latest read, with
    ``live`` providers re-read each time, so ``CaptureContext`` refuses an
    in-flight result once a change is seen.
    """

    def __init__(self, *providers):
        names = [name for provider in providers for name in getattr(provider, "tokens", ())]
        if (not all(getattr(provider, "tokens", None) for provider in providers) or len(names) != len(set(names)) or
                not set(names) <= set(SESSION_TOKENS)):
            raise ValueError("session_token_providers_invalid")
        self.providers = tuple(providers)
        self._lock = threading.Lock()
        self._declared = None
        self._current = {}
        self._frames = 0
        self._first_change = None

    def declare(self):
        with self._lock:
            if self._declared is not None:
                raise ValueError("session_already_declared")
            self._declared = Reading()  # Claimed before the providers run.
        values, reasons = {}, {}
        for provider in self.providers:
            reading = provider.declare()
            values.update((name, value) for name, value in reading.values.items() if name in provider.tokens)
            reasons.update((name, reason) for name, reason in reading.reasons.items()
                           if name in provider.tokens and name not in reading.values)
        reasons.update((name, PROVIDER_MISSING) for name in SESSION_TOKENS if name not in values and name not in reasons)
        declared = Reading(values, reasons)
        with self._lock:
            self._declared = declared
            self._current = dict(values)
        return declared

    def _merge(self, providers):
        """The declared tokens' values as the given providers observe them now, ``None`` once lost."""
        values, reasons = {}, {}
        declared = self._declared.values if self._declared is not None else {}
        for provider in providers:
            reading = provider.read()
            for name in provider.tokens:
                if name not in declared:
                    continue  # Undeclared at declaration: never adopted later.
                values[name] = reading.values.get(name)
                if values[name] is None:
                    reasons[name] = reading.reasons.get(name, "session_token_lost")
        return values, reasons

    def read(self):
        """One frame's session metadata; records the first frame whose tokens differ from the declaration."""
        if self._declared is None:
            raise ValueError("session_not_declared")
        values, reasons = self._merge(self.providers)
        with self._lock:
            self._frames += 1
            self._current = dict(values)
            if self._first_change is None:
                for name in SESSION_TOKENS:
                    if values.get(name) != self._declared.values.get(name) or (name in values) != (
                            name in self._declared.values):
                        self._first_change = {"frame": self._frames, "token": name,
                                              "reason": reasons.get(name, TOKEN_CHANGED)}
                        break
        return values

    def current(self):
        """What the source declares now (see the class docstring)."""
        live, _ = self._merge(provider for provider in self.providers if getattr(provider, "live", False) is True)
        with self._lock:
            values = dict(self._current)
        values.update(live)
        return values

    def report(self):
        with self._lock:
            declared = self._declared or Reading()
            return {"declared": {name: declared.values.get(name) for name in SESSION_TOKENS},
                    "undeclared": dict(declared.reasons), "frames_read": self._frames,
                    "first_change": dict(self._first_change) if self._first_change else None,
                    "runtime_process": next((dict(provider.details) for provider in self.providers
                                             if isinstance(provider, RuntimeProcess)), None),
                    "scope": "Read-only /proc and injected runtime events; tokens identify, they approve nothing"}
