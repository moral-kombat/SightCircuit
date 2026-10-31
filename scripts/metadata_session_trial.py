"""Prepare an opt-in metadata layer trial across two platform session cycles."""
import argparse
import json
import os
from pathlib import Path
import stat
import sys
import time
import uuid

try:
    from . import session_rehearsal as session
    from .capture_session import process_start
except ImportError:
    import session_rehearsal as session
    from capture_session import process_start

service, trial, config = session.service, session.trial, session.config
metadata = trial.metadata
UNIT = "framejacker-metadata-session.service"
RUNS = config.ROOT / "artifacts/metadata-session-trials"
DROPIN = "systemd/user/steamvr.service.d/90-framejacker-session-metadata.conf"
ENABLE = config.describe(b"[Service]\nEnvironment=FRAMEJACKER_CAPTURE_LAYER=1\n", 0o600)
SOURCES = ("metadata_session_trial.py", *session.SOURCES)
ERRORS = session.ERRORS


def proc_bytes(pid, name, limit):
    root = Path("/proc") / str(pid)
    if root.stat().st_uid != os.geteuid():
        raise ValueError("process_owner_changed")
    with (root / name).open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("process_metadata_limit")
    return data


class Systemd(session.Systemd):
    def compositor_flags(self, snapshot, enabled):
        for peer in snapshot["runtime"]["compositors"]:
            # Values outside these two names are never retained or reported.
            selected = {}
            for item in proc_bytes(peer["pid"], "environ", 2 * 1024 * 1024).split(b"\0"):
                name, _, value = item.partition(b"=")
                if name in (b"FRAMEJACKER_CAPTURE_LAYER", b"FRAMEJACKER_DISABLE_CAPTURE_LAYER"):
                    selected[name] = value
            expected = {b"FRAMEJACKER_CAPTURE_LAYER": b"1"} if enabled else {}
            if selected != expected or process_start(peer["pid"]) != peer["start_ticks"]:
                raise ValueError("unexpected_compositor_capture_environment")

    def mapped(self, peer, library):
        info = library.stat()
        found = False
        for line in proc_bytes(peer["pid"], "maps", 8 * 1024 * 1024).splitlines():
            fields = line.split(maxsplit=5)
            if len(fields) < 5:
                raise ValueError("invalid_process_mapping")
            major, minor = (int(value, 16) for value in fields[3].split(b":"))
            if int(fields[4]) == info.st_ino and os.makedev(major, minor) == info.st_dev:
                found = True
        if process_start(peer["pid"]) != peer["start_ticks"]:
            raise ValueError("compositor_identity_changed")
        return found

    def provider_identity(self, runtime, snapshot, library):
        directory = runtime / "framejacker-camera-provider"
        entry = config.read_entry(directory, "provider.lock")
        if entry is None:
            return None
        lease = json.loads(config.payload(entry))
        peer = next((p for p in snapshot["runtime"]["compositors"]
                     if p["pid"] == lease.get("pid") and p["start_ticks"] == lease.get("start")), None)
        if peer is None or not self.mapped(peer, library):
            raise ValueError("provider_not_owned_by_trial_compositor")
        endpoint = (directory / "camera.sock").lstat()
        if (not stat.S_ISSOCK(endpoint.st_mode) or endpoint.st_uid != os.geteuid() or
                stat.S_IMODE(endpoint.st_mode) != 0o600 or endpoint.st_nlink != 1 or
                (endpoint.st_dev, endpoint.st_ino) != (lease.get("device"), lease.get("inode"))):
            raise ValueError("provider_endpoint_identity_changed")
        return {key: lease[key] for key in ("pid", "start", "device", "inode")}

    def inspect(self, run, runtime, *, timeout=5):
        return metadata.inspect(binary=run / "inspector", runtime=runtime, seconds=2, timeout=timeout)

    def verify_peer(self, native, snapshot, library, owner):
        if (owner is None or native.get("peer_pid") != owner["pid"] or
                not trial.peer_is_compositor(native, snapshot["units"][trial.SERVICE])):
            raise ValueError("metadata_peer_not_trial_compositor")
        peer = next((p for p in snapshot["runtime"]["compositors"]
                     if p["pid"] == owner["pid"] and p["start_ticks"] == owner["start"]), None)
        if peer is None or not self.mapped(peer, library):
            raise ValueError("metadata_peer_generation_changed")

    def clean_provider(self, runtime, owner):
        endpoint = runtime / "framejacker-camera-provider/camera.sock"
        if not endpoint.exists() and not endpoint.is_symlink():
            return
        if owner is None:
            raise ValueError("unidentified_provider_endpoint_preserved")
        entry = config.read_entry(endpoint.parent, "provider.lock")
        lease = json.loads(config.payload(entry)) if entry else {}
        if any(lease.get(key) != value for key, value in owner.items()):
            raise ValueError("provider_lease_replaced")
        config.recover_provider(runtime)  # Checks dead PID/start, lock and exact socket identity.

    def provider_dead(self, owner):
        if owner is None:
            return True
        try:
            return process_start(owner["pid"]) != owner["start"]
        except ProcessLookupError:
            return True


