# Siftool v2 — Interface nova — Plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entregar o programa novo do Siftool: uma janela Fluent em português, sobre o motor M1, em que a pessoa solta fotos, vê o que cada uma esconde e limpa todas com um clique, com o selo só quando verificado.

**Architecture:**
- **Pacotes:** um pacote novo `siftool_gui`, separado do motor `siftool`, que continua sem dependências e nunca importa Qt.
- **Lógica sem Qt:** textos e estados ficam em módulos testáveis sem janela (`presentation`, `entries`).
- **Trabalho pesado:** roda num `QThreadPool` limitado (`workers`). Uma árvore Qt (`model`) espelha os arquivos e é desenhada no estilo Fluent por um delegate próprio.
- **Janela:** uma `FluentWindow` com as páginas Limpar, Configurações e Sobre.

**Tech Stack:** Python ≥ 3.11 (o `.venv` usa 3.14.7), PySide6 6.11, PySide6-Fluent-Widgets 1.11 (Community), pytest + pytest-qt.

**Spec:** `docs/superpowers/specs/2026-09-30-interface-v2-design.md`. Leia a spec junto com este plano: as tabelas de texto das §5.3 a 5.5 valem literalmente.

## Global Constraints

- O motor `siftool` continua com `dependencies = []` e nunca importa `PySide6`, `qfluentwidgets` nem `siftool_gui`. Um teste fixa isso.
- Extras do `pyproject.toml`:
  - `gui = ["PySide6>=6.10", "PySide6-Fluent-Widgets>=1.11"]`;
  - `test` ganha `"pytest-qt>=4.4"`.
  - Versões conferidas em 2026-09-30: PySide6 6.11.2, PySide6-Fluent-Widgets 1.11.3, pytest-qt 4.5.0.
- Só componentes da edição Community do QFluentWidgets, nada marcado como Pro. Antes de usar uma API, confira a assinatura na versão instalada.
- Idiomas:
  - textos da interface em português, literalmente como na spec §5 quando ela fixa o texto;
  - nomes no código, logs e mensagens de commit em inglês.
- A interface nunca grava imagens: só `siftool.clean` grava a cópia. O único outro arquivo gravado é o JSON de preferências.
- Nenhum acesso à rede em `siftool_gui`. Miniaturas e relatórios ficam só em memória; nada vai para cache em disco e não há lista de recentes.
- O modelo só é alterado na thread da interface. As tarefas em segundo plano falam por sinais. No máximo `min(4, os.cpu_count() or 1)` tarefas ao mesmo tempo.
- O `siftool.py` antigo, na raiz, não é modificado.
- Testes:
  - rodam com `.venv/Scripts/python.exe -m pytest ...` na raiz do repositório;
  - os que usam Qt ficam em `tests/gui/`, rodam fora da tela (`QT_QPA_PLATFORM=offscreen`) e são pulados sem PySide6;
  - os de lógica sem Qt ficam em `tests/test_gui_*.py`;
  - a saída tem de ser limpa, também com `-W error`;
  - os testes nunca gravam em `%APPDATA%`: todo teste passa um caminho temporário de configuração.
