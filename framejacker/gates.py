"""Closed approval gates: the single statement of what this checkout must refuse.

Every gate here is closed, and nothing in this module can open one. Each
boundary keeps its own check (defence in depth), but performs it through its
gate's ``require``, and reports state the gate's status through ``claims``.
Lifting a gate is a reviewed change to this table and its call sites, made
only after the acceptance rows it names pass on the headset
(``docs/live-capture.md#edge-hud-acceptance-gates``); it is never a new
condition at a single call site.

Outside these gates: recorded-replay output on the VR panel (E47) and bounded
SteamVR mirror recognition without display (E48) are confirmed device uses.
"""
from dataclasses import dataclass

ACCEPTANCE = "docs/live-capture.md#edge-hud-acceptance-gates"


@dataclass(frozen=True)
class Gate:
    name: str
    claim: str  # Report field stating, always False here, that the gated use is enabled.
    closes: str
    lifted_by: tuple  # Acceptance rows (G-numbers) in ACCEPTANCE that must pass first.

    def require(self, permitted, reason):
        """Fail closed: raise ``ValueError(reason)`` unless this use is permitted while closed.

        Only the literal ``True`` permits; any other value, including truthy
        objects, refuses.
        """
        if permitted is not True:
            raise ValueError(reason)


LIVE_IMAGE_INPUT = Gate(
    "live_image_input", "production_live_input_enabled",
    "Non-synthetic images feeding the live pipeline or the HUD path", ("G4", "G5", "G6"))
HARDWARE_MAPPING = Gate(
    "hardware_mapping", "mapping_qualified",
    "Any HUD mask, calibration or mapping that is not a synthetic fixture", ("G4",))
LIVE_HUD_OUTPUT = Gate(
    "live_hud_output", "production_hud_enabled",
    "Fixed HUD placement or expiring frames on the real VR runtime (not validation-only bridges)", ("G5", "G6"))

GATES = (LIVE_IMAGE_INPUT, HARDWARE_MAPPING, LIVE_HUD_OUTPUT)


def claims(*gates):
    """Report fields stating that each gate (default: all) remains closed."""
    return {gate.claim: False for gate in (gates or GATES)}
