# Documentation plan and ownership

This plan organizes project documentation around navigation, operating
procedures, evidence, design and current work. Existing file paths and heading
anchors are preserved. [The index](README.md) is the starting point.

## Authoritative homes

| Document | Owns | Update when |
| --- | --- | --- |
| [Root README](../README.md) | Overview, brief status, Frame/cloud entry points | Project scope or entry points change |
| [Documentation index](README.md) | Task-based navigation and reading order | A guide is added or its role changes |
| [Status](status.md) | Capability matrix, device/cloud evidence and unknowns | A new result or compatibility finding is reported |
| [On-device guide](on-device.md) | Frame access, inventory, recording/replay procedures and session handoff | Device commands or operating procedures change |
| [VR overlay](vr-overlay.md) | Panel launch, UI controls, shutdown and native transport reference | Panel behavior, options or protocol change |
| [Development](development.md) | Cloud/desktop setup, dependency environments, builds, tests and CI | Setup, dependencies or checks change |
| [Architecture](architecture.md) | Components, frame flow, concurrency, interfaces and long-term acceptance criteria | A design or interface changes |
| [Live capture](live-capture.md) | Current capture work order, dependencies and acceptance checks | A task is completed or the chosen path changes |
| [POC scope](poc-scope.md) | Active/deferred work packages, completion criteria and upstream reuse priorities | Scope or work-package state changes |
| [Execution prompt](execution-prompt.md) | Dated copy-ready handoff for completing unblocked implementation work | A new implementation handoff is prepared; refresh current sources before using it |
| [Camera reference](steam-frame-camera.md) | Camera interfaces, compatibility research and pinned references | A source/runtime review produces new findings |
| [Capture review](capture-reference-review.md) | Dated code analysis and the reasoning for the reviewed strategy | A factual correction is needed; later decisions belong in the active plan |
| [Design review](design-review.md) | Dated architecture/implementation review, its work order and current next steps | A factual correction is needed; record progress in the owning documents |
| [CLAUDE.md](../CLAUDE.md) | Short orientation, cloud setup and checks for a new agent session | Setup, layout or checks change |
| [Contributing](../CONTRIBUTING.md) | Contribution and documentation maintenance workflow | Review conventions change |
| [Third-party sources](../THIRD_PARTY.md) | Dependency, fixture and reused-code attribution | Dependencies, fixtures or external code change |
| This document | Ownership, organization and consolidation checklist | Documentation structure or maintenance rules change |

The author changing a component updates its owning documents in the same
change. The maintainer recording a device result updates status and checks any
brief summaries.

## Content rules

- Record full evidence once in status. Other pages may retain a short summary
  and link; keep operating warnings next to the affected command.
- Keep the current capture backlog in live capture. Research snapshots explain
  earlier reasoning and point to the active plan for current tasks.
- Describe a procedure's execution location, prerequisites, commands, expected
  results, output behavior and shutdown. Mark planned tools as unimplemented;
  do not provide a command that does not exist.
- Distinguish planned, implemented, cloud-tested and device-confirmed behavior.
  Label failures, upstream reports and unverified compatibility explicitly.
- Record source pins, review dates and evidence limits. A documentation edit
  date is not a device-test date. Do not infer missing versions or dates from
  filenames or the current Git commit.
- Keep complete operating procedures in the device/overlay guides and build
  procedures in development. A short README quick start may repeat a canonical
  command block, with a link and corresponding updates when behavior changes.
- Keep exact fields, types, limits and timebases consistent with implementation
  sources. Document a future capture interface alongside its implementation,
  rather than creating empty reference pages now.
- Preserve existing paths and heading anchors. If content moves, leave a short
  linked summary under the original heading and check inbound links.

## Consolidation checklist

- [x] Add a task-based documentation index and link it from the root README.
- [x] Add the status matrix and evidence record with explicit unknown fields.
- [x] Move detailed device-result history out of operating/reference guides.
- [x] Keep device commands, output guidance and the probe-interruption warning.
- [x] Separate Frame replay instructions from cloud development in the README.
- [x] Make live capture the authoritative current work checklist.
- [x] Mark the capture review as a dated snapshot and link to the active plan.
- [x] Link architecture and overlay guides to status instead of repeating evidence.
- [x] Add documentation maintenance rules to Contributing.
- [x] Verify local links, heading anchors and command examples against sources.
- [x] Verify formatting and that this change modifies documentation only.
- [x] Commit and publish the organization update after checks pass.

## Verification and continuing maintenance

Before publication, check local file targets and Markdown anchors, retain all
existing linked headings, inspect command paths/options against their sources,
and run `git diff --check`. Do not execute disruptive hardware procedures merely
to check documentation. Runtime tests remain governed by their actual operating
conditions and the [recorded probe failure](status.md#e3-public-camera-probe).

For a session handoff, record new evidence in status, update completed/next work
in live capture, and keep the short on-device continuation prompt aligned with
those documents. Publish related documentation with the implementation or
device-result update so the next session reads a consistent project state.
