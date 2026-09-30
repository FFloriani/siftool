"""Portuguese-language presentation layer for findings, modes and outcomes.

Every user-facing string the GUI shows for a `Category`, `Mode`,
`CleanResult` or engine exception lives here (spec sections 5.3-5.5), so
`pages/clean_page.py` and `delegate.py` (later deliveries) never embed
Portuguese text of their own. Deliberately Qt-free (spec section 4.3 and
architecture rule R4) so this module -- and the rules below -- stay
testable without a display.

Decisions not spelled out verbatim by the spec table (noted here since
the task-4 brief's named tests don't pin them):

- Six of the eleven categories (`location`, `text`, `extras`,
  `camera_settings`, `appearance`, `unknown`) have a fixed, finding-
  independent value in spec 5.4 (the quoted cells); the other five
  (`identity`, `ai_provenance`, `time`, `software`, `device`) describe a
  *rule* instead, given precisely by the task-4 brief.
- When one of those five data-dependent categories has no finding with
  usable `detail` (e.g. a `device` block that only ever reports
  `LensSpecification`, or an XMP `software` finding whose `detail` is
  always `None`), there is no quoted fallback text in the spec table for
  it. Rather than surface the table's technical description (which would
  leak jargon like "Make/Model/Lens" into the simple view, against the
  page's own goal), `_GENERIC_FALLBACKS` supplies a short, jargon-free
  Portuguese phrase for each. `ai_provenance` needs no separate entry:
  its rule already always yields at least "Gerada por IA".

The engine's own words -- finding labels, violations, notes, exception
messages, all in English -- never reach these texts (spec 3, criterion 4;
ruling P11): a failed verification names each category that stayed, a
result with reservations explains itself in one sentence, and an engine
error or unexpected exception gets a Portuguese text of its own. The list
shows the engine's words only under "Detalhes técnicos" (`model.py`).
The one exception is the spec's own: an `OSError` other than "not found"
or "no permission" shows the system's message (spec 5.5) -- its `strerror`
alone, without the error number and the path `str()` adds, and a full disk
in Portuguese.
"""

from __future__ import annotations

import errno
from collections.abc import Sequence
from datetime import datetime
from enum import Enum
from pathlib import Path

from siftool.errors import (
    CorruptFileError,
    DestinationExistsError,
    SameFileError,
    UnsafeFileError,
    UnsupportedFormatError,
    VerificationFailedError,
)
from siftool.model import Category, CleanResult, Finding, Mode, Status
from siftool.modes import ALLOWED

_VALUE_LIMIT = 60


class Outcome(str, Enum):
    """Whether a category's findings survive cleaning in a given `Mode`."""

    REMOVED = "sai"
    KEPT = "fica"


# Display order (task-4 brief) -- deliberately *not* the spec table's
# order. `entries.FileEntry.categories` and the tree model sort by this.
CATEGORY_ORDER: tuple[Category, ...] = (
    Category.LOCATION,
    Category.IDENTITY,
    Category.AI_PROVENANCE,
    Category.TIME,
    Category.DEVICE,
    Category.CAMERA_SETTINGS,
    Category.SOFTWARE,
    Category.TEXT,
    Category.EXTRAS,
    Category.UNKNOWN,
    Category.APPEARANCE,
)

_CATEGORY_NAMES: dict[Category, str] = {
    Category.LOCATION: "Localização",
    Category.IDENTITY: "Dados pessoais",
    Category.AI_PROVENANCE: "Marca de IA",
    Category.TIME: "Data e hora",
    Category.SOFTWARE: "Programas usados",
    Category.TEXT: "Textos e comentários",
    Category.EXTRAS: "Miniaturas e extras",
    Category.DEVICE: "Aparelho",
    Category.CAMERA_SETTINGS: "Configurações da câmera",
    Category.APPEARANCE: "Orientação e cores",
    Category.UNKNOWN: "Dados desconhecidos",
}

# Fixed, finding-independent values -- spec 5.4's quoted cells, copied verbatim.
_FIXED_VALUES: dict[Category, str] = {
    Category.LOCATION: "onde a foto foi tirada",
    Category.TEXT: "descrições, legendas ou comentários",
    Category.EXTRAS: "miniatura escondida ou dados extras",
    Category.CAMERA_SETTINGS: "ISO, abertura, velocidade…",
    Category.APPEARANCE: "orientação e perfil de cor",
    Category.UNKNOWN: "informação que o Siftool não reconhece",
}

