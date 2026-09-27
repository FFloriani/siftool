import pytest
from pathlib import Path
import siftool
from siftool import Mode, Status, Category as C
from siftool.errors import SameFileError, DestinationExistsError, VerificationFailedError
from siftool.planner import MAXIMUM_APPEARANCE_NOTE
from siftool.safe_io import TEMP_MARKER
from fixtures.make_fixtures import make_jpeg, make_png, make_webp, write

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
