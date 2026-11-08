"""The active commands' bodies (decision D5, 2026-11-07).

Each module here is one documented command: ``main(argv=None)`` parses the
arguments and returns the exit code. ``scripts/<name>.py`` is the thin wrapper
that runs it from an uninstalled checkout (the Frame), and ``pyproject.toml``
declares ``sightcircuit-<name>`` console entry points for an installed package.
Commands that re-spawn a worker do so by their own file path, which works
uninstalled and installed because the package sits beside this file. The
paused capture-provider scripts stay in ``scripts/`` and have no entry point.
"""

COMMANDS = (
    "replay_window",
    "mirror_recognize",
    "live_pipeline",
    "hud_marker_check",
    "hud_marker_observer",
    "hud_validation_report",
    "evaluate_detectors",
    "generate_status",
    "mirror_check",
    "frame_session_manifest",
    "annotate_boxes",
    "agent_enforcement_check",
    "fallback_measure",
)
