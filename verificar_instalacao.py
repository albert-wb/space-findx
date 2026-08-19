#!/usr/bin/env python
"""
Verificação de instalação do SPACE-FINDX
========================================

Gera o dataset FITS sintético de demonstração e roda o pipeline completo sobre
ele, conferindo o resultado contra os valores injetados. Como a taxa de
movimento do objeto sintético é conhecida por construção, o teste é objetivo:
se o pipeline estiver saudável, a tracklet recuperada reproduz essa taxa.

Uso::

    python verificar_instalacao.py

O dataset é escrito em um diretório temporário e removido no final, de modo que
os arquivos em ``dados/`` não são tocados.
"""

import logging
import shutil
import sys
import tempfile
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline.pipeline import SpaceFindXPipeline
from pipeline.sample_data import generate_sample_dataset

# Tolerância na comparação da taxa recuperada, em arcsec/hora. O centróide das
# detecções tem ruído, então exigir igualdade exata seria um teste frágil.
RATE_TOLERANCE_ARCSEC_HR = 1.0

CONFIG = {
    "calibration": {"gain": 1.5, "read_noise": 8.0},
    "detection": {"significance_threshold": 5.0, "fwhm_pixels": 3.0, "max_elongation": 2.0},
    "subtraction": {"psf_fwhm_pixels": 3.0},
    "trajectory": {"min_frames": 3, "max_chi2_reduced": 3.0},
    "export": {"obs_code": "W86", "submitter_code": "W86"},
}


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    workdir = Path(tempfile.mkdtemp(prefix="spacefindx_check_"))
    print("SPACE-FINDX - verificacao de instalacao")
    print("=" * 60)
    print(f"Diretorio de trabalho: {workdir}")

    try:
        print("\n[1/3] Gerando dataset FITS de exemplo...")
        info = generate_sample_dataset(workdir / "ciencia", workdir / "referencia")
        expected = info["expected"]
        print(f"      {len(info['science_files'])} frames de ciencia + 1 referencia "
              f"({info['image_size']}x{info['image_size']} px)")
        print(f"      Objeto injetado: mu_ra={expected['mu_ra_arcsec_hr']}\"/hr, "
              f"mu_dec={expected['mu_dec_arcsec_hr']}\"/hr")

        print("\n[2/3] Executando o pipeline completo...")
        started = time.time()
        pipeline = SpaceFindXPipeline(CONFIG)
        ades_path, tracklets = pipeline.run(
            science_dir=workdir / "ciencia",
            reference_fits=workdir / "referencia" / info["reference_file"],
            output_dir=workdir / "saida",
        )
        elapsed = time.time() - started
        print(f"      Concluido em {elapsed:.1f}s - "
              f"{len(pipeline.last_candidates)} candidatos, {len(tracklets)} tracklet(s)")

        print("\n[3/3] Conferindo o resultado...")
        failures = []

        if not tracklets:
            failures.append("Nenhuma tracklet recuperada - o objeto injetado nao foi detectado.")
        else:
            best = min(
                tracklets,
                key=lambda t: abs(t.mu_ra_arcsec_hr - expected["mu_ra_arcsec_hr"])
                + abs(t.mu_dec_arcsec_hr - expected["mu_dec_arcsec_hr"]),
            )
            d_ra = abs(best.mu_ra_arcsec_hr - expected["mu_ra_arcsec_hr"])
            d_dec = abs(best.mu_dec_arcsec_hr - expected["mu_dec_arcsec_hr"])
            print(f"      Recuperado: mu_ra={best.mu_ra_arcsec_hr:.2f}\"/hr "
                  f"(erro {d_ra:.2f}), mu_dec={best.mu_dec_arcsec_hr:.2f}\"/hr (erro {d_dec:.2f})")
            print(f"      chi2_red={best.chi2_reduced:.5f} em {len(best.sky_coords)} frames")

            if d_ra > RATE_TOLERANCE_ARCSEC_HR or d_dec > RATE_TOLERANCE_ARCSEC_HR:
                failures.append(
                    f"Taxa recuperada difere da injetada em mais de {RATE_TOLERANCE_ARCSEC_HR}\"/hr."
                )
            if len(best.sky_coords) < expected["n_frames"]:
                failures.append(
                    f"A tracklet cobre {len(best.sky_coords)} de {expected['n_frames']} frames."
                )

        if ades_path is None:
            failures.append("O arquivo ADES XML nao foi exportado.")
        else:
            print(f"      ADES exportado: {ades_path.name}")

        print("\n" + "=" * 60)
        if failures:
            print("RESULTADO: FALHOU")
            for f in failures:
                print(f"  - {f}")
            return 1

        print("RESULTADO: OK - pipeline funcionando de ponta a ponta.")
        return 0

    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
