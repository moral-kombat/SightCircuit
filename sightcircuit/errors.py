"""Errors that carry a stable machine-readable reason for JSON reports."""


class SightCircuitError(Exception):
    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason


class FrameFormatError(SightCircuitError):
    """A frame's layout is unsupported or inconsistent with its buffer."""


class OutputError(SightCircuitError):
    """An explicit output path is unsuitable or was replaced while in use."""
