"""Exceptions raised by the siftool engine (`inspect`, `clean`, `verify`)."""

from __future__ import annotations

from .model import Verdict


class SiftoolError(Exception):
    """Base class for all siftool engine errors."""


class UnsupportedFormatError(SiftoolError):
    """The file's format could not be recognized, or has no handler yet."""

    def __init__(self, format_name: str | None = None) -> None:
        self.format_name = format_name
        if format_name:
            message = f"{format_name} is not supported yet"
        else:
            message = "Unsupported or unrecognized file format"
        super().__init__(message)


class CorruptFileError(SiftoolError):
    """The file's structure is malformed beyond safe processing."""


class UnsafeFileError(SiftoolError):
    """The file cannot be safely processed."""


class DestinationExistsError(SiftoolError):
    """The output path already exists."""


class SameFileError(SiftoolError):
    """The source and destination resolve to the same file."""


class VerificationFailedError(SiftoolError):
    """Post-clean verification did not pass; carries the failing Verdict."""

    def __init__(self, verdict: Verdict) -> None:
        self.verdict = verdict
        super().__init__("Verification failed")
