from siftool.model import Category as C
from siftool.metadata.xmp import xmp_findings
from siftool.metadata.c2pa import c2pa_findings
from siftool.metadata.icc import icc_description
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

def test_iptc_categories():
    fs = iptc_findings(make_iptc_irb(), "APP13")
    by = {f.detail: f.category for f in fs if f.detail}
    assert by["Felipe"] is C.IDENTITY and by["Sao Paulo"] is C.LOCATION and by["secret caption"] is C.TEXT
    assert fs[-1].label == "Photoshop/IPTC data" and fs[-1].category is C.TEXT
    assert [f.label for f in iptc_findings(b"junk", "x")] == ["Photoshop/IPTC data"]
