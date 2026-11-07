"""Checkpoint artifact manifest: the software identity of one device checkpoint.

It is written on Frame after the native build and verified there just before
the run; built binaries are never transferred. It records the git commit and
whether the tree is clean, the Python and platform identity, the SHA-256 of the
built bridge, every ``sightcircuit`` module outside the agent package, the
checkpoint scripts, the bridge's native sources and the assets in
``scripts/smoke-assets.json``, and the installed versions of the packages the
checkpoint imports. The on-device agent (``sightcircuit/agent``, its two
scripts and its settings template) is operations tooling outside the
production gates (decision D7), so it is pinned in its own ``agent`` group:
recorded and verified like every group, but no longer part of the ``modules``
group that is the HUD software's identity. Paths are relative to the checkout
root. It pins software identity only: it is not device, physical-display or
qualification evidence. ``scripts/checkpoint_manifest.py`` is the command.
"""
import hashlib
from importlib import metadata
import json
import math
import os
from pathlib import Path
import platform
import re
import subprocess
import tempfile

from .. import assets
from ..errors import SightCircuitError

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = 2  # 2 (2026-11-07): the agent package left ``modules`` for its own ``agent`` group.
REPORT_KIND = "checkpoint_artifact_manifest"
VERIFICATION_KIND = "checkpoint_manifest_verification"
SCOPE = ("Pins software identity only (checkout, interpreter, packages, the SteamVR package and the bridge built "
         "on this machine); not device, Frame, physical-display or qualification evidence")
BRIDGE = "artifacts/overlay-bridge/sightcircuit-overlay-bridge"
# Everything the checkpoint procedure runs: docs/hud-qualification.md#next-device-checkpoint and
# docs/on-device.md#bounded-edge-hud-calibration-checkpoint. tests/test_checkpoint_manifest.py
# fails if a script named in those procedures is missing here.
CHECKPOINT_SCRIPTS = (
    "scripts/build_openvr_tool.sh", "scripts/build_overlay_bridge.sh", "scripts/checkpoint_manifest.py",
    "scripts/hud_marker_check.py", "scripts/hud_marker_observer.py", "scripts/observation_page.py",
)
NATIVE_SOURCES = ("tools/hud_contract.hpp", "tools/overlay_bridge.cpp", "tools/overlay_frame.hpp",
                  "tools/overlay_lifecycle.hpp")
ASSET_MANIFEST = "scripts/smoke-assets.json"
ASSET_DIRECTORY = ".cache/sightcircuit/assets"
# Import name -> distribution of every third-party module the checkpoint scripts import (tested).
CHECKPOINT_PACKAGES = {"cv2": "opencv-python-headless", "numpy": "numpy"}
# The on-device agent's own group: its package, its scripts and its settings template (decision D7).
AGENT_PACKAGE = "sightcircuit/agent"
AGENT_FILES = ("scripts/frame_agent_guard.py", "scripts/frame_agent_run.py", "sightcircuit/agent/frame-settings.json")
FILE_GROUPS = ("agent", "assets", "bridge", "modules", "native_sources", "scripts")
GIT_TIMEOUT_SECONDS = 30
MAX_MANIFEST_BYTES = 1 << 20
MAX_MANIFEST_DEPTH = 8  # A manifest nests three levels; anything far deeper is foreign.
MAX_REPORTED_PATHS = 20
# The SteamVR runtime on Frame is the pacman package ``deckard-steamvr-rel`` (E85 read it with ``pacman -Q``:
# r25765468+0ab5c24d-1 on SteamOS 0.5.6, r26496310+24cb4748-1 on 0.5.8). The manifest reads it from the
# pacman local database by file reads, best-effort, trying the DBPath of /etc/pacman.conf, the Arch default
# and the path SteamOS 3 ("holo") ships (the A4 preparation of 2026-11-07 found /var/lib/pacman/local
# unavailable to the steamos user while ``pacman -Q`` answered). Only when no database is readable does it
# fall back to a read-only ``pacman -Q``, and the record says which source answered. The version is unknown
# (None, with a reason) where neither does. The desktop SteamVR package name is unverified and not looked for.
STEAMVR_PACKAGE = "deckard-steamvr-rel"
PACMAN_CONFIG = Path("/etc/pacman.conf")
PACMAN_DEFAULT_DATABASE_ROOT = Path("/var/lib/pacman")
PACMAN_HOLO_DATABASE_ROOT = Path("/usr/lib/holo/pacmandb")  # SteamOS 3 pacman.conf: DBPath = /usr/lib/holo/pacmandb/
PACMAN_QUERY_TIMEOUT_SECONDS = 10
MAX_PACKAGE_DESCRIPTION_BYTES = 64 * 1024
MAX_PACKAGE_VERSION_CHARS = 128
SHA256 = re.compile(r"[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")


