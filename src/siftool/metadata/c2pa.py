"""C2PA/JUMBF manifest reading for reporting.

`c2pa_findings` looks for a `claim_generator_info` (or the older
`claim_generator`) entry inside a JUMBF/C2PA manifest payload and turns
its generator name into a human-readable `detail`, e.g.
`"generator: ChatGPT · AI-generated"` when the manifest also
carries a `trainedAlgorithmicMedia` marker anywhere in the payload.

This does not decode CBOR: it locates the relevant keys and values with
a handful of deterministic byte offsets/searches, matching the exact
layout real encoders (and this module's own `claim_generator_info`
producers) emit for short text values. `c2pa_findings` always returns
exactly one `Finding(Category.AI_PROVENANCE, "C2PA manifest", ...)` --
`detail` is None when no generator name could be found -- since the
mere presence of a C2PA/JUMBF manifest is itself the finding; removal
is decided elsewhere, by the planner.
"""

from __future__ import annotations

from ..model import Category, Finding

_CLAIM_GENERATOR_INFO = b"claim_generator_info"
_CLAIM_GENERATOR = b"claim_generator"
_CBOR_NAME_KEY = b"\x64name"
_AI_MARKER = b"trainedAlgorithmicMedia"


def _cbor_text_at(payload: bytes, offset: int) -> str | None:
    """Decode a CBOR major-type-3 (text string) item starting at `offset`.

    Header `0x60..0x77` encodes its own length (`header - 0x60`); header
    `0x78` stores the length in the following byte. Any other header, or
    an out-of-range `offset`, yields None. Slicing past the end of
    `payload` never raises in Python, so a truncated string just decodes
    whatever bytes remain (via `errors="replace"`) rather than raising.
    """
    if not 0 <= offset < len(payload):
        return None
    header = payload[offset]
    if 0x60 <= header <= 0x77:
        length, start = header - 0x60, offset + 1
    elif header == 0x78:
        if offset + 1 >= len(payload):
            return None
        length, start = payload[offset + 1], offset + 2
    else:
        return None
    return payload[start : start + length].decode("utf-8", errors="replace")


def _generator_name(payload: bytes) -> str | None:
    """Find the generator's name near a `claim_generator_info`/`claim_generator` key.

    `claim_generator_info` is a CBOR map/array whose `name` entry (the
    CBOR text key `\\x64name`, searched within 64 bytes of the outer key
    to stay inside the same small map) is the generator name. The older
    `claim_generator` key holds the name directly after it. Either form
    missing, or containing no CBOR text where expected, yields None.
    """
    i = payload.find(_CLAIM_GENERATOR_INFO)
    if i >= 0:
        j = payload.find(_CBOR_NAME_KEY, i, i + 64)
        return _cbor_text_at(payload, j + len(_CBOR_NAME_KEY)) if j >= 0 else None
    j = payload.find(_CLAIM_GENERATOR)
    return _cbor_text_at(payload, j + len(_CLAIM_GENERATOR)) if j >= 0 else None


def _detail(payload: bytes) -> str | None:
    name = _generator_name(payload)
    if not name:
        return None
    detail = f"generator: {name}"
    if _AI_MARKER in payload:
        detail += " · AI-generated"
    return detail


def c2pa_findings(payload: bytes, where: str) -> list[Finding]:
    """Classify a JUMBF/C2PA manifest payload as a single AI_PROVENANCE Finding.

    Always returns exactly `[Finding(Category.AI_PROVENANCE, "C2PA
    manifest", detail, where, len(payload))]`; `detail` is None when no
    generator name could be located. Never raises: any error while
    searching for the generator name degrades to `detail=None`.
    """
    try:
        detail = _detail(payload)
    except Exception:
        detail = None
    return [Finding(Category.AI_PROVENANCE, "C2PA manifest", detail, where, len(payload))]
