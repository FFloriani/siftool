"""Structural interface every format module implements, plus byte helpers.

A format handler (JPEG/PNG/WEBP, Tasks 6-8) is a plain object -- usually a
module-level singleton -- that calls `siftool.detect.register` on itself at
import time. `siftool.detect` then picks the right handler for a file from
its first bytes via `sniff`, and the engine drives the rest of the
`FormatHandler` methods to parse, plan and rewrite that file.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import BinaryIO, Protocol

from ..model import Block, Category, Container, Plan, Span


class FormatHandler(Protocol):
    """A pluggable codec: recognizes, parses, rewrites and writes one format."""

    name: str
    extensions: tuple[str, ...]

    def sniff(self, head: bytes) -> bool:
        """Return True if `head` (the file's first bytes) is this format."""
        ...

    def parse(self, buf: bytes) -> Container:
        """Parse the full file contents `buf` into a `Container`."""
        ...

    def write(self, container: Container, plan: Plan, out: BinaryIO) -> None:
        """Write `container` to `out`, applying `plan`'s per-block actions."""
        ...

    def essential_digest(self, container: Container) -> str:
        """Return a hex digest identifying the container's essential content."""
        ...

    def rewrite(self, block: Block, allowed: frozenset[Category]) -> bytes | None:
        """Return `block` rewritten to keep only `allowed` categories.

        Returns None if the block cannot be rewritten to satisfy `allowed`
        (the caller then falls back to dropping or zeroing it). The planner
        calls this for every `rewritable` block, even one whose findings
        are all allowed (ruling R18), so the result must be canonical:
        rewriting it again yields the same bytes, and a second clean stays
        byte-identical.
        """
        ...


def sha256_spans(source: bytes, spans: Iterable[Span]) -> str:
    """Return the hex SHA-256 of the concatenation of `source[s.start:s.end]`.

    Spans are hashed in the order given.
    """
    digest = hashlib.sha256()
    for span in spans:
        digest.update(source[span.start : span.end])
    return digest.hexdigest()


def copy_span(source: bytes, span: Span, out: BinaryIO) -> None:
    """Write `source[span.start:span.end]` to `out`."""
    out.write(source[span.start : span.end])
