"""Bounded metadata-only SteamVR trial, with systemd-owned post-exit recovery."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import uuid

try:
    from . import capture_layer_config as config, capture_provider_check as metadata, capture_service_lifecycle as lifecycle
except ImportError:
    import capture_layer_config as config
    import capture_provider_check as metadata
    import capture_service_lifecycle as lifecycle

ROOT = config.ROOT
UNIT = "sightcircuit-metadata-trial.service"
SERVICE = "steamvr.service"
DROPIN = "systemd/user/steamvr.service.d/90-sightcircuit-metadata-trial.conf"
ENABLE = b"[Service]\nEnvironment=SIGHTCIRCUIT_CAPTURE_LAYER=1\n"
EXPECTED = {"LoadState": "loaded", "ActiveState": "active", "SubState": "running",
            "Type": "simple", "KillMode": "control-group", "TimeoutStopUSec": "20s",
            "FragmentPath": "/usr/lib/systemd/user/steamvr.service", "DropInPaths": ""}
PROPERTIES = (*EXPECTED, "MainPID", "ControlGroup", "Job", "Result", "InvocationID")
SERVICE_DETAIL_PROPERTIES = (
    "Id", "LoadState", "ActiveState", "SubState", "Result", "Type", "MainPID", "ControlGroup", "Job", "InvocationID",
    "Restart", "RestartUSec", "NRestarts", "RefuseManualStart", "RefuseManualStop",
    "Before", "After", "Requires", "Requisite", "Wants", "BindsTo", "PartOf", "Conflicts",
    "Upholds", "UpheldBy", "PropagatesStopTo", "StopPropagatedFrom", "StopWhenUnneeded",
    "JobTimeoutUSec", "JobRunningTimeoutUSec", "KillMode", "SendSIGKILL", "TimeoutStopUSec",
    "FragmentPath", "DropInPaths")
SERVICE_EXEC_PROPERTIES = ("ExecCondition", "ExecStartPre", "ExecStart", "ExecStartPost", "ExecStop", "ExecStopPost")
FLAG_NAMES = {"SIGHTCIRCUIT_CAPTURE_LAYER", "SIGHTCIRCUIT_DISABLE_CAPTURE_LAYER"}
RUNS = ROOT / "artifacts/metadata-trials"
ACTIVATION_BLOCKED_REASON = "service_control_failure_unresolved"


class ServiceCommandError(ValueError):
    def __init__(self, arguments, result):
        operation = "launch_unit" if arguments[0] == "systemd-run" else "utility"
        if arguments[:2] == ["systemctl", "--user"] and len(arguments) > 2:
            operation = arguments[2]
        timed_out = isinstance(result, subprocess.TimeoutExpired)
        self.details = {"program": arguments[0], "operation": operation,
                        "return_code": None if timed_out else result.returncode}
        if timed_out:
            self.details.update(timed_out=True, timeout_seconds=result.timeout)
        # Never retain stdout: show-environment/Environment queries can contain
        # secrets. Keep bounded stderr only for our exact non-environment actions.
        if (arguments == ["systemctl", "--user", "daemon-reload"] or
                arguments in (["systemctl", "--user", "start", SERVICE],
                              ["systemctl", "--user", "stop", SERVICE])):
            diagnostics = result.stderr or ""
            if isinstance(diagnostics, bytes):
                diagnostics = diagnostics.decode("utf-8", errors="replace")
            self.details["stderr"] = diagnostics[:4096]
        outcome = "timeout" if timed_out else str(result.returncode)
        super().__init__(f"service_command_failed:{arguments[0]}:{operation}:{outcome}")


def command(arguments, timeout=30):
    try:
        result = subprocess.run(arguments, stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise ServiceCommandError(arguments, error) from None
    if result.returncode:
        raise ServiceCommandError(arguments, result)
    if len(result.stdout) > 256 * 1024 or len(result.stderr) > 64 * 1024:
        raise ValueError("service_output_limit")
    return result.stdout


class Systemd:
    def show(self, timeout=5):
        text = command(["systemctl", "--user", "show", SERVICE,
                        "--all", *("--property=" + field for field in PROPERTIES)], timeout=timeout)
        result = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
        # systemctl serializes the null (0, "/") Job tuple as an empty value.
        # --all retains that explicit property; a missing property stays unknown.
        if result.get("Job") == "":
            result["Job"] = "0"
        return result

    def environment_flags(self):
        # Values remain internal; expose only whether our two enable/disable
        # names already exist in the user manager or service configuration.
        manager = command(["systemctl", "--user", "show-environment"], timeout=5)
        names = {line.split("=", 1)[0] for line in manager.splitlines()}
        service = command(["systemctl", "--user", "show", SERVICE, "--property=Environment", "--value"], timeout=5)
        import shlex
        names.update(word.split("=", 1)[0] for word in shlex.split(service))
        return sorted(names & FLAG_NAMES)

    def details(self):
        fields = (*SERVICE_DETAIL_PROPERTIES, *SERVICE_EXEC_PROPERTIES)
        text = command(["systemctl", "--user", "show", SERVICE,
                        *("--property=" + field for field in fields)], timeout=5)
        # Return only the allowlisted relationship/status fields. Exec* values
        # contain argv and other data; retain only the leading executable path.
        # systemctl prints one line per Exec* array element, repeating its name.
        result = {}
        for line in text.splitlines():
            name, separator, value = line.partition("=")
            if not separator:
                continue
            if name in SERVICE_DETAIL_PROPERTIES:
                result[name] = value
            elif name in SERVICE_EXEC_PROPERTIES:
                entry = result.setdefault(name, {"executables": [], "arguments_included": False})
                if not value:
                    continue
                match = re.match(r"\{\s*path=([^;{}]+?)\s*;\s*argv\[\]=", value)
                if not match:
                    raise ValueError("service_exec_output_invalid:" + name)
                entry["executables"].append(match.group(1).strip())
        return result

    def observe(self, group, timeout=5):
        return lifecycle.observe(self.show(timeout=timeout), group)

    def action(self, action, timeout=25):
        return command(["systemctl", "--user", action, SERVICE], timeout=timeout)

    def reload(self, timeout=5):
        return command(["systemctl", "--user", "daemon-reload"], timeout=timeout)

    def worker_pid(self):
        result = subprocess.run(["systemctl", "--user", "show", UNIT, "--property=MainPID", "--property=LoadState"],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5)
        fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        if result.returncode not in (0, 4) or fields.get("LoadState") not in ("loaded", "not-found"):
            raise ValueError("trial_service_state_unavailable")
        return int(fields.get("MainPID", "-1")) if fields.get("MainPID", "-1").isdigit() else -1


def safe_path(path):
    value = str(Path(path).absolute())
    if not re.fullmatch(r"/[A-Za-z0-9._/-]+", value) or ".." in Path(value).parts:
        raise ValueError("unsupported_trial_path")
    return Path(value)


def read_json(directory, name):
    entry = config.read_entry(directory, name)
    if entry is None:
        raise ValueError("trial_file_missing:" + name)
    return json.loads(config.payload(entry))


def save(directory, name, value):
    config.write_entry(directory, name, config.describe((json.dumps(value, indent=2) + "\n").encode(), 0o600))


def verify_bundle(bundle, home):
    plan = config.load_plan(bundle)
    if (plan.get("home") != str(home) or plan.get("provider_protocol_version") != 3 or
            plan.get("image_access_enabled") is not False or any(plan["before"].values())):
        raise ValueError("trial_requires_v3_bundle_and_absent_prior_targets")
    manifest = json.loads(config.payload(plan["after"][config.MANIFEST]))
    layer = manifest.get("layer", {})
    if (layer.get("implementation_version") != "3" or
            layer.get("library_path") != str(home / config.LIBRARY) or
            layer.get("enable_environment") != {"SIGHTCIRCUIT_CAPTURE_LAYER": "1"} or
            layer.get("disable_environment") != {"SIGHTCIRCUIT_DISABLE_CAPTURE_LAYER": "1"}):
        raise ValueError("trial_manifest_mismatch")
    build = read_json(config.SOURCE.parent, "build-report.json")
    if (build.get("provider_protocol_version") != 3 or build.get("image_fd_export_enabled") is not False or
            build.get("layer_retains_image_fds") is not False or build.get("deployment_ready") is not False):
        raise ValueError("metadata_only_build_required")
    sources = {item["name"]: item for item in build["sources"]}
    for name in ("capture_layer_impl.hpp", "capture_provider.hpp", "capture_layer_gate.hpp", "capture_provider_inspect.cpp"):
        data = (ROOT / "tools" / name).read_bytes()
        if name not in sources or hashlib.sha256(data).hexdigest() != sources[name]["sha256"]:
            raise ValueError("provider_rebuild_required")
    library = next(item for item in build["artifacts"] if item["name"] == config.SOURCE.name)
    expected = plan["after"][config.LIBRARY]
    if library["sha256"] != expected["sha256"] or config.read_entry(bundle, "libsightcircuit_capture.so") != expected:
        raise ValueError("bundle_library_build_mismatch")
    inspector = build["metadata_inspector"]
    data = metadata.BINARY.read_bytes()
    if hashlib.sha256(data).hexdigest() != inspector["sha256"] or not os.access(metadata.BINARY, os.X_OK):
        raise ValueError("inspector_build_mismatch")
    return plan


def check_targets(plan, runtime, home, *, staged=False):
    for name in config.TARGETS:
        current = config.read_entry(home, name)
        accepted = (plan["before"][name], plan["after"][name]) if staged else (plan["before"][name],)
        if current not in accepted:
            raise ValueError("trial_target_changed:" + name)
    current = config.read_entry(runtime, DROPIN)
    accepted = (None, config.describe(ENABLE, 0o600)) if staged else (None,)
    if current not in accepted:
        raise ValueError("trial_dropin_changed")


def preflight(bundle, runtime, home, manager):
    bundle, runtime, home = (safe_path(path) for path in (bundle, runtime, home))
    fd = config.directory(runtime)
    os.close(fd)
    plan = verify_bundle(bundle, home)
    check_targets(plan, runtime, home)
    service = manager.show()
    if any(service.get(key) != value for key, value in EXPECTED.items()) or int(service.get("MainPID", "0")) < 1:
        raise ValueError("steamvr_service_properties_changed")
    if not service.get("ControlGroup", "").startswith("/") or service["ControlGroup"] == "/":
        raise ValueError("service_control_group_unavailable")
    baseline = manager.observe(service["ControlGroup"])
    if not lifecycle.running(baseline) or baseline["service"].get("InvocationID") != service.get("InvocationID"):
        raise ValueError("baseline_runtime_unconfirmed")
    if manager.environment_flags():
        raise ValueError("existing_layer_environment_flag")
    endpoint = runtime / "sightcircuit-camera-provider/camera.sock"
    if endpoint.exists() or endpoint.is_symlink():
        raise ValueError("existing_provider_endpoint")
    return plan, service


def create_run(bundle, runtime, home, manager, parent=None):
    plan, service = preflight(bundle, runtime, home, manager)
    parent = parent or RUNS
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    run = safe_path(parent / ("trial-" + uuid.uuid4().hex))
    run.mkdir(mode=0o700)
    # A private copy makes rollback independent of the original bundle.
    save(run, "plan.json", plan)
    save(run, "state.json", {"schema_version": 1, "home": str(home), "runtime": str(runtime),
                             "unit": UNIT, "changes_started": False, "service_before": service})
    return run


def locked_run(run):
    run = safe_path(run)
    parent = config.directory(run)
    try:
        fd = os.open("lock", os.O_CREAT | os.O_RDWR | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=parent)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1 or info.st_mode & 0o077:
                raise ValueError("invalid_trial_lock")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BaseException:
            os.close(fd)
            raise
    finally:
        os.close(parent)


def load_state(run):
    state = read_json(run, "state.json")
    if state.get("schema_version") != 1 or state.get("unit") != UNIT or type(state.get("changes_started")) is not bool:
        raise ValueError("invalid_trial_state")
    home, runtime = safe_path(state["home"]), safe_path(state["runtime"])
    plan = config.load_plan(run)
    if plan["home"] != str(home) or plan.get("provider_protocol_version") != 3 or plan.get("image_access_enabled") is not False:
        raise ValueError("trial_plan_mismatch")
    return state, plan, home, runtime


def peer_is_compositor(native, service):
    pid = native.get("peer_pid")
    if type(pid) is not int or pid < 1 or native.get("peer_uid") != os.geteuid():
        return False
    try:
        executable = os.readlink(f"/proc/{pid}/exe")
        cgroups = Path(f"/proc/{pid}/cgroup").read_text().splitlines()
    except (OSError, ValueError):
        return False
    group = service.get("ControlGroup", "")
    return (Path(executable).name == "vrcompositor" and group.startswith("/") and group != "/" and
            any(line.partition("::")[2] == group or line.partition("::")[2].startswith(group + "/") for line in cgroups))


def execute(run, manager, inspect=metadata.inspect, verify_peer=peer_is_compositor, wait_seconds=10):
    lock = locked_run(run)
    report = {"report_kind": "capture_metadata_trial", "schema_version": 1,
              "status": "failed", "exit_code": 1, "image_data_accessed": False, "images_saved": False,
              "phase": "preflight"}
    try:
        state, plan, home, runtime = load_state(run)
        def mark_phase(value):
            state["phase"] = report["phase"] = value
            save(run, "state.json", state)
        def checkpoint(history):
            for entry in history:
                entry.setdefault("phase", report["phase"])
            state["service_control"] = report["service_control"] = history
            save(run, "state.json", state)
        controller = lifecycle.Controller(manager, state["service_before"]["ControlGroup"], checkpoint)
        def transition(action):
            outcome = controller.move(action)
            if "command_error" in outcome:
                report["command_error"] = outcome["command_error"]
            if outcome["command_status"] != "completed" or not outcome["confirmed"]:
                raise ValueError(outcome.get("command_reason", outcome.get("reason", "service_transition_failed")))
        check_targets(plan, runtime, home)
        service = manager.show()
        if (any(service.get(key) != value for key, value in EXPECTED.items()) or
                service.get("InvocationID") != state["service_before"].get("InvocationID") or
                service.get("ControlGroup") != state["service_before"].get("ControlGroup") or service.get("Job") != "0"):
            raise ValueError("steamvr_service_properties_changed")
        state["changes_started"] = True
        mark_phase("stopping_steamvr")  # Arm rollback before any service/file mutation.
        transition("stop")
        mark_phase("staging_layer")
        for name in config.TARGETS:
            controller.require_stopped()
            if config.read_entry(home, name) != plan["before"][name]:
                raise ValueError("trial_target_changed:" + name)
            config.write_entry(home, name, plan["after"][name])
        controller.require_stopped()
        if config.read_entry(runtime, DROPIN) is not None:
            raise ValueError("trial_dropin_changed")
        config.write_entry(runtime, DROPIN, config.describe(ENABLE, 0o600))
        mark_phase("reloading_manager")
        manager.reload(timeout=controller.timeout())
        mark_phase("starting_steamvr")
        transition("start")
        mark_phase("waiting_for_provider")
        end = time.monotonic() + min(wait_seconds, max(0, controller.remaining() - 4))
        endpoint = runtime / "sightcircuit-camera-provider/camera.sock"
        while not endpoint.exists() and time.monotonic() < end:
            time.sleep(0.1)
        if controller.remaining() < 4:
            raise ValueError("metadata_inspection_budget_exhausted")
        mark_phase("inspecting_metadata")
        result = inspect(runtime=runtime, seconds=2)
        save(run, "inspection.json", result)
        if result.get("status") != "inspected" or result.get("exit_code") != 0:
            raise ValueError("metadata_inspection_failed")
        mark_phase("verifying_peer")
        observation = controller.observe()
        if observation["service"].get("InvocationID") != controller.history[-1]["last_observation"]["service"].get("InvocationID"):
            raise ValueError("runtime_generation_changed_during_inspection")
        if not lifecycle.running(observation) or not verify_peer(result["native"], observation["service"]):
            raise ValueError("metadata_peer_not_service_compositor")
        mark_phase("complete")
        report.update(status="inspected", exit_code=0, record_count=result["record_count"],
                      reviewed_profile_candidates=result["reviewed_profile_candidates"])
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        report["reason"] = str(error)
        if isinstance(error, ServiceCommandError):
            report["command_error"] = error.details
    finally:
        save(run, "trial.json", report)
        os.close(lock)
    return report


def rollback(run, manager):
    lock = locked_run(run)
    report = {"report_kind": "capture_metadata_rollback", "schema_version": 1,
              "status": "recovery_failed", "exit_code": 1, "configuration_restored": False,
              "normal_service_started": False, "normal_runtime_observed": False,
              "normal_service_already_running": False,
              "passthrough_observation_required": True,
              "phase": "preflight"}
    def record_error(errors, error):
        errors.append(str(error))
        if isinstance(error, ServiceCommandError):
            report.setdefault("command_errors", []).append(dict(error.details, phase=report["phase"]))
    try:
        state, plan, home, runtime = load_state(run)
        if manager.worker_pid():
            raise ValueError("trial_worker_still_running")
        def mark_phase(value):
            state["recovery_phase"] = report["phase"] = value
            save(run, "state.json", state)
            save(run, "rollback.json", report)
        def checkpoint(history):
            for entry in history:
                entry.setdefault("phase", report["phase"])
            report["service_control"] = history
            state["recovery_service_control"] = history
            save(run, "state.json", state)
            save(run, "rollback.json", report)
        controller = lifecycle.Controller(manager, state["service_before"]["ControlGroup"], checkpoint)
        clean_configuration = (all(config.read_entry(home, name) == plan["before"][name] for name in config.TARGETS)
                               and config.read_entry(runtime, DROPIN) is None)
        if config.read_entry(run, "rollback.json") is not None:
            previous = read_json(run, "rollback.json")
            if (previous.get("configuration_restored") and
                    all(config.read_entry(home, name) == plan["before"][name] for name in config.TARGETS) and
                    config.read_entry(runtime, DROPIN) is None):
                service = manager.show()
                if service.get("ActiveState") == "active":
                    report = previous
                    report["normal_runtime_observed"] = controller.confirm_running()
                    report["runtime_rechecked_without_restart"] = True
                    if not report["normal_runtime_observed"]:
                        report.update(status="recovery_failed", exit_code=1, reason="normal_runtime_unconfirmed")
                    return report
        # File restoration alone cannot unload a layer already in a compositor.
        # Skip a restart only when the worker never reached its staging phase.
        if (state["changes_started"] and state.get("phase") == "stopping_steamvr" and
                clean_configuration and manager.show().get("ActiveState") == "active"):
            report.update(configuration_restored=True, normal_service_already_running=True,
                          phase="checking_existing_runtime")
            report["normal_runtime_observed"] = controller.confirm_running()
            if report["normal_runtime_observed"]:
                report.update(status="restored", phase="complete", exit_code=0)
            else:
                report["reason"] = "normal_runtime_unconfirmed"
            return report
        if not state["changes_started"]:
            report.update(status="no_changes", exit_code=0, configuration_restored=True)
        else:
            errors = []
            is_stopped = False
            try:
                mark_phase("stopping_steamvr")
                initial = controller.observe()
                if (initial["service"].get("Job") != "0" or
                        initial["service"].get("ActiveState") in ("activating", "deactivating")):
                    mark_phase("waiting_for_existing_job")
                    pending = controller.wait_idle()
                    if not pending["confirmed"]:
                        raise ValueError("existing_service_job_unsettled")
                    initial = pending["last_observation"]
                is_stopped = lifecycle.stopped(initial)
                if not is_stopped:
                    mark_phase("stopping_steamvr")
                    outcome = controller.move("stop")
                    if outcome["command_status"] == "failed":
                        errors.append(outcome["command_reason"])
                        if "command_error" in outcome:
                            report.setdefault("command_errors", []).append(dict(outcome["command_error"], phase=report["phase"]))
                    is_stopped = outcome["confirmed"]
                if not is_stopped:
                    raise ValueError("steamvr_stop_unconfirmed")
            except (ValueError, OSError, subprocess.SubprocessError) as error:
                record_error(errors, error)
            # Always remove only our exact enable drop-in, even if another
            # target was edited or a service stop failed. Preserve foreign edits.
            dropin_removed = False
            try:
                mark_phase("removing_enable_dropin")
                current = config.read_entry(runtime, DROPIN)
                if current is not None:
                    if current != config.describe(ENABLE, 0o600):
                        raise ValueError("trial_dropin_changed")
                    config.write_entry(runtime, DROPIN, None)
                dropin_removed = True
            except (ValueError, OSError) as error:
                record_error(errors, error)
            if is_stopped:
                try:
                    mark_phase("restoring_configuration")
                    controller.require_stopped()
                    config.restore(run, expected_home=home, before_change=controller.require_stopped)
                    report["configuration_restored"] = True
                    endpoint = runtime / "sightcircuit-camera-provider/camera.sock"
                    if endpoint.exists() or endpoint.is_symlink():
                        config.recover_provider(runtime)
                except (ValueError, OSError) as error:
                    record_error(errors, error)
            try:
                mark_phase("reloading_manager")
                manager.reload(timeout=controller.timeout())
                if is_stopped and dropin_removed and report["configuration_restored"]:
                    controller.require_stopped()
                    mark_phase("starting_normal_steamvr")
                    outcome = controller.move("start")
                    if outcome["command_status"] == "failed":
                        errors.append(outcome["command_reason"])
                        if "command_error" in outcome:
                            report.setdefault("command_errors", []).append(dict(outcome["command_error"], phase=report["phase"]))
                    service = outcome.get("last_observation", {}).get("service", {})
                    report["normal_service_started"] = (outcome["command_status"] == "completed" and
                        service.get("ActiveState") == "active" and service.get("SubState") == "running" and
                        service.get("MainPID", "").isdecimal() and int(service["MainPID"]) > 0)
                    report["normal_runtime_observed"] = outcome["confirmed"]
                    if not report["normal_runtime_observed"]:
                        raise ValueError("normal_runtime_unconfirmed")
            except (ValueError, OSError, subprocess.SubprocessError) as error:
                record_error(errors, error)
            if errors:
                report["errors"] = errors
            elif report["configuration_restored"] and report["normal_service_started"] and report["normal_runtime_observed"]:
                report.update(status="restored", phase="complete", exit_code=0)
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        report["reason"] = str(error)
    finally:
        save(run, "rollback.json", report)
        os.close(lock)
    return report


def launch_command(run):
    python, script = safe_path(sys.executable), safe_path(Path(__file__))
    cleanup = f"{python} {script} --rollback {safe_path(run)}"
    return ["systemd-run", "--user", "--collect", "--no-ask-password", "--unit=" + UNIT,
            "--property=Type=exec", "--property=RuntimeMaxSec=60", "--property=TimeoutStopSec=60",
            "--property=Restart=no", "--property=UMask=0077", "--property=ExecStopPost=" + cleanup,
            str(python), str(script), "--worker", str(run)]


def check_recovery(parent=RUNS, runner=command, budget=8):
    """Prove this manager runs ExecStopPost after a real one-second timeout."""
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = safe_path(parent / ("recovery-" + uuid.uuid4().hex))
    path.mkdir(mode=0o700)
    python, script = safe_path(sys.executable), safe_path(Path(__file__))
    unit = "sightcircuit-metadata-recovery-" + path.name[9:] + ".service"
    cleanup = f"{python} {script} --recovery-complete {path}"
    runner(["systemd-run", "--user", "--collect", "--no-ask-password", "--unit=" + unit,
            "--property=Type=exec", "--property=RuntimeMaxSec=1", "--property=TimeoutStopSec=2",
            "--property=Restart=no", "--property=UMask=0077", "--property=ExecStopPost=" + cleanup,
            str(python), str(script), "--recovery-hold", str(path)], timeout=5)
    end = time.monotonic() + budget
    while time.monotonic() < end:
        if config.read_entry(path, "cleanup.json") is not None:
            result = read_json(path, "cleanup.json")
            if (result.get("service_result") == "timeout" and
                    config.read_entry(path, "started.json") is not None):
                return {"status": "passed", "service_result": "timeout", "steamvr_changed": False}
            break
        time.sleep(0.05)
    try:
        runner(["systemctl", "--user", "stop", unit], timeout=5)
    finally:
        raise ValueError("systemd_post_exit_recovery_check_failed")


def latest_report(parent=RUNS):
    if not parent.exists():
        raise ValueError("no_metadata_trial")
    candidates = []
    for path in parent.iterdir():
        if re.fullmatch(r"trial-[0-9a-f]{32}", path.name):
            fd = config.directory(path)
            try:
                candidates.append((os.fstat(fd).st_mtime_ns, path))
            finally:
                os.close(fd)
    if not candidates:
        raise ValueError("no_metadata_trial")
    run = max(candidates)[1]
    report = {"report_kind": "capture_metadata_trial_result", "schema_version": 1,
              "trial_directory": str(run), "status": "incomplete", "exit_code": 3,
              "images_saved": False, "passthrough_observation_required": True}
    for name in ("trial", "inspection", "rollback"):
        if config.read_entry(run, name + ".json") is not None:
            report[name] = read_json(run, name + ".json")
    if "rollback" in report:
        success = (report["rollback"].get("status") == "restored" and
                   report["rollback"].get("normal_runtime_observed") is True and
                   report.get("trial", {}).get("status") == "inspected")
        report.update(status="completed" if success else "failed", exit_code=0 if success else 1)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--preflight", type=Path, metavar="BUNDLE", help="Validate metadata-only build, inactive bundle and service without changes")
    mode.add_argument("--start-metadata-test", type=Path, metavar="BUNDLE", help="Blocked while Frame service-control failures are investigated; reporting and known-run recovery remain available")
    mode.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    mode.add_argument("--rollback", type=Path, help="Recover a known trial after its worker has exited")
    mode.add_argument("--report-latest", action="store_true", help="Read the last trial and rollback reports without runtime calls")
    mode.add_argument("--service-details", action="store_true", help="Read current SteamVR service relationships and executable paths; no changes, command arguments or environment values")
    mode.add_argument("--runtime-status", action="store_true", help="Read service/cgroup metadata and compositor liveness; no restart, VR API or camera access")
    mode.add_argument("--recovery-hold", type=Path, help=argparse.SUPPRESS)
    mode.add_argument("--recovery-complete", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    report = {"report_kind": "capture_metadata_trial_launcher", "schema_version": 1,
              "status": "not_run", "exit_code": 3, "activation_requested": False}
    if args.start_metadata_test and ACTIVATION_BLOCKED_REASON:
        report.update(status="blocked", reason=ACTIVATION_BLOCKED_REASON,
                      next_step="Read --report-latest from an independent terminal; do not repeat activation")
        print(json.dumps(report, indent=2))
        return report["exit_code"]
    try:
        manager = Systemd()
        if args.service_details:
            report.update(status="service_details", exit_code=0, service=manager.details())
        elif args.runtime_status:
            service = manager.show()
            controller = lifecycle.Controller(manager, service.get("ControlGroup", ""), budget=12)
            confirmed = controller.confirm_running()
            report.update(status="runtime_observed" if confirmed else "runtime_unconfirmed",
                          exit_code=0 if confirmed else 1, runtime=controller.last_observation,
                          normal_runtime_observed=confirmed, rendering_readiness_verified=False,
                          passthrough_observation_required=True,
                          scope="Service and compositor process liveness only; no rendering, passthrough or camera verification")
        elif args.recovery_hold or args.recovery_complete:
            if not os.environ.get("INVOCATION_ID"):
                raise ValueError("recovery_check_requires_systemd")
            if args.recovery_hold:
                save(safe_path(args.recovery_hold), "started.json", {"started": True})
                time.sleep(60)  # The rehearsal unit must terminate this after one second.
                raise ValueError("recovery_check_did_not_time_out")
            save(safe_path(args.recovery_complete), "cleanup.json", {"service_result": os.environ.get("SERVICE_RESULT")})
            report.update(status="recovery_check_cleanup", exit_code=0)
        elif args.report_latest:
            report = latest_report()
        elif args.worker:
            if not os.environ.get("INVOCATION_ID") or manager.worker_pid() != os.getpid():
                raise ValueError("worker_requires_own_systemd_service")
            report = execute(args.worker, manager)
        elif args.rollback:
            report = rollback(args.rollback, manager)
        elif args.preflight or args.start_metadata_test:
            runtime = Path(os.environ.get("XDG_RUNTIME_DIR", "/missing-runtime"))
            home = Path.home().absolute()
            bundle = args.preflight or args.start_metadata_test
            if args.preflight:
                _, service = preflight(bundle, runtime, home, manager)
                report.update(status="preflight_passed", exit_code=0, service=service)
            else:
                preflight(bundle, runtime, home, manager)
                recovery_check = check_recovery()
                run = create_run(bundle, runtime, home, manager)
                save(run, "recovery-check.json", recovery_check)
                report.update(activation_requested=True, trial_directory=str(run), unit=UNIT)
                command(launch_command(run), timeout=10)
                report.update(status="scheduled", exit_code=0, activation_requested=True,
                              trial_directory=str(run), unit=UNIT,
                              worker_maximum_seconds=60, stop_phase_timeout_seconds=60,
                              recovery_check=recovery_check,
                              recovery="systemd ExecStopPost; inspect rollback.json afterward")
    except (ValueError, OSError, KeyError, StopIteration, subprocess.SubprocessError) as error:
        report.update(status="launch_unconfirmed" if report.get("activation_requested") else "refused",
                      exit_code=1, reason=str(error))
    print(json.dumps(report, indent=2))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
