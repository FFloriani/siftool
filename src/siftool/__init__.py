"""Siftool v2: a from-scratch, stdlib-only image metadata-cleaning engine."""

from __future__ import annotations

from .api import clean, inspect, verify
from .errors import (
    CorruptFileError,
    DestinationExistsError,
    SameFileError,
    SiftoolError,
    UnsafeFileError,
    UnsupportedFormatError,
    VerificationFailedError,
)
from .model import Category, CleanResult, Finding, Mode, Report, Status, Verdict

__version__ = "2.0.0b1"  # the one source of truth: pyproject.toml and the build read it from here

__all__ = [
    "inspect",
    "clean",
    "verify",
    "Mode",
    "Status",
    "Category",
    "Finding",
    "Report",
    "Verdict",
    "CleanResult",
    "SiftoolError",
    "UnsupportedFormatError",
    "CorruptFileError",
    "UnsafeFileError",
    "DestinationExistsError",
    "SameFileError",
    "VerificationFailedError",
    "__version__",
]
