# ◈ codebase.md — Mapa de Arquitetura e Estrutura do SPACE-FINDX

Este arquivo serve como uma documentação de referência consolidada e mapa técnico para desenvolvedores e Inteligências Artificiais. Ele detalha a ideia, a estrutura de diretórios, a arquitetura técnica e científica, bem como as restrições estritas do projeto. Ele foi projetado para economizar tokens de contexto, evitar erros de implementação e orientar futuras modificações.

---

## 1. Visão Geral do Projeto

O **SPACE-FINDX** é um pipeline astrométrico de alta precisão orientado a objetos, projetado para o processamento de imagens CCD astronômicas (formato FITS) e detecção automatizada de **Objetos Próximos da Terra (NEOs - Near-Earth Objects)** e transientes celestes. 

O pipeline processa séries temporais de imagens científicas brutas (ou pré-calibradas), realiza alinhamento astrométrico refinado, subtrai estrelas de fundo usando técnicas de ponta e conecta detecções lineares em múltiplos frames para produzir relatórios de submissão padronizados no formato **ADES/XML** exigido pelo **Minor Planet Center (MPC)** da IAU.

### 1.1 Modos de Operação
1. **Interface Web (GUI Principal)**: Uma interface local estática baseada em HTML5, CSS vanilla e Vanilla JavaScript (`index.html`), que oferece logs em tempo real, visualização de tabelas de candidatos, visualizador astrométrico interativo com camadas S/R/D/Scorr e gerador de relatórios XML.
2. **Interface Desktop (CustomTkinter)**: Uma interface nativa desenvolvida em Python (`run_pipeline.py`) utilizando a biblioteca CustomTkinter para renderização de widgets modernos e Matplotlib para projeções WCS reais.
3. **Modo Headless (API/Terminal)**: Permite a importação e execução direta das classes do pipeline em scripts Python para processamento em lote ou automação de observatórios.

---

## 2. Estrutura do Repositório

Abaixo está o mapa de diretórios e arquivos do repositório, com a finalidade de cada componente:

```
space-findx/
├── codebase.md                 # Este arquivo (Mapa do projeto, arquitetura e log de alterações)
├── index.html                  # Interface Web (GUI principal do usuário)
├── style.css                   # Estilo visual moderno e responsivo da Interface Web
├── app.js                      # Lógica do front-end da Interface Web em Vanilla JS
├── run_pipeline.py             # Script de entrada da interface nativa desktop
├── requirements.txt            # Dependências das bibliotecas Python do projeto
├── README.md                   # Visão geral rápida de instalação e uso
├── MANUAL.md                   # Manual completo do usuário e documentação de suporte
├── config/
│   └── pipeline_config.yaml    # Parâmetros padrão e limiares do pipeline
├── dados/                      # Pasta destinada às imagens FITS de entrada (ciência e referência)
├── output/                     # Diretório de saída padrão para os subprodutos
├── saida/                      # Pasta alternativa para processamentos em lote
├── gui/                        # Código-fonte da Interface Desktop nativa
│   ├── __init__.py             # Exports e inicialização do pacote GUI
│   ├── main_window.py          # Janela principal em CustomTkinter (Abas de controle e logs)
│   └── fits_viewer.py          # Widget visualizador FITS interativo integrado ao Matplotlib
└── pipeline/                   # Núcleo científico em Python do processamento de imagens
    ├── __init__.py             # Exports e inicialização do pipeline
    ├── pipeline.py             # Orquestrador geral que executa e conecta todas as etapas
    ├── calibration.py          # Processamento e redução CCD (Bias, Dark, Flat e Hot Pixels)
    ├── ingestor.py             # Gerenciamento físico, leitura e parsing de metadados FITS
    ├── astrometry.py           # Refinamento astrométrico de coordenadas WCS com catálogo Gaia EDR3
    ├── subtraction.py          # Subtração estatística de imagens usando algoritmo ZOGY via FFT
    ├── detection.py            # Localização de fontes (DAOStarFinder) e filtragem morfológica
    ├── trajectory.py           # Linkagem linear χ² dos candidatos a NEO ao longo dos frames
    └── ades_exporter.py        # Exportação dos tracklets confirmados no formato ADES/XML do MPC
```