# See module docstring: generic, jargon-free fallbacks for the
# data-dependent categories, used only when none of their findings carry
# a usable detail.
_GENERIC_FALLBACKS: dict[Category, str] = {
    Category.IDENTITY: "informação que identifica você",
    Category.SOFTWARE: "nome de um programa usado",
    Category.TIME: "data ou hora do arquivo",
    Category.DEVICE: "marca ou modelo do aparelho",
}

# Order device details are joined in (task-4 brief).
_DEVICE_LABELS: tuple[str, ...] = ("Make", "Model", "LensMake", "LensModel")

# Ruling P1 overrides the task-4 brief, which also listed AVIF here.
_COMING_SOON_FORMATS = frozenset({"HEIC", "HEIF", "MP4/MOV"})


def is_coming_soon(exc: BaseException) -> bool:
    """Whether `exc` is an `UnsupportedFormatError` for a format a next version will clean (HEIC, video)."""
    return isinstance(exc, UnsupportedFormatError) and exc.format_name in _COMING_SOON_FORMATS


def _truncate(value: str, limit: int = _VALUE_LIMIT) -> str:
    """Cut `value` to `limit` characters, ending in "…" when it was longer."""
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def category_name(category: Category) -> str:
    """The category's name in the interface (spec 5.4, "Nome na interface")."""
    return _CATEGORY_NAMES[category]


def _ai_provenance_value(findings: list[Finding]) -> str:
    for finding in findings:
        if finding.detail and "generator: " in finding.detail:
            tail = finding.detail.split("generator: ", 1)[1]
            generator = tail.split(" · ", 1)[0]
            return f"Gerada por IA ({generator})"
    return "Gerada por IA"


def _device_value(findings: list[Finding]) -> str:
    by_label: dict[str, list[str]] = {}
    for finding in findings:
        if finding.label in _DEVICE_LABELS and finding.detail:
            by_label.setdefault(finding.label, []).append(finding.detail)

    parts: list[str] = []
    for label in _DEVICE_LABELS:
        for detail in by_label.get(label, ()):
            if detail not in parts:
                parts.append(detail)

    return " · ".join(parts) if parts else _GENERIC_FALLBACKS[Category.DEVICE]


def _format_time(detail: str) -> str | None:
    """`"AAAA:MM:DD HH:MM[:SS]"` -> `"DD/MM/AAAA HH:MM"`, or `None` if it doesn't parse."""
    text = detail.strip()
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y:%m:%d %H:%M"):
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return parsed.strftime("%d/%m/%Y %H:%M")
    return None


def _time_value(findings: list[Finding]) -> str:
    for finding in findings:
        if finding.detail:
            formatted = _format_time(finding.detail)
            if formatted is not None:
                return formatted
    return _GENERIC_FALLBACKS[Category.TIME]


def _first_detail_value(findings: list[Finding], category: Category) -> str:
    for finding in findings:
        if finding.detail:
            return finding.detail
    return _GENERIC_FALLBACKS[category]


def category_value(category: Category, findings: Sequence[Finding]) -> str:
    """The "human" value shown for `category` (spec 5.4, "Valor humano mostrado").

    `findings` may be that category's own findings, or a mixed list (it
    is filtered to `category` here either way).
    """
    if category in _FIXED_VALUES:
        value = _FIXED_VALUES[category]
    else:
        own = [f for f in findings if f.category is category]
        if category is Category.AI_PROVENANCE:
            value = _ai_provenance_value(own)
        elif category is Category.DEVICE:
            value = _device_value(own)
        elif category is Category.TIME:
            value = _time_value(own)
        elif category in (Category.IDENTITY, Category.SOFTWARE):
            value = _first_detail_value(own, category)
        else:  # pragma: no cover -- every Category is one of the branches above
            raise ValueError(f"Unhandled category: {category!r}")

    return _truncate(value)


