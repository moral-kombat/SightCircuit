"""Prepare and explicitly rehearse a SteamOS-managed graphical-session logout."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

try:
    from . import service_rehearsal as service
except ImportError:
    import service_rehearsal as service

trial, config, lifecycle = service.trial, service.config, service.lifecycle
UNIT = "framejacker-session-rehearsal.service"
RUNS = config.ROOT / "artifacts/session-rehearsals"
SOURCES = ("session_rehearsal.py", *service.SOURCES)
UNITS = ("steamvr.service", "gamescope-session.service", "gamescope-session.target", "graphical-session.target")
FIELDS = ("Id", "LoadState", "ActiveState", "SubState", "MainPID", "ControlGroup", "Job", "Result",
          "InvocationID", "PartOf", "Requires", "BindsTo", "RefuseManualStart", "FragmentPath", "DropInPaths")
PLATFORM_FILES = ("/usr/bin/steamosctl", "/usr/lib/steamos-manager", "/usr/bin/start-gamescope-session",
                  "/usr/lib/steamos/gamescope-onready", "/usr/lib/steamos/gamescope-session",
                  "/usr/share/wayland-sessions/gamescope-wayland.desktop",
                  "/usr/share/dbus-1/interfaces/com.steampowered.SteamOSManager1.xml",
                  *("/usr/lib/systemd/user/" + unit for unit in UNITS))
SWITCH = ["/usr/bin/steamosctl", "switch-to-game-mode"]
ERRORS = (ValueError, OSError, KeyError, subprocess.SubprocessError)


def digest(path):
    result, size = hashlib.sha256(), 0
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(65536), b""):
            size += len(block)
            if size > 64 * 1024 * 1024:
                raise ValueError("source_file_too_large")
            result.update(block)
    return result.hexdigest()


def parse_units(text, names):
    result = {}
    for block in text.strip().split("\n\n"):
        fields = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        name = fields.get("Id")
        if name not in names or name in result:
            raise ValueError("unexpected_session_unit_output")
        result[name] = {key: value for key, value in fields.items() if key in FIELDS}
        if result[name].get("Job") == "":
            result[name]["Job"] = "0"
    if set(result) != set(names):
        raise ValueError("incomplete_session_unit_output")
    return result


class Systemd(service.Systemd):
    def snapshot(self, timeout=8):
        end = time.monotonic() + timeout
        def remaining():
            seconds = end - time.monotonic()
            if seconds <= 0:
                raise ValueError("session_observation_deadline")
            return min(4, seconds)
        units = parse_units(trial.command(["systemctl", "--user", "show", *UNITS, "--all",
            "--property=" + ",".join(FIELDS)], timeout=remaining()), UNITS)
        display = parse_units(trial.command(["systemctl", "show", "sddm.service", "--all",
            "--property=" + ",".join(FIELDS)], timeout=remaining()), ("sddm.service",))["sddm.service"]
        runtime = lifecycle.observe(units[trial.SERVICE], units[trial.SERVICE].get("ControlGroup", ""))
        remaining()
        return {"units": units, "display_manager": display, "runtime": runtime}

    def platform_contract(self):
        packages = trial.command(["pacman", "-Q", "steamos-manager", "deckard-steamvr-session"], timeout=5)
        versions = dict(line.split(maxsplit=1) for line in packages.splitlines())
        if not versions.get("steamos-manager", "").startswith("26.4.1-"):
            raise ValueError("unreviewed_steamos_manager_version")
        mode = trial.command(["/usr/bin/steamosctl", "get-default-login-mode"], timeout=5).strip()
        if mode != "game":
            raise ValueError("default_login_mode_is_not_game")
        interface = trial.command(["busctl", "--user", "--auto-start=no", "--no-pager", "introspect",
            "com.steampowered.SteamOSManager1", "/com/steampowered/SteamOSManager1",
            "com.steampowered.SteamOSManager1.SessionManagement1"], timeout=5)
        if not any(line.split()[:2] == [".SwitchToGameMode", "method"] for line in interface.splitlines()):
            raise ValueError("session_switch_interface_unavailable")
        return {"packages": versions, "default_login_mode": mode, "switch_method_available": True,
                "files": {name: digest(name) for name in PLATFORM_FILES}}

    def switch_to_game(self, timeout=10):
        # No retries: a client failure or timeout can still have requested logout.
        result = subprocess.run(SWITCH, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)
        if result.returncode:
            error = ValueError("platform_session_request_failed")
            error.details = {"operation": "switch_to_game_mode", "return_code": result.returncode,
                             "stderr": result.stderr[:4096]}
            raise error


def healthy(snapshot):
    try:
        for name in UNITS:
            item = snapshot["units"][name]
            substate = "running" if name.endswith(".service") else "active"
            if (item.get("LoadState") != "loaded" or item.get("ActiveState") != "active" or
                    item.get("SubState") != substate or item.get("Job") != "0" or
                    not re.fullmatch(r"[0-9a-f]{32}", item.get("InvocationID", ""))):
                return False
            if name.endswith(".service") and (not item.get("MainPID", "").isdecimal() or int(item["MainPID"]) < 1):
                return False
        display = snapshot["display_manager"]
        return (display.get("ActiveState") == "active" and display.get("SubState") == "running" and
                display.get("Job") == "0" and lifecycle.running(snapshot["runtime"]))
    except (KeyError, TypeError):
        return False


def identity(snapshot):
    return ([snapshot["units"][name].get("InvocationID") for name in UNITS],
            [snapshot["units"][name].get("MainPID") for name in UNITS[:2]],
            snapshot["runtime"]["compositors"])


def changed(before, after):
    return all(before["units"][name]["InvocationID"] != after["units"][name].get("InvocationID") for name in UNITS)


def preflight(home, runtime, manager):
    service.capture_absent(home, runtime, manager)
    contract = manager.platform_contract()
    first = manager.snapshot()
    time.sleep(0.2)
    second = manager.snapshot()
    if not healthy(first) or not healthy(second) or identity(first) != identity(second):
        raise ValueError("normal_session_unconfirmed")
    gamescope = second["units"]["gamescope-session.service"]
    if (trial.SERVICE not in gamescope.get("PartOf", "").split() or
            gamescope.get("RefuseManualStart") != "yes" or
            "gamescope-session.service" not in second["units"][trial.SERVICE].get("Requires", "").split()):
        raise ValueError("session_policy_changed")
    if any(second["units"][unit].get("DropInPaths") != "" for unit in UNITS):
        raise ValueError("session_dropins_require_review")
    return contract, second


def prepare(destination, home, runtime, manager):
    destination, home, runtime = map(trial.safe_path, (destination, home, runtime))
    contract, baseline = preflight(home, runtime, manager)
    destination.mkdir(mode=0o700)  # Exclusive; never replace an earlier preparation.
    helpers = {}
    for name in SOURCES:
        entry = config.describe(Path(__file__).with_name(name).read_bytes(), 0o600)
        helpers[name] = entry["sha256"]
        config.write_entry(destination, "code/" + name, entry)
    plan = {"schema_version": 1, "kind": "session_rehearsal_preparation", "home": str(home),
            "runtime": str(runtime), "created_utc": datetime.now(timezone.utc).isoformat(),
            "platform": contract, "baseline": baseline, "helpers": helpers,
            "capture_enabled": False, "session_request": SWITCH}
    trial.save(destination, "plan.json", plan)
    return plan


def load_plan(bundle):
    plan = trial.read_json(bundle, "plan.json")
    if (plan.get("schema_version") != 1 or plan.get("kind") != "session_rehearsal_preparation" or
            plan.get("capture_enabled") is not False or plan.get("session_request") != SWITCH or
            plan.get("home") != str(Path.home().absolute()) or
            plan.get("runtime") != os.environ.get("XDG_RUNTIME_DIR") or set(plan.get("helpers", {})) != set(SOURCES)):
        raise ValueError("invalid_session_preparation")
    for name, expected in plan["helpers"].items():
        entry = config.read_entry(bundle, "code/" + name)
        if entry is None or entry["sha256"] != expected:
            raise ValueError("prepared_helper_changed:" + name)
    return plan


def check(bundle, manager):
    plan = load_plan(bundle)
    contract, baseline = preflight(Path(plan["home"]), Path(plan["runtime"]), manager)
    if contract != plan["platform"]:
        raise ValueError("platform_changed_since_preparation")
    return plan, baseline


def new_report(kind):
    return {"report_kind": "frame_session_" + kind, "schema_version": 1, "status": "not_run", "exit_code": 3,
            "capture_enabled": False, "image_data_accessed": False, "framejacker_configuration_written": False,
            "session_request_sent": False, "normal_runtime_observed": False,
            "visual_confirmation_required": True}


def create_run(bundle, manager, parent=RUNS):
    plan, baseline = check(bundle, manager)
    parent = trial.safe_path(parent)
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.close(config.directory(parent))
    run = parent / ("session-" + uuid.uuid4().hex)
    run.mkdir(mode=0o700)
    for name in SOURCES:
        config.write_entry(run, "code/" + name, config.read_entry(bundle, "code/" + name))
    trial.save(run, "plan.json", plan)
    trial.save(run, "state.json", {"unit": UNIT, "baseline": baseline, "session_request_sent": False})
    return run


def observe(manager, before, report, checkpoint, seconds=75, require_change=True,
            clock=time.monotonic, pause=time.sleep):
    end, stable_since, last_identity = clock() + seconds, None, None
    report["observations"] = 0
    while clock() < end:
        try:
            snapshot = manager.snapshot(timeout=min(8, end - clock()))
            report["observations"] += 1
            report["last_observation"] = snapshot
            ready = healthy(snapshot)
            new = ready and changed(before, snapshot)
            report["session_changed"] = new
            current = identity(snapshot) if ready else None
            qualifies = ready and (new or not require_change)
            if not qualifies or current != last_identity:
                stable_since = clock() if qualifies else None
            last_identity = current
            report["phase"] = "confirming_session" if qualifies else "waiting_for_session"
            checkpoint()
            if stable_since is not None and clock() - stable_since >= 2 and clock() < end:
                report["normal_runtime_observed"] = True
                return True
        except ERRORS as error:
            stable_since, last_identity = None, None
            report["last_observation_error"] = str(error)[:4096]
            checkpoint()
        pause(min(0.5, max(0, end - clock())))
    return False


def execute(run, manager, observer=observe):
    lock = trial.locked_run(run)
    report = new_report("rehearsal")
    report.update(status="failed", exit_code=1, phase="preflight")
    try:
        plan, baseline = check(run, manager)
        state = trial.read_json(run, "state.json")
        if state.get("unit") != UNIT or state.get("session_request_sent") is not False:
            raise ValueError("session_request_already_sent_or_invalid")
        if identity(state["baseline"]) != identity(baseline):
            raise ValueError("session_changed_before_request")
        state["session_request_sent"] = report["session_request_sent"] = True
        trial.save(run, "state.json", state)
        report["phase"] = "requesting_game_session"
        trial.save(run, "rehearsal.json", report)
        request_error = None
        try:
            manager.switch_to_game(timeout=10)
            report["request_status"] = "acknowledged"
        except ERRORS as error:
            request_error = str(error)
            report.update(request_status="unconfirmed", request_error=request_error)
            if hasattr(error, "details"):
                report["command_error"] = error.details
        restored = observer(manager, baseline, report, lambda: trial.save(run, "rehearsal.json", report))
        service.capture_absent(Path(plan["home"]), Path(plan["runtime"]), manager)
        if manager.platform_contract() != plan["platform"]:
            raise ValueError("platform_changed_during_rehearsal")
        if restored and request_error is None:
            report.update(status="session_restarted", exit_code=0, phase="complete")
        else:
            report.update(reason=request_error or "new_session_unconfirmed", phase="complete")
    except ERRORS as error:
        service.record_error(report, error)
    finally:
        try:
            trial.save(run, "rehearsal.json", report)
        finally:
            os.close(lock)
    return report


def recover(run, manager, observer=observe):
    """Observe platform recovery after worker exit; never issue a second logout."""
    lock = trial.locked_run(run)
    report = new_report("recovery")
    report.update(status="failed", exit_code=1, phase="checking_worker")
    try:
        if manager.unit_state(UNIT).get("MainPID") != "0":
            raise ValueError("session_worker_still_alive_or_unknown")
        plan = load_plan(run)
        state = trial.read_json(run, "state.json")
        if state.get("unit") != UNIT or type(state.get("session_request_sent")) is not bool:
            raise ValueError("invalid_session_state")
        report["session_request_sent"] = state["session_request_sent"]
        if not state["session_request_sent"]:
            report.update(status="not_needed", exit_code=0, phase="complete")
        else:
            restored = observer(manager, state["baseline"], report,
                lambda: trial.save(run, "recovery.json", report), seconds=30, require_change=False)
            service.capture_absent(Path(plan["home"]), Path(plan["runtime"]), manager)
            if restored:
                report.update(status="runtime_observed", exit_code=0, phase="complete")
            else:
                report.update(reason="platform_recovery_unconfirmed", phase="complete")
    except ERRORS as error:
        service.record_error(report, error)
    finally:
        try:
            trial.save(run, "recovery.json", report)
        finally:
            os.close(lock)
    return report


def launch_command(run):
    python, script = trial.safe_path(sys.executable), trial.safe_path(run / "code/session_rehearsal.py")
    return ["systemd-run", "--user", "--collect", "--no-ask-password", "--unit=" + UNIT,
        "--property=Type=exec", "--property=Slice=app.slice", "--property=RuntimeMaxSec=120",
        "--property=TimeoutStopSec=45", "--property=KillMode=control-group", "--property=Restart=no",
        "--property=UMask=0077", f"--property=ExecStopPost={python} {script} --recover {run}",
        str(python), str(script), "--worker", str(run)]


def result(run):
    report = new_report("result")
    report.update(status="incomplete", run_directory=str(run))
    for name in ("rehearsal", "recovery"):
        if config.read_entry(run, name + ".json") is not None:
            report[name] = trial.read_json(run, name + ".json")
    report["session_request_sent"] = any(
        report.get(name, {}).get("session_request_sent") is True for name in ("rehearsal", "recovery"))
    recovery = report.get("recovery", {})
    if recovery.get("phase") == "complete" or recovery.get("reason"):
        success = (report.get("rehearsal", {}).get("status") == "session_restarted" and
                   recovery.get("status") == "runtime_observed" and recovery.get("normal_runtime_observed") is True)
        report.update(status="completed" if success else "failed", exit_code=0 if success else 1,
                      normal_runtime_observed=recovery.get("normal_runtime_observed", False))
    return report


def latest(parent=RUNS):
    candidates = []
    if parent.exists():
        for path in parent.iterdir():
            if re.fullmatch(r"session-[0-9a-f]{32}", path.name):
                os.close(config.directory(path))
                candidates.append((path.stat().st_mtime_ns, path))
    if not candidates:
        raise ValueError("no_session_rehearsal")
    return result(max(candidates)[1])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--prepare", type=Path, metavar="NEW_BUNDLE")
    mode.add_argument("--check", type=Path, metavar="BUNDLE")
    mode.add_argument("--start", type=Path, metavar="BUNDLE")
    mode.add_argument("--report", type=Path, metavar="RUN")
    mode.add_argument("--report-latest", action="store_true")
    mode.add_argument("--recover", type=Path, metavar="RUN")
    mode.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--allow-session-logout", action="store_true", help="Acknowledge that graphical applications will close")
    parser.add_argument("--wait", action="store_true", help="With --start, wait at most 180 seconds for the saved result")
    args = parser.parse_args(argv)
    report = new_report("launcher")
    try:
        if (args.allow_session_logout or args.wait) and not args.start:
            raise ValueError("logout_and_wait_flags_require_start")
        if args.start and not args.allow_session_logout:
            raise ValueError("explicit_session_logout_acknowledgment_required")
        manager = Systemd()
        home, runtime = Path.home().absolute(), Path(os.environ.get("XDG_RUNTIME_DIR", "/missing-runtime"))
        if args.preflight:
            contract, baseline = preflight(home, runtime, manager)
            report.update(status="preflight_passed", exit_code=0, platform=contract, baseline=baseline)
        elif args.prepare:
            plan = prepare(args.prepare, home, runtime, manager)
            report.update(status="prepared", exit_code=0, bundle=str(args.prepare.absolute()),
                          platform=plan["platform"], helpers=plan["helpers"])
        elif args.check:
            plan, baseline = check(trial.safe_path(args.check), manager)
            report.update(status="prepared_test_ready", exit_code=0, platform=plan["platform"], baseline=baseline)
        elif args.report:
            report = result(trial.safe_path(args.report))
        elif args.report_latest:
            report = latest()
        elif args.worker:
            if not os.environ.get("INVOCATION_ID") or manager.unit_state(UNIT).get("MainPID") != str(os.getpid()):
                raise ValueError("worker_requires_own_systemd_service")
            report = execute(trial.safe_path(args.worker), manager)
        elif args.recover:
            report = recover(trial.safe_path(args.recover), manager)
        elif args.start:
            service.independent_ssh(os.environ.get("SSH_CONNECTION"))
            bundle = trial.safe_path(args.start)
            check(bundle, manager)
            for unit in (UNIT, service.UNIT, trial.UNIT):
                if manager.unit_state(unit).get("LoadState") != "not-found":
                    raise ValueError("existing_trial_unit:" + unit)
            recovery_check = trial.check_recovery(parent=RUNS)
            run = create_run(bundle, manager)
            trial.save(run, "recovery-check.json", recovery_check)
            report.update(status="launch_unconfirmed", run_directory=str(run))
            trial.command(launch_command(run), timeout=10)
            report.update(status="scheduled", exit_code=0, worker_maximum_seconds=120, recovery_maximum_seconds=45)
            if args.wait:
                print("Session rehearsal scheduled; report directory: " + str(run), file=sys.stderr, flush=True)
                end = time.monotonic() + 180
                while time.monotonic() < end:
                    report = result(run)
                    if report["status"] != "incomplete":
                        break
                    time.sleep(1)
    except ERRORS as error:
        unconfirmed = report.get("status") == "launch_unconfirmed"
        service.record_error(report, error)
        if unconfirmed:
            report["status"] = "launch_unconfirmed"
    print(json.dumps(report, indent=2))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
