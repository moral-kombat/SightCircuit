"""Own a private capture run and recover only its recorded publication inode."""
import fcntl
import json
import os
from pathlib import Path
import stat
import tempfile


def private_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    info = os.fstat(fd)
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
        os.close(fd)
        raise ValueError("capture_directory_not_private")
    return fd


def process_start(pid):
    try:
        # comm may contain spaces and parentheses; starttime is field 22.
        text = Path(f"/proc/{pid}/stat").read_text()
        return int(text[text.rfind(")") + 2:].split()[19])
    except FileNotFoundError:
        return None


def identity(info):
    return {"device": info.st_dev, "inode": info.st_ino, "size": info.st_size}


class Session:
    def __init__(self, runtime):
        self.fd = self.lease = None
        root = private_directory(runtime)
        try:
            self.path = Path(tempfile.mkdtemp(prefix="sightcircuit-capture-", dir=runtime))
        finally:
            os.close(root)
        try:
            self.fd = private_directory(self.path)
            self.directory_identity = identity(os.fstat(self.fd))
            self.lease = os.open("lease.json", os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                                 0o600, dir_fd=self.fd)
            fcntl.flock(self.lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.state = {"schema_version": 1, "supervisor_pid": os.getpid(),
                          "supervisor_start": process_start(os.getpid()), "worker_pid": None,
                          "worker_start": None, "publication": None}
            self.save()
        except BaseException:
            self.close()
            raise

    def save(self):
        payload = json.dumps(self.state, sort_keys=True).encode()
        os.ftruncate(self.lease, 0)
        if os.pwrite(self.lease, payload, 0) != len(payload):
            raise OSError("capture_lease_short_write")
        os.fsync(self.lease)

    def worker(self, pid):
        self.state.update(worker_pid=pid, worker_start=process_start(pid))
        self.save()

    def publication(self, event):
        expected = {key: event.get(key) for key in ("device", "inode", "size")}
        if any(type(value) is not int or value <= 0 for value in expected.values()):
            raise ValueError("invalid_publication_identity")
        try:
            opened = os.open("frames.shm", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                             dir_fd=self.fd)
        except FileNotFoundError:
            # A short worker may have closed/unlinked before its event is read.
            # Retain the announced identity; cleanup never infers a replacement's.
            if self.state["publication"] is not None:
                raise ValueError("duplicate_publication_identity")
            self.state["publication"] = expected
            self.save()
            return
        try:
            info = os.fstat(opened)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or
                    info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600 or
                    info.st_size > 128 * 1024 * 1024 or identity(info) != expected):
                raise ValueError("publication_identity_mismatch")
            if self.state["publication"] is not None:
                raise ValueError("duplicate_publication_identity")
            self.state["publication"] = expected
            self.save()
        finally:
            os.close(opened)

    def cleanup(self):
        """Called only after the supervisor has reaped its direct worker."""
        named = os.stat(self.path, follow_symlinks=False)
        if (named.st_dev, named.st_ino) != (self.directory_identity["device"], self.directory_identity["inode"]):
            raise ValueError("session_directory_replaced")
        try:
            info = os.stat("frames.shm", dir_fd=self.fd, follow_symlinks=False)
        except FileNotFoundError:
            info = None
        if info is not None:
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or
                    info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600 or
                    identity(info) != self.state["publication"]):
                raise ValueError("publication_recovery_unconfirmed")
            os.unlink("frames.shm", dir_fd=self.fd)
        if set(os.listdir(self.fd)) != {"lease.json"}:
            raise ValueError("session_has_unowned_files")
        lease = os.stat("lease.json", dir_fd=self.fd, follow_symlinks=False)
        opened = os.fstat(self.lease)
        if (lease.st_dev, lease.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError("session_lease_replaced")
        os.unlink("lease.json", dir_fd=self.fd)
        os.rmdir(self.path)

    def close(self):
        for name in ("lease", "fd"):
            value = getattr(self, name, None)
            if value is not None:
                os.close(value)
                setattr(self, name, None)


def recover(path):
    """Explicit stale-session recovery: never signal or delete a living run."""
    session = Session.__new__(Session)
    session.path = Path(path).absolute()
    if not session.path.name.startswith("sightcircuit-capture-"):
        raise ValueError("invalid_session_path")
    session.fd = session.lease = None
    try:
        session.fd = private_directory(session.path)
        session.directory_identity = identity(os.fstat(session.fd))
        session.lease = os.open("lease.json", os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                                dir_fd=session.fd)
        info = os.fstat(session.lease)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or
                info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600 or not 0 < info.st_size <= 4096):
            raise ValueError("invalid_session_lease")
        fcntl.flock(session.lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        session.state = json.loads(os.pread(session.lease, 4096, 0))
        if not isinstance(session.state, dict) or session.state.get("schema_version") != 1:
            raise ValueError("invalid_session_schema")
        for prefix in ("supervisor", "worker"):
            pid, start = (session.state.get(prefix + key) for key in ("_pid", "_start"))
            if prefix == "supervisor" and pid is None:
                raise ValueError("invalid_session_process_identity")
            if pid is not None:
                if type(pid) is not int or pid < 2 or (start is not None and (type(start) is not int or start < 1)):
                    raise ValueError("invalid_session_process_identity")
                observed = process_start(pid)
                if observed is not None and (start is None or observed == start):
                    raise ValueError("session_process_still_alive")
        session.cleanup()
    finally:
        session.close()
