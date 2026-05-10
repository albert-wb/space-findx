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

### Interface Web (GUI principal)

Abra o arquivo `index.html` em qualquer navegador moderno:

```bash
# Windows
start index.html

# Linux/macOS
xdg-open index.html   # Linux
open index.html       # macOS
```

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
    reference_fits=Path("dados/referencia.fits"),
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
│   └── ades_exporter.py        # Exportação ADES XML (MPC)
├── dados/                      # Diretório de dados de entrada (FITS)
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
