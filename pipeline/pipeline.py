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
from .fits_utils import effective_wcs
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
    def _pixel_scale_arcsec(ccd) -> Optional[float]:
        """Escala de placa média do frame em arcsec/pixel, ou None sem WCS."""
        wcs = effective_wcs(ccd)
        if wcs is None:
            return None
        try:
            from astropy.wcs.utils import proj_plane_pixel_scales
            scales = proj_plane_pixel_scales(wcs.celestial) * 3600.0
            return float(sum(scales) / len(scales))
        except Exception:
            return None

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
        self.last_candidates = []
        self.last_wcs = {}

        def log(level: str, msg: str):
            getattr(logger, level.lower())(msg)
            if log_callback:
                log_callback(level, msg)

        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            log("info", "="*60)
            log("info", "space-findx Pipeline Iniciado")
            log("info", "="*60)

            # ─── ETAPA 1: CARREGAMENTO E CALIBRAÇÃO ─────────────────────
            log("info", "[1/6] Carregando série de imagens FITS...")
            try:
                loader = FITSSeriesLoader(science_dir)
                fits_paths = loader.load_sorted()
            except Exception as e:
                log("error", f"Erro fatal ao carregar arquivos FITS de {science_dir}: {e}")
                return None, []

            log("info", f"     {len(fits_paths)} frames encontrados.")

            log("info", "[1/6] Calibrando imagem de referência...")
            try:
                ref_calibrated, ref_hot_mask = self.calibrator.calibrate(reference_fits)
            except Exception as e:
                log("error", f"Erro fatal ao calibrar imagem de referência {reference_fits.name}: {e}")
                return None, []

            calibrated_frames = []
            hot_masks = []
            valid_fits_paths = []
            for i, fp in enumerate(fits_paths):
                log("info", f"     Calibrando frame {i+1}/{len(fits_paths)}: {fp.name}")
                try:
                    cal, hot = self.calibrator.calibrate(fp)
                    calibrated_frames.append(cal)
                    hot_masks.append(hot)
                    valid_fits_paths.append(fp)
                except Exception as e:
                    log("error", f"     Erro ao calibrar frame {fp.name}: {e}. O frame será ignorado.")

            if not calibrated_frames:
                log("error", "Nenhum frame de ciência pôde ser calibrado com sucesso. Pipeline abortado.")
                return None, []

            # ─── ETAPA 1.5: ESCOLHA DA GRADE COMUM ──────────────────────
            # Todos os frames acabam reamostrados na grade da referência. Se a
            # referência for mais grosseira que a ciência — o caso típico de um
            # recorte DSS baixado automaticamente, dezenas de vezes mais
            # grosseiro que um frame moderno —, essa reamostragem descarta quase
            # toda a resolução antes da subtração e nenhum objeto sobrevive à
            # detecção. Nesse caso invertemos: é a REFERÊNCIA que é reprojetada
            # para a grade da ciência.
            ref_scale = self._pixel_scale_arcsec(ref_calibrated)
            sci_scale = self._pixel_scale_arcsec(calibrated_frames[0])
            if ref_scale and sci_scale and ref_scale > 1.5 * sci_scale:
                log(
                    "warning",
                    f"     Referência é {ref_scale / sci_scale:.1f}x mais grosseira que a ciência "
                    f"({ref_scale:.2f}\"/px vs {sci_scale:.2f}\"/px). Reprojetando a REFERÊNCIA "
                    f"para a grade da ciência para não descartar resolução.",
                )
                try:
                    ref_calibrated = self.aligner.align_to_reference(
                        ref_calibrated, calibrated_frames[0]
                    )
                    ref_hot_mask = self.calibrator._identify_hot_pixels(ref_calibrated.data)
                except Exception as e:
                    log("warning", f"     Falha ao reprojetar a referência: {e}. Mantendo a grade original.")

            # ─── ETAPA 2: ALINHAMENTO ASTROMÉTRICO ──────────────────────
            log("info", "[2/6] Alinhando frames ao sistema de referência WCS...")
            aligned_frames = []
            frame_wcs: Dict[int, WCS] = {}
            frame_times: Dict[int, Time] = {}
            
            for i, cal in enumerate(calibrated_frames):
                fp_name = valid_fits_paths[i].name
                log("info", f"     Alinhando frame {i+1}/{len(calibrated_frames)}: {fp_name}...")
                try:
                    aligned = self.aligner.align_to_reference(cal, ref_calibrated)
                    aligned_frames.append(aligned)
                except Exception as e:
                    log("warning", f"     Erro no alinhamento do frame {fp_name}: {e}. Usando frame sem alinhamento...")
                    aligned = cal
                    aligned_frames.append(aligned)
                
                # O WCS é lido de `aligned.wcs` quando disponível: o astropy
                # retira as chaves de WCS do header ao carregar o CCDData, de
                # modo que `WCS(aligned.header)` devolveria um WCS sem eixos
                # celestes e a linkagem falharia com coordenadas inválidas.
                resolved_wcs = None
                try:
                    resolved_wcs = effective_wcs(aligned)
                except Exception as e:
                    log("warning", f"     Erro ao resolver o WCS do frame {fp_name}: {e}")

                if resolved_wcs is not None:
                    frame_wcs[i] = resolved_wcs
                else:
                    log("warning", f"     Frame {fp_name} não tem solução astrométrica (WCS). Criando WCS trivial (1 pixel = 1 arcsec) para permitir linkagem — as coordenadas RA/Dec exportadas NÃO serão astrometricamente válidas.")
                    w = WCS(naxis=2)
                    w.wcs.crval = [0, 0]
                    w.wcs.crpix = [1, 1]
                    w.wcs.cdelt = [1.0/3600.0, 1.0/3600.0]
                    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
                    frame_wcs[i] = w
                    
                # Extrai tempo de observação do cabeçalho
                date_obs = cal.header.get("DATE-OBS", "2000-01-01T00:00:00.0")
                try:
                    frame_times[i] = Time(date_obs, format="isot", scale="utc")
                except Exception as e:
                    log("warning", f"     Erro ao analisar data DATE-OBS '{date_obs}' do frame {fp_name}: {e}. Usando data padrão J2000.")
                    frame_times[i] = Time("2000-01-01T00:00:00.0", format="isot", scale="utc")

            # ─── ETAPA 3: SUBTRAÇÃO ZOGY ────────────────────────────────
            log("info", "[3/6] Executando subtração ZOGY (Proper Image Subtraction)...")
            diff_images = []
            scorr_images = []
            final_fits_paths = []
            final_hot_masks = []
            final_frame_wcs = {}
            final_frame_times = {}
            
            final_idx = 0
            for i, al in enumerate(aligned_frames):
                fp_name = valid_fits_paths[i].name
                log("info", f"     Subtraindo frame {i+1}/{len(aligned_frames)}: {fp_name}...")
                try:
                    D, S = self.subtractor.subtract(al, ref_calibrated)
                    diff_images.append(D)
                    scorr_images.append(S)
                    final_fits_paths.append(valid_fits_paths[i])
                    final_hot_masks.append(hot_masks[i])
                    final_frame_wcs[final_idx] = frame_wcs[i]
                    final_frame_times[final_idx] = frame_times[i]
                    final_idx += 1
                except Exception as e:
                    log("error", f"     Erro na subtração ZOGY do frame {fp_name}: {e}. O frame será ignorado.")

            if not scorr_images:
                log("error", "Nenhum frame restou após a subtração ZOGY. Pipeline abortado.")
                return None, []

            # ─── ETAPA 4: DETECÇÃO E VETAÇÃO ────────────────────────────
            log("info", "[4/6] Detectando fontes e aplicando filtros morfológicos...")
            frame_candidates = {}
            all_detected_candidates = []
            for i, scorr in enumerate(scorr_images):
                fp_name = final_fits_paths[i].name
                try:
                    candidates = self.detector.detect(
                        scorr, hot_pixel_mask=final_hot_masks[i], frame_index=i
                    )
                    valid = [c for c in candidates if c.is_valid]
                    frame_candidates[i] = valid
                    all_detected_candidates.extend(candidates)
                    log(
                        "info",
                        f"     Frame {i+1} ({fp_name}): {len(valid)} candidatos válidos "
                        f"(de {len(candidates)} detectados)",
                    )
                except Exception as e:
                    log("error", f"     Erro na detecção de transientes no frame {fp_name}: {e}. Frame ignorado.")
                    frame_candidates[i] = []

            # Armazena os candidatos e WCS para consumo pela GUI
            self.last_candidates = all_detected_candidates
            self.last_wcs = final_frame_wcs

            # ─── ETAPA 5: LINKAGEM DE TRAJETÓRIA ────────────────────────
            log("info", "[5/6] Linkando trajetórias cinemáticas...")
            try:
                tracklets = self.linker.link_tracklets(frame_candidates, final_frame_wcs, final_frame_times)
            except Exception as e:
                log("error", f"Erro na linkagem de trajetórias: {e}")
                tracklets = []
                
            log("info", f"     {len(tracklets)} tracklets confirmadas (χ²_red < {self.linker.max_chi2}).")

            # Um levantamento real produz pouquíssimos objetos em movimento por
            # campo. Uma colheita grande quase sempre significa que o resíduo da
            # subtração está dominado por artefatos — tipicamente uma referência
            # rasa demais (uma placa DSS contra um frame moderno e profundo) —
            # e que o raio de busca cinemático está encadeando ruído. Sinalizamos
            # em vez de deixar o usuário reportar isso como astrometria válida.
            if len(tracklets) > max(5, len(scorr_images)):
                log(
                    "warning",
                    f"     ATENÇÃO: {len(tracklets)} tracklets em {len(scorr_images)} frames é um número "
                    f"implausivelmente alto para um campo real. Provavelmente são falsos positivos "
                    f"de uma referência inadequada (rasa ou de outra época/filtro) ou de "
                    f"max_speed_arcsec_hr={self.linker.max_speed:.0f}\"/hr, que permite encadear "
                    f"detecções não relacionadas. Verifique visualmente antes de submeter ao MPC."
                )

            if not tracklets:
                log("warning", "Nenhuma tracklet confirmada. Encerrando sem exportação.")
                log("info", "="*60)
                log("info", "Pipeline concluído. 0 NEO(s)/transiente(s) reportado(s).")
                log("info", "="*60)
                return None, []

            # ─── ETAPA 6: EXPORTAÇÃO ADES ───────────────────────────────
            log("info", "[6/6] Exportando para formato ADES XML...")
            try:
                obs_date = final_frame_times[0].strftime("%Y-%m-%d")
                ades_path = output_dir / f"ades_submission_{obs_date}_{self.exporter.obs_code}.xml"
                self.exporter.export(tracklets, ades_path)
                log("info", f"     Arquivo ADES salvo em: {ades_path}")
            except Exception as e:
                log("error", f"Erro ao exportar arquivo ADES XML: {e}")
                ades_path = None

            log("info", "="*60)
            log("info", f"Pipeline concluído. {len(tracklets)} NEO(s)/transiente(s) reportado(s).")
            log("info", "="*60)
            return ades_path, tracklets

        except Exception as e:
            log("error", f"Erro crítico e inesperado na execução do pipeline: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None, []
