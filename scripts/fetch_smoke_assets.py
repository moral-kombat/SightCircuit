"""Fetch checksum-verified development fixtures, without bundling them in Git."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if not __package__:  # Run as a file: import this checkout's packages without installation.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sightcircuit.assets import ASSETS as DEFAULT_ASSET_DIR, MANIFEST, fetch


def fetch_assets(asset_dir: Path) -> None:
    fetch(json.loads(MANIFEST.read_text())["assets"], asset_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset-dir", type=Path, default=DEFAULT_ASSET_DIR)
    args = parser.parse_args()
    fetch_assets(args.asset_dir)


if __name__ == "__main__":
    main()
