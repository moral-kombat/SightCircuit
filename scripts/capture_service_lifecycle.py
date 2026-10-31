"""Read-only cgroup observations and bounded service transitions; no VR APIs."""
import os
from pathlib import Path
import re
import subprocess
import time


FIELDS = ("ActiveState", "SubState", "MainPID", "ControlGroup", "Job", "Result", "InvocationID")


def small_text(path):
    with path.open("rb") as stream:
        data = stream.read(65537)
    if len(data) > 65536:
        raise ValueError("service_observation_size_limit")
    return data.decode("utf-8")


def observe(service, group, *, cgroup_root=Path("/sys/fs/cgroup"), proc_root=Path("/proc")):
    """Observe the original unified cgroup, including its descendants.

    A live compositor process is liveness evidence, not rendering readiness.
    Missing/unreadable metadata must never become proof of an empty group.
    """
    if (not isinstance(group, str) or not group.startswith("/") or group == "/" or
            any(part in (".", "..") for part in group.split("/"))):
        raise ValueError("service_control_group_invalid")
    if service.get("ControlGroup") not in ("", group):
        raise ValueError("service_control_group_changed")
    if not (cgroup_root / "cgroup.controllers").is_file():
        raise ValueError("unified_cgroup_unavailable")
    result = {"service": {key: service[key] for key in FIELDS if key in service},
              "group": group, "populated": None, "compositors": []}
    directory = cgroup_root / group.lstrip("/")
    try:
        events = small_text(directory / "cgroup.events")
    except FileNotFoundError:
        # A removed cgroup is empty. A present cgroup with missing events is unknown.
        if directory.exists():
            raise ValueError("service_cgroup_events_missing") from None
        result["populated"] = False
        return result
    event_fields = dict(line.split() for line in events.splitlines())
    if event_fields.get("populated") not in ("0", "1"):
        raise ValueError("service_cgroup_events_invalid")
    result["populated"] = event_fields["populated"] == "1"
    if not result["populated"]:
        return result
    directories, pids, pending = 0, set(), [directory]
    while pending:
        current = pending.pop()
        directories += 1
        if directories > 128:
            raise ValueError("service_cgroup_directory_limit")
        entries = small_text(current / "cgroup.procs").split()
        if any(not pid.isdecimal() or int(pid) < 1 for pid in entries):
            raise ValueError("service_cgroup_pids_invalid")
        pids.update(int(pid) for pid in entries)
        if len(pids) > 2048:
            raise ValueError("service_cgroup_process_limit")
        with os.scandir(current) as children:
            for index, child in enumerate(children):
                if index >= 4096:
                    raise ValueError("service_cgroup_entry_limit")
                if child.is_dir(follow_symlinks=False):
                    if directories + len(pending) >= 128:
                        raise ValueError("service_cgroup_directory_limit")
                    pending.append(Path(child.path))
    for pid in sorted(pids):
        process = proc_root / str(pid)
        try:
            before = small_text(process / "stat")
            fields = before[before.rfind(")") + 2:].split()
            if len(fields) < 20 or fields[0] in ("Z", "X", "x", "T", "t"):
                continue
            if process.stat().st_uid != os.geteuid():
                continue
            executable = os.readlink(process / "exe")
            groups = small_text(process / "cgroup").splitlines()
            after = small_text(process / "stat").rsplit(")", 1)[-1].split()
            if (Path(executable).name == "vrcompositor" and
                    any(line.startswith("0::") and
                        (line[3:] == group or line[3:].startswith(group + "/")) for line in groups) and
                    len(after) >= 20 and after[0] not in ("Z", "X", "x", "T", "t") and
                    after[19] == fields[19] and int(fields[19]) > 0):
                result["compositors"].append({"pid": pid, "start_ticks": int(fields[19])})
        except (FileNotFoundError, ProcessLookupError):
            continue  # A disappearing process is not a live compositor.
    return result


def stopped(observation):
    service = observation["service"]
    return (service.get("ActiveState") in ("inactive", "failed") and
            service.get("MainPID") == "0" and service.get("Job") == "0" and
            observation.get("populated") is False)


