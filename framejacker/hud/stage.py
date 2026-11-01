"""The fixed HUD as one engine stage: native mask before resize, tile after inference.

``Pipeline(hud=HudStage(...))`` masks the full calibrated footprint at native
resolution before the detector sees a frame, and renders only names and
approximate view directions afterwards. ``HudMask`` still accepts synthetic
fixture calibrations only; this stage approves nothing.
"""
from ..convert import native_bgr
from ..frame import frame_source_id, frame_view, native_format
from .contract import HUD_CONFIG, CaptureFormat, HudOutput
from .mask import HudMask


class HudStage:
    def __init__(self, source, calibration, config=HUD_CONFIG):
        self.source, self.calibration, self.config = source, calibration, config
        self.mask = None

    def start(self):
        """Validate the calibration (once) and return report fields describing the HUD."""
        if self.mask is None:
            self.mask = HudMask(self.calibration, self.config, source_kind=getattr(self.source, "kind", None))
        return dict(hud_mode=True, calibration_scope="synthetic_fixture_only",
                    hardware_mapping_verified=False, mask_stage="native_pixels_before_resize",
                    mask_regions_native=self.mask.regions, output_kind="fixed_names_and_view_arrows",
                    world_locked=False, hud_config_id=self.config.layout_id,
                    hud_pixel_size=[self.config.width_px, self.config.height_px])

    def prepare(self, frame):
        """Read-only masked detector input at the calibrated inference size."""
        return self.mask.prepare(native_bgr(frame_view(frame)), source_id=frame_source_id(frame),
                                 capture_format=CaptureFormat.from_mapping(native_format(frame)),
                                 capture_layout=getattr(self.source, "capture_layout", None), config=self.config)

    def render(self, result, shape, threshold):
        tile, rows = self.mask.render(result.detections, shape, threshold)
        return tile, HudOutput(rows, self.config)
