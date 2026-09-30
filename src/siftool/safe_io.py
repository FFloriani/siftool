"""Safe file reading and atomic file writing for the siftool engine.

`load` reads a whole file into memory, refusing empty or over-limit input
before any format handler ever sees the bytes. `check_destination` refuses
to write over the source file, or over an existing destination without
explicit `overwrite`. `AtomicOutput` writes to a temporary file beside the
destination and only moves it into place on `commit()` -- atomically, and,
without `overwrite`, never over a destination that appeared after
`check_destination` ran -- so a crash or error midway never leaves a
partial or corrupt destination.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import BinaryIO

from .errors import (
    CorruptFileError,
    DestinationExistsError,
    SameFileError,
    UnsafeFileError,
)

MAX_IMAGE_BYTES = 512 * 1024 * 1024
TEMP_MARKER = ".siftool-tmp-"

# How `AtomicOutput.commit(overwrite=False)` moves the temp file into place
# without ever replacing a destination (review M9): Windows' `os.rename`
# refuses an existing target; elsewhere `os.rename` silently replaces it, so
# a hard link -- which refuses an existing target on every platform -- is
# made instead and the temp name removed.
_RENAME_REFUSES_EXISTING = os.name == "nt"


def load(path: Path, max_bytes: int = MAX_IMAGE_BYTES) -> bytes:
    """Read `path` fully into memory.

    Raises `CorruptFileError` if the file is empty, or `UnsafeFileError` if
    it is larger than `max_bytes` -- checked via `stat()` before any of the
    file's bytes are read, so an oversized file (e.g. a multi-GB video) is
    never actually loaded into memory. Any `OSError` from the underlying
    `stat`/read (e.g. `FileNotFoundError`) propagates unchanged.
    """
    path = Path(path)
    size = path.stat().st_size
    if size == 0:
        raise CorruptFileError(f"{path} is empty")
    if size > max_bytes:
        raise UnsafeFileError("file too large for an image")
    return path.read_bytes()


def check_destination(src: Path, dst: Path, overwrite: bool) -> None:
    """Refuse an unsafe (`src`, `dst`) pair; the source is never overwritten.

    Raises `SameFileError` if `src` and `dst` name the same file -- either
    by normalized path (catching e.g. a same-file rename that only differs
    in case on Windows) or, once `dst` exists, by `os.path.samefile` (which
    also catches hardlinks/symlinks aliasing the same file). Raises
    `DestinationExistsError` if `dst` already exists and `overwrite` is
    False.
    """
    norm_src = os.path.normcase(os.path.abspath(str(src)))
    norm_dst = os.path.normcase(os.path.abspath(str(dst)))
    if norm_src == norm_dst:
        raise SameFileError(f"{src} and {dst} are the same file")
    if Path(dst).exists():
        if os.path.samefile(src, dst):
            raise SameFileError(f"{src} and {dst} are the same file")
        if not overwrite:
            raise DestinationExistsError(f"{dst} already exists")


class AtomicOutput:
    """Write a file atomically: a temp file beside `dst`, moved into place on commit.

    `open()` gives a binary-write handle to the temp file at `.path`, named
    `f"{dst.name}{TEMP_MARKER}{token}"` so it sorts next to `dst` and is
    easy to spot and sweep up. `commit()` moves it into place (atomic on
    the same filesystem, which it is: same parent directory as `dst`).
    `discard()` removes the temp file if `commit()` never succeeded; it is
    idempotent and a no-op after `commit()`.
    """

    def __init__(self, dst: Path) -> None:
        self.dst = Path(dst)
        temp_name = f"{self.dst.name}{TEMP_MARKER}{secrets.token_hex(4)}"
        self.path = self.dst.parent / temp_name
        self._committed = False

    def open(self) -> BinaryIO:
        return open(self.path, "wb")

    def commit(self, overwrite: bool = True) -> None:
        """Move the temp file to `dst`.

        With `overwrite`, `os.replace`: an existing `dst` is replaced.
        Without it, a no-clobber move that never replaces a `dst` which
        appeared after `check_destination` ran (review M9): `os.rename` on
        Windows, a hard link plus removal of the temp name elsewhere. An
        existing `dst` raises `DestinationExistsError`, leaving the temp
        file for `discard()`. On a filesystem without hard links (FAT, some
        network shares) the move falls back to an existence check followed
        by `os.replace`, the only atomic move left there.
        """
        if overwrite:
            os.replace(self.path, self.dst)
        elif _RENAME_REFUSES_EXISTING:
            try:
                os.rename(self.path, self.dst)
            except FileExistsError:
                raise DestinationExistsError(f"{self.dst} already exists") from None
        else:
            self._link_into_place()
        self._committed = True

    def _link_into_place(self) -> None:
        try:
            os.link(self.path, self.dst)
        except FileExistsError:
            raise DestinationExistsError(f"{self.dst} already exists") from None
        except OSError:  # no hard links on this filesystem
            if self.dst.exists():
                raise DestinationExistsError(f"{self.dst} already exists") from None
            os.replace(self.path, self.dst)
            return
        try:
            self.path.unlink()
        except OSError:
            pass  # dst is complete; only the temp name (TEMP_MARKER) may linger

    def discard(self) -> None:
        if self._committed:
            return
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
