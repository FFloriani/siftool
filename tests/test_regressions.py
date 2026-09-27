"""Regression tests for the 8 audit bugs (spec success criterion 1, section
11). Names follow the spec; the MP4 one (`test_mp4_location_bytes_are_zeroed`)
is left for M2, since video isn't implemented yet.
"""

import siftool
from siftool import Category as C, Mode, Status
from fixtures.make_fixtures import (
    GPS_TEXT,
    MOTION_PHOTO_TRAILER,
    decode_pixels,
    make_jpeg,
    make_png,
    make_webp,
    riff_chunks,
    write,
)


def test_png_output_is_written(tmp_path):
    dst = tmp_path / "o.png"
    siftool.clean(write(tmp_path, "a.png", make_png()), dst)
    assert dst.exists() and decode_pixels(dst.read_bytes())


def test_verify_missing_file_fails(tmp_path):
    assert siftool.verify(tmp_path / "nope_clean.png").status is Status.FAILED


def test_png_ai_text_chunks_removed_and_detected(tmp_path):
    src = write(tmp_path, "a.png", make_png())
    assert any(
        f.category is C.AI_PROVENANCE and f.label == "parameters" for f in siftool.inspect(src).findings
    )
    dst = tmp_path / "o.png"
    siftool.clean(src, dst)
    assert not any(f.category is C.AI_PROVENANCE for f in siftool.inspect(dst).findings)
    assert b"Seed: 123456" not in dst.read_bytes()


def test_jpeg_trailing_data_removed(tmp_path):
    src = write(tmp_path, "a.jpg", make_jpeg(trailing=MOTION_PHOTO_TRAILER))
    dst = tmp_path / "o.jpg"
    siftool.clean(src, dst)
    assert GPS_TEXT not in dst.read_bytes() and b"ftypmp42" not in dst.read_bytes()


def test_webp_lossless_stays_bit_identical(tmp_path):
    data = make_webp("lossless")
    dst = tmp_path / "o.webp"
    siftool.clean(write(tmp_path, "a.webp", data), dst)
    vp8l = lambda d: [v for t, v in riff_chunks(d) if t == b"VP8L"]
    assert vp8l(dst.read_bytes()) == vp8l(data) and decode_pixels(dst.read_bytes()) == decode_pixels(data)


def test_webp_animation_preserved(tmp_path):
    data = make_webp("animated")
    dst = tmp_path / "o.webp"
    siftool.clean(write(tmp_path, "a.webp", data), dst)
    assert len(decode_pixels(dst.read_bytes())) == len(decode_pixels(data)) == 3


def test_photographer_mode_verifies_without_false_warning(tmp_path):
    res = siftool.clean(write(tmp_path, "a.jpg", make_jpeg()), tmp_path / "o.jpg", Mode.PHOTOGRAPHER)
    assert res.verdict.status is Status.VERIFIED and not res.verdict.notes
