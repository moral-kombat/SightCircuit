"""Report execution context without opening cameras or changing runtime settings."""
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
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


def main():
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
