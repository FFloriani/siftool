"""Seeded corruption fuzzing for every image handler (spec success criterion 4).

`test_mutations_never_crash_or_falsely_verify` feeds each format's fixture
through 300 seeded random mutations (truncate / bit-flip / 0xFF-splat /
byte-duplicate-insert) and requires that every single one of `inspect`/
`clean`/`verify` either raises a `SiftoolError` or reports a clean
`VERIFIED`/`WARNINGS` outcome, consistent between `clean`'s own verdict and
a fresh `verify` of the result -- never any other exception, and a failed
`clean` never leaves a destination file or a `*.siftool-tmp-*` temp file
behind. Because `clean` and `verify` share one reader, every VERIFIED
output is also checked by an oracle that owes nothing to it (review M12,
`leaks_in_verified_output`): no fixture metadata string of
`tests/test_matrix.py`'s `FORBIDDEN` list survives, and Pillow's own EXIF
reader sees no GPS entry and no tag outside the appearance set.

`test_jpeg_content_length_mismatch_is_demoted` (controller ruling R16)
adds dedicated coverage for the JPEG content-length-mismatch demotion path
(`jpeg._demoted`, ruling R15) for SOF0/DHT/SOS -- the seeded fuzzing above
has no guarantee of hitting this exact one-byte-off shape, and only DQT
already has a dedicated test
(`tests/test_jpeg.py::test_dqt_with_trailing_bytes_is_demoted`, whose
pattern this borrows).
"""

from __future__ import annotations

import io
import random
import struct
import warnings

import pytest
from PIL import Image

import siftool
from siftool import Status
from siftool.errors import SiftoolError
from siftool.formats.jpeg import JpegHandler
from siftool.model import Category, Finding, Kind
from siftool.safe_io import TEMP_MARKER
from fixtures.make_fixtures import base_jpeg, make_jpeg, make_png, make_webp
from test_matrix import FORBIDDEN

# EXIF tags Pillow may legitimately read from a PRIVACY output: the
# appearance tags (Orientation, X/YResolution, ResolutionUnit, ColorSpace,
# Gamma) and the two the minimal writer generates (ExifIFD pointer,
# ExifVersion). An appearance tag may sit in either IFD.
_APPEARANCE_EXIF_TAGS = frozenset({0x0112, 0x011A, 0x011B, 0x0128, 0xA001, 0xA500})
_WRITER_EXIF_TAGS = frozenset({0x8769, 0x9000})


def pillow_exif(data: bytes) -> tuple[dict, dict, dict] | None:
    """`(IFD0, ExifIFD, GPS)` as Pillow's own EXIF reader sees them, or None if Pillow can't open `data`."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # a mutated image may make Pillow warn; the oracle only reads EXIF
            exif = Image.open(io.BytesIO(data)).getexif()
            return dict(exif), dict(exif.get_ifd(0x8769)), dict(exif.get_ifd(0x8825))
    except Exception:
        return None


def leaks_in_verified_output(data: bytes) -> list[str]:
    """What an independent reader still finds in a PRIVACY output (review M12).

    (a) any `FORBIDDEN` fixture string, by a plain byte search; (b) through
    Pillow's EXIF reader, any GPS entry or any tag outside the appearance
    set. Owes nothing to siftool's own readers, which `clean` and `verify`
    share.
    """
    leaks = [repr(s) for s in FORBIDDEN if s in data]
    exif = pillow_exif(data)
    if exif is not None:
        ifd0, exif_ifd, gps = exif
        allowed = _APPEARANCE_EXIF_TAGS | _WRITER_EXIF_TAGS
        leaks += [f"GPS tag {tag}" for tag in gps]
        leaks += [f"EXIF tag 0x{tag:04X}" for tag in (*ifd0, *exif_ifd) if tag not in allowed]
    return leaks


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


FUZZ_FIXTURES = {
    "jpeg": ("a.jpg", make_jpeg),
    "png": ("a.png", make_png),
    "webp": ("a.webp", lambda: make_webp("lossy")),
    "webp-animated": ("a.webp", lambda: make_webp("animated")),
}


@pytest.mark.parametrize("fixture", sorted(FUZZ_FIXTURES))
def test_oracle_reads_the_pristine_fixtures_exif(tmp_path, fixture):
    # Guards the oracle against passing vacuously: Pillow must read the
    # appearance EXIF a PRIVACY clean keeps, and flag the original's.
    name, base = FUZZ_FIXTURES[fixture]
    src = tmp_path / name
    src.write_bytes(base())
    dst = tmp_path / f"out_{name}"
    res = siftool.clean(src, dst)
    assert res.verdict.status is Status.VERIFIED and leaks_in_verified_output(dst.read_bytes()) == []
    assert pillow_exif(dst.read_bytes())[0][0x0112] == 6
    assert "GPS tag 2" in leaks_in_verified_output(src.read_bytes())


@pytest.mark.parametrize("fixture", sorted(FUZZ_FIXTURES))
def test_mutations_never_crash_or_falsely_verify(tmp_path, fixture):
    name, base = FUZZ_FIXTURES[fixture]
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
        if res.verdict.status is Status.VERIFIED:
            assert leaks_in_verified_output(dst.read_bytes()) == [], f"mutation #{n}"
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
