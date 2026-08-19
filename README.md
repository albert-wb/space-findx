# ◈ SPACE-FINDX — Astrometric NEO Detection Pipeline

> Pipeline astrométrico de alta precisão para detecção de **Objetos Próximos da Terra (NEOs)** e transientes astronômicos em imagens CCD.

---

## Visão Geral

**space-findx** é um pipeline modular orientado a objetos que processa séries de imagens FITS brutas e gera submissões no padrão **ADES/XML do MPC (Minor Planet Center)**. O sistema implementa as melhores práticas de redução de dados astronômicos para levantamento de NEOs, com duas interfaces gráficas: uma **web** (`index.html`) e uma **desktop** (`CustomTkinter`).

```
frames FITS → Calibração CCD → Astrometria WCS → Subtração ZOGY → Detecção + Vetação → Linkagem → ADES XML
```

### Método científico

| Etapa | Algoritmo | Biblioteca |
|---|---|---|
| Calibração | Bias/Dark/Flat + mascaramento de hot pixels | `ccdproc` · `astropy` |
| Astrometria | Ajuste WCS + polinômio SIP + catálogo Gaia EDR3 | `astropy.wcs` · `astroquery` |
| Subtração | ZOGY (Proper Image Subtraction via FFT) | `numpy.fft` |
| Detecção | DAOStarFinder + filtros morfológicos | `photutils` |
| Linkagem | Ajuste linear χ² em coordenadas celestes | `scipy` |
| Exportação | ADES XML — esquema `submit.xsd` (MPC/IAU 2017) | `astropy` |

---

## Requisitos do Sistema

- **Python** ≥ 3.11
- **OS:** Windows, Linux ou macOS

### Dependências Python

```
astropy>=5.3.0
astropy-healpix>=0.7
photutils>=1.9.0
numpy>=1.24.0
ccdproc>=2.4.0
astroquery>=0.4.6
reproject>=0.11.0
pyyaml>=6.0
scipy>=1.11.0
customtkinter>=5.2.0
```

---

## Instalação

```bash
# Clone o repositório
git clone https://github.com/seu-usuario/space-findx.git
cd space-findx

# Instale as dependências
pip install -r requirements.txt
```

---

## Uso Rápido

### Verificar a instalação com dados de exemplo

Antes de usar dados reais, confirme que o pipeline funciona de ponta a ponta.
O script gera um conjunto FITS sintético (5 frames de ciência + 1 referência)
contendo um objeto em movimento com taxa **conhecida**, roda o pipeline
completo e compara o resultado com o valor injetado:

```bash
python verificar_instalacao.py
```

Saída esperada:

```
[3/3] Conferindo o resultado...
      Recuperado: mu_ra=-41.99"/hr (erro 0.01), mu_dec=27.01"/hr (erro 0.01)
      chi2_red=0.00064 em 5 frames
      ADES exportado: ades_submission_2024-01-17_W86.xml
RESULTADO: OK - pipeline funcionando de ponta a ponta.
```

Os arquivos são criados em um diretório temporário e removidos ao final — nada
em `dados/` é tocado. Para escrever o mesmo dataset em `dados/` e explorá-lo na
interface:

```bash
python -m pipeline.sample_data
```

### Interface Web (GUI principal)

Suba o backend e abra a interface que ele mesmo serve:

```bash
npm run backend          # ou: uvicorn server:app --reload
# depois abra http://localhost:8000
```

Servir a interface pelo próprio backend faz a página e a API compartilharem a
mesma origem, o que mantém o app funcional quando ele é acessado de outra
máquina da rede ou através de um túnel (`npm run tunnel`).

O modo de desenvolvimento com Vite continua disponível (`npm start`): nesse
caso a página roda em `localhost:5173` e fala com o backend em
`localhost:8000`.

#### Carregando os dados de entrada

| Botão | O que faz |
|---|---|
| **+ LOAD SCIENCE FRAMES** | Lê os FITS já presentes em `dados/ciencia/` |
| **⇪ UPLOAD LOCAL .FIT(S)** | Envia arquivos do seu computador para a pasta correspondente |
| **+ LOAD REFERENCE FRAME** | Lê os FITS de `dados/referencia/` |
| **◈ CARREGAR FITS DE EXEMPLO** | Gera o dataset sintético de demonstração e o carrega nas galerias |

Extensões aceitas no upload: `.fits`, `.fit`, `.fts` e os comprimidos
`.fits.fz` (Rice) e `.fits.gz`. Arquivos vazios ou que não sejam FITS legíveis
são rejeitados com o motivo exibido no log — nunca salvos silenciosamente.

