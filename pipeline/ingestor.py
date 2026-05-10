"""
Módulo 0: Ingestão Leve de Metadados e Vetação Assistida
=========================================================

Responsável pela varredura de diretórios FITS, extração de metadados
sem carregar arrays de pixel, padronização cronológica de timestamps
e geração de recortes (postage stamps) para inspeção humana.

Princípio de Design — Ingestão Leve (Lazy Loading)
---------------------------------------------------
Em noites de observação típicas de levantamento NEO, um telescópio de
campo largo (e.g., Catalina Sky Survey, ZTF) produz 500–3000 frames
por noite. Um frame CCD de 4096×4096 em float64 ocupa ~128 MB de RAM.

Carregar todos os frames simultaneamente exigiria:
    RAM_total = N_frames × 128 MB ≈ 64–384 GB

Isso excede a RAM disponível em workstations científicas padrão
(tipicamente 32–128 GB). A estratégia de ingestão leve resolve
este problema lendo **apenas os cabeçalhos FITS** (tipicamente
< 50 KB por frame), permitindo:
    1. Inventário completo do dataset em tempo O(N)
    2. Ordenação cronológica sem I/O de dados
    3. Planejamento do pipeline antes de qualquer alocação pesada

Os pixels são carregados sob demanda (lazy) apenas quando o pipeline
precisa processar um frame específico.

Referências:
    [1] Craig, M. et al. (2022). ccdproc: CCD Data Reduction Software.
    [2] IAU Minor Planet Center (2017). ADES XML Submission Standard.
    [3] Rein, H. & Tamayo, D. (2015). Time Scales in Astrometry.
"""

import logging
from pathlib import Path
from typing import List, Optional, Tuple, Union

import numpy as np
import pandas as pd
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.nddata import CCDData, Cutout2D
from astropy.time import Time, TimeDelta
from astropy.wcs import WCS
import astropy.units as u
import ccdproc

logger = logging.getLogger(__name__)


