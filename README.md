# ◈ SPACE-FINDX — Astrometric NEO Detection Pipeline

> Pipeline astrométrico de alta precisão para detecção de **Objetos Próximos da Terra (NEOs)** e transientes astronômicos em imagens CCD.

---

## Visão Geral

**space-findx** é um pipeline modular orientado a objetos que processa séries de imagens FITS brutas e gera submissões no padrão **ADES/XML do MPC (Minor Planet Center)**. O sistema implementa as melhores práticas de redução de dados astronômicos para levantamento de NEOs.

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

### Interface Gráfica (GUI)

Abra o arquivo `index.html` em qualquer navegador moderno:

```bash
# Windows
start index.html

# Linux/macOS
xdg-open index.html   # Linux
open index.html       # macOS
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
├── index.html                  # Interface gráfica (GUI)
├── style.css                   # Estilos da interface
├── app.js                      # Lógica da interface
├── requirements.txt            # Dependências Python
├── config/
│   └── pipeline_config.yaml   # Configuração do pipeline
└── pipeline/
    ├── __init__.py
    ├── pipeline.py             # Orquestrador principal
    ├── calibration.py          # Calibração CCD (Bias/Dark/Flat)
    ├── subtraction.py          # Subtração ZOGY
    ├── detection.py            # Detecção de fontes (DAOStarFinder)
    ├── trajectory.py           # Linkagem de trajetórias
    └── ades_exporter.py        # Exportação ADES XML (MPC)
```

---

## Restrições Científicas

> **Estas restrições são intencionais e garantem a integridade estatística dos dados.**

- ✅ Todos os arrays são mantidos em **`float64`** — `float32` e `int16` são proibidos
- ✅ Transformadas de Fourier (ZOGY) via **`numpy.fft`** exclusivamente
- ❌ **OpenCV é proibido** — introduz interpolações que distorcem a distribuição de ruído
- ✅ Catálogo astrométrico: **Gaia EDR3** (precisão ~0.02 mas)
- ✅ Saída em formato **ADES XML** compatível com o esquema `submit.xsd` (MPC/IAU 2017)
- ✅ Política de privacidade PII: nomes pessoais **nunca** devem aparecer em campos `<comment>` do XML

---

## Configuração

Edite `config/pipeline_config.yaml` para ajustar os parâmetros. Todos os parâmetros também podem ser sobrescritos pela interface gráfica.

Veja o [Manual de Usuário](MANUAL.md) para a descrição detalhada de cada parâmetro.

---

## Saída

O pipeline gera na pasta de saída:

- `ades_submission_YYYY-MM-DD_OBS.xml` — Submissão no padrão MPC/ADES
- Logs com timestamps de cada etapa

---

## Licença

MIT — Veja `LICENSE` para detalhes.
