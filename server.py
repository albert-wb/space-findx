import os
import sys
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import time
import random

# Garante que o diretório raiz está no path para importar módulos da pasta pipeline/
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline.pipeline import SpaceFindXPipeline
from pipeline.ingestor import ImageIngestor
from pathlib import Path
import logging

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

@app.get("/api/frames")
async def get_science_frames():
    """
    Varre a pasta 'dados/ciencia', lê os cabeçalhos FITS de forma leve
    usando o ImageIngestor e retorna a lista de imagens para a galeria Web.
    """
    try:
        BASE_DIR = Path(__file__).resolve().parent
        science_dir = BASE_DIR / "dados" / "ciencia"
        
        if not science_dir.exists() or not any(science_dir.iterdir()):
            return {"status": "empty", "frames": []}
            
        ingestor = ImageIngestor(science_dir, required_keys=["DATE-OBS"])
        df = ingestor.scan_directory()
        
        frames_list = []
        # Para cada arquivo do dataframe, extrai os metadados principais
        for idx, row in df.iterrows():
            frames_list.append({
                "filename": row.get("file", "unknown.fits"),
                "date_obs": str(row.get("DATE-OBS", "N/A")),
                "filter": str(row.get("FILTER", "Clear")),
                "exptime": float(row.get("EXPTIME", 0.0))
            })
            
        return {
            "status": "success",
            "frames": frames_list
        }
    except FileNotFoundError:
        return {"status": "empty", "frames": []}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

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
        BASE_DIR = Path(__file__).resolve().parent
        science_dir = BASE_DIR / "dados" / "ciencia"
        # Supomos que o usuário colocou o frame de referência nomeado 'reference.fits'
        reference_fits = BASE_DIR / "dados" / "referencia" / "reference.fits"
        output_dir = BASE_DIR / "saida"

        # Verifica se as pastas existem e têm arquivos (se não, caímos na exception)
        if not science_dir.exists() or not any(science_dir.iterdir()):
            raise Exception("Pasta 'dados/ciencia' vazia ou inexistente. Coloque FITS de ciência nela.")
        if not reference_fits.exists():
            raise Exception("Arquivo de referência 'reference.fits' não encontrado na pasta 'dados/referencia'.")

        # Roda o processamento completo
        # NOTA: Demorará dependendo da CPU, ZOGY fará as FFTs 2D
        ades_path, tracklets = pipeline_runner.run(
            science_dir=science_dir,
            reference_fits=reference_fits,
            output_dir=output_dir,
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
        # Se os FITS não existirem ou erro no astropy/ZOGY, retorna erro limpo para UI
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