def absent(home, runtime):
    if config.read_entry(runtime, DROPIN) is not None:
        raise ValueError("existing_metadata_session_dropin")
    if config.read_entry(home / ".config", DROPIN) is not None:
        raise ValueError("persistent_metadata_session_dropin")


def baseline(home, runtime, manager):
    absent(home, runtime)
    platform, snapshot = session.preflight(home, runtime, manager)
    manager.compositor_flags(snapshot, False)
    return platform, snapshot


def prepare(destination, metadata_bundle, proof_run, manager):
    home = Path.home().absolute()
    runtime = trial.safe_path(os.environ.get("XDG_RUNTIME_DIR", "/missing-runtime"))
    platform, snapshot = baseline(home, runtime, manager)
    proof = session.result(proof_run)
    proof_plan = session.load_plan(proof_run)
    if proof.get("status") != "completed" or proof_plan["platform"] != platform:
        raise ValueError("matching_completed_session_proof_required")
    source = trial.verify_bundle(metadata_bundle, home)
    library = source["after"][config.LIBRARY]
    inspector = config.read_entry(metadata.BINARY.parent, metadata.BINARY.name)
    if inspector is None or config.payload(inspector)[:4] != b"\x7fELF":
        raise ValueError("metadata_inspector_not_built")
    destination = trial.safe_path(destination)
    destination.mkdir(mode=0o700)
    helpers = {}
    for name in SOURCES:
        entry = config.describe(Path(__file__).with_name(name).read_bytes(), 0o600)
        helpers[name] = entry["sha256"]
        config.write_entry(destination, "code/" + name, entry)
    config.write_entry(destination, "library.so", library)
    inspector = config.describe(config.payload(inspector), 0o700)
    config.write_entry(destination, "inspector", inspector)
    plan = {"schema_version": 1, "kind": "metadata_session_preparation", "home": str(home),
        "runtime": str(runtime), "platform": platform, "baseline": snapshot, "helpers": helpers,
        "library": {"sha256": library["sha256"], "mode": library["mode"]},
        "inspector": {"sha256": inspector["sha256"], "mode": inspector["mode"]},
        "manifest_template": json.loads(config.payload(source["after"][config.MANIFEST])),
        "metadata_only": True, "image_access_enabled": False, "provider_protocol_version": 3,
        "session_proof": str(proof_run)}
    trial.save(destination, "trial-plan.json", plan)
    trial.save(destination, "session-proof.json", proof)
    return plan


