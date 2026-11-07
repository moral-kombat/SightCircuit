# Plan: run Claude Code on Frame for automated testing

Status: **proposal, 2026-11-01; Phase 1 implemented and cloud-tested only
([E77](evidence/e077.md)).** No phase has a recorded Frame result. A Phase 0
report relayed by the Frame project-manager session on 2026-11-01 is a relayed
device report whose raw output is not in PR #16 or E77; E77 notes it but does
not record it as evidence. On 2026-11-03 the owner decided: "Approved: install
the guarded setup following `docs/agent-on-frame.md` Phase 0"
([owner decisions](poc-scope.md#owner-decisions-2026-11-03)). The
[2026-11-03 handoff](execution-prompt.md#coder-handoff-2026-11-03) asks for the
review's cycle-3 follow-ups ([E77](evidence/e077.md)) to be fixed before the
guard is installed. This plan
authorizes no device execution; each phase below needs its own approval, and
its results are recorded in [status](status.md) like any other device result.

## Goal

Today every Frame result needs a person to type commands over SSH, copy the
JSON back to a cloud session and describe what they saw. The
[guided session script](morning-checklist.md) (`scripts/frame_session.sh`)
already automates command order, timeouts and the summary, but a person still
starts it and relays it.

The goal is an agent that executes **on Frame** and can, without a person at the
keyboard:

1. update the checkout to a reviewed revision and rebuild what changed;
2. run a fixed set of bounded checks;
3. read the reports, compare them with earlier runs, and draft an evidence
   summary for a person to review.

A wearer is still needed for anything whose acceptance depends on what the
headset shows (passthrough, tracking, panel visibility). The agent removes the
typing and relaying, not that judgment.

## Prior work

A web and GitHub search on 2026-11-01 found no project that runs Claude Code
**on** Frame to execute guarded, automated tests. Related work, all
community projects whose claims this project has not verified:

| Project | Where the agent runs | Relevant part | Use here |
| --- | --- | --- | --- |
| [saphid/frame-control][frame-control] ([agent tools][frame-control-agents]) | PC; controls Frame over SSH | MCP server with 12 tools. Read-only `status`, `screenshot`, `computer_state` and `job` run directly; `launch`, `install`, `send_file`, `power` and others return an approval URL and one-time token (five-minute expiry) that a person must approve before the agent repeats the call. No arbitrary shell tool | Same "fixed actions plus human approval" model as this plan, already built for Frame. Candidate for the Tier B approval step and for headset screenshots; see [Phase 4](#phase-4-wearer-assisted-runs) |
| [fbl100/frame-starter][frame-starter] | Mac; `ssh frame` | Claude skills and `CLAUDE.md` plus `deploy.sh --check/--launch` and `vrshot.sh`, built on frame-control | Working example of PC-side skills and SSH deploy scripts; no on-device test automation |
| [SimonBiggs/claude-code-steam-frame-coop][coop] | PC; rsync and SSH | Headless tests on the PC; agent watches a mirrored headset view | No guards or on-headset tests |
| [420noscope-exe/Claude-Desktop-for-Steam-Frame][claude-desktop-frame] | Frame | Installs the Claude Desktop Linux beta (not Claude Code) under `~/.local`, without root, on Frame arm64 SteamOS | Suggests home-directory installs work on Frame; does not show that the Claude Code CLI does |
| [28allday/steamos-claude-skill][steamos-skill] | Device | Claude Code skill for maintaining SteamOS safely (read-only root, A/B updates) | Possible reference for SteamOS limits; targets Steam Deck |

Outside Frame, Meta ships agent tooling for Quest testing (an MCP server,
the Horizon debug bridge and the experimental XR Operator); see
[Meta's AI tooling page][meta-ai-tooling]. None of it runs on SteamOS.

Two consequences for this plan. First, every Frame project found that runs
tests or controls the headset runs the agent on a PC (the one on-device install
is Claude Desktop, not Claude Code), so that is the proven fallback if the
on-device install fails (see [Phase 0](#phase-0-install-and-verify-manual-owner)). Second, frame-control
already provides approvals and headset screenshots, so Tier B should reuse it
after review rather than rebuild those parts. It does not replace the Tier A
runner: frame-control has no SightCircuit checks, and its approvals assume a
person is watching.

## Where Claude Code runs

The cloud session that edits this repository cannot reach a headset on a home
network ([execution context](on-device.md#confirm-where-commands-run)), so the
agent has to run on Frame or be attached to it.

| Option | How | Verdict |
| --- | --- | --- |
| **A. Claude Code CLI installed on Frame** | Native Linux ARM64 install into `~/.local/bin` as `steamos`; started over SSH inside `tmux`, interactively or headless (`claude -p`) | **Recommended base.** Commands run in the right place; guards live next to the commands; no root, no read-only-OS changes |
| **B. Remote Control from Frame** | Option A plus `claude remote-control` started in `~/sightcircuit`; a Claude Code session in the app or cloud then attaches to that folder | **Recommended way to drive it** from the phone/desktop app or from a cloud development session. Same guards as A, because commands still run through Frame's Claude Code |
| C. Claude Code on the PC, commands sent over SSH | Agent runs on the PC; it calls the Frame runner as `ssh steamos@<FRAME_IP> python3 sightcircuit/scripts/frame_agent_run.py run <task>` | **Fallback** if Phase 0 fails; this is how the Frame projects in [prior work](#prior-work) that run tests or control the headset operate. **Not ready as written:** the shipped guard and settings cover option A only and block every `ssh` command, and a PC-side prefix rule is not enough, because `ssh` joins its arguments and Frame's login shell parses them again, so an escaped or quoted `;` or `$(…)` that matches the prefix locally becomes a second command on Frame. Option C first needs its own enforcement on Frame, such as an `authorized_keys` forced command that reads `SSH_ORIGINAL_COMMAND` and accepts only runner task IDs, and a separate review. Free-form `ssh` stays denied |
| D. Self-hosted GitHub Actions runner on Frame | Runner service picks up workflow jobs | **Rejected for now.** Pull-request code would execute on the headset, it adds a permanent service, and CI cannot judge headset behaviour anyway |

## Safety model

The [on-device guide](on-device.md) contains commands that are unsafe to run
unattended: the public probe interrupted passthrough
([E3](status.md#e3-public-camera-probe)), a bare SteamVR stop also stopped the
graphical session ([E63](status.md#e63-service-only-rehearsal-and-graphical-session-dependency)),
and the session and metadata trials log the user out. An agent must not be able
to reach them, whatever its prompt says.

### 1. A fixed task runner is the only command the agent may run

`scripts/frame_agent_run.py` (logic and task table in `sightcircuit/agent/`)
takes a **task ID**, not a command line:

```bash
python3 scripts/frame_agent_run.py list
python3 scripts/frame_agent_run.py run tier-a/inventory
python3 scripts/frame_agent_run.py run-tier tier-a
python3 scripts/frame_agent_run.py report latest
```

Each task maps to a hard-coded argument vector with a timeout, a run directory
under `artifacts/agent-runs/<time>-<label>/`, the git revision and a
`result.json`. It refuses unknown task IDs and never accepts free-form
arguments, so `--allow-*` flags, `--start`, `--capture` and the probe binary
cannot be passed through it. Tier B and C IDs are listed but refused with a
reason; Tier B will additionally require a wearer approval (Phase 4). The
procedure is in [on-device](on-device.md#run-automated-checks-with-an-on-device-agent).

### 2. Claude Code permissions allow only the runner

`.claude/settings.local.json` in the Frame checkout, copied from the reviewed
template `sightcircuit/agent/frame-settings.json`. The local settings file is
git-ignored, so cloud sessions are unaffected:

- `permissions.defaultMode: "dontAsk"`, so anything not allowed is refused
  instead of waiting for an absent person;
- `allow`: `Bash(python3 scripts/frame_agent_run.py:*)`, and `Write`/`Edit`
  only for `artifacts/agent-runs/*/summary.md`. There is no bare `Read`,
  `Grep` or `Glob` rule: reads inside the checkout need none, and one would
  open the whole home directory, including Claude Code's own credentials;
- `deny`: `Bash(sudo:*)`, `Bash(systemctl:*)`, `Bash(steamosctl:*)`,
  `Bash(steamos-readonly:*)`, `Bash(pacman:*)`, `Bash(git push:*)`,
  `Monitor`, `WebFetch`, `WebSearch`, reads of the private
  `evaluation/local/`, `recordings/` and `calibration/` directories, of
  `.png`, `.jpg`, `.jpeg` and `.mp4` files and of `~/Videos/**`, and reads of
  `~/.ssh/**`, `~/.claude/**`, `~/.config/**`, `~/.git-credentials` and
  `~/.netrc`.

### 3. A PreToolUse hook re-checks every Bash call

Permission patterns match prefixes, so the hook `scripts/frame_agent_guard.py`
re-checks each call and fails closed: unreadable input or any error inside the
guard blocks the call, and the hook command ends in `|| exit 2`, because Claude
Code blocks only on exit 2 and treats a crash (exit 1) or a missing `python3`
(exit 127) as a non-blocking error. Bash (and `Monitor`, which also runs
commands) is allowed only as `python3 scripts/frame_agent_run.py` followed by
plain words: any shell syntax (`;`, `&&`, `|`, backticks, `$(`, redirection,
quotes, globs, newlines) blocks it, as do environment prefixes and other
commands. Known-unsafe commands (`--allow-`, `camera_probe`,
`session_rehearsal`, `metadata_session_trial`,
`service_rehearsal --start|--recover`, `capture_layer_trial --start…|--rollback`,
`systemctl`, `steamosctl`, `sudo`, `git push`) get a specific reason. The
file a `Read` names, and the directory a `Grep` or `Glob` searches (its
`path`, and the leading directories of a `Glob` pattern), must resolve inside
the checkout, outside the private image, recording and calibration
directories, and not to an image or video file. A recursive search from inside
the checkout (for example `Glob` `**/*.json`) can still descend into those
directories: there the hook relies on the `Read` deny rules above, which
Claude Code turns into search exclusions (seen in the 2.1.316 code, not yet
confirmed in a live session).
File writes must resolve (after symlinks) to `summary.md` directly in a run
directory the runner created, `artifacts/agent-runs/<run>/summary.md`: the
runner's `result.json`, task output and lock are not agent-writable, and
`report latest` considers only runner-named run directories. Claude Code trims
a path and expands a leading `~` before using it, so the hook refuses paths
with surrounding white space or a leading `~`, and `Glob` patterns containing
`..`, instead of resolving them differently. Web tools are blocked. The runner
and the hook are deliberately redundant for Bash; either alone should be
enough.

### 4. Operating limits

- Runs as `steamos` without sudo; no package, service or `/etc` changes.
- Updates only through `frame_agent_run.py update <full commit>`: the
  checkout must be clean, the commit must already be on `origin/main` after a
  fetch, and it must contain the runner and a guard at least as strict as the
  running one (`GUARD_VERSION`), so the agent cannot roll itself back to a
  weaker guard or to a revision without the runner. A git command that fails
  or times out (the fetch after 3 minutes) refuses the update, and git and its
  transport are stopped. Unmerged branches and older revisions are checked out
  by a person.
- One run at a time (lock file); per-task timeout plus a whole-run budget.
  A timed-out task's whole process group is terminated, including members
  that ignore SIGTERM; a task that exits but leaves processes running is
  failed and its group terminated. SIGINT, SIGTERM or SIGHUP to the runner
  terminates the running task's group before the lock is released and records
  the run as `interrupted`, also when it arrives while the task is starting; a
  signal the runner was started with ignored (`nohup` ignores SIGHUP) stays
  ignored. A task that starts its own session (`setsid`) is outside that group
  and is not contained.
- No task touches `dsp_service`, `/dev/cdsp`, `/dev/adsp` or SteamVR's
  `drivers/cv/bin`, and none starts a unit bound to `steamvr.service`. Reach
  checks use port 22, not ping. See the
  [preflight advice](on-device.md#preflight-advice-from-another-frame-project-upstream-not-our-evidence).
- Authentication is done interactively by the owner on Frame (`claude` login);
  no key in the repository, environment files or chat.
- Previews and stills stay on Frame: room images are private and are not pushed
  or pasted into transcripts. `evaluation/local/` remains git-ignored.

## Test tiers

| Tier | Needs | Tasks (existing commands) | Agent may run |
| --- | --- | --- | --- |
| **A. No VR contact** | Nothing | `device_check.py` and `recording_check.py` (what `frame_replay_check.sh` runs); ARM64 unit suites (`unittest discover -s tests`, recording skip counts); `build_overlay_bridge.sh` and the shared-memory reader build; `capture_layer_trial.py --runtime-status`; `service_rehearsal.py --preflight` and `--report-latest`; `hud_marker_check.py --validate --seconds 3`; offscreen replay regression and realtime pacing at 2 and 4 decoder threads (the paced runs of `frame_session.sh regression pacing`, `QT_QPA_PLATFORM=offscreen`; its unpaced 200-frame baseline is not in the task table); `evaluate_detectors.py` on existing `evaluation/local/frame.json` | Unattended, on demand or on a schedule |
| **B. Wearer present** | A person wearing the headset, answering the [observation page](on-device.md#record-headset-observations-for-a-run) | Bounded mirror recognition (`frame_session.sh mirror` with `COUNTDOWN`), stills with `SCENES`, overlay replay, window-in-mirror | Only after a wearer submits a "ready" observation for that run ID; the agent then waits for the "during/after normal" answers before reporting |
| **C. Never by agent** | Separate review and approval | Public probe with its interruption flag, `session_rehearsal --start`, `metadata_session_trial --start`, `service_rehearsal --start/--recover`, `capture_layer_trial --start-metadata-test/--rollback`, HUD calibration with `--allow-*` flags | No. A person runs these from SSH on another computer, as the guide already requires |

Tier A is where nearly all of the value is: it is what currently costs a
manual round trip after each cloud change (the ARM64 test counts, rebuilt
bridge, replay pacing and runtime liveness quoted in [status](status.md)).

## Evidence handling

Agent results are a new provenance class, distinct from cloud checks, CI and
user reports:

- **Agent-run on Frame**: command exit codes and JSON reports produced by the
  runner on the headset, with revision, runtime build (`device_check.py`) and
  run directory.
- Wearer answers from the observation page remain **user-reported**, even
  when the agent collects them.
- The agent drafts `artifacts/agent-runs/<time>/summary.md` using the
  [evidence template](morning-checklist.md). It does not edit
  `docs/evidence/`, `docs/status-data.json` or `docs/status.md`; the evidence
  coordinator reviews and records results, as today.
- An agent's interpretation ("pacing regressed", "looks normal") is commentary,
  never evidence. Process exit and API cleanup still do not establish physical
  headset recovery.

Getting results to the cloud: phase 1 copies `summary.md` and the JSON (no
images) by hand or over Remote Control. A later phase can let the runner push
JSON-only summaries to a dedicated `frame-runs/*` branch with a narrowly
scoped token, if that is approved.

## Phases

Each phase ends with a recorded result before the next begins.

### Phase 0: install and verify (manual, owner)

Prerequisites already established: developer mode and
[independent SSH](on-device.md#verify-independent-ssh-access).

`scripts/frame_claude_install.sh` runs the steps below with a status line
(PASS/WARN/FAIL) after each check and a breakpoint before each stage (Enter
runs it, `s` skips it, `q` stops). It stops at the known aarch64 abort, never
uses sudo, and writes `summary.md`, `commands.log` and `doctor.txt` to
`~/claude-phase0/<time>/`. An optional `signature` stage checks the installed
binary against Anthropic's signed release manifest using a throwaway keyring
(removed afterwards, also when the stage is interrupted), and accepts the
manifest only when gpg reports a good signature by the pinned release key or
one of its signing subkeys: a signature by a revoked or expired key fails. It
checks `~/.local/share/claude/versions/<version>` and records which `claude`
is on `PATH`; a launcher you made yourself is not covered.
The `path`, `verify`, `signature`, `smoke` and `record` stages are tested in
the cloud with a fake `claude` (the signature stage with locally generated
keys and a fake download); `precheck`, `install` and the interactive `login`
are not. The script has not run on Frame yet.

```bash
bash scripts/frame_claude_install.sh            # all stages, with breakpoints
bash scripts/frame_claude_install.sh precheck   # read-only checks only
```

The manual equivalent:

1. Over SSH, install Claude Code for the `steamos` user using the official
   native installer from the
   [Claude Code setup documentation](https://code.claude.com/docs/en/setup);
   it installs under the home directory, which survives SteamOS updates.
   Confirm `claude --version` and `claude doctor` on aarch64.
   Known risk: there are reports of the native installer aborting on aarch64
   Linux ([#26509][aarch64-abort-1], [#30729][aarch64-abort-2]); the first
   is marked closed, the status of the second is unknown. If the install or
   first start aborts, record the version and error, do not work around it on
   the system partition, and switch to option C (agent on the PC, runner on
   Frame). Option C is not covered by the shipped guard and settings: it
   needs its own Frame-side enforcement and review first (see
   [option C](#where-claude-code-runs)) before Phase 2.
2. Sign in interactively; confirm Frame can reach the Anthropic API from its
   network.
3. In a scratch directory (not the checkout), run one read-only prompt to
   confirm the agent executes on Frame: `claude -p "run uname -m and hostname"`
   should report `aarch64` and `frame`.
4. Record the version, runtime build and any install problem as a device
   result.

### Phase 1: runner, guard and settings (cloud work)

**Implemented 2026-11-01, cloud-tested only** (`tests/test_frame_agent.py`;
no Frame execution yet; [E77](evidence/e077.md)).

1. Implement `sightcircuit/agent/` and `scripts/frame_agent_run.py` with the
   Tier A table; Tier B and C IDs exist only as refusals with a reason.
2. Add the settings template `sightcircuit/agent/frame-settings.json` and the
   hook `scripts/frame_agent_guard.py`.
3. Cloud tests: unknown IDs and extra arguments refused; every Tier A argument
   vector names an existing script without opt-in flags and parses with that
   script's own parser (`tests/test_frame_agent_commands.py`); timeouts,
   interruption and the lock work; the hook blocks each Tier C command and
   every chaining form; the runner never imports another script
   (`tests/test_scripts_boundary.py`).
4. Document the procedure in [on-device](on-device.md) and link it from the
   [index](README.md).

### Phase 2: first agent-driven Tier A run (approval needed)

On Frame, install the settings as in
[on-device](on-device.md#run-automated-checks-with-an-on-device-agent), then in
`tmux`:

```bash
cd "$HOME/sightcircuit"
mkdir -p artifacts/agent-runs   # the redirect below needs it before the first run
claude -p "Run every tier-a task with scripts/frame_agent_run.py, then write artifacts/agent-runs/<run>/summary.md comparing results with docs/status.md. Do not run anything else." \
  --max-turns 40 --output-format json > artifacts/agent-runs/last-session.json
```

Acceptance: the same reports as a manual run of the same tasks, the session
transcript showing no blocked attempts (or only expected ones), and normal passthrough
afterwards confirmed by the owner. A deliberate negative test asks the agent to
run `service_rehearsal.py --start` and expects a refusal.

### Phase 3: on-demand runs from the app or cloud

Start `claude remote-control` in `~/sightcircuit` inside `tmux` on Frame. A
development session can then attach to that folder and run "update to
`<revision>` and run Tier A" after each merge to `main`, without anyone at the
PC.
Optionally add a `systemd --user` timer for a nightly Tier A run; it is a
user unit, needs no root, and is removed with
`systemctl --user disable --now` by the owner (the agent itself cannot call
`systemctl`).

### Phase 4: wearer-assisted runs

Enable Tier B tasks in the runner behind a wearer approval: the agent
starts `observation_page.py`, prints the URL for the wearer, waits for "ready",
runs the bounded `frame_session.sh` stage with `COUNTDOWN`, then waits for the
wearer's during/after answers. A missing or negative answer stops the run.

Before building more of this, review [frame-control][frame-control-agents]
as an alternative for two parts:

- **Approval:** its approve/reject link and one-time token match the "wearer
  says ready" step and can be opened in the headset browser.
- **Headset screenshots:** its read-only `screenshot` tool could record what
  the wearer saw next to each run, which observation answers alone cannot.

Conditions if adopted: pin a reviewed revision and record it in
[third-party sources](../THIRD_PARTY.md); expose only its read-only tools
plus approvals (not `power`, `install`, `uninstall`, `send_file` or
`launch`); keep screenshots local like other room images; and treat
screenshots as agent-run evidence, while wearer answers stay user-reported.

## Open questions and risks

- **ARM64 SteamOS compatibility** of the Claude Code native build is
  unverified, and aarch64 install aborts have been reported (see Phase 0).
  Option C is the fallback.
- **Third-party tooling** (frame-control) is community code that controls the
  headset over SSH; adopt only a pinned, reviewed revision with the mutating
  tools disabled.
- **Graphical session from SSH**: overlay and window stages need the `:0`
  session environment used in
  [E55](status.md#e55-annotated-replay-overlay-command-on-frame); Tier A avoids
  it by running Qt offscreen.
- **Resource contention**: an agent process plus inference on the headset may
  affect pacing measurements; record whether the agent was running, and compare
  pacing only between runs made the same way.
- **Thermal and battery**: schedule unattended runs while charging and idle.
- **Prompt injection** through repository content or reports is bounded by the
  runner/hook design: the worst case is a wrong summary, not a new command.
- **Cost**: Tier A summaries are short; a turn limit bounds each run.

[frame-control]: https://github.com/saphid/frame-control
[frame-control-agents]: https://github.com/saphid/frame-control/blob/main/docs/agents.md
[frame-starter]: https://github.com/fbl100/frame-starter
[coop]: https://github.com/SimonBiggs/claude-code-steam-frame-coop
[claude-desktop-frame]: https://github.com/420noscope-exe/Claude-Desktop-for-Steam-Frame
[steamos-skill]: https://github.com/28allday/steamos-claude-skill
[meta-ai-tooling]: https://developers.meta.com/horizon/essentials/ai-tooling-mcp/
[aarch64-abort-1]: https://claudeissues.com/issue/26509-bug-claude-aborted-at-aarch64-native-installation
[aarch64-abort-2]: https://claudeissues.com/issue/30729-bug-install-and-binary-abort-on-aarch64-lambda-ai-s-gh200
