import struct
import pytest
from siftool.model import Category as C, Finding
from siftool.metadata.xmp import xmp_findings
from siftool.metadata.c2pa import c2pa_findings
from siftool.metadata.icc import icc_description, icc_profile_valid
from siftool.metadata.iptc import iptc_findings
from fixtures.make_fixtures import make_xmp, make_c2pa_payload, make_icc, make_iptc_irb

def cats(fs): return {f.category for f in fs}

def test_xmp_categories_and_generic_finding():
    fs = xmp_findings(make_xmp(), "APP1/XMP")
    assert {C.AI_PROVENANCE, C.LOCATION, C.IDENTITY, C.SOFTWARE} <= cats(fs)
    assert fs[-1].label == "XMP packet" and fs[-1].category is C.SOFTWARE
    ai = [f for f in fs if f.category is C.AI_PROVENANCE][0]
    assert ai.detail == "trainedAlgorithmicMedia"

def test_xmp_garbage_gives_only_generic():
    assert [f.label for f in xmp_findings(b"\xff\x00garbage", "x")] == ["XMP packet"]

def test_xmp_detection_requires_exact_property_name():
    bad = b"<rdf:Description><dc:creatorX>NotACreator</dc:creatorX></rdf:Description>"
    assert not any(f.category is C.IDENTITY for f in xmp_findings(bad, "x"))
    good = b"<rdf:Description><dc:creator>Felipe</dc:creator></rdf:Description>"
    assert any(f.category is C.IDENTITY for f in xmp_findings(good, "x"))

def test_xmp_attribute_after_tab_or_newline_is_detected():
    packet = b'<rdf:Description\n\tdc:rights="(c) Felipe"/>'
    assert any(f.category is C.IDENTITY for f in xmp_findings(packet, "x"))

def test_c2pa_generator_and_ai_flag():
    [f] = c2pa_findings(make_c2pa_payload(), "APP11")
    assert f.category is C.AI_PROVENANCE and f.label == "C2PA manifest"
    assert f.detail == "generator: ChatGPT · AI-generated"
    [g] = c2pa_findings(make_c2pa_payload(generator="Firefly", ai=False), "x")
    assert g.detail == "generator: Firefly"
    [h] = c2pa_findings(b"jumbc2pa no generator here", "x")
    assert h.detail is None

def test_c2pa_real_layout_map_and_long_name():
    payload = b"jumbc2pa" + b"\x74claim_generator_info" + b"\xa4" + b"\x64name" + b"\x78\x18" + b"OpenAI Media Service API" + b"\x64icon"
    [f] = c2pa_findings(payload, "caBX")
    assert f.detail == "generator: OpenAI Media Service API"

def test_icc_description_v2_v4_and_garbage():
    assert icc_description(make_icc("sRGB IEC61966-2.1", 2)) == "sRGB IEC61966-2.1"
    assert icc_description(make_icc("Display P3", 4)) == "Display P3"
    assert icc_description(b"not an icc profile") is None

def test_icc_profile_structure_is_validated():  # ruling R20
    good = make_icc("Display P3", 4)
    assert icc_profile_valid(good) and icc_profile_valid(make_icc("sRGB IEC61966-2.1", 2))
    wrong_size_field = struct.pack(">I", len(good) + 1) + good[4:]
    no_acsp = good[:36] + b"xxxx" + good[40:]
    too_many_tags = good[:128] + struct.pack(">I", 1000) + good[132:]
    tag_out_of_bounds = good[:136] + struct.pack(">I", len(good)) + good[140:]  # desc offset := profile end
    for bad in (b"", b"not an icc profile", good[:131], wrong_size_field, no_acsp, too_many_tags, tag_out_of_bounds,
                good + b"\x00"):
        assert not icc_profile_valid(bad)

# Residual round, ruling R26: a valid profile also has at least one tag, zero reserved
# header bytes 100-127, and no byte after its tag table that no tag's data covers, bar
# 4-byte alignment padding (at most 3 bytes per gap, and at the end).

def _icc(ranges, area_size, *, reserved=bytes(28)):
    """A 128-byte header ('acsp', `reserved` at 100-127, size field set) + a tag table of
    `(offset, size)` entries + `area_size` bytes of tag data area right after the table."""
    header = bytearray(128)
    header[36:40] = b"acsp"
    header[100:128] = reserved
    table = struct.pack(">I", len(ranges)) + b"".join(
        b"t%03d" % i + struct.pack(">II", offset, size) for i, (offset, size) in enumerate(ranges))
    profile = bytes(header) + table + b"\x5a" * area_size
    return struct.pack(">I", len(profile)) + profile[4:]

ICC_LAYOUTS = {  # case -> (profile, valid); one tag: data area from 144, two tags: from 156
    "one-tag-exact": (_icc([(144, 20)], 20), True),
    "padding-3-at-end": (_icc([(144, 20)], 23), True),
    "padding-3-between": (_icc([(156, 20), (179, 8)], 31), True),
    "shared-data": (_icc([(156, 20), (156, 20)], 20), True),
    "nested-data": (_icc([(156, 20), (160, 4)], 20), True),
    "unsorted-table": (_icc([(176, 8), (156, 20)], 28), True),
    "no-tags": (_icc([], 0), False),
    "no-tags-then-text": (_icc([], 46), False),
    "slack-4-at-end": (_icc([(144, 20)], 24), False),
    "gap-4-between": (_icc([(156, 20), (180, 8)], 32), False),
    "gap-4-before-first": (_icc([(148, 20)], 24), False),
    "reserved-byte-100": (_icc([(144, 20)], 20, reserved=b"\x01" + bytes(27)), False),
    "reserved-byte-127": (_icc([(144, 20)], 20, reserved=bytes(27) + b"\x01"), False),
}

@pytest.mark.parametrize("case", sorted(ICC_LAYOUTS))
def test_icc_profile_carries_no_free_bytes(case):
    profile, valid = ICC_LAYOUTS[case]
    assert icc_profile_valid(profile) is valid

def test_iptc_dataset_names_only_apply_to_record_2():  # review M1
    # 1:90 is the Envelope Record's CodedCharacterSet (ESC % G = UTF-8), not 2:90 City.
    envelope = b"\x1c\x01\x5a\x00\x03\x1b%G"
    irb = b"Photoshop 3.0\x00" + b"8BIM\x04\x04\x00\x00" + struct.pack(">I", len(envelope)) + envelope
    assert iptc_findings(irb, "APP13/IRB") == [
        Finding(C.TEXT, "IPTC 1:90", "\x1b%G", "APP13/IRB"),
        Finding(C.TEXT, "Photoshop/IPTC data", None, "APP13/IRB", len(irb)),
    ]

def test_iptc_categories():
    fs = iptc_findings(make_iptc_irb(), "APP13")
    by = {f.detail: f.category for f in fs if f.detail}
    assert by["Felipe"] is C.IDENTITY and by["Sao Paulo"] is C.LOCATION and by["secret caption"] is C.TEXT
    assert fs[-1].label == "Photoshop/IPTC data" and fs[-1].category is C.TEXT
    assert [f.label for f in iptc_findings(b"junk", "x")] == ["Photoshop/IPTC data"]
