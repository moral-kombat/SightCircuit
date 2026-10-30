"""Read an explicitly verified SteamVR virtual mirror using Linux V4L2 read()."""
import ctypes
import errno
import os
from pathlib import Path
import platform
import re
import stat
import struct
import sys
import zlib

if not __package__:  # Run as a file: import this checkout's packages without installation.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from framejacker.errors import FramejackerError


MAX_BUFFER_BYTES = 64 * 1024 * 1024
MAX_PIXELS = 16 * 1024 * 1024
CAP_CAPTURE, CAP_READWRITE, CAP_DEVICE_CAPS = 1, 0x01000000, 0x80000000


class MirrorError(FramejackerError):
    pass


class Capability(ctypes.Structure):
    _fields_ = [("driver", ctypes.c_char * 16), ("card", ctypes.c_char * 32),
                ("bus_info", ctypes.c_char * 32), ("version", ctypes.c_uint32),
                ("capabilities", ctypes.c_uint32), ("device_caps", ctypes.c_uint32),
                ("reserved", ctypes.c_uint32 * 3)]


class FormatData(ctypes.Union):
    # v4l2_format's union contains pointers and is 8-byte aligned on our targets.
    _fields_ = [("raw", ctypes.c_ubyte * 200), ("alignment", ctypes.c_uint64)]


class VideoFormat(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("fmt", FormatData)]


QUERYCAP = (2 << 30) | (ctypes.sizeof(Capability) << 16) | (ord("V") << 8)
G_FMT = (3 << 30) | (ctypes.sizeof(VideoFormat) << 16) | (ord("V") << 8) | 4


def candidate_metadata(node_name, sys_root=Path("/sys"), dev_root=Path("/dev")):
    """Read sysfs only; never open a video device during discovery."""
    if not re.fullmatch(r"video[0-9]+", node_name):
        return None
    entry = sys_root / "class" / "video4linux" / node_name
    try:
        resolved = entry.resolve(strict=True)
        virtual = (sys_root / "devices" / "virtual" / "video4linux").resolve()
        resolved.relative_to(virtual)
        if resolved.name != node_name or (entry / "name").read_text().strip() != "SteamVR":
            return None
        major, minor = (int(value) for value in (entry / "dev").read_text().strip().split(":"))
        return {"node": str(dev_root / node_name), "name": "SteamVR",
                "sysfs_path": str(resolved), "major": major, "minor": minor,
                "classification": "steamvr_virtual_mirror_candidate"}
    except (OSError, ValueError):
        return None


def discover(sys_root=Path("/sys"), dev_root=Path("/dev")):
    try:
        entries = sorted((sys_root / "class" / "video4linux").iterdir())
    except FileNotFoundError:
        return {"status": "unavailable", "reason": "video_sysfs_absent", "candidates": []}
    except OSError as error:
        return {"status": "unavailable", "reason": "video_sysfs_unreadable",
                "error": str(error), "candidates": []}
    candidates, rejected = [], []
    for entry in entries:
        item = candidate_metadata(entry.name, sys_root, dev_root)
        if item is not None:
            candidates.append(item)
        else:
            try:
                if (entry / "name").read_text().strip() == "SteamVR":
                    rejected.append({"node": str(dev_root / entry.name),
                                     "sysfs_path": str(entry.resolve()),
                                     "reason": "virtual_video_metadata_not_verified"})
            except OSError:
                pass
    return {"status": "candidates_found" if candidates else "unavailable",
            "reason": "metadata_match" if candidates else "no_verified_steamvr_virtual_node",
            "candidates": candidates, "unverified_named_nodes": rejected}


def select_candidate(discovery, requested=None):
    candidates = discovery["candidates"]
    if requested is not None:
        # No symlink aliases or guessed fallback devices.
        matches = [item for item in candidates if item["node"] == requested]
        if not matches:
            raise MirrorError("unverified_device", "Selected path is not a verified SteamVR virtual mirror candidate")
        return matches[0]
    if not candidates:
        raise MirrorError("mirror_unavailable", "No verified SteamVR virtual mirror candidate is present")
    if len(candidates) != 1:
        raise MirrorError("ambiguous_device", "Several candidates exist; select one explicitly with --device")
    return candidates[0]


