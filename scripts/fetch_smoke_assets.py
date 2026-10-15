"""Fetch checksum-verified development fixtures, without bundling them in Git."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ASSET_DIR = ROOT / ".cache" / "framejacker" / "assets"


def checksum(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def fetch_assets(asset_dir: Path) -> None:
    manifest = json.loads((ROOT / "scripts" / "smoke-assets.json").read_text())
    asset_dir.mkdir(parents=True, exist_ok=True)
    for asset in manifest["assets"]:
        name, url, expected = asset["name"], asset["url"], asset["sha256"]
        if Path(name).name != name or name in (".", ".."):
            raise ValueError(f"Invalid fixture filename: {name}")
        destination = asset_dir / name
        if destination.exists():
            if checksum(destination) != expected:
                raise RuntimeError(
                    f"Cached fixture failed SHA-256 verification: {destination}. "
                    "Inspect/remove that generated file, then rerun setup."
                )
            print(f"Verified cached fixture: {name}")
            continue

        temporary: Path | None = None
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                with tempfile.NamedTemporaryFile(dir=asset_dir, prefix=name + ".", delete=False) as output:
                    temporary = Path(output.name)
                    while chunk := response.read(1024 * 1024):
                        output.write(chunk)
            if checksum(temporary) != expected:
                raise RuntimeError(f"Downloaded fixture failed SHA-256 verification: {name}")
            os.replace(temporary, destination)
            print(f"Downloaded and verified fixture: {name}")
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset-dir", type=Path, default=DEFAULT_ASSET_DIR)
    args = parser.parse_args()
    fetch_assets(args.asset_dir)


if __name__ == "__main__":
    main()