Se já houver arquivos seus nas pastas de entrada quando o dataset de exemplo
for carregado, a interface pergunta antes de movê-los para
`dados/arquivados_<data>/`. Os arquivos são **movidos, nunca apagados**.

A interface web contém **4 abas**:

| Aba | Função |
|---|---|
| **[T] SYSTEM LOGS** | Terminal em tempo real com saída do pipeline e diagrama de fluxo |
| **[C] CANDIDATES** | Tabela interativa de NEOs/transientes detectados |
| **[X] ADES XML** | Pré-visualização e download do relatório de submissão ao MPC |
| **[F] FITS VIEWER** | Visualizador astrométrico com 4 camadas de redução (S/R/D/Scorr) |

### Interface Desktop (CustomTkinter)

```bash
python run_pipeline.py
```

### Via Python (headless)

```python
import yaml
from pathlib import Path
from pipeline.pipeline import SpaceFindXPipeline

# Carregar configuração
with open("config/pipeline_config.yaml") as f:
    config = yaml.safe_load(f)

# Instanciar e executar
pipeline = SpaceFindXPipeline(config)
ades_path = pipeline.run(
    science_dir=Path("dados/ciencia/"),
    reference_fits=Path("dados/referencia/reference.fits"),
    output_dir=Path("saida/"),
)

print(f"Submissão ADES gerada em: {ades_path}")
```

---

## Estrutura do Projeto

```
space-findx/
├── index.html                  # Interface web (GUI principal)
├── style.css                   # Estilos da interface web
├── app.js                      # Lógica da interface web
├── run_pipeline.py             # Ponto de entrada da GUI desktop
├── requirements.txt            # Dependências Python
├── README.md                   # Este arquivo
├── MANUAL.md                   # Manual detalhado do usuário
├── gui/
│   ├── __init__.py             # Exports do pacote GUI
│   ├── main_window.py          # Janela principal (CustomTkinter)
│   └── fits_viewer.py          # Widget FITS Viewer (matplotlib + astropy.wcs)
├── config/
│   └── pipeline_config.yaml    # Configuração do pipeline
├── pipeline/
│   ├── __init__.py
│   ├── pipeline.py             # Orquestrador principal
│   ├── calibration.py          # Calibração CCD (Bias/Dark/Flat)
│   ├── subtraction.py          # Subtração ZOGY
│   ├── detection.py            # Detecção de fontes (DAOStarFinder)
│   ├── trajectory.py           # Linkagem de trajetórias
│   ├── ades_exporter.py        # Exportação ADES XML (MPC)
│   ├── fits_utils.py           # Descoberta/leitura robusta de FITS e WCS
│   ├── sample_data.py          # Gerador do dataset sintético de demonstração
│   └── reference_fetcher.py    # Download automático de referência (SkyView/DSS)
├── verificar_instalacao.py     # Teste de ponta a ponta com dados de exemplo
├── dados/
│   ├── ciencia/                # Frames de ciência (FITS)
│   └── referencia/             # Frame(s) de referência (FITS)
├── output/                     # Diretório padrão de saída
└── saida/                      # Saída alternativa (batch)
```

---

## Funcionalidades da Interface Web

### Seletor Visual de Frames (Gallery View)

Os frames FITS de ciência e referência são carregados em uma **galeria de thumbnails** com pré-visualização. Os thumbnails são gerados com:

- **Binning 8×8** (`data[::8, ::8]`) para prevenção de `MemoryError` — reduz 128 MB → 2 MB por frame
- **`fits.open(mmap=True)`** — memory-mapped I/O que não carrega o arquivo inteiro em RAM
- **`ZScaleInterval`** — rejeita raios cósmicos/estrelas saturadas no thumbnail, evitando "cegueira dinâmica"

### Gerenciamento de Saída (Output Directory)

O pipeline salva **obrigatoriamente** dois subprodutos no diretório de saída:

| Subproduto | Formato | Descrição |
|---|---|---|
| `ades_submission_YYYY-MM-DD_<obs>.xml` | XML ADES IAU 2017 | Relatório astrométrico para submissão ao MPC |
| `science_YYYY-MM-DD_diff.fits` | FITS PrimaryHDU | Imagem de diferença com header WCS integralmente preservado |

### FITS Viewer com Camadas de Redução

O visualizador permite alternar entre 4 camadas de redução via botões **S / R / D / Scorr**:

