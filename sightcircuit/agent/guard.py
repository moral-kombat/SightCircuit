"""PreToolUse guard for a Claude Code session running on Frame.

Claude Code passes each tool call as JSON on stdin. Exit 0 allows it; exit 2
blocks it and returns the stderr reason to the agent. The guard fails closed:
unreadable input, and any error while deciding, block the call. Claude Code
treats every other exit status as a non-blocking error, so the hook command in
``frame-settings.json`` also maps an interpreter failure to exit 2.

Policy, deliberately narrower than the permission rules it backs up:

- Bash and Monitor: only ``python3 scripts/frame_agent_run.py`` with plain word
  arguments (no shell syntax at all). Everything else is blocked, with a
  specific reason for commands known to be unsafe on the headset.
- Read/Grep/Glob: the file read, and the directory searched (Grep/Glob
  ``path``, plus the literal leading directories of a Glob ``pattern``), only
  inside the checkout, never in the private image, recording and calibration
  directories, and never an image or video file (room images stay on Frame).
  A recursive search from inside the checkout can still descend into the
  private directories; for that the guard relies on the settings' Read deny
  rules, which Claude Code applies to search results.
- Write/Edit/MultiEdit/NotebookEdit: only a draft ``summary.md`` directly in a
  run directory the runner created, ``artifacts/agent-runs/<run>/summary.md``;
  the runner's own ``result.json``, task output and lock are not agent-writable.
- Paths are refused, not reinterpreted, when Claude Code would resolve them
  differently from a plain path: leading or trailing white space (it trims),
  a leading ``~`` (it expands ``~`` and ``~/``) and, in a Glob pattern, ``..``.
- WebFetch/WebSearch: blocked.
- Other tools are not routed here (permission rules decide them).
"""
import json
import os
from pathlib import Path
import re
import shlex
import sys

# Raised whenever the policy gets stricter; ``frame_agent_run.py update`` refuses
# a revision whose guard is older than the running one.
GUARD_VERSION = 3

RUNNER = "scripts/frame_agent_run.py"
SHELL_SYNTAX = re.compile(r"[;&|`$<>(){}\[\]*?~!#\\\n\r'\"]")
PLAIN_WORD = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
SHELL_TOOLS = {"Bash", "Monitor"}
READ_TOOLS = {"Read", "Grep", "Glob"}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
BLOCKED_TOOLS = {"WebFetch", "WebSearch"}
AGENT_RUNS = Path("artifacts/agent-runs")
SUMMARY = "summary.md"
# The runner's run directory names: "<UTC time>-<label>", e.g. 20261102T120000Z-tier-a (runner.new_run_dir).
RUN_NAME = re.compile(r"[0-9]{8}T[0-9]{6}Z-[A-Za-z0-9][A-Za-z0-9._-]*")
# Claude Code trims a path (JavaScript trim(), which also removes U+FEFF) and expands a leading "~".
AMBIGUOUS = re.compile(r"\A(~|[\s\ufeff])|[\s\ufeff]\Z")
# Where Claude Code ends the literal part of a Glob pattern.
GLOB_CHARS = re.compile(r"[*?\[{]")
UNRESOLVABLE = "unresolvable path (use a plain path in the checkout: no leading '~', no surrounding spaces)"
PRIVATE_DIRS = (Path("evaluation/local"), Path("recordings"), Path("calibration"))
MEDIA = re.compile(r"\.(png|jpe?g|gif|webp|bmp|tiff?|heic|avif|mp4|mkv|mov|webm|avi)$", re.IGNORECASE)
KNOWN_UNSAFE = (
    (re.compile(r"--allow-"), "opt-in --allow-* flags are for a person, never the agent"),
    (re.compile(r"camera_probe"), "the public camera probe interrupted passthrough (E3)"),
    (re.compile(r"session_rehearsal|metadata_session_trial"), "session tests log out the graphical session"),
    (re.compile(r"service_rehearsal\S*\s+--(start|recover)"), "stopping SteamVR also stopped the session (E63)"),
    (re.compile(r"capture_layer_trial\S*\s+--(start|rollback)"), "capture layer activation is not agent work"),
    (re.compile(r"\b(systemctl|steamosctl|steamos-readonly|sudo|pacman|reboot|shutdown)\b"),
     "service, package and system changes are not agent work"),
    (re.compile(r"\bgit\s+push\b"), "the agent does not push"),
)


def block(reason):
    return 2, f"Blocked by sightcircuit agent guard: {reason}. Use 'python3 {RUNNER} list' for allowed tasks."


