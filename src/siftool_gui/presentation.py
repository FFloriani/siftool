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
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from enum import Enum
from pathlib import Path

from siftool.errors import (
    CorruptFileError,
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


def _describe_finding(finding: Finding) -> str:
    text = f"{category_name(finding.category)}: {finding.label}"
    if finding.detail:
        text += f" ({finding.detail})"
    return text


def error_title(exc: BaseException) -> str:
    """The line-level text for `exc` (spec 5.5, "Texto na linha")."""
    if isinstance(exc, UnsupportedFormatError):
        return "Ainda não suportado" if exc.format_name in _COMING_SOON_FORMATS else "Formato não suportado"
    if isinstance(exc, CorruptFileError):
        return "Arquivo danificado"
    if isinstance(exc, UnsafeFileError):
        return "Grande demais"
    if isinstance(exc, VerificationFailedError):
        return "Não limpou"
    if isinstance(exc, FileNotFoundError):
        return "Não encontrado"
    if isinstance(exc, PermissionError):
        return "Sem permissão"
    if isinstance(exc, OSError):
        return "Erro ao ler ou gravar"
    return "Erro"


def error_explanation(exc: BaseException) -> str:
    """The expanded text for `exc` (spec 5.5, "Texto ao expandir")."""
    if isinstance(exc, UnsupportedFormatError):
        if exc.format_name in _COMING_SOON_FORMATS:
            return "Fotos HEIC e vídeos chegam numa próxima versão."
        return "O Siftool limpa fotos JPG, PNG e WEBP."
    if isinstance(exc, CorruptFileError):
        return "O arquivo tem uma estrutura inválida; limpar assim não seria seguro."
    if isinstance(exc, UnsafeFileError):
        return "Arquivos acima de 512 MB ainda não são aceitos."
    if isinstance(exc, VerificationFailedError):
        base = "A verificação encontrou dados que não saíram, por isso nenhuma cópia foi salva."
        lines = [_describe_finding(f) for f in exc.verdict.violations]
        return base if not lines else base + "\n" + "\n".join(lines)
    if isinstance(exc, FileNotFoundError):
        return "O arquivo foi movido ou apagado."
    if isinstance(exc, PermissionError):
        return "O Windows não deixou ler o arquivo ou gravar a cópia nesta pasta."
    if isinstance(exc, OSError):
        return str(exc)
    return str(exc)


def result_title(result: CleanResult) -> str:
    """"Verificado" or "Com ressalvas" (spec 5.5's `VERIFIED`/`WARNINGS` rows).

    `clean()` only ever returns a `CleanResult` for these two statuses --
    a FAILED verdict raises `VerificationFailedError` instead.
    """
    return "Verificado" if result.verdict.status is Status.VERIFIED else "Com ressalvas"


def result_explanation(result: CleanResult, output_dir: Path) -> str:
    """The expanded text for a (successful) `CleanResult` (spec 5.5)."""
    if result.verdict.status is Status.VERIFIED:
        return f"Cópia salva em {output_dir}. Nada do que o modo proíbe ficou no arquivo."

    base = (
        "A cópia foi salva, mas havia uma estrutura que o Siftool não conhece "
        "e precisou ficar para a imagem não quebrar."
    )
    notes = list(result.notes) + [n for n in result.verdict.notes if n not in result.notes]
    return base if not notes else base + "\n" + "\n".join(notes)


def clean_button_text(n: int) -> str:
    """The primary button's label for `n` cleanable entries."""
    if n == 0:
        return "Limpar"
    if n == 1:
        return "Limpar 1 foto"
    return f"Limpar {n} fotos"


def summary_text(total: int, with_findings: int, unsupported: int) -> str:
    """The list summary line (spec 5.2, item 3). Zero-valued parts are omitted."""
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
    return " · ".join(parts)


def done_text(verified: int, warnings: int, failed: int) -> str:
    """The end-of-batch summary (spec 4.4, item 5)."""
    base = "1 foto limpa e verificada" if verified == 1 else f"{verified} fotos limpas e verificadas"
    parts = [base]
    if warnings:
        parts.append(f"{warnings} com ressalvas")
    if failed:
        parts.append(f"{failed} não limpou")
    return " · ".join(parts)


def failed_title(failed: int) -> str:
    """The title of the warning shown after a batch in which `failed` photos were not cleaned."""
    return "1 foto não foi limpa" if failed == 1 else f"{failed} fotos não foram limpas"


def failed_hint(failed: int) -> str:
    """Where to find out why (each row explains its own failure, spec 5.5)."""
    return "Clique na linha para ver o motivo." if failed == 1 else "Clique em cada linha para ver o motivo."
