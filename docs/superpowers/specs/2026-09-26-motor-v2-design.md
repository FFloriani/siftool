# Siftool v2 — Motor novo (design)

- **Data:** 2026-09-26
- **Status:** aprovado em conversa, seção por seção; aguardando revisão deste documento
- **Subprojeto:** 1 de 4 do Siftool v2:
  1. Motor novo (este documento)
  2. Interface PySide6 + QFluentWidgets
  3. Distribuição
  4. Documentação, licença e idioma
- **Próximo passo:** plano de implementação (skill writing-plans)

## 1. Contexto

A auditoria de 2026-09-26 confirmou, com testes, 8 bugs no motor atual (`siftool.py`):

- PNG sem arquivo de saída;
- selo "Verified Clean" dado a arquivo inexistente;
- verificação que só olha o que o Pillow expõe;
- dados após o fim do JPEG preservados, com EXIF e GPS;
- GPS de MP4 deixado em caixas renomeadas para `free`;
- WEBP re-encodado, virando lossy e perdendo animação;
- TIFF mantendo XMP, Artist e Software;
- presets que geram "⚠" falso;
- editor que grava no original.

Os dois primeiros foram corrigidos no motor antigo. O usuário escolheu **reescrever o motor do zero** (abordagem C), em vez de mover o código existente para módulos (abordagem A, a recomendada). Este design elimina por construção as classes de bug encontradas.

### Entendimento acordado com o usuário

- A interface gráfica (subprojeto 2) é para quem não é técnico; desenvolvedores usam a CLI e a API.
- A interface é em português primeiro; código, API e CLI ficam em inglês.
- Promessas inegociáveis: 100% offline, nunca alterar o arquivo original, nunca re-encodar pixels ou amostras, e selo apenas quando verificado de fato.
- A licença GPLv3 é aceitável (exigida pelo QFluentWidgets no subprojeto 2).

## 2. Escopo

**Dentro:**
- Pacote `siftool` com API `inspect` / `clean` / `verify`.
- Formatos JPEG, PNG, WEBP, HEIC e MP4/MOV (inclui M4V).
- Três modos de limpeza.
- CLI nova.
- Suíte de testes pytest.
- `.venv` novo e `pyproject.toml`.

**Marcos:**
- **M1:** núcleo (modelo, planner, detect, safe_io, verificação, EXIF/XMP/C2PA/ICC/IPTC) + JPEG + PNG + WEBP + CLI. O motor já fica utilizável para imagens, e a interface (subprojeto 2) pode começar aqui.
- **M2:** leitor BMFF + HEIC + MP4/MOV.

**Fora (fases seguintes):**
- Formatos GIF, TIFF, AVIF, BMP, MKV e AVI.
- Editor de tags.
- Publicação no PyPI.
- Interface gráfica e instalador.
- Traduções.
- Sequências HEIC (HEIC com `moov`).

## 3. Critérios de sucesso

1. Todos os bugs da auditoria têm teste de regressão (seção 11) e passam.
2. Para cada amostra gerada × cada modo:
   - nenhum metadado proibido pelo modo sobra, checado pelo leitor **e** por busca de assinaturas nos bytes fora das áreas essenciais;
   - o hash da parte essencial da saída é igual ao do original;
   - o arquivo original não muda (hash antes e depois).
3. A amostra real `tests/samples-local/` (PNG do ChatGPT com C2PA em `caBX`), quando presente, sai com status `verified` e sem C2PA.
4. Nenhuma entrada, inclusive truncada, corrompida ou aleatória, causa exceção fora da hierarquia `SiftoolError`, nem recebe status `verified` sem ser válida.
5. O pacote declara `dependencies = []`.
6. Processar vídeo tem pico de alocação Python abaixo de 64 MB, independentemente do tamanho do `mdat`. Medido com `tracemalloc` num MP4 sintético de 512 MB.

## 4. Princípios

