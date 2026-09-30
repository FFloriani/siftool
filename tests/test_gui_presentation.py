"""Tests for `siftool_gui.presentation` (spec sections 5.3-5.5; Task 4). No Qt."""

from __future__ import annotations

import subprocess
import sys

import pytest

from siftool.errors import (
    CorruptFileError,
    DestinationExistsError,
    SameFileError,
    SiftoolError,
    UnsafeFileError,
    UnsupportedFormatError,
    VerificationFailedError,
)
from siftool.model import Category, CleanResult, Finding, Mode, Status, Verdict
from siftool.modes import ALLOWED

from siftool_gui.presentation import (
    CATEGORY_ORDER,
    Outcome,
    batch_message,
    category_name,
    category_outcome,
    category_value,
    clean_button_text,
    done_text,
    error_explanation,
    error_title,
    mode_description,
    mode_title,
    result_explanation,
    result_title,
    summary_text,
)


def test_category_names_cover_every_category():
    expected = {
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
    for category, name in expected.items():
        assert category_name(category) == name

    # CATEGORY_ORDER (task 4 brief's own display order) covers every
    # Category exactly once, even though it isn't the spec table's order.
    assert set(CATEGORY_ORDER) == set(Category)
    assert len(CATEGORY_ORDER) == len(list(Category))
    assert CATEGORY_ORDER == (
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


@pytest.mark.parametrize("mode", list(Mode))
def test_outcomes_follow_allowed(mode):
    for category in Category:
        expected = Outcome.KEPT if category in ALLOWED[mode] else Outcome.REMOVED
        assert category_outcome(category, mode) is expected


def test_mode_descriptions_are_spec_verbatim():
    assert mode_title(Mode.PRIVACY) == "Privacidade"
    assert mode_title(Mode.PHOTOGRAPHER) == "Fotógrafo"
    assert mode_title(Mode.MAXIMUM) == "Máximo"

    assert mode_description(Mode.PRIVACY) == (
        "Tira tudo o que identifica você: local, aparelho, datas, nomes e marcas de IA. "
        "Mantém só o necessário para a foto aparecer certa (orientação e cores)."
    )
    assert mode_description(Mode.PHOTOGRAPHER) == (
        "Como Privacidade, mas mantém câmera, lente e as configurações da foto "
        "(ISO, abertura e velocidade)."
    )
    assert mode_description(Mode.MAXIMUM) == (
        "Tira tudo, inclusive orientação e perfil de cor. A foto pode aparecer girada "
        "ou com as cores um pouco diferentes."
    )


def test_fixed_category_values_are_spec_verbatim():
    assert category_value(Category.LOCATION, []) == "onde a foto foi tirada"
    assert category_value(Category.TEXT, []) == "descrições, legendas ou comentários"
    assert category_value(Category.EXTRAS, []) == "miniatura escondida ou dados extras"
    assert category_value(Category.CAMERA_SETTINGS, []) == "ISO, abertura, velocidade…"
    assert category_value(Category.APPEARANCE, []) == "orientação e perfil de cor"
    assert category_value(Category.UNKNOWN, []) == "informação que o Siftool não reconhece"


def test_ai_value_names_generator():
    findings = [
        Finding(
            Category.AI_PROVENANCE,
            "C2PA manifest",
            "generator: OpenAI Media Service API · AI-generated",
            "where",
        )
    ]
    assert category_value(Category.AI_PROVENANCE, findings) == "Gerada por IA (OpenAI Media Service API)"


def test_ai_value_without_generator_is_plain():
    findings = [Finding(Category.AI_PROVENANCE, "C2PA manifest", None, "where")]
    assert category_value(Category.AI_PROVENANCE, findings) == "Gerada por IA"


def test_device_value_joins_make_model():
    findings = [
        Finding(Category.DEVICE, "Model", "iPhone 15", "IFD0"),
        Finding(Category.DEVICE, "Make", "Apple", "IFD0"),
        Finding(Category.DEVICE, "LensModel", "iPhone 15 back camera", "ExifIFD"),
    ]
    assert category_value(Category.DEVICE, findings) == "Apple · iPhone 15 · iPhone 15 back camera"


def test_device_value_without_usable_labels_falls_back():
    # LensSpecification is Category.DEVICE too, but not one of the four
    # joined labels -- this must not crash, and must not leak the
    # technical label name into the simple view.
    findings = [Finding(Category.DEVICE, "LensSpecification", "24/1, 70/1", "ExifIFD")]
    value = category_value(Category.DEVICE, findings)
    assert value and "LensSpecification" not in value


def test_time_value_is_brazilian_format():
    findings = [Finding(Category.TIME, "DateTimeOriginal", "2024:03:15 10:30", "ExifIFD")]
    assert category_value(Category.TIME, findings) == "15/03/2024 10:30"


def test_time_value_accepts_seconds_but_drops_them():
    findings = [Finding(Category.TIME, "DateTimeOriginal", "2024:03:15 10:30:45", "ExifIFD")]
    assert category_value(Category.TIME, findings) == "15/03/2024 10:30"


def test_identity_and_software_use_first_non_empty_detail():
    findings = [
        Finding(Category.IDENTITY, "Artist", None, "IFD0"),
        Finding(Category.IDENTITY, "Copyright", "Jane Doe", "IFD0"),
    ]
    assert category_value(Category.IDENTITY, findings) == "Jane Doe"

    findings2 = [Finding(Category.SOFTWARE, "Software", "GIMP 2.10", "IFD0")]
    assert category_value(Category.SOFTWARE, findings2) == "GIMP 2.10"


def test_value_without_usable_data_falls_back_without_crashing():
    for category in (Category.IDENTITY, Category.SOFTWARE, Category.TIME, Category.DEVICE):
        findings = [Finding(category, "some-label", None, "where")]
        value = category_value(category, findings)
        assert isinstance(value, str) and value


def test_value_truncates_at_60():
    long_name = "A" * 80
    findings = [Finding(Category.IDENTITY, "Artist", long_name, "IFD0")]
    value = category_value(Category.IDENTITY, findings)
    assert len(value) == 60
    assert value.endswith("…")


def test_error_texts_for_each_exception():
    coming_soon = "Fotos HEIC e vídeos chegam numa próxima versão."
    not_supported = "O Siftool limpa fotos JPG, PNG e WEBP."

    # Ruling P1: only HEIC/HEIF/MP4-MOV are "coming soon" -- AVIF (listed
    # in the task-4 brief's set) and everything else is "not supported".
    for format_name in ("HEIC", "HEIF", "MP4/MOV"):
        exc = UnsupportedFormatError(format_name)
        assert error_title(exc) == "Ainda não suportado"
        assert error_explanation(exc) == coming_soon

    for format_name in ("AVIF", "GIF", "TIFF", "BMP", "MKV/WebM", "AVI", "PDF", None):
        exc = UnsupportedFormatError(format_name)
        assert error_title(exc) == "Formato não suportado"
        assert error_explanation(exc) == not_supported

    corrupt = CorruptFileError("bad structure")
    assert error_title(corrupt) == "Arquivo danificado"
    assert error_explanation(corrupt) == "O arquivo tem uma estrutura inválida; limpar assim não seria seguro."

    unsafe = UnsafeFileError("too big")
    assert error_title(unsafe) == "Grande demais"
    assert error_explanation(unsafe) == "Arquivos acima de 512 MB ainda não são aceitos."

    not_found = FileNotFoundError(2, "No such file or directory")
    assert error_title(not_found) == "Não encontrado"
    assert error_explanation(not_found) == "O arquivo foi movido ou apagado."

    denied = PermissionError(13, "Permission denied")
    assert error_title(denied) == "Sem permissão"
    assert error_explanation(denied) == "O Windows não deixou ler o arquivo ou gravar a cópia nesta pasta."

    generic = OSError(5, "input/output error")
    assert error_title(generic) == "Erro ao ler ou gravar"
    assert error_explanation(generic) == str(generic)

    verdict = Verdict(
        Status.FAILED,
        [
            Finding(Category.AI_PROVENANCE, "C2PA label", None, "RESERVED:0xF0"),
            Finding(Category.LOCATION, "GPS", "1.0, 2.0", "where"),
            Finding(Category.AI_PROVENANCE, "C2PA manifest", "generator: OpenAI Media Service API", "caBX"),
        ],
        ["Image data differs from the original"],
    )
    failed = VerificationFailedError(verdict)
    assert error_title(failed) == "Não limpou"
    # Ruling P11(a): one Portuguese line per category that stayed, each once. The engine's own
    # labels and notes are only shown under "Detalhes técnicos" (spec 3, criterion 4).
    assert error_explanation(failed).split("\n") == [
        "A verificação encontrou dados que não saíram, por isso nenhuma cópia foi salva.",
        "Marca de IA: ainda estava na cópia",
        "Localização: ainda estava na cópia",
    ]

    # Ruling P11(c): the engine errors only a race can raise (a file appearing between naming the
    # copy and writing it)...
    exists = DestinationExistsError(r"C:\fotos\a_clean.jpg already exists")
    assert error_title(exists) == "Já existe um arquivo com esse nome"
    assert error_explanation(exists) == "A cópia não foi salva para não sobrescrever um arquivo existente."
    same = SameFileError("a.jpg and a.jpg are the same file")
    assert error_title(same) == "Destino igual ao original"
    assert error_explanation(same) == "O Siftool nunca grava por cima do arquivo original."

    # ... and every other engine error or unexpected exception: Portuguese, never the English message.
    class FutureEngineError(SiftoolError):
        pass

    for unexpected in (SiftoolError("engine failure"), FutureEngineError("a new failure"),
                       ValueError("unpack requires a buffer of 4 bytes"), KeyError("chunk"), MemoryError(),
                       RuntimeError("boom")):
        assert error_title(unexpected) == "Não foi possível limpar", unexpected
        assert error_explanation(unexpected) == "O Siftool encontrou um problema com este arquivo.", unexpected


def test_result_title_and_explanation(tmp_path):
    verified = CleanResult(
        src="a.jpg",
        dst=str(tmp_path / "a_clean.jpg"),
        mode=Mode.PRIVACY,
        removed=[],
        kept=[],
        verdict=Verdict(Status.VERIFIED, [], []),
    )
    assert result_title(verified) == "Verificado"
    assert (
        result_explanation(verified, tmp_path)
        == f"Cópia salva em {tmp_path}. Nada do que o modo proíbe ficou no arquivo."
    )

    warned = CleanResult(
        src="a.jpg",
        dst=str(tmp_path / "a_clean.jpg"),
        mode=Mode.PRIVACY,
        removed=[],
        kept=[],
        verdict=Verdict(Status.WARNINGS, [], ["Unrecognized structure kept: iCCP"]),
    )
    assert result_title(warned) == "Com ressalvas"
    # Portuguese only: the engine's note ("Unrecognized structure kept: iCCP") is listed under
    # "Detalhes técnicos" instead (spec 3, criterion 4).
    assert result_explanation(warned, tmp_path) == (
        "A cópia foi salva, mas havia uma estrutura que o Siftool não conhece "
        "e precisou ficar para a imagem não quebrar."
    )


def test_summary_and_button_plurals():
    assert summary_text(12, 9, 1) == "12 fotos · 9 com informações escondidas · 1 ainda não suportada"
    assert summary_text(1, 0, 0) == "1 foto"
    assert summary_text(2, 1, 0) == "2 fotos · 1 com informação escondida"
    assert summary_text(0, 0, 0) == ""

    assert clean_button_text(0) == "Limpar"
    assert clean_button_text(1) == "Limpar 1 foto"
    assert clean_button_text(3) == "Limpar 3 fotos"


def test_done_text_combinations():
    assert done_text(3, 0, 0) == "3 fotos limpas e verificadas"
    assert done_text(1, 0, 0) == "1 foto limpa e verificada"
    assert done_text(3, 1, 1) == "3 fotos limpas e verificadas · 1 com ressalvas · 1 não limpou"
    assert done_text(0, 2, 0) == "0 fotos limpas e verificadas · 2 com ressalvas"


def test_batch_message_after_a_batch():
    # Ruling P13: one message per batch -- the counts, plus where to look when a photo was not cleaned.
    assert batch_message(3, 0, 0) == "3 fotos limpas e verificadas"
    assert batch_message(2, 1, 0) == "2 fotos limpas e verificadas · 1 com ressalvas"
    assert batch_message(2, 1, 1) == (
        "2 fotos limpas e verificadas · 1 com ressalvas · 1 não limpou — clique na linha para ver o motivo."
    )
    assert batch_message(0, 0, 3) == done_text(0, 0, 3) + " — clique em cada linha para ver o motivo."


def test_presentation_imports_no_qt(tmp_path):
    code = (
        "import siftool_gui.presentation, sys; "
        "print(sorted(m for m in sys.modules if m.split('.')[0] in ('PySide6', 'qfluentwidgets')))"
    )
    result = subprocess.run(
        [sys.executable, "-P", "-c", code],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"