def load(run, verify_files=True):
    plan = trial.read_json(run, "trial-plan.json")
    if (plan.get("schema_version") != 1 or plan.get("kind") != "metadata_session_preparation" or
            plan.get("home") != str(Path.home().absolute()) or plan.get("runtime") != os.environ.get("XDG_RUNTIME_DIR") or
            plan.get("metadata_only") is not True or plan.get("image_access_enabled") is not False or
            plan.get("provider_protocol_version") != 3 or set(plan.get("helpers", {})) != set(SOURCES)):
        raise ValueError("invalid_metadata_session_plan")
    layer = plan["manifest_template"]["layer"]
    if (layer.get("name") != "VK_LAYER_FRAMEJACKER_capture" or layer.get("implementation_version") != "3" or
            layer.get("enable_environment") != {"FRAMEJACKER_CAPTURE_LAYER": "1"} or
            layer.get("disable_environment") != {"FRAMEJACKER_DISABLE_CAPTURE_LAYER": "1"}):
        raise ValueError("invalid_metadata_manifest")
    if verify_files:
        for name, expected in plan["helpers"].items():
            entry = config.read_entry(run, "code/" + name)
            if entry is None or entry["sha256"] != expected:
                raise ValueError("metadata_helper_changed:" + name)
        for name, key in (("library.so", "library"), ("inspector", "inspector")):
            entry = config.read_entry(run, name)
            if entry is None or any(entry[field] != plan[key][field] for field in ("sha256", "mode")):
                raise ValueError("metadata_binary_changed:" + name)
    return plan


def check(bundle, manager):
    plan = load(bundle)
    platform, snapshot = baseline(Path(plan["home"]), Path(plan["runtime"]), manager)
    if platform != plan["platform"]:
        raise ValueError("platform_changed_since_preparation")
    return plan, snapshot


def create_run(bundle, manager, parent=RUNS):
    plan, snapshot = check(bundle, manager)
    parent = trial.safe_path(parent)
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.close(config.directory(parent))
    run = parent / ("metadata-" + uuid.uuid4().hex)
    run.mkdir(mode=0o700)
    for name in ("library.so", "inspector", *("code/" + item for item in SOURCES)):
        config.write_entry(run, name, config.read_entry(bundle, name))
    trial.save(run, "trial-plan.json", plan)
    trial.save(run, "state.json", {"unit": UNIT, "baseline": snapshot, "changes_started": False,
        "startup_may_be_enabled": False, "activation_requested": False, "cleanup_requested": False,
        "provider": None})
    return run


def state_for(run):
    state = trial.read_json(run, "state.json")
    if state.get("unit") != UNIT or any(type(state.get(key)) is not bool for key in
            ("changes_started", "startup_may_be_enabled", "activation_requested", "cleanup_requested")):
        raise ValueError("invalid_metadata_session_state")
    return state


def manifest(run, plan):
    document = json.loads(json.dumps(plan["manifest_template"]))
    document["layer"]["library_path"] = str(run / "library.so")
    return config.describe((json.dumps(document, indent=2) + "\n").encode(), 0o600)


def owned_targets(run, plan):
    return ((Path(plan["home"]), config.MANIFEST, manifest(run, plan)),
            (Path(plan["runtime"]), DROPIN, ENABLE))


def verify_targets(run, plan, installed):
    for root, name, entry in owned_targets(run, plan):
        if config.read_entry(root, name) != (entry if installed else None):
            raise ValueError("metadata_target_changed:" + name)


def new_report(kind):
    return {"report_kind": "metadata_session_" + kind, "schema_version": 1, "status": "not_run", "exit_code": 3,
        "metadata_only": True, "image_data_accessed": False, "activation_requested": False,
        "configuration_restored": False, "normal_runtime_observed": False,
        "visual_confirmation_required": True}