- Nunca rode `python -m siftool` ou `python -m siftool_gui` com a raiz do repositório como pasta atual sem `-P`, porque o `siftool.py` antigo faz sombra ao pacote.
- Git:
  - comando: `git -c safe.directory='*' -c user.name='Felipe Floriani' -c user.email='felipeffloriani@gmail.com' commit ...`;
  - trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`;
  - branch `interface-v2`;
  - adicione caminhos explícitos e nunca `scratch/`, `siftool.7z` ou `tests/samples-local/`.
- Capturas de tela para revisão:
  - use a skill `qt-testing` (`C:\Users\Floriani\.claude\skills\qt-testing\scripts\qt_capture.py`);
  - salve em `scratch/.qt-screenshots/`, pasta ignorada e nunca commitada;
  - cite os caminhos no relatório da tarefa.

## Review Focus

1. **Nomes reais com acento, espaço e vírgula** (ex.: `26 de set. de 2026, 03_30_37.png`) em todo o caminho: soltar, exibir, miniatura, limpar e "Mostrar no Explorer". O `/select,` do Explorer quebra com vírgula sem aspas. Testes: Task 10 `test_drop_accented_names`, Task 11 `test_explorer_command_quotes_commas`.
2. **A mesma foto solta duas vezes, uma pasta solta junto com um arquivo dela, ou uma pasta com saídas `_clean` antigas:** nada de linha duplicada e nunca limpar uma saída. Testes: Task 8 `test_add_paths_dedupes_normalized`, Task 10 `test_drop_folder_skips_previous_outputs`.
3. **Arquivo movido, apagado ou travado entre a análise e a limpeza:** a linha mostra o erro em português e o resto do lote continua. Teste: Task 11 `test_file_removed_before_clean_shows_error_and_batch_continues`.
4. **Fechar a janela no meio da limpeza:** o que está na fila é descartado, o que já rodava termina, não sobra arquivo temporário e o programa fecha sem travar. Teste: Task 11 `test_close_while_cleaning_leaves_no_partial_files`.
5. **Pasta de saída salva que sumiu ou não aceita gravação:** a limpeza não começa (nada de erro "não encontrado" arquivo por arquivo, que seria enganoso) e um aviso diz que a pasta não está disponível. Teste: Task 11 `test_missing_output_folder_blocks_cleaning_with_message`.

---

### Task 1: Endurecer o motor (pendências da re-revisão do M1)

**Files:**
- Modify: `src/siftool/metadata/icc.py` (`icc_profile_valid`), `src/siftool/metadata/exif.py` (`_collect_allowed`), `src/siftool/verify.py` (docstring do módulo), `docs/superpowers/specs/2026-09-26-motor-v2-design.md` (§8 "Limites")
- Test: `tests/test_metadata_readers.py`, `tests/test_exif.py`, `tests/test_adversarial.py`

**Interfaces:**
- Consumes: `icc_profile_valid(profile: bytes) -> bool`; `build_minimal_exif(tiff: bytes, allowed: frozenset[Category]) -> bytes | None`; as tabelas `_IFD0_TAGS` / `_EXIF_IFD_TAGS`; os construtores de fixture já usados nesses testes.
- Produces: as mesmas assinaturas, com regras mais estritas.

- [ ] **Step 1: Escrever os testes que falham**

Em `tests/test_metadata_readers.py`:
- `test_icc_tag_count_over_cap_is_invalid`: um perfil com contagem 1001 e tamanho coerente → `icc_profile_valid(p) is False`, sem montar a lista de faixas. Afirme que a chamada leva menos de 50 ms num perfil que declara 1.000.000 de tags.
- `test_icc_tag_data_must_follow_tag_table`: uma tag `b"priv"` com offset 0 (dentro do cabeçalho) → `False`; a mesma tag com offset igual ao fim da tabela, cobrindo o resto → `True`.
- `test_icc_tag_smaller_than_8_bytes_is_invalid`: uma entrada com tamanho 4 → `False`.
- `test_real_icc_profiles_stay_valid`:
  - os perfis dos fixtures (`make_icc`) → `True`;
  - `C:\Windows\System32\spool\drivers\color\sRGB Color Space Profile.icm` → `True` (pular se o arquivo não existir).

Em `tests/test_exif.py`:
- `test_writer_keeps_tags_only_in_their_home_ifd`: um TIFF com `XResolution` (tag do IFD0) só dentro do ExifIFD e `ColorSpace` (tag do ExifIFD) só no IFD0. No PRIVACY, `parse_exif(build_minimal_exif(tiff, ALLOWED[Mode.PRIVACY]))` não traz nem `XResolution` nem `ColorSpace`.
- `test_same_tag_in_ifd0_and_exififd_is_written_once`: `XResolution` com valor 72/1 no IFD0 e 300/1 no ExifIFD. A saída tem exatamente uma `XResolution`, com 72/1.

Em `tests/test_adversarial.py`:
- `test_cross_ifd_duplicate_tag_does_not_survive_privacy_clean`, parametrizado em JPEG / PNG / WEBP. Seis RATIONAL `XResolution` no ExifIFD com os bytes de `b"owner=Felipe Floriani; "`. Em `siftool.clean(..., Mode.PRIVACY)`, o status é `VERIFIED` e o segredo não aparece na saída.

- [ ] **Step 2: Rodar e ver falhar**

Run: `.venv/Scripts/python.exe -m pytest tests/test_metadata_readers.py tests/test_exif.py tests/test_adversarial.py -v`
Expected: os testes novos FALHAM, exceto `test_real_icc_profiles_stay_valid`, que é guarda e passa dos dois lados.

- [ ] **Step 3: Implementar**
- **`icc_profile_valid`:**
  - `count > _MAX_TAGS` devolve `False` antes de ler qualquer entrada;
  - cada entrada precisa de `size >= 8` e de `offset >= table_end`.
- **`_collect_allowed`:** mantém uma tag só quando ela pertence à tabela do próprio IFD (`IFD0` → `_IFD0_TAGS`; `ExifIFD` → `_EXIF_IFD_TAGS`). O `_resolve` e o `parse_exif` continuam como estão: a leitura ainda reporta tags fora do lugar.
- **Docstring do `verify.py` e §8 "Limites" da spec:** reescreva as frases que prometem demais.
  - EXIF: "cada tag permitida no máximo uma vez por bloco EXIF, no seu IFD de origem".
  - ICC: "todo byte depois da tabela pertence aos dados de alguma tag; o conteúdo das tags, inclusive as privadas, não é inspecionado; um perfil válido pode carregar dados arbitrários dentro de uma tag (questão aberta do M2)".

- [ ] **Step 4: Rodar e ver passar**

Run: `.venv/Scripts/python.exe -m pytest -q -W error`
Expected: todos passam (536 anteriores + os novos).

- [ ] **Step 5: Commit**

```bash
git -c safe.directory='*' add src/siftool/metadata/icc.py src/siftool/metadata/exif.py src/siftool/verify.py docs/superpowers/specs/2026-09-26-motor-v2-design.md tests/test_metadata_readers.py tests/test_exif.py tests/test_adversarial.py
git -c safe.directory='*' -c user.name='Felipe Floriani' -c user.email='felipeffloriani@gmail.com' commit -m "fix(engine): cap ICC tag tables and keep EXIF tags in their home IFD" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `siftool.batch` — nomes de saída e varredura de pastas compartilhados

**Files:**
- Create: `src/siftool/batch.py`
- Modify: `src/siftool/cli.py`. Passa a importar do `batch`, e os nomes `iter_inputs`, `output_path_for` e `SPEC_WALK_EXTENSIONS` continuam acessíveis por `siftool.cli`.
- Test: `tests/test_batch.py`

**Interfaces:**
- Consumes: `siftool.detect.supported_extensions()`.
- Produces (usado pelas Tasks 7, 8, 10 e 11):
  - `SPEC_WALK_EXTENSIONS: frozenset[str]`
  - `is_previous_output(path: Path) -> bool`: o stem termina em `_clean` ou casa com `_clean \(\d+\)$`.
  - `iter_inputs(paths: Iterable[str | Path]) -> list[Path]`: a mesma semântica de hoje em `cli.iter_inputs`.
  - `path_key(path: str | Path) -> str`: `os.path.normcase(os.path.abspath(path))`.
  - `output_path_for(src: Path, out_dir: Path | None, overwrite: bool, taken: Collection[str] = frozenset()) -> Path`: a semântica de hoje, com `taken` guardando valores de `path_key`.

- [ ] **Step 1: Escrever os testes** em `tests/test_batch.py`
- `test_iter_inputs_walks_spec_extensions_case_insensitively`: uma pasta com `a.JPG`, `b.heic`, `c.mp4`, `d.txt` → `[a.JPG, b.heic, c.mp4]`, ordenado.
- `test_iter_inputs_skips_previous_outputs_in_folders_only`: numa pasta, `x_clean.jpg` e `x_clean (2).jpg` são pulados; passados explicitamente, entram.
- `test_output_path_for_numbers_existing_and_taken`: com `a_clean.jpg` existindo → `a_clean (2).jpg`; com `path_key(a_clean (2).jpg)` em `taken` e `overwrite=True` → `a_clean (3).jpg`.
- `test_output_path_for_uses_out_dir`.
- `test_path_key_folds_case_on_windows`: pular fora do Windows.

- [ ] **Step 2: Rodar e ver falhar** — `.venv/Scripts/python.exe -m pytest tests/test_batch.py -v` → FAIL (`ModuleNotFoundError: siftool.batch`)

- [ ] **Step 3: Mover as funções** de `cli.py` para `batch.py`, sem mudar o comportamento. O `cli.py` importa de `batch`. `_path_key` vira `path_key` público, e o `cli` mantém um alias interno se precisar.

- [ ] **Step 4: Rodar** — `.venv/Scripts/python.exe -m pytest tests/test_batch.py tests/test_cli.py -v` → PASS. A suíte inteira continua passando.

