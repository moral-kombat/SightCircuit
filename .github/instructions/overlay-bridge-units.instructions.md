---
description: The split overlay bridge units follow the Frame service lifecycle constraints
applyTo: 'tools/overlay_bridge.hpp,tools/overlay_bridge_lifecycle.cpp,tools/overlay_bridge_rendering.cpp,tools/overlay_bridge_reporting.cpp,tools/overlay_bridge_sensing.cpp'
---

These files are the units of the native overlay bridge, split from
`tools/overlay_bridge.cpp` on 2026-11-08 (work item E1). The constraints in
[frame-service-lifecycle.instructions.md](frame-service-lifecycle.instructions.md)
apply to them exactly as to the entry point: no SteamVR restart, no layer
activation and no service change from a cloud session; the bridge ends itself
only on its watchdog or parent death and supervises nothing else; the contract
values come from the generated `tools/hud_contract.hpp` and are never typed
by hand. The source layout is documented in
[docs/reference/overlay.md](../../docs/reference/overlay.md).
