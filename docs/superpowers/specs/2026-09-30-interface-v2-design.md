# Siftool v2 — Interface nova (design)

- **Data:** 2026-09-30
- **Status:** aprovado por delegação. O usuário escolheu o fluxo, o nível de detalhe e o layout, e pediu para as demais decisões serem tomadas sem consulta ("quero que você pare de me perguntar e trabalhe"). As decisões tomadas assim estão marcadas com **(decisão)**.
- **Subprojeto:** 2 de 4 do Siftool v2. Ordem vigente: motor M1 → **interface** → motor M2 (HEIC e vídeo) → distribuição.
- **Base:** o motor M1 (`siftool.inspect` / `clean` / `verify`, JPEG, PNG e WEBP), já integrado ao `master`.
- **Próximo passo:** plano de implementação.

## 1. Objetivo

Substituir a janela Tkinter do `siftool.py` antigo por um programa novo, bonito e fácil de usar, montado sobre o motor novo.

A pessoa leiga precisa conseguir limpar uma foto do celular ou do ChatGPT em poucos cliques e entender o que foi removido, sem ajuda.

### Entendimento confirmado com o usuário
- É para quem não é técnico, em português.
- As promessas do motor continuam valendo:
  - 100% offline;
  - nunca altera o original;
  - nunca recomprime a imagem;
  - selo só quando a verificação passa de verdade.
- Três modos: **Privacidade** (padrão), **Fotógrafo** e **Máximo**.
- Não há editor de metadados.
- Visual do Windows 11: PySide6 + QFluentWidgets Community, licença GPLv3.
- A cópia limpa fica ao lado do original, com `_clean` no nome.
- HEIC e vídeo aparecem como "ainda não suportado" até o M2.
- O menu de clique direito do Windows e o instalador ficam para a etapa de distribuição.

### Escolhas do usuário
1. **Fluxo "Ver e depois limpar":** ao soltar os arquivos, a lista mostra na hora o que cada um esconde; depois, um botão limpa todos com o modo escolhido.
2. **Detalhe simples, com o técnico escondido:** ao abrir uma foto, aparecem as categorias em português claro, dizendo o que sai e o que fica no modo escolhido. Um botão abre a lista técnica.
3. **Coluna única:** de cima para baixo, a área de soltar, depois a lista com miniaturas (a foto clicada abre os detalhes ali mesmo) e, por fim, o modo e o botão de limpar.
4. **Volume:** "os dois". Na maioria das vezes são poucas fotos, às vezes uma pasta com centenas ou milhares.

## 2. Escopo

**Dentro:**
- a janela principal (página "Limpar");
- as páginas Configurações e Sobre;
- arrastar e soltar arquivos e pastas;
- a análise e a limpeza em segundo plano, com progresso e cancelamento;
- mensagens em português para todos os resultados do motor;
- os temas claro e escuro;
- o teclado e o leitor de tela;
- os testes.

**Fora (etapas seguintes):**
- HEIC, MP4 e MOV (M2). A interface já os mostra como "ainda não suportado" e não precisará mudar quando eles chegarem.
- Instalador, `.exe`, menu de clique direito, instância única e atualização automática (distribuição).
- Tradução para outros idiomas.
- Editor de metadados.
- Prévia "antes e depois" da imagem.
- Arrastar a foto limpa para fora da janela.

## 3. Critérios de sucesso

1. **O caso do usuário.** Arrastar o PNG do ChatGPT faz a linha mostrar "Marca de IA: OpenAI". Ao clicar em "Limpar", a linha passa para "Verificado", o `_clean.png` existe ao lado do original e o original fica byte a byte igual.
2. **Pasta grande.** Uma pasta com 1.000 imagens de teste entra na lista sem congelar a janela: a interface continua respondendo durante a análise, a rolagem é fluida e o número de análises simultâneas é limitado.
3. **Formato ainda não suportado.** Um HEIC ou MP4 no meio da pasta aparece como "Ainda não suportado" e não atrapalha os outros arquivos.
4. **Todo resultado em português.** Cada resultado possível do motor (verificado, com ressalvas, falhou, arquivo danificado, grande demais, sem permissão, não encontrado) aparece com mensagem em português. Só os "detalhes técnicos" mostram os textos originais do motor.
5. **Temas e escala.** Os temas claro e escuro e a escala de 100% a 200% ficam legíveis. Isso é conferido por capturas de tela em todas as tarefas visuais.
6. **Motor isolado.** O motor continua sem dependências: `import siftool` não importa Qt, e a suíte do motor segue passando.

