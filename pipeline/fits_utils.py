"""
Utilitários compartilhados de descoberta e leitura leve de arquivos FITS
=======================================================================

Centraliza duas responsabilidades que antes estavam duplicadas (e divergentes)
entre ``server.py``, ``pipeline/ingestor.py`` e ``pipeline/calibration.py``:

1. **Quais arquivos contam como FITS.** Levantamentos reais distribuem os
   frames com extensões variadas — ``.fits``, ``.fit``, ``.fts`` e as versões
   comprimidas ``.fits.fz`` (Rice/tile compression) e ``.fits.gz``. Um glob
   simples como ``*.fits`` descarta silenciosamente boa parte dos arquivos que
   o usuário acabou de enviar, e ``Path('x.fits.fz').suffix`` devolve ``.fz``,
   o que quebra whitelists baseadas apenas no último sufixo.

2. **Como extrair metadados sem carregar pixels.** Em arquivos MEF
   (Multi-Extension FITS) e comprimidos, a HDU 0 costuma ser um stub vazio: o
   cabeçalho científico (``DATE-OBS``, ``EXPTIME``, ``FILTER``, WCS) vive na
   extensão 1. Ler apenas ``fits.getheader(path)`` devolve metadados vazios.
   ``read_fits_header`` percorre as HDUs e devolve o cabeçalho da primeira que
   contém dados, mesclando as chaves da primária (que carrega informação de
   instrumento em muitos arquivos).
"""

import logging
from pathlib import Path
from typing import List, Optional

from astropy.io import fits

logger = logging.getLogger(__name__)

# Sufixos "base" reconhecidos como FITS.
FITS_SUFFIXES = {".fits", ".fit", ".fts"}
# Sufixos de compressão que podem aparecer depois do sufixo base.
COMPRESSION_SUFFIXES = {".fz", ".gz", ".bz2", ".zip", ".z"}


def is_fits_file(path) -> bool:
    """
    Diz se o caminho aparenta ser um FITS, considerando extensões compostas.

    Aceita ``a.fits``, ``a.fit``, ``a.fts``, ``a.fits.fz``, ``a.fit.gz``, etc.
    A comparação é case-insensitive porque câmeras e arquivos públicos
    frequentemente usam ``.FIT`` ou ``.FITS`` em maiúsculas.
    """
    suffixes = [s.lower() for s in Path(str(path)).suffixes]
    if not suffixes:
        return False

    last = suffixes[-1]
    if last in FITS_SUFFIXES:
        return True
    # Extensão composta: o sufixo de compressão vem depois do sufixo FITS.
    if last in COMPRESSION_SUFFIXES and len(suffixes) >= 2:
        return suffixes[-2] in FITS_SUFFIXES
    return False


def find_fits_files(directory, recursive: bool = False) -> List[Path]:
    """
    Lista os arquivos FITS de um diretório, ordenados por nome.

    A ordenação é determinística de propósito: o backend escolhe o "primeiro"
    frame de referência a partir dessa lista, e a ordem do sistema de arquivos
    não é estável entre plataformas.
    """
    directory = Path(directory)
    if not directory.exists():
        return []

    iterator = directory.rglob("*") if recursive else directory.iterdir()
    return sorted(
        (f for f in iterator if f.is_file() and is_fits_file(f)),
        key=lambda p: p.name.lower(),
    )


def read_fits_header(path) -> fits.Header:
    """
    Devolve o cabeçalho "científico" de um FITS, tolerante a MEF/compressão.

    Estratégia: percorre as HDUs e escolhe a primeira que efetivamente contém
    dados de imagem; as chaves da HDU primária são usadas como preenchimento
    para o que faltar. Se nenhuma HDU tiver dados, devolve o cabeçalho da
    primária.
    """
    path = Path(path)
    with fits.open(path, ignore_missing_end=True, memmap=False) as hdul:
        primary = hdul[0].header

        data_header: Optional[fits.Header] = None
        for hdu in hdul:
            # `hdu.shape` não força a leitura dos pixels (ao contrário de .data)
            shape = getattr(hdu, "shape", None)
            if shape:
                data_header = hdu.header
                break

        if data_header is None:
            return primary.copy()

        merged = data_header.copy()
        for key, value in primary.items():
            if key and key not in merged:
                try:
                    merged[key] = value
                except Exception:  # chaves estruturais/inválidas são ignoradas
                    continue
        return merged


