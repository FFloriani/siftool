"""Command-line interface for siftool: the `scan`, `clean` and `verify` subcommands.

Spec section 10. This module only does argument parsing, input discovery,
output naming and text/JSON rendering; every cleaning or verification
decision stays in `siftool.api`. A per-file problem (missing file,
unsupported format, failed verification, ...) never aborts the batch and
never surfaces as a traceback -- it becomes one failed/error entry, and the
process exit code is the worst outcome seen across the whole batch:
`0` all verified, `1` some warning, `2` some failure or usage error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import siftool
from siftool.detect import supported_extensions
from siftool.errors import SiftoolError
from siftool.model import Finding, Mode, Status

# A previous run's own output: "<stem>_clean<suffix>" or "<stem>_clean (N)<suffix>".
_CLEAN_SUFFIX_RE = re.compile(r"_clean \(\d+\)$")

_EXIT_CODE = {Status.VERIFIED.value: 0, Status.WARNINGS.value: 1, Status.FAILED.value: 2}


def iter_inputs(paths: list[str]) -> list[Path]:
    """Expand `paths` into the sorted list of files a command should process.

    A directory is walked recursively and filtered to `supported_extensions()`,
    skipping any file whose stem is itself a previous run's output (ends in
    `_clean`, or matches `_clean (N)`). A path that names a file directly is
    always included, whatever its extension or stem -- explicit means explicit.
    """
    extensions = supported_extensions()
    found: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            for candidate in path.rglob("*"):
                if not candidate.is_file() or candidate.suffix.lower() not in extensions:
                    continue
                stem = candidate.stem
                if stem.endswith("_clean") or _CLEAN_SUFFIX_RE.search(stem):
                    continue
                found.append(candidate)
        else:
            found.append(path)
    return sorted(found, key=str)


def output_path_for(src: Path, out_dir: Path | None, overwrite: bool) -> Path:
    """Return the path `src`'s cleaned copy should be written to.

    `<stem>_clean<suffix>`, beside `src` or inside `out_dir` when given. If
    that already exists and `overwrite` is False, tries `<stem>_clean (2)
    <suffix>`, then `(3)`, ... until a name that does not exist yet.
    """
    directory = out_dir if out_dir is not None else src.parent
    candidate = directory / f"{src.stem}_clean{src.suffix}"
    if overwrite or not candidate.exists():
        return candidate
    n = 2
    while True:
        candidate = directory / f"{src.stem}_clean ({n}){src.suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def _error_message(exc: Exception) -> str:
    """Render a per-file exception the way spec section 10 asks for."""
    if isinstance(exc, SiftoolError):
        return str(exc)
    if isinstance(exc, FileNotFoundError):
        return "File not found"
    return str(exc)


def _finding_dict(finding: Finding) -> dict:
    return {"category": finding.category.value, "label": finding.label, "detail": finding.detail, "where": finding.where}


def _detail_suffix(finding: dict) -> str:
    return f" - {finding['detail']}" if finding["detail"] else ""


def _status_counts(statuses: list[str]) -> dict:
    return {
        "total": len(statuses),
        "verified": statuses.count(Status.VERIFIED.value),
        "warnings": statuses.count(Status.WARNINGS.value),
        "failed": statuses.count(Status.FAILED.value),
    }


# --------------------------------------------------------------------------
# scan
# --------------------------------------------------------------------------


def _scan_one(path: Path) -> dict:
    try:
        report = siftool.inspect(path)
    except (SiftoolError, OSError) as e:
        return {"file": str(path), "error": _error_message(e)}
    return {
        "file": str(path),
        "format": report.format,
        "findings": [_finding_dict(f) for f in report.findings],
        "notes": report.notes,
    }


def _print_scan_text(record: dict) -> None:
    if "error" in record:
        print(f"{record['file']}  [error] {record['error']}")
        return
    print(f"{record['file']} ({record['format']})")
    if record["findings"]:
        for finding in record["findings"]:
            print(f"  {finding['category']}: {finding['label']}{_detail_suffix(finding)}")
    else:
        print("  no metadata found")


def _run_scan(paths: list[str], as_json: bool) -> int:
    results = [_scan_one(path) for path in iter_inputs(paths)]
    worst = 2 if any("error" in r for r in results) else 0

    if as_json:
        print(json.dumps({"schema": 2, "results": results}, indent=2))
    else:
        for record in results:
            _print_scan_text(record)
    return worst


# --------------------------------------------------------------------------
# clean
# --------------------------------------------------------------------------


def _clean_one(src: Path, out_dir: Path | None, overwrite: bool, mode: Mode) -> dict:
    dst = output_path_for(src, out_dir, overwrite)
    try:
        result = siftool.clean(src, dst, mode, overwrite=overwrite)
    except (SiftoolError, OSError) as e:
        return {
            "file": str(src),
            "output": None,
            "status": Status.FAILED.value,
            "error": _error_message(e),
            "removed": [],
            "kept": [],
            "notes": [],
        }
    notes = list(result.notes) + list(result.verdict.notes)
    return {
        "file": str(src),
        "output": str(result.dst),
        "status": result.verdict.status.value,
        "removed": [_finding_dict(f) for f in result.removed],
        "kept": [_finding_dict(f) for f in result.kept],
        "notes": notes,
    }


def _print_clean_text(record: dict) -> None:
    if "error" in record:
        print(f"{record['file']}  [failed] {record['error']}")
        return
    print(f"{record['file']} -> {record['output']}  [{record['status']}]")
    for finding in record["removed"]:
        print(f"  removed: {finding['label']} ({finding['category']}){_detail_suffix(finding)}")
    for finding in record["kept"]:
        print(f"  kept: {finding['label']} ({finding['category']}){_detail_suffix(finding)}")
    for note in record["notes"]:
        print(f"  note: {note}")


def _run_clean(paths: list[str], mode: Mode, out_dir: Path | None, overwrite: bool, as_json: bool) -> int:
    if out_dir is not None:
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            print(str(e), file=sys.stderr)
            return 2

    results: list[dict] = []
    worst = 0
    for src in iter_inputs(paths):
        record = _clean_one(src, out_dir, overwrite, mode)
        results.append(record)
        worst = max(worst, _EXIT_CODE[record["status"]])

    summary = _status_counts([r["status"] for r in results])
    if as_json:
        print(json.dumps({"schema": 2, "mode": mode.value, "results": results, "summary": summary}, indent=2))
    else:
        for record in results:
            _print_clean_text(record)
        print(
            f"{summary['total']} file(s): {summary['verified']} verified, "
            f"{summary['warnings']} with warnings, {summary['failed']} failed"
        )

    return worst


# --------------------------------------------------------------------------
# verify
# --------------------------------------------------------------------------


def _run_verify(path_str: str, mode: Mode, original: str | None, as_json: bool) -> int:
    path = Path(path_str)
    verdict = siftool.verify(path, mode, original=Path(original) if original else None)
    record = {
        "file": str(path),
        "status": verdict.status.value,
        "violations": [_finding_dict(f) for f in verdict.violations],
        "notes": verdict.notes,
        "essential_match": verdict.essential_match,
    }

    if as_json:
        summary = _status_counts([record["status"]])
        print(json.dumps({"schema": 2, "mode": mode.value, "results": [record], "summary": summary}, indent=2))
    else:
        print(f"{record['file']}  [{record['status']}]")
        for finding in record["violations"]:
            print(f"  violation: {finding['label']} ({finding['category']})")
        for note in record["notes"]:
            print(f"  note: {note}")

    return _EXIT_CODE[record["status"]]


# --------------------------------------------------------------------------
# argument parsing
# --------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="siftool", description="Offline, lossless image metadata cleaner")
    subparsers = parser.add_subparsers(dest="command", required=True)

    mode_choices = [m.value for m in Mode]

    scan = subparsers.add_parser("scan", help="Report what a file's metadata contains, by category")
    scan.add_argument("paths", nargs="+", metavar="FILE")
    scan.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of text")

    clean = subparsers.add_parser("clean", help="Remove metadata and write a cleaned copy")
    clean.add_argument("paths", nargs="+", metavar="PATH")
    clean.add_argument("--mode", choices=mode_choices, default=Mode.PRIVACY.value)
    clean.add_argument("--out", dest="out_dir", metavar="DIR", default=None, help="Write cleaned copies into DIR")
    clean.add_argument("--overwrite", action="store_true", help="Replace an existing output file")
    clean.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of text")

    verify = subparsers.add_parser("verify", help="Check a file against a mode's whitelist")
    verify.add_argument("path", metavar="FILE")
    verify.add_argument("--mode", choices=mode_choices, default=Mode.PRIVACY.value)
    verify.add_argument("--original", default=None, help="Compare essential content against this file")
    verify.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of text")

    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse `argv` (`sys.argv[1:]` when omitted) and run the requested subcommand."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

    args = _build_parser().parse_args(argv)

    if args.command == "scan":
        return _run_scan(args.paths, args.json)
    if args.command == "clean":
        out_dir = Path(args.out_dir) if args.out_dir else None
        return _run_clean(args.paths, Mode(args.mode), out_dir, args.overwrite, args.json)
    if args.command == "verify":
        return _run_verify(args.path, Mode(args.mode), args.original, args.json)
    raise AssertionError(f"unhandled subcommand: {args.command!r}")  # argparse enforces the choices above
