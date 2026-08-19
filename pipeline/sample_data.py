"""
Gerador de Dataset Sintético de Demonstração
============================================

Permite testar o SPACE-FINDX de ponta a ponta sem precisar baixar dados de
arquivos públicos: gera um campo estelar de referência e uma série de frames
de ciência do mesmo campo contendo um objeto em movimento linear (um "NEO"
sintético), com WCS tangencial válido e ``DATE-OBS`` espaçados no tempo.

O dataset foi dimensionado para ser leve (512x512 px) e para atravessar todas
as etapas do pipeline — calibração, alinhamento, subtração ZOGY, detecção,
linkagem de trajetória e exportação ADES — em poucos segundos numa CPU comum.

Como o objeto é injetado com posição e velocidade angular conhecidas, o
resultado do pipeline é verificável: a tracklet recuperada deve reproduzir a
taxa de movimento usada na geração (ver ``expected`` no dicionário retornado).
"""

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

logger = logging.getLogger(__name__)

# ─── PARÂMETROS DO CAMPO SINTÉTICO ──────────────────────────────────────────
IMAGE_SIZE = 512               # pixels por lado
PIXEL_SCALE_ARCSEC = 1.0       # "/px — escala típica de um survey de campo largo
FIELD_CENTER_RA = 187.5        # graus
FIELD_CENTER_DEC = 12.0        # graus
N_STARS = 220                  # estrelas fixas do campo
PSF_FWHM_PIXELS = 3.0          # casa com detection.fwhm_pixels do config padrão
SKY_LEVEL = 500.0              # ADU de fundo de céu
READ_NOISE_ADU = 8.0
GAIN = 1.5                     # e-/ADU
EXPTIME = 60.0                 # segundos
N_SCIENCE_FRAMES = 5
CADENCE_MINUTES = 20.0         # intervalo entre frames consecutivos

# Movimento do objeto sintético, em pixels por hora (1 px = 1 arcsec aqui).
NEO_START_X, NEO_START_Y = 180.0, 210.0
NEO_RATE_X_PX_HR = 42.0
NEO_RATE_Y_PX_HR = 27.0
NEO_PEAK_FLUX = 4200.0         # amplitude do objeto em ADU (bem acima do ruído)

_SIGMA = PSF_FWHM_PIXELS / (2.0 * np.sqrt(2.0 * np.log(2.0)))


def _make_wcs() -> WCS:
    """WCS tangencial (TAN) centrado no campo sintético."""
    w = WCS(naxis=2)
    w.wcs.crpix = [IMAGE_SIZE / 2.0, IMAGE_SIZE / 2.0]
    w.wcs.crval = [FIELD_CENTER_RA, FIELD_CENTER_DEC]
    w.wcs.cdelt = [-PIXEL_SCALE_ARCSEC / 3600.0, PIXEL_SCALE_ARCSEC / 3600.0]
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.cunit = ["deg", "deg"]
    return w


def _add_source(image: np.ndarray, x: float, y: float, peak: float) -> None:
    """Soma uma PSF gaussiana à imagem, avaliada só numa janela local."""
    half = int(np.ceil(4 * _SIGMA))
    x0, x1 = max(0, int(x) - half), min(IMAGE_SIZE, int(x) + half + 1)
    y0, y1 = max(0, int(y) - half), min(IMAGE_SIZE, int(y) + half + 1)
    if x0 >= x1 or y0 >= y1:
        return

    yy, xx = np.mgrid[y0:y1, x0:x1]
    image[y0:y1, x0:x1] += peak * np.exp(
        -((xx - x) ** 2 + (yy - y) ** 2) / (2.0 * _SIGMA ** 2)
    )


def _star_field(rng: np.random.Generator) -> np.ndarray:
    """Posições e brilhos das estrelas fixas — idênticas em todos os frames."""
    xs = rng.uniform(8, IMAGE_SIZE - 8, N_STARS)
    ys = rng.uniform(8, IMAGE_SIZE - 8, N_STARS)
    # Distribuição de brilho aproximadamente log-normal (poucas brilhantes)
    peaks = 10.0 ** rng.uniform(1.8, 3.9, N_STARS)
    return np.column_stack([xs, ys, peaks])


def _render(stars: np.ndarray, extra_sources: List[tuple], rng: np.random.Generator) -> np.ndarray:
    """Renderiza um frame: estrelas + fontes extras + céu + ruído."""
    image = np.zeros((IMAGE_SIZE, IMAGE_SIZE), dtype=np.float64)
    for x, y, peak in stars:
        _add_source(image, x, y, peak)
    for x, y, peak in extra_sources:
        _add_source(image, x, y, peak)

    image += SKY_LEVEL
    # Ruído de Poisson (fóton) em elétrons + ruído de leitura, de volta em ADU
    electrons = np.clip(image * GAIN, 0, None)
    noisy = rng.poisson(electrons).astype(np.float64) / GAIN
    noisy += rng.normal(0.0, READ_NOISE_ADU, noisy.shape)
    return noisy.astype(np.float32)


