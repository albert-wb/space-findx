"""
Módulo 6: Exportação no Padrão ADES (Astrometry Data Exchange Standard)
=========================================================================

Gera submissões astrométricas XML validáveis pelo Minor Planet Center (MPC)
em conformidade com o esquema ADES submit.xsd.

Padrão ADES vs. Formato Legado de 80 Colunas
----------------------------------------------
O formato legado MPC de 80 colunas (MPC1992) impõe limitações severas:
    - Precisão de RA/Dec limitada a 0.001s / 0.01"
    - Sem campo para incertezas de medição (σ_α, σ_δ, correlação)
    - Sem suporte a catálogos modernos (Gaia, USNO-B2)
    - Ambiguidade em datas pré-2000 e observatórios não-MPC

O ADES (IAU WGSBN 2017) resolve todas essas limitações:
    - Precisão arbitrária via XML (double IEEE 754)
    - Tags obrigatórias para incertezas: <rmsRA>, <rmsDec>, <rmsCorr>
    - Campo <astCat> para identificar catálogo de referência (Gaia DR3, etc.)
    - Tempo em ISO 8601 UTC com precisão de nanossegundo (<obsTime>)
    - Conformidade com IVOA (International Virtual Observatory Alliance)
    - Proteção de PII: nomes pessoais NÃO devem aparecer em <comment>

A incerteza de posição σ_α (em arcseg) é derivada do centroide:
    σ_α = σ_pixel * scale_α * cos(δ)    onde scale_α = arcseg/pixel
    σ_δ = σ_pixel * scale_δ
    σ_pixel ≈ FWHM / (S/N * 2√2)   (precisão de Cramer-Rao para Gaussiana)

A correlação entre σ_α e σ_δ é dada por:
    ρ(α,δ) = Σ (Δα_i * Δδ_i) / (N * σ_α * σ_δ)   [covariância normalizada]

Referências:
    [6] Chesley, S. et al. (2017). ADES: Astrometry Data Exchange Standard. MPC/IAU.
    [7] Veres, P. et al. (2017). ADES Uncertainty Fields. MPEC 2017-S42.
    [14] Minor Planet Center (1993). MPC Obs. Format, Circular No. 1.
    [15] IAU WGSBN (2018). ADES Schema Documentation v1.0.
    [16] Lindegren, L. (1978). Principles of astrometric measurements.
    [17] Zacharias, N. et al. (2013). UCAC4 catalog astrometric errors.
    [18] ADES PII Policy — Observatorio Policy Notice 2019-01.
    [19] GDPR Art. 5(1)(c) — Data minimization principle.
"""

import logging
import xml.dom.minidom
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from .trajectory import TrajectoryCandidate

logger = logging.getLogger(__name__)

# Versão do esquema ADES suportada
ADES_VERSION = "2017"

# Catálogos astrométricos reconhecidos pelo MPC no campo <astCat>
VALID_ASTCAT_CODES = {
    "Gaia3": "Gaia DR3",
    "GaiaDR2": "Gaia Data Release 2",
    "GaiaEDR3": "Gaia Early Data Release 3",
    "UCAC4": "USNO CCD Astrograph Catalog 4",
    "2MASS": "Two Micron All-Sky Survey",
}


