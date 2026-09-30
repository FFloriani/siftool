import struct, pytest
import tracemalloc
from siftool.model import Category as C, Finding, Mode
from siftool.modes import ALLOWED
from siftool.metadata.exif import parse_exif, build_minimal_exif, strip_exif_header, STANDARD_FORMS, TAG_CATEGORIES
from fixtures.make_fixtures import make_tiff_exif, tiff_header, tiff_ifd

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

# ---------------------------------------------------------------------------
# Final-review fix wave, ruling R18 (review C1, triage T4/T7, review M5).
# parse_exif follows sub-IFD pointers stored as LONG, SHORT or IFD (type 13)
# with count 1, and reports any pointer it cannot follow; the minimal writer
# keeps an allowed tag only in its Exif-standard type and count.
# ---------------------------------------------------------------------------

PRIVACY = ALLOWED[Mode.PRIVACY]
PHOTOGRAPHER = ALLOWED[Mode.PHOTOGRAPHER]
LAT = struct.pack(">6I", 23, 1, 33, 1, 1818, 100)
LON = struct.pack(">6I", 46, 1, 37, 1, 5988, 100)
ORIENTATION_1 = (0x0112, 3, 1, struct.pack(">HH", 1, 0))


def _ifd_len(n):
    return 2 + 12 * n + 4


def _ifd0_tiff(entries, order=">"):
    """A TIFF whose IFD0 holds `entries` = (tag, type, count, value bytes);
    values over 4 bytes are laid out right after the IFD, in entry order."""
    voff = 8 + _ifd_len(len(entries))
    fields, tail = [], b""
    for tag, type_code, count, value in entries:
        if len(value) <= 4:
            fields.append((tag, type_code, count, value.ljust(4, b"\x00")))
        else:
            fields.append((tag, type_code, count, struct.pack(order + "I", voff + len(tail))))
            tail += value
    return tiff_header(order) + tiff_ifd(fields, order=order) + tail


def _ifd0_and_exif_tiff(ifd0_entries, exif_entries, order=">"):
    """A TIFF whose IFD0 holds `ifd0_entries` plus an ExifIFD pointer, and whose
    ExifIFD holds `exif_entries` (both lists of `(tag, type, count, value bytes)`);
    values over 4 bytes are laid out right after both IFDs, IFD0's tail first."""
    exif_off = 8 + _ifd_len(len(ifd0_entries) + 1)
    tail_start = exif_off + _ifd_len(len(exif_entries))

    def _fields(entries, voff):
        fields, tail = [], b""
        for tag, type_code, count, value in entries:
            if len(value) <= 4:
                fields.append((tag, type_code, count, value.ljust(4, b"\x00")))
            else:
                fields.append((tag, type_code, count, struct.pack(order + "I", voff + len(tail))))
                tail += value
        return fields, tail

    ifd0_fields, ifd0_tail = _fields(ifd0_entries, tail_start)
    exif_fields, exif_tail = _fields(exif_entries, tail_start + len(ifd0_tail))
    ifd0_fields.append((0x8769, 4, 1, struct.pack(order + "I", exif_off)))
    ifd0 = tiff_ifd(ifd0_fields, order=order)
    exif = tiff_ifd(exif_fields, order=order)
    return tiff_header(order) + ifd0 + exif + ifd0_tail + exif_tail


def _tiff_gps_pointer(ptr_type, count=1, target=None):
    """IFD0 = Orientation + a GPSInfo pointer typed `ptr_type`, then a GPS IFD with a position."""
    gps_off = 8 + _ifd_len(2)
    vals_off = gps_off + _ifd_len(4)
    target = gps_off if target is None else target
    field = struct.pack(">HH", target, 0) if ptr_type == 3 else struct.pack(">I", target)
    ifd0 = tiff_ifd([ORIENTATION_1, (0x8825, ptr_type, count, field)])
    gps = tiff_ifd([(1, 2, 2, b"S\x00\x00\x00"), (2, 5, 3, struct.pack(">I", vals_off)),
                    (3, 2, 2, b"W\x00\x00\x00"), (4, 5, 3, struct.pack(">I", vals_off + 24))])
    return tiff_header() + ifd0 + gps + LAT + LON


