"""Explicit image outputs: reserve, write after sources close, publish without overwriting.

A launcher reserves each requested output (``reserve``) before its worker
starts; the worker writes the reservation (``write``) after its source has
closed; the launcher publishes every output or none (``publish_all``, which
``withdraw``s a partial publication, including one a signal interrupts) and
always ``discard``s its temporary files. ``sources.mirror.launch`` runs that sequence for the mirror
commands. ``publish_new`` is the one-step form for a caller that already holds
the finished bytes (``shm_capture_check.py``, ``hud_validation_report.py``).
"""
import os
from pathlib import Path
import tempfile

from ..assets import checksum
from ..errors import OutputError


def reserve(target, prefix, suffix=".png", *, kind="preview"):
    """Create an owned temporary file beside a new ``target``; return its reservation.

    The reservation is a JSON-serializable dict so it can be passed to a spawned
    worker. Its inode identity guards later writes and cleanup against replacement.
    An unsuitable ``target`` raises ``OutputError`` ``invalid_<kind>_path``.
    """
    target = Path(target).expanduser().absolute()
    if target.suffix.lower() != suffix or not target.parent.is_dir() or target.exists() or target.is_symlink():
        raise OutputError(f"invalid_{kind}_path", f"--{kind} needs a new {suffix} path in an existing directory")
    fd, name = tempfile.mkstemp(prefix=prefix, suffix=suffix + ".part", dir=target.parent)
    info = os.fstat(fd)
    os.close(fd)
    return {"path": name, "identity": [info.st_dev, info.st_ino], "target": str(target)}


def write(reservation, data):
    fd = os.open(reservation["path"], os.O_WRONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (info.st_dev, info.st_ino) != tuple(reservation["identity"]):
            raise OutputError("preview_changed", "Owned preview temporary file was replaced")
        with os.fdopen(fd, "wb") as output:
            fd = None
            output.write(data)
    finally:
        if fd is not None:
            os.close(fd)


def publish(reservation):
    """Hard-link publication is atomic and refuses to overwrite any target."""
    os.link(reservation["path"], reservation["target"], follow_symlinks=False)
    return reservation["target"]


def publish_all(reservations, report, *, digests=()):
    """Publish every reserved output (``{kind: reservation}``) or none, recording each in ``report``.

    Each needs ``<kind>_ready`` in the report, and one whose report gives a
    ``<kind>_sha256`` must still match it; a kind named in ``digests`` must give
    one. Otherwise ``<kind>_changed`` is raised and nothing is linked. When a
    later link fails, the outputs already linked are withdrawn and
    ``<kind>_publish_failed`` is raised; any other exception during the links,
    such as a ``guard.CANCELLATION``, also withdraws them and is re-raised. A
    published output adds ``<kind>_path`` and sets ``images_saved``.
    """
    if any(not report.get(f"{kind}_ready") for kind in reservations):
        raise OutputError("requested_output_missing", "A requested output was not produced")
    for kind, reservation in reservations.items():
        reported = f"{kind}_sha256" in report
        if not reported and kind in digests:
            raise OutputError(f"{kind}_changed", f"The {kind} has no reported SHA-256 to check")
        if reported and checksum(reservation["path"]) != report[f"{kind}_sha256"]:
            raise OutputError(f"{kind}_changed", f"The {kind} no longer matches its reported SHA-256")
    try:
        for kind, reservation in reservations.items():
            try:
                report[f"{kind}_path"] = publish(reservation)
            except OSError as error:
                raise OutputError(f"{kind}_publish_failed",
                                  f"--{kind} failed, so no output is published: {error}") from error
            report["images_saved"] = True
    except BaseException:
        withdraw(reservations, report)
        raise


def withdraw(reservations, report):
    """Unlink every target that is still its reserved file, recorded in ``report`` or not; clear the record.

    A signal can land after a link and before ``report`` records it, so the
    inode identity, not the report, decides; a target replaced since is kept.
    """
    for kind, reservation in reservations.items():
        target = Path(reservation["target"])
        try:
            info = target.lstat()
        except FileNotFoundError:
            info = None
        if info is not None and (info.st_dev, info.st_ino) == tuple(reservation["identity"]):
            target.unlink()
        report.pop(f"{kind}_path", None)
    report["images_saved"] = False


def discard(reservation):
    """Remove the temporary file only if it is still the one we created."""
    path = Path(reservation["path"])
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if (info.st_dev, info.st_ino) == tuple(reservation["identity"]):
        path.unlink()


def publish_new(target, data, prefix, suffix=".png"):
    """Write ``data`` to a new temporary file beside ``target`` and hard-link it into place; return ``target``.

    For a caller that already holds the finished bytes and has validated
    ``target``. The temporary file is written through the descriptor that
    created it and removed afterwards whether or not the link succeeded; every
    failure, including a ``target`` created meanwhile, raises ``OSError``.
    """
    fd, name = tempfile.mkstemp(prefix=prefix, suffix=suffix + ".part", dir=Path(target).parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
        return publish({"path": name, "target": target})
    finally:
        Path(name).unlink()
