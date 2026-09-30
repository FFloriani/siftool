import struct

import pytest
from pathlib import Path
import siftool
from siftool import Mode, Status, Category as C
from siftool.errors import SameFileError, DestinationExistsError, VerificationFailedError
from siftool.planner import MAXIMUM_APPEARANCE_NOTE
from siftool.safe_io import TEMP_MARKER
from fixtures.make_fixtures import base_jpeg, make_jpeg, make_png, make_webp, png_chunk, write


def _jpeg_with_empty_exif() -> bytes:
    """A base JPEG with an APP1 Exif segment wrapping a tag-less TIFF.

    The TIFF is `MM\\x00*` + IFD0 offset 8 + an IFD0 with 0 entries + next
    IFD 0 -- syntactically valid, but `parse_exif` finds nothing to report
    (ruling R13's repro: a leftover EXIF shell with no parsed tags).
    """
    payload = b"Exif\x00\x00" + b"MM\x00\x2a\x00\x00\x00\x08" + b"\x00\x00" + b"\x00\x00\x00\x00"
    segment = bytes([0xFF, 0xE1]) + struct.pack(">H", len(payload) + 2) + payload
    base = base_jpeg()
    return base[:2] + segment + base[2:]

def test_inspect_reports_categories(tmp_path):
    r = siftool.inspect(write(tmp_path, "a.jpg", make_jpeg()))
    assert r.format == "jpeg" and len(r.essential_digest) == 64
    assert {C.LOCATION, C.IDENTITY, C.AI_PROVENANCE, C.TIME, C.SOFTWARE, C.TEXT, C.DEVICE,
            C.CAMERA_SETTINGS, C.APPEARANCE, C.UNKNOWN} <= {f.category for f in r.findings}

def test_clean_writes_output_and_splits_findings(tmp_path):
    src = write(tmp_path, "a.png", make_png()); dst = tmp_path / "a_clean.png"
    res = siftool.clean(src, dst)
    assert dst.exists() and res.verdict.status is Status.VERIFIED and res.dst == str(dst)
    assert {f.category for f in res.kept} <= {C.APPEARANCE}
    assert any(f.label == "C2PA manifest" for f in res.removed)

def test_clean_refuses_same_file_and_existing_destination(tmp_path):
    src = write(tmp_path, "a.jpg", make_jpeg()); other = write(tmp_path, "b.jpg", b"x")
    with pytest.raises(SameFileError): siftool.clean(src, src)
    with pytest.raises(DestinationExistsError): siftool.clean(src, other)
    assert siftool.clean(src, other, overwrite=True).verdict.status is Status.VERIFIED

def test_verification_failure_writes_nothing(tmp_path, monkeypatch):
    from siftool.formats.jpeg import JpegHandler
    monkeypatch.setattr(JpegHandler, "write", lambda self, c, p, out: out.write(bytes(c.source)))
    src = write(tmp_path, "a.jpg", make_jpeg()); dst = tmp_path / "o.jpg"
    with pytest.raises(VerificationFailedError) as e: siftool.clean(src, dst)
    assert e.value.verdict.status is Status.FAILED and not dst.exists()
    assert not list(tmp_path.glob(f"*{TEMP_MARKER}*"))

def test_verify_never_raises(tmp_path):
    assert siftool.verify(tmp_path / "missing.png").status is Status.FAILED
    assert siftool.verify(write(tmp_path, "x.bin", b"hello")).status is Status.FAILED

def test_verify_with_original_detects_changed_pixels(tmp_path):
    src = write(tmp_path, "a.jpg", make_jpeg()); dst = tmp_path / "b.jpg"; siftool.clean(src, dst)
    data = bytearray(dst.read_bytes()); i = len(data) - 10
    while data[i] >= 0xFE or data[i - 1] == 0xFF: i -= 1   # não criar marcador falso no scan
    data[i] ^= 0x01; dst.write_bytes(bytes(data))
    v = siftool.verify(dst, original=src)
    assert v.status is Status.FAILED and v.essential_match is False

def test_leftover_unknown_block_is_a_violation(tmp_path):
    src = write(
        tmp_path, "a.jpg",
        make_jpeg(exif=False, xmp=False, icc=False, c2pa=False, iptc=False, comment=False, unknown_app=True),
    )
    v = siftool.verify(src)
    assert v.status is Status.FAILED and any(f.category is C.UNKNOWN for f in v.violations)
    findings = siftool.inspect(src).findings
    assert any(f.category is C.UNKNOWN and f.where == "APP15" for f in findings)

