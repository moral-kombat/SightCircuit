"""Prepare an inactive layer bundle and restore exact files; never restart VR."""
import argparse
import base64
import hashlib
import fcntl
import json
import os
from pathlib import Path
import stat
import sys
import uuid
try:
    from .capture_session import process_start, private_directory
except ImportError:
    from capture_session import process_start, private_directory

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "artifacts/capture-provider-reference/reference-libVkLayer_mrhand.so"
LIBRARY = ".local/share/framejacker/capture/libframejacker_capture.so"
MANIFEST = ".local/share/vulkan/implicit_layer.d/framejacker_capture.json"
TARGETS = (LIBRARY, MANIFEST)
MAX_BYTES = 32 * 1024 * 1024


def directory(home, relative="", create=False):
    """Resolve each component through directory FDs, refusing symlink parents."""
    fd = os.open(home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        for component in Path(relative).parts:
            if component in (".", "..", "/"):
                raise ValueError("invalid_configuration_component")
            if create:
                try:
                    os.mkdir(component, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        info = os.fstat(fd)
        if info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise ValueError("configuration_directory_not_owned")
        return fd
    except BaseException:
        os.close(fd)
        raise


def describe(data, mode):
    return {"sha256": hashlib.sha256(data).hexdigest(), "mode": mode,
            "base64": base64.b64encode(data).decode()}


def read_entry(home, relative):
    try:
        parent = directory(home, str(Path(relative).parent))
    except FileNotFoundError:
        return None
    try:
        try:
            fd = os.open(Path(relative).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
        except FileNotFoundError:
            return None
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or
                    info.st_nlink != 1 or info.st_size > MAX_BYTES or info.st_mode & 0o6022):
                raise ValueError("configuration_file_not_owned_regular")
            data = bytearray()
            while len(data) <= MAX_BYTES:
                chunk = os.read(fd, min(65536, MAX_BYTES + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
            after = os.fstat(fd)
            if (len(data) != info.st_size or len(data) > MAX_BYTES or
                    (after.st_mtime_ns, after.st_ctime_ns, after.st_size) !=
                    (info.st_mtime_ns, info.st_ctime_ns, info.st_size)):
                raise ValueError("configuration_file_changed")
            return describe(data, stat.S_IMODE(info.st_mode))
        finally:
            os.close(fd)
    finally:
        os.close(parent)


def payload(entry):
    if (not isinstance(entry, dict) or type(entry.get("mode")) is not int or
            entry["mode"] & ~0o777 or entry["mode"] & 0o022):
        raise ValueError("invalid_configuration_entry")
    data = base64.b64decode(entry["base64"], validate=True)
    if len(data) > MAX_BYTES or hashlib.sha256(data).hexdigest() != entry["sha256"]:
        raise ValueError("configuration_checksum_mismatch")
    return data


def write_entry(home, relative, entry):
    parent = directory(home, str(Path(relative).parent), create=entry is not None)
    name = Path(relative).name
    temporary = ".framejacker-" + uuid.uuid4().hex
    try:
        if entry is None:
            os.unlink(name, dir_fd=parent)
        else:
            data = payload(entry)
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600, dir_fd=parent)
            try:
                with os.fdopen(fd, "wb", closefd=False) as stream:
                    stream.write(data)
                    stream.flush()
                os.fchmod(fd, entry["mode"])
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
    finally:
        try:
            os.unlink(temporary, dir_fd=parent)
        except FileNotFoundError:
            pass
        os.close(parent)


def prepare(home, source, destination):
    home = Path(home).absolute()
    # Read a real build artifact without loading it; no runtime configuration changes.
    source = Path(source).absolute()
    library = read_entry(source.parent, source.name)
    if library is None or payload(library)[:4] != b"\x7fELF":
        raise ValueError("provider_library_not_built")
    before = {name: read_entry(home, name) for name in TARGETS}
    manifest = {"file_format_version": "1.2.0", "layer": {
        "name": "VK_LAYER_FRAMEJACKER_capture", "type": "GLOBAL", "api_version": "1.3.0",
        "implementation_version": "3", "description": "Framejacker metadata-only camera buffer registry",
        "library_path": str(home / LIBRARY),
        "enable_environment": {"FRAMEJACKER_CAPTURE_LAYER": "1"},
        "disable_environment": {"FRAMEJACKER_DISABLE_CAPTURE_LAYER": "1"},
        "functions": {"vkGetInstanceProcAddr": "mrhand_GetInstanceProcAddr",
                      "vkGetDeviceProcAddr": "mrhand_GetDeviceProcAddr",
                      "vkNegotiateLoaderLayerInterfaceVersion": "mrhand_NegotiateLoaderLayerInterfaceVersion"}}}
    after = {LIBRARY: describe(payload(library), 0o600),
             MANIFEST: describe((json.dumps(manifest, indent=2) + "\n").encode(), 0o600)}
    plan = {"schema_version": 1, "home": str(home), "before": before, "after": after,
            "provider_protocol_version": 3, "image_access_enabled": False,
            "activation_performed": False, "runtime_restart_performed": False,
            "restore_requires_controlled_restart_if_loaded": True}
    destination = Path(destination)
    destination.mkdir(mode=0o700)  # Exclusive: retain every previous bundle.
    for name, data in (("plan.json", (json.dumps(plan, indent=2) + "\n").encode()),
                       ("framejacker_capture.json", payload(after[MANIFEST])),
                       ("libframejacker_capture.so", payload(after[LIBRARY]))):
        fd = os.open(destination / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
    return plan


def load_plan(bundle):
    entry = read_entry(Path(bundle).absolute(), "plan.json")
    if entry is None:
        raise ValueError("configuration_plan_missing")
    plan = json.loads(payload(entry))
    if (not isinstance(plan, dict) or plan.get("schema_version") != 1 or
            not isinstance(plan.get("home"), str) or not Path(plan["home"]).is_absolute() or
            set(plan.get("before", {})) != set(TARGETS) or set(plan.get("after", {})) != set(TARGETS)):
        raise ValueError("invalid_configuration_plan")
    for entries in (plan["before"], plan["after"]):
        for entry in entries.values():
            if entry is not None:
                payload(entry)
    return plan


def restore(bundle, *, expected_home=None):
    plan = load_plan(bundle)
    home = Path(plan["home"])
    if home != Path(expected_home if expected_home is not None else Path.home()).absolute():
        raise ValueError("configuration_home_mismatch")
    # Check every target before changing any. Preserve later edits and unknown files.
    current = {name: read_entry(home, name) for name in TARGETS}
    for name in TARGETS:
        if current[name] != plan["before"][name] and current[name] != plan["after"][name]:
            raise ValueError("configuration_target_changed:" + name)
    for name in reversed(TARGETS):  # Remove/restore the manifest before the library.
        if current[name] != plan["before"][name]:
            # Recheck immediately before mutation to detect ordinary concurrent edits.
            if read_entry(home, name) != current[name]:
                raise ValueError("configuration_target_changed:" + name)
            write_entry(home, name, plan["before"][name])
    return {"status": "restored", "runtime_restart_performed": False,
            "loaded_layer_unloaded": False, "controlled_restart_required_if_loaded": True}


def recover_provider(runtime):
    """Remove a crashed provider's recorded socket only; never terminate a process."""
    parent = Path(runtime) / "framejacker-camera-provider"
    directory_fd = private_directory(parent)
    lease = None
    try:
        lease = os.open("provider.lock", os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory_fd)
        info = os.fstat(lease)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or
                info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600 or not 0 < info.st_size <= 4096):
            raise ValueError("invalid_provider_lease")
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = json.loads(os.pread(lease, 4096, 0))
        if (not isinstance(state, dict) or state.get("schema_version") != 1 or
                any(type(state.get(key)) is not int or state[key] < 1 for key in ("pid", "start", "device", "inode"))):
            raise ValueError("invalid_provider_lease")
        if process_start(state["pid"]) == state["start"]:
            raise ValueError("provider_process_still_alive")
        endpoint = os.stat("camera.sock", dir_fd=directory_fd, follow_symlinks=False)
        if (not stat.S_ISSOCK(endpoint.st_mode) or endpoint.st_uid != os.geteuid() or
                stat.S_IMODE(endpoint.st_mode) != 0o600 or endpoint.st_nlink != 1 or
                (endpoint.st_dev, endpoint.st_ino) != (state["device"], state["inode"])):
            raise ValueError("provider_endpoint_replaced")
        os.unlink("camera.sock", dir_fd=directory_fd)
        os.ftruncate(lease, 0)
        os.fsync(lease)
    finally:
        if lease is not None:
            os.close(lease)
        os.close(directory_fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--prepare", type=Path, metavar="NEW_BUNDLE_DIRECTORY")
    mode.add_argument("--restore", type=Path, metavar="BUNDLE_DIRECTORY")
    mode.add_argument("--recover-provider-runtime", action="store_true", help="Recover only the dead provider socket recorded in the current runtime directory")
    args = parser.parse_args(argv)
    report = {"report_kind": "capture_layer_configuration", "schema_version": 1,
              "status": "not_run", "exit_code": 3, "activation_performed": False,
              "runtime_restart_performed": False}
    try:
        if args.prepare:
            prepare(Path.home(), SOURCE, args.prepare)
            report.update(status="prepared_inactive_bundle", exit_code=0, bundle=str(args.prepare.absolute()))
        elif args.restore:
            report.update(restore(args.restore), exit_code=0)
        elif args.recover_provider_runtime:
            runtime = os.environ.get("XDG_RUNTIME_DIR")
            if not runtime or not Path(runtime).is_absolute():
                raise ValueError("runtime_directory_missing")
            recover_provider(runtime)
            report.update(status="provider_endpoint_recovered", exit_code=0)
    except (OSError, ValueError, TypeError, KeyError) as error:
        report.update(status="refused", exit_code=1, reason=str(error))
    print(json.dumps(report, indent=2))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
