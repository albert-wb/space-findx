# ◈ SPACE-FINDX — Manual do Usuário

> **Versão:** 1.0 | **Última atualização:** 2026-05

---

## Sumário

1. [Introdução](#1-introdução)
2. [Interface Gráfica](#2-interface-gráfica)
3. [Preparando os Dados de Entrada](#3-preparando-os-dados-de-entrada)
4. [Parâmetros do Pipeline](#4-parâmetros-do-pipeline)
5. [Etapas do Pipeline](#5-etapas-do-pipeline)
6. [Interpretando os Resultados](#6-interpretando-os-resultados)
7. [Exportação ADES e Submissão ao MPC](#7-exportação-ades-e-submissão-ao-mpc)
8. [Configuração Avançada (YAML)](#8-configuração-avançada-yaml)
9. [Uso via Linha de Comando (Headless)](#9-uso-via-linha-de-comando-headless)
10. [Erros Comuns e Soluções](#10-erros-comuns-e-soluções)

---

## 1. Introdução

O **space-findx** é um pipeline astrométrico para detecção de **Objetos Próximos da Terra (NEOs)** e transientes astronômicos em imagens CCD. Ele processa séries temporais de imagens no formato FITS e gera relatórios no padrão ADES/XML exigido pelo **Minor Planet Center (MPC)** da IAU.

### Fluxo de dados

```
Frames FITS brutos
        │
        ▼
   [1] CALIBRAÇÃO ─────── Bias Mestre + Dark Mestre + Flat Mestre
        │                  Mascaramento de hot pixels
        ▼
   [2] ASTROMETRIA ─────── Solução WCS via Gaia EDR3
        │                   Correção de distorção SIP
        ▼
   [3] SUBTRAÇÃO ZOGY ──── Proper Image Subtraction (no domínio de Fourier)
        │                   Imagem de significância Scorr
        ▼
   [4] DETECÇÃO ──────────── DAOStarFinder na imagem Scorr
        │                    Filtros: sharpness, roundness, elongação
        ▼
   [5] LINKAGEM ──────────── Ajuste linear χ² entre frames
        │                    Confirmação de tracklets (≥3 frames)
        ▼
   [6] EXPORTAÇÃO ADES ───── XML compatível com submit.xsd (MPC/IAU 2017)
```

---

## 2. Interface Gráfica

Abra o arquivo `index.html` em um navegador moderno (Chrome, Firefox, Edge). A interface é dividida em três áreas:

### Barra Lateral (esquerda) — Controles

| Seção | Função |
|---|---|
| **INPUT TARGET** | Carrega os frames de ciência e o frame de referência |
| **OUTPUT DIR** | Define o diretório de saída para o XML e logs |
| **PARÂMETROS** | Ativa/desativa módulos do pipeline |
| **LIMIARES** | Ajusta os limiares de detecção e trajetória |
| **INICIAR PIPELINE** | Executa o pipeline completo |

### Área Central — Visualização

| Aba | Conteúdo |
|---|---|
| **LOG DO PROCESSO** | Terminal em tempo real com todas as mensagens do pipeline |
| **CANDIDATOS DETECTADOS** | Tabela de NEOs/transientes com coordenadas, velocidade angular e χ² |
| **ADES XML** | Pré-visualização e download do arquivo de submissão ao MPC |

### Barra Inferior — Status

Exibe o estado atual do pipeline, o arquivo carregado e o progresso percentual.

---

## 3. Preparando os Dados de Entrada

### Frames de Ciência

- Formato: **FITS** (`.fits` ou `.fit`)
- Devem ser frames brutos (não reduzidos) ou pré-calibrados
- Organizados em um único **diretório**; o pipeline os ordena automaticamente por `DATE-OBS` no cabeçalho FITS
- Recomendado: mínimo de **3 frames** da mesma região do céu com intervalo de tempo entre eles

### Frame de Referência

- Um único arquivo FITS da **mesma região** do campo observado
- Idealmente de uma observação em data diferente (sem o objeto de interesse)
- Usado na subtração ZOGY para eliminar fontes estáticas (estrelas de campo)

### Frames de Calibração (opcionais mas recomendados)

| Frame | Descrição |
|---|---|
| **Bias Mestre** | Combinação mediana de exposições de zero segundos. Corrige o offset eletrônico do ADC. |
| **Dark Mestre** | Combinação mediana de exposições escuras (mesma duração que ciência). Corrige corrente de escuridão. |
| **Flat Mestre** | Combinação mediana de cúpula/crepúsculo, normalizada. Corrige variações de sensibilidade pixel a pixel. |

> **Dica:** Sem frames mestres, o pipeline ainda funciona, mas a sensibilidade de detecção será menor e haverá mais falsos positivos.

### Cabeçalho FITS obrigatório

O pipeline lê os seguintes campos do cabeçalho FITS:

| Keyword | Descrição | Exemplo |
|---|---|---|
| `DATE-OBS` | Data/hora de início da exposição (ISO 8601 UTC) | `2024-03-15T22:30:00.0` |
| `EXPTIME` | Tempo de exposição em segundos | `120.0` |
| `GAIN` *(opcional)* | Ganho do detector (e⁻/ADU) | `1.5` |
| `RDNOISE` *(opcional)* | Ruído de leitura (e⁻) | `8.0` |

---

## 4. Parâmetros do Pipeline

### Parâmetros on/off

| Parâmetro | Descrição | Recomendação |
|---|---|---|
| **ZOGY Subtraction** | Ativa a subtração de imagens pelo método ZOGY. Essencial para detectar objetos em movimento. | Sempre ON |
| **Gaia EDR3 WCS Refinement** | Refina a solução astrométrica usando o catálogo Gaia EDR3. Requer conexão com a internet. | Sempre ON |
| **SIP Polynomial Distortion** | Modela a distorção óptica do sistema telescópio+CCD com polinômios SIP. | ON para instrumentos com distorção notável |
| **Cosmic Ray Rejection** | Remove raios cósmicos via astroscrappy (variante de L.A.Cosmic). | Sempre ON |
| **Satellite Streak Filter** | Rejeita traços de satélites artificiais pela elongação morfológica. | Sempre ON |
| **ADES XML Export (MPC)** | Gera o arquivo de submissão no formato ADES/XML. | ON se deseja reportar ao MPC |
| **Hot Pixel Masking** | Identifica e mascara pixels com sinal anôrmalmente alto (σ > limiar). | Sempre ON |
| **Trajectory Linkage (≥3 frames)** | Confirma objetos com tracklet linear em pelo menos 3 frames. | Sempre ON |

### Limiares numéricos

| Parâmetro | Descrição | Padrão | Quando ajustar |
|---|---|---|---|
| **Detecção σ** | Limiar de significância para aceitar uma detecção na imagem Scorr. | 5.0σ | Reduza para ≥3σ se o sinal for fraco; aumente se houver muitos falsos positivos |
| **Max Elongação** | Razão entre eixo maior e menor. Objetos muito elongados são satélites/artefatos. | 2.0× | Aumente para imageria de campo largo com PSF elíptica |
| **χ² Reduzido** | Limiar de qualidade do ajuste linear de trajetória. Valores altos = trajetória não linear. | 3.0 | Reduza para exigir trajetórias mais lineares; aumente se o seeing for ruim |
| **MPC Obs Code** | Código de 3 caracteres do observatório, registrado no MPC. | `W86` | **Obrigatório** alterar para o código do seu observatório antes de submeter |

---

## 5. Etapas do Pipeline

### Etapa 1 — Calibração CCD

Aplica a equação de calibração instrumental a cada frame:

```
Science_calibrado(x,y) = [Science_bruto(x,y) - Bias(x,y) - Dark(x,y,t)] / FlatNorm(x,y)
```

- Todos os dados são mantidos em **float64** durante todo o processo
- Hot pixels são identificados por σ-clipping e mascarados
- O resultado é um objeto `CCDData` com unidade `adu`

### Etapa 2 — Alinhamento Astrométrico

- Extrai estrelas de campo do frame usando `photutils`
- Consulta o catálogo **Gaia EDR3** via `astroquery` para correspondência
- Ajusta a solução **WCS** (World Coordinate System) com correção de distorção **SIP**
- Valida: RMS ≤ `rms_threshold_arcsec` e número de estrelas ≥ `min_stars`
- Reprojeta todos os frames para o sistema de referência do primeiro frame

### Etapa 3 — Subtração ZOGY

Implementa o método **Proper Image Subtraction** (Zackay, Ofek & Gal-Yam 2016):

- Opera inteiramente no domínio de Fourier via `numpy.fft`
- Modela a PSF de cada imagem como Gaussiana analítica (FWHM configurável)
- Produz a **imagem de diferença D** e a **imagem de significância Scorr**
- A imagem Scorr é normalizada para ter média ≈ 0 e σ ≈ 1 em regiões sem objeto

> **Por que não OpenCV?** O OpenCV aplica interpolações bilineares que introduzem correlações artificiais de ruído, violando os pressupostos estatísticos do ZOGY. Apenas `numpy.fft` é utilizado.

### Etapa 4 — Detecção e Vetação

- **`DAOStarFinder`** detecta fontes na imagem Scorr com limiar ≥ σ configurado
- Filtros morfológicos aplicados a cada candidato:
  - `sharpness`: rejeita fontes muito largas (galáxias) ou muito pontuais (raios cósmicos)
  - `roundness`: rejeita fontes muito assimétricas
  - `max_elongation`: rejeita traços de satélites e raios cósmicos alongados
  - Mascaramento de hot pixels

### Etapa 5 — Linkagem de Trajetória

- Para cada combinação de candidatos entre frames, tenta ajustar uma **trajetória linear** no céu (movimento uniforme)
- O ajuste usa χ² em ascensão reta (α) e declinação (δ)
- Uma tracklet é confirmada se:
  - Aparece em ≥ `min_frames` frames consecutivos
  - χ²_reduzido ≤ `max_chi2_reduced`
  - Velocidade angular ≤ `max_speed_arcsec_hr`
- Calcula movimentos próprios: **μ_α** e **μ_δ** em arcseg/hora

### Etapa 6 — Exportação ADES

- Gera um arquivo XML conforme o esquema `submit.xsd` da IAU/MPC (versão 2017)
- Inclui para cada observação: `obsTime`, `ra`, `dec`, `rmsRA`, `rmsDec`, `rmsCorr`, `astCat`
- **Política PII**: nomes pessoais jamais são incluídos em campos `<comment>`

---

## 6. Interpretando os Resultados

### Tabela de Candidatos

| Coluna | Descrição |
|---|---|
| **ID Tracklet** | Identificador interno da tracklet |
| **α (RA)** | Ascensão reta do centróide em graus decimais (J2000) |
| **δ (Dec)** | Declinação do centróide em graus decimais (J2000) |
| **μ_α ("/hr)** | Movimento próprio em RA (arcseg/hora); positivo = leste |
| **μ_δ ("/hr)** | Movimento próprio em Dec (arcseg/hora); positivo = norte |
| **χ²_red** | Chi-quadrado reduzido do ajuste linear de trajetória (ideal ≤ 3.0) |
| **σ_RA (")** | Incerteza posicional em RA (arcseg) |
| **σ_Dec (")** | Incerteza posicional em Dec (arcseg) |
| **Frames** | Número de frames nos quais o objeto foi detectado |
| **Status** | `CONFIRMADO` ou `REJEITADO` |

### Interpretando velocidades angulares típicas

| Objeto | Velocidade típica |
|---|---|
| NEO próximo (< 0.01 UA) | 500 – 7200 arcseg/hora |
| Asteróide do cinturão principal | 30 – 100 arcseg/hora |
| Objeto trans-Netuniano | < 5 arcseg/hora |
| Satélite geoestacionário | > 15 graus/hora *(rejeitado)* |

---

## 7. Exportação ADES e Submissão ao MPC

### O que é ADES?

O **Astrometry Data Exchange Standard (ADES)** é o formato oficial da IAU para submissão de observações astrométricas ao Minor Planet Center desde 2017.

### Antes de submeter

1. ✅ Confirme que seu **código de observatório MPC** está correto (campo `MPC Obs Code`)
2. ✅ Verifique que as incertezas (`σ_RA`, `σ_Dec`) são realistas (tipicamente 0.1–1.0 arcseg)
3. ✅ Confirme que `DATE-OBS` nos cabeçalhos FITS está em UTC
4. ✅ Valide o schema clicando em **"✓ Validar Schema"** na aba ADES XML
5. ✅ Faça uma inspeção visual dos candidatos confirmados

### Processo de submissão

1. Execute o pipeline completo
2. Acesse a aba **ADES XML**
3. Clique em **"⬇ Download XML"** para salvar localmente
4. Acesse o portal do MPC: [https://www.minorplanetcenter.net/iau/subm/ades_submit.html](https://www.minorplanetcenter.net/iau/subm/ades_submit.html)
5. Faça upload do arquivo XML gerado

---

## 8. Configuração Avançada (YAML)

O arquivo `config/pipeline_config.yaml` permite configuração detalhada de cada módulo:

```yaml
calibration:
  master_bias: "caminho/para/bias_mestre.fits"  # null = desabilitado
  master_dark: "caminho/para/dark_mestre.fits"
  master_flat: "caminho/para/flat_mestre.fits"
  hot_pixel_sigma: 5.0   # σ para identificar hot pixels (recomendado: 4–7)
  gain: 1.5              # Ganho do CCD em e⁻/ADU (verifique no manual do detector)
  read_noise: 8.0        # Ruído de leitura em e⁻

astrometry:
  sip_order: 3           # Ordem do polinômio SIP: 2 (leve), 3 (padrão), 4 (detalhado)
  match_radius_arcsec: 2.0  # Raio de matching com Gaia (ajuste pelo plate scale)
  min_stars: 10          # Mínimo de estrelas para aceitar solução WCS
  rms_threshold_arcsec: 0.5  # RMS máximo (arcseg); reduza para ≤0.3 para maior rigor

subtraction:
  reg_epsilon: 1.0e-10   # Regularização para evitar divisão por zero na FFT
  psf_fwhm_pixels: 3.0   # FWHM estimado da PSF em pixels (medir pelo seeing)

detection:
  significance_threshold: 5.0  # Limiar σ na imagem Scorr
  fwhm_pixels: 3.0              # FWHM para DAOStarFinder
  sharpness_min: 0.2            # Sharpness mínima (0.0–1.0)
  sharpness_max: 1.0            # Sharpness máxima
  roundness_max: 1.0            # Roundness máxima (0 = perfeitamente redondo)
  max_elongation: 2.0           # Razão eixo maior/menor máxima

trajectory:
  min_frames: 3                  # Mínimo de frames para confirmar tracklet
  max_speed_arcsec_hr: 7200.0   # Velocidade máxima em arcseg/hora
  max_chi2_reduced: 3.0          # χ² reduzido máximo para trajetória linear
  position_sigma_arcsec: 0.3    # Incerteza posicional para o cálculo de χ²

export:
  obs_code: "W86"               # ← ALTERE para o código MPC do seu observatório
  telescope_aperture_m: 0.5     # Abertura do telescópio em metros
  telescope_desc: "0.5m f/8 Ritchey-Chretien"
  astrometric_catalog: "GaiaEDR3"
  submitter_code: "W86"         # Código institucional do submetente (não nome pessoal)
```

---

## 9. Uso via Linha de Comando (Headless)

Para uso em servidores ou scripts automatizados:

```python
import yaml
from pathlib import Path
from pipeline.pipeline import SpaceFindXPipeline

# Carregar configuração
with open("config/pipeline_config.yaml") as f:
    config = yaml.safe_load(f)

# Sobrescrever parâmetros programaticamente
config["detection"]["significance_threshold"] = 6.0
config["export"]["obs_code"] = "W86"

# Instanciar pipeline
pipeline = SpaceFindXPipeline(config)

# Callback de log opcional
def meu_log(level, msg):
    print(f"[{level.upper()}] {msg}")

# Executar
ades_path = pipeline.run(
    science_dir=Path("dados/2024-03-15/ciencia/"),
    reference_fits=Path("dados/referencia/campo_north.fits"),
    output_dir=Path("saida/2024-03-15/"),
    log_callback=meu_log,
)

if ades_path:
    print(f"\n✅ Submissão ADES salva em: {ades_path}")
else:
    print("\n⚠ Nenhuma tracklet confirmada. Nenhum arquivo gerado.")
```

### Processamento em batch

```python
from pathlib import Path
import yaml
from pipeline.pipeline import SpaceFindXPipeline

with open("config/pipeline_config.yaml") as f:
    config = yaml.safe_load(f)

pipeline = SpaceFindXPipeline(config)
base = Path("dados/")
referencia = base / "referencia.fits"

for noite in sorted(base.glob("20??-??-??")):
    if noite.is_dir():
        print(f"\nProcessando: {noite.name}")
        pipeline.run(
            science_dir=noite,
            reference_fits=referencia,
            output_dir=Path("saida") / noite.name,
        )
```

---

## 10. Erros Comuns e Soluções

| Erro / Sintoma | Causa Provável | Solução |
|---|---|---|
| `WCS solution failed: too few stars (N < min_stars)` | Campo muito esparso ou imagem desfocada | Reduza `min_stars` para 5–8; verifique qualidade da imagem |
| `RMS threshold exceeded: X.XX > 0.50 arcsec` | Distorção óptica não modelada ou seeing muito ruim | Aumente `sip_order` para 4; aumente `rms_threshold_arcsec` |
| `0 candidatos confirmados` | Limiares muito rígidos ou objeto abaixo do nível de detecção | Reduza `significance_threshold` para 4.0σ; verifique calibração |
| `Cannot find module 'astropy'` | Dependências não instaladas | Execute `pip install -r requirements.txt` |
| Muitos falsos positivos | Imagem com raios cósmicos não removidos ou seeing variável | Confirme que **Cosmic Ray Rejection** está ativo; aumente σ de detecção |
| `DATE-OBS missing in FITS header` | Cabeçalho FITS incompleto | Adicione o campo `DATE-OBS` manualmente ou via ferramenta FITS |
| XML inválido ao validar schema | Campo `obs_code` vazio ou formatado incorretamente | Configure `MPC Obs Code` com exatamente 3 caracteres alfanuméricos |
| Pipeline muito lento | Muitos frames ou imagens grandes | Reduza o número de frames por sessão; use máquina com mais RAM |
