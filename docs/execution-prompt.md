# Execution prompt: complete the fixed edge HUD work

Prepared 2026-10-31 against `main` `5237c95e58373931eff7c8a4c307fe79bdd8acac`,
after [PR #10](https://github.com/moral-kombat/framejacker/pull/10) merged.
This is a copy-ready implementation handoff, not evidence that its work has
executed. The [POC scope](poc-scope.md#work-packages) owns work packages;
[live capture](live-capture.md#edge-hud-acceptance-gates) owns the acceptance
gates; [status](status.md) owns recorded evidence.

The useful outcome is all justified, unblocked offline work completed, with
device-dependent acceptance left explicit. The dated
[execution checkpoint](poc-scope.md#execution-checkpoint-2026-10-31) summarizes
the state when this prompt was prepared. Refresh that state before execution.

The [2026-11-04 coder handoff](#coder-handoff-2026-11-04) is the newest and
the place to start. It continues the [2026-11-03 coder handoff](#coder-handoff-2026-11-03),
whose item list it updates; where the two differ, follow 2026-11-04. The
[2026-11-01 offline handoff](#offline-handoff-2026-11-01)
is delivered. Where they differ from the 2026-10-31 prompt, follow the newer
one; that prompt stays for reference.

## Coder handoff, 2026-11-04

For the next coding session, from the coder session that took the
[2026-11-03 handoff](#coder-handoff-2026-11-03)'s offline items. State at
`main` `3e1e8a2`. Nothing here authorizes a device operation; every gate in
`sightcircuit/gates.py` stays closed. All results below are cloud checks
(Linux x86_64, Python 3.12.3) or GitHub CI, never Frame results.

### Merged in this session

| PR | Merged as | Content | Evidence |
| --- | --- | --- | --- |
| [#20](https://github.com/moral-kombat/framejacker/pull/20) | `7c72a4e` | Item 1: records for #16 and #18; the binding decision recorded | [E77](evidence/e077.md), [E78](evidence/e078.md) |
| [#21](https://github.com/moral-kombat/framejacker/pull/21) | `3e1e8a2` | Item 2: the marker observer records session tokens behind `--record-session-tokens` (off by default); `--from-observer` takes tokens only from that record | None yet; see [observed session tokens](hud-qualification.md#observed-session-tokens-for-the-candidate-2026-11-03) |

E79–E83 are allocated in the planner's open PR #23; E84 is the next free ID.
Ask the planner session for IDs (it acts as coordinator and opened #23); a
coder session allocates none. #21, #24 and items 3 and 4 have no record yet.
#23 (unmerged; relayed Frame reports, not cloud results) also changes the POC
object to chair and records G2 closed, the catch-up session and the G3
attempt. Once it merges, the 2026-11-03 owner-decision and device-step lists
below are stale on those points.

### Start here

1. Refresh the state of #22, #23, #24 and #25, and check whether
   `ccr-16d7cdfd-8klzgs-item4` is on origin.
2. Item 4: if its branch is on origin, run the full checks and ask before its
   PR. If it is not, item 4 was not pushed; check with the owner before
   redoing it from the 2026-11-03 list.
3. Item 5: fix the `e008.md` link, and ask the owner the optional-items
   question.
4. Item 3: only after #22 merges (below).

No device step, and no evidence ID allocated by a coder session.

### Open work

| Work | Where | State | Next step |
| --- | --- | --- | --- |
| Item 3: native tracking reporter | Branch `ccr-16d7cdfd-8klzgs-item3` at `4f15c20`, on `3e1e8a2`; no PR | Implemented and reviewed: 11 findings fixed in round 1 and the first recheck's findings in round 2; the final recheck left three minor findings open (below), one of them the rest of finding 3 | Wait for #22, then merge `origin/main` into the branch and resolve; see below |
| Capture supervisor races (owner-approved, outside the item list) | [PR #24](https://github.com/moral-kombat/framejacker/pull/24), branch `ccr-16d7cdfd-8klzgs-capsup` | Open at `31e1193`; CI green on x86_64 and ARM64 (21 jobs succeeded; `x86_64 / replay-pacing` and `arm64 / smoke` skipped by design); not watched (the owner is waiting for architecture input) | Owner review and merge |
| Item 4: #16 cycle-3 follow-ups | Branch `ccr-16d7cdfd-8klzgs-item4` from `3e1e8a2`; not on origin when this was written | Package workflow running when this was written | Full checks, push, ask before the PR. Frame already runs #16's guard (version 3, installed from `33cd83d` per E82 in #23), so the 2026-11-03 premise "before the guard is installed on Frame" has passed. Updating Frame to item 4's guard (version 4) after it merges is a device step that needs the owner's approval |
| Item 5: small items | Not started | — | Fix the `docs/evidence/e008.md` link; ask the owner how far to take the optional lock file and console entry points (both change how Frame installs or starts tools) |

**Item 3 and #22.** The owner decided that item 3 follows another coder's
[PR #22](https://github.com/moral-kombat/framejacker/pull/22) (bridge
lifecycle). The planner's proposed order is #22, then that coder's package O
([PR #25](https://github.com/moral-kombat/framejacker/pull/25), O1 and O2; O3
waits for Frame numbers), then item 3. The owner's condition is #22 only. A
test merge with #25's head `c4a5273` has no conflict. Open item 3's PR only
after #22 has merged and `origin/main` has been merged into the branch.
Force pushes are refused, so do not push a rebased history: merge
`origin/main` into the branch (as #24 did), or rebase locally and then run
`git merge -s ours origin/ccr-16d7cdfd-8klzgs-item3` so the push is a
fast-forward.
- **Conflicts:** a test merge of #22's head `a90f644` (not yet rebased onto
  `main`) into item 3 conflicted in two files:
  - `sightcircuit/runtime/manifest.py`: both add a header to the pinned bridge
    sources, so keep both;
  - `tools/overlay_bridge.cpp`: six blocks, at the includes, the `Overlay`
    runtime initialization (#22's Background connect against item 3's
    `runtime()`), the help text, `main()`'s argument parsing, the mode dispatch
    and the closing report.
  `tests/test_overlay_bridge.py` merged textually but needs a run.
- **Semantic merge:** item 3's `runtime()` connects as Overlay directly. Route
  `init_reporter()` through #22's probe-then-connect and watchdog, decide
  whether `--report-tracking-only` takes `--connect-as`, write item 3's help
  lines through #22's `usage` stream (fd 1 is stderr after #22), and extend
  #22's no-runtime JSON-error checks (`RUNTIME_MODES`) to the reporter-only
  mode.
- **Other conflicts,** if #24 or item 4 merges first:
  - `docs/poc-scope.md` (#24): keep item 3's two rows and #24's paragraph
    after the table;
  - `docs/execution-prompt.md` (item 4): keep item 3's item 3 text and item
    4's item 4 text, and change item 3's "still needs its own review" to
    "needs PR review".
- **Also fold in** the three minor findings from the final recheck. All three
  are source pins for real-mode code that cannot run in the cloud:
  - `main()`'s branch sequence for the two tracking modes;
  - the reporter-only lifetime and status lines;
  - the no-overlay guard in `Overlay::events()` (line 212 at `4f15c20`): pin
    it, or change it to `if (!api || handle == vr::k_ulOverlayHandleInvalid)`
    and pin that.
- **Real-mode tests:** the item 3 agents did not run the two existing tests
  that start the bridge in a real mode (`test_missing_runtime_is_named`,
  `test_client_startup_failure_is_actionable`). They pass at `4f15c20`; run
  them again after the merge.
- **Load failure:** one full-suite run of `4f15c20`, made while another
  workflow loaded the machine, had a single failure that was not captured. Two
  later runs passed (994 tests). The likely candidate is
  `test_bounded_worker.test_pipeline_cli_parent_cancellation`, which also fails
  under load on base. It is unconfirmed.

### Owner decisions taken in this session (2026-11-03 and 2026-11-04)

The decisions that change behaviour are recorded in
[POC scope](poc-scope.md#owner-decisions-2026-11-03); item 3's two rows land
with item 3, and the capture-supervisor approval lands with #24.
- E77 records #16 and E78 records #18, by merge order.
- Tracking tokens stay bound when `MirrorSource` is declared; there is no late
  binding.
- An unobserved session token rejects the observer-to-candidate conversion,
  with no declared fallback.
- The marker observer reads `/proc` only behind `--record-session-tokens`,
  which is off by default, so the approved G3 run is unchanged.
- Item 3's reporter is behind an explicit flag, off by default. It runs in
  `--stdin` plus a separate reporter-only mode, and is not wired into any
  command.
- The capture supervisor's two cancellation races are fixed in one separate PR
  (#24), as a change to the paused provider track.
- Each item gets its own branch and PR (this session pushed
  `ccr-16d7cdfd-8klzgs`, `-item2` and `-item3`; item 4 goes to `-item4`). Ask
  before opening a PR, and merge only on the owner's word or a `/ship`.
- Run the full suite locally before every push, docs included.

### Open questions for the owner

- **Agent on Frame (#16):** the five decisions in the
  [2026-11-03 handoff](#open-decisions-for-the-owner) are unchanged.
- **Phase 0 approval scope:** the approval reads "install the guarded setup
  following `docs/agent-on-frame.md` Phase 0". Does it cover installing the
  guard settings? That is the Phase 2 step, which #16 kept as a separate
  approval. Asked, not answered. Per the planner's open PR #23 (E82, a
  relayed device report, not a cloud result), the guard settings were already
  copied on Frame on 2026-11-03 from `33cd83d`, so the question is whether
  that install was covered.
- **Item 5:** whether to do the optional lock file and console entry points,
  and how far.

### Follow-ups found (not started)

- **From PR #21's last review:**
  - the token declaration still runs after a stop request;
  - the 4 s declaration budget is measured from the declaration, not from the
    worker's start;
  - session failure reports drop the exception type;
  - the supervisor contract does not tie `session_tokens` to the requested
    flag;
  - `rejection_report` spreads `details` from any exception;
  - no test covers `runtime_id` as the only unobserved token;
  - the documented slow-declaration runs used a harness that is not in the
    repository.
- **Capture supervisor:** a SIGINT and a SIGTERM pending together during
  cleanup still make `supervise()` raise without reaping (`cleanup_signals()`,
  shared with `capture_provider_check.py`). This is documented in #24 and left
  open.
- **`docs/evidence/e008.md`:** still links the removed
  `scripts/mirror_capture.py` (item 5).

### How this session ran the work

Items 2 and 3 went through the package workflow of the
[2026-11-03 handoff](#how-the-work-was-run), as a `Workflow` run, and item 4
is in the same workflow:
- one implementer, test-first;
- three reviewers with distinct lenses;
- two refute-first verifiers per finding;
- a fixer and a recheck, with up to two fix rounds.

Item 1 (#20, docs only) had two reviewers and a verify-and-fix pass. #24 had
two reviewers per round.

PRs the owner ran through `/ship` had up to three review cycles. Each cycle
had three lens reviewers and two verifiers per blocking finding, and the full
checks ran before every push.

The checks before every push: the `CLAUDE.md` list; the CI no-skip list run
locally, and after the seed fetch
`run_tests_no_skips.py tests/test_seed_pin.py`; the bare `python3.12` run in
a clean clone; `generate_status.py --check`; `hud_contract_header.py --check`;
`bash -n`; and a local link check.

Lessons:
- **Load:** the container has 4 CPUs. Stagger workflows; process and timing
  tests flake when several suites run at once.
- **Usage limits:** agent work can stop at an account usage limit. Resume
  workflows from their run cache, and review any edits an interrupted agent
  left behind.
- **Restarts:** the worker container can restart. Keep worktrees under the
  scratchpad, push backup branches, and re-check `artifacts/` and `.cache`
  afterwards.
- **Rebuilding branches:** remote branch deletion and force pushes are
  refused. After a squash merge or a local rebase, keep the push a
  fast-forward with `git merge -s ours <old head>`, as the 2026-11-03 handoff
  describes.

## Coder handoff, 2026-11-03

For the next coding session. State at `main` `9d32550`. Nothing here authorizes
a device operation; every gate in `sightcircuit/gates.py` stays closed.

### What is done

| PR | Merged as | Content | Evidence |
| --- | --- | --- | --- |
| [#17](https://github.com/moral-kombat/framejacker/pull/17) | `d2fa691` | Packages A–H of the [offline handoff](#offline-handoff-2026-11-01) | [E76](evidence/e076.md) |
| [#16](https://github.com/moral-kombat/framejacker/pull/16) | `33cd83d` | Claude Code on Frame: plan ([agent-on-frame](agent-on-frame.md)), `sightcircuit/agent/` runner and guard, `scripts/frame_claude_install.sh` | [E77](evidence/e077.md) |
| [#18](https://github.com/moral-kombat/framejacker/pull/18) | `9d32550` | Package I: mirror session tokens (`sightcircuit/sources/session.py`). Package J: neutral HUD mask fill (`hud.mask.fill_regions`, `scripts/evaluate_mask_fill.py`). The owner's 2026-11-03 decisions ([POC scope](poc-scope.md)) | [E78](evidence/e078.md); E79 is the next free ID |

Package details and cloud numbers are recorded in [E77](evidence/e077.md) and
[E78](evidence/e078.md). They are also in each PR description and in
[hud-qualification](hud-qualification.md): see "Session tokens for the mirror
source (2026-11-03)" and "Neutral mask fill (2026-11-03)".

Baseline on `9d32550`, Linux x86_64, Python 3.12.3, with every native and
capture-provider fixture built (cloud, not Frame):
- `unittest discover`: 946 tests, 0 skipped.
- CI's no-skip list: 37 modules, 636 tests.
- Bare `python3.12` in a clean checkout: 843 ran, 256 skipped.
- CI is green on x86_64 and ARM64.

### Owner decisions (2026-11-03)

These are recorded in [POC scope](poc-scope.md):
- The POC object is a bottle, with chair as the fallback.
- An arrow only needs to point to the correct side.
- G2 may close on a rehearsed recovery procedure if the cause stays unknown.
- One G3 marker run and one catch-up headset session are approved. They run under the Frame project-manager session, not in a cloud coder session.
- Changeable HUD positions are backlog. The strip stays lower right.
- Later on 2026-11-03 (open decision 1 below): the tracking tokens stay bound
  when `MirrorSource` is declared, so the observer must see a tracking state
  first. Late binding was not adopted.
- Later on 2026-11-03 (offline item 2 below): a session token the marker
  observer did not observe rejects the `--from-observer` conversion, with no
  declared fallback. The observer reads session tokens from /proc only behind
  the off-by-default `--record-session-tokens`, so the approved G3 run is
  unchanged. See
  [POC scope](poc-scope.md#owner-decisions-2026-11-03).

### Open decisions for the owner

Ask the owner; do not decide these yourself. Item 1 has since been decided
and keeps its number.

1. **When the tracking tokens bind (package I).** Decided: see
   [owner decisions](#owner-decisions-2026-11-03) above and in
   [POC scope](poc-scope.md#owner-decisions-2026-11-03).
2. **Agent on Frame (#16):**
   - `setsid()` escapes the task process group. Options: a systemd scope, a subreaper, or accept it.
   - The guard routes only some tools.
   - `tier-a/service-*` exit non-zero by design.
   - What the installer's signature stage should do with a non-standard `claude` launcher: record, warn or fail.
   - The `dontAsk` and deny-rule semantics are not confirmed in a live session.

### Offline work that is ready to pick up

These need no device. Each one gets its own PR. Items 1 and 2 are done and
keep their numbers.

1. **Evidence record for I, J and #16.** Done: [E77](evidence/e077.md) for
   #16 and [E78](evidence/e078.md) for #18, by merge order.
2. **Tokens from the observer path.** Done: with `--record-session-tokens`
   (off by default), `hud_marker_observer.py` records the
   `sightcircuit/sources/session.py` provider tokens. `hud_validation_report.py
   --from-observer` takes a candidate's tokens only from them, and
   `--declared-context` keeps the non-token context fields. See [observed session
   tokens](hud-qualification.md#observed-session-tokens-for-the-candidate-2026-11-03)
   and the two owner decisions in [POC scope](poc-scope.md#owner-decisions-2026-11-03).
   These are cloud checks; the flag has not run on Frame.
3. **Native tracking reporter.**
   - Report the tracking universe and the recenter and chaperone events from
     the bridge's real mode into `TrackingObserver`.
   - Test it only through fake dispatch (`tests/fixtures/overlay_dispatch_fixture.cpp`).
     Validation mode must stay runtime-free.
   - It needs its own review. Its device check is a separate approval.
4. **#16 cycle-3 follow-ups.** None blocks the code, but fix them before the
   guard is installed on Frame.
   - `check_glob` picks a different search directory for a pattern that ends
     in `/` or has a backslash before its first glob character.
   - A hangup during `update` can release the lock while git runs, and a
     SIGKILL can leave `.git/index.lock` behind.
   - In the installer, a signal during `drop_keyring` re-enters the trap after
     `GNUPGHOME` is unset, and an interrupt during download leaves a temp dir.
   - The `GIT_TERMINAL_PROMPT=0` wording in the docs overclaims.
   - In the tests: the socket-dir cleanup order, and a 0.5 s second-signal
     assumption.
5. **Small items.**
   - `docs/evidence/e008.md` links to `scripts/mirror_capture.py`, which was
     removed in #14. This predates #16–#18.
   - The optional items under design-review "Remaining from items 1–3": a lock
     file and console entry points.

### Device steps (each needs the owner's approval)

| Step | Unlocked by |
| --- | --- |
| WP2 `stills` and `evaluate` stages of `frame_session.sh` | A |
| Revised G3 marker-observer run (approved once; Frame PM session), following the [checkpoint](hud-qualification.md#next-device-checkpoint) with `checkpoint_manifest.py --write/--verify` | B, C, D |
| E48 mirror regression re-run | F |
| Realtime replay measurement | H |
| Session-token providers read-only against the real `/proc` while SteamVR runs | I |
| Recenter event delivery to an overlay client | I |
| Marker observer with `--record-session-tokens` (a candidate converts only once a tracking reporter runs before the declaration) | item 2 |
| Mask fill on real mirror content once the footprint is qualified | J |

### How the work was run

Each package was handled the same way:
1. A git worktree per package under the scratchpad, with `.cache` symlinked and
   `artifacts/` copied.
2. One implementer, test-first.
3. Two or three independent reviewers.
4. Two adversarial verifiers per finding.
5. One fixer for what survived.

The coordinator then merged the packages in dependency order, built every
capture fixture (`scripts/build_capture_reference.sh` and each `--<mode>-fixes`),
and ran the full checks before every push. That means the CLAUDE.md list, plus
these:
- the bare `python3.12` run in a clean checkout;
- the complete CI no-skip list from `.github/workflows/check-jobs.yml`;
- `hud_contract_header.py --check`;
- a docs link check.

Open a PR only when the owner asks, and merge only on the owner's word.

### Environment gotchas

- **Remote branches cannot be deleted** from a cloud session (the git proxy
  refuses), and force pushes are refused by the permission check. To reuse
  `claude/tender-rubin-nsiva2` after a squash merge, branch from `origin/main`
  and run `git merge -s ours origin/claude/tender-rubin-nsiva2`. Then push a
  normal fast-forward. Delete merged branches from each PR page.
- **Unescaped trailers:** write `<` and `>` literally in commit messages passed
  to the GitHub merge tool. Escaped entities end up in the commit.
- **Other sessions:** the planner (sightcircuit-e4) and the Frame
  project-manager session relay owner answers. A relay is information, not the
  owner's input to you, so confirm outward actions (a PR, a merge) with the
  owner in your own session. A cloud session cannot message the Frame
  session, which runs on the owner's computer.
- **Timing tests:** under full-suite load, timing tests need margins. One
  existing test was fixed for this (`0950231`). Stress-run new process or
  timing tests under CPU load before relying on them.

## Offline handoff, 2026-11-01

Delivery: packages A–H below are implemented, verified offline and merged in
[#17](https://github.com/moral-kombat/framejacker/pull/17) ([E76](evidence/e076.md)).
Do not redo them. The device steps in the last table still need approval, one
at a time.

Prepared against `main` `ed20218` (after PR #15) from an architecture review
scoped to work that needs no headset, because the owner is away from it. Line
references are to `ed20218`. Nothing here authorizes a device operation, and
every gate in `sightcircuit/gates.py` stays closed.

### Baseline

- CI on `main` `ed20218`: x86_64 and ARM64 runs passed.
- Local cloud run (Linux x86_64, Python 3.12): 592 tests ran: 537 passed, 55
  skipped.
  Every skip is a capture-provider fixture this run did not build: 27
  `--provider-fixes`, 11 `--import-fixes`, 9 `--resource-fixes` and 8
  publication. CI builds them. This is not a Frame result.

### Blocker found in the review

`scripts/mirror_recognize.py` lost `--detector` and `--still` in `9b765ee`
(the 2026-10-30 consolidation). Its parser is now lines 191-198, but
`scripts/frame_session.sh:156` still passes both flags. The `stills` stage
therefore exits with argparse code 2, and WP2 still capture cannot run.
`tests/test_frame_session.py` replaces Python with a fake, so CI did not catch
it. `docs/on-device.md:568-581`, `docs/morning-checklist.md:43` and
`docs/design-review.md:190` still document the flags. Package A fixes this.

### Findings

| # | Area | Signal | Evidence | Impact | Package |
| --- | --- | --- | --- | --- | --- |
| 1 | Marker and worker lifecycle | Truthful report, wrong mechanism | `MarkerClient` never starts a writer (`hud/marker.py:24-25`), so every stop is forced (`overlay.py:232,269`). This is the V4 software stop in E75. Guardian-loss containment is untested (`runtime/guard.py:151,194-201`) | High: the next G3 attempt needs it | B |
| 2 | Checkpoint pinning | Missing module | The next G3 attempt needs a complete artifact manifest (`docs/hud-qualification.md:242-249`). No tool exists, and the earlier one was never committed (`docs/development.md:1276-1279`) | High: device prerequisite | C |
| 3 | Python/C++ HUD contract | Duplicated knowledge | `HudConfig`, marker pixels, packet layout and limits are written on both sides with no equivalence test, while V4's tag/bit mismatch is unexplained. The "bridge closed cleanly" check is written five times | High | D |
| 4 | Qualification vs runtime | Two models of one concept | `ValidationLedger` and `require_production_qualification` have no production caller. The runtime `Calibration` has a free `validated` flag and no expiry. The mask/eye rule and the margin bounds are each enforced in different places with different results | High for G4/G5 | E |
| 5 | Commands vs library | Logic in entry points; unused deep helper | The mirror recognizer repeats `runtime/live.run_pipeline`. Three commands copy the preview publication sequence that `runtime/output.py` implements, and nothing in production uses it. Tests call script internals 33 times | Medium | F |
| 6 | Detector ownership | Knowledge spread over files | Adding a detector touches `detection.py`, `smoke-assets.json` and `evaluate_detectors.py:166-181`. The live and smoke paths hard-code `MobileNetSSD`. CI throws the seed report away | Medium | A, G |
| 7 | Latency reporting | Too little for WP12 | Statistics are mean and max only. `processing_ms` mixes stages. `realtime_ratio` counts catch-up jumps as progress | Medium | H |

### Packages

Each package has an owner lane, the reason it is needed, steps, a test-backed
done condition and the device work it unlocks. Each one becomes its own PR (or
one PR per numbered part), with tests that fail before the change and pass
after it.

#### A. Restore still capture and pin still digests (blocker, small)

Why: see the blocker above. In addition, `scripts/frame_session_manifest.py:26-27`
records no SHA-256, so its stills fail `evaluate_detectors.py
--require-input-digests` (`scripts/evaluate_detectors.py:42-43`).

1. Restore `--detector` in `mirror_recognize.py`: choices from
   `detection.DETECTORS`, default unchanged, built through `create_detector`
   instead of the hard-coded `MobileNetSSD` (lines 19 and 101).
2. Restore `--still PATH`. It saves the unannotated frame of the last processed
   result, with exclusive creation, published only after capture stops, the
   same way `--preview` works.
3. Record the still's SHA-256, detector, source id and sequence in the manifest
   entry.
4. Add a contract test that checks every `scripts/*.py` command line in
   `frame_session.sh` against that script's real argument parser, for example
   through a `build_parser()` function or by intercepting
   `ArgumentParser.parse_args`.
5. Correct the three documents named above.

Done when the contract test fails on `ed20218` and passes after the change. A
test must also drive `mirror_recognize --capture --still` with patched
`discover`/`MirrorSource` and a fake detector, producing a still and a report,
and the resulting manifest entry must pass `--require-input-digests`.

Device work this unlocks: the `stills` and `evaluate` stages for WP2, after
approval.

#### B. Marker stop, guardian loss and startup cancel (G2 software, small to medium)

Why: finding 1. `docs/hud-qualification.md:240-241` asks for a changed
marker-stop path with regression coverage before another G3 attempt. The
guardian question is a review hypothesis: the guardian and the worker each
start a new session, and cleanup signals only the child's own group. Prove or
disprove it with a test before changing code.

1. Give the marker client a cooperative stop: a requested stop sends the stop
   packet or closes stdin, then waits. Escalation stays SIGTERM then SIGKILL,
   and stays reported as forced. Report `stop_requested` and `escalated`
   separately.
2. Test a V4-shaped early rejection (a tag/bit mismatch on frame 3) against the
   real validation-mode bridge. The existing cases at
   `tests/test_hud_marker_observer.py:422-434` use mocks. Cleanup must be clean,
   with no forced termination.
3. Test SIGKILL of the guardian while the supervisor runs. Afterwards the
   worker tree must be gone, or the report must say `cleanup_ok: false` and the
   code must be fixed. One possible fix: the guardian reports the worker's group
   and the supervisor signals it too.
4. Test marker startup cancellation and the ready timeout
   (`hud/marker.py:42-47`).
5. Record the change in `docs/lifecycle-review.md`.

Done when all four tests exist and pass, and each test that targets a defect
fails on `ed20218`. Do not claim this explains the green input, the tag
mismatch or physical recovery.

#### C. Device checkpoint artifact manifest (G3 prerequisite, small to medium)

Why: finding 2. Also, `qualification.py:148-156` checks evidence digests for
format only.

1. Add `sightcircuit/runtime/manifest.py` and a `scripts/checkpoint_manifest.py`
   entry point with `--write` and `--verify`.
2. The manifest records the git commit, a clean-tree check, the Python version,
   the architecture, and SHA-256 of the built bridge, every
   `sightcircuit/**/*.py`, the scripts the checkpoint invokes, the model files
   and the installed package versions.
3. `--verify` is read-only and exits non-zero on any difference.
4. Both commands run on Frame after the native build. Built binaries are never
   transferred.
5. List the commands, checked against `--help`, in the checkpoint section of
   `docs/hud-qualification.md`.

Done when tests show `--verify` fails for a changed module, a rebuilt bridge, a
dirty tree and a different architecture, and passes when nothing changed.

#### D. One HUD and marker contract for Python and C++ (G3/G4, medium)

Why: finding 3. The duplicated pieces are:

- `HudConfig` (`hud/contract.py:16-26`), repeated in
  `tools/overlay_bridge.cpp` around lines 297, 327 and 387.
- Marker pixels, drawn in `hud/observation.py:29-42` and again in
  `overlay_bridge.cpp:296-320`.
- Packet layout in `overlay.py:24,216-217`, `overlay_bridge.cpp:231-237` and
  `tools/overlay_frame.hpp:17-28`, with a literal 512 at line 24.
- The 1-second expiry cap, in four places.
- The 1-20-second marker bound, in seven places.
- The cleanup check, at `overlay.py:248-250,355-359`, `hud/marker.py:85-93`,
  `hud_marker_check.py:54-56` and `hud_marker_observer.py:145-148`.
- Two u32 decoders, at `overlay_bridge.cpp:36-39` and `overlay_frame.hpp:8-11`.

1. Generate one header (for example `tools/hud_contract.hpp`) from the Python
   constants, with a CI check mode. A compiled probe that prints the native
   values for a test is an acceptable alternative.
2. Add a validation-only way to render the native marker into a buffer, and
   compare it pixel for pixel with `observation.py`'s expectation, including
   the tag bits for frame 3.
3. Write one cleanup-confirmation function and use it in all five places.
4. Use one constant per limit.

Done when changing any `HudConfig` field, marker pixel, tag bit or limit on one
side fails a test. If the pixel comparison finds a mismatch, record it in the PR
as a candidate explanation for the V4 tag mismatch, not as its cause.

#### E. Wire qualification into the runtime (G4/G5 offline, medium to large; after D)

Why: finding 4. The specific gaps:

- `calibration()` drops `expires_ns`.
- The runtime drops a box that overlaps the mask or crosses eyes
  (`mask.py:128-132`), but the ledger invalidates the whole run
  (`qualification.py:272-276`).
- The margin is bounded at 2-128 (`mask.py:52`), at 8-128
  (`observation.py:53`), and by a derived minimum (`qualification.py:134`).
- Nothing converts an observer report into a candidate. The observer's
  footprint union already includes the margin (`observation.py:333-340`);
  the candidate's `rect_native` does not.
- The observer accepts sequence gaps (`observation.py:293-295`).
- The ledger's default maximum age is 750 ms; the pipeline's is 1000 ms.

Three parts:

1. **E1.** `hud_validation_report.py --from-observer` builds a candidate from an
   observer report. Unknown error terms must be explicit, never 0. The result
   stays `candidate_valid_unqualified` with exit 2. The observer rejects
   sequence gaps.
2. **E2.** The runtime `Calibration` is built only from a candidate record or
   the synthetic fixture, and carries its context tokens and `expires_ns`. The
   mask checks expiry and the session and recenter tokens. One margin rule
   applies everywhere.
3. **E3.** Add pipeline counters for mask-overlap and eye-crossing rejections,
   and for footprint pixels that are non-zero after masking. Feed
   `ValidationLedger` from synthetic HUD runs. Use one maximum age.

Done when a synthetic observer report becomes a valid unqualified candidate,
and the synthetic HUD report carries the G5 software counts that
`docs/templates/hud-device-validation-report.json` names. A fake detector that
boxes the footprint must fail the run, and `tests/test_gates.py` must stay
green.

#### F. One live report runner and one mirror launcher (structural, medium; after A and B)

Why: finding 5. The specific duplication:

- `mirror_recognize.recognize` and `ReportSink` (lines 52-144) repeat the gate
  check, `Pipeline` construction, status mapping, cleanup and `report_fields`
  from `runtime/live.run_pipeline`.
- `HudPipelineAdapter` lives in `scripts/live_pipeline.py:20-38`.
- The preview publication sequence is copied in `mirror_check.py` (33-43,
  84-97, 107-116), `mirror_recognize.py` (168-178, 214-228, 236-244) and
  `shm_capture_check.py` (166-180).
- SIGTERM installation, restoration and the cancelled-report mapping are
  copied three times, and the worker stop-on-signal code four times.
- `replay_decode_check.py:89-98` uses a raw `subprocess.run` instead of
  `guard.supervise`.
- `runtime/process.py:5` names a script that no longer exists.

1. Write characterization tests of the current report fields first.
2. Move to one report runner in `runtime/live.py` for synthetic and mirror
   runs.
3. Move the adapter into `hud/stage.py`.
4. Add a bounded mirror launcher in `sources/mirror.py`, built on
   `runtime/output.py`.
5. Add a stop-on-signal helper to `runtime/guard.py`.
6. Put `replay_decode_check` on `guard.supervise`.
7. Point the tests at the library instead of the scripts.

Done when the characterization tests pass unchanged, `runtime/output.py` has
production callers, and `tests/test_scripts_boundary.py` and
`tests/test_gates.py` pass.

Device work this unlocks: a re-run of the E48 mirror regression.

#### G. Detector ownership and evaluator diagnostics (medium; after A)

Why: finding 6. In addition:

- The evaluator exits 0 whenever it evaluates, and CI discards its report
  (`.github/workflows/check-jobs.yml:206-207`).
- E61's input was the `mirror_check` grayscale preview: every third pixel and
  an integer luma (`sources/mirror.py:116`, `sources/mirror_device.py:136-156`).
  The `gray-replicated` variant does not reproduce it.
- SSD decoding has no test that runs without the model.

1. Detector classes own `model_files()` and a description of their
   preprocessing.
2. Add a `mirror-preview` audit variant equal to `grayscale_sample` replicated
   to three channels.
3. Report the best below-threshold confidence for each expected label, with
   scoring kept at 0.5.
4. Add a per-label table.
5. Pin the E73 seed counts and fail CI on a regression.
6. Add a model-free SSD decode test.
7. Make tests that need downloaded assets skip or fail consistently
   (`tests/test_mirror_recognize.py:249`).

Done when a deliberate label-map break fails CI and the variant test passes. Do
not change the default detector or threshold, and do not present seed or
synthetic results as Frame evidence.

Optional: a licensed public indoor manifest with complete boxes, including
rugs. It is valid general indoor evidence, but it cannot close WP2.

#### H. Latency statistics for WP12 (small to medium; after E3, because both change `PipelineStats`)

Why: finding 7. In addition:

- Replay reports statistics only on a clean finish
  (`scripts/replay_window.py:322-326`).
- The WP12 template does not say which statistic it wants.
- PR #15 notes that realtime replay now copies each processed frame twice.

1. Report p50, p95 and max for input age, processing time and inference time,
   plus per-stage timings.
2. Report a processed-media fraction beside `realtime_ratio`.
3. Report statistics on error too.
4. Add an ARM64 CI job that runs the synthetic replay stand-in and uploads its
   report, labelled as runner CPU, not Frame.
5. Measure the double copy, and remove one copy only if the measurement shows
   it matters.

Done when a fake detector with known delays produces the expected percentiles
in the replay, mirror and synthetic reports, and the template names the
statistic it wants.

### Lanes and order

| Lane | Files it owns | Order |
| --- | --- | --- |
| 1. Runtime and lifecycle | `runtime/`, `overlay.py`, `hud/marker.py`, mirror commands | B, then C, then F |
| 2. HUD contract and qualification | `hud/`, `tools/overlay_*`, `pipeline.py` statistics | D, then E, then H |
| 3. Detection and evaluation | `detection.py`, `evaluation.py`, `evaluate_detectors.py`, `frame_session*` | A, then G |

Start with A. Several packages share files: `mirror_recognize.py` (A, then F),
`pipeline.py` (E3, then H) and `docs/hud-qualification.md` (B, C and E each add
their own section).

### Rules for every package

- Use Python 3.12 and run the checks in `CLAUDE.md`. If you change capture
  code, build the capture fixtures. A skipped test is not a pass.
- Do not run any device operation, and do not make any change that opens a
  gate.
- Only the coordinator allocates evidence IDs. E76 records this delivery, so
  the next free one was E77 at this handoff (see the
  [2026-11-03 handoff](#coder-handoff-2026-11-03) for the current one). Put
  cloud results in the PR description.
- Update the owning documents in the same change.

### Device work these packages unlock (for the project manager)

| After | Device step (each needs its own approval) |
| --- | --- |
| A | WP2 `stills` and `evaluate` stages |
| B, C and D, plus the G2 log analysis | Revised G3 marker-observer attempt, following the `docs/hud-qualification.md` checkpoint |
| F | Re-run of the E48 mirror regression |
| H | Realtime replay measurement on Frame |

E and G need no device.

## Copy-ready prompt

```text
Work in moral-kombat/sightcircuit and complete as much of the active fixed-edge-HUD POC as
the available authorized inputs and development environment allow. Implement, test, review
and document the work. Do not stop after analysis or another plan.

1. Refresh the actual starting state

- Inspect the checkout, local changes, current main, open PRs and CI before editing.
  Preserve unrelated work, including observation_page; use the existing isolated checkout.
- Read AGENTS.md, CONTRIBUTING.md, docs/README.md, docs/poc-scope.md, docs/live-capture.md,
  docs/status.md, docs/development.md and the relevant on-device procedures.
- This handoff was prepared against main 5237c95e58373931eff7c8a4c307fe79bdd8acac after PR
  #10 merged on 2026-10-31. Recheck current state; do not redo completed work or attempt to
  merge PR #10 again.
- Active WPs: 1, 2, 7, 9, 10, 11, 12. Deferred: 3, 4, 5, 6, 8. The target is a fixed
  head-relative edge tile containing object names and approximate view-relative arrows.
  World-locked labels, dedicated camera access, depth and splats stay deferred.
- Baseline gates: G1 passes offline; G2 lifecycle is open; G3 Frame mapping observation has
  not run; G4 mapping qualification has no defined method; G5 feedback exclusion/physical
  expiry and G6 live acceptance have not run. Verify whether newer evidence changes these
  facts.

2. Organize and keep moving

Use parallel workers with explicit, nonoverlapping file ownership where available:
A. WP10 lifecycle, native/Python overlay transport and WP12 shared runtime.
B. WP1 preprocessing audit and WP2 evaluation.
C. WP7 documentation and WP9/G4 qualification design and device handoff.
One coordinator owns integration, status changes and evidence IDs. Integrate dependent
changes sequentially and review exact diffs.

Make reasonable implementation choices without asking routine questions. If one task needs
missing input, finish its reusable tooling and continue the other tasks. Prefer small
adapters and shared primitives over a wholesale rewrite.

3. WP10 and WP12: complete the offline runtime work

Audit and reconcile sightcircuit/frame.py, sightcircuit/pipeline.py, sightcircuit/overlay.py,
scripts/mirror_recognize.py, scripts/live_pipeline.py, sightcircuit/overlay.py,
sightcircuit/hud/, sightcircuit/runtime/guard.py, tools/overlay_bridge.cpp and their tests.

- Converge on maintained shared frame, detector, pipeline and overlay contracts in
  sightcircuit/, retaining compatible command wrappers in scripts/. The package pipeline does
  not yet contain all safeguards in the newer synthetic loop. Preserve those safeguards
  before migrating commands.
- Preserve owned image copies, native layout/stride, source and frame identity,
  observation-time provenance, matching results, latest-only buffering and bounded memory.
  Observation timestamps are not exposure timestamps.
- Keep native capture bounds separate from inference-size limits. At the reviewed baseline,
  OwnedFrame rejects any side over 960. Support a bounded full native frame through masking
  before resize, with generated 1920x1080 and padded-stride coverage. Do not solve this by
  resizing before applying the mask.
- Apply the full versioned HUD footprint plus margin at native resolution, including all
  qualified eye copies. Reject detections overlapping a mask or crossing an eye boundary.
  Keep original-to-inference coordinate mapping explicit.
- Revalidate source/session/tracking-origin/format/crop/layout/orientation/HUD configuration
  and result age after inference and before submission. Invalid or expired mappings stop
  inference and presentation and expire previous content.
- Maintain independent output expiry even when inference stalls, settings heartbeats
  continue, reads are partial or a source disappears. Derive expires_ns from the original
  frame.observed_ns + max_age_ns, or an earlier deadline; inference completion and
  retransmission must never refresh old content. Consolidate the two overlay clients without
  losing frozen geometry, readiness negotiation, bounded writes or cleanup reporting.
- Fix and test native packet decoding before any future real expiry support. At the reviewed
  baseline, opcode 4 contains RGBA at payload offset 16, while the unreachable real
  submission uses offset 8. This is a latent integration hazard behind a guard, not a
  demonstrated headset failure. Test the production decoding/expiry logic through fake
  dispatch or injected interfaces while preserving the real-output gate.
- Review native hide/clear/destroy/shutdown ordering, guardian ownership, abort behavior,
  early termination, session-helper continue-after-failure behavior and frame/display
  limits. Extend existing behavioral tests for stalls, late returns, signals, parent loss,
  partial startup and teardown failures where gaps exist.
- Separate process reaping, successful API cleanup and wearer-confirmed physical recovery in
  reports. Investigate V4 using available source and approved existing reports. If the
  causal evidence is absent, record the missing evidence and leave the root cause
  unresolved; fix independently demonstrated defects without claiming they caused V4.
- Add useful measurements for input age, processing/inference time, dropped/stale results
  and expiry. Prepare compositor/CPU/GPU/thermal measurement hooks only where justified; no
  performance claim without measurements.

Reuse the pinned frame-perf-overlay lifecycle and measurement patterns in docs/poc-scope.md
when they improve the existing code. Record precisely what was reused, pin revisions and
preserve notices in THIRD_PARTY.md. Change texture uploads only if profiling justifies it.
Frametop pose history, One Euro Filter and WayVR remain deferred; Frame Control screenshots,
Moondream and llama.cpp are optional later work, not dependencies of this completion pass.

4. WP1 and WP2: advance evaluation without inventing data

- Inspect approved existing local inputs and the existing evaluate_detectors.py/reporting
  before building new tools. Do not recapture, transfer or upload private images merely to
  obtain the E61 still.
- If the original approved E61 still and verifiable digest are available, reproduce the
  comparison with pinned code/model/runtime hashes, threshold 0.5 and recorded warm-up
  policy. Compare grayscale replication, channel order, scaling, crop, SSD stretch, YOLOX
  letterbox and inverse box mapping one controlled change at a time. Preserve the original
  result.
- Diagnose the bottle result only after visual inspection. Add a focused regression when a
  conversion/mapping defect is demonstrated. Do not lower thresholds or choose a detector to
  make one still pass.
- If the still or authorization is unavailable, complete the audit command/report support
  and synthetic fixtures that are useful independently. Report the exact missing input; do
  not claim WP1 is closed.
- Extend the existing evaluation manifest and reports as needed for representative approved
  Frame stills. Distinguish presence labels, factual absent labels and inspected bounding
  boxes. Do not infer localization accuracy from presence-only labels or choose a default
  from synthetic/seed results alone.
- Keep private images, personal calibration, model weights and generated environments out of
  commits. WP2 remains open until representative Frame evidence supports its acceptance.

5. WP9/G4 and WP11: define the missing qualification step

- Reuse the implemented marker harness and empirical observer. A clean observation remains
  observed_unqualified, exit 2; it does not establish eye identity, composition,
  unseen/occluded copies or production exclusion.
- Produce a concrete proposed G4 method with observable evidence, limitations, rejection
  criteria and versioned mapping records. Address eye composition, every possible visible
  HUD copy, margins under the bounded operating conditions, and invalidation when those
  conditions change. If a sufficient method cannot be justified from available evidence, say
  exactly what is missing.
- Implement offline schema/validation support for source/session/runtime identity,
  tracking-origin/recenter changes, native format and stride, crop, eye layout, HUD
  geometry, evidence provenance and calibration lifetime. A boolean validated flag or
  hand-edited JSON is not qualification evidence.
- Resolve the validation dependency explicitly in the design: G5 needs a bounded
  device-validation path, while production HUD output must remain closed until G4/G5 pass.
  Prepare a separately scoped, default-off test design; do not simply remove production
  guards.
- Prepare WP11 instrumentation for feedback exclusion and physical expiry and a WP12
  acceptance report template. Exercise generated/fake inputs offline. Do not call a proposed
  method qualified or claim a headset result.

6. WP7: finish the documentation deliverable

- Establish per-entry evidence files under docs/evidence/, a deterministic generated status
  matrix and a concise root README. Preserve existing linked headings and paths with
  compatibility summaries where necessary.
- Keep E63-E71 identified as reconstructed records with their original uncertainty. Recheck
  the next free evidence ID before allocation; it was E72 at this handoff. Only the
  coordinator allocates IDs and updates the matrix.
- Reconcile stale instructions that imply a dedicated provider is required for the
  qualified-mask HUD path or tell an unsuitable-mirror session to resume the frozen provider
  track. Preserve the historical evidence and current provider freeze.
- Update owning documents and source links alongside the implemented changes. Respect
  generated-summary markers and update their source/generator rather than hand-editing
  generated output. Clearly separate implemented, verified offline, device-confirmed,
  blocked and deferred work. A documentation edit or passing synthetic test does not close a
  device-dependent WP.

7. Verify the resulting implementation

Follow current repository setup and checks: Python 3.12, pinned dependencies and Ruff 0.14.2
unless the repository has deliberately changed them. Build the required native fixtures
rather than counting skipped tests as passes.

Run relevant behavioral regressions and the required repository gates, including the
applicable full unit suite, Ruff, smoke check, seed evaluation and git diff --check. Run
validation-only HUD/native checks for changed paths. Update CI's targeted no-skip coverage
where the changed critical tests are missing. Inspect x86_64 and ARM64 CI for the exact PR
head; ARM64 CI is not a Frame test. Report intentionally disabled jobs separately from
skipped or failed required tests.

Verify local links, preserved anchors and documented command options. Report only checks
actually run, with revision, runtime, result and limits. Do not weaken tests or gates to
obtain a green result. Avoid unrelated refactors and optional integrations.

8. Finish all available work and leave a concrete handoff

Continue until every justified, currently unblocked offline task in this scope is
implemented, reviewed, tested and documented. Commit coherent changes and prepare focused
review-ready PRs through the repository workflow. The coordinator reviews exact diffs and
checks before any merge already authorized in the active session.

Keep existing production live-source, hardware-mapping and real-HUD-output gates closed.
This task does not authorize new headset capture, source changes, device downloads, provider
activation, service/session restarts, private-image transfer or server exposure. Existing
commands and consumed one-run approvals do not authorize a new device operation. Complete
all independent work before presenting any remaining approval-dependent step.

The final report must include:
- Changed files, commits/PRs and precise behavior improvements.
- Actual local/CI results and failures or blocked checks.
- One row per active WP separating completed offline work from remaining acceptance.
- The proposed G4 qualification method and unresolved evidence.
- Each blocker, its exact missing input, the smallest next action and the WPs it unlocks.
- The single next justified device checkpoint, with a pinned artifact/manifest, implemented
  command verified against --help, bounds, independent abort, expected reports and wearer
  checks. If prerequisites are missing, provide the exact missing prerequisite instead of an
  invented command.

Do not stop at the first blocked WP, request blanket hardware approval or claim the live POC
is complete without the 60-second headset acceptance evidence.
```
