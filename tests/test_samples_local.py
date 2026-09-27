"""Real-world local samples (spec success criterion 3, section 3).

Every file under `tests/samples-local/` (git-ignored; only `.gitkeep` is
tracked -- see `.gitignore`) with a supported extension is run through the
same public API a user would call, in the default PRIVACY mode, and must
come out `VERIFIED` or `WARNINGS` with nothing left behind but `APPEARANCE`
findings and an unchanged essential digest. Skipped entirely (not failed)
when the folder holds no samples, e.g. on CI or a fresh checkout.

The ChatGPT-generated PNG that motivated this whole rewrite carries a C2PA
manifest in a `caBX` chunk that the old app failed to strip; when a sample
contains one, the test additionally demands it come out `VERIFIED` with the
`caBX` bytes gone and the removal reported.

Ruling R3 (controller): the real manifest's `claim_generator_info.name` is
"OpenAI Media Service API", not "ChatGPT" (see
`tests/test_metadata_readers.py::test_c2pa_real_layout_map_and_long_name`),
so the last assertion checks `"OpenAI" in detail` instead of the plan's
literal wording.
"""

from pathlib import Path

import pytest

import siftool
from siftool import Category as C, Status
from siftool.detect import supported_extensions

SAMPLES = sorted(
    p
    for p in (Path(__file__).parent / "samples-local").iterdir()
    if p.suffix.lower() in supported_extensions()
)


@pytest.mark.skipif(not SAMPLES, reason="no local samples")
@pytest.mark.parametrize("sample", SAMPLES, ids=lambda p: p.name)
def test_real_sample_privacy(tmp_path, sample):
    dst = tmp_path / f"clean{sample.suffix}"
    res = siftool.clean(sample, dst)
    assert res.verdict.status in (Status.VERIFIED, Status.WARNINGS)
    assert {f.category for f in siftool.inspect(dst).findings} <= {C.APPEARANCE}
    assert siftool.inspect(dst).essential_digest == siftool.inspect(sample).essential_digest
    if b"caBX" in sample.read_bytes():
        assert res.verdict.status is Status.VERIFIED and b"caBX" not in dst.read_bytes()
        assert any(f.label == "C2PA manifest" and "OpenAI" in (f.detail or "") for f in res.removed)
