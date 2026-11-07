"""Rehearse SteamVR stop/start over SSH without installing a capture layer."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

try:
    from . import capture_layer_trial as trial, capture_service_lifecycle as lifecycle
except ImportError:
    import capture_layer_trial as trial
    import capture_service_lifecycle as lifecycle

config = trial.config
UNIT = "sightcircuit-service-rehearsal.service"
RUNS = config.ROOT / "artifacts/service-rehearsals"
SOURCES = ("service_rehearsal.py", "capture_layer_trial.py", "capture_service_lifecycle.py",
           "capture_layer_config.py", "capture_session.py", "capture_provider_check.py",
           "capture_supervisor.py")


class Systemd(trial.Systemd):
    def session_policy(self):
        text = trial.command(["systemctl", "--user", "show", "gamescope-session.service", "--all",
                              "--property=LoadState,PartOf,RefuseManualStart"], timeout=5)
        return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)

    def unit_state(self, unit):
        result = subprocess.run(
            ["systemctl", "--user", "show", unit, "--all",
             "--property=LoadState,ActiveState,MainPID,Job"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5)
        if result.returncode not in (0, 4) or len(result.stdout) > 8192:
            raise ValueError("rehearsal_service_state_unavailable")
        fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        if fields.get("LoadState") not in ("loaded", "not-found"):
            raise ValueError("rehearsal_service_state_unavailable")
        return fields


def new_report(kind):
    return {"report_kind": "steamvr_service_" + kind, "schema_version": 1,
            "status": "not_run", "exit_code": 3, "activation_requested": False,
            "configuration_written": False, "image_data_accessed": False,
            "normal_runtime_observed": False, "rendering_readiness_verified": False,
            "passthrough_observation_required": True}


def independent_ssh(value):
    """Require an inbound connection from another host, without saving addresses."""
    fields = (value or "").split()
    try:
        peer, server = ipaddress.ip_address(fields[0]), ipaddress.ip_address(fields[2])
        valid = (len(fields) == 4 and peer != server and not peer.is_loopback and
                 not peer.is_unspecified and all(0 < int(fields[i]) < 65536 for i in (1, 3)))
    except (ValueError, IndexError):
        valid = False
    if not valid:
        raise ValueError("start_requires_independent_inbound_ssh")


def capture_absent(home, runtime, manager):
    for name in config.TARGETS:
        if config.read_entry(home, name) is not None:
            raise ValueError("existing_capture_configuration:" + name)
    for root in (home / ".config", runtime):
        if config.read_entry(root, trial.DROPIN) is not None:
            raise ValueError("existing_capture_dropin")
    endpoint = runtime / "sightcircuit-camera-provider/camera.sock"
    if endpoint.exists() or endpoint.is_symlink():
        raise ValueError("existing_provider_endpoint")
    if manager.environment_flags():
        raise ValueError("existing_layer_environment_flag")


def preflight(home, runtime, manager):
    home, runtime = trial.safe_path(home), trial.safe_path(runtime)
    for path in (home, runtime):
        os.close(config.directory(path))
    capture_absent(home, runtime, manager)
    policy = manager.session_policy()
    if policy.get("LoadState") not in ("loaded", "not-found"):
        raise ValueError("gamescope_session_policy_unavailable")
    if policy.get("LoadState") == "loaded":
        if "PartOf" not in policy:
            raise ValueError("gamescope_session_policy_unavailable")
        if trial.SERVICE in policy["PartOf"].split():
            raise ValueError("steamvr_stop_propagates_to_graphical_session")
    service = manager.show()
    if any(service.get(key) != value for key, value in trial.EXPECTED.items()):
        raise ValueError("steamvr_service_properties_changed")
    controller = lifecycle.Controller(manager, service.get("ControlGroup", ""), budget=12)
    if (not controller.confirm_running() or
            controller.last_observation["service"].get("InvocationID") != service.get("InvocationID")):
        raise ValueError("baseline_runtime_unconfirmed")
    return service


def create_run(home, runtime, manager, parent=RUNS):
    service = preflight(home, runtime, manager)
    parent = trial.safe_path(parent)
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.close(config.directory(parent))
    run = parent / ("rehearsal-" + uuid.uuid4().hex)
    run.mkdir(mode=0o700)
    # Recovery uses this run's source snapshot even if the checkout is updated.
    sources = {}
    for name in SOURCES:
        entry = config.describe(Path(__file__).with_name(name).read_bytes(), 0o600)
        sources[name] = entry["sha256"]
        config.write_entry(run, "code/" + name, entry)
    trial.save(run, "sources.json", sources)
    trial.save(run, "state.json", {"schema_version": 1, "unit": UNIT, "home": str(home),
        "runtime": str(runtime), "service_before": service, "changes_started": False})
    return run


def load_state(run):
    state = trial.read_json(run, "state.json")
    if (state.get("schema_version") != 1 or state.get("unit") != UNIT or
            type(state.get("changes_started")) is not bool or
            state.get("home") != str(Path.home().absolute())):
        raise ValueError("invalid_rehearsal_state")
    home, runtime = trial.safe_path(state["home"]), trial.safe_path(state["runtime"])
    if str(runtime) != os.environ.get("XDG_RUNTIME_DIR"):
        raise ValueError("rehearsal_runtime_mismatch")
    return state, home, runtime


def record_error(report, error):
    report.update(status="failed", exit_code=1, reason=str(error))
    if hasattr(error, "details"):
        report["command_error"] = error.details


def controller_for(run, state, report, manager, filename):
    def checkpoint(history):
        for entry in history:
            entry.setdefault("phase", report["phase"])
        report["service_control"] = history
        trial.save(run, filename, report)
    return lifecycle.Controller(manager, state["service_before"]["ControlGroup"], checkpoint)


def require_transition(controller, action, report):
    outcome = controller.move(action)
    if "command_error" in outcome:
        report["command_error"] = outcome["command_error"]
    if outcome["command_status"] != "completed" or not outcome["confirmed"]:
        raise ValueError(outcome.get("command_reason", outcome.get("reason", "service_transition_failed")))


def execute(run, manager):
    lock = trial.locked_run(run)
    report = new_report("rehearsal")
    report.update(status="failed", exit_code=1, phase="preflight")
    try:
        state, home, runtime = load_state(run)
        if state["changes_started"]:
            raise ValueError("rehearsal_already_started")
        service = preflight(home, runtime, manager)
        if any(service.get(key) != state["service_before"].get(key)
               for key in ("InvocationID", "ControlGroup")):
            raise ValueError("baseline_runtime_changed")
        state["changes_started"] = True
        trial.save(run, "state.json", state)  # Arm independent recovery before stop.
        controller = controller_for(run, state, report, manager, "rehearsal.json")
        report["phase"] = "stopping_steamvr"
        require_transition(controller, "stop", report)
        capture_absent(home, runtime, manager)
        controller.require_stopped()
        report["phase"] = "starting_normal_steamvr"
        require_transition(controller, "start", report)
        if controller.last_observation["service"]["InvocationID"] == service["InvocationID"]:
            raise ValueError("runtime_generation_did_not_change")
        capture_absent(home, runtime, manager)
        report.update(status="rehearsed", exit_code=0, phase="complete", normal_runtime_observed=True)
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        record_error(report, error)
    finally:
        try:
            trial.save(run, "rehearsal.json", report)
        finally:
            os.close(lock)
    return report


def recover(run, manager):
    lock = trial.locked_run(run)
    report = new_report("recovery")
    report.update(status="failed", exit_code=1, phase="checking_worker",
                  service_result=os.environ.get("SERVICE_RESULT"))
    try:
        if manager.unit_state(UNIT).get("MainPID") != "0":
            raise ValueError("rehearsal_worker_still_alive_or_unknown")
        state, home, runtime = load_state(run)
        if not state["changes_started"]:
            report.update(status="not_needed", exit_code=0, phase="complete")
        else:
            capture_absent(home, runtime, manager)
            controller = controller_for(run, state, report, manager, "recovery.json")
            report["phase"] = "observing_normal_steamvr"
            observation = controller.observe()
            if not lifecycle.running(observation) and not lifecycle.stopped(observation):
                outcome = controller.wait_idle()
                if not outcome["confirmed"]:
                    raise ValueError("existing_service_job_unsettled")
                observation = controller.last_observation
            if lifecycle.running(observation):
                if not controller.confirm_running():
                    raise ValueError("normal_runtime_unconfirmed")
                report["normal_service_already_running"] = True
            else:
                # An old successful run must never revive an unrelated later stop.
                if state.get("recovery_completed"):
                    raise ValueError("completed_rehearsal_runtime_changed")
                controller.require_stopped()
                capture_absent(home, runtime, manager)
                report["phase"] = "starting_normal_steamvr"
                require_transition(controller, "start", report)
                report["normal_service_started"] = True
            capture_absent(home, runtime, manager)
            state["recovery_completed"] = True
            trial.save(run, "state.json", state)
            report.update(status="restored", exit_code=0, phase="complete", normal_runtime_observed=True,
                          runtime=controller.last_observation)
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        record_error(report, error)
    finally:
        try:
            trial.save(run, "recovery.json", report)
        finally:
            os.close(lock)
    return report


def launch_command(run):
    python = trial.safe_path(sys.executable)
    script = trial.safe_path(run / "code/service_rehearsal.py")
    cleanup = f"{python} {script} --recover {trial.safe_path(run)}"
    return ["systemd-run", "--user", "--collect", "--no-ask-password", "--unit=" + UNIT,
            "--property=Type=exec", "--property=RuntimeMaxSec=60", "--property=TimeoutStopSec=60",
            "--property=KillMode=control-group", "--property=Restart=no", "--property=UMask=0077",
            "--property=ExecStopPost=" + cleanup, str(python), str(script), "--worker", str(run)]


def result(run):
    report = new_report("rehearsal_result")
    report.update(status="incomplete", run_directory=str(run))
    for name in ("rehearsal", "recovery"):
        if config.read_entry(run, name + ".json") is not None:
            report[name] = trial.read_json(run, name + ".json")
    if report.get("recovery", {}).get("phase") == "complete" or report.get("recovery", {}).get("reason"):
        success = (report.get("rehearsal", {}).get("status") == "rehearsed" and
                   report["recovery"].get("status") == "restored" and
                   report["recovery"].get("normal_runtime_observed") is True)
        report.update(status="completed" if success else "failed", exit_code=0 if success else 1,
                      normal_runtime_observed=report["recovery"].get("normal_runtime_observed", False))
    return report


def latest(parent=RUNS):
    candidates = []
    if parent.exists():
        for path in parent.iterdir():
            if trial.re.fullmatch(r"rehearsal-[0-9a-f]{32}", path.name):
                fd = config.directory(path)
                try:
                    candidates.append((os.fstat(fd).st_mtime_ns, path))
                finally:
                    os.close(fd)
    if not candidates:
        raise ValueError("no_service_rehearsal")
    return result(max(candidates)[1])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--preflight", action="store_true", help="Read-only service and absent-capture checks")
    mode.add_argument("--start", action="store_true", help="Restart SteamVR once; requires independent inbound SSH")
    mode.add_argument("--report", type=Path, metavar="RUN", help="Read a saved rehearsal result")
    mode.add_argument("--report-latest", action="store_true")
    mode.add_argument("--recover", type=Path, metavar="RUN", help="Recover one known run after its worker exits")
    mode.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    report = new_report("rehearsal_launcher")
    report["restart_requested"] = False
    try:
        manager = Systemd()
        if args.report:
            report = result(trial.safe_path(args.report))
        elif args.report_latest:
            report = latest()
        elif args.worker:
            if not os.environ.get("INVOCATION_ID") or manager.unit_state(UNIT).get("MainPID") != str(os.getpid()):
                raise ValueError("worker_requires_own_systemd_service")
            report = execute(trial.safe_path(args.worker), manager)
        elif args.recover:
            report = recover(trial.safe_path(args.recover), manager)
        elif args.preflight or args.start:
            home = Path.home().absolute()
            runtime = trial.safe_path(os.environ.get("XDG_RUNTIME_DIR", "/missing-runtime"))
            service = preflight(home, runtime, manager)
            report.update(status="preflight_passed", exit_code=0, service=service)
            if args.start:
                independent_ssh(os.environ.get("SSH_CONNECTION"))
                for unit in (UNIT, trial.UNIT):
                    if manager.unit_state(unit).get("LoadState") != "not-found":
                        raise ValueError("existing_trial_unit:" + unit)
                recovery_check = trial.check_recovery(parent=RUNS)
                run = create_run(home, runtime, manager)
                trial.save(run, "recovery-check.json", recovery_check)
                report.update(restart_requested=True, run_directory=str(run), unit=UNIT)
                trial.command(launch_command(run), timeout=10)
                report.update(status="scheduled", exit_code=0, recovery_check=recovery_check,
                              worker_maximum_seconds=60, stop_phase_timeout_seconds=60)
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        record_error(report, error)
        if report.get("restart_requested"):
            report["status"] = "launch_unconfirmed"
    print(json.dumps(report, indent=2))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