1. **Identifica pelo conteúdo, não pela extensão.** O formato vem dos primeiros bytes (assinatura/`ftyp`). O arquivo é desmontado em blocos `ESSENTIAL`, `METADATA` ou `UNKNOWN`, mais eventuais dados após o fim lógico do formato (*trailing*).
2. **Lista do que pode ficar (whitelist).** Tudo o que não é essencial nem explicitamente permitido pelo modo é removido, inclusive blocos desconhecidos e dados após o fim.
3. **Nunca re-encoda, nunca desloca.** Os dados essenciais são copiados byte a byte. Em formatos com posições absolutas (HEIC, MP4), metadados são **neutralizados no lugar**: o conteúdo é zerado, o tamanho mantido e o tipo trocado para `free` quando for uma caixa. Renomear sem zerar é proibido.
4. **Original intocável, saída atômica.** A saída vai para um arquivo temporário no diretório de destino e é renomeada (`os.replace`) só depois de verificada. Destino igual à origem é sempre recusado.
5. **Selo que falha fechado.** O `verify` relê a saída com o mesmo leitor e, se houver original, compara o hash essencial. Qualquer erro vira `failed`.
6. **Sem dependências em tempo de execução.** Só biblioteca padrão. O Pillow (e opcionalmente o `pillow-heif`) é usado apenas nos testes, para decodificar pixels.

## 5. Arquitetura

```
src/siftool/
  __init__.py        API pública: inspect, clean, verify, Mode, Status, exceções
  model.py           Category, Finding, Block, Container, Action, Plan, Report, Verdict, CleanResult
  modes.py           Mode -> conjunto de categorias permitidas
  planner.py         decide a Action de cada bloco a partir do modo
  detect.py          identifica o formato e devolve o handler
  safe_io.py         mapeamento de leitura (mmap), temporário + os.replace, checagens de caminho
  errors.py          hierarquia SiftoolError
  formats/
    base.py          contrato FormatHandler
    jpeg.py  png.py  webp.py
    bmff.py          leitor de caixas ISO BMFF (compartilhado)
    heic.py  mp4.py
  metadata/
    exif.py          leitura de TIFF/IFD com categorização por tag + escrita de EXIF mínimo
    xmp.py           leitura leve (GPS, criador, ferramenta, DigitalSourceType) para relatório
    c2pa.py          detecção de JUMBF/C2PA e resumo (claim generator)
    icc.py           descrição do perfil (ex.: "Display P3")
    iptc.py          leitura de IPTC/IRB para relatório
  cli.py
  __main__.py        python -m siftool -> cli
tests/
pyproject.toml
```

O `siftool.py` antigo continua na raiz, sem mudanças, até o subprojeto 2. O layout `src/` evita conflito de import entre os dois.

### 5.1 API pública

```python
def inspect(path: str | os.PathLike) -> Report
def clean(src, dst, mode: Mode = Mode.PRIVACY, *, overwrite: bool = False) -> CleanResult
def verify(path, mode: Mode = Mode.PRIVACY, *, original=None) -> Verdict
```

- `Mode`: `PRIVACY` (padrão), `PHOTOGRAPHER`, `MAXIMUM`.
- `Status`: `VERIFIED`, `WARNINGS`, `FAILED`.
- `Category` (valor em snake_case):
  - `location`
  - `identity`
  - `ai_provenance`
  - `time`
  - `software`
  - `text`
  - `extras`
  - `device`
  - `camera_settings`
  - `appearance`
  - `unknown`
- `Finding(category, label, detail: str | None, where: str, size: int)`. O `label` é uma descrição curta em inglês. O `detail` é uma prévia truncada em 80 caracteres (ex.: `"claim generator: ChatGPT"`), e `where` é o caminho estrutural (ex.: `"APP1/Exif/GPS"`).
- `Report(path, format, findings, essential_digest, notes)`.
- `Verdict(status, violations: list[Finding], notes: list[str], essential_match: bool | None)`.
- `CleanResult(src, dst, mode, removed: list[Finding], kept: list[Finding], verdict: Verdict)`.