def test_leftover_empty_exif_block_is_a_violation(tmp_path):
    src = write(tmp_path, "a.jpg", _jpeg_with_empty_exif())
    assert siftool.verify(src, Mode.MAXIMUM).status is Status.FAILED
    assert siftool.verify(src, Mode.PRIVACY).status is Status.FAILED
    findings = siftool.inspect(src).findings
    assert any(f.category is C.UNKNOWN and f.where == "APP1:Exif" for f in findings)

def test_empty_exif_shell_is_listed_as_removed(tmp_path):
    src = write(tmp_path, "a.jpg", _jpeg_with_empty_exif())
    res = siftool.clean(src, tmp_path / "a_clean.jpg")
    assert res.verdict.status in (Status.VERIFIED, Status.WARNINGS)
    assert any(f.category is C.UNKNOWN and f.where == "APP1:Exif" for f in res.removed)

def test_kept_unknown_block_gives_warnings_or_fails_on_signature(tmp_path):
    ok = siftool.clean(write(tmp_path, "a.jpg", make_jpeg(reserved_marker=True)), tmp_path / "a_clean.jpg")
    assert ok.verdict.status is Status.WARNINGS
    assert "Unrecognized structure kept: RESERVED:0xF0" in ok.verdict.notes
    bad = write(tmp_path, "b.jpg", make_jpeg(reserved_marker=True, reserved_payload=b"has c2pa inside"))
    with pytest.raises(VerificationFailedError): siftool.clean(bad, tmp_path / "b_clean.jpg")

def test_maximum_mode_note(tmp_path):
    res = siftool.clean(write(tmp_path, "a.jpg", make_jpeg()), tmp_path / "o.jpg", Mode.MAXIMUM)
    assert res.verdict.status is Status.VERIFIED and res.notes == [MAXIMUM_APPEARANCE_NOTE]

# Foco de revisão
def test_clean_path_with_accents_spaces_and_commas(tmp_path):
    src = write(tmp_path, "26 de set. de 2026, 03_30_37 ção.png", make_png())
    assert siftool.clean(src, tmp_path / "saída, limpa ção.png").verdict.status is Status.VERIFIED

def test_clean_detects_format_by_content_not_extension(tmp_path):
    res = siftool.clean(write(tmp_path, "really_png.jpg", make_png()), tmp_path / "really_png_clean.jpg")
    assert siftool.inspect(res.dst).format == "png" and res.verdict.status is Status.VERIFIED

@pytest.mark.parametrize("name,data", [("a.jpg", make_jpeg()), ("a.png", make_png()), ("a.webp", make_webp("lossless"))])
def test_clean_of_already_clean_file_is_byte_identical(tmp_path, name, data):
    first = tmp_path / ("1_" + name); second = tmp_path / ("2_" + name)
    siftool.clean(write(tmp_path, name, data), first)
    res = siftool.clean(first, second)
    assert second.read_bytes() == first.read_bytes() and res.verdict.status is Status.VERIFIED

def _jpeg_with_app14(payload: bytes) -> bytes:
    """A base JPEG with a raw APP14 segment carrying `payload`, spliced in
    right after SOI (Task 9b: reproduce the oversize-Adobe-segment hole)."""
    segment = bytes([0xFF, 0xEE]) + struct.pack(">H", len(payload) + 2) + payload
    base = base_jpeg()
    return base[:2] + segment + base[2:]

# Review M8: OSError (FileNotFoundError, PermissionError, ...) propagates from
# inspect/clean unchanged -- it is not a SiftoolError.

def test_inspect_and_clean_let_oserror_propagate(tmp_path):
    missing = tmp_path / "missing.jpg"
    with pytest.raises(FileNotFoundError): siftool.inspect(missing)
    with pytest.raises(FileNotFoundError): siftool.clean(missing, tmp_path / "out.jpg")
    src = write(tmp_path, "a.jpg", make_jpeg())
    with pytest.raises(FileNotFoundError): siftool.clean(src, tmp_path / "no_such_dir" / "out.jpg")
    assert not list(tmp_path.glob(f"*{TEMP_MARKER}*"))

# Review M9: without overwrite, clean never replaces a destination that
# appeared while it was running (after its own existence check).