@pytest.mark.parametrize("ptr_type", [4, 3, 13], ids=["LONG", "SHORT", "IFD"])
def test_gps_pointer_is_followed_in_every_standard_form(ptr_type):
    fs = parse_exif(_tiff_gps_pointer(ptr_type), "x")
    assert [(f.label, f.category, f.where) for f in fs] == [
        ("Orientation", C.APPEARANCE, "x/IFD0"),
        ("GPSLatitudeRef", C.LOCATION, "x/GPS"),
        ("GPSLatitude", C.LOCATION, "x/GPS"),
        ("GPSLongitudeRef", C.LOCATION, "x/GPS"),
        ("GPSLongitude", C.LOCATION, "x/GPS"),
    ]
    assert by_label(fs)["GPSLatitude"].detail == "23/1, 33/1, 1818/100"


@pytest.mark.parametrize(
    "ptr_type,count,target",
    [(2, 1, None), (16, 1, None), (4, 2, None), (4, 1, 0x00FFFFF0), (4, 1, 8)],
    ids=["ascii", "long8", "count-2", "out-of-bounds", "loop"],
)
def test_unfollowable_pointer_is_an_unknown_finding(ptr_type, count, target):
    tiff = _tiff_gps_pointer(ptr_type, count, target)
    assert parse_exif(tiff, "x") == [
        Finding(C.APPEARANCE, "Orientation", "1", "x/IFD0"),
        Finding(C.UNKNOWN, "Unfollowed sub-IFD pointer", None, "x/IFD0", len(tiff)),
    ]


def test_exif_ifd_behind_type13_pointer_is_read():
    exif_off = 8 + _ifd_len(2)
    vals_off = exif_off + _ifd_len(2)
    owner, serial = b"Felipe Floriani\x00", b"BODY-SN-998877\x00"
    ifd0 = tiff_ifd([ORIENTATION_1, (0x8769, 13, 1, struct.pack(">I", exif_off))])
    exif = tiff_ifd([(0xA430, 2, len(owner), struct.pack(">I", vals_off)),
                     (0xA431, 2, len(serial), struct.pack(">I", vals_off + len(owner)))])
    fs = parse_exif(tiff_header() + ifd0 + exif + owner + serial, "x")
    assert [(f.label, f.category, f.detail) for f in fs] == [
        ("Orientation", C.APPEARANCE, "1"),
        ("CameraOwnerName", C.IDENTITY, "Felipe Floriani"),
        ("BodySerialNumber", C.IDENTITY, "BODY-SN-998877"),
    ]


def test_interop_ifd_is_descended():  # triage T4
    exif_off = 8 + _ifd_len(1)
    interop_off = exif_off + _ifd_len(2)
    ifd0 = tiff_ifd([(0x8769, 4, 1, struct.pack(">I", exif_off))])
    exif = tiff_ifd([(0xA001, 3, 1, struct.pack(">HH", 1, 0)), (0xA005, 4, 1, struct.pack(">I", interop_off))])
    interop = tiff_ifd([(0x0001, 2, 4, b"R98\x00")])
    assert parse_exif(tiff_header() + ifd0 + exif + interop, "x") == [
        Finding(C.APPEARANCE, "ColorSpace", "1", "x/ExifIFD"),
        Finding(C.UNKNOWN, "Tag 0x0001", "R98", "x/Interop"),
    ]