**Erros** (`errors.py`, todos derivam de `SiftoolError`):
- `UnsupportedFormatError`
- `CorruptFileError`
- `UnsafeFileError` (estrutura que não dá para limpar com segurança)
- `DestinationExistsError`
- `SameFileError`
- `VerificationFailedError` (carrega o `Verdict`)

**Comportamento de cada função:**
- `inspect` levanta essas exceções.
- `clean` levanta essas exceções. Se a verificação pós-limpeza der `FAILED`, apaga o temporário e levanta `VerificationFailedError`: nenhum arquivo fica no destino. Com `VERIFIED` ou `WARNINGS`, grava o destino e retorna `CleanResult`.
- `verify` **nunca** levanta por problema de conteúdo. Arquivo ilegível, corrompido ou não suportado resulta em `Verdict(FAILED, ...)`.

### 5.2 Contrato dos formatos (`formats/base.py`)

```python
class FormatHandler(Protocol):
    name: str
    def sniff(self, head: bytes) -> bool
    def parse(self, buf: memoryview) -> Container
    def write(self, container: Container, plan: Plan, out: BinaryIO) -> None
    def essential_digest(self, container: Container) -> str   # SHA-256 hex
    def rewrite(self, block: Block, allowed: set[Category]) -> bytes | None
```

- `Container(format, blocks: list[Block], trailing: Span | None)`.
- `Block(kind, span, label, findings, removable, rewritable)`.
- `rewrite` gera a versão reduzida de um bloco misto, com só as categorias permitidas: EXIF mínimo (6.1) ou JFIF sem miniatura. Devolve `None` quando nada permitido sobra.
- `Action` pode ser:
  - `KEEP`
  - `DROP`: omite o bloco; só em formatos sem posições absolutas.
  - `ZERO`: neutraliza no lugar.
  - `REWRITE`: bytes novos, gerados por `rewrite`.
- `Plan` mapeia cada bloco a uma `Action`.

O `write` monta a saída copiando fatias do `buf` (mapeamento `mmap` do original). Assim, vídeos grandes são processados em fluxo, sem carregar o arquivo na memória.

Um formato novo é um arquivo novo que cumpre o contrato e se registra em `detect.py`.

### 5.3 Fluxo do `clean`

1. `detect`: escolhe o handler e faz `parse`.
2. `planner`: decide a `Action` de cada bloco.
3. `write`: grava num temporário no diretório de destino.
4. `verify`: relê o temporário, com o original disponível para comparar o hash.
5. Se `VERIFIED` ou `WARNINGS`, `os.replace` para o destino. Se `FAILED`, remove o temporário e levanta `VerificationFailedError`.

### 5.4 Regras do planner

Para cada bloco:

| Tipo do bloco | Situação | Ação |
|---|---|---|
| `ESSENTIAL` | sempre | `KEEP` |
| `METADATA` | todas as categorias permitidas pelo modo | `KEEP` |
| `METADATA` | nenhuma categoria permitida | `DROP`, ou `ZERO` em HEIC/MP4 |
| `METADATA` | mistura de categorias permitidas e proibidas | `REWRITE` se `rewritable`; senão `DROP`/`ZERO` (erra a favor da privacidade) e registra nota dizendo o que se perdeu |
| `UNKNOWN` | `removable` | `DROP`/`ZERO` |
| `UNKNOWN` | não removível | `KEEP` + nota (leva a `WARNINGS`) |
| trailing | sempre | removido (categoria `extras`) |

## 6. Categorias e modos

Categorias permitidas por modo. Todo o resto é removido:

| Modo | Mantém |
|---|---|
| `PRIVACY` (padrão) | `appearance` |
| `PHOTOGRAPHER` | `appearance`, `device`, `camera_settings` |
| `MAXIMUM` | nada |

Exceções e observações:

