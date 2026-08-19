import os
import sys
import shutil
import time
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

import yaml
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

# Garante que o diretório raiz está no path para importar módulos da pasta pipeline/
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline.pipeline import SpaceFindXPipeline
from pipeline.fits_utils import (
    COMPRESSION_SUFFIXES,
    FITS_SUFFIXES,
    find_fits_files,
    is_fits_file,
    read_fits_metadata,
)
from pipeline.sample_data import generate_sample_dataset

logger = logging.getLogger(__name__)

# ─── CONSTANTES ──────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
DADOS_DIR = BASE_DIR / "dados"
SCIENCE_DIR = DADOS_DIR / "ciencia"
REFERENCE_DIR = DADOS_DIR / "referencia"
OUTPUT_DIR = BASE_DIR / "saida"
CONFIG_PATH = BASE_DIR / "config" / "pipeline_config.yaml"
SAMPLE_PREFIX = "demo"

# Extensões aceitas no upload. Levantamentos reais distribuem FITS como .fits,
# .fit, .fts e nas variantes comprimidas .fits.fz (Rice) e .fits.gz — aceitar
# apenas {.fit, .fits} rejeitava silenciosamente boa parte dos arquivos de
# referência que os usuários tentavam enviar.
ACCEPTED_EXTENSIONS_LABEL = ", ".join(
    sorted(FITS_SUFFIXES) + [f"{f}{c}" for f in (".fits",) for c in sorted(COMPRESSION_SUFFIXES)]
)

FOLDERS = {"ciencia": SCIENCE_DIR, "referencia": REFERENCE_DIR}

app = FastAPI(
    title="SPACE-FINDX API",
    description="Backend API para detecção de NEOs usando ZOGY e Astrometria",
    version="1.1.0"
)

# Configuração do CORS para permitir chamadas do Vite (localhost:5173)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Em produção, defina o domínio exato
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── MODELOS DE DADOS (PYDANTIC) ────────────────────────────────────────────────
class RunPipelineRequest(BaseModel):
    sigma: float
    elongation: float
    chi2: float
    modules: Dict[str, bool]


class RunPipelineResponse(BaseModel):
    status: str
    tracklets: List[Dict[str, Any]]
    execution_time: float
    frames_processed: int = 0
    reference_used: Optional[str] = None
    message: str = ""


class SampleDataResponse(BaseModel):
    status: str
    message: str
    dataset: Dict[str, Any]


# ─── HELPERS ────────────────────────────────────────────────────────────────────

def _safe_float(val, default: float = 0.0) -> float:
    """Converte valor para float de forma segura, tratando NaN e strings vazias."""
    try:
        result = float(val)
        import math
        return default if math.isnan(result) else result
    except (ValueError, TypeError):
        return default


def _load_base_config() -> dict:
    """
    Carrega ``config/pipeline_config.yaml`` como base da execução.

    Antes o backend montava um dicionário mínimo em código, o que descartava
    silenciosamente ganho, ruído de leitura, código MPC do observatório e
    demais parâmetros do arquivo de configuração — o ADES exportado saía com
    o código de observatório genérico "000".
    """
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh) or {}
        if not isinstance(loaded, dict):
            raise ValueError("O YAML de configuração não contém um mapeamento no topo.")
        return loaded
    except FileNotFoundError:
        logger.warning("Arquivo de configuração %s não encontrado. Usando padrões.", CONFIG_PATH)
        return {}
    except Exception:
        logger.exception("Erro ao ler %s. Usando padrões.", CONFIG_PATH)
        return {}


def _list_frames(directory: Path) -> Dict[str, Any]:
    """
    Monta a resposta de galeria para um diretório.

    Os erros de leitura viram um campo ``error`` na própria linha do frame, em
    vez de fazer o arquivo desaparecer da lista: um FITS corrompido deve
    aparecer na interface com o motivo, não como "pasta vazia".
    """
    files = find_fits_files(directory)
    if not files:
        return {"status": "empty", "frames": [], "directory": str(directory), "message": ""}

    frames = []
    for path in files:
        meta = read_fits_metadata(path)
        meta["exptime"] = _safe_float(meta.get("exptime"), 0.0)
        frames.append(meta)

    unreadable = [f["filename"] for f in frames if f.get("error")]
    message = ""
    if unreadable:
        message = f"{len(unreadable)} arquivo(s) com cabeçalho ilegível: {', '.join(unreadable)}"

    return {
        "status": "success",
        "frames": frames,
        "directory": str(directory),
        "message": message,
    }


