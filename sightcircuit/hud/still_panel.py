"""Still panel for device step L-D2: one public seed image at the HUD position and size, on a timer.

The step's canary shows a picture of a class absent from the room where the
HUD tile will sit and re-submits it at 5 Hz, so that a camera recognition run
beside it can show the panel is not being recognised. The recorded-replay
panel (``replay_window --overlay``, E47) cannot do that: it needs a decodable
recording, runs a detector on it, starts at the default panel placement and
takes the HUD placement only from its window's spin boxes. This driver is the
smallest thing that can: it letterboxes one image of the committed seed
manifest (``evaluation/seed.json``, public, checksum-pinned) into the
contract's 320x128 tile and feeds it to the bridge's existing stream mode
(frames without a deadline, opcode 1) with the HUD placement as its settings.

Nothing here is camera-derived and nothing here can be: the only input is an
image id of the seed manifest, verified by its SHA-256 before it is decoded.
There is no path, source or device argument. It never sends an expiring frame
and never uses ``--fixed-hud``; real output is the replay panel's own path
(``--stdin``) and needs ``allow_device_output``, which only an approved device
step passes. The report states software facts; it is not headset evidence.

    python -m sightcircuit.hud.still_panel --validate --seconds 120
"""
import argparse
import json
from pathlib import Path
import sys
import threading
import time

if __name__ == "__main__":
    # Run as a file: import the checkout's packages, never siblings of this file.
    sys.path[0] = str(Path(__file__).resolve().parents[2])
from sightcircuit import assets, evaluation  # noqa: E402
from sightcircuit.hud.contract import HUD_CONFIG, RGBA_BYTES, config_digest  # noqa: E402
from sightcircuit.overlay import OverlayClient, PanelSettings  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SEED_MANIFEST = ROOT / "evaluation/seed.json"
SEED_DOWNLOADS = ROOT / ".cache/sightcircuit/evaluation"  # where evaluate_detectors.py --fetch puts the seed images
DEFAULT_IMAGE_ID = "darknet-horses"
UPDATE_HZ = 5
MIN_SECONDS, MAX_SECONDS = 1, 180
REPORT_KIND = "sightcircuit_still_panel"


def hud_placement(visible=True):
    """The fixed HUD placement as the stream mode's settings."""
    c = HUD_CONFIG
    return PanelSettings(width=c.panel_width_m, distance=c.distance_m, x=c.x_m, y=c.y_m, opacity=c.opacity,
                         visible=visible)


def seed_tile(image_id=DEFAULT_IMAGE_ID, *, directory=SEED_DOWNLOADS):
    """``(rgba, source)``: seed image ``image_id`` letterboxed into the HUD tile, and the record of what it is.

    The image must be an entry of the committed seed manifest and the file in
    ``directory`` must match the manifest's SHA-256; anything else raises
    before a byte is decoded. The whole picture is kept (scaled to fit, centred
    on black), so what the panel shows is the fixture, not a crop of it.
    """
    import cv2
    import numpy as np

    manifest = evaluation.load_manifest(SEED_MANIFEST)
    entries = {entry["id"]: entry for entry in manifest["images"]}
    entry = entries.get(image_id)
    if entry is None or "file" not in entry or not entry.get("sha256"):
        raise ValueError("still_panel_image_not_in_seed_manifest")
    path = Path(directory) / entry["file"]
    if not path.is_file():
        raise FileNotFoundError(f"still_panel_seed_image_missing: {entry['file']} "
                                "(run scripts/evaluate_detectors.py --fetch)")
    if assets.checksum(path) != entry["sha256"]:
        raise ValueError("still_panel_seed_image_digest_mismatch")
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("still_panel_seed_image_undecodable")
    width, height = HUD_CONFIG.width_px, HUD_CONFIG.height_px
    scale = min(width / image.shape[1], height / image.shape[0])
    fitted = cv2.resize(image, (max(1, round(image.shape[1] * scale)), max(1, round(image.shape[0] * scale))),
                        interpolation=cv2.INTER_AREA)
    tile = np.zeros((height, width, 3), dtype=np.uint8)
    top, left = (height - fitted.shape[0]) // 2, (width - fitted.shape[1]) // 2
    tile[top:top + fitted.shape[0], left:left + fitted.shape[1]] = fitted
    rgba = cv2.cvtColor(tile, cv2.COLOR_BGR2RGBA).tobytes()
    source = {"kind": "public_seed_fixture", "manifest": "evaluation/seed.json", "id": image_id,
              "sha256": entry["sha256"], "present_labels": list(entry.get("present", ())),
              "fit": "letterbox", "picture_px": [fitted.shape[1], fitted.shape[0]]}
    return rgba, source