- [ ] **Step 5: Commit** — `refactor(engine): move output naming and folder walking to siftool.batch`

---

### Task 3: Pacote `siftool_gui`, dependências e a janela vazia

**Files:**
- Modify: `pyproject.toml`
  - `[project.optional-dependencies]`: `gui` novo, `test` + `pytest-qt>=4.4`;
  - `[project.gui-scripts] siftool-gui = "siftool_gui.app:main"`;
  - `[tool.pytest.ini_options] qt_api = "pyside6"`.
- Create:
  - `src/siftool_gui/__init__.py` (só `__version__ = siftool.__version__`, sem Qt);
  - `src/siftool_gui/__main__.py` (`raise SystemExit(main())`);
  - `src/siftool_gui/app.py`, `src/siftool_gui/window.py`;
  - `src/siftool_gui/pages/__init__.py`;
  - `src/siftool_gui/pages/clean_page.py`, `settings_page.py`, `about_page.py` (páginas provisórias: um `QWidget` com o `objectName` final);
  - `tests/gui/conftest.py`, `tests/gui/test_window.py`, `tests/test_engine_isolation.py`.

**Interfaces:**
- Produces:
  - `siftool_gui.app.main(argv: list[str] | None = None) -> int`: com `--version`, imprime `siftool-gui <versão>` e sai com 0 sem criar a janela. Sem argumentos, cria o `QApplication`, a janela e roda o laço.
  - `siftool_gui.app.create_window(config_path: Path | None = None) -> MainWindow`: testável sem o laço de eventos. A partir da Task 6, carrega a configuração desse caminho.
  - `MainWindow(FluentWindow)`:
    - `windowTitle() == "Siftool"` e `minimumSize() == QSize(720, 560)`;
    - atributos `clean_page`, `settings_page`, `about_page`;
    - `addSubInterface(clean_page, FluentIcon.BROOM, "Limpar")`;
    - `addSubInterface(settings_page, FluentIcon.SETTING, "Configurações", NavigationItemPosition.BOTTOM)`;
    - `addSubInterface(about_page, FluentIcon.INFO, "Sobre", NavigationItemPosition.BOTTOM)`.
- `tests/gui/conftest.py`: começa com `os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")` e `pytest.importorskip("PySide6")`.

- [ ] **Step 1: Instalar** — `.venv/Scripts/python.exe -m pip install -e ".[test,legacy,gui]"` depois de editar o `pyproject.toml`. Confira as versões com `pip show`.
- [ ] **Step 2: Escrever os testes**
  - `test_main_window_has_title_min_size_and_three_pages(qtbot, tmp_path)`: `create_window(tmp_path / "cfg.json")` → título, tamanho mínimo e `{p.objectName() for p in (w.clean_page, w.settings_page, w.about_page)} == {"cleanPage", "settingsPage", "aboutPage"}`.
  - `test_gui_version_entry_point(tmp_path)`: `subprocess.run([sys.executable, "-P", "-m", "siftool_gui", "--version"], cwd=tmp_path)` → código 0 e `siftool.__version__` na saída.
  - `test_import_siftool_loads_no_qt(tmp_path)` (em `tests/test_engine_isolation.py`): um subprocesso que roda `import siftool, siftool.cli, siftool.batch, sys; print(sorted(m for m in sys.modules if m.split('.')[0] in ('PySide6', 'qfluentwidgets', 'siftool_gui')))` → imprime `[]`.
- [ ] **Step 3: Rodar e ver falhar** (`.venv/Scripts/python.exe -m pytest tests/gui/test_window.py tests/test_engine_isolation.py -v`)
- [ ] **Step 4: Implementar** `app.py`, `window.py` e as páginas provisórias.
- [ ] **Step 5: Rodar e ver passar**, e tirar uma captura da janela vazia.
- [ ] **Step 6: Commit** — `feat(gui): siftool_gui package, dependencies and main window shell`

---

### Task 4: `presentation.py` — todo o texto em português

**Files:** Create `src/siftool_gui/presentation.py` (sem Qt) · Test `tests/test_gui_presentation.py`

**Interfaces:**
- Consumes: `siftool.model.{Category, Mode, Finding, CleanResult, Status}`, `siftool.modes.ALLOWED`, `siftool.errors.*`.
- Produces:

```python
class Outcome(str, Enum): REMOVED = "sai"; KEPT = "fica"
CATEGORY_ORDER: tuple[Category, ...]  # LOCATION, IDENTITY, AI_PROVENANCE, TIME, DEVICE, CAMERA_SETTINGS, SOFTWARE, TEXT, EXTRAS, UNKNOWN, APPEARANCE
def category_name(category: Category) -> str
def category_value(category: Category, findings: Sequence[Finding]) -> str
def category_outcome(category: Category, mode: Mode) -> Outcome
def mode_title(mode: Mode) -> str            # "Privacidade" | "Fotógrafo" | "Máximo"
def mode_description(mode: Mode) -> str      # spec §5.3, literal
def error_title(exc: BaseException) -> str        # spec §5.5, column "Texto na linha"
def error_explanation(exc: BaseException) -> str  # spec §5.5, column "Texto ao expandir"
def result_title(result: CleanResult) -> str      # "Verificado" | "Com ressalvas"
def result_explanation(result: CleanResult, output_dir: Path) -> str
def clean_button_text(n: int) -> str   # 0 → "Limpar", 1 → "Limpar 1 foto", 3 → "Limpar 3 fotos"
def summary_text(total: int, with_findings: int, unsupported: int) -> str
def done_text(verified: int, warnings: int, failed: int) -> str
```

Regras que os testes fixam:
- **Nomes das categorias:** os da spec §5.4.
- **`category_outcome`:** devolve `KEPT` sse `category in ALLOWED[mode]`.
- **`category_value`:** segue a spec §5.4, com estas regras:
  - `ai_provenance`: se algum `detail` contém `generator: `, o resultado é `"Gerada por IA (" + <texto após "generator: " até " · " ou o fim> + ")"`; senão, `"Gerada por IA"`.
  - `device`: junta com ` · ` os `detail` distintos dos achados com label `Make`, `Model`, `LensMake`, `LensModel`, nessa ordem.
  - `time`: o primeiro `detail` no formato `AAAA:MM:DD HH:MM` vira `DD/MM/AAAA HH:MM`.
  - `identity` e `software`: o primeiro `detail` não vazio.
  - Sem dado utilizável, vale a frase genérica da tabela.
  - O valor é cortado em 60 caracteres, terminando com `…`.