def wait_provider(manager, runtime, snapshot, library, seconds=8, clock=time.monotonic, pause=time.sleep):
    end = clock() + seconds
    while clock() < end:
        current = manager.snapshot(timeout=min(4, end - clock()))
        if not session.healthy(current) or session.identity(current) != session.identity(snapshot):
            raise ValueError("session_changed_while_waiting_for_provider")
        try:
            owner = manager.provider_identity(runtime, current, library)
        except FileNotFoundError:
            owner = None  # The lease/socket may still be appearing during startup.
        if owner is not None and clock() < end:
            return owner
        pause(min(0.1, max(0, end - clock())))
    raise ValueError("metadata_provider_startup_deadline")


def inspect_settled(run, manager, runtime, snapshot, owner, clock=time.monotonic, pause=time.sleep):
    """Retry only clean registry invalidations, never a new session/provider."""
    end = clock() + 12
    history, epoch, generation, last = [], None, None, None
    summary = {"status": "settling", "maximum_attempts": 4, "budget_seconds": 12,
               "required_stable_seconds": 2, "registry_changes_observed": False, "attempts": history}

    def finish(status, reason=None):
        summary["status"] = status
        if reason:
            summary["reason"] = reason
        trial.save(run, "inspection-settling.json", summary)
        result = dict(last or metadata.new_report())
        result["settling"] = summary
        if status != "stable":
            result.update(status="inspection_failed", exit_code=1, reason=reason or "metadata_inspection_failed")
        return result

    try:
        for attempt in range(1, 5):
            if end - clock() < 3:  # Preserve the full two-second check plus bounded overhead.
                return finish("exhausted", "metadata_settling_deadline")
            current = manager.snapshot(timeout=min(4, end - clock()))
            if not session.healthy(current) or session.identity(current) != session.identity(snapshot):
                return finish("failed", "session_changed_during_settling")
            manager.compositor_flags(current, True)
            if manager.provider_identity(runtime, current, run / "library.so") != owner:
                return finish("failed", "provider_identity_changed_during_settling")
            if end - clock() < 3:
                return finish("exhausted", "metadata_settling_deadline")
            last = manager.inspect(run, runtime, timeout=min(4, end - clock()))
            # Preserve every full report; a later stable result never overwrites an invalidated snapshot.
            trial.save(run, f"inspection-attempt-{attempt}.json", last)
            native = last.get("native", {})
            history.append({"attempt": attempt, "status": last.get("status"),
                "reason": native.get("reason") or last.get("reason"),
                "provider_epoch": native.get("provider_epoch"), "provider_generation": native.get("provider_generation"),
                "record_count": last.get("record_count"), "reviewed_profile_candidates": last.get("reviewed_profile_candidates"),
                "elapsed_ms": last.get("elapsed_ms")})
            trial.save(run, "inspection-settling.json", summary)
            if clock() >= end:
                return finish("exhausted", "metadata_settling_deadline")
            clean = (last.get("cleanup_ok") is True and last.get("worker_reaped") is True and
                     last.get("worker_forced_termination") is False and native.get("lease_established") is True)
            if not clean:
                return finish("failed", "metadata_inspection_not_clean")
            manager.verify_peer(native, current, run / "library.so", owner)
            current_epoch, current_generation = native.get("provider_epoch"), native.get("provider_generation")
            if (not isinstance(current_epoch, str) or not session.re.fullmatch(r"[0-9a-f]{1,16}", current_epoch) or
                    int(current_epoch, 16) == 0 or type(current_generation) is not int or current_generation < 1):
                return finish("failed", "invalid_provider_generation")
            if epoch is not None and (current_epoch != epoch or current_generation < generation):
                return finish("failed", "provider_generation_changed_during_settling")
            epoch, generation = current_epoch, current_generation
            if (last.get("status") == "inspected" and last.get("exit_code") == 0 and
                    native.get("registry_unchanged") is True):
                return finish("stable")
            retryable = (last.get("status") == "inspection_failed" and last.get("exit_code") == 1 and
                         native.get("reason") == "provider_registry_changed" and native.get("registry_unchanged") is False)
            if not retryable:
                return finish("failed", "metadata_inspection_failed")
            summary["registry_changes_observed"] = True
            if attempt < 4:
                pause(min(float(attempt), max(0, end - clock())))
        return finish("exhausted", "metadata_registry_did_not_settle")
    except ERRORS as error:
        return finish("failed", str(error))


