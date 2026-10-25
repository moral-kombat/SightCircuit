"""Checksum-verified downloads of models and fixtures, never bundled in Git."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / ".cache/framejacker/assets"
MANIFEST = ROOT / "scripts/smoke-assets.json"


def checksum(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def manifest_entries(names, manifest=MANIFEST):
    """Return {name: entry} for ``names``; every name must be in the manifest."""
    entries = {entry["name"]: entry for entry in json.loads(Path(manifest).read_text())["assets"]}
    missing = sorted(set(names) - set(entries))
    if missing:
        raise RuntimeError(f"Checksums for {', '.join(missing)} are missing from {Path(manifest).name}")
    return {name: entries[name] for name in names}


def verify(names, directory=ASSETS, manifest=MANIFEST, hint="Run bash scripts/setup_window.sh"):
    """Fail closed unless each named asset exists and matches its manifest checksum."""
    for name, entry in manifest_entries(names, manifest).items():
        path = Path(directory) / name
        if not path.is_file():
            raise RuntimeError(f"Asset missing: {name}. {hint}")
        if checksum(path) != entry["sha256"]:
            raise RuntimeError(f"Asset failed SHA-256 verification: {name}")
    return [Path(directory) / name for name in names]


def fetch(entries, directory, log=print):
    """Download entries ({name, url, sha256}) into ``directory``; verify cached copies."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for entry in entries:
        name, url, expected = entry["name"], entry["url"], entry["sha256"]
        if Path(name).name != name or name in (".", ".."):
            raise ValueError(f"Invalid fixture filename: {name}")
        destination = directory / name
        if destination.exists():
            if checksum(destination) != expected:
                raise RuntimeError(
                    f"Cached fixture failed SHA-256 verification: {destination}. "
                    "Inspect/remove that generated file, then rerun setup."
                )
            log(f"Verified cached fixture: {name}")
            continue
        temporary = None
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                with tempfile.NamedTemporaryFile(dir=directory, prefix=name + ".", delete=False) as output:
                    temporary = Path(output.name)
                    while chunk := response.read(1024 * 1024):
                        output.write(chunk)
            if checksum(temporary) != expected:
                raise RuntimeError(f"Downloaded fixture failed SHA-256 verification: {name}")
            os.replace(temporary, destination)
            log(f"Downloaded and verified fixture: {name}")
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