def read_fits_metadata(path) -> dict:
    """
    Extrai os metadados que a interface Web exibe na galeria.

    Nunca levanta exceção: um frame ilegível vira uma linha com ``error``
    preenchido, para que a UI possa mostrar o motivo em vez de simplesmente
    sumir com o arquivo da lista.
    """
    path = Path(path)
    meta = {
        "filename": path.name,
        "date_obs": "N/A",
        "filter": "Clear",
        "exptime": 0.0,
        "naxis1": None,
        "naxis2": None,
        "object": None,
        "instrument": None,
        "telescope": None,
        "has_wcs": False,
        "size_mb": round(path.stat().st_size / (1024 * 1024), 2) if path.exists() else 0.0,
        "error": None,
    }

    try:
        hdr = read_fits_header(path)
    except Exception as exc:
        logger.warning("Não foi possível ler o cabeçalho de %s: %s", path.name, exc)
        meta["error"] = f"Cabeçalho FITS ilegível: {exc}"
        return meta

    def first(*keys, default=None):
        for k in keys:
            if k in hdr and hdr[k] not in (None, ""):
                return hdr[k]
        return default

    meta["date_obs"] = str(first("DATE-OBS", "DATE_OBS", "DATE-BEG", "DATE-AVG", default="N/A"))
    meta["filter"] = str(first("FILTER", "FILTER1", "FILTNAM", "BAND", default="Clear"))
    meta["object"] = first("OBJECT")
    meta["instrument"] = first("INSTRUME", "CAMERA")
    meta["telescope"] = first("TELESCOP")
    meta["naxis1"] = first("NAXIS1", "ZNAXIS1")
    meta["naxis2"] = first("NAXIS2", "ZNAXIS2")
    meta["has_wcs"] = all(k in hdr for k in ("CRVAL1", "CRVAL2")) and (
        "CTYPE1" in hdr or "CD1_1" in hdr or "CDELT1" in hdr
    )

    try:
        exptime = first("EXPTIME", "EXPOSURE", "ITIME", "TELAPSE", default=0.0)
        meta["exptime"] = float(exptime)
    except (TypeError, ValueError):
        meta["exptime"] = 0.0

    return meta


# Prefixos e nomes das palavras-chave que definem um WCS FITS padrão (incluindo
# distorção SIP). Usados para reconstruir um WCS limpo quando o cabeçalho
# completo contém convenções legadas que a wcslib rejeita.
_CORE_WCS_PREFIXES = (
    "CTYPE", "CRVAL", "CRPIX", "CDELT", "CUNIT", "CROTA",
    "CD1_", "CD2_", "PC1_", "PC2_", "PV1_", "PV2_",
    "A_", "B_", "AP_", "BP_",
)
_CORE_WCS_KEYS = (
    "WCSAXES", "NAXIS", "NAXIS1", "NAXIS2",
    "LONPOLE", "LATPOLE", "EQUINOX", "RADESYS", "RADECSYS", "MJD-OBS", "DATE-OBS",
    "A_ORDER", "B_ORDER", "AP_ORDER", "BP_ORDER",
)


def wcs_from_header(header):
    """
    Constrói um WCS a partir de um cabeçalho, tolerando convenções legadas.

    Alguns arquivos de levantamentos reais (Pan-STARRS, por exemplo) trazem, ao
    lado de um WCS padrão perfeitamente válido, palavras-chave herdadas de
    convenções antigas — ``CNPIX1``/``CNPIX2`` do formato DSS são o caso
    encontrado em campo. A wcslib tenta interpretá-las junto com o WCS padrão e
    aborta com ``SingularMatrixError: PCi_ja matrix is singular``, e o astropy
    então descarta o WCS inteiro (``CCDData.wcs`` vira ``None``). O frame passa
    a ser tratado como se não tivesse astrometria alguma.

    Por isso, quando a leitura do cabeçalho completo falha, o WCS é reconstruído
    apenas com as palavras-chave do padrão FITS (mantendo CD/PC, PV e a
    distorção SIP), descartando o resto.

    Returns
    -------
    astropy.wcs.WCS or None
    """
    import warnings

    from astropy.io import fits as _fits
    from astropy.wcs import WCS, FITSFixedWarning

    if header is None:
        return None

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FITSFixedWarning)
        try:
            candidate = WCS(header)
            if candidate.has_celestial:
                return candidate
        except Exception as exc:
            logger.debug("WCS do cabeçalho completo rejeitado (%s). Tentando versão limpa.", exc)

        # Segunda tentativa: só as chaves do padrão FITS.
        clean = _fits.Header()
        for key in header:
            if not key:
                continue
            upper = key.upper()
            if upper in _CORE_WCS_KEYS or upper.startswith(_CORE_WCS_PREFIXES):
                try:
                    clean[key] = header[key]
                except Exception:
                    continue

        try:
            candidate = WCS(clean)
            if candidate.has_celestial:
                logger.info(
                    "WCS reconstruído a partir das palavras-chave padrão "
                    "(cabeçalho continha convenções legadas incompatíveis)."
                )
                return candidate
        except Exception as exc:
            logger.debug("WCS limpo também falhou: %s", exc)

    return None


def effective_wcs(ccd, fallback_shape=None):
    """
    Devolve o WCS realmente utilizável de um ``CCDData``.

    ``CCDData.read`` **remove** as palavras-chave de WCS (``CTYPE*``, ``CRVAL*``,
    ``CD*_*`` …) do atributo ``.header`` e as move para o atributo ``.wcs``.
    Reconstruir o WCS com ``WCS(ccd.header)`` — como o pipeline fazia — devolve
    portanto um WCS vazio, sem eixos celestes: o alinhamento astrométrico caía
    silenciosamente no fallback "sem reprojeção" e a conversão pixel→céu
    produzia coordenadas sem sentido, o que impedia a linkagem de trajetórias.

    A ordem de preferência é ``ccd.wcs`` → ``WCS(ccd.header)`` → ``None``.

    Returns
    -------
    astropy.wcs.WCS or None
        WCS com eixos celestes, ou ``None`` se o frame não tiver astrometria.
    """
    candidate = getattr(ccd, "wcs", None)
    if candidate is not None and getattr(candidate, "has_celestial", False):
        return candidate

    return wcs_from_header(getattr(ccd, "header", None))
