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

from .fits_utils import find_fits_files, read_fits_header

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
    max_hot_area : int
        Número máximo de pixels conexos que um defeito de detector pode ter.
        Aglomerados maiores são considerados fontes reais (PSF) e preservados.
    """

    def __init__(
        self,
        master_bias_path: Optional[Path] = None,
        master_dark_path: Optional[Path] = None,
        master_flat_path: Optional[Path] = None,
        hot_pixel_sigma: float = 5.0,
        gain: float = 1.0,
        read_noise: float = 10.0,
        max_hot_area: int = 3,
    ):
        self.hot_pixel_sigma = hot_pixel_sigma
        # Área máxima (em pixels conexos) de um defeito de detector. Acima
        # disso o aglomerado é tratado como fonte astronômica real.
        self.max_hot_area = max(1, int(max_hot_area))
        self.gain = gain * u.electron / u.adu
        self.read_noise = read_noise * u.electron

        # Carrega os frames mestres se os caminhos forem fornecidos
        self.master_bias = self._load_ccd(master_bias_path, "Bias Mestre")
        self.master_dark = self._load_ccd(master_dark_path, "Dark Mestre")
        self.master_flat = self._load_ccd(master_flat_path, "Flat Mestre")

        # Normaliza o flat pela sua mediana para que FlatNorm ∈ [~0, ~1]
        if self.master_flat is not None:
            flat_median = np.median(self.master_flat.data)
            if flat_median == 0 or np.isnan(flat_median):
                logger.error("Flat mestre tem mediana zero ou NaN — desabilitando correção de flat.")
                self.master_flat = None
            else:
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
            logger.error(f"Falha ao carregar {name} de {path}: {e}. Continuando sem esse frame mestre.")
            return None

    def _identify_hot_pixels(self, data: np.ndarray) -> np.ndarray:
        """
        Identifica hot pixels e raios cósmicos por desvio LOCAL e isolamento.

        Um hot pixel é um defeito do detector: um pixel isolado que destoa dos
        seus vizinhos imediatos. Uma fonte astronômica real, ao contrário, é
        convoluída pela PSF e se espalha por vários pixels contíguos.

        Um limiar global do tipo ``data > mediana + Nσ`` não distingue os dois
        casos e marca como "hot" o núcleo de toda estrela e todo transiente do
        campo — os pixels são depois zerados no mapa de significância, de modo
        que o objeto que o pipeline procura desaparece antes da detecção. Por
        isso o teste é feito em duas etapas:

            1. **Desvio local** — resíduo em relação à mediana 3×3 do entorno,
               comparado ao ruído robusto do próprio resíduo. Isso remove a
               dependência do brilho absoluto: um pixel quente no meio de uma
               estrela brilhante continua sendo detectável.
            2. **Isolamento morfológico** — apenas grupos conexos com poucos
               pixels sobrevivem. Uma PSF com FWHM ~3 px gera um aglomerado
               bem maior que ``max_hot_area`` e é preservada.

        Returns
        -------
        np.ndarray
            Máscara booleana (True = pixel defeituoso a ser ignorado).
        """
        try:
            from scipy.ndimage import label, median_filter

            # (1) Resíduo em relação à vizinhança imediata
            local_median = median_filter(data, size=3, mode="nearest")
            residual = data - local_median

            _, res_median, res_std = sigma_clipped_stats(residual, sigma=3.0, maxiters=10)
            if not np.isfinite(res_std) or res_std <= 0:
                logger.warning("Ruído do resíduo local inválido. Máscara de hot pixels vazia.")
                return np.zeros(data.shape, dtype=bool)

            threshold = res_median + self.hot_pixel_sigma * res_std
            candidate_mask = residual > threshold

            # (2) Isolamento: descarta aglomerados grandes (= fontes reais)
            labels, n_groups = label(candidate_mask)
            hot_mask = np.zeros(data.shape, dtype=bool)
            if n_groups > 0:
                # bincount[0] conta o fundo; as demais posições são as áreas
                areas = np.bincount(labels.ravel())
                isolated_labels = np.flatnonzero(areas <= self.max_hot_area)
                isolated_labels = isolated_labels[isolated_labels != 0]
                if isolated_labels.size:
                    hot_mask = np.isin(labels, isolated_labels)

            n_hot = int(np.sum(hot_mask))
            n_rejected = int(np.sum(candidate_mask)) - n_hot
            logger.info(
                f"Hot pixels identificados: {n_hot} "
                f"({100*n_hot/data.size:.4f}% do frame) | "
                f"Limiar local: {threshold:.2f} ADU | "
                f"{n_rejected} pixels preservados por pertencerem a fontes extensas"
            )
            return hot_mask
        except Exception as e:
            logger.warning(f"Falha ao identificar hot pixels ({e}). Retornando máscara vazia.")
            return np.zeros(data.shape, dtype=bool)

    def calibrate(self, raw_fits_path: Path) -> Tuple[CCDData, np.ndarray]:
        """
        Aplica a cadeia completa de calibração a um frame bruto.
        """
        logger.info(f"Iniciando calibração de: {raw_fits_path.name}")

        # --- Etapa 1: Leitura ---
        raw_ccd = CCDData.read(raw_fits_path, unit=u.adu)
        raw_ccd.data = raw_ccd.data.astype(np.float64)

        calibrated = raw_ccd

        # --- Etapa 2: Subtração do Bias ---
        if self.master_bias is not None:
            try:
                calibrated = ccdproc.subtract_bias(calibrated, self.master_bias)
                logger.debug("Bias subtraído.")
            except Exception as e:
                logger.error(f"Falha ao subtrair bias: {e}. Ignorando bias.")

        # --- Etapa 3: Subtração do Dark Mestre ---
        if self.master_dark is not None:
            # exposure_time obtido do cabeçalho FITS
            exposure_key = "EXPTIME"
            if exposure_key not in calibrated.header:
                if "EXPOSURE" in calibrated.header:
                    exposure_key = "EXPOSURE"
                else:
                    logger.warning(
                        "Cabeçalho FITS sem 'EXPTIME' ou 'EXPOSURE'. "
                        "Usando tempo de exposição padrão de 30.0s para escalonamento do dark."
                    )
                    calibrated.header["EXPTIME"] = 30.0
                    exposure_key = "EXPTIME"
            
            try:
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
            except Exception as e:
                logger.error(f"Falha ao subtrair dark: {e}. Ignorando dark.")

        # --- Etapa 4: Correção de Flat Field ---
        if self.master_flat is not None:
            try:
                calibrated = ccdproc.flat_correct(
                    calibrated,
                    self.master_flat,
                    min_value=0.01,  # evita divisão por flat ≈ 0 (pixels mortos)
                )
                logger.debug("Flat field aplicado.")
            except Exception as e:
                logger.error(f"Falha na correção de flat field: {e}. Ignorando flat.")

        # --- Etapa 5: Garantia de Tipo Float ---
        if calibrated.data.dtype not in (np.float32, np.float64):
            logger.warning(f"Tipo de dados do frame corrompido para {calibrated.data.dtype}. Forçando conversão para float64.")
            calibrated.data = calibrated.data.astype(np.float64)

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

    def __init__(self, directory: Path, pattern: Optional[str] = None):
        self.directory = Path(directory)
        # pattern=None (padrão) delega a descoberta a `fits_utils.find_fits_files`,
        # que reconhece .fits/.fit/.fts e as variantes comprimidas .fz/.gz.
        # Um glob fixo como "*.fits" descartava silenciosamente arquivos que a
        # interface Web já havia aceitado e listado na galeria.
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
        if self.pattern:
            fits_files = [p for p in self.directory.glob(self.pattern) if p.is_file()]
        else:
            fits_files = find_fits_files(self.directory)

        if not fits_files:
            raise FileNotFoundError(
                f"Nenhum arquivo FITS encontrado em {self.directory} "
                f"(extensões aceitas: .fits, .fit, .fts, .fits.fz, .fits.gz)"
            )

        def sort_key(p: Path) -> str:
            # read_fits_header tolera MEF/comprimidos, onde a HDU 0 é um stub
            # vazio e o DATE-OBS científico mora numa extensão.
            try:
                return str(read_fits_header(p).get("DATE-OBS", p.stem))
            except Exception:
                return p.stem

        sorted_files = sorted(fits_files, key=sort_key)
        logger.info(
            f"Carregados {len(sorted_files)} arquivos FITS de {self.directory}, "
            f"ordenados cronologicamente."
        )
        return sorted_files
