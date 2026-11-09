# Documentation plan and ownership

SightCircuit documentation is written for a contributor joining the project for
the first time. Start at the [documentation index](README.md). It separates
learning, development, device operation, technical reference, and history.

The overhaul changes how information is explained and found. It does not change
the POC, implementation priorities, acceptance evidence, or device permissions.
The [current work page](live-capture.md) remains the work-order authority.

## Authoritative homes

| Topic | Home | Maintenance rule |
| --- | --- | --- |
| Project purpose and entry points | [Root README](../README.md) | Briefly describe the goal, current maturity, and where to start. |
| Navigation and terminology | [Index](README.md), [glossary](glossary.md) | Group by reader task; introduce terms before IDs. |
| Verified capabilities | [Generated status](status.md) | Edit reviewed `status-data.json`, then regenerate. |
| Observations and provenance | [Evidence records and catalog](evidence/README.md) | Preserve IDs and history; the coordinator registers new evidence. |
| Current work, blockers, and gates | [Current work and acceptance](live-capture.md) | Update completion and next actions here, rather than in several handoffs. |
| Scope and accepted decisions | [POC scope](poc-scope.md) | Keep durable decisions and completion criteria; link current progress. |
| Contributor setup and checks | [Development](development.md), [Contributing](../CONTRIBUTING.md) | Put prerequisites before commands; link detailed subsystem references. |
| Frame access and task selection | [On-device entry point](on-device.md) | Explain execution location, access, and inventory. |
| Device procedures | [Replay](guides/replay.md), [mirror recognition](guides/mirror-recognition.md), [Frame sessions](guides/frame-sessions.md), [HUD checkpoint](guides/hud-checkpoint.md), [fallback measurement](guides/fallback-measurement.md), [camera SDK probe](guides/camera-probe.md), [camera recognition](guides/camera-recognize.md), [VR panel](vr-overlay.md) | Keep one canonical runnable procedure per task, including output and shutdown. |
| Design and contracts | [Architecture](architecture.md), [HUD qualification](hud-qualification.md), [camera reference](steam-frame-camera.md), subsystem references linked from development | Separate implemented paths, proposed methods, and paused work. |
| Camera SDK containment | [Camera helper reference](reference/camera-helper.md), [third-party sources](../THIRD_PARTY.md#arcturus-vision-camera-preview-sdk-headers); the device procedure is the [camera probe guide](guides/camera-probe.md) | Keep the containment rules beside the tests that pin them; state cloud checks, CI results and device reports separately; the Client Library is never named outside the build script, its tests and these pages. |
| Agent operation and design | [Frame sessions](guides/frame-sessions.md), [enforcement test](guides/agent-enforcement-test.md), [agent design](agent-on-frame.md) | Keep supported operation separate from the proposal and historical installation claims; the enforcement test guide owns the Phase 2 scope until its device result is recorded. |
| Session orientation | [Execution handoff](execution-prompt.md), [AGENTS](../AGENTS.md), [CLAUDE](../CLAUDE.md) | Link current owners; do not accumulate dated work orders. |
| Historical context | [History index](history/README.md), [previous README](project-history.md) | Label snapshots and direct readers to maintained guidance. |
| Licenses and provenance | [Third-party sources](../THIRD_PARTY.md) | Preserve notices, source revisions, and original repository URLs. |
| Documentation conventions | This document | Update when ownership, structure, or validation changes. |

The author changing a component updates its owning documentation in the same
change. A reviewer checks that any brief summaries still agree with that home.

## Content rules

### Write for someone new

- Lead with the task or concept in plain language. Expand abbreviations on first
  use and link the [glossary](glossary.md) for recurring terminology.
- Start a guide with purpose, prerequisites, execution environment, and expected
  outcome. Follow with commands, output interpretation, troubleshooting, and
  shutdown. Keep operating restrictions beside the affected command.
- Prefer short sections and descriptive link labels. Keep chronological accounts
  and long technical tables out of onboarding. Use a diagram when it clarifies
  a real data flow; distinguish implemented and future paths.
- Say “lifecycle acceptance (G2)” or “recovered marker evidence (E75)” before
  relying on an identifier. A reader should not need to memorize work packages.

### Keep facts in their proper context

- Record full evidence once. Distinguish implementation, offline tests, CI,
  headset reports, and upstream claims. A successful process exit cannot certify
  physical recovery, and a documentation review date is not a device-test date.
- Preserve measurements, failed outcomes, source pins, reconstructed provenance,
  and unknown fields. Add later context outside a historical narrative rather
  than rewriting what was observed.
- The overhaul baseline contained E1–E78. Evidence E79–E84 from the former
  [Framejacker PR #23](https://github.com/moral-kombat/framejacker/pull/23)
  is now merged through [SightCircuit PR #1](https://github.com/moral-kombat/SightCircuit/pull/1).
  Preserve those records and the resulting reviewed status when reconciling
  documentation. Check the current index and pending allocations before adding
  an ID; a historical handoff is not allocation authority.
- Keep command options and report fields consistent with source. Planned tools
  are not runnable commands. Paused provider procedures belong in reference or
  history, outside the normal contributor path.
- Preserve historical GitHub links: SightCircuit and Framejacker have different
  repository identities. Do not replace repository names inside old PR or source
  URLs. The [commit-history mapping](history-rewrite-2026-11-07.txt) explains
  rewritten internal revision identifiers.

### Move content without breaking readers

- Preserve original file paths and heading anchors. Leave a short forwarding
  section or an explicit anchor with a descriptive link to the new home.
- Update current navigation to point directly to the canonical page. Forwarding
  sections provide compatibility, not another copy of the instructions.
- Move documentation-dependent tests with the canonical procedure. Preserve
  command parsing, script pinning, source boundaries, and verification ordering.
- `legacy-anchors.json` freezes original compatibility anchors. Do not remove
  entries merely to make a migration pass. New maintained links are checked too.

## Consolidation checklist

The implementation is organized into three reviewable batches:

1. **Factual corrections and ownership:** fix stale instructions and capability
   wording; give current work, scope, evidence, and history separate roles.
2. **Guide extraction and rewriting:** shorten entry pages, add focused task
   guides and references, and preserve old destinations with forwarding sections.
3. **Generated navigation and automated checks:** group status, generate the
   evidence catalog, check local links and compatibility anchors, and migrate
   procedure tests without weakening their assertions.

The result is complete when a newcomer can reach setup and its smoke check,
find the architecture and current work, and submit a change without reading
historical experiments. A headset operator must reach one canonical procedure
with clear prerequisites and shutdown guidance. Every historical page must
identify itself as historical.

## Verification and continuing maintenance

Run from the repository root after editing documentation sources:

```bash
python3 scripts/generate_status.py
python3 scripts/generate_status.py --check
python3 scripts/check_docs.py
python3 -m unittest discover -s tests -p 'test_generate_status.py'
python3 -m unittest discover -s tests -p 'test_check_docs.py'
git diff --check
```

The generator owns the whole status page and marked blocks in architecture and
the evidence README. Edit its reviewed sources, not the generated output. The
catalog's descending evidence IDs express recording order, not execution dates.
Capability groups must include every capability exactly once.

The offline documentation checker validates local files, Markdown fragments,
and compatibility anchors. It does not fetch remote URLs or execute code
blocks. Check changed commands against their parsers and source as well.

When moving or changing procedure examples, run the documentation-dependent
`test_scripts_boundary`, `test_checkpoint_manifest`, and
`test_hud_marker_observer` suites in the development test environment. The
[development guide](development.md) explains dependencies and native builds.
Use the smoke check to validate the Linux setup path; do not execute headset
procedures to validate a documentation edit.

For a session handoff, register any reviewed evidence through its coordinator,
regenerate summaries, and update the current work page. Link those sources from
the short handoff instead of copying their status into another dated checklist.

## Evidence migration (WP7)

The original evidence migration placed E1–E71 in individual files and preserved
status anchors; subsequent records extend that catalog. E63–E71 retain their
reconstructed provenance, including unknown execution dates and revisions.
The [previous README](project-history.md) and dated history retain earlier
context. This documentation overhaul adds no device evidence or acceptance.
