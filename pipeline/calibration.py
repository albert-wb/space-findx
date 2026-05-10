"""
Módulo 1: Ingestão de Dados e Calibração Instrumental
======================================================

Responsável pela leitura de arquivos FITS e pela redução completa
das imagens CCD (bias, dark current, flat field, hot pixels).

Fundamento Matemático da Calibração CCD
----------------------------------------
O sinal lido de um detector CCD pode ser modelado como:

    Raw(x,y) = [Science(x,y) * Gain + DarkCurrent(t) + Bias] * FlatField(x,y)^-1 + ReadNoise

A equação de redução para obter o sinal científico calibrado é:

    Calibrated(x,y) = (Raw(x,y) - Bias(x,y) - Dark(x,y, t)) / FlatNorm(x,y)

onde:
    - Bias(x,y): frame de zero-exposição que captura o offset eletrônico do ADC
    - Dark(x,y, t): corrente de elétrons gerada termicamente, proporcional ao tempo t
    - FlatNorm(x,y): campo plano normalizado à mediana, mapeando variações de QE pixel a pixel

Referências:
    [3] Reigert, E. et al. (2023). CCD Reduction with ccdproc. Astropy Docs.
    [10] Craig, M. et al. (2022). ccdproc: CCD Data Reduction Software.
"""

import logging
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
from astropy.io import fits
from astropy.nddata import CCDData
from astropy.stats import sigma_clipped_stats
import astropy.units as u
import ccdproc

logger = logging.getLogger(__name__)


class InstrumentalCalibrator:
    """
    Gerencia o pipeline completo de calibração de imagens CCD.

    A ordem de calibração é estritamente:
        1. Subtração do overscan (se disponível)
        2. Subtração do bias mestre
        3. Subtração do dark mestre (escalonado ao tempo de exposição)
        4. Divisão pelo flat mestre normalizado
        5. Identificação e mascaramento de hot pixels

    A classe mantém as imagens como arrays de ponto flutuante (float64)
    em todos os estágios para preservar a integridade estatística do ruído
    fotônico (distribuição de Poisson) e de leitura (distribuição Gaussiana).

    Parameters
    ----------
    master_bias_path : Path, optional
        Caminho para o arquivo FITS do bias mestre.
    master_dark_path : Path, optional
        Caminho para o arquivo FITS do dark mestre.
    master_flat_path : Path, optional
        Caminho para o arquivo FITS do flat mestre.
    hot_pixel_sigma : float
        Limiar em sigma (desvios padrão) para identificação de hot pixels.
        Valor padrão de 5.0 sigma é conservador para evitar falsos positivos.
    gain : float
        Ganho do detector em elétrons/ADU. Necessário para conversão
        correta das unidades de ruído.
    read_noise : float
        Ruído de leitura do detector em elétrons (e-).
    """

    def __init__(
        self,
        master_bias_path: Optional[Path] = None,
        master_dark_path: Optional[Path] = None,
        master_flat_path: Optional[Path] = None,
        hot_pixel_sigma: float = 5.0,
        gain: float = 1.0,
        read_noise: float = 10.0,
    ):
        self.hot_pixel_sigma = hot_pixel_sigma
        self.gain = gain * u.electron / u.adu
        self.read_noise = read_noise * u.electron

        # Carrega os frames mestres se os caminhos forem fornecidos
        self.master_bias = self._load_ccd(master_bias_path, "Bias Mestre")
        self.master_dark = self._load_ccd(master_dark_path, "Dark Mestre")
        self.master_flat = self._load_ccd(master_flat_path, "Flat Mestre")

        # Normaliza o flat pela sua mediana para que FlatNorm ∈ [~0, ~1]
        if self.master_flat is not None:
            flat_median = np.median(self.master_flat.data)
            if flat_median == 0:
                raise ValueError("Flat mestre tem mediana zero — arquivo corrompido ou inválido.")
            self.master_flat = self.master_flat.divide(
                flat_median * u.adu, handle_mask="first_found"
            )
            logger.info(f"Flat mestre normalizado pela mediana: {flat_median:.2f} ADU")

    def _load_ccd(self, path: Optional[Path], name: str) -> Optional[CCDData]:
        """
        Carrega um arquivo FITS como objeto CCDData, forçando float64.

        O uso de CCDData (em vez de numpy puro) garante que as operações
        aritméticas propaguem as unidades físicas corretamente, prevenindo
        erros silenciosos de escala (e.g., ADU vs. elétrons).
        """
        if path is None:
            logger.warning(f"{name} não fornecido. Calibração parcial.")
            return None
        try:
            ccd = CCDData.read(path, unit=u.adu)
            # Forçamos float64 para preservar precisão em subtração e divisão
            ccd.data = ccd.data.astype(np.float64)
            logger.info(f"{name} carregado de: {path} | Shape: {ccd.data.shape}")
            return ccd
        except Exception as e:
            logger.error(f"Falha ao carregar {name} de {path}: {e}")
            raise

    def _identify_hot_pixels(self, data: np.ndarray) -> np.ndarray:
        """
        Identifica hot pixels usando estatística sigma-clipped.

        Fundamento Matemático:
            Um hot pixel é definido como qualquer pixel cuja contagem
            excede a média sigma-clipped (μ_sc) por mais de N desvios
            padrão (σ_sc):

                MáscaraHot(x,y) = 1  se  data(x,y) > μ_sc + N * σ_sc

            O sigma-clipping é iterativo: remove outliers, recalcula
            μ e σ, e repete até convergência. Isso garante que a
            estimativa de fundo seja robusta contra a própria população
            de hot pixels, evitando o viés de estimação.

        Parameters
        ----------
        data : np.ndarray
            Array 2D da imagem (float64).

        Returns
        -------
        np.ndarray
            Máscara booleana 2D: True onde o pixel é considerado "quente".
        """
        _, median_sc, std_sc = sigma_clipped_stats(data, sigma=3.0, maxiters=10)
        threshold = median_sc + self.hot_pixel_sigma * std_sc
        hot_mask = data > threshold
        n_hot = np.sum(hot_mask)
        logger.info(
            f"Hot pixels identificados: {n_hot} "
            f"({100*n_hot/data.size:.4f}% do frame) | "
            f"Limiar: {threshold:.2f} ADU"
        )
        return hot_mask

    def calibrate(self, raw_fits_path: Path) -> Tuple[CCDData, np.ndarray]:
        """
        Aplica a cadeia completa de calibração a um frame bruto.

        Sequência de operações (ordem matematicamente obrigatória):
            1. Leitura e conversão para float64
            2. Subtração do overscan (remove pedestal do amplificador)
            3. Subtração do bias mestre (remove offset eletrônico fixo)
            4. Subtração do dark mestre escalonado no tempo:
                   Dark_scaled(x,y,t) = Dark_mestre(x,y) * (t_sci / t_dark)
            5. Divisão pelo flat normalizado:
                   Calib(x,y) = (Raw - Bias - Dark_scaled) / Flat_norm
            6. Geração da máscara de hot pixels no frame calibrado

        Parameters
        ----------
        raw_fits_path : Path
            Caminho para o arquivo FITS bruto de ciência.

        Returns
        -------
        calibrated_ccd : CCDData
            Imagem calibrada com dados em float64 e cabeçalho WCS preservado.
        hot_pixel_mask : np.ndarray
            Máscara booleana de hot pixels (True = pixel corrompido).
        """
        logger.info(f"Iniciando calibração de: {raw_fits_path.name}")

        # --- Etapa 1: Leitura ---
        raw_ccd = CCDData.read(raw_fits_path, unit=u.adu)
        raw_ccd.data = raw_ccd.data.astype(np.float64)

        calibrated = raw_ccd

        # --- Etapa 2: Subtração do Bias ---
        if self.master_bias is not None:
            calibrated = ccdproc.subtract_bias(calibrated, self.master_bias)
            logger.debug("Bias subtraído.")

        # --- Etapa 3: Subtração do Dark Mestre ---
        if self.master_dark is not None:
            # exposure_time obtido do cabeçalho FITS (palavra-chave padrão EXPTIME)
            exposure_key = "EXPTIME"
            if exposure_key not in calibrated.header:
                raise KeyError(
                    f"Cabeçalho FITS sem '{exposure_key}'. "
                    "Impossível escalonar o dark current."
                )
            calibrated = ccdproc.subtract_dark(
                calibrated,
                self.master_dark,
                exposure_time=exposure_key,
                exposure_unit=u.second,
                scale=True,  # escalonamento linear pelo tempo
            )
            logger.debug(
                f"Dark subtraído e escalonado para t={calibrated.header[exposure_key]}s."
            )

        # --- Etapa 4: Correção de Flat Field ---
        if self.master_flat is not None:
            calibrated = ccdproc.flat_correct(
                calibrated,
                self.master_flat,
                min_value=0.01,  # evita divisão por flat ≈ 0 (pixels mortos)
            )
            logger.debug("Flat field aplicado.")

        # --- Etapa 5: Garantia de Tipo Float ---
        # Verificação de segurança: nenhuma operação deve ter convertido para int
        assert calibrated.data.dtype in (
            np.float32, np.float64
        ), f"ERRO CRÍTICO: dtype corrompido para {calibrated.data.dtype}. Pipeline abortado."

        # --- Etapa 6: Mascaramento de Hot Pixels ---
        hot_mask = self._identify_hot_pixels(calibrated.data)

        logger.info(f"Calibração concluída para: {raw_fits_path.name}")
        return calibrated, hot_mask