def test_clean_never_clobbers_a_destination_created_during_the_clean(tmp_path, monkeypatch):
    src = write(tmp_path, "a.jpg", make_jpeg()); dst = tmp_path / "o.jpg"
    real_verify_file = siftool.api.verify_file
    def racing_verify_file(path, mode, original=None):
        dst.write_bytes(b"late")  # another program creates dst mid-clean
        return real_verify_file(path, mode, original=original)
    monkeypatch.setattr(siftool.api, "verify_file", racing_verify_file)
    with pytest.raises(DestinationExistsError): siftool.clean(src, dst)
    assert dst.read_bytes() == b"late" and not list(tmp_path.glob(f"*{TEMP_MARKER}*"))
    assert siftool.clean(src, dst, overwrite=True).verdict.status is Status.VERIFIED
    assert dst.read_bytes() != b"late"

# Review M11: mode strings are coerced with Mode(mode); a bad one is a
# ValueError before any I/O (a missing source proves nothing was read).

def test_mode_is_coerced_and_validated_before_any_io(tmp_path):
    src = write(tmp_path, "a.jpg", make_jpeg())
    res = siftool.clean(src, tmp_path / "o.jpg", "photographer")
    assert res.mode is Mode.PHOTOGRAPHER and res.verdict.status is Status.VERIFIED
    assert siftool.verify(tmp_path / "o.jpg", "photographer").status is Status.VERIFIED
    missing = tmp_path / "missing.jpg"
    with pytest.raises(ValueError, match="'privcy' is not a valid Mode"): siftool.clean(missing, tmp_path / "x.jpg", "privcy")
    with pytest.raises(ValueError, match="'privcy' is not a valid Mode"): siftool.verify(missing, "privcy")
    assert not (tmp_path / "x.jpg").exists()

# Ruling R23 (review I4, triage T9): a FAILED verdict always says why.

def _flip_a_scan_bit(path):
    data = bytearray(path.read_bytes()); i = len(data) - 10
    while data[i] >= 0xFE or data[i - 1] == 0xFF: i -= 1   # não criar marcador falso no scan
    data[i] ^= 0x01; path.write_bytes(bytes(data))

def test_verify_notes_explain_every_failure(tmp_path):
    src = write(tmp_path, "a.jpg", make_jpeg()); dst = tmp_path / "b.jpg"; siftool.clean(src, dst)
    gif = write(tmp_path, "g.gif", b"GIF89a" + b"\x00" * 20)
    v = siftool.verify(dst, original=gif)
    assert (v.status, v.violations, v.notes) == (Status.FAILED, [], ["Cannot read original: GIF is not supported yet"])
    v = siftool.verify(gif)
    assert (v.status, v.violations, v.notes) == (Status.FAILED, [], ["Cannot read file: GIF is not supported yet"])
    _flip_a_scan_bit(dst)
    v = siftool.verify(dst, original=src)
    assert (v.status, v.violations, v.notes) == (Status.FAILED, [], ["Image data differs from the original"])
    assert v.essential_match is False

def test_verify_fails_a_block_with_a_bad_crc(tmp_path):  # review M3
    png = make_png(icc=False, exif=False, xmp=False, ai_text=False, meta=False, c2pa=False, unknown_ancillary=False)
    at = png.index(b"IDAT") - 4
    bad = write(tmp_path, "crc.png", png[:at] + png_chunk(b"sRGB", b"\x00", bad_crc=True) + png[at:])
    v = siftool.verify(bad)
    assert (v.status, v.violations) == (Status.FAILED, [siftool.Finding(C.UNKNOWN, "Corrupt structure", None, "sRGB", 13)])

def test_secret_in_oversize_adobe_segment_is_never_verified(tmp_path):
    standard = b"Adobe" + b"\x00\x64\x00\x00\x00\x00\x01"
    secret = b" GPS +23.5505-046.6333 owner=Felipe"
    src = write(tmp_path, "a.jpg", _jpeg_with_app14(standard + secret))
    dst = tmp_path / "a_clean.jpg"
    with pytest.raises(VerificationFailedError):
        siftool.clean(src, dst)
    assert not dst.exists()

    src2 = write(tmp_path, "b.jpg", _jpeg_with_app14(standard + b"\x00\x00\x00\x00"))
    res = siftool.clean(src2, tmp_path / "b_clean.jpg")
    assert res.verdict.status is Status.WARNINGS
    assert "Unrecognized structure kept: APP14" in res.verdict.notes