class ManifestError(SightCircuitError):
    """The checkout cannot be pinned, or the manifest cannot be written."""


def _digest(path):
    """SHA-256 of a regular file, or None when it is absent or not a file."""
    return assets.checksum(path) if Path(path).is_file() else None


def verify_file_digest(path, expected):
    """True only if ``path`` is a regular file whose SHA-256 is ``expected``.

    ``hud.qualification`` checks evidence digests for format only; this checks
    content. A malformed ``expected`` raises ValueError.
    """
    if type(expected) is not str or not SHA256.fullmatch(expected):
        raise ValueError("sha256_invalid")
    return _digest(path) == expected


def _git(root, *arguments):
    """Run one read-only git query; optional locks are off, so the index is never refreshed."""
    try:
        return subprocess.run(("git", "--no-optional-locks", "-C", str(root), *arguments), capture_output=True,
                              stdin=subprocess.DEVNULL, timeout=GIT_TIMEOUT_SECONDS, check=False,
                              env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ManifestError("git_unavailable", f"git {arguments[0]} failed: {error}") from None


def git_state(root=ROOT):
    """(full commit, sorted porcelain entries). Tracked changes and untracked, non-ignored files are dirty.

    Rename detection is off, so every entry is one path with its status.
    """
    root = Path(root)
    top = _git(root, "rev-parse", "--show-toplevel")
    if top.returncode or Path(os.fsdecode(top.stdout.strip())).resolve() != root.resolve():
        raise ManifestError("not_a_checkout", f"{root} is not the top of a git checkout")
    head = _git(root, "rev-parse", "--verify", "HEAD^{commit}")
    commit = head.stdout.decode("ascii", "replace").strip()
    if head.returncode or not COMMIT.fullmatch(commit):
        raise ManifestError("no_commit", f"{root} has no readable HEAD commit")
    status = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignore-submodules=none",
                  "--no-renames")
    if status.returncode:
        raise ManifestError("git_status_failed", status.stderr.decode("utf-8", "replace").strip()[:200])
    return commit, tuple(sorted(entry for entry in os.fsdecode(status.stdout).split("\0") if entry))


def asset_paths(root=ROOT):
    """Checkout-relative paths of every model and fixture named in the asset manifest."""
    try:
        entries = json.loads((Path(root) / ASSET_MANIFEST).read_text())["assets"]
        names = [entry["name"] for entry in entries]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ManifestError("asset_manifest_invalid", f"{ASSET_MANIFEST} is unreadable: {error}") from None
    if any(type(name) is not str or Path(name).name != name or name in (".", "..") for name in names):
        raise ManifestError("asset_manifest_invalid", f"{ASSET_MANIFEST} has an invalid asset name")
    return sorted(f"{ASSET_DIRECTORY}/{name}" for name in names)


def os_release():
    """The distribution's os-release ID, version and build (SteamOS on Frame); None where absent."""
    try:
        info = platform.freedesktop_os_release()
    except OSError:
        info = {}
    return {key.lower(): info.get(key) for key in ("ID", "VERSION_ID", "BUILD_ID")}


def _package_description(text):
    """{FIELD: first value} of a pacman ``desc`` file: ``%FIELD%`` lines, each followed by its values."""
    fields, current = {}, None
    for line in text.splitlines():
        if len(line) > 2 and line[0] == line[-1] == "%":
            current = line[1:-1]
        elif current is not None and line and current not in fields:
            fields[current] = line
    return fields


