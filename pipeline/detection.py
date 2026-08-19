"""
Módulo 4: Detecção de Fontes e Vetação de Anomalias
=====================================================

Detecta candidatos a objetos transientes na imagem de significância
S_corr e aplica filtros morfológicos rigorosos para rejeitar falsos
positivos (raios cósmicos, satélites, artefatos de redução).

Fundamento Matemático da Detecção Morfológica
----------------------------------------------
DAOStarFinder usa correlação cruzada local com uma Gaussiana de largura
σ_psf para determinar sharpness e roundness de cada fonte:

    Sharpness = (valor_pico - média_anel) / (G_pico)
    Roundness_1 = (eixo_x - eixo_y) / média_eixos   [assimetria biaxial]
    Roundness_2 = (4·log2·Σ pix·G_pico / pico_G) - 1  [comparação Gaussiana]

Critérios de rejeição:
    - Raios Cósmicos: sharpness muito alto (pico pontual, anel baixo)
    - Satélites/FMOs: elongação alta (e = b/a << 1, onde a e b são
      os semi-eixos do momento de inércia da detecção)
    - Artefatos de redução: roundness extremo e sharpness negativo

A elongação e é calculada pelos momentos de segunda ordem da imagem:
    Ixx = Σ (x - x̄)² · I(x,y) / Σ I
    Iyy = Σ (y - ȳ)² · I(x,y) / Σ I
    Ixy = Σ (x - x̄)(y - ȳ) · I(x,y) / Σ I
    e = (Ixx - Iyy) / (Ixx + Iyy)   (elongação normalizada ∈ [-1, 1])

Referências:
    [4] Stetson, P.B. (1987). PASP, 99, 191. (DAOphot/DAOStarFinder)
    [5] Virtanen, M. et al. (2021). Fast Moving Object detection (FMO).
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np
from astropy.nddata import CCDData
from astropy.stats import sigma_clipped_stats
from astropy.table import Table
from photutils.detection import DAOStarFinder
from photutils.segmentation import SourceCatalog, detect_sources

logger = logging.getLogger(__name__)


@dataclass
class DetectedCandidate:
    """
    Representa um candidato a objeto transiente detectado.

    Attributes
    ----------
    id : int
        Identificador único no frame.
    x_pixel : float
        Centroide X em pixels (sistema 0-indexado).
    y_pixel : float
        Centroide Y em pixels.
    peak_significance : float
        Valor máximo de S_corr na detecção (em sigma).
    sharpness : float
        Métrica de sharpness do DAOStarFinder (0 < sharpness < 1 ideal).
    roundness1 : float
        Roundness biaxial (|roundness1| < 0.5 para objetos pontiformes).
    roundness2 : float
        Roundness Gaussiana (|roundness2| < 0.5 para objetos pontiformes).
    elongation : float
        Razão semi-eixo maior / semi-eixo menor (≈1.0 para puntiforme).
    frame_index : int
        Índice do frame temporal em que foi detectado.
    is_valid : bool
        True se passou em todos os filtros morfológicos.
    rejection_reason : str
        Motivo de rejeição se is_valid=False, vazio caso contrário.
    """

    id: int
    x_pixel: float
    y_pixel: float
    peak_significance: float
    sharpness: float
    roundness1: float
    roundness2: float
    elongation: float
    frame_index: int
    is_valid: bool = True
    rejection_reason: str = ""


class TransientDetector:
    """
    Detecta candidatos a transientes em imagens de significância S_corr.

    Parameters
    ----------
    significance_threshold : float
        Limiar de detecção em sigma sobre S_corr. Padrão 5.0σ corresponde
        a uma taxa de falsos positivos de ~5.7e-7 por pixel (sob N(0,1)).
        Para imagens de 4k×4k pixels com ~1.6e7 pixels, esperam-se ~9
        falsos positivos por frame sem filtros adicionais.
    fwhm_pixels : float
        FWHM estimada da PSF em pixels para o DAOStarFinder.
    sharpness_min : float
        Limite inferior de sharpness. Raios cósmicos têm sharpness ~ 1.
    sharpness_max : float
        Limite superior de sharpness.
    roundness_max : float
        Valor absoluto máximo de roundness1 e roundness2 aceitável.
        Objetos muito alongados (satélites) têm |roundness| >> 0.5.
    max_elongation : float
        Razão máxima semi-eixo maior/menor para aceitar a detecção.
        Rastros de satélites tipicamente têm elongação >> 3.
    min_area_pixels : int
        Área mínima em pixels para uma fonte válida (segmentação).
    """

    def __init__(
        self,
        significance_threshold: float = 5.0,
        fwhm_pixels: float = 3.0,
        sharpness_min: float = 0.2,
        sharpness_max: float = 1.0,
        roundness_max: float = 1.0,
        max_elongation: float = 2.0,
        min_area_pixels: int = 5,
    ):
        self.threshold = significance_threshold
        self.fwhm = fwhm_pixels
        self.sharpness_min = sharpness_min
        self.sharpness_max = sharpness_max
        self.roundness_max = roundness_max
        self.max_elongation = max_elongation
        self.min_area_pixels = min_area_pixels

    def detect(
        self,
        scorr: np.ndarray,
        hot_pixel_mask: Optional[np.ndarray] = None,
        frame_index: int = 0,
    ) -> List[DetectedCandidate]:
        """
        Executa detecção e filtragem morfológica completa.

        Etapas:
            1. Mascara hot pixels no S_corr
            2. DAOStarFinder para detecção inicial de picos
            3. Segmentação morfológica para elongação
            4. Filtros: sharpness, roundness, elongação

        Parameters
        ----------
        scorr : np.ndarray
            Imagem de significância (float64). Deve ser N(0,1) sob H0.
        hot_pixel_mask : np.ndarray, optional
            Máscara booleana de hot pixels (True = mascarar).
        frame_index : int
            Índice temporal do frame para rastreabilidade.

        Returns
        -------
        List[DetectedCandidate]
            Lista de candidatos com flags de validação morfológica.
        """
        scorr = scorr.astype(np.float64)

        # Máscara de hot pixels: os pixels defeituosos são substituídos pela
        # MEDIANA do frame (não por -999). Cravar um valor muito negativo
        # criaria um degrau artificial de dezenas de sigma nas bordas do
        # defeito, que o DAOStarFinder interpreta como fonte e que contamina
        # as estatísticas de fundo. A máscara também é repassada ao
        # DAOStarFinder, que sabe ignorar os pixels nativamente.
        if hot_pixel_mask is not None and np.any(hot_pixel_mask):
            scorr_clean = scorr.copy()
            fill_value = float(np.median(scorr[~hot_pixel_mask])) if np.any(~hot_pixel_mask) else 0.0
            scorr_clean[hot_pixel_mask] = fill_value
            dao_mask = hot_pixel_mask
        else:
            scorr_clean = scorr
            dao_mask = None

        # Calcula fundo local do S_corr (deve ser ~0 por construção ZOGY)
        try:
            _, median_sc, std_sc = sigma_clipped_stats(scorr_clean, sigma=3.0, maxiters=5)
            if np.isnan(median_sc):
                median_sc = 0.0
            if np.isnan(std_sc) or std_sc <= 0:
                std_sc = 1.0
        except Exception as e:
            logger.warning(f"Erro ao calcular estatísticas de fundo em detect ({e}). Usando fallbacks.")
            median_sc = 0.0
            std_sc = 1.0
        logger.debug(f"S_corr fundo: median={median_sc:.4f}, std={std_sc:.4f}")

        # --- DAOStarFinder ---
        try:
            dao = DAOStarFinder(
                threshold=self.threshold * std_sc,
                fwhm=self.fwhm,
                sharplo=self.sharpness_min,
                sharphi=self.sharpness_max,
                roundlo=-self.roundness_max,
                roundhi=self.roundness_max,
                brightest=None,  # sem limite de número de fontes
                peakmax=None,
            )
            sources = dao(scorr_clean - median_sc, mask=dao_mask)
        except Exception as e:
            logger.error(f"Erro fatal no DAOStarFinder ({e}). Pulando detecção no frame {frame_index}.")
            return []

        if sources is None or len(sources) == 0:
            logger.info(f"Frame {frame_index}: Nenhuma fonte detectada acima de {self.threshold}σ.")
            return []

        logger.info(f"Frame {frame_index}: {len(sources)} fontes detectadas pelo DAOStarFinder.")

        # --- Segmentação para elongação morfológica ---
        elongation_map = self._compute_elongations(scorr_clean, median_sc, std_sc)

        # --- Construção de candidatos com filtragem ---
        candidates = []
        for i, src in enumerate(sources):
            try:
                x = float(src["xcentroid"])
                y = float(src["ycentroid"])
                peak = float(src["peak"])
                sharp = float(src["sharpness"])
                round1 = float(src["roundness1"])
                round2 = float(src["roundness2"])
                elong = elongation_map.get((int(round(y)), int(round(x))), 1.0)

                candidate = DetectedCandidate(
                    id=i,
                    x_pixel=x,
                    y_pixel=y,
                    peak_significance=peak / std_sc,
                    sharpness=sharp,
                    roundness1=round1,
                    roundness2=round2,
                    elongation=elong,
                    frame_index=frame_index,
                )

                # Aplica filtros morfológicos
                rejection = self._apply_morphological_filters(candidate)
                if rejection:
                    candidate.is_valid = False
                    candidate.rejection_reason = rejection
                    logger.debug(f"Fonte {i} rejeitada: {rejection}")

                candidates.append(candidate)
            except Exception as src_err:
                logger.warning(f"Erro ao processar fonte {i} no frame {frame_index}: {src_err}. Ignorando fonte.")

        valid_count = sum(c.is_valid for c in candidates)
        logger.info(
            f"Frame {frame_index}: {valid_count}/{len(candidates)} "
            f"candidatos aprovados nos filtros morfológicos."
        )
        return candidates

    def _compute_elongations(
        self,
        scorr: np.ndarray,
        background: float,
        background_std: float,
    ) -> dict:
        """
        Calcula elongação de fontes segmentadas via momentos de segunda ordem.

        Usa photutils.segmentation para segmentar a imagem e calcular
        os momentos de inércia. A elongação é definida como:
            elongation = semi-eixo_maior / semi-eixo_menor

        Para fontes pontiformes: elongation ≈ 1.0
        Para rastros de satélites: elongation >> 3

        Returns
        -------
        dict
            Dicionário (row, col) -> elongação da fonte dominante.
        """
        threshold_map = background + 3.0 * background_std
        try:
            seg_map = detect_sources(
                scorr - background,
                threshold=threshold_map - background,
                npixels=self.min_area_pixels,
            )
            if seg_map is None:
                return {}

            catalog = SourceCatalog(scorr - background, seg_map)
            elongation_map = {}
            for source in catalog:
                try:
                    row = int(round(source.centroid[0]))
                    col = int(round(source.centroid[1]))
                    elong_val = getattr(source.elongation, 'value', float(source.elongation))
                    elongation_map[(row, col)] = float(elong_val)
                except Exception as src_el_err:
                    logger.debug(f"Erro ao obter elongação individual de fonte: {src_el_err}")
            return elongation_map
        except Exception as e:
            logger.warning(f"Erro no cálculo de elongações das fontes segmentadas: {e}")
            return {}

    def _apply_morphological_filters(self, c: DetectedCandidate) -> str:
        """
        Aplica todos os filtros morfológicos a um candidato.

        Retorna string com o motivo de rejeição, ou '' se válido.
        """
        if c.sharpness > 0.9:
            return f"RAIO_COSMICO: sharpness={c.sharpness:.3f} > 0.9"

        if c.elongation > self.max_elongation:
            return (
                f"SATELITE_RASTRO: elongação={c.elongation:.2f} > "
                f"{self.max_elongation:.2f}"
            )

        if abs(c.roundness1) > self.roundness_max:
            return f"ROUNDNESS1_ALTO: |roundness1|={abs(c.roundness1):.3f}"

        if abs(c.roundness2) > self.roundness_max:
            return f"ROUNDNESS2_ALTO: |roundness2|={abs(c.roundness2):.3f}"

        if c.peak_significance < self.threshold:
            return f"ABAIXO_LIMIAR: pico={c.peak_significance:.2f}σ < {self.threshold}σ"

        return ""  # passou em todos os filtros
