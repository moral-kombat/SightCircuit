# November 6 review follow-ups and November 7 coder handoff

> Historical source record carried in by merged SightCircuit PR #1. It preserves
> the original session wording and repository/branch references. Current status
> and ordering belong to [current work](../live-capture.md#current-work-order).
> A statement about a former repository's merge does not merge a SightCircuit PR.

## Ship pipeline follow-ups, 2026-11-06

The `/ship` pass that merged PRs #24, #22, #26, #25 and #27 reviewed each one
and fixed what was safe to fix in place; these findings were deferred. None is
a device step. The next coder takes them in this order unless the owner says
otherwise.

| Source | Follow-up | Why deferred |
| --- | --- | --- |
| #24 `scripts/capture_supervisor.py` | A SIGINT and a SIGTERM pending together during cleanup still make `supervise()` raise without reaping; `deferred_signals()` makes the pair coincide. Deliver held signals one at a time, or enter `cleanup_signals()` before the mask is restored. | Pre-existing gap the PR disclosed; the owner scoped #24 to two races on a paused track |
| #24 | A recorded signal whose previous disposition is `SIG_DFL` is re-raised after the pid save and ends the process without unwinding, leaving the worker running; replayed handlers get `frame=None`. | Only a caller without Python handlers reaches it; `main` installs them |
| #24 | `recorded_signals()` re-implements `sightcircuit/runtime/guard.py:cancellation()`; `deferred_signals()` plus `noted_error()` cost three mask syscalls per 16 KiB chunk. | Structural; the script is quarantined |
| #22 `tools/overlay_bridge.cpp` | The 6 s connect budget covers up to three Background probes, three Overlay `VR_Init` attempts and the backoff; a slow but healthy SteamVR could be cut off mid-`VR_Init` where the old code ran inside the client's 8 s ready wait. | Needs the headset check #22 lists; the budget and the client's wait must move together |
| #22 `sightcircuit/hud/geometry.py` | `clip_to_frame` duplicates the Sutherland-Hodgman loop in `project()`; `scripts/sample_run_context.py` re-validates the interval limits `sensors.run` already checks. | Simplifications |
| #25 `sightcircuit/hud/scene.py` | Validate a report's budgets against the budgets it declares, not the module's constants; hoist the per-call imports and the repeated `contourArea`; write the statistics once per report; collapse `reject_scene`'s duplicate status write. | Folded into package O3, which is in progress with the Frame numbers |
| #27 `sightcircuit/agent/runner.py` | `git()` and `run_task` each hand-roll the hold/Popen/resume signal sequence; one `Interruption` context manager should serve both. | Refactor of a subtle protocol; needs its own review |
| CI | `test_bounded_worker.test_pipeline_cli_parent_cancellation` fails about one run in four on a loaded runner (`cleanup_ok` false after SIGTERM); a cancellation race in the same family as #24's. | Not touched by any merged PR; root-cause it like #24 |

## Coder handoff, 2026-11-07 (agent-runner coder; session closed after the move)

Written by that coder on its last turn and carried here by the planner. Every
result is a cloud check or a CI result of the former repository; none is a
Frame result. Pull-request numbers below are the former repository's unless
they say SightCircuit.

### Item 3: the native tracking reporter, carried over unfinished

Owner's answer: carry it over, unfinished. It is on the SightCircuit branch
`work/native-tracking-reporter-unintegrated` (the former
`ccr-16d7cdfd-8klzgs-item3` at `67997f4`, based on the former main `9c72779`,
rewritten but not renamed and not rebased). It was implemented test-first and
passed two package-review rounds. It is not ready to merge. Remaining work:

1. Integrate with the former #22 (packages K–N), which is merged: resolve the
   conflicts in `sightcircuit/runtime/manifest.py` (keep both pinned-source
   headers) and the six conflicting blocks in `tools/overlay_bridge.cpp`; route
   `init_reporter` through the probe-then-connect and the watchdog; decide
   `--connect-as` for the reporter; send usage errors to the usage stream; add
   the reporter-only mode to `RUNTIME_MODES`; re-arm the stall watchdog in the
   reporter-only loop.
2. Fix three minor source-pin findings from review round 2: the `main()`
   tracking branches; the reporter-only lifetime and status lines; the
   no-overlay guard in `Overlay::events`, which should become
   `if (!api || handle == ...)`.
3. Apply the rename to `sightcircuit` (the branch still says `framejacker`).
4. Run the full checks and a ship review.

Owner decisions that bind it (2026-11-03): tracking tokens stay bound at
declaration, no late binding; the reporter sits behind an explicit flag, off
by default, and without the flag the bridge makes no new OpenVR call;
placement is `--stdin --report-tracking` plus a reporter-only mode, not wired
into any command; its device check needs separate approval. The earlier
conflict map against #22's pre-merge head was never committed; redo the merge
against the current main.

### What that session finished

- #20 (item 1, the E77 and E78 records) and #21 (item 2, observed session
  tokens): merged.
- #24 (capture supervisor races): merged 2026-11-06 at the former `4f4a4a7`,
  the coder's `6207bf6` plus one test commit from the planner; `6207bf6` had a
  green full suite (990 tests, 0 skips) after review cycle 3.
- #26 (the 2026-11-04 handoff): merged as it stood. Its five cycle-1 review
  findings were never folded in: item 4 was already a pull request when the
  handoff merged; E82 is a relayed report but sat under an "all cloud/CI"
  heading with an overstated "runs" claim; the start-here steps were stale;
  the 2026-11-01 rules pointed to a stale next-free evidence id. Check which
  still apply after the move.
- #27 (item 4): the coder's last head was `02c6af0` (1,001 tests, 0 skips,
  three ship-review cycles, an independent check). The planner's ship pass
  then added two commits the coder has not reviewed. It is SightCircuit PR #2.
- Review comments the coder posted on #22, #25 and #23 in the former
  repository; #22 and #25 merged afterwards. Check whether their verified
  findings were addressed, for example: #22's watchdog handler could block on
  a full protocol pipe (fixed in the ship pass); #22's docs said "exit 64"
  where the code exits 2 (corrected in the ship pass); #25's `scene.py` claims
  to identify resized stills but only tells PNG from JPEG (open).

### Still open

- Item 5: the `e008.md` link fix, plus the optional lock file and console
  entry points; waits for the owner's scope answer.
- `test_bounded_worker.test_pipeline_cli_parent_cancellation` flakes about 1
  in 12 runs under load; the race predates the coder's work.
- SIGINT subtests fail when the test process starts with SIGINT ignored; this
  affects the whole suite.
- The capture supervisor's SIGINT+SIGTERM-pending-together gap in
  `cleanup_signals()` is documented and not fixed.
- The Phase 0 approval-scope question is unanswered.