---

## 3. Fluxo Científico e Arquitetura do Pipeline

O pipeline processa os frames de forma sequencial na seguinte ordem lógica:

```
[1] Calibração CCD ──► [2] Astrometria WCS ──► [3] Subtração ZOGY ──► [4] Detecção/Vetação ──► [5] Linkagem χ² ──► [6] ADES/XML
```

### [1] Calibração CCD (`pipeline/calibration.py`)
- Realiza a redução instrumental clássica de ruído do CCD:
  $$\text{Calibrado}(x,y) = \frac{\text{Bruto}(x,y) - \text{BiasMestre}(x,y) - \text{DarkMestre}(x,y) \cdot \left(\frac{t_{\text{sci}}}{t_{\text{dark}}}\right)}{\text{FlatMestreNormalizado}(x,y)}$$
- **Preservação estatística**: Enforca o uso estrito de dados em ponto flutuante `float64` para garantir a integridade estatística do ruído fotônico (distribuição de Poisson) e de leitura (distribuição Gaussiana).
- **Máscara de Hot Pixels**: Executa estatística de sigma-clipping iterativo para catalogar e criar máscaras booleanas para pixels anômalos quentes sem afetar o ruído geral do fundo do céu.

### [2] Alinhamento Astrométrico (`pipeline/astrometry.py`)
- Extrai estrelas de campo do frame científico calibrado.
- Realiza uma consulta ao catálogo de alta precisão **Gaia EDR3** (precisão de ~0.02 mas) usando `astroquery`.
- Encontra correspondências de posições e atualiza a solução **WCS** (World Coordinate System) do cabeçalho da imagem.
- Modela a distorção geométrica da óptica usando polinômios **SIP (Simple Image Polynomial)** de ordem configurável (padrão $n=3$).
- Valida matematicamente a solução se: erro posicional $\text{RMS} \le 0.5$ segundos de arco e estrelas detectadas $\ge 10$. Reprojeta todas as imagens para o sistema do primeiro frame de referência.

### [3] Subtração Estatística ZOGY (`pipeline/subtraction.py`)
- Implementa o algoritmo **ZOGY** (*Proper Image Subtraction* - Zackay, Ofek & Gal-Yam 2016) no domínio de Fourier via `numpy.fft` (FFTPACK/pocketfft).
- O ZOGY calcula a diferença ótima ponderada pela Transformada de Fourier das PSFs de ciência e de referência.
- Gera a **imagem de diferença $D$** (onde estrelas estáticas de campo são canceladas e transientes permanecem).
- Gera a **imagem de significância $S_{\text{corr}}$** (razão sinal/ruído espacial local). Por design estatístico, sob a hipótese nula de não haver transiente, a imagem $S_{\text{corr}}$ deve ter média zero e desvio padrão unitário ($\mathcal{N}(0,1)$).

### [4] Detecção de Fontes e Filtros Morfológicos (`pipeline/detection.py`)
- Utiliza o localizador de centróides **`DAOStarFinder`** (do pacote `photutils`) operando em cima do mapa de significância $S_{\text{corr}}$.
- Aplica vetação morfológica rigorosa para separar NEOs de falsos positivos:
  - **Limiar $\sigma$**: Seleciona fontes cujo sinal exceda um nível configurado (padrão $\ge 5.0\sigma$).
  - **Sharpness (Nitidez)**: Rejeita fontes excessivamente pontuais (ruído de pixel ou raios cósmicos residuais) e fontes muito espalhadas (galáxias de fundo).
  - **Roundness (Redondeza)**: Elimina objetos assimétricos ou deformados eletronicamente.
  - **Elongation (Elongação)**: Filtra traços lineares contínuos gerados por satélites artificiais de órbita baixa ou raios cósmicos em ângulo.
  - **Hot Pixel Masking**: Sobrepõe a máscara de pixels identificada na calibração para rejeitar falsas detecções em locais defeituosos do CCD.

