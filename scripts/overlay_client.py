"""Compatibility imports for the shared bounded overlay transport.

New code uses :mod:`framejacker.overlay`; script imports retain the same types.
"""
from pathlib import Path
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from framejacker.overlay import (  # noqa: E402,F401 - compatibility exports
    DEFAULT_BRIDGE, MAX_SIDE, PANEL_LIMITS, ROOT, OverlayClient, OverlaySink, PanelSettings,
    packet, rgba_from_bgr,
)
