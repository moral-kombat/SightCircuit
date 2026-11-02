"""One policy for tests that need downloaded models, fixtures or seed images.

Absent locally: the test skips, naming each missing file and how to fetch it.
The gate checks presence only; detectors and the evaluator verify model and
seed-image checksums themselves, so an altered file fails instead of skipping.
CI downloads the assets first and runs these modules through
``scripts/run_tests_no_skips.py``, so an absent asset fails CI instead of
skipping there.
"""
import json
from pathlib import Path
import unittest

from framejacker import assets, detection

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = "dog.jpg"
SEED_MANIFEST = ROOT / "evaluation/seed.json"
SEED_IMAGES = ROOT / ".cache/framejacker/evaluation"  # evaluate_detectors.py --fetch downloads here.


def model_assets(*detectors):
    """The sample fixture plus the model files of each named (default: every registered) detector."""
    names = detectors or tuple(detection.DETECTORS)
    return (FIXTURE, *dict.fromkeys(item for name in names for item in detection.model_files(name)))


def _skip_unless_present(paths, hint):
    absent = [path.name for path in paths if not path.is_file()]
    return unittest.skipIf(bool(absent), f"Downloaded asset absent: {', '.join(absent)}; {hint}")


def requires_assets(*names):
    """Skip unless each named checksum-pinned download is in the asset cache."""
    return _skip_unless_present([assets.ASSETS / name for name in names], "run bash scripts/setup_window.sh")


def requires_seed_images():
    """Skip unless every downloadable seed manifest image is in the evaluation cache."""
    images = json.loads(SEED_MANIFEST.read_text())["images"]
    return _skip_unless_present([SEED_IMAGES / entry["file"] for entry in images if "file" in entry],
                                "run scripts/evaluate_detectors.py --fetch")
