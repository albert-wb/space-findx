"""
Módulo 2: Astrometria e Alinhamento
===================================

Responsável por alinhar imagens da série ao WCS da imagem de referência,
permitindo a subtração de imagens no mesmo referencial de coordenadas.
"""

import logging
from astropy.wcs import WCS
from astropy.nddata import CCDData
import numpy as np

try:
    from reproject import reproject_interp
except ImportError:
    reproject_interp = None

logger = logging.getLogger(__name__)

class AstrometricAligner:
    """
    Classe para realizar calibração astrométrica e alinhamento
    de imagens usando astropy.wcs e reproject.
    """
    
    def __init__(self, sip_order=3, match_radius_arcsec=2.0, min_stars=10, rms_threshold_arcsec=0.5):
        self.sip_order = sip_order
        self.match_radius = match_radius_arcsec
        self.min_stars = min_stars
        self.rms_threshold = rms_threshold_arcsec

    def align_to_reference(self, image: CCDData, reference: CCDData) -> CCDData:
        """
        Alinha a imagem atual ao sistema de coordenadas WCS da imagem de referência.
        
        Parameters
        ----------
        image : CCDData
            A imagem de ciência calibrada a ser alinhada.
        reference : CCDData
            A imagem de referência já calibrada.
            
        Returns
        -------
        CCDData
            A imagem alinhada e reprojetada para a mesma grade de pixels da referência.
        """
        logger.info("Iniciando alinhamento astrométrico...")
        
        try:
            import warnings
            from astropy.wcs import FITSFixedWarning
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', FITSFixedWarning)
                wcs_in = WCS(image.header)
                wcs_out = WCS(reference.header)
                has_wcs = wcs_in.has_celestial and wcs_out.has_celestial
        except Exception as e:
            logger.warning(f"Cabeçalho FITS com WCS inválido ({e}). Pulando reprojeção WCS...")
            wcs_in = None
            wcs_out = None
            has_wcs = False
        
        # Usar reproject se ele estiver instalado e tivermos informações WCS válidas
        if reproject_interp and has_wcs:
            try:
                logger.info("Reprojetando imagem para o WCS de referência...")
                array_aligned, footprint = reproject_interp(
                    (image.data, wcs_in), 
                    wcs_out, 
                    shape_out=reference.data.shape
                )
                
                # Reproject retorna NaN fora das bordas, podemos querer converter para 0 ou preservar
                # Substituindo NaNs por 0 ou fundo para evitar problemas na subtração de FITS
                array_aligned = np.nan_to_num(array_aligned, nan=0.0)
                
                aligned_ccd = CCDData(array_aligned, wcs=wcs_out, unit=image.unit)
                
                # Criamos um novo header combinando as propriedades base da imagem
                # e a geometria do WCS da referência
                aligned_ccd.header = image.header.copy()
                
                # Atualiza chaves de WCS no header
                wcs_header = wcs_out.to_header()
                for key in wcs_header.keys():
                    aligned_ccd.header[key] = wcs_header[key]
                    
                return aligned_ccd
            except Exception as reproj_err:
                logger.warning(
                    f"Erro durante reprojeção astrométrica: {reproj_err}. "
                    "Retornando imagem original sem alinhamento rigoroso."
                )
            
        logger.warning(
            "WCS inválido ou pacote reproject não instalado. "
            "Retornando imagem original sem alinhamento rigoroso."
        )
        
        # Fallback: Retorna a imagem sem reprojetar os pixels
        return image