def execute(run, manager, observer=session.observe):
    lock = trial.locked_run(run)
    report = new_report("trial")
    report.update(status="failed", exit_code=1, phase="preflight")
    try:
        plan, snapshot = check(run, manager)
        state = state_for(run)
        if state["changes_started"] or session.identity(state["baseline"]) != session.identity(snapshot):
            raise ValueError("metadata_trial_started_or_baseline_changed")
        home, runtime = Path(plan["home"]), Path(plan["runtime"])
        state["changes_started"] = True
        trial.save(run, "state.json", state)  # Arm recovery before the first visible file.
        report["phase"] = "staging_manifest"
        trial.save(run, "trial.json", report)
        verify_targets(run, plan, False)
        config.write_entry(home, config.MANIFEST, manifest(run, plan))
        report["phase"] = "enabling_next_service_start"
        state["startup_may_be_enabled"] = True
        trial.save(run, "state.json", state)
        if config.read_entry(runtime, DROPIN) is not None:
            raise ValueError("metadata_dropin_changed")
        config.write_entry(runtime, DROPIN, ENABLE)
        manager.reload(timeout=5)
        verify_targets(run, plan, True)
        report["phase"] = "requesting_metadata_session"
        state["activation_requested"] = report["activation_requested"] = True
        trial.save(run, "state.json", state)
        trial.save(run, "trial.json", report)
        manager.switch_to_game(timeout=10)
        report["request_status"] = "acknowledged"
        observed = observer(manager, snapshot, report, lambda: trial.save(run, "trial.json", report), seconds=75)
        report["metadata_runtime_observed"] = report["normal_runtime_observed"]
        report["normal_runtime_observed"] = False  # Reserved for the restored, metadata-free session.
        if not observed:
            raise ValueError("metadata_session_unconfirmed")
        snapshot = report["last_observation"]
        manager.compositor_flags(snapshot, True)
        verify_targets(run, plan, True)
        state["provider"] = wait_provider(manager, runtime, snapshot, run / "library.so")
        trial.save(run, "state.json", state)
        report["phase"] = "inspecting_metadata"
        inspected = inspect_settled(run, manager, runtime, snapshot, state["provider"])
        trial.save(run, "inspection.json", inspected)
        report["inspection_attempts"] = len(inspected.get("settling", {}).get("attempts", []))
        report["registry_changes_observed"] = inspected.get("settling", {}).get("registry_changes_observed", False)
        if inspected.get("status") != "inspected" or inspected.get("exit_code") != 0 or inspected.get("cleanup_ok") is not True:
            raise ValueError("metadata_inspection_failed")
        after = manager.snapshot()
        if not session.healthy(after) or session.identity(snapshot) != session.identity(after):
            raise ValueError("session_changed_during_inspection")
        manager.verify_peer(inspected["native"], after, run / "library.so", state["provider"])
        verify_targets(run, plan, True)
        report.update(status="inspected", exit_code=0, phase="complete", record_count=inspected["record_count"],
                      reviewed_profile_candidates=inspected["reviewed_profile_candidates"])
    except ERRORS as error:
        service.record_error(report, error)
    finally:
        try:
            trial.save(run, "trial.json", report)
        finally:
            os.close(lock)
    return report


