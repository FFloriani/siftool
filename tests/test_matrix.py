"""Format x mode matrix (spec success criterion 2): for every synthetic
sample x every Mode, cleaning must leave nothing the mode forbids -- neither
via the reader (`inspect`) nor via a raw byte search for known signatures --
while the essential digest and decoded pixels stay identical to the source,
and the source file itself is never touched.
"""

import hashlib

import pytest

import siftool
from siftool import Mode, Status
from siftool.modes import ALLOWED
from fixtures.make_fixtures import (
    GPS_TEXT,
    MOTION_PHOTO_TRAILER,
    decode_pixels,
    make_jpeg,
    make_png,
    make_webp,
    write,
)

SAMPLES = {
    "jpeg.jpg": lambda: make_jpeg(mpf=True, trailing=MOTION_PHOTO_TRAILER),
    "jpeg_prog.jpg": lambda: make_jpeg(progressive=True),
    "png.png": lambda: make_png(trailing=b"junk"),
    "apng.png": lambda: make_png(apng=True),
    "lossless.webp": lambda: make_webp("lossless", trailing=b"t"),
    "lossy.webp": lambda: make_webp("lossy"),
    "anim.webp": lambda: make_webp("animated"),
}

FORBIDDEN = (
    b"<x:xmpmeta", b"jumb", b"c2pa", b"caBX", GPS_TEXT, b"Felipe", b"SN123456",
    b"TestSoftware", b"secret comment", b"NIKON", b"UNKNOWNDATA",
)


@pytest.mark.parametrize("name", SAMPLES)
@pytest.mark.parametrize("mode", list(Mode))
def test_matrix(tmp_path, name, mode):
    data = SAMPLES[name]()
    src = write(tmp_path, name, data)
    dst = tmp_path / f"out_{name}"
    before = hashlib.sha256(src.read_bytes()).hexdigest()

    res = siftool.clean(src, dst, mode)
    out = dst.read_bytes()

    assert res.verdict.status is Status.VERIFIED
    assert {f.category for f in siftool.inspect(dst).findings} <= ALLOWED[mode]
    assert siftool.inspect(dst).essential_digest == siftool.inspect(src).essential_digest
    assert decode_pixels(out) == decode_pixels(data)
    assert hashlib.sha256(src.read_bytes()).hexdigest() == before
    assert not [s for s in FORBIDDEN if s in out]
