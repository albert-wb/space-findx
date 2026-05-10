"""
Módulo 3: Subtração Estatística de Imagens — Algoritmo ZOGY
============================================================

Implementa a subtração ótima de imagens descrita em Zackay, Ofek & Gal-Yam (2016),
que generaliza o método de Alard & Lutz (1998) ao tratar ambas as imagens
(referência e ciência) como tendo PSF conhecida e variável no espaço.

Fundamento Matemático ZOGY
----------------------------
Dadas imagens R (referência) e N (nova/ciência), com PSFs P_r e P_n e
variâncias de ruído σ_r² e σ_n², a imagem de diferença ótima D é:

    D̂(ω) = [F_r·P̂_n(ω)·N̂(ω) - F_n·P̂_r(ω)·R̂(ω)] / √(σ_n²·F_r²·|P̂_n|² + σ_r²·F_n²·|P̂_r|²)

onde:
    - ω: frequências espaciais (espaço de Fourier via FFT)
    - ˆ: denota a Transformada de Fourier 2D
    - F_r, F_n: fluxos de ponto zero (fotométricos) das imagens
    - P̂_n, P̂_r: Transformadas de Fourier das PSFs

A imagem de significância estatística Scorr é:

    S_corr(x,y) = D(x,y) / √(Var_D(x,y))

onde Var_D é a variância estimada localmente, tornando Scorr uma
estatística com N(0,1) sob hipótese nula (ausência de transiente).
Detecções com Scorr > N_σ (típico: 5σ) são candidatos.

Esta implementação é rigorosamente em ponto flutuante (float64) e
PROÍBE o uso de OpenCV, que aplica normalização de inteiros que
corrompe a distribuição de ruído [8, 9].

Referências:
    [1] Zackay, B., Ofek, E.O., & Gal-Yam, A. (2016). ApJ, 830, 27.
    [2] Bramich, D.M. (2008). MNRAS Letters, 386(1), L77-L81.
    [13] Masci, F.J. et al. (2019). PASP, 131, 018003 (IPAC/ZTF ZOGY impl.)
"""

import logging
from typing import Tuple

import numpy as np
from astropy.nddata import CCDData
from astropy.stats import sigma_clipped_stats

logger = logging.getLogger(__name__)


