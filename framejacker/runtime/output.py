"""Explicit image outputs: reserve, write after sources close, publish without overwriting."""
import os
from pathlib import Path
import tempfile

from ..errors import OutputError


def reserve(target, prefix, suffix=".png"):
    """Create an owned temporary file beside a new ``target``; return its reservation.

    The reservation is a JSON-serializable dict so it can be passed to a spawned
    worker. Its inode identity guards later writes and cleanup against replacement.
    """
    target = Path(target).expanduser().absolute()
    if target.suffix.lower() != suffix or not target.parent.is_dir() or target.exists() or target.is_symlink():
        raise OutputError("invalid_preview_path", f"Preview needs a new {suffix} path in an existing directory")
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


def discard(reservation):
    """Remove the temporary file only if it is still the one we created."""
    path = Path(reservation["path"])
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if (info.st_dev, info.st_ino) == tuple(reservation["identity"]):
        path.unlink()