- **Vídeo (MP4/MOV):** `PHOTOGRAPHER` se comporta como `PRIVACY`, porque marca e modelo vêm no mesmo bloco que a localização. Isso gera uma nota no resultado.
- **HEIC e MP4:** rotação (`irot`/`imir`, matriz do `tkhd`) e cor (`colr`) são estrutura essencial, mantidas em todos os modos. No `MAXIMUM`, só JPEG, PNG e WEBP perdem orientação e cor, e o resultado leva uma nota de aviso.
- **Sempre removidos,** em todos os modos:
  - XMP;
  - MakerNote;
  - miniaturas e imagens secundárias (MPF, gain map anexada após EOI);
  - Motion Photo;
  - C2PA;
  - IPTC/IRB;
  - blocos desconhecidos removíveis.

### 6.1 Categorização de tags EXIF (`metadata/exif.py`)

| Categoria | Tags |
|---|---|
| `location` | todo o GPS IFD |
| `identity` | Artist, Copyright, CameraOwnerName, BodySerialNumber, LensSerialNumber, ImageUniqueID, XPAuthor |
| `device` | Make, Model, LensMake, LensModel, LensSpecification |
| `camera_settings` | ExposureTime, FNumber, ExposureProgram, ISOSpeedRatings/PhotographicSensitivity, SensitivityType, ShutterSpeedValue, ApertureValue, BrightnessValue, ExposureBiasValue, MaxApertureValue, MeteringMode, LightSource, Flash, FocalLength, FocalLengthIn35mmFilm, ExposureMode, WhiteBalance, DigitalZoomRatio, SceneCaptureType, Contrast, Saturation, Sharpness |
| `time` | DateTime, DateTimeOriginal, DateTimeDigitized, OffsetTime\*, SubSecTime\* |
| `software` | Software, ProcessingSoftware, HostComputer, MakerNote |
| `text` | ImageDescription, UserComment, XPTitle, XPComment, XPKeywords, XPSubject |
| `appearance` | Orientation, ColorSpace, Gamma, XResolution, YResolution, ResolutionUnit |
| `extras` | IFD1 inteiro (miniatura) |
| `unknown` | qualquer outra tag |

O **escritor de EXIF mínimo** gera um TIFF novo (big-endian) com IFD0 + Exif IFD contendo apenas as tags permitidas. Os ponteiros (ExifIFDPointer, ExifVersion) são gerados pelo escritor, não copiados. Se nenhuma tag permitida sobrar, o bloco é removido.

## 7. Estratégia por formato

### 7.1 JPEG

**Leitura:**
- Marcadores a partir de SOI, tolerando bytes de preenchimento `0xFF`.
- Após cada SOS, varre os dados entrópicos respeitando `0xFF00` e RST0–7, e continua lendo marcadores até o EOI. Isso cobre JPEG progressivo, com vários SOS.
- Tudo o que vem após o EOI é trailing, e o rótulo é detectado quando possível: `MPF`, `Motion Photo`, `SEFT` (Samsung).

**Classificação:**

| Segmento | Tratamento |
|---|---|
| DQT, DHT, SOFn, DRI, SOS + dados entrópicos, DNL, DAC | `ESSENTIAL` |
| APP14 `Adobe` | `ESSENTIAL` (afeta a decodificação de cor em CMYK/YCCK) |
| APP0 `JFIF` | `appearance`; se tiver miniatura JFIF, é regravado sem ela (miniatura = `extras`) |
| APP1 `Exif` | EXIF, `rewritable` |
| APP1 XMP e XMP estendido | removidos (achados vêm de `xmp.py`) |
| APP2 `ICC_PROFILE` (inclusive multissegmento) | `appearance` |
| APP2 `MPF` | `extras` |
| APP2 `FPXR` | `unknown` |
| APP11 JUMBF/C2PA | `ai_provenance` |
| APP12 | `software` |
| APP13 Photoshop/IPTC | removido; achados do `iptc.py` |
| Demais APPn | `unknown` removível |
| COM | `text` |

