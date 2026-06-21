"""
Orquestrador Principal do Pipeline space-findx
===============================================

Coordena todos os módulos em uma sequência de processamento coerente.
"""

import logging
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

from astropy.time import Time
from astropy.wcs import WCS

from .calibration import FITSSeriesLoader, InstrumentalCalibrator
from .astrometry import AstrometricAligner
from .subtraction import ZOGYSubtractor
from .detection import TransientDetector
from .trajectory import TrajectoryLinker
from .ades_exporter import ADESExporter

logger = logging.getLogger(__name__)


class SpaceFindXPipeline:
    """
    Pipeline completo de detecção de NEOs e transientes.

    Coordena as etapas na ordem cientificamente correta:
        Calibração → Alinhamento → Subtração ZOGY →
        Detecção + Vetação → Linkagem → Exportação ADES

    Parameters
    ----------
    config : dict
        Dicionário de configuração com os parâmetros de cada módulo.
        Veja o exemplo em config/pipeline_config.yaml.
    """

    def __init__(self, config: dict):
        self.config = config
        self._setup_logging()

        cal_cfg = config.get("calibration", {})
        self.calibrator = InstrumentalCalibrator(
            master_bias_path=self._path(cal_cfg.get("master_bias")),
            master_dark_path=self._path(cal_cfg.get("master_dark")),
            master_flat_path=self._path(cal_cfg.get("master_flat")),
            hot_pixel_sigma=cal_cfg.get("hot_pixel_sigma", 5.0),
            gain=cal_cfg.get("gain", 1.0),
            read_noise=cal_cfg.get("read_noise", 10.0),
        )

        astr_cfg = config.get("astrometry", {})
        self.aligner = AstrometricAligner(
            sip_order=astr_cfg.get("sip_order", 3),
            match_radius_arcsec=astr_cfg.get("match_radius_arcsec", 2.0),
            min_stars=astr_cfg.get("min_stars", 10),
            rms_threshold_arcsec=astr_cfg.get("rms_threshold_arcsec", 0.5),
        )

        sub_cfg = config.get("subtraction", {})
        self.subtractor = ZOGYSubtractor(
            reg_epsilon=sub_cfg.get("reg_epsilon", 1e-10),
            psf_fwhm_pixels=sub_cfg.get("psf_fwhm_pixels", 3.0),
        )

        det_cfg = config.get("detection", {})
        self.detector = TransientDetector(
            significance_threshold=det_cfg.get("significance_threshold", 5.0),
            fwhm_pixels=det_cfg.get("fwhm_pixels", 3.0),
            sharpness_min=det_cfg.get("sharpness_min", 0.2),
            sharpness_max=det_cfg.get("sharpness_max", 1.0),
            roundness_max=det_cfg.get("roundness_max", 1.0),
            max_elongation=det_cfg.get("max_elongation", 2.0),
        )

        trk_cfg = config.get("trajectory", {})
        self.linker = TrajectoryLinker(
            min_frames=trk_cfg.get("min_frames", 3),
            max_speed_arcsec_hr=trk_cfg.get("max_speed_arcsec_hr", 7200.0),
            max_chi2_reduced=trk_cfg.get("max_chi2_reduced", 3.0),
            position_sigma_arcsec=trk_cfg.get("position_sigma_arcsec", 0.3),
        )

        exp_cfg = config.get("export", {})
        self.exporter = ADESExporter(
            obs_code=exp_cfg.get("obs_code", "000"),
            telescope_aperture_m=exp_cfg.get("telescope_aperture_m", 0.5),
            telescope_desc=exp_cfg.get("telescope_desc", ""),
            astrometric_catalog=exp_cfg.get("astrometric_catalog", "GaiaEDR3"),
            submitter_code=exp_cfg.get("submitter_code", ""),
            software_name="space-findx v1.0",
        )

    @staticmethod
    def _path(p: Optional[str]) -> Optional[Path]:
        return Path(p) if p else None

    @staticmethod
    def _setup_logging():
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    def run(
        self,
        science_dir: Path,
        reference_fits: Path,
        output_dir: Path,
        log_callback=None,
    ) -> Tuple[Optional[Path], List[Any]]:
        """
        Executa o pipeline completo do início ao fim.

        Parameters
        ----------
        science_dir : Path
            Diretório com frames de ciência FITS brutos.
        reference_fits : Path
            Frame de referência FITS para subtração.
        output_dir : Path
            Diretório de saída para resultados e ADES XML.
        log_callback : callable, optional
            Função callback para redirecionar logs para a UI.

        Returns
        -------
        Tuple[Optional[Path], List[Tracklet]]
            Caminho do arquivo ADES XML gerado e lista de tracklets confirmados.
        """

        def log(level: str, msg: str):
            getattr(logger, level.lower())(msg)
            if log_callback:
                log_callback(level, msg)

        output_dir.mkdir(parents=True, exist_ok=True)
        log("info", "="*60)
        log("info", "space-findx Pipeline Iniciado")
        log("info", "="*60)

        # ─── ETAPA 1: CARREGAMENTO E CALIBRAÇÃO ─────────────────────
        log("info", "[1/6] Carregando série de imagens FITS...")
        loader = FITSSeriesLoader(science_dir)
        fits_paths = loader.load_sorted()
        log("info", f"     {len(fits_paths)} frames encontrados.")

        log("info", "[1/6] Calibrando imagem de referência...")
        ref_calibrated, ref_hot_mask = self.calibrator.calibrate(reference_fits)

        calibrated_frames = []
        hot_masks = []
        for i, fp in enumerate(fits_paths):
            log("info", f"     Calibrando frame {i+1}/{len(fits_paths)}: {fp.name}")
            cal, hot = self.calibrator.calibrate(fp)
            calibrated_frames.append(cal)
            hot_masks.append(hot)

        # ─── ETAPA 2: ALINHAMENTO ASTROMÉTRICO ──────────────────────
        log("info", "[2/6] Alinhando frames ao sistema de referência WCS...")
        aligned_frames = []
        frame_wcs: Dict[int, WCS] = {}
        frame_times: Dict[int, Time] = {}

        for i, cal in enumerate(calibrated_frames):
            log("info", f"     Alinhando frame {i+1}/{len(calibrated_frames)}...")
            aligned = self.aligner.align_to_reference(cal, ref_calibrated)
            aligned_frames.append(aligned)
            frame_wcs[i] = WCS(aligned.header)
            # Extrai tempo de observação do cabeçalho
            date_obs = cal.header.get("DATE-OBS", "2000-01-01T00:00:00.0")
            frame_times[i] = Time(date_obs, format="isot", scale="utc")

        # ─── ETAPA 3: SUBTRAÇÃO ZOGY ────────────────────────────────
        log("info", "[3/6] Executando subtração ZOGY (Proper Image Subtraction)...")
        diff_images = []
        scorr_images = []
        for i, al in enumerate(aligned_frames):
            log("info", f"     Subtraindo frame {i+1}/{len(aligned_frames)}...")
            D, S = self.subtractor.subtract(al, ref_calibrated)
            diff_images.append(D)
            scorr_images.append(S)

        # ─── ETAPA 4: DETECÇÃO E VETAÇÃO ────────────────────────────
        log("info", "[4/6] Detectando fontes e aplicando filtros morfológicos...")
        frame_candidates = {}
        for i, scorr in enumerate(scorr_images):
            candidates = self.detector.detect(
                scorr, hot_pixel_mask=hot_masks[i], frame_index=i
            )
            valid = [c for c in candidates if c.is_valid]
            frame_candidates[i] = valid
            log(
                "info",
                f"     Frame {i+1}: {len(valid)} candidatos válidos "
                f"(de {len(candidates)} detectados)",
            )

        # ─── ETAPA 5: LINKAGEM DE TRAJETÓRIA ────────────────────────
        log("info", "[5/6] Linkando trajetórias cinemáticas...")
        tracklets = self.linker.link_tracklets(frame_candidates, frame_wcs, frame_times)
        log("info", f"     {len(tracklets)} tracklets confirmadas (χ²_red < {self.linker.max_chi2}).")

        if not tracklets:
            log("warning", "Nenhuma tracklet confirmada. Encerrando sem exportação.")
            return None, []

        # ─── ETAPA 6: EXPORTAÇÃO ADES ───────────────────────────────
        log("info", "[6/6] Exportando para formato ADES XML...")
        obs_date = frame_times[0].strftime("%Y-%m-%d")
        ades_path = output_dir / f"ades_submission_{obs_date}_{self.exporter.obs_code}.xml"
        self.exporter.export(tracklets, ades_path)
        log("info", f"     Arquivo ADES salvo em: {ades_path}")

        log("info", "="*60)
        log("info", f"Pipeline concluído. {len(tracklets)} NEO(s)/transiente(s) reportado(s).")
        log("info", "="*60)
        return ades_path, tracklets
