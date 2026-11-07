"""The fixed table of tasks an on-device agent may request by ID.

Only Tier A tasks run. Tier B and C IDs exist so a request for them gets a
specific refusal instead of "unknown task". Argument vectors are fixed here:
the runner never accepts free-form arguments, so opt-in flags such as
``--allow-*`` or ``--start`` cannot reach a command through it.

Placeholders in argument vectors, substituted by the runner:
``{venv_python}`` the replay environment's interpreter, ``{run_dir}`` the task's
own output directory.
"""
from dataclasses import dataclass, field

VENV_PYTHON = ".venv-replay/bin/python"
OFFSCREEN = {"QT_QPA_PLATFORM": "offscreen"}


@dataclass(frozen=True)
class Task:
    id: str
    tier: str
    description: str
    argv: tuple = ()
    timeout: int = 0
    env: dict = field(default_factory=dict)
    requires: tuple = ()  # repository-relative paths that must exist first
    stop_on_failure: bool = False  # later tasks in the same run are not started
    refusal: str = ""

    @property
    def runnable(self):
        return self.tier == "A" and not self.refusal


def _a(task_id, description, argv, timeout, **extra):
    return Task(id=f"tier-a/{task_id}", tier="A", description=description, argv=tuple(argv),
                timeout=timeout, **extra)


WEARER_REFUSAL = ("needs a person wearing the headset and their approval; "
                  "not enabled until Phase 4 of docs/agent-on-frame.md")
NEVER_REFUSAL = ("never run by an agent: a person runs it from SSH on another computer "
                 "after separate review and approval (docs/on-device.md)")

TASKS = (
    _a("setup", "Create or update .venv-replay (pinned wheels only)",
       ["bash", "scripts/setup_window.sh"], 1200, stop_on_failure=True),
    _a("inventory", "Read-only device inventory", ["python3", "scripts/device_check.py"], 30),
    _a("recording-metadata", "MP4 header report for the newest Arcturus recording",
       ["python3", "scripts/recording_check.py"], 60),
    _a("runtime-status", "SteamVR service/cgroup metadata and compositor liveness; no restart",
       ["python3", "scripts/capture_layer_trial.py", "--runtime-status"], 30),
    _a("service-preflight", "Read-only service-rehearsal preflight (expects the session-coupling guard)",
       ["python3", "scripts/service_rehearsal.py", "--preflight"], 30),
    _a("service-report", "Read the last saved service rehearsal result",
       ["python3", "scripts/service_rehearsal.py", "--report-latest"], 30),
    _a("build-bridge", "Build the native overlay bridge on this machine",
       ["bash", "scripts/build_overlay_bridge.sh"], 600),
    _a("build-shm-reader", "Build the isolated shared-memory reader",
       ["bash", "scripts/build_shm_reader.sh"], 600),
    _a("unit-tests", "Full offline unit suite, Qt offscreen",
       ["{venv_python}", "-m", "unittest", "discover", "-s", "tests"], 1800,
       env=OFFSCREEN, requires=(VENV_PYTHON,)),
    _a("marker-validate", "Calibration marker validation mode; generated marker, never initializes VR",
       ["{venv_python}", "scripts/hud_marker_check.py", "--validate", "--seconds", "3"], 60,
       requires=(VENV_PYTHON, "artifacts/overlay-bridge/sightcircuit-overlay-bridge")),
    _a("replay-regression", "Offscreen replay of the newest recording, 30 frames",
       ["{venv_python}", "scripts/replay_window.py", "--max-frames", "30",
        "--report", "{run_dir}/replay-offscreen.json"], 300, env=OFFSCREEN, requires=(VENV_PYTHON,)),
    _a("replay-pacing", "Offscreen realtime replay pacing, 200 frames, 2 decoder threads",
       ["{venv_python}", "scripts/replay_window.py", "--realtime", "--max-frames", "200",
        "--report", "{run_dir}/replay-realtime.json"], 300, env=OFFSCREEN, requires=(VENV_PYTHON,)),
    _a("replay-pacing-4threads", "Offscreen realtime replay pacing, 200 frames, 4 decoder threads",
       ["{venv_python}", "scripts/replay_window.py", "--realtime", "--decode-threads", "4",
        "--max-frames", "200", "--report", "{run_dir}/replay-realtime-4threads.json"], 300,
       env=OFFSCREEN, requires=(VENV_PYTHON,)),
    _a("evaluate-local", "Detector comparison on existing private Frame stills",
       ["{venv_python}", "scripts/evaluate_detectors.py", "--manifest", "evaluation/local/frame.json",
        "--report", "{run_dir}/detector-report.json"], 900,
       requires=(VENV_PYTHON, "evaluation/local/frame.json")),
    Task("tier-b/mirror", "B", "Bounded mirror recognition (frame_session.sh mirror)", refusal=WEARER_REFUSAL),
    Task("tier-b/stills", "B", "Labelled evaluation stills from the mirror", refusal=WEARER_REFUSAL),
    Task("tier-b/overlay", "B", "Replay with the head-relative VR panel", refusal=WEARER_REFUSAL),
    Task("tier-b/window-mirror", "B", "Desktop window in the SteamVR mirror", refusal=WEARER_REFUSAL),
    Task("tier-c/camera-probe", "C", "Public OpenVR camera probe (interrupted passthrough, E3)",
         refusal=NEVER_REFUSAL),
    Task("tier-c/session-rehearsal", "C", "Platform session cycle (logs out the graphical session)",
         refusal=NEVER_REFUSAL),
    Task("tier-c/metadata-session-trial", "C", "Metadata layer trial across two session logouts",
         refusal=NEVER_REFUSAL),
    Task("tier-c/service-rehearsal", "C", "SteamVR stop/restart rehearsal (stopped the session, E63)",
         refusal=NEVER_REFUSAL),
    Task("tier-c/capture-layer-trial", "C", "Capture layer activation and rollback", refusal=NEVER_REFUSAL),
    Task("tier-c/hud-calibration", "C", "Marker/observer calibration with --allow-* opt-ins",
         refusal=NEVER_REFUSAL),
)

TASKS_BY_ID = {task.id: task for task in TASKS}
TIER_A = tuple(task for task in TASKS if task.runnable)