@pytest.mark.parametrize("order", ["<", ">"])
def test_srational_is_read_and_kept_signed(order):  # triage T4
    # ExposureBiasValue's home is ExifIFD (re-review: the writer no longer
    # keeps it from a flat IFD0), so it is placed behind an ExifIFD pointer.
    exif_off = 8 + _ifd_len(1)
    vals_off = exif_off + _ifd_len(1)
    ifd0 = tiff_ifd([(0x8769, 4, 1, struct.pack(order + "I", exif_off))], order=order)
    exif = tiff_ifd([(0x9204, 10, 1, struct.pack(order + "I", vals_off))], order=order)
    tiff = tiff_header(order) + ifd0 + exif + struct.pack(order + "ii", -1, 3)
    assert parse_exif(tiff, "x") == [Finding(C.CAMERA_SETTINGS, "ExposureBiasValue", "-1/3", "x/ExifIFD")]
    out = build_minimal_exif(tiff, PHOTOGRAPHER)
    assert out[:4] == b"MM\x00*"
    assert parse_exif(out, "x") == [Finding(C.CAMERA_SETTINGS, "ExposureBiasValue", "-1/3", "x/ExifIFD")]


def test_float_and_double_values_are_formatted():  # triage T4
    tiff = _ifd0_tiff([(0xC000, 11, 2, struct.pack(">ff", 1.5, -2.25)), (0xC001, 12, 1, struct.pack(">d", 0.1))])
    assert [(f.label, f.detail) for f in parse_exif(tiff, "x")] == [("Tag 0xC000", "1.5, -2.25"), ("Tag 0xC001", "0.1")]


# Every tag a mode can allow, each in its Exif 2.32 (CIPA DC-008) type and count.
STANDARD_ENTRIES = [
    (0x0112, 3, 1, struct.pack(">H", 8)),                              # Orientation
    (0x011A, 5, 1, struct.pack(">II", 300, 1)),                        # XResolution
    (0x011B, 5, 1, struct.pack(">II", 300, 1)),                        # YResolution
    (0x0128, 3, 1, struct.pack(">H", 2)),                              # ResolutionUnit
    (0xA001, 3, 1, struct.pack(">H", 1)),                              # ColorSpace
    (0xA500, 5, 1, struct.pack(">II", 22, 10)),                        # Gamma
    (0x010F, 2, 6, b"Canon\x00"),                                      # Make
    (0x0110, 2, 128, b"M" * 127 + b"\x00"),                            # Model, at the 128-byte limit
    (0xA432, 5, 4, struct.pack(">8I", 24, 1, 70, 1, 28, 10, 28, 10)),  # LensSpecification
    (0xA433, 2, 6, b"Canon\x00"),                                      # LensMake
    (0xA434, 2, 11, b"EF24-70mm\x00\x00"),                             # LensModel
    (0x829A, 5, 1, struct.pack(">II", 1, 120)),                        # ExposureTime
    (0x829D, 5, 1, struct.pack(">II", 28, 10)),                        # FNumber
    (0x8822, 3, 1, struct.pack(">H", 2)),                              # ExposureProgram
    (0x8827, 3, 3, struct.pack(">3H", 100, 0, 0)),                     # PhotographicSensitivity
    (0x8830, 3, 1, struct.pack(">H", 2)),                              # SensitivityType
    (0x9201, 10, 1, struct.pack(">ii", 7, 1)),                         # ShutterSpeedValue
    (0x9202, 5, 1, struct.pack(">II", 3, 1)),                          # ApertureValue
    (0x9203, 10, 1, struct.pack(">ii", -2, 1)),                        # BrightnessValue
    (0x9204, 10, 1, struct.pack(">ii", -1, 3)),                        # ExposureBiasValue
    (0x9205, 5, 1, struct.pack(">II", 3, 1)),                          # MaxApertureValue
    (0x9207, 3, 1, struct.pack(">H", 5)),                              # MeteringMode
    (0x9208, 3, 1, struct.pack(">H", 0)),                              # LightSource
    (0x9209, 3, 1, struct.pack(">H", 16)),                             # Flash
    (0x920A, 5, 1, struct.pack(">II", 50, 1)),                         # FocalLength
    (0xA402, 3, 1, struct.pack(">H", 0)),                              # ExposureMode
    (0xA403, 3, 1, struct.pack(">H", 0)),                              # WhiteBalance
    (0xA404, 5, 1, struct.pack(">II", 1, 1)),                          # DigitalZoomRatio
    (0xA405, 3, 1, struct.pack(">H", 50)),                             # FocalLengthIn35mmFilm
    (0xA406, 3, 1, struct.pack(">H", 0)),                              # SceneCaptureType
    (0xA408, 3, 1, struct.pack(">H", 0)),                              # Contrast
    (0xA409, 3, 1, struct.pack(">H", 0)),                              # Saturation
    (0xA40A, 3, 1, struct.pack(">H", 0)),                              # Sharpness
]