class ZOGYSubtractor:
    """
    Subtração ótima de imagens pelo método ZOGY no domínio de Fourier.

    Todo o processamento é feito em float64. As FFTs são calculadas
    com numpy.fft (baseada em FFTPACK/pocketfft), que é numericamente
    estável e não modifica os valores dos pixels (ao contrário de
    implementações internas do OpenCV).

    Parameters
    ----------
    reg_epsilon : float
        Fator de regularização (ε) adicionado ao denominador da FFT para
        evitar divisão por zero em frequências com baixa resposta da PSF.
        Valor padrão de 1e-10 é conservador (muito menor que qualquer
        sinal real normalizado).
    psf_fwhm_pixels : float
        FWHM da PSF em pixels, usado como fallback para modelagem
        por Gaussiana analítica quando a PSF empírica não está disponível.
    """

    def __init__(self, reg_epsilon: float = 1e-10, psf_fwhm_pixels: float = 3.0):
        self.epsilon = reg_epsilon
        self.psf_fwhm = psf_fwhm_pixels

    def _estimate_background_variance(self, data: np.ndarray) -> float:
        """
        Estima a variância do ruído de fundo via sigma-clipping.

        Usa sigma-clipping iterativo (σ=3, máx 10 iterações) para
        calcular o desvio padrão do fundo livre de fontes pontuais.
        A variância estimada σ² é o quadrado deste desvio.

        A variância é usada na normalização espectral do ZOGY, sendo
        crítica para a validade estatística do Scorr. Uma estimativa
        enviesada aqui invalida o limiar de detecção em sigma.
        """
        _, _, std = sigma_clipped_stats(data, sigma=3.0, maxiters=10)
        variance = std**2
        logger.debug(f"Variância de fundo estimada: {variance:.4f} ADU²")
        return variance

    def _gaussian_psf(self, shape: Tuple[int, int], fwhm: float) -> np.ndarray:
        """
        Gera PSF Gaussiana 2D normalizada (integral = 1).

        Parâmetro de escala: σ = FWHM / (2√(2 ln 2)) ≈ FWHM / 2.3548

        A normalização garante conservação de fluxo após convolução.
        """
        sigma = fwhm / (2.0 * np.sqrt(2.0 * np.log(2.0)))
        ny, nx = shape
        y, x = np.mgrid[-ny // 2 : ny // 2, -nx // 2 : nx // 2]
        psf = np.exp(-(x**2 + y**2) / (2.0 * sigma**2))
        psf /= psf.sum()  # normalização L1 = conservação de fluxo
        return psf

    def subtract(
        self,
        science_ccd: CCDData,
        reference_ccd: CCDData,
        psf_science: np.ndarray = None,
        psf_reference: np.ndarray = None,
        flux_science: float = 1.0,
        flux_reference: float = 1.0,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Executa a subtração ZOGY completa entre ciência e referência.

        Parameters
        ----------
        science_ccd : CCDData
            Imagem de ciência calibrada e alinhada (float64).
        reference_ccd : CCDData
            Imagem de referência calibrada (float64).
        psf_science : np.ndarray, optional
            PSF empírica da imagem de ciência. Se None, usa Gaussiana.
        psf_reference : np.ndarray, optional
            PSF empírica da imagem de referência. Se None, usa Gaussiana.
        flux_science : float
            Escala fotométrica (ponto zero) da ciência. Default=1.0.
        flux_reference : float
            Escala fotométrica da referência. Default=1.0.

        Returns
        -------
        D : np.ndarray
            Imagem de diferença D(x,y) em float64.
        S_corr : np.ndarray
            Imagem de significância S_corr(x,y) — estatística N(0,1)
            sob H0. Valores > 5 indicam candidatos a transiente em 5σ.
        """
        N = science_ccd.data.astype(np.float64)
        R = reference_ccd.data.astype(np.float64)

        if N.shape != R.shape:
            raise ValueError(
                f"Shape incompatível: ciência {N.shape} ≠ referência {R.shape}. "
                "Execute o alinhamento (Módulo 2) antes da subtração."
            )

        # Estima variâncias do fundo
        sigma_n_sq = self._estimate_background_variance(N)
        sigma_r_sq = self._estimate_background_variance(R)

        F_n = flux_science
        F_r = flux_reference

        # PSF: usa empírica se disponível, senão Gaussiana
        shape = N.shape
        if psf_science is None:
            psf_science = self._gaussian_psf(shape, self.psf_fwhm)
            logger.info("PSF ciência: Gaussiana analítica (empírica não fornecida)")
        if psf_reference is None:
            psf_reference = self._gaussian_psf(shape, self.psf_fwhm)
            logger.info("PSF referência: Gaussiana analítica (empírica não fornecida)")

        # Pad PSFs para o tamanho da imagem e centra-as para FFT correta
        P_n = np.zeros(shape, dtype=np.float64)
        P_r = np.zeros(shape, dtype=np.float64)
        ph, pw = psf_science.shape
        P_n[:ph, :pw] = psf_science
        P_r[:ph, :pw] = psf_reference
        P_n = np.fft.ifftshift(P_n)
        P_r = np.fft.ifftshift(P_r)

        # Transformadas de Fourier 2D (numpy.fft — float64, sem arredondamento)
        N_hat = np.fft.fft2(N)
        R_hat = np.fft.fft2(R)
        P_n_hat = np.fft.fft2(P_n)
        P_r_hat = np.fft.fft2(P_r)
        P_n_hat_conj = np.conj(P_n_hat)
        P_r_hat_conj = np.conj(P_r_hat)

        # Denominador espectral (com regularização ε para evitar divisão por zero)
        denom = np.sqrt(
            sigma_n_sq * F_r**2 * np.abs(P_n_hat)**2
            + sigma_r_sq * F_n**2 * np.abs(P_r_hat)**2
            + self.epsilon
        )

        # Imagem de diferença D no domínio de Fourier
        D_hat = (F_r * P_n_hat_conj * N_hat - F_n * P_r_hat_conj * R_hat) / denom

        # Volta ao domínio espacial via FFT inversa
        D = np.real(np.fft.ifft2(D_hat)).astype(np.float64)

        # Imagem de significância S_corr
        # Var(D) = 1 por construção ZOGY (normalização espectral acima)
        # Na prática, re-estimamos sigma_D para verificação de sanidade
        _, _, sigma_D = sigma_clipped_stats(D, sigma=3.0, maxiters=5)
        if sigma_D < 1e-10:
            sigma_D = 1.0
            logger.warning("sigma_D muito próximo de zero — imagens idênticas?")

        S_corr = D / sigma_D

        logger.info(
            f"ZOGY concluído | D: min={D.min():.3f}, max={D.max():.3f} | "
            f"S_corr: RMS={S_corr.std():.3f} (esperado ≈ 1.0 sob H0)"
        )
        return D, S_corr
