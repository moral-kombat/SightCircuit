"""Check repository Markdown links and the preserved documentation anchors offline.

This checker covers the Markdown used in this repository: ATX/setext headings,
inline and reference links/images, fenced code, and explicit HTML IDs. It never
opens an external URL or executes a documented command.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import html
import json
from pathlib import Path
import re
import subprocess
import unicodedata
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
LEGACY_PATH = "docs/legacy-anchors.json"
NEW_DOCUMENT_ROOTS = {"docs", ".github", ".agents", ".codex", "patches", "third_party"}


def blank(text: str) -> str:
    return "".join("\n" if character == "\n" else " " for character in text)


def prose(text: str) -> str:
    """Hide comments and fenced code without changing offsets or line numbers."""
    text = re.sub(r"<!--[\s\S]*?-->", lambda match: blank(match[0]), text)
    output = []
    fence = None
    for line in text.splitlines(keepends=True):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line.rstrip("\n"))
        if fence:
            output.append(blank(line))
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                fence = None
        elif marker:
            fence = marker[1]
            output.append(blank(line))
        else:
            output.append(line)
    return "".join(output)


def slug(heading: str) -> str:
    heading = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", heading)
    heading = re.sub(r"<[^>]+>", "", heading)
    heading = html.unescape(heading).lower()
    # GitHub removes punctuation/formatting, retains Unicode letters and marks,
    # and replaces individual spaces with hyphens (without collapsing them).
    return "".join(
        "-" if character == " " else character
        for character in heading
        if character in " _-" or unicodedata.category(character)[0] in {"L", "N", "M"}
    )


def anchors(text: str) -> set[str]:
    text = prose(text)
    tags = re.sub(r"(`+)(?!`)([\s\S]*?)(?<!`)\1(?!`)", lambda match: blank(match[0]), text)
    found = set(re.findall(r"<[A-Za-z][^>]*?\b(?:id|name)\s*=\s*[\"']([^\"']+)[\"'][^>]*>", tags))
    headings = []
    lines = text.splitlines()
    for number, line in enumerate(lines):
        match = re.match(r"^ {0,3}#{1,6}(?:[ \t]+(.*?)|[ \t]*)$", line)
        if match:
            headings.append(re.sub(r"[ \t]+#+[ \t]*$", "", match[1] or "").strip())
        elif number and re.fullmatch(r" {0,3}(?:=+|-+)[ \t]*", line) and lines[number - 1].strip():
            headings.append(lines[number - 1].strip())
    used: set[str] = set()
    for heading in headings:
        base = slug(heading)
        candidate = base
        suffix = 0
        while candidate in used:
            suffix += 1
            candidate = f"{base}-{suffix}"
        used.add(candidate)
        found.add(candidate)
    return found


def normalized_label(label: str) -> str:
    return " ".join(label.split()).casefold()


def unescape(value: str) -> str:
    return html.unescape(re.sub(r"\\([!\"#$%&'()*+,\-./:;<=>?@\[\]\\^_`{|}~])", r"\1", value))


def destination(text: str, start: int) -> str:
    """Read a Markdown destination, excluding its optional quoted title."""
    while start < len(text) and text[start].isspace():
        start += 1
    if start < len(text) and text[start] == "<":
        end = text.find(">", start + 1)
        return unescape(text[start + 1:end]) if end >= 0 else ""
    end = start
    depth = 0
    while end < len(text):
        character = text[end]
        if character == "\\" and end + 1 < len(text):
            end += 2
            continue
        if character.isspace() and depth == 0:
            break
        if character == ")":
            if depth == 0:
                break
            depth -= 1
        elif character == "(":
            depth += 1
        end += 1
    return unescape(text[start:end])


@dataclass(frozen=True)
class Link:
    line: int
    target: str
    missing_reference: bool = False


def links(text: str) -> list[Link]:
    text = prose(text)
    definitions: dict[str, str] = {}
    found: list[Link] = []
    definition_pattern = re.compile(r"^ {0,3}\[([^\]\n]+)\]:[ \t]*(.*)$", re.MULTILINE)
    for match in definition_pattern.finditer(text):
        target = destination(match[2], 0)
        definitions.setdefault(normalized_label(match[1]), target)
        found.append(Link(text.count("\n", 0, match.start()) + 1, target))
    text = definition_pattern.sub(lambda match: blank(match[0]), text)
    text = re.sub(r"(`+)(?!`)([\s\S]*?)(?<!`)\1(?!`)", lambda match: blank(match[0]), text)
    for match in re.finditer(r"<(?:a|img)\b[^>]*?\b(?:href|src)=[\"']([^\"']+)[\"'][^>]*>", text):
        found.append(Link(text.count("\n", 0, match.start()) + 1, html.unescape(match[1])))
    index = 0
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text[index] != "[":
            index += 1
            continue
        start = index
        end = index + 1
        depth = 1
        while end < len(text) and depth:
            if text[end] == "\\":
                end += 2
                continue
            if text[end] == "[":
                depth += 1
            elif text[end] == "]":
                depth -= 1
            end += 1
        if depth:
            index += 1
            continue
        label = text[start + 1:end - 1]
        line = text.count("\n", 0, start) + 1
        if end < len(text) and text[end] == "(":
            found.append(Link(line, destination(text, end + 1)))
        elif end < len(text) and text[end] == "[":
            reference_end = text.find("]", end + 1)
            if reference_end >= 0:
                reference = text[end + 1:reference_end] or label
                target = definitions.get(normalized_label(reference))
                found.append(Link(line, target if target is not None else reference, target is None))
                end = reference_end + 1
        elif normalized_label(label) in definitions:
            found.append(Link(line, definitions[normalized_label(label)]))
        # Starting just after the first '[' also sees nested image links.
        index = start + 1 if "[" in label else end
    return found


def markdown_files(root: Path) -> list[Path]:
    def listed(*arguments: str) -> set[str]:
        result = subprocess.run(["git", "ls-files", "-z", *arguments], cwd=root, check=True,
                                capture_output=True, text=True)
        return {name for name in result.stdout.split("\0") if name.lower().endswith((".md", ".mdx"))}

    tracked = listed("--cached")
    new = listed("--others", "--exclude-standard")
    selected = tracked | {name for name in new if "/" not in name or Path(name).parts[0] in NEW_DOCUMENT_ROOTS}
    return sorted(root / name for name in selected)


def legacy_anchors(root: Path) -> dict[str, list[str]]:
    data = json.loads((root / LEGACY_PATH).read_text())
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("anchors"), dict):
        raise ValueError("invalid compatibility manifest")
    for name, values in data["anchors"].items():
        path = Path(name)
        if (path.is_absolute() or ".." in path.parts or not isinstance(values, list)
                or not all(isinstance(value, str) for value in values) or len(set(values)) != len(values)):
            raise ValueError("invalid compatibility path or anchor list")
    return data["anchors"]


def check(root: Path, paths: list[Path] | None = None) -> list[str]:
    root = root.resolve()
    paths = markdown_files(root) if paths is None else paths
    errors = []
    cache: dict[Path, set[str]] = {}

    def file_anchors(path: Path) -> set[str]:
        path = path.resolve()
        if path not in cache:
            cache[path] = anchors(path.read_text())
        return cache[path]

    for path in paths:
        name = path.relative_to(root).as_posix()
        if not path.is_file():
            errors.append(f"{name}: missing Markdown file")
            continue
        for link in links(path.read_text()):
            label = f"{name}:{link.line}"
            if link.missing_reference:
                errors.append(f"{label}: undefined link reference [{link.target}]")
                continue
            try:
                parts = urlsplit(link.target)
            except ValueError:
                errors.append(f"{label}: invalid link {link.target!r}")
                continue
            if parts.scheme or parts.netloc:
                continue
            local = unquote(parts.path)
            target = (root / local.lstrip("/") if local.startswith("/") else path.parent / local) if local else path
            target = target.resolve()
            if not target.is_relative_to(root):
                errors.append(f"{label}: local link leaves repository: {link.target}")
                continue
            if not target.exists():
                errors.append(f"{label}: missing target: {link.target}")
                continue
            fragment = unquote(parts.fragment)
            if fragment and target.suffix.lower() in {".md", ".mdx"} and fragment not in file_anchors(target):
                errors.append(f"{label}: missing anchor: {link.target}")
    for name, preserved in legacy_anchors(root).items():
        path = root / name
        if not path.is_file():
            errors.append(f"{name}: missing compatibility document")
        else:
            for anchor in preserved:
                if anchor not in file_anchors(path):
                    errors.append(f"{name}: removed compatibility anchor #{anchor}")
    return sorted(set(errors))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root (default: this checkout)")
    args = parser.parse_args(argv)
    try:
        errors = check(args.root)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Documentation check failed: {error}")
        return 1
    if errors:
        print("\n".join(errors))
        return 1
    print("Documentation links and compatibility anchors are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