def _in_order(value, type_code, order):
    """Re-pack a big-endian test value in `order` (ASCII bytes have no byte order)."""
    if order == ">" or type_code == 2:
        return value
    fmt = {3: "H", 5: "I", 10: "i"}[type_code]
    n = len(value) // struct.calcsize(fmt)
    return struct.pack("<" + fmt * n, *struct.unpack(">" + fmt * n, value))


def test_every_allowable_tag_has_a_standard_form():
    allowable = frozenset().union(*ALLOWED.values())
    assert {tag for (_ifd, tag), cat in TAG_CATEGORIES.items() if cat in allowable} == set(STANDARD_FORMS)
    assert {entry[0] for entry in STANDARD_ENTRIES} == set(STANDARD_FORMS)


# Which STANDARD_ENTRIES tags are IFD0-native (the rest are ExifIFD-native):
# re-review, the writer now keeps a tag only from its own home IFD, so the
# fixture below must place each one there instead of flattening all of them
# into IFD0.
_IFD0_TAG_SET = {tag for (ifd, tag) in TAG_CATEGORIES if ifd == "IFD0"}


@pytest.mark.parametrize("order", ["<", ">"])
def test_minimal_writer_keeps_standard_forms_and_is_a_fixed_point(order):
    ifd0_entries = [(t, ty, n, _in_order(v, ty, order)) for t, ty, n, v in STANDARD_ENTRIES if t in _IFD0_TAG_SET]
    exif_entries = [(t, ty, n, _in_order(v, ty, order)) for t, ty, n, v in STANDARD_ENTRIES if t not in _IFD0_TAG_SET]
    tiff = _ifd0_and_exif_tiff(ifd0_entries, exif_entries, order)
    source = parse_exif(tiff, "x")
    assert len(source) == len(STANDARD_ENTRIES)
    for allowed in (PRIVACY, PHOTOGRAPHER):
        out = build_minimal_exif(tiff, allowed)
        expected = sorted((f.label, f.detail) for f in source if f.category in allowed)
        assert sorted((f.label, f.detail) for f in parse_exif(out, "x")) == expected
        assert build_minimal_exif(out, allowed) == out


_STANDARD_BY_TAG = {t: (ty, n, v) for t, ty, n, v in STANDARD_ENTRIES}


def _single_tag_tiff_in_home_ifd(tag, type_code, count, value):
    """A TIFF with exactly one entry -- `(tag, type_code, count, value)` -- in `tag`'s real home IFD."""
    entry = (tag, type_code, count, value)
    if tag in _IFD0_TAG_SET:
        return _ifd0_tiff([entry])
    return _ifd0_and_exif_tiff([], [entry])


