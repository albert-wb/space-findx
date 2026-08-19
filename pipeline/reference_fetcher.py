import logging
from pathlib import Path
from typing import Optional

from astropy.io import fits
from astropy.wcs import WCS
from astropy.coordinates import SkyCoord
import astropy.units as u
from astroquery.skyview import SkyView

from .fits_utils import wcs_from_header

logger = logging.getLogger(__name__)

# Teto para o recorte pedido ao SkyView. Acima disso o download deixa de ser
# prático e o DSS já não acrescenta informação real (sua resolução nativa é da
# ordem de 1 arcsec/pixel).
MAX_REFERENCE_PIXELS = 2048


class ReferenceFetcher:
    """
    Realiza o download automático de um frame de referência (ex: DSS) 
    baseado nas coordenadas e Field of View (FOV) da primeira imagem de ciência.
    """
    
    @staticmethod
    def fetch_reference(science_dir: Path, output_dir: Path) -> Optional[Path]:
        try:
            # 1. Encontrar o primeiro arquivo FITS de ciência
            science_files = list(science_dir.glob("*.fit*"))
            if not science_files:
                logger.error("Nenhuma imagem de ciência para extrair as coordenadas.")
                return None
                
            first_science = science_files[0]
            science_shape = None
            logger.info(f"Extraindo coordenadas de {first_science.name} para download da referência...")
            
            with fits.open(first_science) as hdul:
                # Normalmente, imagens calibradas têm os dados na extensão 0
                header = hdul[0].header
                
                # `wcs_from_header` reconstrói o WCS quando o cabeçalho traz
                # convenções legadas que fazem a wcslib abortar — sem isso, um
                # frame com astrometria perfeitamente válida caía no fallback de
                # RA/DEC e o campo baixado saía com raio e amostragem errados.
                wcs = wcs_from_header(header)
                has_wcs = wcs is not None
                if not has_wcs:
                    logger.warning("WCS não pôde ser interpretado. Usando fallback de RA/DEC do cabeçalho...")

                # A forma do frame é lida do cabeçalho e vale mesmo sem WCS:
                # é ela que define a amostragem pedida ao SkyView.
                try:
                    science_shape = (int(header["NAXIS2"]), int(header["NAXIS1"]))
                except (KeyError, TypeError, ValueError):
                    science_shape = None
                
                # Se tiver WCS completo:
                if has_wcs:
                    ny, nx = science_shape if science_shape else hdul[0].data.shape
                    # Pega o centro
                    center_sky = wcs.pixel_to_world(nx/2, ny/2)
                    
                    # Estimar tamanho do campo (FOV) em arcmin
                    p1 = wcs.pixel_to_world(0, ny/2)
                    p2 = wcs.pixel_to_world(nx, ny/2)
                    fov_deg = p1.separation(p2).degree
                    radius_arcmin = (fov_deg / 2) * 60.0
                    if radius_arcmin <= 0:
                        radius_arcmin = 15.0
                else:
                    # Tenta ler RA/DEC direto do header caso não tenha WCS resolvido
                    if 'RA' in header and 'DEC' in header:
                        # Em telescópios amadores geralmente RA vem em horas e DEC em graus
                        try:
                            center_sky = SkyCoord(header['RA'], header['DEC'], unit=(u.hourangle, u.deg))
                        except ValueError:
                            # Se falhar, talvez já esteja em graus
                            center_sky = SkyCoord(header['RA'], header['DEC'], unit=(u.deg, u.deg))
                    elif 'CRVAL1' in header and 'CRVAL2' in header:
                        center_sky = SkyCoord(header['CRVAL1'], header['CRVAL2'], unit=(u.deg, u.deg))
                    else:
                        logger.error("Não foi possível encontrar WCS ou RA/DEC no cabeçalho da imagem.")
                        return None
                    radius_arcmin = 15.0 # Padrão
                    
            # Ampliar o raio em 10% para garantir sobreposição nas bordas
            radius_arcmin *= 1.1
            
            logger.info(f"Coordenadas centrais (RA={center_sky.ra.deg:.4f}, DEC={center_sky.dec.deg:.4f}).")
            logger.info(f"Buscando no SkyView (DSS) com raio de {radius_arcmin:.1f} arcmin...")

            # Amostragem do recorte. Sem este parâmetro o SkyView devolve um
            # recorte de 300x300 px: para um frame de ciência de alguns milhares
            # de pixels isso é uma referência dezenas de vezes mais grosseira, e
            # reamostrar a ciência nessa grade jogava fora quase toda a
            # resolução antes da subtração. Pedimos um número de pixels
            # compatível com o frame de ciência (com teto, para não gerar um
            # download gigante).
            requested_pixels = 300
            if science_shape is not None:
                requested_pixels = int(min(max(science_shape), MAX_REFERENCE_PIXELS))
                requested_pixels = max(requested_pixels, 300)
            logger.info(f"Amostragem solicitada ao SkyView: {requested_pixels}x{requested_pixels} px.")

            # 2. Fazer query no SkyView (usamos o Digitized Sky Survey padrão)
            hdulist_list = SkyView.get_images(
                position=center_sky,
                survey=['DSS'],
                radius=radius_arcmin * u.arcmin,
                pixels=str(requested_pixels),
            )
            
            if not hdulist_list:
                logger.error("SkyView não retornou nenhuma imagem para essas coordenadas.")
                return None
                
            # 3. Salvar o arquivo baixado
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / "reference_downloaded.fits"
            
            # O SkyView retorna uma lista de HDULists. Pegamos a primeira
            downloaded_hdul = hdulist_list[0]
            
            # Garantir que não haja conflitos de tipo e salvar
            downloaded_hdul.writeto(output_path, overwrite=True)
            logger.info(f"Frame de referência baixado com sucesso: {output_path}")
            
            return output_path
            
        except Exception as e:
            logger.exception("Falha ao tentar baixar frame de referência.")
            return None