def recover(run, manager, observer=session.observe):
    lock = trial.locked_run(run)
    report = new_report("recovery")
    report.update(status="failed", exit_code=1, phase="checking_worker", errors=[])
    try:
        if manager.unit_state(UNIT).get("MainPID") != "0":
            raise ValueError("metadata_worker_still_alive_or_unknown")
        # Disabling future starts must remain possible after source/binary drift.
        plan = load(run, verify_files=False)
        state = state_for(run)
        home, runtime = Path(plan["home"]), Path(plan["runtime"])
        if not state["changes_started"]:
            report.update(status="not_needed", exit_code=0, phase="complete")
            return report
        if state["provider"] is None:
            try:
                state["provider"] = manager.provider_identity(runtime, manager.snapshot(), run / "library.so")
                trial.save(run, "state.json", state)
            except ERRORS:
                pass  # Unknown endpoints will be preserved, never guessed away.
        report["phase"] = "disabling_future_starts"
        for root, name, expected in reversed(owned_targets(run, plan)):
            try:
                current = config.read_entry(root, name)
                if current is not None:
                    if current != expected:
                        raise ValueError("foreign_metadata_target_preserved:" + name)
                    config.write_entry(root, name, None)
            except ERRORS as error:
                report["errors"].append(str(error))
        try:
            manager.reload(timeout=5)
        except ERRORS as error:
            report["errors"].append(str(error))
        verify_targets(run, plan, False)
        report["configuration_restored"] = True
        trial.save(run, "recovery.json", report)
        if report["errors"]:
            raise ValueError("metadata_configuration_recovery_failed")
        if manager.platform_contract() != plan["platform"]:
            raise ValueError("platform_changed_before_recovery_request")
        if state["startup_may_be_enabled"]:
            report["phase"] = "waiting_before_normal_session"
            if state["cleanup_requested"]:
                before = state["cleanup_baseline"]
            else:
                ready = {}
                if not observer(manager, state["baseline"], ready,
                        lambda: trial.save(run, "recovery-observation.json", ready), seconds=30, require_change=False):
                    raise ValueError("session_unready_for_recovery_request")
                before = ready["last_observation"]
                state.update(cleanup_requested=True, cleanup_baseline=before)
                trial.save(run, "state.json", state)  # Never repeat an ambiguous request.
                report["phase"] = "requesting_normal_session"
                trial.save(run, "recovery.json", report)
                try:
                    manager.switch_to_game(timeout=10)
                    state["cleanup_request_status"] = "acknowledged"
                except ERRORS as error:
                    state["cleanup_request_status"] = "unconfirmed"
                    state["cleanup_request_error"] = str(error)
                    if hasattr(error, "details"):
                        state["cleanup_command_error"] = error.details
                trial.save(run, "state.json", state)
            restored = observer(manager, before, report, lambda: trial.save(run, "recovery.json", report), seconds=75)
        else:
            restored = observer(manager, state["baseline"], report,
                lambda: trial.save(run, "recovery.json", report), seconds=15, require_change=False)
        if not restored:
            raise ValueError("normal_metadata_free_session_unconfirmed")
        manager.compositor_flags(report["last_observation"], False)
        for peer in report["last_observation"]["runtime"]["compositors"]:
            if manager.mapped(peer, run / "library.so"):
                raise ValueError("metadata_library_still_mapped_in_compositor")
        if not manager.provider_dead(state["provider"]):
            raise ValueError("metadata_provider_still_alive")
        manager.clean_provider(runtime, state["provider"])
        service.capture_absent(home, runtime, manager)
        absent(home, runtime)
        report["layer_absent_from_compositor"] = True
        retained = config.read_entry(run, "library.so")
        if retained is None or retained["sha256"] != plan["library"]["sha256"]:
            raise ValueError("retained_trial_library_changed")
        if state.get("cleanup_request_status") == "unconfirmed":
            raise ValueError("recovery_request_unconfirmed:" + state.get("cleanup_request_error", "unknown"))
        report.update(status="restored", exit_code=0, phase="complete")
    except ERRORS as error:
        service.record_error(report, error)
    finally:
        try:
            trial.save(run, "recovery.json", report)
        finally:
            os.close(lock)
    return report