### [5] Linkagem de Trajetória Cinemática (`pipeline/trajectory.py`)
- Agrupa os candidatos extraídos ao longo da série cronológica de frames.
- Aplica um ajuste linear por mínimos quadrados ($\chi^2$) nas coordenadas celestes J2000 ($\alpha$ - Ascensão Reta e $\delta$ - Declinação) dos candidatos para encontrar objetos com velocidade angular linear uniforme (consistente com dinâmica orbital de NEOs).
- Confirma uma **tracklet** se:
  1. O candidato for detectado em pelo menos 3 frames consecutivos ($\text{N} \ge 3$).
  2. O chi-quadrado reduzido do ajuste linear for menor que o limiar (padrão $\chi^2_{\text{red}} \le 3.0$).
  3. A velocidade angular total calculada estiver abaixo de $7200\text{ arcseg/hora}$ (rejeitando satélites ultrarrápidos e mantendo NEOs velozes e asteroides de cinturão).
- Calcula os movimentos próprios específicos $\mu_{\alpha}$ e $\mu_{\delta}$ em arcseg/hora para caracterização.

### [6] Exportação ADES/XML (`pipeline/ades_exporter.py`)
- Formata as tracklets confirmadas em um arquivo XML compatível com o padrão **ADES (Astrometry Data Exchange Standard)** da IAU e MPC, validado contra o esquema `submit.xsd` (versão 2017).
- Fornece no XML informações estruturadas de posição, incertezas fotométricas e posicionais ($\sigma_{\text{RA}}$, $\sigma_{\text{Dec}}$), catálogo de referência e correlações espaciais.

---

## 4. Restrições Técnicas Críticas e Regras do Projeto

As seguintes restrições de código e ciência são **mandatórias**. Qualquer alteração de código ou nova funcionalidade deve respeitá-las estritamente para manter a integridade operacional do pipeline:

1. **Uso de Ponto Flutuante (`float64`)**: Todos os arrays de imagens científicas ou de calibração devem ser lidos e mantidos obrigatoriamente no tipo de dados de dupla precisão (`np.float64`). Tipos como `float32` ou inteiros estão vetados no núcleo científico para evitar propagação de erros de truncamento em cálculos de FFT e propagação de ruído.
2. **Proibição Absoluta do OpenCV (`cv2`)**: É estritamente proibido utilizar bibliotecas de processamento como OpenCV para operações geométricas, alinhamentos ou filtragens no pipeline. O OpenCV converte/normaliza dinamicamente dados para inteiros de 8 ou 16 bits, destruindo a distribuição estatística de Poisson do ruído CCD necessária para o ZOGY. Todas as FFTs devem usar `numpy.fft`.
3. **Ingestão Protegida contra Estouro de Memória (`MemoryError`)**:
   - Sempre utilize o modo de mapeamento de memória (`mmap=True`) ao abrir arquivos FITS grandes através do `astropy.io.fits.open`.
   - Para exibição de pré-visualizações visuais e thumbnails rápidos na galeria, aplique a técnica de **binning 8×8** (`data[::8, ::8]`) que diminui o volume de dados na RAM de $\sim 128\text{ MB}$ para apenas $\sim 2\text{ MB}$.
   - Aplique o algoritmo **`ZScaleInterval`** para calcular os limites dinâmicos da escala de cinza de visualização, descartando os $5\%$ de pixels extremos para evitar que raios cósmicos de alta contagem mascarem as fontes de luz normais (evitando a "cegueira dinâmica").
4. **Política de Privacidade Estrita (PII - Personally Identifiable Information)**: Devido às regras de conformidade da IAU, MPC (2019) e GDPR, nomes pessoais de astrônomos ou operadores jamais devem aparecer em campos de texto livre do XML de submissão (como a tag `<comment>`). Os observadores devem ser identificados estritamente pelo código institucional e código do observatório registrados no MPC (exemplo: `W86`).
5. **WCS Compartilhado nas Camadas de Redução**: No visualizador FITS (FITS Viewer), o WCS do cabeçalho original do frame científico deve ser preservado integralmente e repassado para a imagem de diferença. Ao alternar entre as visualizações **S** (Science), **R** (Reference), **D** (Difference) e **Scorr** (Significância), as caixas de delimitação (bounding boxes) dos candidatos devem permanecer ancoradas nas coordenadas espaciais originais compartilhadas.

