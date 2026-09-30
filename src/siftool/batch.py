"""Output naming and folder-walking, shared by the CLI and the GUI.

Spec section 10. `iter_inputs` expands a batch's input paths (files and/or
folders) into the sorted list of files to actually process; `output_path_for`
picks the destination for a cleaned copy, numbering around any collision.
Both the CLI (`siftool.cli`) and the Qt-based GUI (later tasks) drive a batch
through this same logic, so it lives here rather than in either front end.
Stdlib-only, plus `siftool` itself -- no Qt, no argument parsing, no
text/JSON rendering.
"""

from __future__ import annotations

import os
import re
from collections.abc import Collection, Iterable
from pathlib import Path

from siftool.detect import supported_extensions

# A previous run's own output: "<stem>_clean<suffix>" or "<stem>_clean (N)<suffix>".
_CLEAN_SUFFIX_RE = re.compile(r"_clean \(\d+\)$")

# Spec section 10's folder-walk extensions. Formats without a handler yet
# (HEIC, video) are walked too, so each is reported ("HEIC is not supported
# yet", exit 2) instead of silently skipped (ruling R22).
SPEC_WALK_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".mp4", ".mov", ".m4v"})


def is_previous_output(path: Path) -> bool:
    """True when `path` looks like a previous run's own output.

    Its stem either ends in `_clean`, or ends in `_clean (N)` for some
    integer `N` -- the two forms `output_path_for` produces.
    """
    stem = path.stem
    return stem.endswith("_clean") or bool(_CLEAN_SUFFIX_RE.search(stem))


def iter_inputs(paths: Iterable[str | Path]) -> list[Path]:
    """Expand `paths` into the sorted list of files a command should process.

    A directory is walked recursively and filtered to spec section 10's
    extensions (`SPEC_WALK_EXTENSIONS`) plus any extension a registered
    handler declares (`supported_extensions()`, e.g. `.apng`/`.jfif`/`.jpe`),
    case-insensitively (ruling R22), skipping any file whose stem is itself
    a previous run's output (`is_previous_output`). A path that names a
    file directly is always included, whatever its extension or stem --
    explicit means explicit.
    """
    extensions = SPEC_WALK_EXTENSIONS | supported_extensions()
    found: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            for candidate in path.rglob("*"):
                if not candidate.is_file() or candidate.suffix.lower() not in extensions:
                    continue
                if is_previous_output(candidate):
                    continue
                found.append(candidate)
        else:
            found.append(path)
    return sorted(found, key=str)


def path_key(path: str | Path) -> str:
    """How two output paths are compared: absolute, and case-folded where the OS folds case."""
    return os.path.normcase(os.path.abspath(path))


def output_path_for(
    src: Path, out_dir: Path | None, overwrite: bool, taken: Collection[str] = frozenset()
) -> Path:
    """Return the path `src`'s cleaned copy should be written to.

    `<stem>_clean<suffix>`, beside `src` or inside `out_dir` when given;
    else `<stem>_clean (2)<suffix>`, then `(3)`, ... The first candidate
    wins unless it is in `taken` -- the `path_key` of every output this
    run has already produced, which is never reused, `overwrite` or not
    (ruling R22: two sources named alike, e.g. `trip1/IMG_0001.jpg` and
    `trip2/IMG_0001.jpg` with `--out`, never share an output) -- or it
    already exists and `overwrite` is False.
    """
    directory = out_dir if out_dir is not None else src.parent
    n = 1
    while True:
        name = f"{src.stem}_clean{src.suffix}" if n == 1 else f"{src.stem}_clean ({n}){src.suffix}"
        candidate = directory / name
        if path_key(candidate) not in taken and (overwrite or not candidate.exists()):
            return candidate
        n += 1
