"""Seeded corruption fuzzing for every image handler (spec success criterion 4).

`test_mutations_never_crash_or_falsely_verify` feeds each format's fixture
through 300 seeded random mutations (truncate / bit-flip / 0xFF-splat /
byte-duplicate-insert) and requires that every single one of `inspect`/
`clean`/`verify` either raises a `SiftoolError` or reports a clean
`VERIFIED`/`WARNINGS` outcome, consistent between `clean`'s own verdict and
a fresh `verify` of the result -- never any other exception, and a failed
`clean` never leaves a destination file or a `*.siftool-tmp-*` temp file
behind.

`test_jpeg_content_length_mismatch_is_demoted` (controller ruling R16)
adds dedicated coverage for the JPEG content-length-mismatch demotion path
(`jpeg._demoted`, ruling R15) for SOF0/DHT/SOS -- the seeded fuzzing above
has no guarantee of hitting this exact one-byte-off shape, and only DQT
already has a dedicated test
(`tests/test_jpeg.py::test_dqt_with_trailing_bytes_is_demoted`, whose
pattern this borrows).
"""

from __future__ import annotations

import random
import struct

import pytest

import siftool
from siftool import Status
from siftool.errors import SiftoolError
from siftool.formats.jpeg import JpegHandler
from siftool.model import Category, Finding, Kind
from siftool.safe_io import TEMP_MARKER
from fixtures.make_fixtures import base_jpeg, make_jpeg, make_png, make_webp


def mutate(data: bytes, rng: random.Random) -> bytes:
    d = bytearray(data)
    kind = rng.randrange(4)
    if kind == 0:
        return bytes(d[: rng.randrange(1, len(d))])
    if kind == 1:
        for _ in range(rng.randint(1, 8)):
            d[rng.randrange(len(d))] ^= 1 << rng.randrange(8)
    if kind == 2:
        i = rng.randrange(len(d) - 4)
        d[i : i + 4] = b"\xff\xff\xff\xff"
    if kind == 3:
        i = rng.randrange(len(d))
        d[i:i] = d[i : i + rng.randint(1, 64)]
    return bytes(d)


@pytest.mark.parametrize(
    "name,base",
    [("a.jpg", make_jpeg), ("a.png", make_png), ("a.webp", lambda: make_webp("lossy"))],
    ids=["jpeg", "png", "webp"],
)
def test_mutations_never_crash_or_falsely_verify(tmp_path, name, base):
    rng = random.Random(20260926)
    data = base()
    for n in range(300):
        src = tmp_path / f"{n}_{name}"
        src.write_bytes(mutate(data, rng))
        dst = tmp_path / f"{n}_out_{name}"
        try:
            siftool.inspect(src)
        except SiftoolError:
            pass
        try:
            res = siftool.clean(src, dst)
        except SiftoolError:
            assert not dst.exists()
            continue
        assert res.verdict.status in (Status.VERIFIED, Status.WARNINGS)
        assert siftool.verify(dst, original=src).status is res.verdict.status
    assert not list(tmp_path.glob(f"*{TEMP_MARKER}*"))


# ---------------------------------------------------------------------------
# Controller ruling R16: dedicated coverage for the JPEG content-length
# mismatch demotion paths (ruling R15's `jpeg._demoted`) that the seeded
# fuzzing above has no guarantee of ever constructing. Local helpers
# (rather than importing from tests/test_jpeg.py) per the task brief.
# ---------------------------------------------------------------------------

_MARKER_BY_NAME = {"SOF0": 0xC0, "DHT": 0xC4, "SOS": 0xDA}

H = JpegHandler()


def _first_segment_span(data: bytes, marker: int) -> tuple[int, int]:
    """Return `(start, end)` of the first length-prefixed segment with
    marker byte `marker`, scanning from SOI. Assumes `marker` appears
    before SOS, or is SOS itself (true for APPn/DQT/DHT/SOF0/SOS, reached
    by walking only markers strictly before it) and that no restart
    markers or fill bytes precede it -- true of `base_jpeg()`. Mirrors
    the helper of the same name in `tests/test_jpeg.py`."""
    p = 2
    while True:
        code = data[p + 1]
        length = int.from_bytes(data[p + 2 : p + 4], "big")
        if code == marker:
            return p, p + 2 + length
        p = p + 2 + length


def _rebuild_with_segment(data: bytes, marker: int, payload: bytes) -> bytes:
    """Rebuild a JPEG, replacing the first segment with marker byte
    `marker` with one carrying `payload`; everything else -- including,
    for SOS, the entropy-coded scan data that follows the header -- is
    copied unchanged. Mirrors the helper of the same name in
    `tests/test_jpeg.py`."""
    start, end = _first_segment_span(data, marker)
    segment = bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload
    return data[:start] + segment + data[end:]


@pytest.mark.parametrize("name", ["SOF0", "DHT", "SOS"])
def test_jpeg_content_length_mismatch_is_demoted(tmp_path, name):
    marker = _MARKER_BY_NAME[name]
    src = base_jpeg()
    start, end = _first_segment_span(src, marker)
    original_payload = src[start + 4 : end]
    new_payload = original_payload + b"\x00"
    bad = _rebuild_with_segment(src, marker, new_payload)

    tag = f"{name}?"
    [block] = [b for b in H.parse(bad).blocks if b.tag == tag]
    assert block.kind is Kind.UNKNOWN and block.removable is False
    assert block.findings == [
        Finding(Category.UNKNOWN, f"Non-standard {name} segment", None, tag, len(new_payload))
    ]

    src_path = tmp_path / f"bad_{name}.jpg"
    src_path.write_bytes(bad)
    dst = tmp_path / f"clean_{name}.jpg"
    res = siftool.clean(src_path, dst)
    assert res.verdict.status is Status.WARNINGS
    assert f"Unrecognized structure kept: {tag}" in res.verdict.notes