**Hash essencial:** todos os segmentos `ESSENTIAL`, em ordem, incluindo os dados entrópicos.

### 7.2 PNG

**Leitura:**
- Assinatura e chunks, com checagem de CRC. CRC inválido num chunk que vai ficar na saída (essencial ou mantido pelo modo) gera `CorruptFileError`. Num chunk que será removido, é ignorado.
- Tudo após IEND é trailing.

**Classificação:**

| Chunk | Tratamento |
|---|---|
| IHDR, PLTE, IDAT, IEND, tRNS, sBIT, acTL, fcTL, fdAT (APNG) | `ESSENTIAL` |
| iCCP, sRGB, gAMA, cHRM, cICP, mDCV, cLLI, pHYs | `appearance` |
| eXIf | EXIF, `rewritable` |
| tIME | `time` |
| caBX, cPRV | `ai_provenance` |
| tEXt / zTXt / iTXt | por palavra-chave (abaixo) |
| Ancilar desconhecido (1ª letra minúscula) | `unknown` removível |
| Crítico desconhecido (1ª letra maiúscula) | `UnsafeFileError` |

Palavras-chave de tEXt/zTXt/iTXt:
- `XML:com.adobe.xmp` → XMP, removido.
- `parameters`, `prompt`, `workflow`, `invokeai_metadata`, `sd-metadata`, `Dream`, e `Comment` contendo JSON com `"prompt"` → `ai_provenance`.
- `Author`, `Copyright` → `identity`.
- `Creation Time` → `time`.
- `Software` → `software`.
- Demais → `text`.

**Hash essencial:** os chunks `ESSENTIAL`, em ordem.

### 7.3 WEBP

**Leitura:**
- Chunks RIFF, respeitando o padding de tamanho ímpar.
- Tudo após o tamanho declarado no RIFF é trailing.

**Classificação:**

| Chunk | Tratamento |
|---|---|
| VP8, VP8L, VP8X, ALPH, ANIM, ANMF | `ESSENTIAL` |
| ICCP | `appearance` |
| EXIF | EXIF, `rewritable` (só existe em arquivo com VP8X) |
| `XMP ` | removido |
| `C2PA` | `ai_provenance` |
| Outros | `unknown` removível |

**Escrita:**
- Recalcula o tamanho do RIFF.
- Atualiza as flags do VP8X (ICC, EXIF, XMP) para refletir os chunks que ficaram.
- O WEBP animado é preservado intacto.

**Hash essencial:** os payloads dos chunks `ESSENTIAL`, exceto as flags de metadados do VP8X.

### 7.4 ISO BMFF (`bmff.py`)

- Leitor de caixas com tamanho de 32 e 64 bits, `size == 0` (até o fim do arquivo), tipo `uuid` e full boxes (versão/flags).
- Desce apenas nos contêineres conhecidos; os demais são opacos.
- Neutralização no lugar: o payload é zerado e o tipo vira `free`.
- UUIDs reconhecidos:
  - XMP: `BE7ACFCB-97A9-42E8-9C71-999491E3AFAC`
  - C2PA: `D8FEC3D6-1B0E-483C-9297-5828877EC481`

### 7.5 HEIC (`ftyp` com marcas `heic`/`heix`/`mif1`/`msf1`/`heim`/`heis`)

**Leitura:**
- Da caixa `meta`: `hdlr`, `pitm`, `iinf`/`infe`, `iloc` (versões 0–2, métodos de construção 0 e 1), `iref`, `iprp`/`ipco`/`ipma`, `idat`.
- Um `moov` presente gera `UnsafeFileError` ("sequência HEIC não suportada na v1").