## 4. Arquitetura

### 4.1 Pacotes e dependências (decisão)
- **Pacote novo `src/siftool_gui/`,** na mesma distribuição `siftool`. O pacote `siftool` nunca importa `siftool_gui` nem Qt.
- **Extra `gui` no `pyproject.toml`:** `PySide6>=6.7` e `PySide6-Fluent-Widgets>=1.7`. O extra `test` ganha `pytest-qt`.
- **Como abrir o programa:**
  - pelo `[project.gui-scripts]`: `siftool-gui = "siftool_gui.app:main"`;
  - por `python -m siftool_gui`, rodando fora da raiz do repositório, pelo mesmo motivo da regra R4: o `siftool.py` antigo na raiz faria sombra ao pacote.
- **O `siftool.py` antigo não é alterado.** Ele continua existindo até a etapa de distribuição.

### 4.2 Peça compartilhada com a linha de comando (decisão)
Um módulo novo do motor, `src/siftool/batch.py` (só stdlib), recebe o que hoje vive em `cli.py`:
- `iter_inputs(paths)`: percorre pastas recursivamente, usa as extensões da spec §10 e pula as saídas `_clean` anteriores;
- `output_path_for(src, out_dir, overwrite, reserved)`: gera `<nome>_clean<ext>` ou `(2)`, `(3)`…, sem reutilizar um nome já dado na mesma rodada.

A linha de comando e a interface usam as mesmas funções, com os mesmos testes. O comportamento da linha de comando não muda.

### 4.3 Unidades da interface

| Arquivo | Responsabilidade | Depende de |
|---|---|---|
| `entries.py` | `FileEntry` (caminho, estado, relatório, resultado, erro, miniatura) e as transições de estado. Sem Qt. | `siftool` |
| `presentation.py` | Textos em português: nome e ícone de cada categoria, o valor "humano" de cada achado, descrições dos modos, textos de estado e de erro. Diz se uma categoria "sai" ou "fica" num modo. Sem Qt. | `siftool`, `siftool.modes.ALLOWED` |
| `settings.py` | Preferências persistentes (`QConfig` do QFluentWidgets, em `%APPDATA%\Siftool\config.json`): modo padrão, onde salvar, tema. | QFluentWidgets |
| `workers.py` | `JobRunner` (QObject) com um `QThreadPool` limitado; tarefas de varrer pasta, analisar, gerar miniatura e limpar; sinais de resultado; cancelamento. | Qt, `siftool`, `siftool.batch` |
| `model.py` | `FileListModel` (QAbstractItemModel). Linha de arquivo → linhas de categoria → linha "Detalhes técnicos" → linhas técnicas. Recalcula "sai/fica" quando o modo muda. | Qt, `entries`, `presentation` |
| `delegate.py` | `FileItemDelegate`: desenha cada tipo de linha no estilo Fluent (cartão, miniatura, etiquetas, selo), nos temas claro e escuro. | Qt, QFluentWidgets |
| `pages/clean_page.py` | Página "Limpar": área de soltar, lista (TreeView), barra de ação (modo, progresso, botões). | tudo acima |
| `pages/settings_page.py` | Página Configurações (`SettingCardGroup`). | `settings` |
| `pages/about_page.py` | Página Sobre: versão, promessas, licença e créditos. | — |
| `window.py` | `MainWindow` (`FluentWindow`) com navegação: Limpar, Configurações e Sobre. | páginas |
| `app.py` | `main()`: `QApplication`, tema, janela, laço de eventos. | `window`, `settings` |

`entries.py` e `presentation.py` não importam Qt, para que a lógica seja testável sem janela.