- **`UnsupportedFormatError`:**
  - com `format_name` em `{"HEIC", "HEIF", "AVIF", "MP4/MOV"}` → "Ainda não suportado" / "Fotos HEIC e vídeos chegam numa próxima versão.";
  - nos demais → "Formato não suportado" / "O Siftool limpa fotos JPG, PNG e WEBP.".
- **`VerificationFailedError`:** "Não limpou", com a frase da spec.
- **`summary_text(12, 9, 1)`:** `"12 fotos · 9 com informações escondidas · 1 ainda não suportada"`. Os trechos com zero são omitidos, e o singular vira `"1 foto"`.
- **`done_text(3, 0, 0)`:** `"3 fotos limpas e verificadas"`. Com ressalvas e falhas, acrescenta `" · 1 com ressalvas"` e `" · 1 não limpou"`.

- [ ] **Step 1: Escrever os testes**, em forma de tabela, um por regra acima:
  - `test_category_names_cover_every_category`
  - `test_outcomes_follow_allowed`
  - `test_mode_descriptions_are_spec_verbatim`
  - `test_ai_value_names_generator`, usando o detalhe real `"generator: OpenAI Media Service API · AI-generated"` → `"Gerada por IA (OpenAI Media Service API)"`
  - `test_device_value_joins_make_model`
  - `test_time_value_is_brazilian_format`
  - `test_value_truncates_at_60`
  - `test_error_texts_for_each_exception`, com todas as linhas da tabela §5.5, incluindo `FileNotFoundError`, `PermissionError` e um `OSError` genérico
  - `test_summary_and_button_plurals`
  - `test_done_text_combinations`
  - `test_presentation_imports_no_qt`, pelo mesmo método de subprocesso da Task 3
- [ ] **Step 2: Rodar e ver falhar** — `.venv/Scripts/python.exe -m pytest tests/test_gui_presentation.py -v`
- [ ] **Step 3: Implementar** `presentation.py`
- [ ] **Step 4: Rodar e ver passar**
- [ ] **Step 5: Commit** — `feat(gui): Portuguese presentation layer for findings, modes and outcomes`

---

### Task 5: `entries.py` — o estado de cada arquivo

**Files:** Create `src/siftool_gui/entries.py` (sem Qt) · Test `tests/test_gui_entries.py`

**Interfaces:**
- Consumes: `Report`, `CleanResult`, `Status`, `UnsupportedFormatError`, `presentation.CATEGORY_ORDER`.
- Produces:

```python
class EntryState(Enum): QUEUED, ANALYZING, ANALYZED, UNSUPPORTED, ERROR, CLEANING, VERIFIED, WARNINGS, FAILED
class InvalidTransition(ValueError): ...
@dataclass(eq=False)
class FileEntry:
    path: Path
    state: EntryState = EntryState.QUEUED
    report: Report | None = None
    result: CleanResult | None = None
    error: BaseException | None = None
    def start_analysis(self) -> None                 # QUEUED → ANALYZING
    def finish_analysis(self, report: Report) -> None # ANALYZING → ANALYZED
    def fail_analysis(self, exc: BaseException) -> None  # ANALYZING → UNSUPPORTED (UnsupportedFormatError) | ERROR
    def cancel_analysis(self) -> None                # ANALYZING → QUEUED
    def start_clean(self) -> None     # ANALYZED | VERIFIED | WARNINGS | FAILED → CLEANING; clears result/error
    def finish_clean(self, result: CleanResult) -> None  # CLEANING → VERIFIED | WARNINGS (by result.verdict.status)
    def fail_clean(self, exc: BaseException) -> None     # CLEANING → FAILED
    def cancel_clean(self) -> None    # CLEANING → ANALYZED
    @property
    def categories(self) -> list[Category]  # distinct categories of report.findings in CATEGORY_ORDER; [] without a report
    @property
    def cleanable(self) -> bool  # state is ANALYZED
    @property
    def busy(self) -> bool       # ANALYZING or CLEANING
```

- [ ] **Step 1: Testes**
  - `test_happy_path_transitions`
  - `test_unsupported_vs_error`
  - `test_clean_again_from_finished_states`
  - `test_invalid_transitions_raise`, parametrizado em cada par proibido, por exemplo QUEUED→`start_clean` e ERROR→`start_clean`
  - `test_categories_distinct_in_display_order`
  - `test_cancel_reverts`
- [ ] **Step 2: Falhar** · **Step 3: Implementar** · **Step 4: Passar**
- [ ] **Step 5: Commit** — `feat(gui): file entry state machine`

---

### Task 6: `settings.py` — preferências

**Files:** Create `src/siftool_gui/settings.py` · Modify `src/siftool_gui/app.py` (`create_window` carrega a configuração) · Test `tests/gui/test_settings.py`

**Interfaces:**
- Consumes: `qfluentwidgets.{QConfig, ConfigItem, OptionsConfigItem, OptionsValidator, EnumSerializer, Theme, qconfig}`.
- Produces:

```python
class SiftoolConfig(QConfig):
    defaultMode = OptionsConfigItem("Cleaning", "DefaultMode", "privacy", OptionsValidator(["privacy", "photographer", "maximum"]))
    saveLocation = OptionsConfigItem("Cleaning", "SaveLocation", "beside", OptionsValidator(["beside", "folder"]))
    outputFolder = ConfigItem("Cleaning", "OutputFolder", "")
    windowGeometry = ConfigItem("Window", "Geometry", "")  # base64 of QWidget.saveGeometry()
    themeMode = OptionsConfigItem("QFluentWidgets", "ThemeMode", Theme.AUTO, OptionsValidator(Theme), EnumSerializer(Theme))
def default_config_path() -> Path   # Path(os.environ.get("APPDATA") or Path.home()) / "Siftool" / "config.json"
def load_config(path: Path | None = None) -> SiftoolConfig   # qconfig.load(str(path or default_config_path()), cfg)
def default_mode(cfg: SiftoolConfig) -> Mode
def output_dir(cfg: SiftoolConfig) -> Path | None   # None when "beside" or the folder string is empty
```

- [ ] **Step 1: Testes** (sempre com `tmp_path`)
  - `test_defaults_without_file`: modo `PRIVACY`, `output_dir is None` e `Theme.AUTO`.
  - `test_values_persist_across_loads`: `cfg.set(cfg.defaultMode, "maximum")` → o arquivo é gravado → um `load_config` novo lê `MAXIMUM`.
  - `test_invalid_values_fall_back_to_defaults`: um JSON com `"DefaultMode": "xyz"` → `PRIVACY`.
  - `test_output_dir_rules`.
