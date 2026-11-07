"""The fixed HUD as one engine stage: native mask before resize, tile after inference.

``Pipeline(hud=HudStage(...))`` masks the full calibrated footprint at native
resolution before the detector sees a frame, and renders only names and
approximate view directions afterwards. ``HudMask`` still accepts synthetic
fixture calibrations only; this stage approves nothing. ``expires_ns`` caps
every HUD result's deadline at the calibration's own expiry.

Each frame's exclusion checks travel with its output as ``HudOutput.checks``:
the footprint pixels left non-zero after zeroing, before the neutral fill
(returned by ``prepare``, so the engine can stop before the detector), the boxes
dropped for touching the mask or crossing an eye, and the masked frame's sequence.

``HudPipelineAdapter`` is the stage bound to its run's sink, as the synthetic
live command (``scripts/live_pipeline.py``) uses it: a sink with an output
contract must carry the fixed HUD configuration.
"""
from dataclasses import replace

from ..convert import native_bgr
from ..frame import native_format
from .contract import HUD_CONFIG, CaptureFormat, HudOutput
from .mask import HudMask
from .render import draw_tile


class HudStage:
    def __init__(self, source, calibration, config=HUD_CONFIG):
        self.source, self.calibration, self.config = source, calibration, config
        self.mask = None
        self._prepared = None  # (sequence, unmasked footprint pixels) of the frame awaiting render.

    def start(self):
        """Validate the calibration (once) and return report fields describing the HUD."""
        if self.mask is None:
            self.mask = HudMask(self.calibration, self.config, source_kind=getattr(self.source, "kind", None))
        return dict(hud_mode=True, calibration_scope="synthetic_fixture_only",
                    hardware_mapping_verified=False, mask_stage="native_pixels_before_resize",
                    mask_regions_native=self.mask.regions, output_kind="fixed_names_and_view_arrows",
                    world_locked=False, hud_config_id=self.config.layout_id,
                    hud_pixel_size=[self.config.width_px, self.config.height_px],
                    calibration_provenance=self.mask.calibration.provenance,
                    calibration_expires_ns=self.mask.calibration.expires_ns)

    @property
    def expires_ns(self):
        """The started calibration's monotonic deadline; no HUD result is presented past it."""
        return self.mask.calibration.expires_ns if self.mask is not None else None

    def prepare(self, frame):
        """``(image, unmasked_px)``: read-only masked detector input and its footprint pixels left non-zero by zeroing.

        ``unmasked_px`` should be 0; the engine stops the run before the
        detector when it is not.
        """
        self._prepared = None
        image, unmasked = self.mask.prepare_counted(
            native_bgr(frame), source_id=frame.source, capture_format=CaptureFormat.from_mapping(native_format(frame)),
            capture_layout=getattr(self.source, "capture_layout", None), config=self.config)
        self._prepared = (frame.sequence, unmasked)
        return image, unmasked

    def render(self, result, shape, threshold):
        """The tile and its ``HudOutput`` for the frame ``prepare`` masked last; each prepared frame renders once."""
        if self._prepared is None:
            raise ValueError("hud_render_without_prepared_frame")
        (sequence, unmasked), self._prepared = self._prepared, None
        rows, checks = self.mask.select(result.detections, shape, threshold)
        checks = replace(checks, unmasked_footprint_px=unmasked, frame_sequence=sequence)
        return draw_tile(rows, self.config), HudOutput(rows, self.config, checks)


class HudPipelineAdapter(HudStage):
    """The HUD stage plus its sink's fixed output contract.

    A sink that declares an output contract (``check_contract(config)``, as
    ``overlay.OverlaySink`` does) must carry the fixed HUD configuration
    (``sink.hud``): the stage refuses to start otherwise, and checks the
    contract when it starts and, through ``check_sink``, at every admission
    checkpoint. Capture identity remains the engine's ``CaptureContext``.
    """

    def __init__(self, source, sink, calibration, config=HUD_CONFIG):
        super().__init__(source, calibration, config)
        self.sink = sink

    def start(self):
        report = super().start()
        check = getattr(self.sink, "check_contract", None)
        if check is not None:
            if getattr(self.sink, "hud", None) is None:
                raise ValueError("hud_requires_fixed_validation_sink")
            check(self.config)
        return report

    def check_sink(self):
        """The sink's output contract, when it declares one and carries the HUD configuration."""
        check = getattr(self.sink, "check_contract", None)
        if check is not None and getattr(self.sink, "hud", None) is not None:
            check(self.config)