NON_STANDARD = {
    "Orientation-ASCII": (0x0112, 2, 2, b"6\x00"),
    "Orientation-SHORTx2": (0x0112, 3, 2, struct.pack(">HH", 6, 6)),
    "Orientation-LONG": (0x0112, 4, 1, struct.pack(">I", 6)),
    "Orientation-value-0": (0x0112, 3, 1, struct.pack(">H", 0)),
    "Orientation-value-9": (0x0112, 3, 1, struct.pack(">H", 9)),
    "XResolution-SHORT": (0x011A, 3, 1, struct.pack(">H", 72)),
    "XResolution-FLOAT": (0x011A, 11, 1, struct.pack(">f", 72.0)),
    "YResolution-ASCII": (0x011B, 2, 7, b"Felipe\x00"),  # fuzz seed 1001 #96: Artist bit-flipped into YResolution
    "ResolutionUnit-LONG": (0x0128, 4, 1, struct.pack(">I", 2)),
    "ColorSpace-LONG": (0xA001, 4, 1, struct.pack(">I", 1)),
    "Gamma-DOUBLE": (0xA500, 12, 1, struct.pack(">d", 2.2)),
    "Make-UNDEFINED": (0x010F, 7, 6, b"Canon\x00"),
    "Make-129-bytes": (0x010F, 2, 129, b"C" * 128 + b"\x00"),
    "LensSpecification-RATIONALx3": (0xA432, 5, 3, struct.pack(">6I", 24, 1, 70, 1, 28, 10)),
    "ExposureTime-SRATIONAL": (0x829A, 10, 1, struct.pack(">ii", 1, 120)),
    "ShutterSpeedValue-RATIONAL": (0x9201, 5, 1, struct.pack(">II", 7, 1)),
    "PhotographicSensitivity-SHORTx4": (0x8827, 3, 4, struct.pack(">4H", 100, 100, 100, 100)),
    "FocalLengthIn35mmFilm-LONG": (0xA405, 4, 1, struct.pack(">I", 50)),
}


@pytest.mark.parametrize("case", sorted(NON_STANDARD))
def test_minimal_writer_drops_allowed_tag_in_non_standard_form(case):
    tag, type_code, count, value = NON_STANDARD[case]
    tiff = _single_tag_tiff_in_home_ifd(tag, type_code, count, value)
    [finding] = parse_exif(tiff, "x")
    assert finding.category in PHOTOGRAPHER  # the reader does see an allowed tag...
    assert build_minimal_exif(tiff, PHOTOGRAPHER) is None  # ...that the writer never carries over

    # Control: the same tag, in the same home IFD, in its real standard form,
    # must be kept -- proving the "None" above is the form mismatch pinned by
    # this case, not the tag's home IFD, its category, or some other
    # unrelated reason (re-review: 7 of these 18 cases used to be vacuous,
    # dropped by the home-IFD gate before the form check ever ran).
    std_type, std_count, std_value = _STANDARD_BY_TAG[tag]
    control_tiff = _single_tag_tiff_in_home_ifd(tag, std_type, std_count, std_value)
    [control_finding] = parse_exif(control_tiff, "x")
    control_out = build_minimal_exif(control_tiff, PHOTOGRAPHER)
    assert control_out is not None
    assert control_finding.label in {f.label for f in parse_exif(control_out, "x")}


def test_minimal_writer_keeps_ascii_only_up_to_its_first_nul():
    tiff = _ifd0_tiff([(0x010F, 2, 19, b"Canon\x00owner=Felipe\x00")])
    out = build_minimal_exif(tiff, PHOTOGRAPHER)
    assert b"owner" not in out
    assert parse_exif(out, "x") == [Finding(C.DEVICE, "Make", "Canon", "x/IFD0")]


# ---------------------------------------------------------------------------
# Re-review of the fix wave: `_collect_allowed` keeps a tag only when it is
# found in its OWN IFD's table (IFD0 -> IFD0's table, ExifIFD -> ExifIFD's
# table). `_resolve`/`parse_exif` are unchanged -- reading still labels a
# misplaced tag via the other table's fallback -- but the minimal writer no
# longer carries one over: a reader like Pillow looks for a tag in its home
# IFD only, so a tag kept in the "wrong" one is dead weight at best.
# ---------------------------------------------------------------------------


def test_writer_keeps_tags_only_in_their_home_ifd():
    exif_off = 8 + _ifd_len(3)  # IFD0: Orientation + ColorSpace (misplaced) + ExifIFD pointer
    xres_off = exif_off + _ifd_len(1)  # right after the 1-entry ExifIFD
    ifd0 = tiff_ifd([ORIENTATION_1,
                      (0xA001, 3, 1, struct.pack(">HH", 1, 0)),        # ColorSpace: home is ExifIFD
                      (0x8769, 4, 1, struct.pack(">I", exif_off))])
    exif = tiff_ifd([(0x011A, 5, 1, struct.pack(">I", xres_off))])     # XResolution: home is IFD0
    tiff = tiff_header() + ifd0 + exif + struct.pack(">II", 72, 1)
    out = build_minimal_exif(tiff, PRIVACY)
    labels = {f.label for f in parse_exif(out, "x")}
    assert labels == {"Orientation"}  # neither misplaced tag survives the write


