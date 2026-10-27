"""Record headset-side observations for one Frame run.

Serves a plain HTML form on the local network, for example from the headset browser
or a phone on the same Wi-Fi. Each submission is appended to ``observations.json`` in
the run directory with its submission time, so answers stay tied to the run they
describe. A random token in the URL keeps other LAN clients out, and the server stops
after ``--minutes``. Traffic is plain HTTP; use it only during a test session.
"""

import argparse
from datetime import datetime, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import socket
import tempfile
import time
from urllib.parse import parse_qs

MAX_BODY = 8192
MAX_TEXT = 500
MAX_FIELDS = 32
TEXT_FIELDS = (
    ("target", "What was aimed at? (optional)"),
    ("notes", "Anything else? (optional)"),
)
CHOICE_FIELDS = (
    ("visible_in_headset", "Could you see the window or panel in the headset during capture?",
     ("yes", "no", "unsure", "not_applicable")),
    ("labels_matched", "Did the labels and boxes match what was in view?",
     ("yes", "partly", "no", "nothing_in_view")),
    ("passthrough_normal", "Were passthrough and tracking normal during and after?",
     ("yes", "no", "unsure")),
)


def _load(path, run_id):
    if not path.exists():
        return {"schema_version": 1, "run_id": run_id, "submissions": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or not isinstance(data.get("submissions"), list):
        raise ValueError(f"Unsupported observations file: {path}")
    return data


def append_submission(run_dir, run_id, answers):
    path = run_dir / "observations.json"
    data = _load(path, run_id)
    data["submissions"].append({
        "submitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "answers": answers,
    })
    with tempfile.NamedTemporaryFile("w", dir=run_dir, suffix=".tmp", delete=False, encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")
    os.replace(handle.name, path)


def count_submissions(run_dir):
    return len(_load(run_dir / "observations.json", "").get("submissions", []))


def _single(fields, name):
    values = fields.get(name, [""])
    if len(values) != 1:
        raise ValueError(f"Repeated field: {name}")
    return values[0]


def _clean_text(value):
    return "".join(ch for ch in value if ch.isprintable() or ch in "\n\t").strip()


def validate(fields):
    allowed = {name for name, _ in TEXT_FIELDS} | {name for name, _, _ in CHOICE_FIELDS}
    unknown = sorted(set(fields) - allowed)
    if unknown:
        return None, f"Unknown field: {unknown[0]}"
    answers = {}
    try:
        for name, _ in TEXT_FIELDS:
            value = _clean_text(_single(fields, name))
            if len(value) > MAX_TEXT:
                raise ValueError(f"Too long: {name}")
            answers[name] = value
        for name, _, choices in CHOICE_FIELDS:
            value = _single(fields, name)
            if value not in choices:
                raise ValueError(f"Choose an answer for: {name}")
            answers[name] = value
    except ValueError as error:
        return None, str(error)
    return answers, None


def render_page(run_id, token, saved):
    parts = [
        "<!doctype html><meta charset=utf-8>",
        "<meta name=viewport content='width=device-width,initial-scale=1'>",
        "<title>Framejacker observations</title>",
        "<style>body{font:20px system-ui;margin:16px;max-width:40em}"
        "fieldset{margin:1em 0;padding:.5em 1em}label{display:block;padding:.4em 0}"
        "input[type=text],textarea{width:100%;font:inherit;box-sizing:border-box}"
        "button{font:inherit;padding:.6em 1.2em}</style>",
        f"<h1>Run {escape(run_id)}</h1>",
        f"<p>{saved} saved submission(s) for this run.</p>",
        f"<form method=post action='/{token}/submit'>",
        "<p><label>What was aimed at?<input type=text name=target maxlength=500></label></p>",
    ]
    for name, question, choices in CHOICE_FIELDS:
        parts.append(f"<fieldset><legend>{escape(question)}</legend>")
        for choice in choices:
            parts.append(f"<label><input type=radio name={name} value={choice} required> "
                         f"{escape(choice.replace('_', ' '))}</label>")
        parts.append("</fieldset>")
    parts.append("<p><label>Anything else? (optional)<textarea name=notes rows=3 maxlength=500>"
                 "</textarea></label></p>")
    parts.append("<p><button type=submit>Save answers</button></p></form>")
    return "".join(parts)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.rstrip("/") != f"/{self.server.token}":
            return self._send(404, "<p>Not found.</p>")
        saved = count_submissions(self.server.run_dir)
        return self._send(200, render_page(self.server.run_id, self.server.token, saved))

    def do_POST(self):
        if self.path != f"/{self.server.token}/submit":
            return self._send(404, "<p>Not found.</p>")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0:
            return self._send(400, "<p>Invalid request length.</p>")
        if length > MAX_BODY:
            self.close_connection = True
            return self._send(413, "<p>Submission too large.</p>")
        try:
            fields = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True,
                              max_num_fields=MAX_FIELDS)
        except ValueError:
            return self._send(400, "<p>Malformed submission.</p>")
        answers, error = validate(fields)
        if error:
            return self._send(400, f"<p>{escape(error)}</p><p><a href='/{self.server.token}/'>Back</a></p>")
        append_submission(self.server.run_dir, self.server.run_id, answers)
        self.send_response(303)
        self.send_header("Location", f"/{self.server.token}/")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send(self, status, body, content_type="text/html; charset=utf-8"):
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        return


class ObservationServer(HTTPServer):
    def __init__(self, address, run_dir, run_id, token):
        super().__init__(address, Handler)
        self.run_dir = run_dir
        self.run_id = run_id
        self.token = token


def make_server(address, run_dir, run_id, token):
    run_dir.mkdir(parents=True, exist_ok=True)
    return ObservationServer(address, run_dir, run_id, token)


def lan_address():
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-dir", type=Path, required=True, help="Directory that receives observations.json")
    parser.add_argument("--run-id", help="Label shown on the page (default: run directory name)")
    parser.add_argument("--host", default="0.0.0.0", help="Interface to bind; 127.0.0.1 keeps it local")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--minutes", type=float, default=20.0, help="Stop after this long (0 < minutes <= 120)")
    args = parser.parse_args(argv)
    if not 0 < args.minutes <= 120:
        parser.error("--minutes must be greater than 0 and at most 120")
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")

    run_id = args.run_id or args.run_dir.name
    token = secrets.token_urlsafe(9)
    server = make_server((args.host, args.port), args.run_dir, run_id, token)
    server.timeout = 1
    host = lan_address() if args.host == "0.0.0.0" else args.host
    print(json.dumps({
        "status": "listening",
        "run_id": run_id,
        "url": f"http://{host}:{server.server_address[1]}/{token}/",
        "expires_in_seconds": int(args.minutes * 60),
        "observations": str(args.run_dir / "observations.json"),
    }, indent=2), flush=True)

    deadline = time.monotonic() + args.minutes * 60
    try:
        while time.monotonic() < deadline:
            server.handle_request()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    print(json.dumps({"status": "closed", "submissions": count_submissions(args.run_dir)}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
