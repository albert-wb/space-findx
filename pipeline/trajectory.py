"""
Módulo 5: Validação de Trajetória e Linkagem Cinemática
=========================================================

Confirma candidatos a asteroides/NEOs verificando se aparecem em ≥ 3
frames consecutivos formando uma trajetória linear compatível com a
cinemática de corpos do Sistema Solar.

Fundamento Matemático da Linkagem
-----------------------------------
Um asteroide em movimento retilíneo uniforme (aproximação válida para
observações de poucas horas) satisfaz:

    α(t) = α₀ + μ_α · (t - t₀)      [ascensão reta]
    δ(t) = δ₀ + μ_δ · (t - t₀)      [declinação]

onde μ_α e μ_δ são as velocidades de movimento próprio em arcseg/hora.
O ajuste é feito por mínimos quadrados (regressão linear) e o resíduo:

    χ² = Σ [(α_i - α̂(t_i))² / σ_α² + (δ_i - δ̂(t_i))² / σ_δ²]

deve ser pequeno para uma trajetória real. O χ² reduzido (χ²/ν, onde
ν = N - 2 graus de liberdade) deve satisfazer χ²_red < χ²_max_threshold.

Para NEOs em risco de impacto, μ tipicamente excede 1"/minuto.
Para asteroides do cinturão principal: 0.2" a 1.0"/hora.

Referências:
    [20] Bernstein, G., & Khushalani, B. (2000). AJ, 120, 3323.
    [21] Kubica, J. et al. (2007). Icarus, 189(1), 151-168.
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
from astropy.coordinates import SkyCoord
from astropy.time import Time
from astropy.wcs import WCS
import astropy.units as u

from .detection import DetectedCandidate

logger = logging.getLogger(__name__)


@dataclass
class TrajectoryCandidate:
    """
    Asteroide/transiente confirmado por linkagem em múltiplos frames.

    Attributes
    ----------
    tracklet_id : str
        Identificador único da tracklet (ex: "TRK_0001").
    detections : List[DetectedCandidate]
        Lista de detecções em frames individuais (mínimo 3).
    sky_coords : List[SkyCoord]
        Coordenadas celestes ICRS correspondentes.
    obs_times : List[Time]
        Tempos de observação astropy.time.Time em escala UTC.
    mu_ra_arcsec_hr : float
        Taxa de movimento em ascensão reta em arcseg/hora.
    mu_dec_arcsec_hr : float
        Taxa de movimento em declinação em arcseg/hora.
    chi2_reduced : float
        Chi-quadrado reduzido do ajuste linear (qualidade da trajetória).
    ra_rms_arcsec : float
        RMS dos resíduos em α (arcseg).
    dec_rms_arcsec : float
        RMS dos resíduos em δ (arcseg).
    is_confirmed : bool
        True se passou em todos os critérios de linkagem.
    """

    tracklet_id: str
    detections: List[DetectedCandidate]
    sky_coords: List[SkyCoord]
    obs_times: List[Time]
    mu_ra_arcsec_hr: float = 0.0
    mu_dec_arcsec_hr: float = 0.0
    chi2_reduced: float = 999.0
    ra_rms_arcsec: float = 999.0
    dec_rms_arcsec: float = 999.0
    is_confirmed: bool = False


class TrajectoryLinker:
    """
    Linka detecções em frames consecutivos e valida trajetórias lineares.

    O algoritmo de linkagem usa uma janela deslizante de posição:
    para cada detecção no frame F, busca candidatos compatíveis
    nos frames F+1 e F+2 dentro de um raio cinemático esperado,
    calculado a partir da velocidade máxima possível para NEOs.

    Parameters
    ----------
    min_frames : int
        Número mínimo de frames para confirmar uma tracklet. MPC exige ≥3.
    max_speed_arcsec_hr : float
        Velocidade angular máxima em arcseg/hora. NEOs em close approach
        podem atingir 3600"/hora (1°/hora). Padrão liberal de 7200.
    max_chi2_reduced : float
        Limiar de chi-quadrado reduzido para linearidade da trajetória.
        Trajetórias reais de asteroides têm χ²_red < 3 para sequências
        curtas (<4h), onde a curvatura orbital é desprezível.
    position_sigma_arcsec : float
        Incerteza de posição estimada em arcsegundos para o cálculo de χ².
        Tipicamente igual ao RMS astrométrico do alinhamento WCS.
    """

    def __init__(
        self,
        min_frames: int = 3,
        max_speed_arcsec_hr: float = 7200.0,
        max_chi2_reduced: float = 3.0,
        position_sigma_arcsec: float = 0.3,
    ):
        self.min_frames = min_frames
        self.max_speed = max_speed_arcsec_hr
        self.max_chi2 = max_chi2_reduced
        self.pos_sigma = position_sigma_arcsec

    def pixel_to_sky(
        self, candidates: List[DetectedCandidate], wcs: WCS
    ) -> Dict[int, SkyCoord]:
        """
        Converte coordenadas de pixel para ICRS usando WCS validado.

        A conversão usa a solução WCS refinada do Módulo 2, garantindo
        que as posições sejam absolutas (não relativas).

        Parameters
        ----------
        candidates : List[DetectedCandidate]
            Candidatos com coordenadas em pixel.
        wcs : WCS
            Solução WCS do frame.

        Returns
        -------
        Dict[int, SkyCoord]
            Mapa id_candidato -> SkyCoord ICRS.
        """
        sky_map = {}
        for c in candidates:
            try:
                sky = wcs.pixel_to_world(c.x_pixel, c.y_pixel)
                sky_map[c.id] = sky
            except Exception as e:
                logger.warning(f"Falha na conversão pixel->sky para candidato {c.id}: {e}")
        return sky_map

    def link_tracklets(
        self,
        frame_candidates: Dict[int, List[DetectedCandidate]],
        frame_wcs: Dict[int, WCS],
        frame_times: Dict[int, Time],
    ) -> List[TrajectoryCandidate]:
        """
        Executa linkagem de detecções em múltiplos frames.

        Algoritmo:
            1. Para cada detecção no frame inicial, tenta estender
               a tracklet nos frames seguintes.
            2. Em cada extensão, calcula o raio de busca cinemático:
                   r_max = max_speed * Δt  (em arcseg)
            3. Seleciona o vizinho mais próximo dentro de r_max.
            4. Após ≥ min_frames detecções, ajusta trajetória linear.
            5. Calcula χ²_red e rejeita tracklets não-lineares.

        Parameters
        ----------
        frame_candidates : Dict[int, List[DetectedCandidate]]
            Dicionário frame_index -> lista de candidatos válidos.
        frame_wcs : Dict[int, WCS]
            Dicionário frame_index -> WCS do frame.
        frame_times : Dict[int, Time]
            Dicionário frame_index -> tempo de observação.

        Returns
        -------
        List[TrajectoryCandidate]
            Lista de tracklets confirmadas com ajuste linear.
        """
        frame_indices = sorted(frame_candidates.keys())
        if len(frame_indices) < self.min_frames:
            logger.warning(
                f"Apenas {len(frame_indices)} frames disponíveis. "
                f"Mínimo necessário: {self.min_frames}."
            )
            return []

        # Converte pixels -> sky para todos os frames de forma segura
        frame_sky = {}
        for fi in frame_indices:
            try:
                if fi in frame_wcs and frame_wcs[fi] is not None:
                    frame_sky[fi] = self.pixel_to_sky(frame_candidates[fi], frame_wcs[fi])
                else:
                    frame_sky[fi] = {}
            except Exception as e:
                logger.error(f"Erro na conversão pixel->céu do frame {fi}: {e}")
                frame_sky[fi] = {}

        tracklets = []
        tracklet_counter = 0
        used_detections = set()  # evita reutilizar a mesma detecção

        # Frame semente: o primeiro frame
        seed_frame = frame_indices[0]
        if seed_frame not in frame_sky or seed_frame not in frame_times or seed_frame not in frame_candidates:
            logger.warning(f"Frame semente {seed_frame} com dados incompletos. Cancelando linkagem.")
            return []

        for seed_cand in frame_candidates[seed_frame]:
            if not seed_cand.is_valid:
                continue
            if (seed_frame, seed_cand.id) in used_detections:
                continue
            if seed_cand.id not in frame_sky[seed_frame]:
                continue

            # Inicia uma tracklet candidata com a semente
            tracklet_detections = [seed_cand]
            tracklet_skies = [frame_sky[seed_frame][seed_cand.id]]
            tracklet_times = [frame_times[seed_frame]]

            last_sky = tracklet_skies[-1]
            last_time = tracklet_times[-1]

            # Extende para frames subsequentes
            for fi in frame_indices[1:]:
                if fi not in frame_times or fi not in frame_sky or fi not in frame_candidates:
                    continue
                dt_hr = (frame_times[fi] - last_time).to(u.hour).value
                max_sep = self.max_speed * dt_hr * u.arcsec

                best_match = None
                best_sep = max_sep

                for cand in frame_candidates[fi]:
                    if not cand.is_valid:
                        continue
                    if (fi, cand.id) in used_detections:
                        continue
                    if cand.id not in frame_sky[fi]:
                        continue

                    cand_sky = frame_sky[fi][cand.id]
                    if last_sky is None or cand_sky is None:
                        continue
                    sep = last_sky.separation(cand_sky).to(u.arcsec)

                    if sep < best_sep:
                        best_sep = sep
                        best_match = cand

                if best_match is not None:
                    tracklet_detections.append(best_match)
                    tracklet_skies.append(frame_sky[fi][best_match.id])
                    tracklet_times.append(frame_times[fi])
                    last_sky = tracklet_skies[-1]
                    last_time = tracklet_times[-1]

            # Verifica se atingiu o mínimo de frames
            if len(tracklet_detections) < self.min_frames:
                continue

            # Ajusta trajetória linear e calcula χ²
            traj = self._fit_linear_trajectory(
                tracklet_id=f"TRK_{tracklet_counter:04d}",
                detections=tracklet_detections,
                sky_coords=tracklet_skies,
                obs_times=tracklet_times,
            )

            if traj.is_confirmed:
                tracklet_counter += 1
                tracklets.append(traj)
                # Marca detecções como usadas
                for det in tracklet_detections:
                    used_detections.add((det.frame_index, det.id))
                logger.info(
                    f"Tracklet {traj.tracklet_id} confirmada | "
                    f"μ_α={traj.mu_ra_arcsec_hr:.2f}\"/hr | "
                    f"μ_δ={traj.mu_dec_arcsec_hr:.2f}\"/hr | "
                    f"χ²_red={traj.chi2_reduced:.3f}"
                )

        logger.info(f"Linkagem concluída: {len(tracklets)} tracklets confirmadas.")
        return tracklets

    def _fit_linear_trajectory(
        self,
        tracklet_id: str,
        detections: List[DetectedCandidate],
        sky_coords: List[SkyCoord],
        obs_times: List[Time],
    ) -> TrajectoryCandidate:
        """
        Ajusta modelo de trajetória linear aos pontos da tracklet.

        O modelo é:
            α(t) = α₀ + μ_α · t'   onde t' = (t - t₀) em horas
            δ(t) = δ₀ + μ_δ · t'

        Resolvido por mínimos quadrados: [α₀, μ_α] = (AᵀA)⁻¹Aᵀb
        onde A é a matriz de design [1, t'_i] e b = [α_i].

        O χ² reduzido é calculado com σ_pos como incerteza nominal.
        """
        t0 = obs_times[0]
        t_hrs = np.array([(t - t0).to(u.hour).value for t in obs_times])

        ra_vals = np.array([sky.ra.deg for sky in sky_coords])
        dec_vals = np.array([sky.dec.deg for sky in sky_coords])

        # Corrige RA pelo cos(δ) para métrica uniforme
        cos_dec = np.cos(np.mean(dec_vals) * np.pi / 180.0)

        # Matriz de design para regressão linear
        A = np.column_stack([np.ones_like(t_hrs), t_hrs])

        try:
            # Mínimos quadrados via numpy (SVD internamente)
            coeff_ra, _, _, _ = np.linalg.lstsq(A, ra_vals, rcond=None)
            coeff_dec, _, _, _ = np.linalg.lstsq(A, dec_vals, rcond=None)
        except Exception as e:
            logger.error(f"Falha no ajuste linear de {tracklet_id}: {e}")
            return TrajectoryCandidate(
                tracklet_id=tracklet_id,
                detections=detections,
                sky_coords=sky_coords,
                obs_times=obs_times,
                is_confirmed=False,
            )

        ra_pred = A @ coeff_ra
        dec_pred = A @ coeff_dec

        # Resíduos em arcseg
        res_ra = (ra_vals - ra_pred) * 3600.0 * cos_dec
        res_dec = (dec_vals - dec_pred) * 3600.0

        ra_rms = float(np.sqrt(np.mean(res_ra**2)))
        dec_rms = float(np.sqrt(np.mean(res_dec**2)))

        # χ² com σ_pos como incerteza de posição
        sigma_arcsec = self.pos_sigma
        chi2 = np.sum(res_ra**2 / sigma_arcsec**2 + res_dec**2 / sigma_arcsec**2)
        dof = 2 * len(detections) - 4  # 4 parâmetros: α₀, μ_α, δ₀, μ_δ
        chi2_red = chi2 / max(dof, 1)

        # Velocidades em arcseg/hora (graus/hora * 3600)
        mu_ra = coeff_ra[1] * 3600.0 * cos_dec
        mu_dec = coeff_dec[1] * 3600.0

        is_confirmed = chi2_red < self.max_chi2

        return TrajectoryCandidate(
            tracklet_id=tracklet_id,
            detections=detections,
            sky_coords=sky_coords,
            obs_times=obs_times,
            mu_ra_arcsec_hr=float(mu_ra),
            mu_dec_arcsec_hr=float(mu_dec),
            chi2_reduced=float(chi2_red),
            ra_rms_arcsec=ra_rms,
            dec_rms_arcsec=dec_rms,
            is_confirmed=is_confirmed,
        )