def category_outcome(category: Category, mode: Mode) -> Outcome:
    """Whether `category` survives cleaning in `mode` (spec 5.4 / 4.4)."""
    return Outcome.KEPT if category in ALLOWED[mode] else Outcome.REMOVED


_MODE_TITLES: dict[Mode, str] = {
    Mode.PRIVACY: "Privacidade",
    Mode.PHOTOGRAPHER: "Fotógrafo",
    Mode.MAXIMUM: "Máximo",
}

# Spec section 5.3, copied verbatim.
_MODE_DESCRIPTIONS: dict[Mode, str] = {
    Mode.PRIVACY: (
        "Tira tudo o que identifica você: local, aparelho, datas, nomes e marcas de IA. "
        "Mantém só o necessário para a foto aparecer certa (orientação e cores)."
    ),
    Mode.PHOTOGRAPHER: (
        "Como Privacidade, mas mantém câmera, lente e as configurações da foto "
        "(ISO, abertura e velocidade)."
    ),
    Mode.MAXIMUM: (
        "Tira tudo, inclusive orientação e perfil de cor. A foto pode aparecer girada "
        "ou com as cores um pouco diferentes."
    ),
}


def mode_title(mode: Mode) -> str:
    """The mode's name in the interface (spec 5.1's `SegmentedWidget`)."""
    return _MODE_TITLES[mode]


def mode_description(mode: Mode) -> str:
    """The mode's description, verbatim from spec section 5.3."""
    return _MODE_DESCRIPTIONS[mode]


_UNEXPECTED_TITLE = "Não foi possível limpar"
_UNEXPECTED_EXPLANATION = "O Siftool encontrou um problema com este arquivo."


def _violation_lines(violations: Sequence[Finding]) -> list[str]:
    """One Portuguese line per category the verification still found in the copy, each once, in order."""
    lines: list[str] = []
    for finding in violations:
        line = f"{category_name(finding.category)}: ainda estava na cópia"
        if line not in lines:
            lines.append(line)
    return lines


def error_title(exc: BaseException) -> str:
    """The line-level text for `exc` (spec 5.5, "Texto na linha"); any exception gets one."""
    if isinstance(exc, UnsupportedFormatError):
        return "Ainda não suportado" if is_coming_soon(exc) else "Formato não suportado"
    if isinstance(exc, CorruptFileError):
        return "Arquivo danificado"
    if isinstance(exc, UnsafeFileError):
        return "Grande demais"
    if isinstance(exc, VerificationFailedError):
        return "Não limpou"
    if isinstance(exc, DestinationExistsError):  # only in a race: a file took the copy's name meanwhile
        return "Já existe um arquivo com esse nome"
    if isinstance(exc, SameFileError):
        return "Destino igual ao original"
    if isinstance(exc, FileNotFoundError):
        return "Não encontrado"
    if isinstance(exc, PermissionError):
        return "Sem permissão"
    if isinstance(exc, OSError):
        return "Erro ao ler ou gravar"
    return _UNEXPECTED_TITLE  # any other engine error, or a bug: never the English message


_ERROR_DISK_FULL = 112  # the Windows error code for a full disk


def _os_error_text(exc: OSError) -> str:
    """What to say about an `OSError` that has no text of its own: the system's message, or a full disk.

    `str(exc)` puts an error number in brackets in front of the message and
    the file's path, repr'd (so every backslash doubled), after it: only the
    message (`strerror`) is shown, or the whole text when the error has none.
    """
    if exc.errno == errno.ENOSPC or getattr(exc, "winerror", None) == _ERROR_DISK_FULL:
        return "O disco está cheio."
    return str(exc.strerror) if exc.strerror else str(exc)