### 4.4 Fluxo de dados
1. **Entrada.** O usuário solta itens ou clica em "Adicionar fotos" ou "Adicionar pasta". O `JobRunner` varre as pastas em segundo plano (`batch.iter_inputs`) e entrega os caminhos em lotes. O modelo cria um `FileEntry` por arquivo, no estado **Na fila**, e ignora duplicados, comparando o caminho normalizado.
2. **Análise.** Cada entrada vai para "Analisando". A tarefa chama `siftool.inspect(path)` e o resultado vira:
   - **Analisado**, com o `Report`;
   - **Não suportado**, se vier `UnsupportedFormatError`;
   - **Erro**, se vier outro `SiftoolError` ou `OSError`, com a mensagem em português.

   Em paralelo e com prioridade menor, uma tarefa gera a miniatura (`QImageReader` com `setAutoTransform(True)`, reduzida para 96 px, só em memória).
3. **Visão.** A linha mostra as etiquetas das categorias encontradas, ou "Nada escondido" se não houver achados. Ao expandir, cada categoria mostra o valor humano e ✖ sai ou ✔ fica, conforme `ALLOWED[modo]`. Trocar o modo só recalcula o texto, sem nova análise.
4. **Limpeza.** O botão "Limpar N fotos" limpa as entradas analisadas que ainda não foram limpas. Para cada uma:
   - calcula o destino (`batch.output_path_for`, com a pasta escolhida nas preferências);
   - chama `siftool.clean(src, dst, modo)`;
   - o estado passa para **Verificado**, **Com ressalvas** ou **Não limpou**, com o motivo.
5. **Fim.** Um `InfoBar` resume o lote ("3 fotos limpas e verificadas") e oferece "Abrir pasta". Cada linha limpa tem a ação "Mostrar no Explorer" (`explorer /select,`).

Concorrência (decisão): `QThreadPool` com no máximo `min(4, núcleos)` tarefas simultâneas, porque o motor lê o arquivo inteiro e aceita até 512 MB. O modelo só é alterado na thread da interface, por meio de sinais. Cancelar tira da fila o que ainda não começou; o que já começou termina. Como a gravação do motor é atômica, não sobra arquivo pela metade.

### 4.5 Estados de uma entrada

```
Na fila → Analisando → Analisado ──(Limpar)──→ Limpando → Verificado
                     ↘ Não suportado                    ↘ Com ressalvas
                     ↘ Erro                             ↘ Não limpou
```

- Uma entrada **Verificado** ou **Com ressalvas** não entra no próximo "Limpar".
- "Limpar de novo" existe no menu da linha e gera nova cópia com o modo atual.
- "Remover da lista" (tecla Delete) vale para qualquer estado, menos Limpando.

## 5. A janela

### 5.1 Casca (decisão)
- **`FluentWindow`,** com três itens de navegação: **Limpar** (ícone de vassoura/pincel), **Configurações** e **Sobre** (os dois últimos no rodapé da barra).
- **Título:** "Siftool".
- **Tamanho:** mínimo de 720 × 560. A janela lembra o último tamanho.
- **Tema:** segue o Windows por padrão (`Theme.AUTO`), com a cor de destaque padrão do QFluentWidgets.

### 5.2 Página "Limpar"
Conteúdo centralizado, com largura máxima de leitura de 960 px e rolagem da página inteira quando necessário.

1. **Cabeçalho.**
   - `TitleLabel`: "Limpar fotos".
   - `BodyLabel`: "Tire localização, dados pessoais e marcas de IA das suas fotos. O original nunca é alterado."
2. **Área de soltar.** Um cartão com borda tracejada, o texto "Arraste fotos ou pastas aqui" e os botões "Adicionar fotos" e "Adicionar pasta".
   - O cartão se destaca quando algo é arrastado sobre ele.
   - Com a lista vazia, é o elemento dominante da página. Com a lista preenchida, encolhe para uma faixa baixa.