def pacman_local_databases(config=PACMAN_CONFIG):
    """The local package databases to try, in order: ``DBPath`` from ``config`` when it names an absolute
    path, the Arch default and the SteamOS 3 path, each as ``<root>/local``, without duplicates."""
    roots = []
    try:
        text = Path(config).read_bytes()[:MAX_PACKAGE_DESCRIPTION_BYTES].decode("utf-8", "replace")
    except OSError:
        text = ""
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line.startswith("DBPath") and "=" in line:
            value = line.split("=", 1)[1].strip()
            if value.startswith("/"):
                roots.append(Path(value))
    roots += [PACMAN_DEFAULT_DATABASE_ROOT, PACMAN_HOLO_DATABASE_ROOT]
    return tuple(dict.fromkeys(root / "local" for root in roots))


def pacman_query(name, timeout=PACMAN_QUERY_TIMEOUT_SECONDS):
    """Read-only ``pacman -Q <name>``: ``(version, None)`` or ``(None, reason)``. Nothing is installed or changed."""
    try:
        completed = subprocess.run(["pacman", "-Q", name], capture_output=True, text=True, timeout=timeout,
                                   stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        return None, "pacman_unavailable"
    except (OSError, subprocess.TimeoutExpired):
        return None, "pacman_query_failed"
    if completed.returncode != 0:
        return None, "package_not_installed"
    parts = completed.stdout.split()
    if len(parts) != 2 or parts[0] != name or not _version_valid(parts[1]):
        return None, "pacman_query_invalid"
    return parts[1], None


def _version_valid(version):
    return (type(version) is str and 1 <= len(version) <= MAX_PACKAGE_VERSION_CHARS and
            version.isprintable() and " " not in version)


def _read_local_database(database, name):
    """``(status, version, reason)`` for one local database: status ``read``, ``absent``, ``permission_denied``
    or ``unreadable``; with ``read``, the version or the reason it is unknown."""
    try:
        entries = sorted(entry for entry in database.iterdir() if entry.name.startswith(name + "-"))
    except PermissionError:
        return "permission_denied", None, None
    except (FileNotFoundError, NotADirectoryError):
        return "absent", None, None
    except OSError:
        return "unreadable", None, None
    versions, unreadable = [], False
    for entry in entries:
        try:
            text = (entry / "desc").read_bytes()[:MAX_PACKAGE_DESCRIPTION_BYTES].decode("utf-8", "replace")
        except OSError:
            unreadable = True
            continue
        fields = _package_description(text)
        if fields.get("NAME") == name:
            versions.append(fields.get("VERSION"))
    if not versions:
        return "read", None, "package_description_unreadable" if unreadable else "package_not_installed"
    if len(versions) > 1:
        return "read", None, "package_ambiguous"
    if not _version_valid(versions[0]):
        return "read", None, "package_version_invalid"
    return "read", versions[0], None


def steamvr_package(name=STEAMVR_PACKAGE, databases=None, query=None):
    """The SteamVR package's installed version, best-effort and read-only.

    Returns ``{"name", "source", "database", "databases", "version", "reason", "query"}``.
    Each candidate local database (``pacman_local_databases()``) is tried in
    order by file reads only; ``databases`` records each path's status and
    ``database`` the one that answered, with ``source`` ``pacman_local_database``.
    Only when no database could be read does a read-only ``pacman -Q`` run
    (``query``, default ``pacman_query``), and then ``source`` is ``pacman_query``;
    ``query`` records why that fallback gave nothing. ``version`` is None, with
    ``reason``, where neither source answers: ``package_database_permission_denied``
    when a database exists but this user may not read it (the A4 preparation's
    case), ``package_database_unavailable`` when none exists, or the readable
    database's own reason (not installed, ambiguous, unreadable or invalid
    description).
    """
    databases = pacman_local_databases() if databases is None else tuple(Path(entry) for entry in databases)
    query = pacman_query if query is None else query
    record = {"name": name, "source": None, "database": None, "databases": {}, "version": None, "reason": None,
              "query": None}
    first_read = None
    for database in databases:
        status, version, reason = _read_local_database(database, name)
        record["databases"][str(database)] = status
        if status == "read":
            if version is not None:
                return dict(record, source="pacman_local_database", database=str(database), version=version)
            if first_read is None:
                first_read = (str(database), reason)
    if first_read is not None:
        return dict(record, source="pacman_local_database", database=first_read[0], reason=first_read[1])
    denied = any(status == "permission_denied" for status in record["databases"].values())
    record["reason"] = "package_database_permission_denied" if denied else "package_database_unavailable"
    version, query_reason = query(name)
    if version is not None:
        return dict(record, source="pacman_query", version=version, reason=None)
    return dict(record, query=query_reason)


def package_versions(packages=CHECKPOINT_PACKAGES):
    """{distribution: installed version or None}."""
    versions = {}
    for distribution in sorted(set(packages.values())):
        try:
            versions[distribution] = metadata.version(distribution)
        except metadata.PackageNotFoundError:
            versions[distribution] = None
    return versions


def observe(root=ROOT, packages=CHECKPOINT_PACKAGES):
    """The manifest of the checkout and interpreter as they are now, plus its dirty entries; writes nothing."""
    root = Path(root)
    commit, changes = git_state(root)
    package = sorted(path.relative_to(root).as_posix() for path in (root / "sightcircuit").rglob("*.py"))
    agent = [path for path in package if path.startswith(AGENT_PACKAGE + "/")]
    modules = [path for path in package if path not in agent]
    groups = {"agent": sorted([*agent, *AGENT_FILES]), "assets": asset_paths(root), "bridge": [BRIDGE],
              "modules": modules, "native_sources": NATIVE_SOURCES, "scripts": CHECKPOINT_SCRIPTS}
    current = {"schema_version": SCHEMA_VERSION, "report_kind": REPORT_KIND, "scope": SCOPE,
               "git": {"commit": commit, "dirty": bool(changes)},
               "python": {"implementation": platform.python_implementation(), "version": platform.python_version()},
               "platform": {"machine": platform.machine(), "system": platform.system(),
                            "kernel_release": platform.release(), "os_release": os_release(),
                            "steamvr_package": steamvr_package()},
               "packages": package_versions(packages)}
    current.update({group: {path: _digest(root / path) for path in paths} for group, paths in groups.items()})
    return current, changes


def _difference(field, reason, expected, actual, **extra):
    return {"field": field, "reason": reason, "expected": expected, "actual": actual, **extra}


def compare(expected, actual, prefix=""):
    """Every field that differs between two manifests; values must match in type and value."""
    found = []
    for key in sorted(set(expected) | set(actual)):
        field = prefix + key
        if key not in actual:
            found.append(_difference(field, "missing", expected[key], None))
        elif key not in expected:
            found.append(_difference(field, "unexpected", None, actual[key]))
        elif isinstance(expected[key], dict) and isinstance(actual[key], dict):
            found += compare(expected[key], actual[key], field + ".")
        elif type(expected[key]) is not type(actual[key]) or expected[key] != actual[key]:
            found.append(_difference(field, "changed", expected[key], actual[key]))
    return found


def problems(current, changes=()):
    """What makes the current state unfit for a run, whatever any manifest records."""
    found = []
    if current["git"]["dirty"] is not False:
        found.append(_difference("git.dirty", "dirty_tree", False, True, path_count=len(changes),
                                 paths=list(changes[:MAX_REPORTED_PATHS])))
    for group in FILE_GROUPS:
        found += [_difference(f"{group}.{path}", "file_missing", None, None)
                  for path, digest in current[group].items() if digest is None]
    found += [_difference(f"packages.{name}", "package_missing", None, None)
              for name, version in current["packages"].items() if version is None]
    return found


def differences(recorded, current, changes=()):
    """Every difference from ``recorded`` plus every problem of the current state, one per field."""
    found = compare(recorded, current)
    fields = {item["field"]: item for item in found}
    for problem in problems(current, changes):
        if problem["field"] not in fields:
            found.append(problem)
        elif "paths" in problem:
            fields[problem["field"]].update(path_count=problem["path_count"], paths=problem["paths"])
    return sorted(found, key=lambda item: (item["field"], item["reason"]))


def encode(data):
    """Deterministic JSON: sorted keys, two-space indent, trailing newline."""
    return (json.dumps(data, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def _would_dirty(root, path):
    """True when ``path`` lies inside the checkout where git would see it as untracked."""
    try:
        relative = Path(path).resolve().relative_to(Path(root).resolve())
    except ValueError:
        return False
    return _git(root, "check-ignore", "-q", "--", relative.as_posix()).returncode != 0


def _create(path, data):
    """Publish ``data`` at a new ``path``: never replaces anything, never leaves a partial file."""
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".part", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary, path, follow_symlinks=False)
    finally:
        os.unlink(temporary)


def write_manifest(path, root=ROOT, allow_dirty=False, packages=CHECKPOINT_PACKAGES):
    """Pin the current state to a new file; return the write result.

    Refuses an existing path, a path that would dirty the checkout and, unless
    ``allow_dirty`` (recorded as ``dirty: true``, which never verifies), a dirty
    tree. Missing files and packages are recorded as null and returned as problems.
    """
    path = Path(path)
    if os.path.lexists(path):
        raise ManifestError("manifest_exists", f"{path} already exists; --write never overwrites")
    if not path.parent.is_dir():
        raise ManifestError("manifest_directory_missing", f"{path.parent} is not an existing directory")
    current, changes = observe(root, packages)
    if changes and not allow_dirty:
        raise ManifestError("dirty_tree", f"the checkout has {len(changes)} changed or untracked paths: " +
                            ", ".join(changes[:MAX_REPORTED_PATHS]))
    if _would_dirty(root, path):
        raise ManifestError("manifest_would_dirty_tree",
                            f"{path} is inside the checkout and not ignored; it would dirty the tree")
    data = encode(current)
    try:
        _create(path, data)
    except FileExistsError:
        raise ManifestError("manifest_exists", f"{path} already exists; --write never overwrites") from None
    return {"status": "written", "manifest": str(path), "manifest_sha256": hashlib.sha256(data).hexdigest(),
            "commit": current["git"]["commit"], "dirty": current["git"]["dirty"],
            "problems": problems(current, changes), "scope": SCOPE}


def _reject_constant(name):
    raise ValueError(f"manifest contains the non-finite number {name}")


def _finite_float(text):
    """A JSON number as a float; one that overflows to infinity is rejected, as NaN and Infinity are."""
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(f"manifest contains the non-finite number {text[:40]}")
    return value


def _check_depth(value):
    """Raise ValueError when containers nest deeper than MAX_MANIFEST_DEPTH; iterative, so it never recurses."""
    pending = [(value, 1)]
    while pending:
        item, depth = pending.pop()
        if isinstance(item, (dict, list)):
            if depth > MAX_MANIFEST_DEPTH:
                raise ValueError(f"manifest nests deeper than {MAX_MANIFEST_DEPTH} levels")
            pending += [(child, depth + 1) for child in (item.values() if isinstance(item, dict) else item)]


def _read_manifest(path):
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"{path} is not a readable file")
    with path.open("rb") as source:
        data = source.read(MAX_MANIFEST_BYTES + 1)
    if len(data) > MAX_MANIFEST_BYTES:
        raise ValueError(f"manifest exceeds {MAX_MANIFEST_BYTES} bytes")
    return data


def verify_manifest(path, root=ROOT, packages=CHECKPOINT_PACKAGES):
    """Compare a manifest with the current state; read-only and never raises.

    The report's ``exit_code`` is 0 only when nothing differs, the tree is clean
    and every pinned file and package is present.
    """
    report = {"report_kind": VERIFICATION_KIND, "status": "error", "exit_code": 1, "manifest": str(path),
              "manifest_sha256": None, "commit": None, "differences": [], "scope": SCOPE}
    try:
        data = _read_manifest(path)
        report["manifest_sha256"] = hashlib.sha256(data).hexdigest()
        recorded = json.loads(data, parse_constant=_reject_constant, parse_float=_finite_float)
        if not isinstance(recorded, dict):
            raise ValueError("manifest is not a JSON object")
        _check_depth(recorded)  # The report repeats recorded values; keep them encodable.
    except (OSError, ValueError, RecursionError) as error:
        report["differences"] = [_difference("manifest", "unreadable_manifest", None, str(error)[:200])]
        return report
    try:
        current, changes = observe(root, packages)
    except (OSError, ManifestError) as error:
        reason = getattr(error, "reason", "checkout_unreadable")
        report["differences"] = [_difference("checkout", reason, None, str(error)[:200])]
        return report
    found = differences(recorded, current, changes)
    report.update(status="differs" if found else "verified", exit_code=1 if found else 0,
                  commit=current["git"]["commit"], differences=found)
    return report