- [ ] **Step 2-4:** falhar, implementar, passar. A versão instalada é a 1.11.3; confira nela como o `qconfig.load` age sobre o singleton global, e documente no docstring de `load_config`.
- [ ] **Step 5: Commit** — `feat(gui): persistent settings (mode, save location, theme, window geometry)`

---

### Task 7: `workers.py` — trabalho em segundo plano

**Files:** Create `src/siftool_gui/workers.py` · Test `tests/gui/test_workers.py`

**Interfaces:**
- Consumes: `siftool.inspect`, `siftool.clean`, `siftool.batch.iter_inputs`, `QThreadPool`, `QRunnable`, `QImageReader`.
- Produces:

```python
class JobRunner(QObject):
    pathsFound = Signal(list)          # list[str], batches of at most 200
    scanFinished = Signal()
    analyzed = Signal(int, object)     # entry_id, Report
    analysisFailed = Signal(int, object)   # entry_id, exception
    thumbnailReady = Signal(int, QImage)   # a null QImage when decoding fails
    cleaned = Signal(int, object)      # entry_id, CleanResult
    cleanFailed = Signal(int, object)  # entry_id, exception
    jobDropped = Signal(int, str)      # entry_id, "analyze" | "clean"  (cancelled before starting)
    idle = Signal()                    # nothing pending or running
    def __init__(self, parent: QObject | None = None, max_workers: int | None = None) -> None
    def scan(self, paths: list[str]) -> None
    def analyze(self, entry_id: int, path: Path) -> None
    def thumbnail(self, entry_id: int, path: Path, size: int = 96) -> None   # lower priority
    def clean(self, entry_id: int, src: Path, dst: Path, mode: Mode) -> None
    def cancel(self) -> None           # drops pending analyze/clean/thumbnail jobs, emits jobDropped for each
    def busy(self) -> bool
    def wait_for_done(self, msecs: int = -1) -> bool
```

Cada tarefa pega toda exceção do motor (`SiftoolError`, `OSError`, `MemoryError`) e emite o sinal de falha. Nenhuma exceção pode escapar de `run()`.

- [ ] **Step 1: Testes** (`qtbot.waitSignal` e fixtures de `fixtures.make_fixtures`)
  - `test_scan_reports_supported_files_and_skips_outputs`: uma pasta com `a.jpg`, `b.png`, `a_clean.jpg`, `note.txt` → `pathsFound` recebe só `a.jpg` e `b.png`, depois vem `scanFinished`.
  - `test_analyze_success_unsupported_and_corrupt`:
    - `make_png()` → `analyzed`, com `report.format == "png"`;
    - bytes de HEIC (`b"\x00\x00\x00\x18ftypheic" + ...`) → `analysisFailed` com `UnsupportedFormatError`;
    - lixo → `CorruptFileError`.
  - `test_thumbnail_scaled_or_null`: a miniatura tem no máximo 96 px e não é nula; um arquivo corrompido dá imagem nula, sem exceção.
  - `test_clean_writes_verified_copy`.
  - `test_clean_into_missing_folder_fails_with_oserror`.
  - `test_concurrency_is_bounded`: com `monkeypatch` em `siftool.inspect`, uma versão lenta que conta chamadas simultâneas; 12 tarefas, `max_workers=2` → o máximo observado é ≤ 2.
  - `test_cancel_drops_pending_and_reports_them`: `max_workers=1`, inspect lento e 5 tarefas → `cancel()` → 1 `analyzed`, 4 `jobDropped`, depois `idle`.
  - `test_signals_arrive_on_gui_thread`: o slot registra `QThread.currentThread() is QApplication.instance().thread()`.
- [ ] **Step 2-4:** falhar, implementar, passar.
- [ ] **Step 5: Commit** — `feat(gui): bounded background job runner`

---

### Task 8: `model.py` — a árvore da lista

**Files:** Create `src/siftool_gui/model.py` · Test `tests/gui/test_model.py`

**Interfaces:**
- Consumes: `entries.FileEntry`, `presentation.*`, `siftool.batch.path_key`.
- Produces:

```python
class RowKind(IntEnum): FILE = 1; CATEGORY = 2; NOTE = 3; TECH_HEADER = 4; TECH = 5
KIND_ROLE, ENTRY_ID_ROLE, CATEGORY_ROLE, OUTCOME_ROLE, VALUE_ROLE, FINDING_ROLE, THUMBNAIL_ROLE, STATE_ROLE  # Qt.UserRole + 1..8
class Summary(NamedTuple):
    total: int; with_findings: int; unsupported: int; errors: int; cleanable: int
    verified: int; warnings: int; failed: int; busy: int
class FileListModel(QStandardItemModel):
    summaryChanged = Signal(object)
    def __init__(self, mode: Mode = Mode.PRIVACY, parent: QObject | None = None) -> None
    def add_paths(self, paths: Iterable[Path]) -> list[int]   # ids of new entries AND of existing QUEUED entries (to re-dispatch)
    def entry(self, entry_id: int) -> FileEntry
    def entry_ids(self) -> list[int]
    def index_for(self, entry_id: int) -> QModelIndex
    def entry_id_at(self, index: QModelIndex) -> int | None   # any row kind resolves to its file
    def mode(self) -> Mode
    def set_mode(self, mode: Mode) -> None
    def mark_analyzing(self, entry_id: int) -> None
    def apply_report(self, entry_id: int, report: Report) -> None
    def apply_analysis_error(self, entry_id: int, exc: BaseException) -> None
    def mark_cleaning(self, entry_id: int) -> None
    def apply_result(self, entry_id: int, result: CleanResult) -> None
    def apply_clean_error(self, entry_id: int, exc: BaseException) -> None
    def revert_dropped(self, entry_id: int, job: str) -> None
    def set_thumbnail(self, entry_id: int, image: QImage) -> None   # stores QPixmap.fromImage (GUI thread)
    def remove_entry(self, entry_id: int) -> None
    def clear_entries(self) -> None
    def cleanable_ids(self) -> list[int]
    def summary(self) -> Summary
```

Linhas-filhas, reconstruídas sempre que o estado da entrada muda:
- **Com relatório e achados:**
  - uma linha `CATEGORY` por categoria de `entry.categories` (texto `category_name`, `VALUE_ROLE = category_value`, `OUTCOME_ROLE` pelo modo);
  - depois, as linhas `NOTE` do resultado, quando houver: a explicação e depois cada nota ou violação;
  - por fim, `TECH_HEADER` "Detalhes técnicos", com um `TECH` por achado (texto `label`, `VALUE_ROLE = detail`, `FINDING_ROLE = finding`). Em `FAILED`, as violações também entram.