| Camada | Conteúdo | Range típico |
|---|---|---|
| **S** (Science) | Imagem CCD calibrada com fontes astronômicas e transientes | 900–2000 ADU |
| **R** (Reference) | Template de referência sem transiente | 900–2000 ADU |
| **D** (Difference) | D(x,y) = S − R via subtração ZOGY | −50 a +50 ADU |
| **Scorr** | Scorr(x,y) = D / σ_D — mapa de razão sinal/ruído | −3 a +8 σ |

Os **bounding boxes** dos candidatos permanecem ancorados via WCS compartilhado entre todas as camadas.

---

## Restrições Científicas

> **Estas restrições são intencionais e garantem a integridade estatística dos dados.**

- ✅ Todos os arrays são mantidos em **`float64`** — `float32` e `int16` são proibidos
- ✅ Transformadas de Fourier (ZOGY) via **`numpy.fft`** exclusivamente
- ❌ **OpenCV é proibido** — introduz interpolações que distorcem a distribuição de ruído
- ✅ Catálogo astrométrico: **Gaia EDR3** (precisão ~0.02 mas)
- ✅ Saída em formato **ADES XML** compatível com o esquema `submit.xsd` (MPC/IAU 2017)
- ✅ Política de privacidade PII: nomes pessoais **nunca** devem aparecer em campos `<comment>` do XML
- ✅ Thumbnails FITS via **`mmap=True`** + binning + **`ZScaleInterval`** para prevenção de MemoryError

---

## Configuração

Edite `config/pipeline_config.yaml` para ajustar os parâmetros. Todos os parâmetros também podem ser sobrescritos pela interface gráfica.

Veja o [Manual de Usuário](MANUAL.md) para a descrição detalhada de cada parâmetro.

---

## Saída

O pipeline gera na pasta de saída:

- `ades_submission_YYYY-MM-DD_OBS.xml` — Submissão no padrão MPC/ADES
- `science_YYYY-MM-DD_diff.fits` — Imagem de diferença FITS com WCS preservado
- Logs com timestamps de cada etapa

---

## Licença

MIT — Veja `LICENSE` para detalhes.

---

## Solução de Problemas

### "Não consigo adicionar meu arquivo FITS de referência"

A causa mais comum é a extensão. O upload aceita `.fits`, `.fit`, `.fts` e os
comprimidos `.fits.fz` e `.fits.gz` — arquivos de levantamentos como
Pan-STARRS, LCO e DECam costumam vir como `.fits.fz`. Se o arquivo for
rejeitado, o motivo exato aparece no log do sistema (aba **[T] SYSTEM LOGS**) e
no alerta: extensão não reconhecida, arquivo de 0 byte ou cabeçalho FITS
ilegível.

Um arquivo que aparece na pasta mas não na galeria indica cabeçalho corrompido;
nesse caso ele é listado com o erro em vez de ser omitido.

### "A galeria diz que a pasta está vazia, mas o arquivo está lá"

Verifique se o backend está no ar (`npm run backend`). A interface distingue
três situações e informa qual delas ocorreu: backend inacessível, backend
respondeu com erro (mostra a mensagem do servidor) e pasta realmente vazia.

### Frames com WCS que o astropy rejeita

Alguns arquivos trazem, ao lado de um WCS válido, palavras-chave de convenções
antigas (`CNPIX1`/`CNPIX2` do formato DSS, por exemplo). A wcslib aborta com
`SingularMatrixError: PCi_ja matrix is singular` e o astropy descarta o WCS
inteiro. O pipeline detecta esse caso e reconstrói o WCS apenas com as
palavras-chave do padrão FITS (preservando CD/PC, PV e distorção SIP),
registrando no log quando isso acontece.

### Muitas tracklets "confirmadas" em dados reais

Um campo real produz pouquíssimos objetos em movimento. Dezenas de tracklets
quase sempre significam falsos positivos, tipicamente por:

- **Referência inadequada** — uma placa DSS baixada automaticamente é rasa
  demais para servir de template a um frame moderno e profundo; o resíduo da
  subtração fica dominado por artefatos.
- **`max_speed_arcsec_hr` alto demais** — o padrão de 7200"/hr dá um raio de
  busca que cobre o campo inteiro, permitindo encadear detecções não
  relacionadas.

O pipeline emite um aviso explícito quando a colheita é implausível. **Sempre
inspecione visualmente os candidatos antes de submeter ao MPC.**

### Referência baixada automaticamente é grosseira demais

Quando não há frame em `dados/referencia/`, o pipeline baixa um recorte DSS via
SkyView com amostragem compatível com o frame de ciência. Se a referência ainda
assim for mais grosseira, ela é reprojetada para a grade da ciência (e não o
contrário), preservando a resolução das imagens de entrada. O log informa o
fator de diferença de escala.