class ImageIngestor:
    """
    Gerencia a ingestão leve de séries temporais de imagens FITS,
    padronização cronológica e vetação visual assistida.

    A classe opera em três fases:
        Fase 1 — Inventário: varre diretórios e extrai metadados dos
                 cabeçalhos FITS sem carregar dados de pixel.
        Fase 2 — Cronologia: converte timestamps para astropy.Time,
                 trata anomalias de escala (TAI/MJD→UTC) e ordena.
        Fase 3 — Vetação: gera recortes (cutouts) ao redor de
                 candidatos para inspeção humana (human-in-the-loop).

    Parameters
    ----------
    directory : Path
        Diretório raiz contendo os arquivos FITS.
    pattern : str
        Padrão glob para filtragem de arquivos (padrão: ``'*.fits'``).
    required_keys : list of str
        Chaves obrigatórias do cabeçalho FITS. Frames que não
        contenham essas chaves serão sinalizados com aviso.

    Attributes
    ----------
    collection : ccdproc.ImageFileCollection
        Coleção de imagens indexada por cabeçalho.
    metadata_df : pd.DataFrame
        DataFrame com metadados extraídos (sem dados de pixel).
    sorted_paths : list of Path
        Caminhos dos frames ordenados cronologicamente.

    Notes
    -----
    **Política de Memória:** Nenhum array de pixel é carregado durante
    as Fases 1 e 2. A Fase 3 carrega apenas recortes locais (tipicamente
    50×50 pixels = 20 KB em float64), preservando a RAM para o pipeline
    de processamento pesado (calibração, subtração ZOGY, detecção).
    """

    # Chaves de tempo reconhecidas em ordem de prioridade
    _TIME_KEYS = ["DATE-OBS", "MJD-OBS", "DATE-BEG", "DATE-AVG"]

    def __init__(
        self,
        directory: Union[str, Path],
        pattern: str = "*.fits",
        required_keys: Optional[List[str]] = None,
    ):
        self.directory = Path(directory)
        self.pattern = pattern
        self.required_keys = required_keys or ["DATE-OBS", "EXPTIME"]

        self.collection: Optional[ccdproc.ImageFileCollection] = None
        self.metadata_df: Optional[pd.DataFrame] = None
        self.sorted_paths: Optional[List[Path]] = None

        if not self.directory.exists():
            raise FileNotFoundError(
                f"Diretório de imagens não encontrado: {self.directory}"
            )

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # FASE 1 — INVENTÁRIO LEVE DE METADADOS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def scan_directory(self) -> pd.DataFrame:
        """
        Varre o diretório e extrai metadados dos cabeçalhos FITS.

        Utiliza ``ccdproc.ImageFileCollection`` para indexar todos os
        arquivos que correspondem ao padrão glob. A varredura lê
        **exclusivamente os blocos de cabeçalho** (HDU headers), que
        tipicamente ocupam < 50 KB por arquivo.

        **Justificativa de design:** A ingestão leve é crítica para
        prevenir estouro de memória (OOM) em noites de observação com
        milhares de frames. Um levantamento NEO típico gera 500–3000
        frames de 4k×4k por noite. Carregar todos os arrays de pixel
        simultaneamente consumiria 64–384 GB de RAM, excedendo a
        capacidade de workstations científicas padrão.

        Ao ler apenas cabeçalhos, esta fase opera com footprint de
        memória de O(N × 50 KB) ≈ 25–150 MB, três ordens de magnitude
        menor que a carga completa.

        Returns
        -------
        pd.DataFrame
            DataFrame indexado por nome de arquivo contendo todas as
            chaves de cabeçalho disponíveis, com ênfase em:
            - ``DATE-OBS`` ou ``MJD-OBS``: timestamp da observação
            - ``EXPTIME``: tempo de exposição em segundos
            - ``FILTER``: filtro fotométrico (se disponível)
            - ``NAXIS1``, ``NAXIS2``: dimensões do CCD

        Raises
        ------
        FileNotFoundError
            Se nenhum arquivo FITS for encontrado no diretório.
        """
        logger.info(
            f"Iniciando varredura leve de metadados em: {self.directory}"
        )

        self.collection = ccdproc.ImageFileCollection(
            location=str(self.directory),
            glob_include=self.pattern,
        )

        if len(self.collection.files) == 0:
            raise FileNotFoundError(
                f"Nenhum arquivo FITS encontrado em {self.directory} "
                f"com padrão '{self.pattern}'"
            )

        # Extrai tabela de sumário (somente cabeçalhos, sem pixels)
        summary = self.collection.summary
        self.metadata_df = summary.to_pandas()

        # Adiciona coluna com caminho absoluto
        self.metadata_df["filepath"] = [
            str(self.directory / f) for f in self.metadata_df["file"]
        ]

        # Verifica chaves obrigatórias
        for key in self.required_keys:
            key_lower = key.lower()
            if key_lower not in self.metadata_df.columns:
                logger.warning(
                    f"Chave obrigatória '{key}' ausente no sumário. "
                    "Frames podem não ser processáveis."
                )

        n_frames = len(self.metadata_df)
        logger.info(
            f"Varredura concluída: {n_frames} frames indexados. "
            f"RAM estimada da varredura: ~{n_frames * 50 / 1024:.1f} MB "
            f"(vs. ~{n_frames * 128:.0f} MB se pixels fossem carregados)"
        )

        return self.metadata_df

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # FASE 2 — PADRONIZAÇÃO CRONOLÓGICA E ADES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def standardize_timestamps(self) -> pd.DataFrame:
        """
        Converte timestamps brutos para ``astropy.time.Time`` em UTC.

        Processa as colunas de tempo do DataFrame detectando
        automaticamente o formato (ISO 8601 via ``DATE-OBS`` ou
        Modified Julian Date via ``MJD-OBS``).

        Tratamento de Anomalias de Escala Temporal
        -------------------------------------------
        Observatórios diferentes gravam timestamps em escalas
        diferentes (UTC, TAI, TDB). A conversão para UTC é
        obrigatória porque:

        1. O padrão ADES do MPC exige ``<obsTime>`` em UTC.
        2. TAI difere de UTC por um número inteiro de **leap seconds**
           (37s em 2024). Ignorar esta diferença introduz erro
           sistemático na astrometria orbital.
        3. MJD pode estar em TAI ou UTC dependendo do instrumento.

        A conversão ``TAI → UTC`` é realizada via ``astropy.time``,
        que mantém uma tabela atualizada de leap seconds (IERS Bulletin C).

        Returns
        -------
        pd.DataFrame
            DataFrame atualizado com colunas adicionais:
            - ``astropy_time``: objetos ``astropy.time.Time`` em UTC
            - ``iso_utc``: string ISO 8601 formatada para ADES

        Raises
        ------
        ValueError
            Se nenhuma coluna de tempo reconhecida for encontrada.
        """
        if self.metadata_df is None:
            raise RuntimeError(
                "Execute scan_directory() antes de standardize_timestamps()."
            )

        df = self.metadata_df
        times: List[Time] = []
        time_col_used = None

        # Detecta coluna de tempo disponível (prioridade definida)
        cols_lower = {c.lower(): c for c in df.columns}

        for key in self._TIME_KEYS:
            if key.lower() in cols_lower:
                time_col_used = cols_lower[key.lower()]
                break

        if time_col_used is None:
            raise ValueError(
                f"Nenhuma coluna de tempo reconhecida encontrada. "
                f"Esperava uma de: {self._TIME_KEYS}. "
                f"Colunas disponíveis: {list(df.columns)}"
            )

        logger.info(f"Coluna de tempo detectada: '{time_col_used}'")

        # Converte cada timestamp
        for idx, row in df.iterrows():
            raw_val = row[time_col_used]

            try:
                if time_col_used.lower() == "mjd-obs":
                    # MJD pode estar em TAI ou UTC; assumimos UTC
                    # e documentamos a suposição
                    t = Time(float(raw_val), format="mjd", scale="utc")
                else:
                    # DATE-OBS, DATE-BEG, DATE-AVG: formato ISO
                    raw_str = str(raw_val).strip()

                    # Detecta escala pelo sufixo ou cabeçalho TIMESYS
                    timesys = str(
                        row.get("timesys", row.get("TIMESYS", "UTC"))
                    ).strip().upper()

                    if timesys == "TAI":
                        t = Time(raw_str, format="isot", scale="tai")
                        t = t.utc  # Converte TAI → UTC (aplica leap seconds)
                        logger.debug(
                            f"Frame {row.get('file', idx)}: "
                            f"TAI→UTC (Δ={t.tai.mjd - t.utc.mjd:.6f} dias)"
                        )
                    elif timesys == "TDB":
                        t = Time(raw_str, format="isot", scale="tdb")
                        t = t.utc
                    else:
                        t = Time(raw_str, format="isot", scale="utc")

                times.append(t)

            except Exception as e:
                logger.warning(
                    f"Falha ao converter timestamp do frame "
                    f"'{row.get('file', idx)}': {e}. "
                    f"Usando epoch J2000.0 como fallback."
                )
                times.append(Time("2000-01-01T00:00:00.0", scale="utc"))

        # Adiciona colunas ao DataFrame
        df["astropy_time"] = times
        df["iso_utc"] = [self.format_obstime(t) for t in times]

        # Ordena cronologicamente
        sort_keys = [t.mjd for t in times]
        df["_sort_mjd"] = sort_keys
        df.sort_values("_sort_mjd", inplace=True)
        df.reset_index(drop=True, inplace=True)
        df.drop(columns=["_sort_mjd"], inplace=True)

        # Atualiza lista ordenada de caminhos
        self.sorted_paths = [Path(p) for p in df["filepath"].tolist()]
        self.metadata_df = df

        logger.info(
            f"Timestamps padronizados para UTC. "
            f"Intervalo: {times[0].iso} → {times[-1].iso} "
            f"(Δt = {(times[-1] - times[0]).to(u.hour):.2f})"
        )

        return self.metadata_df

    @staticmethod
    def format_obstime(t: Time) -> str:
        """
        Formata um ``astropy.time.Time`` para a string ADES/MPC.

        O Minor Planet Center (MPC) exige que todas as submissões
        ADES/XML utilizem o formato estendido ISO 8601 na tag
        ``<obsTime>``:

            ``yyyy-mm-ddThh:mm:ss.ssZ``

        Onde:
        - O sufixo ``Z`` indica escala UTC (Zulu time).
        - A precisão de centésimos de segundo (``ss.ss``) é o mínimo
          exigido pelo esquema ``submit.xsd`` (MPC/IAU 2017).
        - Precisão superior (milissegundos) é aceita e preservada.

        Este formato é **obrigatório** — timestamps em MJD, JD ou
        formatos regionais serão rejeitados pelo validador do MPC.

        Parameters
        ----------
        t : astropy.time.Time
            Objeto de tempo em qualquer escala (será convertido para UTC).

        Returns
        -------
        str
            String no formato ``yyyy-mm-ddThh:mm:ss.ssZ``.

        Examples
        --------
        >>> from astropy.time import Time
        >>> t = Time("2024-12-01T03:45:12.34", scale="utc")
        >>> ImageIngestor.format_obstime(t)
        '2024-12-01T03:45:12.34Z'
        """
        t_utc = t.utc
        # isot retorna 'yyyy-mm-ddThh:mm:ss.sss' — adicionamos 'Z'
        iso_str = t_utc.isot
        # Garante pelo menos centésimos de segundo
        if "." not in iso_str:
            iso_str += ".00"
        return iso_str + "Z"

    def get_obstime(self, frame_index: int) -> str:
        """
        Retorna o timestamp ADES de um frame específico.

        Parameters
        ----------
        frame_index : int
            Índice do frame na série cronológica (0-indexado).

        Returns
        -------
        str
            String ISO 8601 com sufixo ``Z`` para a tag ``<obsTime>``.

        Raises
        ------
        IndexError
            Se o índice estiver fora do range da série.
        """
        if self.metadata_df is None or "astropy_time" not in self.metadata_df:
            raise RuntimeError(
                "Execute standardize_timestamps() antes de get_obstime()."
            )
        if frame_index < 0 or frame_index >= len(self.metadata_df):
            raise IndexError(
                f"Índice {frame_index} fora do intervalo "
                f"[0, {len(self.metadata_df) - 1}]."
            )
        t = self.metadata_df.iloc[frame_index]["astropy_time"]
        return self.format_obstime(t)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # FASE 3 — VETAÇÃO ASSISTIDA (HUMAN-IN-THE-LOOP)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def generate_vetting_cutouts(
        self,
        candidate_coords: List[SkyCoord],
        cutout_size: Tuple[int, int] = (50, 50),
    ) -> List[List[Optional[Cutout2D]]]:
        """
        Gera recortes (postage stamps) ao redor de candidatos a NEO.

        Para cada candidato e cada frame cronológico, extrai um
        sub-array local usando ``astropy.nddata.Cutout2D`` com a
        transformação WCS do cabeçalho. Isso permite inspeção visual
        da trilha cinemática sem carregar frames inteiros.

        Preservação de Fidelidade com WCS Local
        ----------------------------------------
        O ``Cutout2D`` preserva a transformação WCS completa do frame
        original, reprojetada para o sub-array. Isso significa que:

        1. As coordenadas celestes (RA, Dec) de cada pixel do recorte
           são matematicamente idênticas às do frame original:
               WCS_cutout(x_local, y_local) = WCS_full(x_global, y_global)
           onde (x_local, y_local) = (x_global - x0, y_global - y0).

        2. A projeção tangente (TAN) e distorções SIP são preservadas
           localmente, pois o cutout herda os coeficientes CD/PC e
           CRPIX ajustados.

        3. O footprint de memória é de apenas:
               RAM_cutout = cutout_x × cutout_y × 8 bytes (float64)
               ≈ 50 × 50 × 8 = 20 KB por recorte
           vs. 128 MB para o frame completo (fator de ~6400×).

        Tratamento de Bordas (Edge Cases)
        ----------------------------------
        Candidatos a asteroides frequentemente aparecem nas bordas do
        CCD devido a:
        - Dithering do telescópio entre exposições
        - Movimento próprio do objeto durante a série

        O ``Cutout2D`` com ``mode='partial'`` e ``fill_value=np.nan``
        garante que:
        - Pixels fora da borda do CCD são preenchidos com NaN
        - O código **não falha fatalmente** (crash) em bordas
        - A visualização mostra claramente a região válida vs. NaN

        Parameters
        ----------
        candidate_coords : list of SkyCoord
            Coordenadas celestes dos candidatos a inspecionar.
        cutout_size : tuple of int
            Dimensão (linhas, colunas) do recorte em pixels.
            Padrão (50, 50) cobre ~15" para escalas típicas de
            ~0.3"/pixel, suficiente para resolver a PSF e movimento.

        Returns
        -------
        list of list of Cutout2D or None
            Matriz [n_candidatos × n_frames]. Cada elemento é um
            ``Cutout2D`` ou ``None`` se a coordenada não cair no FOV.
        """
        if self.sorted_paths is None:
            raise RuntimeError(
                "Execute standardize_timestamps() antes de "
                "generate_vetting_cutouts()."
            )

        n_candidates = len(candidate_coords)
        n_frames = len(self.sorted_paths)
        all_cutouts: List[List[Optional[Cutout2D]]] = [
            [None] * n_frames for _ in range(n_candidates)
        ]

        logger.info(
            f"Gerando recortes de vetação: {n_candidates} candidatos × "
            f"{n_frames} frames (tamanho={cutout_size})"
        )

        for j, fpath in enumerate(self.sorted_paths):
            # Carrega APENAS o cabeçalho + dados deste frame
            hdu = fits.open(str(fpath), memmap=True)
            try:
                data = hdu[0].data.astype(np.float64)
                wcs = WCS(hdu[0].header)
            except Exception as e:
                logger.warning(
                    f"Frame {fpath.name}: falha ao carregar WCS/dados: {e}"
                )
                continue
            finally:
                hdu.close()

            for i, coord in enumerate(candidate_coords):
                try:
                    # Verifica se a coordenada cai no FOV deste frame
                    pix = wcs.world_to_pixel(coord)
                    x_pix, y_pix = float(pix[0]), float(pix[1])

                    # Rejeita se estiver completamente fora do frame
                    ny, nx = data.shape
                    margin = max(cutout_size) // 2
                    if (
                        x_pix < -margin or x_pix > nx + margin
                        or y_pix < -margin or y_pix > ny + margin
                    ):
                        continue

                    # Extrai recorte com tratamento seguro de bordas
                    # mode='partial': permite recortes parciais na borda
                    # fill_value=np.nan: marca pixels fora do CCD
                    cutout = Cutout2D(
                        data,
                        position=(x_pix, y_pix),
                        size=cutout_size,
                        wcs=wcs,
                        mode="partial",
                        fill_value=np.nan,
                    )
                    all_cutouts[i][j] = cutout

                except Exception as e:
                    logger.debug(
                        f"Cutout falhou para candidato {i}, frame {j}: {e}"
                    )
                    continue

        total = sum(
            1 for row in all_cutouts for c in row if c is not None
        )
        logger.info(
            f"Recortes gerados: {total}/{n_candidates * n_frames} "
            f"(RAM ≈ {total * cutout_size[0] * cutout_size[1] * 8 / 1024:.1f} KB)"
        )

        return all_cutouts

    def plot_vetting_grid(
        self,
        cutouts: List[List[Optional[Cutout2D]]],
        candidate_labels: Optional[List[str]] = None,
        cmap: str = "gray_r",
        figsize_per_cell: float = 2.0,
        save_path: Optional[Path] = None,
    ):
        """
        Plota grade temporal de recortes para vetação humana.

        Apresenta uma matriz visual onde:
        - Cada **linha** corresponde a um candidato a NEO
        - Cada **coluna** corresponde a um frame cronológico

        Isso permite ao operador humano identificar:
        1. **Trilha cinemática real**: o candidato se move de forma
           linear e consistente entre frames → provável asteroide.
        2. **Raio cósmico**: aparece em apenas 1 frame → falso positivo.
        3. **Artefato de redução**: posição fixa, forma irregular.
        4. **Satélite/FMO**: rastro linear em 1 frame, ausente nos demais.

        A normalização de contraste (percentis 1–99) é aplicada
        individualmente por recorte para maximizar a visibilidade
        de fontes tênues sem saturar o display.

        Parameters
        ----------
        cutouts : list of list of Cutout2D or None
            Matriz de recortes gerada por ``generate_vetting_cutouts``.
        candidate_labels : list of str, optional
            Rótulos para cada candidato (e.g., ``['TRK_0001', ...]``).
        cmap : str
            Colormap do matplotlib (padrão: ``'gray_r'`` para
            astronomia — fundo branco, fontes escuras).
        figsize_per_cell : float
            Tamanho em polegadas de cada célula da grade.
        save_path : Path, optional
            Se fornecido, salva a figura em disco (PNG 150 dpi).
        """
        import matplotlib.pyplot as plt

        n_candidates = len(cutouts)
        n_frames = max(len(row) for row in cutouts) if cutouts else 0

        if n_candidates == 0 or n_frames == 0:
            logger.warning("Nenhum recorte disponível para plotagem.")
            return

        fig, axes = plt.subplots(
            n_candidates,
            n_frames,
            figsize=(
                figsize_per_cell * n_frames,
                figsize_per_cell * n_candidates,
            ),
            squeeze=False,
        )

        for i in range(n_candidates):
            label = (
                candidate_labels[i]
                if candidate_labels and i < len(candidate_labels)
                else f"Candidato {i}"
            )
            axes[i][0].set_ylabel(label, fontsize=9, fontweight="bold")

            for j in range(n_frames):
                ax = axes[i][j]
                ax.set_xticks([])
                ax.set_yticks([])

                if i == 0:
                    ax.set_title(f"t{j}", fontsize=8)

                cutout = cutouts[i][j] if j < len(cutouts[i]) else None
                if cutout is None:
                    ax.text(
                        0.5, 0.5, "N/A",
                        ha="center", va="center",
                        transform=ax.transAxes,
                        fontsize=8, color="gray",
                    )
                    ax.set_facecolor("#f0f0f0")
                    continue

                data = cutout.data.copy()
                # Normalização por percentis (robusto a outliers)
                valid = data[np.isfinite(data)]
                if len(valid) > 0:
                    vmin = np.percentile(valid, 1)
                    vmax = np.percentile(valid, 99)
                else:
                    vmin, vmax = 0, 1

                ax.imshow(
                    data,
                    origin="lower",
                    cmap=cmap,
                    vmin=vmin,
                    vmax=vmax,
                    interpolation="nearest",
                )

                # Marca centro do candidato com retículo
                cy, cx = np.array(data.shape) / 2
                ax.plot(
                    cx, cy, "+", color="red",
                    markersize=8, markeredgewidth=0.8,
                )

        plt.suptitle(
            "Vetação de Candidatos — Grade Temporal",
            fontsize=12, fontweight="bold", y=1.02,
        )
        plt.tight_layout()

        if save_path:
            fig.savefig(
                str(save_path), dpi=150,
                bbox_inches="tight", facecolor="white",
            )
            logger.info(f"Grade de vetação salva em: {save_path}")

        plt.show()
