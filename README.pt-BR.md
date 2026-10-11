<div align="center">

[English](README.md) · **Português (Brasil)**

<img src="./siftool_logo.png" width="96" height="96" alt="Logo do Siftool">

# Siftool — Remova Metadados, Localização (GPS) e Marcas de IA das Suas Fotos

**Programa gratuito para Windows que tira a localização, os dados pessoais e as marcas de IA das suas fotos — sem perder qualidade e sem internet.**<br>
O original nunca é alterado · nada é recomprimido · a cópia só ganha o selo *Verificado* depois de conferida

[![Última versão](https://img.shields.io/github/v/release/FFloriani/siftool?style=flat-square&label=vers%C3%A3o)](https://github.com/FFloriani/siftool/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/FFloriani/siftool/total?style=flat-square&label=downloads)](https://github.com/FFloriani/siftool/releases)
[![Windows 10 e 11](https://img.shields.io/badge/Windows-10%20%7C%2011-0078D4?style=flat-square&logo=windows)](https://github.com/FFloriani/siftool/releases/latest)
[![Licença GPLv3](https://img.shields.io/badge/licen%C3%A7a-GPLv3-blue?style=flat-square)](LICENSE)
[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-apoiar-ffdd00?style=flat-square&logo=buy-me-a-coffee&logoColor=black)](https://buymeacoffee.com/1b9hbqniv1)

**[⬇ Baixar o instalador](https://github.com/FFloriani/siftool/releases/download/v2.0.0-beta.1/Siftool-2.0.0-beta.1-setup.exe)** (22 MB)
&nbsp;·&nbsp;
**[⬇ Baixar a versão portátil](https://github.com/FFloriani/siftool/releases/download/v2.0.0-beta.1/Siftool-2.0.0-beta.1-portable.zip)** (27 MB)
&nbsp;·&nbsp;
[Todas as versões](https://github.com/FFloriani/siftool/releases)

<img src="docs/images/janela-clara.png" width="820" alt="Siftool, programa para remover metadados de fotos no Windows, mostrando a localização (GPS), os dados pessoais, a data, o celular e as marcas de IA encontrados em cada foto">

</div>

## Por que tirar os metadados das fotos?

Toda foto carrega informações escondidas além da imagem — os **metadados EXIF, XMP e IPTC**. Uma foto enviada como arquivo pode revelar:

- **onde** foi tirada: as coordenadas de GPS, muitas vezes com precisão de poucos metros;
- **quando**: a data e a hora exatas;
- **quem** tirou e **com o quê**: o seu nome, o modelo do celular ou da câmera e o número de série;
- o **programa** que editou a foto;
- nas imagens feitas por IA, a **marca de procedência C2PA** (*Content Credentials*), que o ChatGPT (OpenAI), o Adobe Firefly e outras ferramentas gravam para dizer que a imagem foi gerada por IA — além dos prompts que alguns geradores gravam dentro do PNG.

O Siftool mostra exatamente o que cada foto esconde, em português claro, e grava uma **cópia limpa** ao lado do original.

## O que ele faz

- **Remove metadados sem perder qualidade** — o Siftool não decodifica nem recomprime a imagem. Ele copia os bytes da foto exatamente como estão e tira só os blocos de metadados: os pixels saem idênticos e o JPEG não perde qualidade.
- **Tira a localização (GPS), o EXIF, o XMP, o IPTC, comentários e miniaturas escondidas** com um clique, de uma foto ou de pastas inteiras.
- **Tira as marcas de IA**: os manifestos C2PA / Content Credentials (como o do ChatGPT) e os parâmetros de geração que ficam nos textos do PNG.
- **Selo "Verificado" de verdade**: depois de gravar, o Siftool relê a cópia do zero e confere que nada do que o modo proíbe ficou lá. Se sobrou alguma coisa, a cópia não é salva e ele diz o motivo.
- **O original nunca é alterado** — a cópia limpa sai como `foto_clean.jpg`; nada é sobrescrito.
- **100% offline e privado** — sem internet, sem telemetria, sem conta. As fotos nunca saem do seu computador.
- **Três modos**: Privacidade, Fotógrafo (mantém câmera e lente) e Máximo.
- **Integrado ao Windows**: **Limpar com Siftool** no clique direito, arrastar e soltar, tema claro e escuro, instalador sem pedir administrador ou versão portátil.
- **Linha de comando** para scripts e automação, com saída em JSON.
- **Gratuito e de código aberto** (GPLv3).

## Baixar e instalar

Precisa de **Windows 10 ou 11 (64 bits)**. Não precisa instalar Python nem nada mais.

| | |
|---|---|
| **Instalador** · [`Siftool-2.0.0-beta.1-setup.exe`](https://github.com/FFloriani/siftool/releases/download/v2.0.0-beta.1/Siftool-2.0.0-beta.1-setup.exe) | Instala só para o seu usuário, **sem pedir senha de administrador**. Cria o atalho no menu Iniciar e, se você deixar marcado, põe **Limpar com Siftool** no clique direito das fotos e das pastas. Para remover: Configurações → Aplicativos → Siftool → Desinstalar. |
| **Portátil** · [`Siftool-2.0.0-beta.1-portable.zip`](https://github.com/FFloriani/siftool/releases/download/v2.0.0-beta.1/Siftool-2.0.0-beta.1-portable.zip) | Não instala nada. Extraia a pasta `Siftool` num caminho **curto** (como `C:\Siftool` ou um pendrive) e abra o `Siftool.exe`. As configurações ficam dentro da própria pasta. Para remover, apague a pasta. |

> [!NOTE]
> **"O Windows protegeu o computador"**: o Siftool ainda não tem assinatura digital, então na primeira vez o Windows SmartScreen mostra esse aviso. Clique em **Mais informações** → **Executar assim mesmo**. Com o *Controle inteligente de aplicativos* do Windows 11 ligado, o Windows pode bloquear programas sem assinatura sem oferecer essa opção.

<details>
<summary>Mais detalhes sobre a instalação</summary>

- O instalador grava em `%LOCALAPPDATA%\Programs\Siftool`. As configurações ficam em `%APPDATA%\Siftool` e continuam lá depois de desinstalar.
- No Windows 11, **Limpar com Siftool** aparece em **Mostrar mais opções** no clique direito. O Windows esconde esse item quando mais de 15 arquivos estão selecionados: para muitas fotos, use o item da pasta ou arraste as fotos para a janela.
- Na versão portátil, o caminho completo da pasta precisa ser curto: com cerca de 200 caracteres ou mais (pastas muito fundas, como as do OneDrive), o programa não abre.
- Abrir várias fotos pelo clique direito manda todas para a mesma janela.
- Os códigos SHA-256 dos arquivos estão na [página da versão](https://github.com/FFloriani/siftool/releases/tag/v2.0.0-beta.1).

</details>

## Como usar

1. **Arraste** fotos ou pastas para a janela (ou use **Adicionar fotos** / **Adicionar pasta**, ou o clique direito do Windows).
2. **Veja** o que cada foto esconde. Clique numa foto para ver o que **sai** e o que **fica** no modo escolhido.
3. Escolha o **modo** e clique em **Limpar**. Cada foto ganha o selo **Verificado** e a cópia limpa aparece ao lado do original. **Abrir pasta** leva direto até ela.

<div align="center">
<img src="docs/images/limpeza-concluida.png" width="820" alt="Fotos limpas e verificadas pelo Siftool, com a marca de IA (C2PA) removida de uma imagem do ChatGPT">
</div>

### Os três modos

| Modo | Tira | Mantém |
|---|---|---|
| **Privacidade** (padrão) | Localização, dados pessoais, datas, aparelho, configurações da câmera, programas usados, textos e comentários, miniaturas escondidas, marcas de IA e qualquer dado desconhecido | Só o necessário para a foto aparecer certa: orientação e perfil de cor |
| **Fotógrafo** | O mesmo que Privacidade, menos o aparelho e as configurações da câmera | Orientação, cores, câmera, lente, ISO, abertura e velocidade |
| **Máximo** | Tudo, inclusive orientação e perfil de cor | Nada (a foto pode aparecer girada ou com as cores um pouco diferentes) |

### Formatos

| Formato | Situação |
|---|---|
| JPG / JPEG | ✅ Limpa |
| PNG (inclusive PNG animado) | ✅ Limpa — incluindo a marca C2PA e os prompts de IA |
| WEBP (com perda, sem perda e animado) | ✅ Limpa, sem recomprimir |
| HEIC (fotos do iPhone) e vídeos MP4 / MOV | ⏳ Aparecem como "Ainda não suportado" — chegam numa próxima versão |

<details>
<summary>Tema escuro</summary>

O Siftool segue o tema do Windows (claro ou escuro), ou o que você escolher em Configurações.

<img src="docs/images/janela-escura.png" width="820" alt="Siftool no tema escuro">
</details>

## Por que dá para confiar no selo

- **Leitura em nível de bytes.** O Siftool entende a estrutura de cada formato (segmentos JPEG, chunks PNG, chunks RIFF do WEBP) e classifica cada bloco como imagem ou metadado. O que é imagem é copiado byte a byte; o que é metadado é removido ou reescrito do zero, só com os campos permitidos.
- **Verificação que falha fechada.** Depois de gravar, a cópia é lida de novo como se fosse um arquivo desconhecido, varrida em busca de assinaturas de metadados (EXIF, XMP, C2PA, coordenadas) e comparada com o original para garantir que a imagem em si não mudou. Qualquer dúvida vira "Não limpou", nunca "Verificado".
- **Estruturas conferidas.** Blocos que contam como imagem precisam ter o tamanho, a ordem e a quantidade que o formato manda — isso fecha os truques de esconder dados dentro de estruturas que parecem válidas.
- **Testado.** 909 testes automáticos, incluindo arquivos corrompidos gerados aleatoriamente, arquivos montados de propósito para tentar enganar a verificação e uma imagem real gerada pelo ChatGPT com marca C2PA.

Limite conhecido: dados escondidos **dentro dos próprios pixels** (esteganografia ou marcas d'água invisíveis como o SynthID) não são detectados. O Siftool remove metadados; ele não altera a imagem.

## Linha de comando

O mesmo motor também roda no terminal, para scripts e automação (Python 3.11 ou mais novo):

```bash
pip install git+https://github.com/FFloriani/siftool.git

siftool scan foto.jpg                       # mostra o que a foto contém, por categoria
siftool clean fotos/ --mode photographer    # limpa uma pasta inteira (modos: privacy, photographer, maximum)
siftool clean foto.jpg --out limpas/        # grava as cópias em outra pasta
siftool verify foto_clean.jpg --original foto.jpg
```

Use `--json` em qualquer comando para uma saída legível por máquina (schema 2). O código de saída é `0` quando tudo foi verificado, `1` com ressalvas e `2` em caso de falha.

## Perguntas frequentes

<details>
<summary><b>Como tirar a localização (GPS) de uma foto no Windows?</b></summary>

Abra o Siftool, arraste a foto para a janela e clique em **Limpar** no modo *Privacidade*. A cópia limpa (`foto_clean.jpg`) não tem coordenadas de GPS, data, aparelho nem nome. Você também pode clicar com o botão direito na foto, no Explorador de Arquivos, e escolher **Limpar com Siftool**.
</details>

<details>
<summary><b>Como tirar a marca de IA (C2PA) de uma imagem do ChatGPT?</b></summary>

As imagens geradas pelo ChatGPT carregam um manifesto C2PA (*Content Credentials*) que as identifica como feitas por IA. O Siftool detecta essa marca — a linha da foto mostra a etiqueta **Marca de IA** — e a remove em qualquer modo, sem recomprimir a imagem.
</details>

<details>
<summary><b>Tirar os metadados diminui a qualidade da foto?</b></summary>

Não. O Siftool não decodifica nem recomprime a imagem: os bytes da foto são copiados sem mudança, então o JPEG mantém exatamente a mesma qualidade. Salvar de novo num editor de imagens, ao contrário, recomprime a foto a cada vez.
</details>

<details>
<summary><b>Ele altera ou apaga a minha foto original?</b></summary>

Nunca. O Siftool grava um arquivo novo ao lado do original (com `_clean` no nome) e não sobrescreve nada.
</details>

<details>
<summary><b>É seguro? Ele envia as minhas fotos para algum lugar?</b></summary>

O Siftool funciona totalmente offline: não usa internet, não tem telemetria e não pede conta. O código-fonte é público, sob a licença GPLv3.
</details>

<details>
<summary><b>Ele remove marcas d'água invisíveis de IA (como o SynthID)?</b></summary>

Não. Marcas d'água invisíveis ficam nos pixels, não nos metadados. O Siftool remove só os metadados e nunca altera a imagem.
</details>

## Para desenvolvedores

```bash
git clone https://github.com/FFloriani/siftool.git
cd siftool
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[gui,test]"
.venv\Scripts\python.exe -m pytest           # 909 testes, ~30 s
.venv\Scripts\siftool-gui.exe                # abre o programa
```

- `src/siftool/` — o motor: só biblioteca padrão do Python, sem dependências.
- `src/siftool_gui/` — a janela, em PySide6 + [QFluentWidgets](https://qfluentwidgets.com).
- `packaging/` — o instalador e a versão portátil. Instale o [Inno Setup 6](https://jrsoftware.org/isinfo.php), rode `pip install -e ".[gui,build]"` e depois `.venv\Scripts\python.exe packaging\build.py`. O resultado fica em `dist\`.
- `docs/superpowers/` — os documentos de design e os planos de cada etapa.

> [!WARNING]
> Não rode `python -m siftool_gui` dentro da pasta raiz do repositório: lá, o `siftool.py` antigo faz sombra ao pacote novo.

<details>
<summary>O programa antigo (Tkinter)</summary>

O Siftool 1.x era um único arquivo, `siftool.py`, com interface em Tkinter. Ele continua no repositório por enquanto, porque ainda tem recursos que o programa novo não tem (limpeza de vídeo com ffmpeg e editor de metadados), e será aposentado quando HEIC e vídeo chegarem ao programa novo. Para gerá-lo, use o `build.bat`, que grava em `dist\legacy\siftool`.
</details>

## Próximos passos

- [ ] Fotos HEIC do iPhone e vídeos MP4 / MOV
- [ ] Assinatura digital do instalador (sem o aviso do Windows)
- [ ] Interface em inglês

## Apoie

Se o Siftool foi útil para você, considere apoiar o desenvolvimento:

<a href="https://buymeacoffee.com/1b9hbqniv1"><img src="https://img.shields.io/badge/Buy%20Me%20a%20Coffee-apoiar-ffdd00?style=for-the-badge&logo=buy-me-a-coffee&logoColor=black" alt="Buy Me a Coffee"></a>

## Licença

O Siftool é software livre, sob a **GNU General Public License versão 3 (GPLv3)** — veja [LICENSE](LICENSE). A interface usa o [QFluentWidgets](https://qfluentwidgets.com) (edição Community), que é GPLv3, e por isso o programa todo também é. As licenças dos outros componentes (Qt for Python, PyInstaller e outros) estão em [packaging/THIRD-PARTY-NOTICES.txt](packaging/THIRD-PARTY-NOTICES.txt).