# ─── ENDPOINTS ─────────────────────────────────────────────────────────────────

@app.get("/api/status")
async def get_status():
    """Health check do servidor."""
    return {
        "status": "online",
        "message": "SPACE-FINDX Backend is running",
        "accepted_extensions": ACCEPTED_EXTENSIONS_LABEL,
        "science_dir": str(SCIENCE_DIR),
        "reference_dir": str(REFERENCE_DIR),
    }


@app.get("/api/frames")
async def get_science_frames():
    """Lista os frames de ciência presentes em 'dados/ciencia'."""
    try:
        return _list_frames(SCIENCE_DIR)
    except Exception as e:
        logger.exception("Erro ao listar science frames")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/frames/reference")
async def get_reference_frames():
    """Lista os frames de referência presentes em 'dados/referencia'."""
    try:
        return _list_frames(REFERENCE_DIR)
    except Exception as e:
        logger.exception("Erro ao listar reference frames")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/upload/{folder}")
async def upload_files(folder: str, files: List[UploadFile] = File(...)):
    """
    Recebe múltiplos arquivos FITS e salva na pasta correspondente.

    Valida a extensão considerando sufixos compostos (``.fits.fz``, ``.fit.gz``)
    e retorna lista detalhada com arquivos salvos e rejeitados. Arquivos vazios
    também são rejeitados: um upload interrompido gerava um FITS de 0 byte que
    depois quebrava a leitura da galeria.
    """
    if folder not in FOLDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Pasta inválida '{folder}'. Use: {', '.join(FOLDERS.keys())}"
        )

    target_dir = FOLDERS[folder]
    target_dir.mkdir(parents=True, exist_ok=True)

    saved_files: List[str] = []
    skipped_files: List[Dict[str, str]] = []

    for upload in files:
        # Sanitiza o nome do arquivo para evitar path traversal
        safe_name = Path(upload.filename or "").name
        if not safe_name:
            skipped_files.append({"filename": str(upload.filename), "reason": "Nome de arquivo inválido"})
            continue

        if not is_fits_file(safe_name):
            skipped_files.append({
                "filename": safe_name,
                "reason": f"Extensão não reconhecida como FITS. Aceitas: {ACCEPTED_EXTENSIONS_LABEL}",
            })
            await upload.close()
            continue

        file_path = target_dir / safe_name
        try:
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(upload.file, buffer)

            if file_path.stat().st_size == 0:
                file_path.unlink(missing_ok=True)
                skipped_files.append({"filename": safe_name, "reason": "Arquivo vazio (0 byte)"})
                continue

            # Valida que o conteúdo é mesmo um FITS legível antes de aceitar.
            meta = read_fits_metadata(file_path)
            if meta.get("error"):
                file_path.unlink(missing_ok=True)
                skipped_files.append({"filename": safe_name, "reason": meta["error"]})
                continue

            saved_files.append(safe_name)
            logger.info("Upload salvo: %s", file_path)
        except Exception as e:
            logger.exception("Erro ao salvar %s", safe_name)
            skipped_files.append({"filename": safe_name, "reason": str(e)})
        finally:
            await upload.close()

    return {
        "status": "success" if saved_files else "rejected",
        "saved": saved_files,
        "skipped": skipped_files,
        "total_saved": len(saved_files),
        "total_skipped": len(skipped_files),
        "accepted_extensions": ACCEPTED_EXTENSIONS_LABEL,
    }


@app.delete("/api/frames/{folder}")
async def clear_folder(folder: str, only_sample: bool = True):
    """
    Remove FITS de uma das pastas de entrada.

    O padrão ``only_sample=true`` apaga apenas o dataset de demonstração
    (arquivos com o prefixo ``demo_``), que o próprio backend gerou e pode
    regerar. Apagar arquivos enviados pelo usuário exige ``only_sample=false``
    explicitamente — remover dados de observação por engano é irreversível.
    """
    if folder not in FOLDERS:
        raise HTTPException(status_code=400, detail=f"Pasta inválida '{folder}'.")

    removed = []
    for path in find_fits_files(FOLDERS[folder]):
        if only_sample and not path.name.startswith(f"{SAMPLE_PREFIX}_"):
            continue
        try:
            path.unlink()
            removed.append(path.name)
        except Exception as e:
            logger.warning("Não foi possível remover %s: %s", path, e)

    return {"status": "success", "removed": removed, "total_removed": len(removed)}


