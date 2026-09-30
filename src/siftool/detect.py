"""Recognizes a file's format from its first bytes ("head").

Registered `FormatHandler`s (Tasks 6-8) are tried first, in registration
order: the first one whose `sniff` returns True wins. If none match, a
table of known-but-unsupported formats gives the resulting error a
friendly name. Anything else raises `UnsupportedFormatError(None)`.

This module must never import `siftool.formats`: handler modules import
`register` from here at import time, so importing the package back would
create a circular import. `FormatHandler` is needed only for type hints, so
it is imported under `TYPE_CHECKING` and never actually loaded at runtime.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from .errors import UnsupportedFormatError

if TYPE_CHECKING:
    from .formats.base import FormatHandler

_HEIC_BRANDS = frozenset(
    {b"heic", b"heix", b"mif1", b"msf1", b"heim", b"heis", b"hevc", b"hevx"}
)
_AVIF_BRANDS = frozenset({b"avif", b"avis"})
_MP4_ATOMS = frozenset({b"moov", b"mdat", b"wide", b"free", b"skip", b"pnot"})


def _is_heic(head: bytes) -> bool:
    return head[4:8] == b"ftyp" and head[8:12] in _HEIC_BRANDS


def _is_avif(head: bytes) -> bool:
    return head[4:8] == b"ftyp" and head[8:12] in _AVIF_BRANDS


def _is_mp4_mov(head: bytes) -> bool:
    if head[4:8] == b"ftyp":
        return not _is_heic(head) and not _is_avif(head)
    return head[4:8] in _MP4_ATOMS


_Sniffer = Callable[[bytes], bool]

# Formats siftool recognizes but does not yet know how to clean. Checked in
# order, after every registered handler, so a real handler can always
# override an entry here later.
_KNOWN_UNSUPPORTED: tuple[tuple[_Sniffer, str], ...] = (
    (lambda head: head.startswith((b"GIF87a", b"GIF89a")), "GIF"),
    (lambda head: head.startswith((b"II*\x00", b"MM\x00*")), "TIFF"),
    (lambda head: head.startswith(b"BM"), "BMP"),
    (_is_heic, "HEIC"),
    (_is_avif, "AVIF"),
    (_is_mp4_mov, "MP4/MOV"),
    (lambda head: head.startswith(b"\x1aE\xdf\xa3"), "MKV/WebM"),
    (lambda head: head.startswith(b"RIFF") and head[8:12] == b"AVI ", "AVI"),
    (lambda head: head.startswith(b"%PDF-"), "PDF"),
)

_handlers: list[FormatHandler] = []


def register(handler: FormatHandler) -> FormatHandler:
    """Add `handler` to the registry and return it (for use as a decorator)."""
    _handlers.append(handler)
    return handler


def unregister(handler: FormatHandler) -> None:
    """Remove `handler` from the registry. For tests only."""
    _handlers.remove(handler)


def registered() -> tuple[FormatHandler, ...]:
    """Return the currently registered handlers, in registration order."""
    return tuple(_handlers)


def supported_extensions() -> frozenset[str]:
    """Return the union of every registered handler's `extensions`, lower-cased."""
    return frozenset(ext.lower() for handler in _handlers for ext in handler.extensions)


def detect(head: bytes) -> FormatHandler:
    """Identify the format of a file from its first bytes.

    Tries registered handlers first, in registration order (the first
    `sniff` that returns True wins), then the known-unsupported table.
    Raises `UnsupportedFormatError` (named, or `None` if truly unknown)
    when nothing matches.
    """
    for handler in _handlers:
        if handler.sniff(head):
            return handler
    for sniff, name in _KNOWN_UNSUPPORTED:
        if sniff(head):
            raise UnsupportedFormatError(name)
    raise UnsupportedFormatError(None)
