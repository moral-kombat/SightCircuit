"""Report execution context without opening cameras or changing runtime settings."""
import argparse
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import stat
import subprocess
import sys


def os_release():
    selected = {}
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            key, separator, value = line.partition("=")
            if separator and key in ("ID", "ID_LIKE", "PRETTY_NAME", "VERSION_ID", "BUILD_ID"):
                fields = shlex.split(value)
                selected[key] = fields[0] if fields else ""
    except (OSError, ValueError):
        selected["readable"] = False
    return selected


def video_nodes():
    root = Path("/sys/class/video4linux")
    try:
        nodes = sorted(root.iterdir())
    except OSError:
        return {"directory_readable": False, "nodes": []}
    result = []
    for node in nodes:
        entry = {"node": "/dev/" + node.name}
        try:
            entry["driver_name"] = (node / "name").read_text().strip()
        except OSError:
            entry["name_readable"] = False
        result.append(entry)
    return {"directory_readable": True, "nodes": result}


def steamvr_service():
    if shutil.which("systemctl") is None:
        return {"query": "unavailable", "reason": "systemctl_not_on_path"}
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", "steamvr.service"],
            capture_output=True, text=True, timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"query": "unavailable", "reason": "user_service_query_failed"}
    state = result.stdout.strip()
    if state not in ("active", "inactive", "activating", "deactivating", "failed", "unknown"):
        state = "unavailable"
    return {"query": "completed", "state": state, "exit_code": result.returncode}


def path_metadata(path):
    """Inspect a known path without reading file contents or opening a socket."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {"path": str(path), "present": False}
    except OSError:
        return {"path": str(path), "present": None, "query": "unreadable"}
    kind = ("symlink" if stat.S_ISLNK(info.st_mode) else
            "regular_file" if stat.S_ISREG(info.st_mode) else
            "directory" if stat.S_ISDIR(info.st_mode) else
            "socket" if stat.S_ISSOCK(info.st_mode) else "other")
    return {"path": str(path), "present": True, "kind": kind,
            "size_bytes": info.st_size, "owned_by_current_user": info.st_uid == os.getuid()}


def command_metadata(arguments):
    executable = shutil.which(arguments[0])
    if executable is None:
        return {"query": "unavailable", "reason": "tool_not_on_path"}
    try:
        result = subprocess.run([executable, *arguments[1:]], capture_output=True,
                                text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return {"query": "unavailable", "reason": "metadata_query_failed"}
    lines = result.stdout.strip().splitlines()
    return {"query": "completed", "exit_code": result.returncode,
            "value": lines[0][:300] if result.returncode == 0 and lines else None}


def capture_prerequisites():
    """Build/configuration metadata only; no Vulkan loader or camera API calls."""
    pkg_version = command_metadata(["pkg-config", "--modversion", "vulkan"])
    includedir = command_metadata(["pkg-config", "--variable=includedir", "vulkan"])
    include_roots = [Path("/usr/include"), Path("/usr/local/include")]
    if includedir.get("value") and Path(includedir["value"]).is_absolute():
        include_roots.append(Path(includedir["value"]))
    headers = sorted({root / "vulkan" / name for root in include_roots
                      for name in ("vulkan.h", "vk_layer.h")})
    library_roots = ("/usr/lib", "/usr/lib64", "/lib",
                     "/usr/lib/aarch64-linux-gnu", "/lib/aarch64-linux-gnu",
                     "/usr/lib/x86_64-linux-gnu", "/lib/x86_64-linux-gnu")
    libraries = [path_metadata(Path(root) / name) for root in library_roots
                 for name in ("libvulkan.so", "libvulkan.so.1")]
    home = Path.home()
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    return {
        "report_kind": "capture_build_prerequisites", "schema_version": 1,
        "machine": platform.machine(), "kernel": platform.release(), "os": os_release(),
        "compiler": command_metadata(["g++", "--version"]),
        "vulkan_pkg_config": pkg_version,
        "vulkan_header_candidates": [path_metadata(path) for path in headers],
        "vulkan_library_candidates": [item for item in libraries if item["present"] is not False],
        "vulkan_library_search": "Known Linux library directories only; no loader initialization",
        "community_capture_paths": {
            "layer_manifest": path_metadata(home / ".local/share/vulkan/implicit_layer.d/mrhand.json"),
            "layer_library": path_metadata(home / ".local/share/mrhand/libVkLayer_mrhand.so"),
            "enable_marker": path_metadata(home / ".config/mrhand/enable-capture"),
            "runtime_socket": (path_metadata(Path(runtime) / "mrhand/cam.sock") if runtime
                               else {"query": "unavailable", "reason": "XDG_RUNTIME_DIR_not_set"}),
            "luma_shared_memory": path_metadata(Path("/dev/shm/mrhand-pt")),
        },
        "runtime_initialized": False, "capture_started": False, "files_changed": False,
        "notes": [
            "Only compiler/pkg-config metadata commands and known-path metadata were queried.",
            "No Vulkan library was loaded, camera opened, socket connected, image read or layer enabled.",
            "File presence does not prove ABI compatibility, a successful build or working capture.",
            "Path metadata uses lstat; symlink targets and other installation locations are not verified.",
            "Missing development files are reported; no package installation or runtime restart is attempted.",
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-prerequisites", action="store_true",
                        help="Report native build files and existing capture configuration without starting capture")
    args = parser.parse_args(argv)
    if args.capture_prerequisites:
        print(json.dumps(capture_prerequisites(), indent=2))
        return
    tools = ("git", "python3", "uv", "cmake", "ninja", "g++", "pkg-config",
             "vulkaninfo", "vrcmd", "vrpathreg", "systemctl")
    try:
        render_nodes = sorted(str(path) for path in Path("/dev/dri").glob("renderD*"))
    except OSError:
        render_nodes = []
    try:
        available_bytes = shutil.disk_usage(Path(__file__).resolve().parent).free
    except OSError:
        available_bytes = None
    report = {
        "report_kind": "device_inventory",
        "machine": platform.machine(),
        "kernel": platform.release(),
        "os": os_release(),
        "python": sys.version.split()[0],
        "logical_cpus": os.cpu_count(),
        "repository_filesystem_free_bytes": available_bytes,
        "tools_on_path": {name: shutil.which(name) is not None for name in tools},
        "session_variables_present": {
            name: bool(os.environ.get(name))
            for name in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR")
        },
        "steamvr_user_service": steamvr_service(),
        "video_devices": video_nodes(),
        "render_nodes_present": render_nodes,
        "capture_validation": "not_performed",
        "notes": [
            "No camera device was opened and no settings or services were changed.",
            "Device nodes and tool presence do not demonstrate usable camera or GPU access.",
            "Unavailable services may reflect an isolated or non-login terminal session.",
        ],
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
