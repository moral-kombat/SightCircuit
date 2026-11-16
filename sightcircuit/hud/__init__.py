"""Fixed edge HUD: one contract for layout, native mask and mapping qualification.

* ``contract``: versioned ``HudConfig``, ``CaptureFormat``, ``Footprint`` and
  ``Calibration`` records plus the configuration digest, the one margin rule
  (``margin_valid``), and the values shared with the native bridge: the
  calibration ``MARKER``, packet layouts and limits.
* ``native``: generates ``tools/hud_contract.hpp``, the bridge's copy of those
  values (``scripts/hud_contract_header.py``).
* ``geometry``: the single eye-partition and rectangle rules.
* ``mask``: ``HudMask``, native-resolution exclusion before any resize (zero,
  count, then ``fill_regions``' neutral fill from the surround) and selection
  of names/directions. It accepts unexpired synthetic fixtures only and checks
  their session tokens; ``mask_regions`` is its geometry rule alone.
* ``render``: the fixed tile, drawn from ``HudRow``s without any scene image.
* ``camera``: ``camera_rows``, the mask-free selector for camera detections: a
  3x3 direction cell over the full frame, or names only. It shares the row
  hygiene in ``contract`` (``row_label``, ``select_rows``) with ``HudMask`` and
  imports no mask, mapping or qualification code.
* ``stage``: ``HudStage``, the engine stage that masks before the detector and
  renders after it, with each frame's exclusion checks (``HudChecks``), and
  ``HudPipelineAdapter``, the stage bound to its sink's fixed output contract.
* ``validation``: ``ValidationSink``, which feeds a synthetic HUD run's results
  to the ``ValidationLedger``, and the run's G5 software counts.
* ``qualification``: offline mapping records (always unqualified) and
  telemetry checks. ``MappingCandidate.calibration()`` yields an unvalidated
  calibration that ``HudMask`` rejects; no code here lifts a hardware gate.
  It and ``runtime.synthetic.fixture_calibration`` are the only two
  ``Calibration`` factories.
"""
from .camera import camera_rows
from .contract import (HUD_CONFIG, Calibration, CaptureFormat, Footprint, HudConfig, HudOutput, HudRow,
                       config_digest, viewports)
from .mask import HudMask
from .qualification import MappingCandidate, ValidationLedger
from .stage import HudPipelineAdapter, HudStage

__all__ = ("HUD_CONFIG", "Calibration", "CaptureFormat", "Footprint", "HudConfig", "HudMask", "HudOutput", "HudPipelineAdapter", "HudRow", "HudStage",
           "MappingCandidate", "ValidationLedger", "camera_rows", "config_digest", "viewports")