def test_same_tag_in_ifd0_and_exififd_is_written_once():
    exif_off = 8 + _ifd_len(2)  # IFD0: XResolution (home) + ExifIFD pointer
    vals_off = exif_off + _ifd_len(1)  # right after the 1-entry ExifIFD
    ifd0 = tiff_ifd([(0x011A, 5, 1, struct.pack(">I", vals_off)),
                      (0x8769, 4, 1, struct.pack(">I", exif_off))])
    exif = tiff_ifd([(0x011A, 5, 1, struct.pack(">I", vals_off + 8))])  # same tag, wrong IFD
    tiff = tiff_header() + ifd0 + exif + struct.pack(">II", 72, 1) + struct.pack(">II", 300, 1)
    out = build_minimal_exif(tiff, PRIVACY)
    findings = [f for f in parse_exif(out, "x") if f.label == "XResolution"]
    assert len(findings) == 1 and findings[0].detail == "72/1"


# ---------------------------------------------------------------------------
# Residual round, ruling R24 (C1 variant): one entry per (IFD, tag). TIFF
# forbids repeating a tag in an IFD; the reader reports every repeat after
# the first as an UNKNOWN "Duplicate tag" finding (so the block is never
# all-allowed), and the minimal writer keeps only the first entry.
# ---------------------------------------------------------------------------

XRES_72 = struct.pack(">II", 72, 1)
TEXT_PIECES = [b"owner=Fe", b"lipe Flo", b"riani; p"]  # 8 bytes each: one RATIONAL value apiece


def test_repeated_tag_after_the_first_is_an_unknown_finding():
    # IFD0: Orientation, XResolution x2, ExifIFD and GPS pointers; ExifIFD: ColorSpace x2; GPS: GPSLatitudeRef x2.
    exif_off = 8 + _ifd_len(5)
    gps_off = exif_off + _ifd_len(2)
    vals_off = gps_off + _ifd_len(2)
    ifd0 = tiff_ifd([ORIENTATION_1, (0x011A, 5, 1, struct.pack(">I", vals_off)),
                     (0x011A, 5, 1, struct.pack(">I", vals_off + 8)),
                     (0x8769, 4, 1, struct.pack(">I", exif_off)), (0x8825, 4, 1, struct.pack(">I", gps_off))])
    exif = tiff_ifd([(0xA001, 3, 1, struct.pack(">HH", 1, 0)), (0xA001, 3, 1, struct.pack(">HH", 2, 0))])
    gps = tiff_ifd([(1, 2, 2, b"S\x00\x00\x00"), (1, 2, 2, b"N\x00\x00\x00")])
    tiff = tiff_header() + ifd0 + exif + gps + XRES_72 + TEXT_PIECES[0]
    assert parse_exif(tiff, "x") == [
        Finding(C.APPEARANCE, "Orientation", "1", "x/IFD0"),
        Finding(C.APPEARANCE, "XResolution", "72/1", "x/IFD0"),
        Finding(C.UNKNOWN, "Duplicate tag 0x011A", None, "x/IFD0", 8),
        Finding(C.APPEARANCE, "ColorSpace", "1", "x/ExifIFD"),
        Finding(C.UNKNOWN, "Duplicate tag 0xA001", None, "x/ExifIFD", 2),
        Finding(C.LOCATION, "GPSLatitudeRef", "S", "x/GPS"),
        Finding(C.UNKNOWN, "Duplicate tag 0x0001", None, "x/GPS", 2),
    ]