**Classificação:**
- Itens `Exif`: EXIF, com o prefixo de 4 bytes de deslocamento do TIFF, e `rewritable`.
- Itens `mime` com `application/rdf+xml`: XMP.
- Miniaturas (`iref` do tipo `thmb`): `extras`.
- Caixa `uuid` C2PA no nível raiz: `ai_provenance`.
- Itens de imagem (`hvc1`, `grid`, `iovl`, `iden`, `tmap`, auxiliares) e todas as propriedades do `ipco`: `ESSENTIAL`.

**Remoção de item:**
1. Retira as entradas do item de `iinf`, `iloc`, `iref` e `ipma`.
2. Reescreve a `meta` com **o mesmo tamanho total**; a sobra vira uma caixa `free` dentro da `meta`.
3. Zera os bytes do item no `mdat`/`idat`.

Nenhum deslocamento absoluto muda.

**`REWRITE` de EXIF (modo `PHOTOGRAPHER`):**
- O EXIF mínimo é gravado no início da extensão original do item.
- O restante da extensão é zerado.
- O `extent_length` do `iloc` é atualizado. É um campo de largura fixa, então a `meta` não muda de tamanho.

**Hash essencial:** os bytes dos itens de imagem (pelas extensões do `iloc`) + o conteúdo do `ipco`.

### 7.6 MP4/MOV (`ftyp` isom/mp41/mp42/M4V/qt; MOV sem `ftyp` é reconhecido por `moov`/`mdat`/`wide` no início)

**Classificação:**

| Onde | Tratamento |
|---|---|
| `moov/udta`, `trak/udta`, `moov/meta` (keys/ilst da Apple), `uuid` XMP/C2PA | `ZERO` (categorias pelos achados: location, device, software, time etc.) |
| mvhd/tkhd/mdhd (tempo de criação e modificação) | `time`; os campos são zerados no lugar |
| Trilhas com `hdlr` `meta`, `mebx`, `gpmd` ou `camm` | estrutura mantida; as amostras são localizadas pelas tabelas `stsz`/`stsc`/`stco`/`co64` e zeradas no `mdat` durante a cópia, em todos os modos. Categoria: `gpmd`/`camm` = `location` (telemetria); `meta`/`mebx` = `unknown` (conteúdo variado) |
| Caixas desconhecidas no nível raiz (fora de ftyp, moov, mdat, free, skip, wide, uuid, pdin, moof, mfra, sidx, styp, meco) | `ZERO` |
| Caixas desconhecidas dentro de `moov`/`trak`, fora de `udta`/`meta` | `KEEP` + nota (leva a `WARNINGS`) |
| Dados após a última caixa | trailing |

**Hash essencial:** as caixas de decodificação do `moov`, excluindo `udta`, `meta` e os campos de tempo, + os bytes do `mdat`, excluindo as faixas de amostras de trilhas de metadados.

**Cópia:** o `mdat` é copiado em blocos de 1 MiB, aplicando as faixas de zeramento durante a cópia.

## 8. Verificação

`verify(path, mode, original=None)`:

1. Identifica o formato e faz o `parse`. Formato não suportado ou erro de leitura dá `FAILED`.
2. Aponta como violação cada `Finding` restante cuja categoria não é permitida pelo modo, e qualquer trailing.
3. Blocos `UNKNOWN` não removíveis que restarem geram notas e levam a `WARNINGS`.
4. Faz uma busca de assinaturas fora das áreas essenciais: `Exif\0\0` fora de bloco EXIF permitido, `<x:xmpmeta`, `jumb`, `c2pa`, `caBX`, padrão ISO 6709 (`[+-]\d{2}\.\d+[+-]\d{3}\.\d+`), `GPS`. Achado ali é violação.
5. Com `original`: compara o `essential_digest` de ambos. Se diferir, `FAILED`.
6. O status final segue esta ordem:
   - qualquer violação ou erro → `FAILED`;
   - senão, alguma nota de ressalva → `WARNINGS`;
   - senão → `VERIFIED`.

### Limites

