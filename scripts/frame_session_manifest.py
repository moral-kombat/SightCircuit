"""Add one still to a private detector-evaluation manifest (evaluation/local/frame.json).

The image path is stored relative to the manifest, as ``evaluate_detectors.py`` expects.
Labels are canonical COCO names; boxes are not recorded here.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile


def split(text):
    return [item.strip() for item in text.split(",") if item.strip()]


def add_image(manifest, image_id, image, present, absent):
    manifest = Path(manifest)
    data = json.loads(manifest.read_text()) if manifest.exists() else {"schema_version": 1, "images": []}
    if data.get("schema_version") != 1 or not isinstance(data.get("images"), list):
        raise ValueError("Unsupported manifest")
    if any(entry.get("id") == image_id for entry in data["images"]):
        raise ValueError(f"Image id already present: {image_id}")
    relative = os.path.relpath(Path(image).resolve(), manifest.parent.resolve())
    data["images"].append({"id": image_id, "path": Path(relative).as_posix(),
                           "present": present, "absent": absent})
    manifest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=manifest.parent, suffix=".tmp", delete=False) as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")
    os.replace(handle.name, manifest)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--id", required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--present", default="", help="Comma-separated labels that are in the scene")
    parser.add_argument("--absent", default="", help="Comma-separated labels that must not be reported")
    args = parser.parse_args(argv)
    if not args.image.is_file():
        sys.exit(f"Image not found: {args.image}")
    try:
        add_image(args.manifest, args.id, args.image, split(args.present), split(args.absent))
    except ValueError as error:
        sys.exit(str(error))


if __name__ == "__main__":
    main()