def run_still_panel(rgba, source, *, seconds, validation_only=True, allow_device_output=False,
                    client_factory=OverlayClient, stop=None, clock=time.monotonic):
    """Show the tile at the HUD placement and re-submit it ``UPDATE_HZ`` times a second for ``seconds``; the report.

    ``rgba`` and ``source`` come from ``seed_tile``. Real output
    (``validation_only`` false) needs ``allow_device_output``. Every update is
    one frame packet without a deadline; an update whose predecessor the
    writer had not yet taken is counted in ``updates_replaced`` and the run is
    then not ``complete``. ``stop`` (an event) ends the run early; the bridge
    is always closed, which retires the panel.
    """
    if type(seconds) is not int or not MIN_SECONDS <= seconds <= MAX_SECONDS:
        raise ValueError("still_panel_seconds_out_of_range")
    if source.get("kind") != "public_seed_fixture":
        raise ValueError("still_panel_source_not_a_seed_fixture")
    if len(rgba) != HUD_CONFIG.width_px * HUD_CONFIG.height_px * RGBA_BYTES:
        raise ValueError("still_panel_tile_size")
    if not validation_only and not allow_device_output:
        raise ValueError("still_panel_device_output_not_allowed")
    stop = stop or threading.Event()
    updates = seconds * UPDATE_HZ
    placement = hud_placement()
    report = {"report_kind": REPORT_KIND, "schema_version": 1, "status": "error", "source": source,
              "camera_derived": False, "live_source_accepted": False, "images_saved": False,
              "validation_only": validation_only, "bridge_mode": "--validate-stdin" if validation_only else "--stdin",
              "frames_carry_deadline": False,
              "geometry": {"layout_id": HUD_CONFIG.layout_id, "config_sha256": config_digest(HUD_CONFIG),
                           "width_px": HUD_CONFIG.width_px, "height_px": HUD_CONFIG.height_px,
                           "panel_width_m": placement.width, "distance_m": placement.distance, "x_m": placement.x,
                           "y_m": placement.y, "opacity": placement.opacity},
              "update_hz": UPDATE_HZ, "seconds_requested": seconds, "updates_requested": updates,
              "updates_submitted": 0, "updates_replaced": 0, "updates_acknowledged": None, "complete": False,
              "scope": "software counts from the bridge's report; not headset evidence",
              "physical_display_verified": False}
    client = client_factory(validation_only=validation_only)
    client.set_settings(placement)  # before start: the first settings packet is already the HUD placement
    try:
        client.start()
        started = clock()
        for index in range(updates):
            while (remaining := started + index / UPDATE_HZ - clock()) > 0:
                if stop.wait(remaining):
                    break
            if stop.is_set():
                break
            if client.error:
                raise RuntimeError(client.error)
            with client.condition:
                report["updates_replaced"] += client.pending_frame is not None
            client.submit_frame(HUD_CONFIG.width_px, HUD_CONFIG.height_px, rgba)
            report["updates_submitted"] += 1
        # The last update's period: it stays up as long as every other, and the writer has time to send it.
        while not stop.is_set() and (remaining := started + updates / UPDATE_HZ - clock()) > 0:
            stop.wait(remaining)
        report["status"] = "stopped" if stop.is_set() else "completed"
    except (OSError, RuntimeError, ValueError) as error:
        report["error"] = str(error)
    finally:
        bridge = report["bridge"] = client.close()
    native = bridge.get("native_report") or {}
    report["updates_acknowledged"] = native.get("frames_received")
    if bridge.get("error") and report["status"] != "error":
        report.update(status="error", error=str(bridge["error"]))
    report["cleanup_confirmed"] = bridge.get("cleanup_confirmed") is True
    report["complete"] = (report["status"] == "completed" and report["cleanup_confirmed"] and
                          report["updates_replaced"] == 0 and
                          report["updates_submitted"] == updates == report["updates_acknowledged"])
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--image-id", default=DEFAULT_IMAGE_ID, help="An image id of evaluation/seed.json")
    parser.add_argument("--seconds", type=int, required=True, help=f"{MIN_SECONDS} to {MAX_SECONDS}")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--validate", action="store_true", help="Validation bridge: no OpenVR call, nothing displayed")
    mode.add_argument("--allow-device-output", action="store_true",
                      help="Real panel through the replay stream; only inside an approved device step")
    args = parser.parse_args(argv)
    from sightcircuit.runtime.guard import stop_on_signals

    try:
        rgba, source = seed_tile(args.image_id)
        report = run_still_panel(rgba, source, seconds=args.seconds, validation_only=args.validate,
                                 allow_device_output=args.allow_device_output, stop=stop_on_signals())
    except (OSError, RuntimeError, ValueError) as error:
        report = {"report_kind": REPORT_KIND, "schema_version": 1, "status": "error", "error": str(error),
                  "camera_derived": False, "complete": False}
    print(json.dumps(report, sort_keys=True))
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
