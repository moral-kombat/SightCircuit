"""Fixed edge HUD: one contract for layout, native mask and mapping qualification.

* ``contract``: versioned ``HudConfig``, ``CaptureFormat``, ``Footprint`` and
  ``Calibration`` records plus the configuration digest.
* ``geometry``: the single eye-partition and rectangle rules.
* ``mask``: ``HudMask``, native-resolution exclusion before any resize and
  selection of names/directions. It accepts synthetic fixtures only.
* ``render``: the fixed tile, drawn from ``HudRow``s without any scene image.
* ``stage``: ``HudStage``, the engine stage that masks before the detector and
  renders after it.
* ``qualification``: offline mapping records (always unqualified) and
  telemetry checks. ``MappingCandidate.calibration()`` yields an unvalidated
  calibration that ``HudMask`` rejects; no code here lifts a hardware gate.
"""
from .contract import (HUD_CONFIG, Calibration, CaptureFormat, Footprint, HudConfig, HudOutput, HudRow,
                       config_digest, viewports)
from .mask import HudMask
from .qualification import MappingCandidate, ValidationLedger
from .stage import HudStage

__all__ = ("HUD_CONFIG", "Calibration", "CaptureFormat", "Footprint", "HudConfig", "HudMask", "HudOutput", "HudRow", "HudStage",
           "MappingCandidate", "ValidationLedger", "config_digest", "viewports")