---

## 5. Log de Versões e Alterações (Version Log)

> [!IMPORTANT]
> **REGRAS PARA A I.A. E DESENVOLVEDORES (LEITURA OBRIGATÓRIA):**
> 1. Este arquivo (`codebase.md`) deve ser lido no início de cada sessão para atuar como o mapa de design técnico, evitando buscas de código demoradas e economizando contexto (tokens).
> 2. Toda e qualquer alteração de versão, adição de recursos, refatoração de módulos, modificação de bibliotecas ou correção de erros **DEVE ser documentada nesta seção de Logs** de forma clara e cronológica antes de encerrar o trabalho.
> 3. O registro de log deve detalhar a versão modificada, o autor/agente da mudança, a data ISO, os arquivos afetados e um resumo conciso das mudanças estruturais ou lógicas realizadas.

### Histórico de Logs do Projeto

#### [v1.0] - 2026-05-10
- **Autor**: Desenvolvedor Original
- **Arquivos Criados/Modificados**: Todos os arquivos do repositório original (versão estável de lançamento).
- **Resumo**: Implementação do pipeline de detecção astrométrica de NEOs contendo módulos de calibração CCD, alinhamento WCS via Gaia EDR3, subtração estatística ZOGY, DAOStarFinder e linkagem $\chi^2$. Inclusão das interfaces GUI Web (`index.html`) e GUI Desktop (`CustomTkinter`).

