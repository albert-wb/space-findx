import os
import sys
import shutil
import time
import logging
from pathlib import Path
from typing import List, Dict, Any

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Garante que o diretório raiz está no path para importar módulos da pasta pipeline/
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline.pipeline import SpaceFindXPipeline
from pipeline.ingestor import ImageIngestor

logger = logging.getLogger(__name__)

# ─── CONSTANTES ──────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
DADOS_DIR = BASE_DIR / "dados"
SCIENCE_DIR = DADOS_DIR / "ciencia"
REFERENCE_DIR = DADOS_DIR / "referencia"
OUTPUT_DIR = BASE_DIR / "saida"
ALLOWED_EXTENSIONS = {".fit", ".fits"}
FITS_GLOB = "*.fit*"

app = FastAPI(
    title="SPACE-FINDX API",
    description="Backend API para detecção de NEOs usando ZOGY e Astrometria",
    version="1.0.0"
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

# ─── ENDPOINTS ─────────────────────────────────────────────────────────────────

@app.get("/api/status")
async def get_status():
    """Health check do servidor."""
    return {"status": "online", "message": "SPACE-FINDX Backend is running"}

def _safe_float(val, default: float = 0.0) -> float:
    """Converte valor para float de forma segura, tratando NaN e strings vazias."""
    try:
        result = float(val)
        import math
        return default if math.isnan(result) else result
    except (ValueError, TypeError):
        return default


def _find_fits_files(directory: Path) -> List[Path]:
    """Retorna lista de arquivos .fit/.fits num diretório, ignorando subpastas."""
    if not directory.exists():
        return []
    return [f for f in directory.iterdir() if f.is_file() and f.suffix.lower() in ALLOWED_EXTENSIONS]


@app.get("/api/frames")
async def get_science_frames():
    """
    Varre a pasta 'dados/ciencia', lê os cabeçalhos FITS de forma leve
    usando o ImageIngestor e retorna a lista de imagens para a galeria Web.
    """
    try:
        if not _find_fits_files(SCIENCE_DIR):
            return {"status": "empty", "frames": []}
            
        ingestor = ImageIngestor(SCIENCE_DIR, required_keys=["DATE-OBS"], pattern=FITS_GLOB)
        df = ingestor.scan_directory()
        
        frames_list = []
        for _, row in df.iterrows():
            def get_val(key_upper, default):
                return row.get(key_upper, row.get(key_upper.lower(), default))

            frames_list.append({
                "filename": row.get("file", "unknown.fits"),
                "date_obs": str(get_val("DATE-OBS", "N/A")),
                "filter": str(get_val("FILTER", "Clear")),
                "exptime": _safe_float(get_val("EXPTIME", 0.0))
            })
            
        return {
            "status": "success",
            "frames": frames_list
        }
    except FileNotFoundError:
        return {"status": "empty", "frames": []}
    except Exception as e:
        logger.exception("Erro ao listar science frames")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/frames/reference")
async def get_reference_frames():
    """
    Varre a pasta 'dados/referencia' e retorna os metadados dos frames de referência
    disponíveis, de forma idêntica ao endpoint de ciência.
    """
    try:
        if not _find_fits_files(REFERENCE_DIR):
            return {"status": "empty", "frames": []}
            
        ingestor = ImageIngestor(REFERENCE_DIR, required_keys=[], pattern=FITS_GLOB)
        df = ingestor.scan_directory()
        
        frames_list = []
        for _, row in df.iterrows():
            def get_val(key_upper, default):
                return row.get(key_upper, row.get(key_upper.lower(), default))

            frames_list.append({
                "filename": row.get("file", "unknown.fits"),
                "date_obs": str(get_val("DATE-OBS", "N/A")),
                "filter": str(get_val("FILTER", "Clear")),
                "exptime": _safe_float(get_val("EXPTIME", 0.0))
            })
            
        return {
            "status": "success",
            "frames": frames_list
        }
    except FileNotFoundError:
        return {"status": "empty", "frames": []}
    except Exception as e:
        logger.exception("Erro ao listar reference frames")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/upload/{folder}")
async def upload_files(folder: str, files: List[UploadFile] = File(...)):
    """
    Recebe múltiplos arquivos FITS e salva na pasta correspondente.
    Valida extensão e retorna lista detalhada com arquivos salvos e rejeitados.
    """
    valid_folders = {"ciencia": SCIENCE_DIR, "referencia": REFERENCE_DIR}
    if folder not in valid_folders:
        raise HTTPException(
            status_code=400,
            detail=f"Pasta inválida '{folder}'. Use: {', '.join(valid_folders.keys())}"
        )
        
    target_dir = valid_folders[folder]
    target_dir.mkdir(parents=True, exist_ok=True)
    
    saved_files = []
    skipped_files = []
    
    for upload in files:
        # Sanitiza o nome do arquivo para evitar path traversal
        safe_name = Path(upload.filename).name
        ext = Path(safe_name).suffix.lower()
        
        if ext not in ALLOWED_EXTENSIONS:
            skipped_files.append({"filename": safe_name, "reason": f"Extensão '{ext}' não permitida"})
            continue
            
        file_path = target_dir / safe_name
        try:
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(upload.file, buffer)
            saved_files.append(safe_name)
            logger.info(f"Upload salvo: {file_path}")
        except Exception as e:
            logger.exception(f"Erro ao salvar {safe_name}")
            skipped_files.append({"filename": safe_name, "reason": str(e)})
        finally:
            await upload.close()
            
    return {
        "status": "success",
        "saved": saved_files,
        "skipped": skipped_files,
        "total_saved": len(saved_files),
        "total_skipped": len(skipped_files)
    }

@app.post("/api/pipeline/run", response_model=RunPipelineResponse)
async def run_pipeline(request: RunPipelineRequest):
    """
    Executa o pipeline astrométrico completo.
    """
    start_time = time.time()
    
    try:
        # Mapeando os parâmetros do Web UI para a configuração do Python Pipeline
        config = {
            "detection": {
                "significance_threshold": request.sigma,
                "max_elongation": request.elongation,
                "fwhm_pixels": 3.0
            },
            "trajectory": {
                "max_chi2_reduced": request.chi2,
                "min_frames": 3
            },
            "subtraction": {
                "psf_fwhm_pixels": 3.0
            }
            # Adicionar outros se necessário
        }

        # Inicializando o objeto do pipeline
        pipeline_runner = SpaceFindXPipeline(config)

        # Configurando as Hot Folders
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        
        ref_files = _find_fits_files(REFERENCE_DIR)
        reference_fits = ref_files[0] if ref_files else None

        # Verifica se as pastas existem e têm arquivos
        if not _find_fits_files(SCIENCE_DIR):
            raise Exception("Pasta 'dados/ciencia' vazia ou sem arquivos .fit/.fits.")
            
        if not reference_fits:
            logger.info("Imagem de referência não encontrada. Tentando download automático...")
            from pipeline.reference_fetcher import ReferenceFetcher
            reference_fits = ReferenceFetcher.fetch_reference(SCIENCE_DIR, REFERENCE_DIR)
            if not reference_fits:
                raise Exception("Nenhum arquivo de referência (.fit/.fits) encontrado em 'dados/referencia/' e o download falhou.")

        # Roda o processamento completo
        # NOTA: Demorará dependendo da CPU, ZOGY fará as FFTs 2D
        ades_path, tracklets = pipeline_runner.run(
            science_dir=SCIENCE_DIR,
            reference_fits=reference_fits,
            output_dir=OUTPUT_DIR,
            log_callback=None
        )

        # Formata os tracklets retornados para a API Web
        final_tracklets = []
        if tracklets:
            for i, t in enumerate(tracklets):
                # Extrai a coordenada do primeiro frame (ou média) e converte para string sexagesimal
                if t.sky_coords and len(t.sky_coords) > 0:
                    first_coord = t.sky_coords[0]
                    ra_str = first_coord.ra.to_string(unit='hour', sep=' ', precision=3, pad=True)
                    dec_str = first_coord.dec.to_string(unit='degree', sep=' ', precision=2, pad=True, alwayssign=True)
                else:
                    ra_str, dec_str = "00 00 00.000", "+00 00 00.00"

                final_tracklets.append({
                    "id": f"TRK_{(i+1):04d}",
                    "ra": ra_str,
                    "dec": dec_str,
                    "mu_ra": getattr(t, 'mu_ra_arcsec_hr', 0.0),
                    "mu_dec": getattr(t, 'mu_dec_arcsec_hr', 0.0),
                    "chi2": getattr(t, 'chi2_reduced', 0.0),
                    "status": "CONFIRMED"
                })

        return {
            "status": "success",
            "tracklets": final_tracklets,
            "execution_time": time.time() - start_time
        }

    except Exception as e:
        logger.exception("Erro durante execução do pipeline")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
