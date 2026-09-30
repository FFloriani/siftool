"""Public API: `inspect`, `clean`, `verify` (spec sections 5.1 and 5.3).

`clean` is the "Verified Clean" seal: it never touches `src`. It writes the
cleaned bytes to a temporary file beside `dst` (`AtomicOutput`), re-reads
that temporary file with `verify_file` -- the same fail-closed check
`verify` exposes on its own -- and only then, if verification did not
`FAIL`, moves it into place (`AtomicOutput.commit`: never over a `dst`
that appeared meanwhile unless `overwrite=True`). Any failure along the
way (including a FAILED verification) leaves no trace beside `dst`:
`out.discard()` runs unconditionally in a `finally`, so no unverified file
is ever delivered.

`mode` may be a `Mode` or its string value (`"privacy"`, ...); `clean` and
`verify` coerce it with `Mode(mode)` before any I/O, so an invalid one is a
`ValueError` (review M11). `inspect` and `clean` raise `SiftoolError`
subclasses for content problems and let `OSError` (`FileNotFoundError`,
`PermissionError`, ...) from reading `src` or writing beside `dst`
propagate unchanged (review M8); `verify` turns both into a FAILED
`Verdict` instead.
"""

from __future__ import annotations

from pathlib import Path

from . import formats  # noqa: F401 -- import registers the JPEG/PNG/WEBP handlers
from .detect import detect
from .errors import VerificationFailedError
from .model import Action, CleanResult, Finding, Kind, Mode, Report, Status, Verdict
from .modes import ALLOWED
from .planner import plan_container
from .safe_io import AtomicOutput, check_destination, load
from .verify import block_findings, verify_file


def inspect(path) -> Report:
    """Parse `path` and report every non-`ESSENTIAL` block's findings, in order.

    Raises on a problem: a content problem is a `SiftoolError`
    (`UnsupportedFormatError`, `CorruptFileError`, `UnsafeFileError`), and
    an `OSError` from reading `path` (`FileNotFoundError`,
    `PermissionError`, ...) propagates unchanged -- unlike `verify`,
    `inspect` never catches either. `report.notes` carries
    `f"Unrecognized structure: {tag}"` for every non-removable `UNKNOWN`
    block.
    """
    buf = load(path)
    handler = detect(buf)
    container = handler.parse(buf)

    findings: list[Finding] = []
    notes: list[str] = []
    for block in container.blocks:
        if block.kind is Kind.ESSENTIAL:
            continue
        findings.extend(block_findings(block))
        if block.kind is Kind.UNKNOWN and not block.removable:
            notes.append(f"Unrecognized structure: {block.tag}")

    digest = handler.essential_digest(container)
    return Report(
        path=str(path), format=container.format, findings=findings, essential_digest=digest, notes=notes
    )


def clean(src, dst, mode: Mode = Mode.PRIVACY, *, overwrite: bool = False) -> CleanResult:
    """Clean `src` into `dst` per spec section 5.3; the "Verified Clean" seal.

    Coerces `mode` with `Mode(mode)` first (`ValueError` if invalid).
    Refuses `src == dst` (`SameFileError`) and an existing `dst` without
    `overwrite=True` (`DestinationExistsError`) before touching either
    file. Otherwise: detect + parse `src`, plan the per-block actions,
    write them to a temporary file beside `dst`, verify that temporary
    file against `src`, and only on `VERIFIED`/`WARNINGS` move it to
    `dst` -- without `overwrite`, never over a `dst` created meanwhile
    (`DestinationExistsError` then too). On `FAILED`, the temporary file
    is removed and `VerificationFailedError` (carrying the failing
    `Verdict`) is raised instead -- no file is ever left at `dst`. Other
    content problems raise their `SiftoolError`; an `OSError`
    (`FileNotFoundError`, `PermissionError`, ...) from reading `src` or
    writing beside `dst` propagates unchanged.

    `removed`/`kept` are derived from the plan's per-block `Action`:
    `KEEP` findings go to `kept`; `DROP`/`ZERO` findings go to `removed`;
    for `REWRITE`, only the findings whose category `mode` allows go to
    `kept`, the rest to `removed`. `CleanResult.notes` is `plan.notes`.
    """
    mode = Mode(mode)
    src = Path(src)
    dst = Path(dst)
    check_destination(src, dst, overwrite)

    buf = load(src)
    handler = detect(buf)
    container = handler.parse(buf)
    plan = plan_container(container, mode, handler.rewrite)

    ok = ALLOWED[mode]
    removed: list[Finding] = []
    kept: list[Finding] = []
    for block, action in zip(container.blocks, plan.actions):
        findings = block_findings(block)
        if action is Action.KEEP:
            kept.extend(findings)
        elif action is Action.REWRITE:
            for finding in findings:
                (kept if finding.category in ok else removed).append(finding)
        else:  # Action.DROP or Action.ZERO
            removed.extend(findings)

    out = AtomicOutput(dst)
    try:
        with out.open() as f:
            handler.write(container, plan, f)
        verdict = verify_file(out.path, mode, original=src)
        if verdict.status is Status.FAILED:
            raise VerificationFailedError(verdict)
        out.commit(overwrite=overwrite)
    finally:
        out.discard()

    return CleanResult(
        src=str(src), dst=str(dst), mode=mode, removed=removed, kept=kept, verdict=verdict, notes=plan.notes
    )


def verify(path, mode: Mode = Mode.PRIVACY, *, original=None) -> Verdict:
    """Verify `path` per spec section 8; never raises for a content problem.

    `mode` is coerced with `Mode(mode)` before any I/O, so an invalid one
    raises `ValueError` -- a caller error, not a content problem. Every
    read or parse failure, `OSError` included, is a FAILED `Verdict`.
    """
    return verify_file(path, Mode(mode), original=original)