3. **Resumo da lista.** Uma linha como "12 fotos · 9 com informações escondidas · 1 ainda não suportada", com a ação "Limpar lista" à direita.
4. **Lista (`TreeView`).**
   - **Linha de arquivo:**
     - miniatura de 48 px;
     - nome do arquivo, com a pasta em tamanho menor;
     - etiquetas das categorias encontradas (até 3, mais "+N");
     - estado à direita: "Analisando…", "Nada escondido", "Ainda não suportado", "Arquivo danificado"; depois da limpeza, "Verificado" com o selo, "Com ressalvas" ou "Não limpou".
   - **Ao expandir:**
     - uma linha por categoria: ícone, nome, valor humano e "sai" ou "fica";
     - para resultados com ressalva ou falha, as notas e violações em português;
     - a linha "Detalhes técnicos", que ao expandir lista cada achado com rótulo, detalhe e local (`where`) exatamente como o motor informa.
   - **Menu da linha** (clique direito ou tecla Menu): "Mostrar no Explorer", "Limpar de novo", "Remover da lista".
5. **Barra de ação,** fixa embaixo da página.
   - **Seletor de modo:** `SegmentedWidget` com Privacidade, Fotógrafo e Máximo. Abaixo dele, uma linha que descreve o modo atual.
   - **Barra de progresso e "Cancelar",** durante a análise ou a limpeza.
   - **Botão principal:** `PrimaryPushButton` "Limpar N fotos". Fica desativado quando não há nada a limpar.

Estados vazios e de carga (decisão):
- **Lista vazia:** só o cabeçalho e a área de soltar, grande.
- **Arquivo sem nada a remover no modo atual:** conta no botão mesmo assim. Limpar gera a cópia verificada, o que é útil para ter o selo.

### 5.3 Textos dos modos (decisão)
- **Privacidade (recomendado):** "Tira tudo o que identifica você: local, aparelho, datas, nomes e marcas de IA. Mantém só o necessário para a foto aparecer certa (orientação e cores)."
- **Fotógrafo:** "Como Privacidade, mas mantém câmera, lente e as configurações da foto (ISO, abertura e velocidade)."
- **Máximo:** "Tira tudo, inclusive orientação e perfil de cor. A foto pode aparecer girada ou com as cores um pouco diferentes."

### 5.4 Categorias em português (decisão)

| Categoria do motor | Nome na interface | Valor humano mostrado |
|---|---|---|
| `location` | Localização | "onde a foto foi tirada" |
| `identity` | Dados pessoais | o nome ou número de série encontrado (detalhe do achado) |
| `ai_provenance` | Marca de IA | "Gerada por IA" + o gerador, quando o detalhe traz `generator:` (ex.: "OpenAI") |
| `time` | Data e hora | a data encontrada, formatada dd/mm/aaaa hh:mm quando possível |
| `software` | Programas usados | o nome do programa (detalhe) |
| `text` | Textos e comentários | "descrições, legendas ou comentários" |
| `extras` | Miniaturas e extras | "miniatura escondida ou dados extras" |
| `device` | Aparelho | marca e modelo (detalhes de Make/Model/Lens) |
| `camera_settings` | Configurações da câmera | "ISO, abertura, velocidade…" |
| `appearance` | Orientação e cores | "orientação e perfil de cor" |
| `unknown` | Dados desconhecidos | "informação que o Siftool não reconhece" |

A regra de exibição vive em `presentation.py` e tem testes. Quando não houver detalhe utilizável, aparece a frase genérica da tabela.

### 5.5 Mensagens de estado e erro (decisão)

| Situação | Texto na linha | Texto ao expandir |
|---|---|---|
| `Status.VERIFIED` | Verificado ✓ (selo) | "Cópia salva em <pasta>. Nada do que o modo proíbe ficou no arquivo." |
| `Status.WARNINGS` | Com ressalvas | "A cópia foi salva, mas havia uma estrutura que o Siftool não conhece e precisou ficar para a imagem não quebrar." + notas |
| `VerificationFailedError` | Não limpou | "A verificação encontrou dados que não saíram, por isso nenhuma cópia foi salva." + violações |
| `UnsupportedFormatError` | Ainda não suportado | "Fotos HEIC e vídeos chegam numa próxima versão." (ou "Formato não suportado" para os demais) |
| `CorruptFileError` | Arquivo danificado | "O arquivo tem uma estrutura inválida; limpar assim não seria seguro." |
| `UnsafeFileError` | Grande demais | "Arquivos acima de 512 MB ainda não são aceitos." |
| `FileNotFoundError` | Não encontrado | "O arquivo foi movido ou apagado." |
| `PermissionError` | Sem permissão | "O Windows não deixou ler o arquivo ou gravar a cópia nesta pasta." |
| Outro `OSError` | Erro ao ler ou gravar | a mensagem do sistema |