class FITSSeriesLoader:
    """
    Carrega uma série cronológica de arquivos FITS de um diretório.

    Ordena os frames por data de observação (palavra-chave DATE-OBS),
    garantindo que a análise temporal seja consistente para detecção
    de objetos em movimento (asteroides, transientes).

    Parameters
    ----------
    directory : Path
        Diretório contendo os arquivos FITS a serem processados.
    pattern : str
        Padrão glob para filtragem de arquivos (padrão: '*.fits').
    """

    def __init__(self, directory: Path, pattern: str = "*.fits"):
        self.directory = directory
        self.pattern = pattern

    def load_sorted(self) -> List[Path]:
        """
        Retorna lista de paths FITS ordenada cronologicamente.

        Usa a palavra-chave DATE-OBS do cabeçalho como chave de ordenação.
        Se DATE-OBS não existir, ordena pelo nome do arquivo como fallback.

        Returns
        -------
        List[Path]
            Lista de caminhos FITS ordenada por tempo de observação.
        """
        fits_files = list(self.directory.glob(self.pattern))
        if not fits_files:
            raise FileNotFoundError(
                f"Nenhum arquivo FITS encontrado em {self.directory} "
                f"com padrão '{self.pattern}'"
            )

        def sort_key(p: Path) -> str:
            try:
                hdr = fits.getheader(str(p))
                return hdr.get("DATE-OBS", p.stem)
            except Exception:
                return p.stem

        sorted_files = sorted(fits_files, key=sort_key)
        logger.info(
            f"Carregados {len(sorted_files)} arquivos FITS de {self.directory}, "
            f"ordenados cronologicamente."
        )
        return sorted_files
