# Frame sessions

Use this guide for the guided shell runner, wearer observations and the fixed
on-device agent runner. Check the [current plan](../live-capture.md) for the
selected work and the [device guide](../on-device.md#confirm-where-commands-run)
for execution context. These commands run on Frame; cloud checks and CI do not
establish headset behavior. The [replay panel guide](../vr-overlay.md) covers
interactive panel controls and the [device guide](../on-device.md) covers the
individual mirror and still-capture procedures.

## Run a guided shell session

Run `scripts/frame_session.sh` from the normal graphical session on Frame.
It writes reports and `summary.md` to `artifacts/frame-session/<local time>/`.
With no stage arguments it runs `setup regression pacing mirror stills evaluate`.
`overlay` and `window-mirror` are explicit additions; mirror/still stages also
need a person aiming the headset and judging the result.

For a checkout intended to follow `main`:

```bash
cd "$HOME/sightcircuit"
git switch main
bash scripts/frame_session.sh setup regression pacing
```

Setup runs `git pull --ff-only` on the current branch, then creates/updates
`.venv-replay` and builds the overlay bridge. It never switches branches itself.
To retain a reviewed branch or detached commit, omit `git switch main` and use
`SKIP_UPDATE=1 bash scripts/frame_session.sh setup regression pacing`.

Choose stages explicitly when a full session is unnecessary:

| Stage | Behavior |
| --- | --- |
| `setup` | Pull current branch unless `SKIP_UPDATE` is set, set up replay dependencies, build bridge |
| `regression` | Offscreen replay of 30 frames |
| `pacing` | Offscreen 200-frame realtime runs with 2 and 4 decoder threads, then an unpaced baseline |
| `overlay` | Realtime replay with a VR panel for up to 100 frames; ask about visibility and passthrough |
| `mirror` | Ten-second mirror recognition capped at 5 processed fps; save report and annotated preview |
| `stills` | Five-second mirror captures, each with a raw still, capture report and manifest entry |
| `evaluate` | Compare detectors on `evaluation/local/frame.json`; skip if that manifest is absent |
| `window-mirror` | Run a desktop replay while capturing the mirror; inspect whether the window appears |

For a bounded mirror run after setup:

```bash
SKIP_UPDATE=1 COUNTDOWN=15 STEP_TIMEOUT=90 \
  bash scripts/frame_session.sh mirror
```

### Session options and observations

| Variable | Meaning |
| --- | --- |
| `DEVICE` | Mirror node; default `/dev/video99` |
| `RECORDING` | Explicit replay recording; otherwise discover the newest recording |
| `SKIP_UPDATE=1` | Skip the setup stage's pull; other setup commands still run |
| `STEP_TIMEOUT` | Per-command timeout in seconds; default 300, timed-out commands report exit 124 |
| `YES=1` | Skip prompts; observation answers remain `unanswered`; stills need `SCENES` |
| `COUNTDOWN=15` | Wait with terminal bells before human steps; observation answers remain `unanswered` |
| `SCENES` | Semicolon-separated `id|present,labels|absent,labels` still descriptions |

`COUNTDOWN` automates timing only. Aim during the countdown, inspect the preview
and report afterward, and provide the wearer observations separately.
[E81](../evidence/e081.md) found terminal bells inaudible in the headset: the
first capture attempt was invalid because prompts did not reach the owner.
Confirm readiness with the wearer before each capture; a countdown is not an
acknowledgment. For
no-typing still capture set `SCENES` as well; without it the stills stage asks for
scene IDs and labels. Use canonical COCO labels that were actually inspected,
for example `SCENES="rug|chair|couch;tv|tv|"`. Images stay in ignored
`evaluation/local/`; do not put room images in Git or transcripts.

A failed stage stops later stages. Preserve its reports and check passthrough
and tracking; an exit code or API cleanup report cannot establish physical
recovery. The script does not activate capture layers, restart SteamVR or run
the camera probe. The [observation page](#record-headset-observations-for-a-run)
collects answers separately; it is not wired into shell or agent task approval.

### Read and record the results

For pacing, inspect `realtime_ratio`, `processed_fps`, `decode_fps`, `catch_ups`,
`frames_dropped`, `mean_processing_ms` and `max_input_age_ms`. A ratio near 1.0
means recorded-time pacing; the offscreen stage cannot establish how replay
looks in the headset. Use the explicit overlay stage for wearer observations.

The stills stage pins the capture report's SHA-256 through
`scripts/frame_session_manifest.py`. Evaluation verifies every supplied hash,
but neither the shell `evaluate` stage nor the agent's `tier-a/evaluate-local`
passes `--require-input-digests`; entries without hashes are accepted. To require
a digest for every entry, run the evaluator explicitly:

```bash
.venv-replay/bin/python scripts/evaluate_detectors.py --manifest evaluation/local/frame.json \
  --require-input-digests --report "evaluation/local/report-$(date +%Y%m%d-%H%M%S).json"
```

Choose a default detector only from representative inspected Frame evidence.
[E81](../evidence/e081.md) confirms regression/pacing, a direct 400-frame panel
run and the mirror redo, with normal passthrough and tracking. The shell's
`overlay` stage still uses 100 frames. Its five valid unboxed stills supported
the owner's POC-object change to chair, not a detector default; WP2 remains
open. The invalid initial attempt is excluded. [E80](../evidence/e080.md)
reproduced the E61 TV miss in a file-only rerun without explaining it; WP1 also
remains open.

[E79](../evidence/e079.md) closed G2 through an owner-confirmed recovery
rehearsal; the green-input cause remains unknown and a cure was not established.
The later [E83 observer attempt](../evidence/e083.md) stopped at the scene
precondition before any tile appeared, leaving G3 open. The E81 panel used the
old connect order and cannot qualify the later lifecycle changes recorded as
cloud work in [E84](../evidence/e084.md). The [session evidence](../status.md)
keeps these outcomes distinct. Submit new reports with their revision,
runtime, command and limitations through [evidence maintenance](../evidence/README.md).
Keep command results, user-reported observations, cloud checks and CI separate.

## Record headset observations for a run

`scripts/observation_page.py` serves a short form on the local network. The
person wearing the headset opens it in the headset browser (or a phone on the
same Wi-Fi) and answers fixed questions, so answers are saved against the run
instead of being left unanswered at an SSH prompt. It is standard-library Python
and needs no setup beyond the checkout. Start it before the test and leave it
running:

```bash
cd "$HOME/sightcircuit"
sightcircuit_observations="artifacts/observations/$(date +%Y%m%d-%H%M%S)"
python3 scripts/observation_page.py --run-dir "$sightcircuit_observations" --minutes 20
```

The first output object is JSON with a `url` that contains a random token.
Open that URL from the headset or phone. Each submission is appended to
`$sightcircuit_observations/observations.json` with a UTC `submitted_at` time and the answers. The
server stops after `--minutes` (maximum 120) or on Ctrl-C, and prints how many
submissions it saved.

The page and the shell/agent runners allocate their directories independently;
starting this page does not set either runner's output directory or approve a
runner task. Record the observation-directory path in the session summary.
To store answers beside an already-started run, give `--run-dir` that run's
actual directory instead. Keep the run ID and revision with the answers.

Options: `--run-id` sets the label shown on the page (default: the run directory
name); `--port` defaults to 8765; `--host 127.0.0.1` keeps the page local, so
only the Frame itself (for example with `curl` over SSH) can reach it. The default `--host 0.0.0.0` listens on
all interfaces, so the page is reachable from the LAN while it runs. Traffic is
plain HTTP, the token is the only access control, and the server only accepts
the form's fixed fields and choices. Do not leave it running after the session.

Whether the headset browser can reach the printed address is not yet verified.
If it cannot, note the URL, the error, and the network setup in
the [evidence handoff](../evidence/README.md) rather than assuming the page works. Record the answers in
`observations.json` as user-reported results, not as device checks.

## Run automated checks with an on-device agent

**Phase 0 installed; guard enforcement and Tier A runs remain untested on Frame.**
[E82](../evidence/e082.md) records passing installation checks and settings
matching the template. The commands below are Phase 2 of the
[agent plan](../agent-on-frame.md), which needs its own approval; the
[enforcement test guide](agent-enforcement-test.md) is its scope. A Claude Code
session on Frame may run only `scripts/frame_agent_run.py`, which executes a
fixed task table by ID. Tier A tasks do not produce VR output, capture camera
frames or change service state. They do read service metadata and may build
local artifacts or update the replay environment. Tier B (wearer present) and
Tier C (person-only) IDs are refused.

```bash
cd "$HOME/sightcircuit"
python3 scripts/frame_agent_run.py list
python3 scripts/frame_agent_run.py run tier-a/inventory
python3 scripts/frame_agent_run.py run-tier tier-a
python3 scripts/frame_agent_run.py report latest
```

Each run creates `artifacts/agent-runs/<UTC time>-<label>/` with `result.json`
(revision, clean flag, host, per-task argument vector, exit code, status and
duration) and each task's `stdout.txt`/`stderr.txt`; `result.json` is rewritten
before and after each task. Statuses are `passed`, `failed` (a nonzero exit,
or an exit that left processes running in the task's group, which are then
terminated), `failed_to_start` (the command could not be executed),
`timed_out` (the task's whole process group is terminated, SIGKILL after a
10-second grace), `skipped_precondition` (for example no `.venv-replay`),
`running` (while it runs), `interrupted` and `not_started` (after a failed
`tier-a/setup`, when the run budget of 90 minutes is spent, or after an
interruption). Exit codes: 0 all passed, 1 otherwise, 2 refused, 3 another run
holds the lock, 128 + signal number when SIGINT, SIGTERM or SIGHUP (for
example a dropped SSH connection) interrupted the run; the running task's
group is terminated before the lock is released. A signal while the run
records the checkout, before its first task, ends that git as `update` does
(below), and `result.json` then has no revision, clean flag or branch; a run
does not check for a leftover `.git/index.lock`. Run `run-tier` inside `tmux`
when the connection may drop (`nohup` also works: a signal the runner was
started with ignored stays ignored).

The runner's signal handling and group termination are the
[supervision primitive](../reference/pipeline.md#the-supervision-primitive)
in its raise mode (since 2026-11-07, work-order item B4): the first signal
raises, an inherited ignore is honoured, the handler is held while a task or
git starts, and a task's whole process group is ended with SIGTERM then
SIGKILL and reaped, the runner being the subreaper of its tasks for the run.
A task is a command, not a supervised report worker, so it has no arm
handshake: a signal to the runner ends the task's group at once, and a task
that has not yet installed a handler ends by the default disposition. Where
a task is itself a guarded command (`tier-a/marker-validate`, which runs
`hud_marker_check.py --validate` under the guard), the guard inside it waits
up to one second (`Grace.arm`) for its worker to arm before SIGTERM; a worker
whose start-up takes longer under load is still force-ended and reported as
such. The group that an exited task leaves behind
has half a second (`LEFTOVER_GRACE`) to follow it before the task counts as
failed and the group is terminated.

On the runtime described by the recorded service result, `run-tier tier-a` is
expected not to report `all_passed`: by design
`tier-a/service-preflight` exits 1 with
`steamvr_stop_propagates_to_graphical_session` (the
[current guard](../on-device.md#service-only-recovery-rehearsal-result)),
`tier-a/service-report` exits nonzero because no successful rehearsal exists,
and `tier-a/evaluate-local` is skipped when `evaluation/local/frame.json` is
absent. Read each task's status and reason rather than the overall exit code.

### Updates and checkout state

`python3 scripts/frame_agent_run.py update <full commit>` accepts a full
40-character lowercase commit hash. It fetches `origin/main` and requires a
clean checkout, a commit on that history, all required agent files and a
`GUARD_VERSION` at least as high as the running guard's. It does not reject an
older commit solely because it precedes the current checkout, and the version
check does not prove that guard semantics are equally strict. Unmerged commits,
missing agent files and lower guard versions are refused. A failed or timed-out
git command also refuses the update (exit 2; the fetch times out after 3
minutes). That includes the git commands that record the checkout after the
switch: if one fails or times out, `update` refuses with "switched to
<commit>, but recording the checkout failed" instead of reporting `updated`.
git runs with `GIT_TERMINAL_PROMPT=0`, stdin from `/dev/null` and in its own
session without a controlling terminal, so it cannot prompt on the terminal; a
configured credential helper or askpass program can still run, bounded by the
fetch timeout. A timed-out git, or the running one when SIGINT, SIGTERM or
SIGHUP interrupts `update` (exit 128 + signal number), is ended with its
transport before the lock is released: SIGTERM first, on which git removes its
lock files, and SIGKILL 5 seconds later; ending git takes at most 25 seconds.
An interrupted `update`, like one refused after the switch, may already have
switched the checkout, so check it (`git status`) before using it. A
`.git/index.lock` still there after a timed-out or interrupted git, or after a
failed checkout record, is named in the refusal (or the interruption report)
and never deleted: check that no git command is running before removing it.
Only the index lock is checked: another lock file a killed git leaves, such as
a ref lock from the fetch, makes the next git command that needs it fail, and
`update` then refuses with git's error.

An accepted update leaves a detached `HEAD`. Before using the shell runner's
`setup` stage, either switch to `main` for its `git pull --ff-only`, or use
`SKIP_UPDATE=1` to retain the reviewed revision. Setup never switches branches.

### Guarded Claude Code settings

The Frame settings were installed and matched the template in
[E82](../evidence/e082.md); that did not test guard enforcement. For a checkout
being set up under the approved installation scope, install the settings once,
in the Frame checkout only:

```bash
cd "$HOME/sightcircuit"
mkdir -p .claude
cp sightcircuit/agent/frame-settings.json .claude/settings.local.json
```

That file is git-ignored. It refuses every tool call that is not allowed
instead of prompting, permits Bash only for the runner, denies reads of
private stills, recordings, images and credentials, and installs
`scripts/frame_agent_guard.py` as a PreToolUse hook that blocks shell syntax,
other commands, reads outside the checkout or of images, and every write except
a run's `summary.md`. The hook also refuses `Glob` patterns with a backslash (an
escape to Claude Code), a trailing `/` (Claude Code then searches the parent
directory) or `..` anywhere, so it never checks a different directory than the
one searched; give the directory as the `path` instead (`GUARD_VERSION` 4).
Remove the file to return the checkout to normal interactive use.

Precondition the operator checks before the Phase 2 enforcement test: Claude
Code applies the checkout's `permissions.allow` entries only once the
workspace is trusted. [E86](../evidence/e086.md) recorded `claude doctor` on
Frame saying "Ignoring 3 permissions.allow entries from
.claude/settings.local.json: this workspace has not been trusted". Until the
workspace trust dialog has been accepted interactively for the checkout, or
the trust is recorded in `~/.claude.json`, the allow entries are ignored, so
an enforcement test would observe prompts or refusals that come from the
missing trust and not from the guard. Check `claude doctor` for that line
before the test; this guide records the condition and does not instruct a
change to the device. The [enforcement test guide](agent-enforcement-test.md)
makes the check its first step and owns the test's matrix, report and
approval boundary.

`scripts/frame_claude_install.sh` runs the Phase 0 stages with a status line
and a breakpoint before each stage; the procedure is preserved in the
[Phase 0 procedure](../history/agent-on-frame-2026-11-01.md#phase-0-install-and-verify-manual-owner).
Its summary names the channel the install stage was asked for (`CHANNEL`,
default `stable`) as the requested install channel, and the installed
auto-update channel only as `claude doctor` reports it in the `record` stage,
or `unknown` when doctor did not report one; the two are not the same setting
([E86](../evidence/e086.md) found "latest" installed where the summary had
said "stable").
Its optional `signature` stage checks the installed binary against the signed
release manifest with a throwaway keyring. From the downloads on, every way out
of the stage, an interrupted stage included, asks the keyring's agent to stop
and removes the keyring with its temporary directory; the cleanup runs once,
and `gpgconf` runs only for the throwaway keyring. Each `gpgconf` call is
bounded by `timeout`: the cleanup keeps INT, TERM and HUP ignored so that no
second signal can cut it short, which `gpgconf` inherits, so `timeout`'s
SIGTERM at 20 seconds does not end a hung call and its SIGKILL 5 seconds later
does; such a cut-off is not reported, and the agent may then outlive the stage.

Results are agent-run device output: they are not user reports, and exit codes
do not establish what the headset showed. Room images stay on Frame: the
settings and hook refuse the agent's reads of them. Hand `result.json` and the
agent's draft summary to the evidence coordinator.

### Preflight advice from another Frame project (upstream, not our evidence)

coah80/framecorder's development guide ([`AGENTS.md` on `dev`](https://github.com/coah80/framecorder/blob/0c624afb008e0f2e1e467eacae9a9206480943ba/AGENTS.md) at
`0c624afb008e0f2e1e467eacae9a9206480943ba`, MIT) gives this advice from its own
Frame work. It is upstream advice, not a SightCircuit result:

- The headset does not answer ping. Check that port 22 answers instead
  (for example `nc -z -w 3 <frame-host> 22`); if it does not, the headset may
  be asleep ([L231](https://github.com/coah80/framecorder/blob/0c624afb008e0f2e1e467eacae9a9206480943ba/AGENTS.md#L231)).
- Desktop Mode runs a nested Plasma session with its own `XDG_RUNTIME_DIR` and
  session bus. From a Desktop Mode terminal, point user-service and runtime
  paths at `/run/user/<uid>` rather than trusting `$XDG_RUNTIME_DIR`
  ([L225](https://github.com/coah80/framecorder/blob/0c624afb008e0f2e1e467eacae9a9206480943ba/AGENTS.md#L225)).
- Starting a game can restart PipeWire; anything holding a stream must notice
  and reconnect ([L229](https://github.com/coah80/framecorder/blob/0c624afb008e0f2e1e467eacae9a9206480943ba/AGENTS.md#L229)).
- Never start a unit bound to SteamVR (`BindsTo=steamvr.service`) while
  SteamVR is off: starting it pulls SteamVR up, and a SteamVR started outside
  its own launcher can leave the headset stuck in passthrough ([L62](https://github.com/coah80/framecorder/blob/0c624afb008e0f2e1e467eacae9a9206480943ba/AGENTS.md#L62)).

SightCircuit's own rule, beside that advice: no runner task touches
`dsp_service`, `/dev/cdsp`, `/dev/adsp` or SteamVR's `drivers/cv/bin`.
`tests/test_frame_agent.py` checks the task table for those names.
