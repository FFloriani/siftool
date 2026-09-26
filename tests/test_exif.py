import struct, pytest
from siftool.model import Category as C
from siftool.metadata.exif import parse_exif, build_minimal_exif, strip_exif_header
from fixtures.make_fixtures import make_tiff_exif

def by_label(findings): return {f.label: f for f in findings}

@pytest.mark.parametrize("order", ["<", ">"])
def test_categories_follow_spec_table(order):
    f = by_label(parse_exif(make_tiff_exif(order), "APP1/Exif"))
    expected = {"Make": C.DEVICE, "Model": C.DEVICE, "LensModel": C.DEVICE, "Orientation": C.APPEARANCE,
                "ColorSpace": C.APPEARANCE, "Software": C.SOFTWARE, "MakerNote": C.SOFTWARE,
                "DateTime": C.TIME, "DateTimeOriginal": C.TIME, "Artist": C.IDENTITY,
                "BodySerialNumber": C.IDENTITY, "ExposureTime": C.CAMERA_SETTINGS,
                "FNumber": C.CAMERA_SETTINGS, "PhotographicSensitivity": C.CAMERA_SETTINGS,
                "UserComment": C.TEXT, "GPSLatitude": C.LOCATION, "GPSLongitudeRef": C.LOCATION,
                "Tag 0xC4A5": C.UNKNOWN, "Thumbnail (IFD1)": C.EXTRAS}
    assert {k: f[k].category for k in expected} == expected
    assert f["Make"].detail == "TestCam" and f["FNumber"].detail == "28/10"
    assert f["GPSLatitude"].detail.startswith("37/1") and f["GPSLatitude"].where == "APP1/Exif/GPS"

def test_strip_exif_header():
    assert strip_exif_header(b"Exif\x00\x00MM\x00*") == b"MM\x00*" and strip_exif_header(b"II*\x00") == b"II*\x00"

def test_unreadable_tiff_is_a_single_unknown_finding():
    fs = parse_exif(b"II*\x00\xff\xff\xff\x7f", "x")
    assert len(fs) == 1 and fs[0].category is C.UNKNOWN and fs[0].label == "EXIF (unreadable)"

@pytest.mark.parametrize("order", ["<", ">"])
def test_minimal_exif_privacy_keeps_only_appearance_big_endian(order):
    out = build_minimal_exif(make_tiff_exif(order), frozenset({C.APPEARANCE}))
    assert out[:4] == b"MM\x00*"
    assert {f.label for f in parse_exif(out, "x")} == {"Orientation", "ColorSpace"}
    assert by_label(parse_exif(out, "x"))["Orientation"].detail == "6"

def test_minimal_exif_photographer_keeps_device_and_camera_values():
    out = build_minimal_exif(make_tiff_exif("<"), frozenset({C.APPEARANCE, C.DEVICE, C.CAMERA_SETTINGS}))
    f = by_label(parse_exif(out, "x"))
    assert {"Make", "Model", "LensModel", "ExposureTime", "FNumber", "PhotographicSensitivity", "Orientation"} <= set(f)
    assert not {"Artist", "BodySerialNumber", "GPSLatitude", "DateTime", "Software", "MakerNote", "Thumbnail (IFD1)"} & set(f)
    assert f["FNumber"].detail == "28/10" and f["Make"].detail == "TestCam"
    assert b"Felipe" not in out and b"SN123456" not in out and b"NIKON" not in out

def test_minimal_exif_returns_none_when_nothing_allowed():
    assert build_minimal_exif(make_tiff_exif(">"), frozenset()) is None
    assert build_minimal_exif(b"II*\x00\xff\xff\xff\x7f", frozenset({C.APPEARANCE})) is None
