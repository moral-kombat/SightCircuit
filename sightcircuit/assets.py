"""Checksum-verified downloads of models and fixtures, never bundled in Git.

A download is retried on a transient network error (a timeout, a reset or
otherwise broken connection, an incomplete read, an HTTP 429 or 5xx answer)
with a fixed, bounded backoff: ``ATTEMPTS`` tries with ``BACKOFF_SECONDS``
between them, so one asset waits at most ``sum(BACKOFF_SECONDS)`` on top of
``ATTEMPTS`` connection timeouts (work-order item P3; one release download
timing out on the CI runner had cost re-runs). Each retry is one line on the
error stream naming the asset, its URL (the manifests carry no secrets) and
the error class. The digest check is unchanged and is never retried: a
downloaded file whose SHA-256 differs from the manifest fails closed at once,
and a partial or rejected file is never left in the asset directory.
"""
import hashlib
import http.client
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / ".cache/sightcircuit/assets"
MANIFEST = ROOT / "scripts/smoke-assets.json"

TIMEOUT_SECONDS = 60  # One connection or read may block this long (``urlopen(timeout=...)``), as before P3.
ATTEMPTS = 4  # Tries per asset; the last failure is raised.
BACKOFF_SECONDS = (2.0, 4.0, 8.0)  # Waited before the second, third and fourth try: doubling, 14 s in all.
TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})  # Rate limiting and server-side failures; a 4xx otherwise is final.
CHUNK = 1024 * 1024
assert len(BACKOFF_SECONDS) == ATTEMPTS - 1


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


def is_transient(error):
    """True for the network failures a retry can cure; a wrong answer (4xx other than 429) or a local fault is final."""
    if isinstance(error, urllib.error.HTTPError):
        return error.code in TRANSIENT_STATUS
    if isinstance(error, urllib.error.URLError):
        return error.reason is None or isinstance(error.reason, (OSError, http.client.HTTPException))
    return isinstance(error, (socket.timeout, TimeoutError, ConnectionError, http.client.IncompleteRead,
                              http.client.RemoteDisconnected))


def describe(error):
    """The error's class, and an HTTP status when it has one: what the retry line and the give-up name."""
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTPError {error.code}"
    if isinstance(error, urllib.error.URLError) and error.reason is not None:
        return f"URLError({type(error.reason).__name__})"
    return type(error).__name__


def download(opener, url, directory, name):
    """One attempt: stream ``url`` into a new temporary file in ``directory`` and return its path; nothing is left on failure."""
    temporary = None
    try:
        with opener(url, timeout=TIMEOUT_SECONDS) as response:
            with tempfile.NamedTemporaryFile(dir=directory, prefix=name + ".", delete=False) as output:
                temporary = Path(output.name)
                while chunk := response.read(CHUNK):
                    output.write(chunk)
    except BaseException:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise
    return temporary


def fetch(entries, directory, log=print, opener=None, sleep=time.sleep, warn=None):
    """Download entries ({name, url, sha256}) into ``directory``; verify cached copies.

    ``opener`` (default ``urllib.request.urlopen``), ``sleep`` and ``warn`` (a
    line on the error stream by default) are the seams the tests use.
    """
    opener = urllib.request.urlopen if opener is None else opener
    if warn is None:
        def warn(message):
            print(message, file=sys.stderr, flush=True)
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
        for attempt in range(1, ATTEMPTS + 1):
            try:
                temporary = download(opener, url, directory, name)
            except Exception as error:
                if not is_transient(error):
                    raise
                if attempt == ATTEMPTS:
                    raise RuntimeError(f"Download of fixture {name} failed after {ATTEMPTS} attempts "
                                       f"({url}): {describe(error)}") from error
                delay = BACKOFF_SECONDS[attempt - 1]
                warn(f"Retrying fixture {name} ({url}): attempt {attempt} of {ATTEMPTS} failed with "
                     f"{describe(error)}; next try in {delay:g} s")
                sleep(delay)
                continue
            try:
                # Never retried: a complete download with the wrong digest is a wrong file, not a bad connection.
                if checksum(temporary) != expected:
                    raise RuntimeError(f"Downloaded fixture failed SHA-256 verification: {name}")
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
            log(f"Downloaded and verified fixture: {name}")
            break