def launch_command(run):
    python, script = trial.safe_path(sys.executable), trial.safe_path(run / "code/metadata_session_trial.py")
    return ["systemd-run", "--user", "--collect", "--no-ask-password", "--unit=" + UNIT,
        "--property=Type=exec", "--property=Slice=app.slice", "--property=RuntimeMaxSec=150",
        "--property=TimeoutStopSec=180", "--property=KillMode=control-group", "--property=Restart=no",
        "--property=UMask=0077", f"--property=ExecStopPost={python} {script} --recover {run}",
        str(python), str(script), "--worker", str(run)]


def result(run):
    report = new_report("result")
    report.update(status="incomplete", run_directory=str(run))
    for name in ("trial", "inspection", "recovery"):
        if config.read_entry(run, name + ".json") is not None:
            report[name] = trial.read_json(run, name + ".json")
    report["activation_requested"] = report.get("trial", {}).get("activation_requested", False)
    recovery = report.get("recovery", {})
    if recovery.get("phase") == "complete" or recovery.get("reason"):
        good = (report.get("trial", {}).get("status") == "inspected" and recovery.get("status") == "restored"
            and recovery.get("configuration_restored") is True and recovery.get("normal_runtime_observed") is True
            and recovery.get("layer_absent_from_compositor") is True)
        report.update(status="completed" if good else "failed", exit_code=0 if good else 1,
                      configuration_restored=recovery.get("configuration_restored", False),
                      normal_runtime_observed=recovery.get("normal_runtime_observed", False))
    return report


def latest(parent=RUNS):
    candidates = []
    if parent.exists():
        for path in parent.iterdir():
            if session.re.fullmatch(r"metadata-[0-9a-f]{32}", path.name):
                os.close(config.directory(path))
                candidates.append((path.stat().st_mtime_ns, path))
    if not candidates:
        raise ValueError("no_metadata_session_trial")
    return result(max(candidates)[1])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--prepare", type=Path)
    mode.add_argument("--check", type=Path)
    mode.add_argument("--start", type=Path)
    mode.add_argument("--report", type=Path)
    mode.add_argument("--report-latest", action="store_true")
    mode.add_argument("--recover", type=Path)
    mode.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--metadata-bundle", type=Path)
    parser.add_argument("--session-proof", type=Path)
    parser.add_argument("--allow-session-logouts", action="store_true")
    parser.add_argument("--wait", action="store_true")
    args = parser.parse_args(argv)
    report = new_report("launcher")
    try:
        if (args.allow_session_logouts or args.wait) and not args.start:
            raise ValueError("logout_flags_require_start")
        if args.start and not args.allow_session_logouts:
            raise ValueError("explicit_two_session_logout_acknowledgment_required")
        if (args.metadata_bundle or args.session_proof) and not args.prepare:
            raise ValueError("bundle_and_proof_options_require_prepare")
        manager = Systemd()
        if args.prepare:
            if not args.metadata_bundle or not args.session_proof:
                raise ValueError("metadata_bundle_and_completed_session_proof_required")
            prepare(args.prepare, trial.safe_path(args.metadata_bundle), trial.safe_path(args.session_proof), manager)
            report.update(status="prepared_inactive", exit_code=0, bundle=str(args.prepare.absolute()))
        elif args.check:
            _, snapshot = check(trial.safe_path(args.check), manager)
            report.update(status="metadata_session_test_ready", exit_code=0, baseline=snapshot)
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
            for unit in (UNIT, session.UNIT, service.UNIT, trial.UNIT):
                if manager.unit_state(unit).get("LoadState") != "not-found":
                    raise ValueError("existing_trial_unit:" + unit)
            cleanup_check = trial.check_recovery(parent=RUNS)
            run = create_run(bundle, manager)
            trial.save(run, "recovery-check.json", cleanup_check)
            report.update(status="launch_unconfirmed", run_directory=str(run))
            trial.command(launch_command(run), timeout=10)
            report.update(status="scheduled", exit_code=0)
            if args.wait:
                print("Metadata trial scheduled; report directory: " + str(run), file=sys.stderr, flush=True)
                end = time.monotonic() + 360
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