def validate_format(width, height, fourcc, stride, size_image, **metadata):
    bytes_per_pixel = {"GREY": 1, "NV12": 1, "NV21": 1, "YU12": 1, "YV12": 1,
                       "YUYV": 2, "UYVY": 2, "YVYU": 2, "VYUY": 2,
                       "RGB3": 3, "BGR3": 3}
    if fourcc not in bytes_per_pixel:
        raise MirrorError("unsupported_format", f"Unsupported mirror FOURCC {fourcc!r}; no format changes were requested")
    if min(width, height) < 1 or max(width, height) > 8192 or width * height > MAX_PIXELS:
        raise MirrorError("invalid_format", "Mirror dimensions exceed the bounded image limits")
    packed_yuv = fourcc in ("YUYV", "UYVY", "YVYU", "VYUY")
    planar_yuv = fourcc in ("NV12", "NV21", "YU12", "YV12")
    if (packed_yuv and width % 2) or (planar_yuv and (width % 2 or height % 2)):
        raise MirrorError("invalid_format", "YUV mirror dimensions are not correctly aligned")
    row_minimum = width * bytes_per_pixel[fourcc]
    stride = stride or row_minimum
    if stride < row_minimum:
        raise MirrorError("invalid_format", "Mirror stride is smaller than a pixel row")
    minimum = stride * height
    if fourcc in ("NV12", "NV21"):
        minimum += stride * (height // 2)
    elif fourcc in ("YU12", "YV12"):
        minimum += ((stride + 1) // 2) * (height // 2) * 2
    if not minimum <= size_image <= MAX_BUFFER_BYTES:
        raise MirrorError("invalid_format", "Mirror sizeimage is inconsistent or exceeds the buffer limit")
    return {"width": width, "height": height, "fourcc": fourcc,
            "stride_bytes": stride, "buffer_bytes": size_image,
            "minimum_buffer_bytes": minimum, **metadata}


def grayscale_sample(data, fmt, max_side):
    """Bounded nearest-neighbor grayscale sample; skip row padding and chroma."""
    if len(data) < fmt["minimum_buffer_bytes"]:
        raise MirrorError("short_frame", "Mirror read did not contain a complete image")
    scale = max(1, (max(fmt["width"], fmt["height"]) + max_side - 1) // max_side)
    xs, ys = range(0, fmt["width"], scale), range(0, fmt["height"], scale)
    pixels = bytearray()
    fourcc = fmt["fourcc"]
    for y in ys:
        row = y * fmt["stride_bytes"]
        for x in xs:
            if fourcc in ("RGB3", "BGR3"):
                offset = row + x * 3
                a, g, b = data[offset:offset + 3]
                r, b = (a, b) if fourcc == "RGB3" else (b, a)
                pixels.append((77 * r + 150 * g + 29 * b) >> 8)
            elif fourcc in ("YUYV", "YVYU", "UYVY", "VYUY"):
                pixels.append(data[row + x * 2 + (fourcc in ("UYVY", "VYUY"))])
            else:
                pixels.append(data[row + x])
    return len(xs), len(ys), bytes(pixels)


def grayscale_png(width, height, pixels):
    if not 0 < width <= 640 or not 0 < height <= 640 or len(pixels) != width * height:
        raise MirrorError("invalid_preview", "Invalid bounded preview dimensions")

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    rows = b"".join(b"\0" + pixels[y * width:(y + 1) * width] for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


class NativeMirror:
    """Only QUERYCAP, G_FMT and nonblocking read on an already verified virtual node."""
    def __init__(self, candidate):
        if platform.system() != "Linux" or platform.machine() not in ("x86_64", "aarch64", "arm64"):
            raise MirrorError("unsupported_platform", "Mirror capture supports Linux x86_64 and ARM64")
        self.fd = None
        self.candidate = candidate
        self.capabilities = None
        self.format = None
        self.observed_format = None
        self.read_diagnostics = {"read_attempts": 0, "would_block_reads": 0, "interrupted_reads": 0}

    def open(self):
        import fcntl

        candidate = self.candidate
        current = candidate_metadata(Path(candidate["node"]).name)
        if current != candidate:
            raise MirrorError("device_changed", "Mirror metadata changed after discovery")
        self.fd = os.open(candidate["node"], os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            info = os.fstat(self.fd)
            if (not stat.S_ISCHR(info.st_mode)
                    or (os.major(info.st_rdev), os.minor(info.st_rdev)) != (candidate["major"], candidate["minor"])
                    or candidate_metadata(Path(candidate["node"]).name) != candidate):
                raise MirrorError("device_changed", "Opened device does not match the verified virtual node")
            data = bytearray(ctypes.sizeof(Capability))
            fcntl.ioctl(self.fd, QUERYCAP, data, True)
            cap = Capability.from_buffer_copy(data)
            driver = bytes(cap.driver).decode("ascii", "replace")
            card = bytes(cap.card).decode("ascii", "replace")
            flags = cap.device_caps if cap.capabilities & CAP_DEVICE_CAPS else cap.capabilities
            self.capabilities = {"driver": driver, "card": card,
                                 "bus_info": bytes(cap.bus_info).decode("ascii", "replace"),
                                 "version": cap.version, "flags": flags}
            compact_driver = re.sub(r"[^a-z0-9]", "", driver.lower())
            if card.strip() != "SteamVR" or not ("v4l2" in compact_driver and "loopback" in compact_driver):
                raise MirrorError("unsupported_driver", "Device is not the expected SteamVR V4L2 loopback mirror")
            if flags & (CAP_CAPTURE | CAP_READWRITE) != (CAP_CAPTURE | CAP_READWRITE):
                raise MirrorError("unsupported_capabilities", "Mirror does not advertise single-plane capture and read()")
            self.format = self.get_format()
        except BaseException:
            self.close()
            raise

    def get_format(self):
        import fcntl

        query = VideoFormat()
        query.type = 1  # V4L2_BUF_TYPE_VIDEO_CAPTURE
        data = bytearray(bytes(query))
        fcntl.ioctl(self.fd, G_FMT, data, True)
        fields = struct.unpack_from("=12I", data, VideoFormat.fmt.offset)
        width, height, code, field, stride, size_image, colorspace, _, flags, encoding, quantization, transfer = fields
        fourcc = code.to_bytes(4, "little").decode("ascii", "replace")
        self.observed_format = {"width": width, "height": height, "fourcc": fourcc,
                                "stride_bytes": stride, "buffer_bytes": size_image,
                                "field": field}
        if field not in (0, 1):  # ANY or progressive NONE; avoid interpreting interlaced fields as frames.
            raise MirrorError("unsupported_format", "Interlaced mirror fields are not supported")
        return validate_format(width, height, fourcc, stride, size_image,
                               field=field, colorspace_code=colorspace, format_flags=flags,
                               ycbcr_encoding_code=encoding, quantization_code=quantization,
                               transfer_function_code=transfer)

    def read_frame(self, _timeout):
        # The caller paces attempts; O_NONBLOCK keeps each read nonblocking.
        # v4l2loopback may register file-I/O capture on the first read(), while
        # poll() reports no readable events for a still-unnegotiated opener.
        # Waiting for readiness first can therefore prevent startup indefinitely.
        self.read_diagnostics["read_attempts"] += 1
        try:
            frame = os.read(self.fd, self.format["buffer_bytes"])
        except OSError as error:
            if error.errno == errno.EAGAIN:
                self.read_diagnostics["would_block_reads"] += 1
                return None
            if error.errno == errno.EINTR:
                self.read_diagnostics["interrupted_reads"] += 1
                return None
            raise
        if not frame:
            raise MirrorError("stream_ended", "Mirror returned end-of-stream")
        if self.get_format() != self.format:
            raise MirrorError("format_changed", "Mirror format changed during acquisition")
        return frame

    def close(self):
        if self.fd is not None:
            descriptor, self.fd = self.fd, None
            os.close(descriptor)