- **Com relatório e sem achados:** um `NOTE` "Nada escondido nesta foto.".
- **`UNSUPPORTED` ou `ERROR`:** um `NOTE` com `error_explanation`.
- **Papéis da linha `FILE`:**
  - `Qt.DisplayRole` = nome do arquivo;
  - `Qt.ToolTipRole` = caminho completo;
  - `Qt.AccessibleTextRole` = `f"{nome}, {estado}, {categorias}"`.

- [ ] **Step 1: Testes**
  - `test_add_paths_dedupes_normalized`: o mesmo arquivo com barras e maiúsculas diferentes, relativo e absoluto → uma linha.
  - `test_add_paths_returns_existing_queued_ids`.
  - `test_report_builds_category_rows_in_order_with_outcomes`: o relatório de `make_jpeg()` gera as categorias na ordem de `CATEGORY_ORDER`, com `appearance` → `KEPT` em PRIVACY.
  - `test_set_mode_updates_outcomes_and_emits_data_changed`.
  - `test_no_findings_note`.
  - `test_unsupported_and_error_notes`.
  - `test_result_rows_verified_warnings_failed`: com `CleanResult` e `VerificationFailedError(Verdict(...))` montados à mão.
  - `test_tech_rows_carry_findings`.
  - `test_summary_counts_and_signal`.
  - `test_remove_and_clear`.
  - `test_thumbnail_role_is_pixmap`.
  - `test_accessible_and_tooltip_roles`.
- [ ] **Step 2-4:** falhar, implementar, passar.
- [ ] **Step 5: Commit** — `feat(gui): tree model mirroring file entries`

---

### Task 9: `delegate.py` — o desenho das linhas

**Files:** Create `src/siftool_gui/delegate.py` · Test `tests/gui/test_delegate.py`

**Interfaces:**
- Consumes: os papéis e `RowKind` de `model.py`; `qfluentwidgets.{isDarkTheme, themeColor, FluentIcon}`.
- Produces: `FileItemDelegate(QStyledItemDelegate)` com `FILE_ROW_HEIGHT = 64`, `CHILD_ROW_HEIGHT = 32`, `sizeHint()` por `RowKind` e `paint()`.

Desenho (spec §5.2):
- **`FILE`:**
  - cartão arredondado com as cores de cartão do tema (claro e escuro);
  - realce de seleção e de passagem do mouse com `themeColor()` translúcido;
  - miniatura de 48 × 48 arredondada, ou `FluentIcon.PHOTO` quando não houver;
  - nome em negrito, com a pasta embaixo em texto secundário (os dois cortados com reticências);
  - até 3 etiquetas de categoria, mais `+N`;
  - à direita, o estado em texto **e** ícone: `ACCEPT` verde em Verificado, ícone de aviso amarelo em Com ressalvas, `CANCEL` vermelho em Não limpou/erro, cinza em Ainda não suportado, e "Analisando…"/"Limpando…" enquanto ocupado.
- **`CATEGORY`:** ✖ ou ✔ conforme o resultado, o nome, o valor em texto secundário e "sai"/"fica" alinhado à direita.
- **`NOTE`:** texto cortado com reticências; o texto inteiro vai em `ToolTipRole`.
- **`TECH`:** texto pequeno, `label — detail — where`.
- **Cores:** sempre por par claro/escuro. O estado nunca é indicado só por cor.

- [ ] **Step 1: Testes**
  - `test_size_hints_per_kind`.
  - `test_paint_every_kind_and_state_in_both_themes`: para cada `RowKind` e para cada `EntryState` nas linhas `FILE`, pinta num `QImage` de 600 × 64 com `setTheme(Theme.LIGHT)` e depois `Theme.DARK`. Não pode haver exceção, e a imagem não pode ser uniforme.
- [ ] **Step 2-4:** falhar, implementar, passar.
- [ ] **Step 5:** capturas de um `TreeView` com um modelo de exemplo em todos os estados, uma linha expandida, nos temas claro e escuro. Os caminhos vão no relatório.
- [ ] **Step 6: Commit** — `feat(gui): Fluent row delegate for files, categories and details`

---

### Task 10: Página "Limpar" — soltar, analisar e ver

**Files:** Create `src/siftool_gui/drop_area.py` · Modify `src/siftool_gui/pages/clean_page.py`, `src/siftool_gui/window.py` · Test `tests/gui/test_clean_page_analysis.py`

**Interfaces:**
- Consumes: Tasks 4 a 9 e `settings.default_mode`.
- Produces:
  - **`DropArea(QFrame)`:**
    - sinal `pathsDropped(list)`, com `list[str]` de arquivos e pastas locais;
    - `set_compact(compact: bool) -> None`;
    - destaque em `dragEnterEvent`;
    - só aceita URLs locais.
  - **`CleanPage(QWidget)`**, com `objectName "cleanPage"`:

```python
def __init__(self, config: SiftoolConfig, runner: JobRunner | None = None, parent: QWidget | None = None) -> None
model: FileListModel; view: TreeView; drop_area: DropArea
summary_label: BodyLabel; clear_button: TransparentPushButton
mode_selector: SegmentedWidget   # route keys "privacy" / "photographer" / "maximum"
mode_description: BodyLabel; clean_button: PrimaryPushButton
progress_bar: ProgressBar; cancel_button: PushButton
def add_paths(self, paths: list[str]) -> None   # folders → runner.scan; files → model.add_paths → analyze + thumbnail
def set_mode(self, mode: Mode) -> None
def remove_selected(self) -> None                # ignores busy entries
def clear_list(self) -> None                     # only when nothing is busy
```

Layout (spec §5.2), com largura máxima de 960 px, centralizado:
1. **Cabeçalho:** `TitleLabel` "Limpar fotos" e o `BodyLabel` com a frase literal da spec.
2. **`DropArea`,** com "Arraste fotos ou pastas aqui" e os botões:
   - **"Adicionar fotos":** `QFileDialog.getOpenFileNames` com o filtro `"Imagens (*.jpg *.jpeg *.png *.webp *.heic *.heif);;Todos os arquivos (*.*)"`;
   - **"Adicionar pasta":** `getExistingDirectory`.