A verificação cobre estruturas de metadados: blocos não ESSENTIAL e a busca
de assinaturas (passo 4) fora dos spans ESSENTIAL. Dado escondido nos
VALORES de uma estrutura essencial válida (um bit de flag, um valor de
quantização) ou dentro dos próprios dados de imagem comprimidos
(esteganografia) não é detectado. O selo "Verified Clean" garante a
ausência de metadados fora do padrão nas estruturas do arquivo, não a
ausência de qualquer bit de informação escondido na imagem em si.

## 9. Erros, caminhos e memória

- `clean` recusa destino igual à origem (`SameFileError`, comparando por `os.path.samefile` quando existir e pelo caminho normalizado).
- Destino existente só é substituído com `overwrite=True`; senão, `DestinationExistsError`.
- O temporário vai no diretório de destino (`<nome>.siftool-tmp-<aleatório>`) e é removido em qualquer falha, com `try/finally`.
- Imagens de até 512 MB são lidas via `mmap`. Acima disso, um formato de imagem gera `UnsafeFileError` ("arquivo grande demais para imagem"). Vídeos não têm limite.
- Arquivo de 0 byte gera `CorruptFileError`.

## 10. CLI (`siftool`, ou `python -m siftool`)

```
siftool scan FILE...                                      # o que o arquivo contém, por categoria
siftool clean PATH... [--mode privacy|photographer|maximum] [--out DIR] [--overwrite]
siftool verify FILE [--mode ...] [--original FILE]
opções comuns: --json
```

**Nomes e pastas:**
- A saída padrão é `<stem>_clean<ext>` ao lado do original, ou em `--out`.
- Em caso de colisão sem `--overwrite`, usa `<stem>_clean (2)<ext>`, `(3)` etc.
- Pastas são percorridas recursivamente e consideram as extensões `.jpg .jpeg .png .webp .heic .heif .mp4 .mov .m4v`. O conteúdo é conferido pelo `detect`.
- Arquivos cujo stem termina em `_clean` são ignorados em pastas, mas não quando passados explicitamente.

**Saída:**
- stdout/stderr reconfigurados para UTF-8.
- Texto legível por padrão.
- `--json` usa o schema versionado:

```json
{
  "schema": 2,
  "mode": "privacy",
  "results": [
    {
      "file": "foto.png",
      "output": "foto_clean.png",
      "status": "verified",
      "removed": [{"category": "ai_provenance", "label": "C2PA manifest", "detail": "claim generator: ChatGPT", "where": "caBX"}],
      "kept": [{"category": "appearance", "label": "ICC profile", "detail": "Display P3", "where": "iCCP"}],
      "notes": []
    }
  ],
  "summary": {"total": 1, "verified": 1, "warnings": 0, "failed": 0}
}
```

Arquivos com erro aparecem com `"status": "failed"`, `"output": null` e `"error": "<mensagem>"`.

**Código de saída:** o pior caso do lote:
- `0`: tudo verificado;
- `1`: alguma ressalva;
- `2`: alguma falha ou erro de uso.

## 11. Testes

**Estrutura:**
- `tests/fixtures/make_fixtures.py`: gerador **determinístico** de amostras para cada formato, com blocos de todas as categorias, blocos desconhecidos (removíveis e não removíveis), trailing, C2PA sintético, EXIF com todas as tags da tabela 6.1 e WEBP animado. O HEIC e o MP4 são construídos caixa a caixa, com dados de imagem/vídeo sintéticos.
- `tests/samples-local/`: pasta ignorada pelo git para amostras reais (PNG do ChatGPT, fotos de iPhone e de Android, vídeos de celular). Os testes parametrizam sobre o que existir ali e são pulados se ela estiver vazia.

**Testes de regressão (bugs da auditoria):**
- `test_png_output_is_written`
- `test_verify_missing_file_fails`
- `test_png_ai_text_chunks_removed_and_detected`
- `test_jpeg_trailing_data_removed`
- `test_mp4_location_bytes_are_zeroed` (busca `+37.7749-122.4194` nos bytes da saída)
- `test_webp_lossless_stays_bit_identical`
- `test_webp_animation_preserved`
- `test_photographer_mode_verifies_without_false_warning`

