"""Tests for `siftool.batch`: output naming and folder-walk helpers shared
by the CLI and the (future) GUI. See spec section 10 and Task 2's brief.
"""

import sys

import pytest
from fixtures.make_fixtures import write

from siftool.batch import (
    SPEC_WALK_EXTENSIONS,
    is_previous_output,
    iter_inputs,
    output_path_for,
    path_key,
)


def test_iter_inputs_walks_spec_extensions_case_insensitively(tmp_path):
    a = write(tmp_path, "a.JPG", b"x")
    b = write(tmp_path, "b.heic", b"x")
    c = write(tmp_path, "c.mp4", b"x")
    write(tmp_path, "d.txt", b"x")
    assert iter_inputs([tmp_path]) == [a, b, c]


def test_iter_inputs_skips_previous_outputs_in_folders_only(tmp_path):
    clean1 = write(tmp_path, "x_clean.jpg", b"x")
    clean2 = write(tmp_path, "x_clean (2).jpg", b"x")

    # Folder walk: both look like a previous run's own output, so neither is picked up.
    assert iter_inputs([tmp_path]) == []

    # Named explicitly: explicit means explicit, whatever the stem looks like.
    assert iter_inputs([clean1, clean2]) == sorted([clean1, clean2], key=str)


def test_output_path_for_numbers_existing_and_taken(tmp_path):
    src = write(tmp_path, "a.jpg", b"src")
    write(tmp_path, "a_clean.jpg", b"previous run")

    first = output_path_for(src, None, overwrite=False)
    assert first == tmp_path / "a_clean (2).jpg"

    # `taken` rejects a name even under `overwrite=True` (ruling R22): this run
    # already produced both "a_clean.jpg" and "a_clean (2).jpg" for other
    # sources, so the next candidate is "(3)" regardless of what's on disk.
    taken = {path_key(tmp_path / "a_clean.jpg"), path_key(first)}
    second = output_path_for(src, None, overwrite=True, taken=taken)
    assert second == tmp_path / "a_clean (3).jpg"


def test_output_path_for_uses_out_dir(tmp_path):
    src = tmp_path / "in" / "a.jpg"
    out_dir = tmp_path / "out"
    assert output_path_for(src, out_dir, overwrite=False) == out_dir / "a_clean.jpg"


@pytest.mark.skipif(sys.platform != "win32", reason="case-folding is Windows-specific")
def test_path_key_folds_case_on_windows(tmp_path):
    assert path_key(tmp_path / "A.JPG") == path_key(tmp_path / "a.jpg")


def test_is_previous_output_matches_clean_suffix_forms(tmp_path):
    assert is_previous_output(tmp_path / "x_clean.jpg") is True
    assert is_previous_output(tmp_path / "x_clean (2).jpg") is True
    assert is_previous_output(tmp_path / "x_clean (12).jpg") is True
    assert is_previous_output(tmp_path / "x.jpg") is False
    assert is_previous_output(tmp_path / "x_clean_extra.jpg") is False


def test_spec_walk_extensions_is_the_documented_frozenset():
    assert SPEC_WALK_EXTENSIONS == frozenset(
        {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".mp4", ".mov", ".m4v"}
    )