3. **Linha de resumo:** `summary_text` e "Limpar lista". Fica oculta com a lista vazia. A `DropArea` fica compacta quando a lista não está vazia.
4. **`TreeView`,** com o modelo e o delegate: cabeçalho oculto, expansão animada.
5. **Barra de ação:** `mode_selector` (inicial = `default_mode(config)`), `mode_description` (`mode_description(mode)`) e, à direita, o `clean_button`. O `clean_button` só será ligado na Task 11; aqui ele existe, desativado.

A tecla Delete remove a seleção.

- [ ] **Step 1: Testes** (`qtbot`, configuração em `tmp_path`, `runner` real)
  - `test_drop_files_adds_rows_and_analyzes`: `page.drop_area.pathsDropped.emit([jpg, png])` → espera o resumo com total 2 e os dois em ANALYZED.
  - `test_real_drop_event_with_urls`: um `QDropEvent` com `QMimeData.setUrls` enviado a `drop_area.dropEvent`.
  - `test_drop_accented_names`: `"fotó 26 de set., ção.png"` → `DisplayRole` idêntico ao nome, e a linha fica ANALYZED.
  - `test_drop_folder_skips_previous_outputs`: `a.jpg` + `a_clean.jpg` → 1 linha.
  - `test_heic_row_shows_not_supported_yet`.
  - `test_mode_switch_updates_outcomes_and_description`.
  - `test_summary_text_and_compact_drop_area`.
  - `test_delete_key_removes_selected_row` e `test_clear_list`.
- [ ] **Step 2-4:** falhar, implementar, passar.
- [ ] **Step 5:** capturas da página vazia e da página com 3 fotos analisadas, uma expandida, nos temas claro e escuro.
- [ ] **Step 6: Commit** — `feat(gui): clean page drop, analysis and list`

---

### Task 11: Página "Limpar" — limpar, progresso, resultado e ações

**Files:** Create `src/siftool_gui/shell.py` · Modify `src/siftool_gui/pages/clean_page.py`, `src/siftool_gui/window.py` · Test `tests/gui/test_clean_page_cleaning.py`, `tests/test_gui_shell.py`

**Interfaces:**
- Consumes: `batch.output_path_for`, `batch.path_key`, `settings.output_dir`, `presentation.done_text` / `clean_button_text`.
- Produces:
  - **`shell.explorer_select_command(path: Path) -> str`:** `explorer /select,"<caminho absoluto>"`, com o caminho inteiro entre aspas.
  - **`shell.reveal_in_explorer(path: Path) -> None`:** no Windows, `subprocess.Popen(command_string)`; nos outros sistemas, `QDesktopServices.openUrl` na pasta pai (o Qt é importado dentro da função).
  - **`shell.open_folder(path: Path) -> None`:** `os.startfile` no Windows.
  - **`CleanPage.start_cleaning() -> None`:**
    - valida a pasta de saída (existe, é pasta, `os.access(W_OK)`). Se falhar, mostra `InfoBar.error` com "A pasta escolhida para salvar as cópias não está disponível. Escolha outra em Configurações." e não inicia;
    - para cada `cleanable_id`, calcula `dst = output_path_for(src, output_dir(cfg), False, self._taken)`, guarda o `path_key(dst)` em `_taken` (pelo resto da sessão), chama `mark_cleaning` e `runner.clean`.
  - **Outros membros da `CleanPage`:** `clean_again(entry_id)`, `cancel()`, `busy() -> bool` e o sinal `batchFinished(object)` (`(verified, warnings, failed)`).
  - **`MainWindow.closeEvent`:** se `clean_page.busy()`, chama `runner.cancel()` e `runner.wait_for_done(10000)`; depois grava `windowGeometry` e aceita o fechamento.

Comportamento:
- **Botão principal:** texto `clean_button_text(summary.cleanable)`. Fica desativado com 0 ou enquanto há limpeza em curso. Ctrl+Enter aciona.
- **Progresso:** `progress_bar` e `cancel_button` aparecem enquanto o runner está ocupado; o progresso é "concluídos / total do lote".
- **Cancelar:** as tarefas descartadas voltam ao estado anterior (`revert_dropped`).
- **Fim do lote:**
  - `InfoBar.success("Pronto", done_text(...))` com o botão "Abrir pasta": a pasta de saída configurada, ou a pasta do primeiro arquivo limpo;
  - `InfoBar.warning` quando houver falhas.
- **Menu da linha (`RoundMenu`):**
  - "Mostrar no Explorer": a cópia, se existir, senão o original;
  - "Limpar de novo": só em estados finais;
  - "Remover da lista": não disponível durante uma limpeza.

- [ ] **Step 1: Testes**
  - `test_clean_all_writes_verified_copies_and_keeps_originals`: `make_jpeg()`, `make_png()` e `make_webp("lossless")` → os três VERIFIED; os `_clean` existem ao lado; o SHA-256 dos originais não muda; a linha diz "Verificado".
  - `test_clean_button_text_and_enabled_state`: "Limpar 3 fotos" antes; desativado durante a limpeza e depois.
  - `test_output_folder_setting_is_used`.
  - `test_missing_output_folder_blocks_cleaning_with_message`.
  - `test_same_name_sources_get_distinct_outputs`: `trip1/IMG.jpg` e `trip2/IMG.jpg`, com pasta de saída → `IMG_clean.jpg` e `IMG_clean (2).jpg`.
  - `test_file_removed_before_clean_shows_error_and_batch_continues`: o original é apagado depois da análise → "Não encontrado"; os outros ficam VERIFIED.
  - `test_verification_failure_shows_violations`: marcador reservado com payload `b"has c2pa inside"`, o fixture de FAILED do M1 → "Não limpou", violação na linha `NOTE`, nenhuma cópia gravada.
  - `test_warnings_result`: marcador reservado inofensivo → "Com ressalvas".
  - `test_cancel_during_clean_reverts_pending`: `max_workers=1`, limpeza lenta via monkeypatch → `cancel()` → as pendentes voltam para ANALYZED e não fica nenhum `*.siftool-tmp-*`.
  - `test_close_while_cleaning_leaves_no_partial_files`.
  - `test_clean_again_uses_current_mode_and_new_name`.
  - `test_batch_finished_counts_and_infobar`.
  - Em `tests/test_gui_shell.py`, sem Qt: `test_explorer_command_quotes_commas`. `C:\a b, c\x.png` → `explorer /select,"C:\a b, c\x.png"`.
