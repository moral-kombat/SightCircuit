"""Generated C++ headers from Python contracts: the one renderer and its checks.

A contract module (``hud.native`` for ``tools/hud_contract.hpp``,
``camera.native`` for ``tools/camera_export_protocol.hpp``) describes its header
as sections of ``(name, ctype, value)`` entries; this module turns them into
``constexpr`` declarations, pins every value with a SHA-256 digest constant,
and writes or checks the file. Python stays the single source of truth for any
contract that crosses into C++: the native side includes the header instead of
repeating a value. Values that cannot be rendered exactly (an out-of-range
integer, a non-finite float, a string with a quote) fail closed.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

UINT32 = "std::uint32_t"
UINT64 = "std::uint64_t"


def camel(name):
    """``packet_header`` -> ``PacketHeader``."""
    return "".join(part[:1].upper() + part[1:] for part in name.split("_"))


def literal(ctype, value, unsupported="native_header_value_unsupported"):
    """One C++ initializer; out-of-range or unprintable values fail closed with ``ValueError(unsupported)``."""
    if ctype == "bool" and type(value) is bool:
        return "true" if value else "false"
    if ctype == "int" and type(value) is int and -2**31 <= value < 2**31:
        return str(value)
    if ctype == UINT32 and type(value) is int and 0 <= value < 2**32:
        return str(value)
    if ctype == UINT64 and type(value) is int and 0 <= value < 2**64:
        return f"{value}ULL"
    if ctype == "float" and type(value) is float and math.isfinite(value):
        return f"{value!r}f"
    if (ctype == "char[]" and type(value) is str and value.isascii() and value.isprintable() and
            '"' not in value and "\\" not in value):
        return f'"{value}"'
    if ctype == UINT32 + "[]" and type(value) is tuple and value:
        return "{" + ", ".join(literal(UINT32, item, unsupported) for item in value) + "}"
    raise ValueError(unsupported)


def declaration(name, ctype, value, unsupported="native_header_value_unsupported"):
    array = ctype.endswith("[]")
    return f"constexpr {ctype[:-2] if array else ctype} {name}{'[]' if array else ''} = {literal(ctype, value, unsupported)};"


def entries_of(sections, duplicated="native_header_name_duplicated"):
    """Every entry of ``sections`` in order; a name declared twice fails closed."""
    entries = [entry for _, items in sections for entry in items]
    if len({name for name, _, _ in entries}) != len(entries):
        raise ValueError(duplicated)
    return entries


def digest_of(entries):
    """SHA-256 of every generated name, type and value."""
    canonical = [[name, ctype, list(value) if type(value) is tuple else value] for name, ctype, value in entries]
    return hashlib.sha256(json.dumps(canonical, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def render(banner, namespace, sections, digest_comment, *, unsupported="native_header_value_unsupported",
           duplicated="native_header_name_duplicated"):
    """The header text: ``banner``, the sections as commented declaration blocks, then ``kContractDigest``."""
    entries = entries_of(sections, duplicated)  # Validate names before rendering anything.
    lines = [banner + "#pragma once", "#include <cstdint>", "", f"namespace {namespace} {{"]
    for title, items in sections:
        lines += ["", f"// {title}"] + [declaration(*entry, unsupported) for entry in items]
    lines += ["", f"// {digest_comment}", declaration("kContractDigest", "char[]", digest_of(entries), unsupported),
              f"}} // namespace {namespace}", ""]
    return "\n".join(lines)


def check(path, text):
    """True when ``path`` holds exactly ``text``."""
    try:
        return Path(path).read_text() == text
    except FileNotFoundError:
        return False


def write(path, text):
    """Replace ``path`` with ``text`` atomically; returns whether it changed."""
    path = Path(path)
    if check(path, text):
        return False
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as output:
            output.write(text)
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return True
