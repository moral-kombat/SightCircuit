# Plan: run Claude Code on Frame for automated testing

The fixed on-device runner, guard and settings are implemented; [E77](evidence/e077.md)
records cloud verification, not an accepted Frame agent run. The November 1
Phase 0 report was relayed without raw output in that record. The owner approved
guarded installation on November 3, with the recorded review follow-ups to be
resolved before installing the guard. See the [current plan](live-capture.md)
for the active checkpoint and approval scope.

Use [Frame sessions](guides/frame-sessions.md#run-automated-checks-with-an-on-device-agent)
for supported commands and result handling. The
[November 1 proposal and later updates](history/agent-on-frame-2026-11-01.md)
preserve the full research, technical design and phase procedures. This overview
does not turn proposed phases into authorization for device execution.

## Goal

Run a fixed set of bounded checks on Frame and draft a reviewable summary.
A wearer still supplies visibility, passthrough and tracking observations.

## Prior work

Community comparisons and their limits are preserved in the
[proposal research](history/agent-on-frame-2026-11-01.md#prior-work).

## Where Claude Code runs

The shipped guard targets Claude Code running in the Frame checkout. Remote
Control was proposed as a way to drive that same session. PC-side SSH automation
needs separate Frame-side enforcement and review; a local prefix rule is
insufficient. See the [options and rationale](history/agent-on-frame-2026-11-01.md#where-claude-code-runs).

## Safety model

The runner accepts task IDs, never arbitrary commands. Current Tier B and C
IDs are refused. Read the [supported runner procedure](guides/frame-sessions.md#run-automated-checks-with-an-on-device-agent)
for setup, statuses, timeouts and handoff.

### 1. A fixed task runner is the only command the agent may run

The task table is `sightcircuit/agent/tasks.py`; [the proposal](history/agent-on-frame-2026-11-01.md#1-a-fixed-task-runner-is-the-only-command-the-agent-may-run)
explains the fixed argument vectors and their boundaries.

### 2. Claude Code permissions allow only the runner

The reviewed local settings constrain commands, reads and writes. Installation
is described once in [Frame sessions](guides/frame-sessions.md#guarded-claude-code-settings);
[the archived design](history/agent-on-frame-2026-11-01.md#2-claude-code-permissions-allow-only-the-runner)
explains the permission rules.

### 3. A PreToolUse hook re-checks every Bash call

The hook checks shell syntax and resolved file paths as well as permissions.
See the [guard design and limits](history/agent-on-frame-2026-11-01.md#3-a-pretooluse-hook-re-checks-every-bash-call).

### 4. Operating limits

Tier A can read service metadata and update local environments/artifacts; it
does not change service state or produce VR output. Updates require a clean
checkout, a commit on `origin/main`, required agent files and a guard version
at least as high as the running version. Older commits are not categorically
refused, and a version comparison is not a semantic guard review. See
[checkout behavior](guides/frame-sessions.md#updates-and-checkout-state).

## Test tiers

| Tier | Current runner behavior |
| --- | --- |
| A | Fixed setup, inventory, service reads, builds, offline tests, offscreen replay and existing-still evaluation |
| B | Mirror recognition, new stills and visible panels are refused; wearer-assisted automation remains proposed |
| C | Camera probe, runtime/session changes, capture-layer trials and calibration output are refused |

The [historical tier design](history/agent-on-frame-2026-11-01.md#test-tiers)
records the intended later wearer-approval model; it is not implemented behavior.

## Evidence handling

Keep agent-run commands distinct from user-reported wearer observations, cloud
checks and CI. The agent drafts a run summary; the evidence coordinator records
reviewed results through [evidence maintenance](evidence/README.md). Private
images stay on Frame. Process cleanup does not prove physical recovery.

## Phases

The [current plan](live-capture.md) owns the active sequence. Historical phases
remain addressable below so existing links still resolve.

### Phase 0: install and verify (manual, owner)

The owner-approved installation and its checks are preserved in the
[Phase 0 procedure](history/agent-on-frame-2026-11-01.md#phase-0-install-and-verify-manual-owner).

### Phase 1: runner, guard and settings (cloud work)

Implemented and cloud-tested in [E77](evidence/e077.md); see the
[implementation scope](history/agent-on-frame-2026-11-01.md#phase-1-runner-guard-and-settings-cloud-work).

### Phase 2: first agent-driven Tier A run (approval needed)

Use the [runner guide](guides/frame-sessions.md#run-automated-checks-with-an-on-device-agent)
within the approved scope. The proposed first-run acceptance and negative test
are preserved in [Phase 2](history/agent-on-frame-2026-11-01.md#phase-2-first-agent-driven-tier-a-run-approval-needed).

### Phase 3: on-demand runs from the app or cloud

Remote Control and scheduling remain in the
[Phase 3 proposal](history/agent-on-frame-2026-11-01.md#phase-3-on-demand-runs-from-the-app-or-cloud).

### Phase 4: wearer-assisted runs

Tier B approval integration is proposed, not enabled. The
[Phase 4 design](history/agent-on-frame-2026-11-01.md#phase-4-wearer-assisted-runs)
considers observation-page integration and reviewed third-party tooling.

## Open questions and risks

Device installation, guard behavior in a live Claude session and measurement
contention need their own evidence. See the
[recorded risks](history/agent-on-frame-2026-11-01.md#open-questions-and-risks)
and [current plan](live-capture.md).