def error_explanation(exc: BaseException) -> str:
    """The expanded text for `exc` (spec 5.5, "Texto ao expandir"), in Portuguese.

    A failed verification adds one line per category that stayed in the
    copy (ruling P11(a)); its labels are for "Detalhes técnicos" only.
    """
    if isinstance(exc, UnsupportedFormatError):
        if is_coming_soon(exc):
            return "Fotos HEIC e vídeos chegam numa próxima versão."
        return "O Siftool limpa fotos JPG, PNG e WEBP."
    if isinstance(exc, CorruptFileError):
        return "O arquivo tem uma estrutura inválida; limpar assim não seria seguro."
    if isinstance(exc, UnsafeFileError):
        return "Arquivos acima de 512 MB ainda não são aceitos."
    if isinstance(exc, VerificationFailedError):
        base = "A verificação encontrou dados que não saíram, por isso nenhuma cópia foi salva."
        return "\n".join([base, *_violation_lines(exc.verdict.violations)])
    if isinstance(exc, DestinationExistsError):
        return "A cópia não foi salva para não sobrescrever um arquivo existente."
    if isinstance(exc, SameFileError):
        return "O Siftool nunca grava por cima do arquivo original."
    if isinstance(exc, FileNotFoundError):
        return "O arquivo foi movido ou apagado."
    if isinstance(exc, PermissionError):
        return "O Windows não deixou ler o arquivo ou gravar a cópia nesta pasta."
    if isinstance(exc, OSError):
        return _os_error_text(exc)  # spec 5.5: the system's own message
    return _UNEXPECTED_EXPLANATION


def result_title(result: CleanResult) -> str:
    """"Verificado" or "Com ressalvas" (spec 5.5's `VERIFIED`/`WARNINGS` rows).

    `clean()` only ever returns a `CleanResult` for these two statuses --
    a FAILED verdict raises `VerificationFailedError` instead.
    """
    return "Verificado" if result.verdict.status is Status.VERIFIED else "Com ressalvas"


def result_explanation(result: CleanResult, output_dir: Path) -> str:
    """The expanded text for a (successful) `CleanResult` (spec 5.5), in Portuguese.

    The engine's notes -- which structure had to stay -- are for "Detalhes
    técnicos" only: a copy "com ressalvas" always means that an unknown
    structure was kept, which the sentence already says.
    """
    if result.verdict.status is Status.VERIFIED:
        return f"Cópia salva em {output_dir}. Nada do que o modo proíbe ficou no arquivo."
    return (
        "A cópia foi salva, mas havia uma estrutura que o Siftool não conhece "
        "e precisou ficar para a imagem não quebrar."
    )


def clean_button_text(n: int) -> str:
    """The primary button's label for `n` cleanable entries."""
    if n == 0:
        return "Limpar"
    if n == 1:
        return "Limpar 1 foto"
    return f"Limpar {n} fotos"


def summary_text(total: int, with_findings: int, unsupported: int, problems: int = 0) -> str:
    """The list summary line (spec 5.2, item 3). Zero-valued parts are omitted.

    `unsupported` counts only the formats that are coming ("ainda não
    suportada": HEIC, video); `problems` counts the files that cannot be
    cleaned and will not be by a next version either -- another unsupported
    format (txt, PDF, GIF...) or a file with an error.
    """
    parts: list[str] = []
    if total:
        parts.append("1 foto" if total == 1 else f"{total} fotos")
    if with_findings:
        parts.append(
            "1 com informação escondida"
            if with_findings == 1
            else f"{with_findings} com informações escondidas"
        )
    if unsupported:
        parts.append("1 ainda não suportada" if unsupported == 1 else f"{unsupported} ainda não suportadas")
    if problems:
        parts.append("1 com problema" if problems == 1 else f"{problems} com problemas")
    return " · ".join(parts)


def done_text(verified: int, warnings: int, failed: int) -> str:
    """The end-of-batch summary (spec 4.4, item 5). Zero-valued parts are omitted.

    "não limpou" for one photo, "não limparam" from two up.
    """
    parts: list[str] = []
    if verified:
        parts.append("1 foto limpa e verificada" if verified == 1 else f"{verified} fotos limpas e verificadas")
    if warnings:
        parts.append(f"{warnings} com ressalvas")
    if failed:
        parts.append("1 não limpou" if failed == 1 else f"{failed} não limparam")
    return " · ".join(parts)


def batch_message(verified: int, warnings: int, failed: int) -> str:
    """The one message after a batch (ruling P13): `done_text`, plus where to look when some failed.

    Each row explains its own failure (spec 5.5), so the hint sends the
    user to the row -- or to each row, when more than one failed.
    """
    text = done_text(verified, warnings, failed)
    if failed:
        text += " — clique na linha para ver o motivo." if failed == 1 else " — clique em cada linha para ver o motivo."
    return text