def _count_user_frames() -> int:
    """Quantos FITS não pertencentes ao dataset de demonstração há na entrada."""
    return sum(
        1
        for folder in FOLDERS.values()
        for path in find_fits_files(folder)
        if not path.name.startswith(f"{SAMPLE_PREFIX}_")
    )


@app.get("/api/sample/status")
async def sample_status():
    """Informa se há dados do usuário que colidiriam com o dataset de exemplo."""
    return {
        "status": "success",
        "user_frames": _count_user_frames(),
        "sample_loaded": any(
            p.name.startswith(f"{SAMPLE_PREFIX}_")
            for folder in FOLDERS.values()
            for p in find_fits_files(folder)
        ),
    }


@app.post("/api/sample/load", response_model=SampleDataResponse)
async def load_sample_dataset(exclusive: bool = False):
    """
    Gera o dataset FITS de exemplo para testar o software de ponta a ponta.

    Escreve em ``dados/ciencia`` uma série de frames sintéticos contendo um
    objeto em movimento linear com taxa conhecida, e em ``dados/referencia`` o
    frame de referência do mesmo campo sem o objeto. Serve para validar a
    instalação sem depender de download de arquivos públicos.

    Parameters
    ----------
    exclusive : bool
        Quando verdadeiro, os FITS que já estavam nas pastas de entrada são
        **movidos** para ``dados/arquivados_<timestamp>/`` antes da geração, de
        modo que o pipeline rode só com o exemplo. Os arquivos do usuário nunca
        são apagados: misturar campos diferentes quebraria a subtração (formas
        e coordenadas incompatíveis), mas descartar observações reais seria
        irreversível.
    """
    try:
        archived: List[str] = []
        if exclusive:
            stamp = time.strftime("%Y%m%d_%H%M%S")
            for name, folder in FOLDERS.items():
                existing = [p for p in find_fits_files(folder) if not p.name.startswith(f"{SAMPLE_PREFIX}_")]
                if not existing:
                    continue
                archive_dir = DADOS_DIR / f"arquivados_{stamp}" / name
                archive_dir.mkdir(parents=True, exist_ok=True)
                for path in existing:
                    shutil.move(str(path), str(archive_dir / path.name))
                    archived.append(f"{name}/{path.name}")
            if archived:
                logger.info("%d arquivo(s) movidos para dados/arquivados_%s/", len(archived), stamp)

        info = generate_sample_dataset(
            science_dir=SCIENCE_DIR,
            reference_dir=REFERENCE_DIR,
            prefix=SAMPLE_PREFIX,
        )
        info["archived"] = archived

        expected = info["expected"]
        message = (
            f"{len(info['science_files'])} frames de ciência + 1 referência gerados. "
            f"O objeto sintético se move a {expected['total_rate_arcsec_hr']}\"/hr "
            f"(μα={expected['mu_ra_arcsec_hr']}\"/hr, μδ={expected['mu_dec_arcsec_hr']}\"/hr)."
        )
        if archived:
            message += f" {len(archived)} arquivo(s) anteriores foram movidos para dados/arquivados_{stamp}/."
        return {"status": "success", "message": message, "dataset": info}
    except Exception as e:
        logger.exception("Erro ao gerar o dataset de exemplo")
        raise HTTPException(status_code=500, detail=f"Falha ao gerar o dataset de exemplo: {e}")