O selo só aparece em `Status.VERIFIED`.

### 5.6 Configurações (decisão)
Um grupo "Limpeza":
- **Modo padrão:** Privacidade, Fotógrafo ou Máximo.
- **Onde salvar as cópias:** ao lado do original (padrão) ou numa pasta escolhida. Um `PushSettingCard` abre a escolha de pasta e mostra o caminho atual.

Um grupo "Aparência":
- **Tema:** Seguir o Windows, Claro ou Escuro.

### 5.7 Sobre (decisão)
- As versões do programa e do motor.
- As promessas, em 4 linhas: offline, original intocado, sem recompressão, selo só quando verificado.
- A licença GPLv3.
- Os créditos: PySide6/Qt (LGPL) e QFluentWidgets (GPLv3).
- Uma nota de que HEIC e vídeo chegam numa próxima versão.

## 6. Teclado e acessibilidade (decisão)
- **Ordem de foco:** "Adicionar fotos" → "Adicionar pasta" → lista → modo → "Limpar".
- **Na lista:** setas navegam, Enter ou → expande, ← recolhe, Delete remove e a tecla Menu abre o menu da linha.
- **Atalhos:** Ctrl+O adiciona fotos, Ctrl+Shift+O adiciona pasta e Ctrl+Enter limpa.
- **Leitor de tela:** todo controle só com ícone tem dica e nome acessível, e as linhas expõem um texto que resume nome, estado e categorias.
- **Estado nunca só por cor:** sempre vai junto com texto e ícone.
- **Textos longos:** nomes longos são cortados com reticências e mostram o nome inteiro na dica.

## 7. Privacidade da própria interface (decisão)
- As miniaturas e os relatórios ficam só em memória. Nada é gravado em cache no disco.
- As preferências guardam apenas o modo, a pasta de saída e o tema. Não há lista de arquivos recentes.
- Não há rede em nenhum momento: nada de telemetria, verificação de atualização ou fontes externas.

## 8. Testes

- **Sem Qt (pytest puro):**
  - `batch.py` (movido da linha de comando, com os testes atuais adaptados);
  - `presentation.py`: nomes, valores humanos, "sai/fica" por modo, textos de estado e de erro para cada exceção;
  - `entries.py`: as transições de estado.
- **Com Qt** (`pytest-qt`, `QT_QPA_PLATFORM=offscreen`):
  - o modelo: adicionar, deduplicar, analisar com o motor real sobre os fixtures, expandir, trocar o modo, limpar e cancelar;
  - o `JobRunner`, com o limite de concorrência e sem tocar no modelo fora da thread principal;
  - a página: soltar arquivos via `QMimeData`, o botão ativando e desativando, a contagem e o `InfoBar`;
  - o critério 1 com o PNG de amostra (quando existir em `tests/samples-local/`);
  - o critério 2 com 1.000 arquivos gerados em pasta temporária.
- **Visual:** capturas fora da tela (skill `qt-testing`) da página vazia, com lista, expandida, depois da limpeza, nos temas claro e escuro. São conferidas em cada revisão de tarefa visual, sem comparação de pixels automática.
- **Motor:** a suíte atual continua passando.
- **Isolamento:** um teste garante que `import siftool` não carrega `PySide6`.

## 9. Riscos

| Risco | Mitigação |
|---|---|
| A API do QFluentWidgets muda entre versões | Versão mínima fixada; cada tarefa confere a API instalada antes de usá-la (regra da skill). |
| Desenhar linhas com delegate dá trabalho | O delegate é isolado em `delegate.py`, e as capturas de tela revisam cada mudança. |
| Decodificar miniaturas de arquivos hostis | A miniatura usa só o decodificador do Qt, em thread separada, e não interfere na limpeza. Uma falha vira ícone genérico. |
| Pasta enorme consome memória | Análise limitada a `min(4, núcleos)` por vez; o relatório guarda só os achados (texto), sem os bytes. |
| O `siftool.py` na raiz faz sombra ao pacote | Os testes rodam o programa fora da raiz, e `python -m siftool_gui` é documentado para rodar fora dela. |