- [ ] **Step 2-4:** falhar, implementar, passar.
- [ ] **Step 5:** capturas depois de uma limpeza com linhas Verificado, Com ressalvas e Não limpou, nos temas claro e escuro.
- [ ] **Step 6: Commit** — `feat(gui): cleaning flow with progress, results and row actions`

---

### Task 12: Configurações e Sobre

**Files:** Modify `src/siftool_gui/pages/settings_page.py`, `src/siftool_gui/pages/about_page.py`, `src/siftool_gui/window.py`, `src/siftool_gui/app.py` · Test `tests/gui/test_settings_about.py`

**Interfaces:**
- **`SettingsPage(ScrollArea)`**, `objectName "settingsPage"`, `__init__(config, parent=None)`. Cartões:
  - `mode_card` (`OptionsSettingCard` sobre `defaultMode`, textos `["Privacidade", "Fotógrafo", "Máximo"]`);
  - `save_card` (sobre `saveLocation`, textos `["Ao lado do original", "Numa pasta que eu escolher"]`);
  - `folder_card` (`PushSettingCard` "Escolher pasta"; o conteúdo é a pasta atual ou "Nenhuma pasta escolhida"; só fica ativo quando `saveLocation == "folder"`);
  - `theme_card` (sobre `themeMode`, textos `["Claro", "Escuro", "Seguir o Windows"]`).
  - Grupos "Limpeza" e "Aparência", título `TitleLabel` "Configurações".
- **Tema:** trocar o tema chama `setTheme(...)` na hora. O `app.main` aplica o tema salvo antes de mostrar a janela.
- **Modo padrão:** a página Limpar lê o modo padrão quando é criada. Mudar a configuração não troca o modo de uma lista já aberta.
- **`AboutPage(ScrollArea)`**, `objectName "aboutPage"`:
  - título "Sobre o Siftool";
  - "Versão <siftool.__version__>";
  - as quatro promessas, uma por linha:
    - "Funciona 100% offline."
    - "Nunca altera o arquivo original."
    - "Nunca recomprime a imagem."
    - "Só dá o selo quando a verificação passa."
  - "Licença: GPLv3";
  - "Feito com PySide6/Qt (LGPL) e QFluentWidgets (GPLv3).";
  - "Fotos HEIC e vídeos chegam numa próxima versão.".
- **Geometria da janela:** a `MainWindow` restaura `windowGeometry` na criação.

- [ ] **Step 1: Testes**
  - `test_default_mode_card_updates_config_and_next_clean_page`.
  - `test_save_location_toggles_folder_card`.
  - `test_folder_card_sets_output_folder`: `monkeypatch` em `QFileDialog.getExistingDirectory`.
  - `test_theme_card_applies_theme`: Escuro → `isDarkTheme()`; Claro → não.
  - `test_about_page_texts`.
  - `test_window_geometry_round_trip`: fechar grava a geometria, e uma janela nova restaura o tamanho.
- [ ] **Step 2-4:** falhar, implementar, passar.
- [ ] **Step 5:** capturas de Configurações e Sobre, nos temas claro e escuro.
- [ ] **Step 6: Commit** — `feat(gui): settings and about pages`

---

### Task 13: Escala, teclado e acessibilidade

**Files:** Modify o que os testes exigirem em `clean_page.py`, `delegate.py` e `window.py` · Test `tests/gui/test_scale_accessibility.py`

- [ ] **Step 1: Testes**
  - `test_thousand_files_keep_ui_responsive`:
    - gera 1.000 arquivos numa pasta temporária (os bytes de `make_png()` e `make_jpeg()` alternados, com nomes diferentes) e chama `add_paths([pasta])`;
    - durante a análise, um `QTimer` de 10 ms mede o maior intervalo entre disparos, que tem de ser < 250 ms;
    - as 1.000 entradas terminam ANALYZED em até 180 s;
    - `view.scrollToBottom()` funciona.
  - `test_tab_order`: "Adicionar fotos" → "Adicionar pasta" → lista → seletor de modo → botão Limpar, por `nextInFocusChain` e pelas chamadas de `setTabOrder`.
  - `test_shortcuts`: Ctrl+O abre o diálogo de fotos (com monkeypatch); Ctrl+Shift+O, o de pasta; Ctrl+Enter chama `start_cleaning`.
  - `test_icon_only_controls_have_accessible_names_and_tooltips`.
  - `test_long_names_are_elided_with_full_tooltip`.
- [ ] **Step 2-4:** falhar, corrigir o que falhar (sem afrouxar limites), passar.
- [ ] **Step 5:** capturas em escala 200%, com `QT_SCALE_FACTOR=2` num subprocesso de captura, nos temas claro e escuro: página vazia, com lista e depois da limpeza.
- [ ] **Step 6: Commit** — `test(gui): scale, keyboard and accessibility guarantees`

---

### Task 14: Fechamento — amostra real, ponta a ponta e instruções

**Files:** Create `tests/gui/test_end_to_end.py` · Modify `README.md` (nova seção "Programa novo (prévia)", em português, com a instalação `pip install -e ".[gui]"` e o comando `siftool-gui`)

- [ ] **Step 1: Testes**
  - `test_chatgpt_sample_end_to_end` (pular se `tests/samples-local/chatgpt_c2pa.png` não existir):
    - copia a amostra para `tmp_path` (nunca grava ao lado do original privado) e solta a cópia;
    - a linha mostra a categoria "Marca de IA" com valor que contém "OpenAI";
    - `start_cleaning()` → VERIFIED;
    - o `_clean.png` existe em `tmp_path`, e o SHA-256 da cópia de origem não muda.
  - `test_window_smoke_full_flow(qtbot, tmp_path)`: `create_window` → soltar 2 fixtures → limpar → as duas VERIFIED → fechar sem erro.
- [ ] **Step 2: Rodar tudo** — `.venv/Scripts/python.exe -m pytest -q -rs` e `-W error`. Os testes da amostra aparecem como PASSED.
- [ ] **Step 3:** conferir os critérios de sucesso da spec §3 com evidência e registrar no relatório:

| Critério | Prova |
|---|---|
| 1 | `test_chatgpt_sample_end_to_end` |
| 2 | `test_thousand_files_keep_ui_responsive` |
| 3 | `test_heic_row_shows_not_supported_yet` |
| 4 | `test_error_texts_for_each_exception` + `test_verification_failure_shows_violations` |
| 5 | capturas claro/escuro a 100% e 200% (Tasks 9-13) |
| 6 | `test_import_siftool_loads_no_qt` + suíte do motor |

- [ ] **Step 4: Commit** — `test(gui): end-to-end with the real sample, README instructions`