**Matriz** (formato × modo, pytest parametrizado):
- nada proibido sobra (leitor + busca de assinaturas);
- `essential_digest` igual;
- pixels iguais quando o Pillow decodifica (o HEIC só se o `pillow-heif` estiver instalado);
- original intacto.

**Robustez:**
- Para cada formato, 300 mutações com semente fixa: truncamento, bytes invertidos, tamanhos de caixa/chunk inválidos.
- Nenhuma exceção fora de `SiftoolError`, e nunca `VERIFIED` quando o `clean` falhar.

**Outros testes:**
- **Memória:** MP4 sintético com `mdat` de 512 MB, com pico `tracemalloc` abaixo de 64 MB.
- **CLI:** comandos, `--json` válido contra o schema 2, códigos de saída, nomes com acento, colisão de nomes.

## 12. Ambiente e transição

- O `.venv` atual está quebrado (aponta para `C:\Users\Floriani\miniconda3`, que não existe mais) e será substituído por um `.venv` criado com `E:\miniconda3\python.exe` (Python 3.14.7).
- **`pyproject.toml`:**
  - `name = "siftool"`, `version = "2.0.0.dev0"`, `requires-python = ">=3.11"`, `dependencies = []`;
  - extra `test` = `pytest`, `Pillow`, com `pillow-heif` opcional;
  - extra `legacy` = `Pillow`, `piexif`, `tkinterdnd2`, para rodar o `siftool.py` antigo;
  - script de console `siftool = "siftool.cli:main"`;
  - instalado com `pip install -e .[test,legacy]`.
- A compatibilidade do app antigo (Tkinter + tkinterdnd2) com o Python 3.14 é conferida na primeira tarefa do plano. Se falhar, o `.exe` antigo em `dist/` continua sendo o app utilizável até o subprojeto 2. O `siftool.py` não é modificado nesta fase.
- **Pré-requisito de execução:** as mudanças hoje não commitadas (`siftool.py`, `ANALISE_COMPETITIVA.md`) precisam de um commit de base antes da implementação, para que o trabalho novo tenha um ponto de partida limpo. Esse commit depende de aprovação do usuário.

## 13. Riscos e mitigação

| Risco | Mitigação |
|---|---|
| Variações de HEIC (grid, gain map `tmap`, `idat`, `iloc` v2, métodos de construção) | Suporte explícito às variantes listadas; qualquer outra gera `UnsafeFileError` em vez de saída duvidosa; amostras reais de iPhone em `samples-local`. |
| Localizar amostras de trilhas de metadados em MP4 (`stsc`/`stco`/`co64`) | Testes sintéticos com várias configurações de chunk + vídeos reais de celular. |
| Reescrever demora mais que mover | O app antigo segue disponível; o motor é construído formato a formato, e a interface pode começar com JPEG + PNG prontos. |
| Leitores de XMP/C2PA/IPTC encontrarem estruturas inesperadas | São só para relatório: falha de leitura vira achado genérico ("XMP (unreadable)") e não impede a remoção. |

## 14. Decisões registradas

| Decisão | Escolha |
|---|---|
| Abordagem | Reescrever o motor (C), em vez de mover (A, recomendada) |
| Formatos da v1 | JPEG, PNG, WEBP, HEIC, MP4/MOV |
| Modos | `PRIVACY` (padrão, mantém a aparência), `PHOTOGRAPHER`, `MAXIMUM` |
| Editor de tags | Fora da v1 |
| Nomes no código e na CLI | Em inglês; rótulos em português só na interface |
| JSON da CLI | Schema 2 (o formato antigo do README deixa de existir) |
| Códigos de saída | 0 verificado, 1 ressalvas, 2 falha |