def check_bash(command):
    if not isinstance(command, str) or not command.strip():
        return block("empty or malformed command")
    for pattern, reason in KNOWN_UNSAFE:
        if pattern.search(command):
            return block(reason)
    if SHELL_SYNTAX.search(command):
        return block("shell syntax (chaining, substitution, redirection, quoting or globbing) is not allowed")
    words = shlex.split(command)
    if words[:2] != ["python3", RUNNER]:
        return block(f"the only allowed command is 'python3 {RUNNER} ...'")
    if not all(PLAIN_WORD.match(word) for word in words[2:]):
        return block("runner arguments must be plain task IDs or words")
    return 0, ""


def resolved(path, root):
    """``path`` (absolute or relative to ``root``) after symlinks, or None when it cannot be resolved.

    A path that Claude Code would resolve differently (see ``AMBIGUOUS``) is not resolved at all.
    """
    if AMBIGUOUS.search(path):
        return None
    target = Path(path) if os.path.isabs(path) else root / path
    try:
        return target.resolve()
    except (OSError, RuntimeError, ValueError):  # ValueError: NUL bytes, lone surrogates
        return None


def check_read(path, root):
    if not isinstance(path, str) or not path:
        return block("missing or malformed path")
    target = resolved(path, root)
    if target is None:
        return block(UNRESOLVABLE)
    if target != root and root not in target.parents:
        return block("files outside the checkout are not read by the agent")
    relative = target.relative_to(root)
    if any(relative == private or private in relative.parents for private in PRIVATE_DIRS):
        return block("private stills, recordings and calibration data stay on Frame")
    if MEDIA.search(target.name):
        return block("images and videos are not read by the agent: room images stay on Frame")
    return 0, ""


def check_write(path, root):
    if not isinstance(path, str) or not path:
        return block("missing file path")
    runs = (root / AGENT_RUNS).resolve()
    target = resolved(path, root)
    if target is None:
        return block(UNRESOLVABLE)
    run = target.parent
    if target.name != SUMMARY or run.parent != runs or not RUN_NAME.fullmatch(run.name) or not run.is_dir():
        return block(f"the agent may only write {SUMMARY} in a run directory the runner made, "
                     f"{AGENT_RUNS}/<run>/{SUMMARY}")
    return 0, ""


def check_glob(pattern, path, root):
    """Glob searches ``path`` (default: the checkout), or the leading directories of an absolute pattern."""
    if not isinstance(pattern, str) or not pattern:
        return block("missing or malformed pattern")
    parts = pattern.replace("\\", "/").split("/")
    if AMBIGUOUS.search(pattern) or ".." in parts:
        return block("glob patterns may not start with '~', have surrounding spaces or contain '..'")
    leading = []
    for part in parts[:-1]:
        if GLOB_CHARS.search(part):
            break
        leading.append(part)
    base = path or str(root)
    code, reason = check_read(base, root)
    if code or not leading:
        return code, reason
    # "/x/y/*" names /x/y; "a/b/*.json" names a/b inside the searched directory.
    return check_read("/" + "/".join(leading[1:]) if leading[0] == "" else os.path.join(base, *leading), root)


def decide(payload, root):
    if not isinstance(payload, dict):
        return block("malformed hook input")
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if not isinstance(tool, str) or not isinstance(tool_input, dict):
        return block("malformed hook input")
    if tool in SHELL_TOOLS:
        return check_bash(tool_input.get("command"))
    if tool == "Read":
        return check_read(tool_input.get("file_path"), root)
    if tool == "Glob":
        return check_glob(tool_input.get("pattern"), tool_input.get("path"), root)
    if tool in READ_TOOLS:  # Grep searches the working directory unless given a path.
        return check_read(tool_input.get("path") or str(root), root)
    if tool in WRITE_TOOLS:
        return check_write(tool_input.get("file_path") or tool_input.get("notebook_path"), root)
    if tool in BLOCKED_TOOLS:
        return block("network tools are not used on the headset")
    return 0, ""


def main(stdin=None, root=None):
    try:
        root = Path(root or os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2]).resolve()
        try:
            payload = json.loads((stdin or sys.stdin).read())
        except (ValueError, OSError):
            payload = None
        code, reason = decide(payload, root)
    except BaseException as error:  # Any failure must block, never allow.
        code, reason = block(f"guard error ({type(error).__name__})")
    if code:
        print(reason, file=sys.stderr)
    return code