@app.post("/api/pipeline/run", response_model=RunPipelineResponse)
async def run_pipeline(request: RunPipelineRequest):
    """
    Executa o pipeline astrométrico completo.
    """
    start_time = time.time()

    try:
        # Parte do YAML e sobrescreve apenas o que a interface controla.
        config = _load_base_config()
        config.setdefault("detection", {}).update({
            "significance_threshold": request.sigma,
            "max_elongation": request.elongation,
        })
        config.setdefault("trajectory", {}).update({
            "max_chi2_reduced": request.chi2,
        })
        config.setdefault("subtraction", {})
        config.setdefault("calibration", {})
        config.setdefault("export", {})

        science_files = find_fits_files(SCIENCE_DIR)
        if not science_files:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Nenhum frame de ciência encontrado em 'dados/ciencia'. "
                    "Envie arquivos FITS ou carregue o dataset de exemplo."
                ),
            )

        min_frames = int(config.get("trajectory", {}).get("min_frames", 3))
        if len(science_files) < min_frames:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"São necessários pelo menos {min_frames} frames de ciência para linkar "
                    f"uma trajetória; há apenas {len(science_files)}."
                ),
            )

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        ref_files = find_fits_files(REFERENCE_DIR)
        reference_fits = ref_files[0] if ref_files else None

        if not reference_fits:
            logger.info("Imagem de referência não encontrada. Tentando download automático...")
            from pipeline.reference_fetcher import ReferenceFetcher
            reference_fits = ReferenceFetcher.fetch_reference(SCIENCE_DIR, REFERENCE_DIR)
            if not reference_fits:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Nenhum frame de referência em 'dados/referencia' e o download "
                        "automático do SkyView falhou. Envie um FITS de referência ou "
                        "carregue o dataset de exemplo."
                    ),
                )

        pipeline_runner = SpaceFindXPipeline(config)
        ades_path, tracklets = pipeline_runner.run(
            science_dir=SCIENCE_DIR,
            reference_fits=reference_fits,
            output_dir=OUTPUT_DIR,
            log_callback=None,
        )

        final_tracklets = []
        for i, t in enumerate(tracklets or []):
            if t.sky_coords:
                first_coord = t.sky_coords[0]
                ra_str = first_coord.ra.to_string(unit='hour', sep=' ', precision=3, pad=True)
                dec_str = first_coord.dec.to_string(unit='degree', sep=' ', precision=2, pad=True, alwayssign=True)
            else:
                ra_str, dec_str = "00 00 00.000", "+00 00 00.00"

            final_tracklets.append({
                "id": getattr(t, "tracklet_id", f"TRK_{(i+1):04d}"),
                "ra": ra_str,
                "dec": dec_str,
                "mu_ra": _safe_float(getattr(t, 'mu_ra_arcsec_hr', 0.0)),
                "mu_dec": _safe_float(getattr(t, 'mu_dec_arcsec_hr', 0.0)),
                "chi2": _safe_float(getattr(t, 'chi2_reduced', 0.0)),
                "rms_ra": _safe_float(getattr(t, 'ra_rms_arcsec', 0.0)),
                "rms_dec": _safe_float(getattr(t, 'dec_rms_arcsec', 0.0)),
                "frames": len(getattr(t, 'sky_coords', []) or []),
                "status": "CONFIRMED",
            })

        if final_tracklets:
            message = f"{len(final_tracklets)} tracklet(s) confirmada(s)."
        else:
            message = (
                "Pipeline concluído sem tracklets confirmadas. "
                "Nenhum objeto em movimento passou nos filtros — tente reduzir o limiar "
                "sigma ou aumentar o χ² máximo."
            )

        return {
            "status": "success",
            "tracklets": final_tracklets,
            "execution_time": time.time() - start_time,
            "frames_processed": len(science_files),
            "reference_used": reference_fits.name,
            "message": message + (f" ADES: {ades_path.name}" if ades_path else ""),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Erro durante execução do pipeline")
        raise HTTPException(status_code=500, detail=str(e))


# ─── INTERFACE WEB ESTÁTICA ─────────────────────────────────────────────────────
# Servir a UI pelo próprio backend faz frontend e API compartilharem a mesma
# origem. Isso mantém o app funcional quando ele é exposto por um túnel
# (cloudflared) ou acessado de outra máquina da rede, situações em que a URL
# fixa http://localhost:8000 gravada no frontend não resolve no navegador.
_STATIC_FILES = {
    "/": (BASE_DIR / "index.html", "text/html"),
    "/index.html": (BASE_DIR / "index.html", "text/html"),
    "/app.js": (BASE_DIR / "app.js", "application/javascript"),
    "/style.css": (BASE_DIR / "style.css", "text/css"),
}


def _make_static_route(file_path: Path, media_type: str):
    async def _serve():
        if not file_path.exists():
            raise HTTPException(status_code=404, detail=f"{file_path.name} não encontrado")
        return FileResponse(file_path, media_type=media_type)
    return _serve


for _route, (_path, _media) in _STATIC_FILES.items():
    app.add_api_route(_route, _make_static_route(_path, _media), methods=["GET"], include_in_schema=False)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