def running(observation):
    service = observation["service"]
    return (service.get("ActiveState") == "active" and service.get("SubState") == "running" and
            service.get("Result") == "success" and service.get("MainPID", "").isdecimal() and
            int(service["MainPID"]) > 0 and service.get("Job") == "0" and
            re.fullmatch(r"[0-9a-f]{32}", service.get("InvocationID", "")) is not None and
            observation.get("populated") is True and bool(observation.get("compositors")))


class Controller:
    """One command per transition, then observations within a shared deadline."""
    def __init__(self, manager, group, checkpoint=lambda _: None, *, budget=55,
                 clock=time.monotonic, pause=time.sleep):
        self.manager, self.group, self.checkpoint = manager, group, checkpoint
        self.clock, self.pause = clock, pause
        self.deadline = clock() + budget
        self.history = []
        self.last_observation = None

    def remaining(self):
        return max(0, self.deadline - self.clock())

    def timeout(self, maximum=5):
        if self.remaining() <= 0:
            raise ValueError("service_control_deadline")
        return min(maximum, self.remaining())

    def observe(self, end=None):
        timeout = self.timeout()
        if end is not None:
            timeout = min(timeout, end - self.clock())
        if timeout <= 0:
            raise ValueError("service_control_deadline")
        self.last_observation = self.manager.observe(self.group, timeout=timeout)
        if self.clock() >= self.deadline:
            raise ValueError("service_control_deadline")
        return self.last_observation

    def require_stopped(self):
        if not stopped(self.observe()):
            raise ValueError("steamvr_stop_unconfirmed")

    def confirm_running(self):
        first = self.observe()
        if not running(first):
            return False
        self.pause(min(0.1, self.remaining()))
        second = self.observe()
        return running(second) and first == second

    def move(self, action):
        entry = {"operation": action, "command_status": "requested", "confirmed": False}
        self.history.append(entry)
        self.checkpoint(self.history)  # Persist intent before starting a command.
        end = min(self.deadline, self.clock() + 25)
        try:
            if end <= self.clock():
                raise ValueError("service_control_deadline")
            self.manager.action(action, timeout=min(20, end - self.clock()))
            entry["command_status"] = "completed"
        except (ValueError, OSError, subprocess.SubprocessError) as error:
            entry.update(command_status="failed", command_reason=str(error))
            if hasattr(error, "details"):
                entry["command_error"] = error.details
        self.checkpoint(self.history)
        predicate = stopped if action == "stop" else running
        reason = "steamvr_stop_unconfirmed" if action == "stop" else "normal_runtime_unconfirmed"
        return self._wait(entry, predicate, end, reason)

    def wait_idle(self):
        entry = {"operation": "observe_existing_job", "command_status": "not_requested", "confirmed": False}
        self.history.append(entry)
        self.checkpoint(self.history)
        def idle(observation):
            service = observation["service"]
            return service.get("Job") == "0" and service.get("ActiveState") in ("active", "inactive", "failed")
        return self._wait(entry, idle, min(self.deadline, self.clock() + 25), "existing_service_job_unsettled")

    def _wait(self, entry, predicate, end, reason):
        previous = None
        try:
            while self.clock() < end:
                observation = self.observe(end=end)
                changed = observation != entry.get("last_observation")
                entry["last_observation"] = observation
                if changed:
                    self.checkpoint(self.history)
                if self.clock() >= end:
                    break
                service = observation["service"]
                identity = (service.get("InvocationID"), service.get("MainPID"),
                            tuple((item["pid"], item["start_ticks"]) for item in observation["compositors"]))
                if predicate(observation) and previous == identity:
                    entry["confirmed"] = True
                    break
                previous = identity if predicate(observation) else None
                self.pause(min(0.1, max(0, end - self.clock())))
        except (ValueError, OSError, subprocess.SubprocessError) as error:
            entry["observation_error"] = str(error)
        if not entry["confirmed"]:
            entry["reason"] = reason
        self.checkpoint(self.history)
        return entry