def _header(wcs: WCS, date_obs: str, object_name: str, frame_kind: str) -> fits.Header:
    hdr = wcs.to_header()
    hdr["DATE-OBS"] = (date_obs, "UTC start of exposure")
    hdr["EXPTIME"] = (EXPTIME, "Exposure time (s)")
    hdr["FILTER"] = ("R", "Filter used")
    hdr["OBJECT"] = (object_name, "Target field")
    hdr["TELESCOP"] = ("SPACE-FINDX SIM", "Synthetic telescope")
    hdr["INSTRUME"] = ("SIMCAM", "Synthetic detector")
    hdr["GAIN"] = (GAIN, "e-/ADU")
    hdr["RDNOISE"] = (READ_NOISE_ADU, "Read noise (e-)")
    hdr["AIRMASS"] = (1.15, "Airmass at mid-exposure")
    hdr["IMAGETYP"] = (frame_kind, "Frame type")
    hdr["ORIGIN"] = ("space-findx sample_data", "Synthetic demo dataset")
    hdr["COMMENT"] = "Dataset sintetico de demonstracao - nao e uma observacao real."
    return hdr


def generate_sample_dataset(
    science_dir: Path,
    reference_dir: Path,
    prefix: str = "demo",
    seed: int = 20240117,
    overwrite: bool = True,
) -> Dict:
    """
    Escreve o dataset de demonstração no disco.

    Parameters
    ----------
    science_dir, reference_dir : Path
        Pastas de destino (criadas se não existirem).
    prefix : str
        Prefixo dos nomes de arquivo, usado também para localizar e remover
        um dataset de demonstração anterior.
    seed : int
        Semente do gerador aleatório — mesma semente, mesmo dataset.
    overwrite : bool
        Remove arquivos de demonstração anteriores com o mesmo prefixo.

    Returns
    -------
    dict
        Descrição do que foi gerado, incluindo os valores esperados da
        trajetória para conferência do resultado do pipeline.
    """
    science_dir = Path(science_dir)
    reference_dir = Path(reference_dir)
    science_dir.mkdir(parents=True, exist_ok=True)
    reference_dir.mkdir(parents=True, exist_ok=True)

    if overwrite:
        for folder in (science_dir, reference_dir):
            for old in folder.glob(prefix + "_*.fits"):
                old.unlink()
                logger.info("Dataset de demonstração anterior removido: %s", old.name)

    rng = np.random.default_rng(seed)
    wcs = _make_wcs()
    stars = _star_field(rng)

    # ─── Frame de referência: mesmo campo, sem o objeto em movimento ────────
    # Representa um empilhamento profundo de época anterior.
    ref_rng = np.random.default_rng(seed + 1)
    ref_data = _render(stars, [], ref_rng)
    t0 = datetime(2024, 1, 17, 3, 0, 0, tzinfo=timezone.utc)
    ref_epoch = (t0 - timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%S.000")

    reference_path = reference_dir / (prefix + "_reference.fits")
    fits.PrimaryHDU(
        data=ref_data,
        header=_header(wcs, ref_epoch, "SPACE-FINDX DEMO FIELD", "REFERENCE"),
    ).writeto(reference_path, overwrite=True)

    # ─── Frames de ciência: campo + NEO sintético em movimento linear ───────
    science_paths: List[Path] = []
    positions = []
    for i in range(N_SCIENCE_FRAMES):
        elapsed_hr = i * (CADENCE_MINUTES / 60.0)
        x = NEO_START_X + NEO_RATE_X_PX_HR * elapsed_hr
        y = NEO_START_Y + NEO_RATE_Y_PX_HR * elapsed_hr
        obs_time = t0 + timedelta(hours=elapsed_hr)
        date_obs = obs_time.strftime("%Y-%m-%dT%H:%M:%S.000")

        frame = _render(stars, [(x, y, NEO_PEAK_FLUX)], np.random.default_rng(seed + 10 + i))
        path = science_dir / (prefix + "_science_%02d.fits" % (i + 1))
        fits.PrimaryHDU(
            data=frame,
            header=_header(wcs, date_obs, "SPACE-FINDX DEMO FIELD", "LIGHT"),
        ).writeto(path, overwrite=True)

        science_paths.append(path)
        positions.append({"frame": i + 1, "date_obs": date_obs, "x": round(x, 2), "y": round(y, 2)})

    # Taxa esperada em arcsec/hora (1 px = PIXEL_SCALE_ARCSEC).
    # RA cresce para a esquerda (CDELT1 negativo), logo mu_ra tem sinal oposto a dx.
    expected = {
        "mu_ra_arcsec_hr": round(-NEO_RATE_X_PX_HR * PIXEL_SCALE_ARCSEC, 2),
        "mu_dec_arcsec_hr": round(NEO_RATE_Y_PX_HR * PIXEL_SCALE_ARCSEC, 2),
        "total_rate_arcsec_hr": round(
            float(np.hypot(NEO_RATE_X_PX_HR, NEO_RATE_Y_PX_HR)) * PIXEL_SCALE_ARCSEC, 2
        ),
        "n_frames": N_SCIENCE_FRAMES,
    }

    logger.info(
        "Dataset de demonstração gerado: %d frames de ciência + 1 referência (%dx%d px)",
        len(science_paths), IMAGE_SIZE, IMAGE_SIZE,
    )

    return {
        "science_files": [p.name for p in science_paths],
        "reference_file": reference_path.name,
        "science_dir": str(science_dir),
        "reference_dir": str(reference_dir),
        "image_size": IMAGE_SIZE,
        "pixel_scale_arcsec": PIXEL_SCALE_ARCSEC,
        "cadence_minutes": CADENCE_MINUTES,
        "injected_positions": positions,
        "expected": expected,
    }


if __name__ == "__main__":
    import json

    logging.basicConfig(level=logging.INFO)
    base = Path(__file__).resolve().parent.parent / "dados"
    info = generate_sample_dataset(base / "ciencia", base / "referencia")
    print(json.dumps(info, indent=2, ensure_ascii=False))