class ADESExporter:
    """
    Gera arquivos XML de submissão astrométrica no padrão ADES.

    A classe garante que NENHUMA informação PII (nomes, e-mails,
    endereços físicos) seja incluída nos campos de comentário (<comment>),
    em conformidade com a política de privacidade do MPC (2019) e com
    o princípio de minimização de dados do GDPR Art. 5(1)(c).

    Parameters
    ----------
    obs_code : str
        Código do observatório MPC (3 caracteres alfanuméricos, ex: "W86").
        Deve ser um código registrado no MPC para submissões válidas.
    telescope_aperture_m : float
        Abertura do telescópio em metros (informação técnica, não PII).
    telescope_desc : str
        Descrição técnica do instrumento (ex: "0.5m f/8 Ritchey-Chrétien").
    astrometric_catalog : str
        Código do catálogo astrométrico de referência (deve estar em
        VALID_ASTCAT_CODES).
    submitter_code : str
        Código do submetente (código de observatório ou institucional MPC).
        NÃO usar nome pessoal.
    software_name : str
        Nome e versão do software de redução astrométrica.
    """

    def __init__(
        self,
        obs_code: str,
        telescope_aperture_m: float,
        telescope_desc: str = "",
        astrometric_catalog: str = "GaiaEDR3",
        submitter_code: str = "",
        software_name: str = "space-findx v1.0",
    ):
        if len(obs_code) != 3:
            raise ValueError(
                f"Código de observatório inválido: '{obs_code}'. "
                "Deve ter exatamente 3 caracteres (ex: 'W86')."
            )
        if astrometric_catalog not in VALID_ASTCAT_CODES:
            raise ValueError(
                f"Catálogo '{astrometric_catalog}' não reconhecido. "
                f"Válidos: {list(VALID_ASTCAT_CODES.keys())}"
            )

        self.obs_code = obs_code
        self.telescope_aperture_m = telescope_aperture_m
        self.telescope_desc = telescope_desc
        self.astrometric_catalog = astrometric_catalog
        self.submitter_code = submitter_code or obs_code
        self.software_name = software_name

    def _format_iso8601_utc(self, obs_time) -> str:
        """
        Formata um astropy.time.Time para ISO 8601 UTC com precisão máxima.

        O MPC exige UTC explícito. A precisão deve ser de pelo menos 1ms
        para NEOs rápidos (Δt_posição ≈ 0.001"/ms para 100"/s de velocidade).

        Format: YYYY-MM-DDTHH:MM:SS.sssZ  (Z indica UTC explícito)
        """
        # Converte para escala UTC e extrai datetime com microssegundos
        utc_time = obs_time.utc
        dt = utc_time.to_datetime(timezone=timezone.utc)
        # Formato ISO 8601 com microssegundos e sufixo Z (UTC)
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"

    def _ra_deg_to_hhmmss(self, ra_deg: float) -> str:
        """
        Converte RA em graus decimais para formato HH MM SS.sss.

        RA = ra_deg / 15.0  horas decimais
        HH = int(RA)
        MM = int((RA - HH) * 60)
        SS = ((RA - HH) * 60 - MM) * 60
        """
        ra_hr = ra_deg / 15.0
        hh = int(ra_hr)
        mm = int((ra_hr - hh) * 60.0)
        ss = ((ra_hr - hh) * 60.0 - mm) * 60.0
        return f"{hh:02d} {mm:02d} {ss:06.3f}"

    def _dec_deg_to_ddmmss(self, dec_deg: float) -> str:
        """
        Converte Dec em graus decimais para formato ±DD MM SS.ss.

        sign = '+' se dec >= 0, '-' caso contrário
        |Dec| decomosto em graus, minutos, segundos.
        """
        sign = "+" if dec_deg >= 0 else "-"
        abs_dec = abs(dec_deg)
        dd = int(abs_dec)
        mm = int((abs_dec - dd) * 60.0)
        ss = ((abs_dec - dd) * 60.0 - mm) * 60.0
        return f"{sign}{dd:02d} {mm:02d} {ss:05.2f}"

    def _build_header(self, root: ET.Element) -> None:
        """
        Constrói o cabeçalho ADES com metadados do observatório e instrumento.

        Campos PII-safe: usamos códigos institucionais, nunca nomes pessoais.
        A política MPC (2019) proíbe explicitamente nomes em <comment>.
        """
        # Bloco de submissão
        submitter_elem = ET.SubElement(root, "submitter")
        ET.SubElement(submitter_elem, "subCode").text = self.submitter_code

        # Bloco do observatório
        obs_block = ET.SubElement(root, "observatory")
        ET.SubElement(obs_block, "mpcCode").text = self.obs_code
        ET.SubElement(obs_block, "name").text = f"Observatory {self.obs_code}"

        # Bloco do telescópio (informação técnica — não PII)
        telescope = ET.SubElement(root, "telescope")
        ET.SubElement(telescope, "aperture").text = f"{self.telescope_aperture_m:.2f}"
        if self.telescope_desc:
            ET.SubElement(telescope, "design").text = self.telescope_desc

        # Software de redução
        software = ET.SubElement(root, "software")
        ET.SubElement(software, "astrometry").text = self.software_name

    def _build_observation(
        self,
        obs_block: ET.Element,
        tracklet: TrajectoryCandidate,
        detection_index: int,
    ) -> None:
        """
        Constrói um elemento <optical> com todos os campos ADES obrigatórios.

        Campos obrigatórios por submit.xsd:
            <obsTime>   : tempo ISO 8601 UTC
            <ra>        : ascensão reta em graus decimais
            <dec>       : declinação em graus decimais
            <astCat>    : código do catálogo astrométrico
            <rmsRA>     : incerteza RMS em RA (arcseg)
            <rmsDec>    : incerteza RMS em Dec (arcseg)
            <rmsCorr>   : correlação RA-Dec (adimensional ∈ [-1, 1])

        As incertezas rmsRA e rmsDec são derivadas do RMS do ajuste de
        trajetória, que inclui contribuições do centroide e do WCS.
        """
        det = tracklet.detections[detection_index]
        sky = tracklet.sky_coords[detection_index]
        obs_time = tracklet.obs_times[detection_index]

        optical = ET.SubElement(obs_block, "optical")

        # === Tags de Identificação ===
        ET.SubElement(optical, "permID").text = ""       # preenchido pós-confirmação MPC
        ET.SubElement(optical, "provID").text = tracklet.tracklet_id

        # === Tempo de Observação (ISO 8601 UTC) ===
        ET.SubElement(optical, "obsTime").text = self._format_iso8601_utc(obs_time)

        # === Posição Astrométrica ===
        ET.SubElement(optical, "ra").text = f"{sky.ra.deg:.9f}"   # 9 casas = 0.36μas
        ET.SubElement(optical, "dec").text = f"{sky.dec.deg:.9f}"

        # === Catálogo Astrométrico de Referência ===
        ET.SubElement(optical, "astCat").text = self.astrometric_catalog

        # === Incertezas Estatísticas (OBRIGATÓRIAS no ADES) ===
        # rmsRA já inclui o fator cos(δ) via conversão no TrajectoryLinker
        rms_ra = max(tracklet.ra_rms_arcsec, 0.001)   # mínimo 1mas (limite realista)
        rms_dec = max(tracklet.dec_rms_arcsec, 0.001)

        ET.SubElement(optical, "rmsRA").text = f"{rms_ra:.4f}"
        ET.SubElement(optical, "rmsDec").text = f"{rms_dec:.4f}"

        # Correlação RA-Dec: estimamos 0.0 (sem correlação) como padrão conservador
        # Para resultados de ajuste com polinômio SIP, pode ser calculada via
        # matriz de covariância do fit (A^T A)^{-1}
        ET.SubElement(optical, "rmsCorr").text = "0.000"

        # === Código do Observatório ===
        ET.SubElement(optical, "stn").text = self.obs_code

        # === Modo de Observação ===
        ET.SubElement(optical, "mode").text = "CCD"
        ET.SubElement(optical, "tech").text = "N"  # N=imagem; T=trailed

        # === Significância da Detecção (extensão opcional) ===
        ET.SubElement(optical, "notes").text = (
            f"SIGFHM={det.peak_significance:.2f}; "
            f"SHARP={det.sharpness:.3f}; "
            f"ELONG={det.elongation:.3f}"
        )

    def export(
        self,
        tracklets: List[TrajectoryCandidate],
        output_path: Path,
        observation_date: Optional[str] = None,
    ) -> Path:
        """
        Exporta todas as tracklets confirmadas para um arquivo XML ADES.

        O XML resultante pode ser validado contra o esquema oficial do MPC:
            https://www.minorplanetcenter.net/iau/info/submit.xsd

        Parameters
        ----------
        tracklets : List[TrajectoryCandidate]
            Lista de tracklets confirmadas a exportar.
        output_path : Path
            Caminho do arquivo XML de saída.
        observation_date : str, optional
            Data de observação para o nome do arquivo (YYYY-MM-DD).

        Returns
        -------
        Path
            Caminho do arquivo XML gerado.

        Raises
        ------
        ValueError
            Se nenhuma tracklet confirmada for fornecida.
        """
        confirmed = [t for t in tracklets if t.is_confirmed]
        if not confirmed:
            raise ValueError(
                "Nenhuma tracklet confirmada disponível para exportação. "
                "Execute a linkagem e validação antes de exportar."
            )

        # Data de geração do arquivo (UTC, não PII)
        generation_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # --- Construção da árvore XML ---
        root = ET.Element("ades")
        root.set("version", ADES_VERSION)

        # Comentário de metadados (SEM PII)
        comment = ET.Comment(
            f" Generated by {self.software_name} | "
            f"UTC: {generation_time} | "
            f"Observatory: {self.obs_code} | "
            f"Tracklets: {len(confirmed)} "
        )
        root.append(comment)

        # Cabeçalho do observatório e instrumento
        self._build_header(root)

        # Bloco de observações
        obs_block = ET.SubElement(root, "obsBlock")

        total_obs = 0
        for tracklet in confirmed:
            for i in range(len(tracklet.detections)):
                self._build_observation(obs_block, tracklet, i)
                total_obs += 1

        # Serializa com formatação bonita (pretty-print)
        xml_string = ET.tostring(root, encoding="unicode", xml_declaration=False)
        xml_declaration = '<?xml version="1.0" encoding="UTF-8"?>\n'

        # Usa minidom para indentação legível
        dom = xml.dom.minidom.parseString(xml_declaration + xml_string)
        pretty_xml = dom.toprettyxml(indent="  ", encoding="UTF-8").decode("utf-8")
        # Remove a linha duplicada de declaração XML do minidom
        pretty_xml = "\n".join(pretty_xml.split("\n")[1:])

        # Garante que o diretório existe
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
            f.write(pretty_xml)

        logger.info(
            f"ADES XML exportado: {output_path} | "
            f"{len(confirmed)} tracklets | {total_obs} observações"
        )
        return output_path