#### [v1.1] - 2026-06-21
- **Autor**: Antigravity AI
- **Arquivos Criados/Modificados**:
  - [NEW] [codebase.md](file:///d:/Projetos%20pessoais/space-findx/codebase.md)
- **Resumo**: Criação e estruturação do arquivo de documentação e arquitetura `codebase.md` para servir como mapa estático do projeto, detalhando diretórios, fluxo científico, algoritmos, restrições e guias de versionamento para economizar tokens de contexto de IA e prevenir bugs estruturais.

#### [v1.2] - 2026-06-21
- **Autor**: Antigravity AI
- **Arquivos Criados/Modificados**:
  - [MODIFY] [app.js](file:///d:/Projetos%20pessoais/space-findx/app.js)
  - [MODIFY] [style.css](file:///d:/Projetos%20pessoais/space-findx/style.css)
  - [MODIFY] [index.html](file:///d:/Projetos%20pessoais/space-findx/index.html)
- **Resumo**: 
  - **Metadados FITS:** Adicionado overlay modal (`showFrameDetails()`) para exibir metadados sintéticos de astronomia (DATE-OBS, ExpTime, Binning, etc.) ao clicar nos thumbnails da galeria.
  - **Interface Melhorada:** Checkbox discreta (✓) separada da visualização para alternar seleção de análise de cada frame.
  - **Sistema Anti-Duplicação e Cache (Local Storage):** Implementados `analysisCache` e `imageLibrary`. A galeria acumula frames com lotes novos aleatórios (8-24) sem resetar. Frames repetidos são bloqueados. Thumbnail exibe badge verde "✓ ANALYZED" caso já tenha sido processado pelo pipeline.
  - **Aba [L] IMAGE LIBRARY:** Novo painel na GUI contendo um histórico persistente de todas as imagens que passaram pelo programa (organizadas em cards listados com informações sintéticas), equipado com estatísticas em tempo real, barra de pesquisa text-based e filtros (All, Analyzed, Pending).
  - **Envio de Relatórios (EmailJS & Fallback Robusto):** O envio estático (`reportModule`) foi integrado de verdade à biblioteca **EmailJS** para despachar resumos diretamente ao e-mail do usuário. Um robusto sistema de fallback foi criado, fazendo com que se o serviço não estiver configurado (campos de API Keys vazios) ou falhar, o relatório seja formatado como `SFX_Report_[ProtocolId].txt` e auto-baixado no navegador local.

#### [v1.3] - 2026-06-21
- **Autor**: Antigravity AI
- **Arquivos Criados/Modificados**:
  - [NEW] [package.json](file:///d:/Projetos%20pessoais/space-findx/package.json)
  - [MODIFY] [index.html](file:///d:/Projetos%20pessoais/space-findx/index.html)
- **Resumo**: 
  - **Gerenciamento de Pacotes (NPM):** Projeto convertido para ser gerenciado pelo NPM.
  - **Servidor Local Moderno (Vite):** Integrado servidor de desenvolvimento `Vite` para melhorar performance local (Live Reload, empacotamento).
  - **Exposição Segura de Rede (Cloudflare Tunnels):** Integrado a CLI oficial em wrapper via npm (`cloudflared`). Isso resolve a exigência de hospedar o frontend remotamente (ou de acessar o possível back-end em Python futuro globalmente) providenciando uma ponte tunelada HTTPS local -> web, sem necessitar abrir portas NAT de roteador. A biblioteca `concurrently` sincroniza ambos simultaneamente via `npm start`.

#### [v1.4] - 2026-06-21
- **Autor**: Antigravity AI
- **Arquivos Criados/Modificados**:
  - [NEW] [server.py](file:///d:/Projetos%20pessoais/space-findx/server.py)
  - [MODIFY] [requirements.txt](file:///d:/Projetos%20pessoais/space-findx/requirements.txt)
  - [MODIFY] [package.json](file:///d:/Projetos%20pessoais/space-findx/package.json)
  - [MODIFY] [app.js](file:///d:/Projetos%20pessoais/space-findx/app.js)
- **Resumo**:
  - **Servidor Base FastAPI:** Criada a infraestrutura backend em Python isolada em `server.py` rodando assíncrono via `uvicorn` com `CORS` habilitado.
  - **Integração Front-Back:** O JavaScript (`app.js`) na função `runPipeline()` agora consome a rota `/api/pipeline/run` do backend via requisição `fetch` invés de puramente animar de modo estático.
  - **Orquestração:** Atualização do script NPM (`concurrently`) para subir e desligar de forma limpa o FastAPI, o Vite e o Cloudflare simultaneamente no comando `npm start`.

#### [v1.5] - 2026-06-21
- **Autor**: Antigravity AI
- **Arquivos Modificados**:
  - [MODIFY] [server.py](file:///d:/Projetos%20pessoais/space-findx/server.py)
  - [MODIFY] [pipeline/pipeline.py](file:///d:/Projetos%20pessoais/space-findx/pipeline/pipeline.py)
  - [MODIFY] [app.js](file:///d:/Projetos%20pessoais/space-findx/app.js)
- **Resumo**:
  - **Integração ZOGY:** O servidor FastAPI agora importa e instancia o `SpaceFindXPipeline` matematicamente autêntico.
  - **Hot Folders:** O sistema lê diretamente as pastas fixas `dados/ciencia` e `dados/referencia` no disco local, contornando a necessidade de upload via navegador (ideal para FITS pesados).
  - **Conversão de Astrometria:** O retorno do pipeline foi alterado para devolver a matriz de Tracklets, cujas coordenadas celestes (`SkyCoord`) e vetores de velocidade angular são mapeados para sexagesimal e serializados na API JSON para exibição transparente na Tabela de Resultados do Web UI.

#### [v1.6] - 2026-06-21
- **Autor**: Antigravity AI
- **Arquivos Modificados**:
  - [MODIFY] [server.py](file:///d:/Projetos%20pessoais/space-findx/server.py)
  - [MODIFY] [app.js](file:///d:/Projetos%20pessoais/space-findx/app.js)
- **Resumo**:
  - **Ingestão Leve (Lazy Loading):** Adicionada a rota `GET /api/frames` que utiliza o `ImageIngestor` (`ccdproc.ImageFileCollection`) para varrer a pasta `dados/ciencia/` e ler apenas os cabeçalhos (< 50 KB) das imagens `.fits`, extraindo `DATE-OBS`, `FILTER` e `EXPTIME` sem causar estouro de memória (OOM).
  - **Galeria Real na UI:** O botão `LOAD SCIENCE FRAMES` da interface Web parou de simular nomes aleatórios e agora efetua um `fetch` diretamente nessa API, populando a galeria visual (e a aba Image Library) com as imagens que realmente estão presentes no disco rígido. Mantido sistema de anti-duplicação via `localStorage`.