def test_repeated_sub_ifd_pointer_is_reported_not_followed():
    first_gps = 8 + _ifd_len(3)
    second_gps = first_gps + _ifd_len(1)
    lat_off = second_gps + _ifd_len(2)
    ifd0 = tiff_ifd([ORIENTATION_1, (0x8825, 4, 1, struct.pack(">I", first_gps)),
                     (0x8825, 4, 1, struct.pack(">I", second_gps))])
    gps_a = tiff_ifd([(1, 2, 2, b"S\x00\x00\x00")])
    gps_b = tiff_ifd([(1, 2, 2, b"N\x00\x00\x00"), (2, 5, 3, struct.pack(">I", lat_off))])
    assert parse_exif(tiff_header() + ifd0 + gps_a + gps_b + LAT, "x") == [
        Finding(C.APPEARANCE, "Orientation", "1", "x/IFD0"),
        Finding(C.LOCATION, "GPSLatitudeRef", "S", "x/GPS"),
        Finding(C.UNKNOWN, "Duplicate tag 0x8825", None, "x/IFD0", 4),
    ]


def test_minimal_writer_keeps_only_the_first_entry_per_tag():
    tiff = _ifd0_tiff([(0x0112, 3, 1, struct.pack(">H", 6)), (0x011A, 5, 1, XRES_72)]
                      + [(0x011A, 5, 1, piece) for piece in TEXT_PIECES])
    for allowed in (PRIVACY, PHOTOGRAPHER):
        out = build_minimal_exif(tiff, allowed)
        assert parse_exif(out, "x") == [Finding(C.APPEARANCE, "Orientation", "6", "x/IFD0"),
                                        Finding(C.APPEARANCE, "XResolution", "72/1", "x/IFD0")]
        assert not [piece for piece in TEXT_PIECES if piece in out]
        assert build_minimal_exif(out, allowed) == out


def test_minimal_writer_ignores_a_later_entry_even_when_the_first_is_dropped():
    tiff = _ifd0_tiff([(0x011A, 2, 8, b"owner=F\x00"), (0x011A, 5, 1, XRES_72)])  # first: ASCII, not standard
    assert build_minimal_exif(tiff, PRIVACY) is None


def test_minimal_writer_follows_only_the_first_exif_ifd_pointer():
    first_exif = 8 + _ifd_len(2)
    second_exif = first_exif + _ifd_len(1)
    gamma_off = second_exif + _ifd_len(1)
    ifd0 = tiff_ifd([(0x8769, 4, 1, struct.pack(">I", first_exif)), (0x8769, 4, 1, struct.pack(">I", second_exif))])
    exif_a = tiff_ifd([(0xA001, 3, 1, struct.pack(">HH", 1, 0))])
    exif_b = tiff_ifd([(0xA500, 5, 1, struct.pack(">I", gamma_off))])  # Gamma whose RATIONAL is text
    out = build_minimal_exif(tiff_header() + ifd0 + exif_a + exif_b + TEXT_PIECES[0], PRIVACY)
    assert parse_exif(out, "x") == [Finding(C.APPEARANCE, "ColorSpace", "1", "x/ExifIFD")]
    assert TEXT_PIECES[0] not in out


@pytest.mark.parametrize(
    "type_code,count,head,expected",
    [
        (3, 3_000_000, struct.pack(">30H", *range(1, 31)), ", ".join(map(str, range(1, 21))) + ", ..."),
        (5, 1_000_000, struct.pack(">60I", *(v for i in range(1, 31) for v in (i, 1))),
         (", ".join(f"{i}/1" for i in range(1, 21)) + ", ...")[:80]),
        (2, 6_000_000, b"Felipe\x00", "Felipe"),
    ],
    ids=["SHORT", "RATIONAL", "ASCII"],
)
def test_huge_value_arrays_are_formatted_from_their_first_values_only(type_code, count, head, expected):  # M5
    size = count * {2: 1, 3: 2, 5: 8}[type_code]
    tiff = tiff_header() + tiff_ifd([(0xC000, type_code, count, struct.pack(">I", 26))]) + head + bytes(size - len(head))
    tracemalloc.start()
    try:
        [finding] = parse_exif(tiff, "x")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert finding.detail == expected
    assert peak < 1 << 20  # was ~291 MB for 3,000,000 SHORTs: every value unpacked and joined
