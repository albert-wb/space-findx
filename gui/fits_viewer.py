"""
FITS Viewer — Visualizador Astrométrico com Projeção WCS
==========================================================

Módulo responsável pela renderização de imagens FITS com projeção
astrométrica (RA/Dec), normalização de contraste robusta e marcação
interativa de candidatos a asteroides.

Política de Memória
--------------------
Imagens CCD de 4096×4096 em float64 ocupam ~128 MB cada. Para evitar
vazamento de memória ao trocar entre frames ou candidatos:

1. **Limpeza explícita de axes**: Cada chamada a ``display_fits``
   executa ``ax.cla()`` e ``fig.clf()`` antes de redesenhar, liberando
   referências a arrays anteriores.
2. **Garbage collector forçado**: Após limpar a figura, ``gc.collect()``
   é chamado para forçar a liberação de arrays NumPy orphans.
3. **Referência única**: Apenas UM array FITS é mantido em memória
   por vez (``self._current_data``). O array anterior é substituído
   por referência, permitindo coleta pelo GC.

Normalização de Contraste — Justificativa
-------------------------------------------
A escolha do ``ZScaleInterval`` (Modo 1) ou ``μ ± kσ`` (Modo 2) é
crítica porque:

- **Blooming de estrelas saturadas** gera valores de pixel 10³–10⁴×
  acima do fundo. Sem normalização, estes pixels dominam o mapeamento
  linear [0, 2^16] → [0, 1], comprimindo todo o range dinâmico do
  asteroide tênue para <1% da escala de cinza.

- **Raios cósmicos** geram spikes pontuais de ~10⁵ ADU que saturam
  completamente qualquer escala linear simples.

- O **ZScale** (Fitzpatrick, 1999) ajusta uma reta iterativa no
  histograma ordenado de pixels e define vmin/vmax pela intersecção
  da reta com os extremos, rejeitando outliers naturalmente.

- O **μ ± kσ** (sigma-clipping) é mais rápido computacionalmente
  e fornece resultado semelhante para CCDs com distribuição unimodal
  de fundo (a maioria dos campos astronômicos).

Usamos ZScale como padrão (mais robusto), com fallback para μ ± kσ
se o ZScale falhar (e.g., imagens saturadas uniformemente).

Referências:
    [1] Fitzpatrick, M.J. (1999). IRAF ZScale Algorithm. NOAO.
    [2] Lupton, R. et al. (2004). Preparing Red-Green-Blue images.
"""

import gc
import logging
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import matplotlib
matplotlib.use("Agg")  # Backend não-interativo para embedding em Tkinter
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.patches import Rectangle
from matplotlib.figure import Figure

from astropy.io import fits
from astropy.wcs import WCS
from astropy.visualization import ZScaleInterval
from astropy.stats import sigma_clipped_stats

logger = logging.getLogger(__name__)


class FITSViewerWidget:
    """
    Widget de visualização FITS com projeção WCS incorporável em Tkinter.

    Renderiza imagens FITS com eixos em Ascensão Reta (RA) e Declinação
    (Dec) usando a projeção do cabeçalho WCS, aplica normalização de
    contraste robusta e permite marcação interativa de candidatos.

    Parameters
    ----------
    parent : tk.Widget
        Widget pai do Tkinter/CustomTkinter onde a figura será embarcada.
    figsize : tuple of float
        Tamanho da figura matplotlib em polegadas (largura, altura).
    dpi : int
        Resolução da figura em pontos por polegada.

    Attributes
    ----------
    fig : matplotlib.figure.Figure
        Figura matplotlib principal.
    canvas : FigureCanvasTkAgg
        Canvas Tkinter que renderiza a figura.
    _current_data : np.ndarray or None
        Array FITS atualmente exibido (referência única para GC).
    _current_wcs : WCS or None
        WCS do frame atualmente exibido.
    _bounding_boxes : list
        Lista de patches Rectangle ativos na figura.
    """

    # Cores da paleta científica (coerentes com o tema do projeto)
    _COLORS = {
        "bg":           "#0a0e17",
        "grid":         "#1e2d4a",
        "text":         "#c8dce8",
        "accent":       "#00e5ff",
        "bbox_default": "#ffeb3b",  # Amarelo para bounding box
        "bbox_active":  "#00e676",  # Verde para candidato selecionado
        "crosshair":   "#ff1744",   # Vermelho para retículo
    }

    def __init__(
        self,
        parent,
        figsize: Tuple[float, float] = (8, 6),
        dpi: int = 100,
    ):
        self._parent = parent
        self._current_data: Optional[np.ndarray] = None
        self._current_wcs: Optional[WCS] = None
        self._current_header = None
        self._bounding_boxes: list = []
        self._fits_path: Optional[Path] = None

        # ── Cria figura matplotlib com fundo escuro ──
        self.fig = Figure(figsize=figsize, dpi=dpi, facecolor=self._COLORS["bg"])
        self._ax: Optional[plt.Axes] = None

        # ── Embarca no canvas Tkinter ──
        self.canvas = FigureCanvasTkAgg(self.fig, master=parent)
        self.canvas_widget = self.canvas.get_tk_widget()
        self.canvas_widget.configure(bg=self._COLORS["bg"])

        # Placeholder inicial
        self._show_placeholder()

    def _show_placeholder(self):
        """Exibe mensagem de placeholder quando nenhuma imagem está carregada."""
        self.fig.clf()
        ax = self.fig.add_subplot(111)
        ax.set_facecolor(self._COLORS["bg"])
        ax.text(
            0.5, 0.5,
            "⌖  Nenhuma imagem FITS carregada\n\n"
            "Execute o pipeline ou selecione\n"
            "um candidato na aba Candidates",
            ha="center", va="center",
            fontsize=12, color=self._COLORS["accent"],
            fontfamily="monospace", alpha=0.7,
            transform=ax.transAxes,
        )
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)
        self.canvas.draw_idle()

    def _release_memory(self):
        """
        Libera memória de arrays FITS anteriores.

        Executa limpeza explícita da figura matplotlib e força
        garbage collection para liberar arrays NumPy orphans.
        O footprint de memória é mantido em O(1 frame) em vez
        de O(N frames visualizados).
        """
        self._bounding_boxes.clear()
        self._current_data = None

        if self._ax is not None:
            self._ax.cla()
            self._ax = None

        self.fig.clf()
        gc.collect()

        logger.debug("Memória do FITS Viewer liberada (gc.collect executado)")

    def load_fits(self, fits_path: Path) -> bool:
        """
        Carrega uma imagem FITS do disco e extrai WCS.

        O carregamento usa ``memmap=True`` para leitura preguiçosa
        do array, mas força cópia para float64 para evitar problemas
        com memmap durante a normalização.

        Parameters
        ----------
        fits_path : Path
            Caminho para o arquivo FITS.

        Returns
        -------
        bool
            True se o carregamento foi bem-sucedido.
        """
        self._release_memory()

        try:
            with fits.open(str(fits_path), memmap=True) as hdu_list:
                # Encontra a extensão com dados de imagem
                data_hdu = None
                for hdu in hdu_list:
                    if hdu.data is not None and hdu.data.ndim == 2:
                        data_hdu = hdu
                        break

                if data_hdu is None:
                    logger.error(f"Nenhuma extensão 2D encontrada em: {fits_path}")
                    return False

                # Cópia explícita para float64 — libera o memmap
                self._current_data = data_hdu.data.astype(np.float64)
                self._current_header = data_hdu.header.copy()

                try:
                    self._current_wcs = WCS(self._current_header)
                except Exception as wcs_err:
                    logger.warning(
                        f"WCS inválido em {fits_path.name}: {wcs_err}. "
                        "Usando projeção de pixel."
                    )
                    self._current_wcs = None

            self._fits_path = fits_path
            logger.info(
                f"FITS carregado: {fits_path.name} "
                f"({self._current_data.shape[1]}×{self._current_data.shape[0]} px, "
                f"{self._current_data.nbytes / 1e6:.1f} MB)"
            )
            return True

        except Exception as e:
            logger.error(f"Falha ao carregar FITS '{fits_path}': {e}")
            self._current_data = None
            self._current_wcs = None
            return False

    def _normalize_contrast(
        self,
        data: np.ndarray,
        method: str = "zscale",
    ) -> Tuple[float, float]:
        """
        Calcula limites de contraste (vmin, vmax) para a imagem.

        Dois métodos são suportados:

        Método 1 — ZScale (padrão)
        ~~~~~~~~~~~~~~~~~~~~~~~~~~
        O algoritmo ZScale (Fitzpatrick 1999) ajusta uma reta ao
        histograma cumulativo dos pixels e calcula vmin/vmax pela
        intersecção da reta nos extremos do intervalo. Rejeita
        naturalmente blooming, raios cósmicos e pixels defeituosos.

        Método 2 — Sigma-clipping (fallback)
        ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        Calcula μ (média) e σ (desvio padrão) do fundo usando
        sigma-clipping iterativo (3σ, 5 iterações). Define:
            vmin = μ - 2σ  (preserva estrutura do fundo)
            vmax = μ + 5σ  (captura fontes tênues sem saturar)

        A assimetria [-2σ, +5σ] é intencional: em campos astronômicos,
        as fontes estão sempre ACIMA do fundo, então o range positivo
        deve ser maior para capturar estrelas e asteroides tênues.

        Parameters
        ----------
        data : np.ndarray
            Array 2D de pixels (float64).
        method : str
            "zscale" (padrão) ou "sigma".

        Returns
        -------
        tuple of float
            (vmin, vmax) para normalização.
        """
        # Filtra NaN e Inf
        valid = data[np.isfinite(data)]
        if len(valid) == 0:
            return 0.0, 1.0

        if method == "zscale":
            try:
                zscale = ZScaleInterval(
                    nsamples=1000,
                    contrast=0.25,
                    max_reject=0.5,
                    min_npixels=5,
                    krej=2.5,
                    max_iterations=5,
                )
                vmin, vmax = zscale.get_limits(valid)
                if vmin is not None and vmax is not None and vmin < vmax:
                    return float(vmin), float(vmax)
            except Exception as e:
                logger.debug(f"ZScale falhou, usando fallback sigma: {e}")

        # Fallback: sigma-clipping
        mean, median, std = sigma_clipped_stats(valid, sigma=3.0, maxiters=5)
        vmin = float(median - 2.0 * std)
        vmax = float(median + 5.0 * std)

        if vmin >= vmax:
            vmin = float(np.nanmin(valid))
            vmax = float(np.nanmax(valid))

        return vmin, vmax

    def display(
        self,
        contrast_method: str = "zscale",
        cmap: str = "gray",
    ):
        """
        Renderiza a imagem FITS carregada com projeção WCS.

        O eixo é instanciado com ``projection=wcs`` quando o WCS
        é válido, exibindo coordenadas celestes (RA, Dec) nos ticks
        em vez de índices de pixel.

        Parameters
        ----------
        contrast_method : str
            Método de normalização: "zscale" ou "sigma".
        cmap : str
            Colormap do matplotlib (padrão: "gray" para astronomia).
        """
        if self._current_data is None:
            self._show_placeholder()
            return

        # Limpa figura anterior (preservando memória)
        self.fig.clf()
        self._bounding_boxes.clear()

        data = self._current_data

        # ── Instancia axes com projeção WCS ──
        if self._current_wcs is not None and self._current_wcs.has_celestial:
            self._ax = self.fig.add_subplot(
                111,
                projection=self._current_wcs,
            )
            self._ax.set_xlabel(
                "Ascensão Reta (RA)",
                color=self._COLORS["text"], fontsize=9,
            )
            self._ax.set_ylabel(
                "Declinação (Dec)",
                color=self._COLORS["text"], fontsize=9,
            )
        else:
            self._ax = self.fig.add_subplot(111)
            self._ax.set_xlabel(
                "X (pixels)", color=self._COLORS["text"], fontsize=9,
            )
            self._ax.set_ylabel(
                "Y (pixels)", color=self._COLORS["text"], fontsize=9,
            )

        ax = self._ax

        # ── Normalização de contraste ──
        vmin, vmax = self._normalize_contrast(data, method=contrast_method)

        # ── Renderiza imagem com origin='lower' (padrão FITS) ──
        ax.imshow(
            data,
            origin="lower",
            cmap=cmap,
            vmin=vmin,
            vmax=vmax,
            interpolation="nearest",
            aspect="equal",
        )

        # ── Estilização dos eixos ──
        ax.set_facecolor(self._COLORS["bg"])
        ax.tick_params(
            colors=self._COLORS["text"],
            labelsize=7,
            direction="in",
        )
        ax.grid(
            True, color=self._COLORS["grid"],
            linewidth=0.3, alpha=0.4,
        )
        for spine in ax.spines.values():
            spine.set_color(self._COLORS["grid"])

        # ── Título com info do arquivo ──
        title = ""
        if self._fits_path:
            title = self._fits_path.name
        if self._current_header:
            filter_name = self._current_header.get("FILTER", "")
            exptime = self._current_header.get("EXPTIME", "")
            if filter_name:
                title += f"  |  {filter_name}"
            if exptime:
                title += f"  |  {exptime}s"
        ax.set_title(
            title, color=self._COLORS["accent"],
            fontsize=10, fontfamily="monospace", pad=8,
        )

        self.fig.tight_layout(pad=1.5)
        self.canvas.draw_idle()

        logger.debug(f"FITS renderizado: vmin={vmin:.1f}, vmax={vmax:.1f}")

    def highlight_candidate(
        self,
        x_centroid: float,
        y_centroid: float,
        fwhm: float = 5.0,
        box_scale: float = 4.0,
        color: Optional[str] = None,
        label: Optional[str] = None,
    ):
        """
        Desenha bounding box ao redor de um candidato a asteroide.

        A caixa é parametrizada pelo centroide exato da detecção e
        pelo FWHM do objeto, escalado por ``box_scale``:
            tamanho_caixa = FWHM × box_scale

        Edge Cases (Verificação de Borda)
        -----------------------------------
        Se a caixa se estender parcialmente fora dos limites do CCD,
        ela é clipada (recortada) para caber dentro da imagem. Isso
        previne:
        - Exceções matplotlib ao desenhar patches fora do domínio
        - Confusão visual com coordenadas negativas
        - Crash ao tentar indexar pixels fora do array

        A verificação garante:
            x0 = max(0, centroide_x - half_size)
            y0 = max(0, centroide_y - half_size)
            x1 = min(NAXIS1, centroide_x + half_size)
            y1 = min(NAXIS2, centroide_y + half_size)

        Parameters
        ----------
        x_centroid : float
            Coordenada X do centroide em pixels (0-indexado).
        y_centroid : float
            Coordenada Y do centroide em pixels.
        fwhm : float
            FWHM do objeto em pixels (Full Width at Half Maximum).
        box_scale : float
            Fator de escala para o tamanho da caixa relativo ao FWHM.
            Padrão 4.0 significa caixa de 4×FWHM de lado.
        color : str, optional
            Cor da borda da caixa (padrão: amarelo).
        label : str, optional
            Rótulo de texto sobre a caixa (ex: "TRK_0001").
        """
        if self._ax is None or self._current_data is None:
            logger.warning("Nenhuma imagem exibida — impossível marcar candidato.")
            return

        color = color or self._COLORS["bbox_active"]
        ny, nx = self._current_data.shape

        # ── Calcula tamanho da caixa ──
        half_size = (fwhm * box_scale) / 2.0

        # ── Verificação de borda (Edge Case) ──
        # Clipa a caixa para caber dentro dos limites do CCD
        x0 = max(0.0, x_centroid - half_size)
        y0 = max(0.0, y_centroid - half_size)
        x1 = min(float(nx), x_centroid + half_size)
        y1 = min(float(ny), y_centroid + half_size)

        box_width = x1 - x0
        box_height = y1 - y0

        # ── Segurança: ignora caixas degeneradas ──
        if box_width <= 0 or box_height <= 0:
            logger.warning(
                f"Caixa degenerada para candidato em ({x_centroid:.1f}, "
                f"{y_centroid:.1f}): w={box_width:.1f}, h={box_height:.1f}"
            )
            return

        # ── Desenha Rectangle (quadrado vazado) ──
        rect = Rectangle(
            (x0, y0),
            box_width,
            box_height,
            linewidth=1.8,
            edgecolor=color,
            facecolor="none",
            linestyle="-",
            alpha=0.9,
        )
        self._ax.add_patch(rect)
        self._bounding_boxes.append(rect)

        # ── Crosshair central ──
        self._ax.plot(
            x_centroid, y_centroid, "+",
            color=self._COLORS["crosshair"],
            markersize=12, markeredgewidth=1.2,
            alpha=0.85,
        )

        # ── Rótulo de texto ──
        if label:
            label_y = y1 + 3 if y1 + 15 < ny else y0 - 8
            self._ax.text(
                x_centroid, label_y,
                label,
                color=color,
                fontsize=7,
                fontfamily="monospace",
                fontweight="bold",
                ha="center", va="bottom",
                bbox=dict(
                    boxstyle="round,pad=0.15",
                    facecolor=self._COLORS["bg"],
                    edgecolor=color,
                    alpha=0.8,
                ),
            )

        self.canvas.draw_idle()
        logger.debug(
            f"Candidato marcado: ({x_centroid:.1f}, {y_centroid:.1f}) "
            f"FWHM={fwhm:.1f} box={box_width:.0f}×{box_height:.0f}px"
        )

    def clear_highlights(self):
        """Remove todas as bounding boxes de candidatos da imagem."""
        for patch in self._bounding_boxes:
            try:
                patch.remove()
            except ValueError:
                pass
        self._bounding_boxes.clear()
        self.canvas.draw_idle()

    def zoom_to_candidate(
        self,
        x_centroid: float,
        y_centroid: float,
        zoom_radius: float = 100.0,
    ):
        """
        Centraliza a visualização no candidato com um zoom local.

        Parameters
        ----------
        x_centroid : float
            Coordenada X do centroide em pixels.
        y_centroid : float
            Coordenada Y do centroide em pixels.
        zoom_radius : float
            Raio em pixels ao redor do centroide para o zoom.
        """
        if self._ax is None or self._current_data is None:
            return

        ny, nx = self._current_data.shape

        x0 = max(0, x_centroid - zoom_radius)
        x1 = min(nx, x_centroid + zoom_radius)
        y0 = max(0, y_centroid - zoom_radius)
        y1 = min(ny, y_centroid + zoom_radius)

        self._ax.set_xlim(x0, x1)
        self._ax.set_ylim(y0, y1)
        self.canvas.draw_idle()

    def reset_zoom(self):
        """Restaura visualização para a imagem completa."""
        if self._ax is None or self._current_data is None:
            return

        ny, nx = self._current_data.shape
        self._ax.set_xlim(-0.5, nx - 0.5)
        self._ax.set_ylim(-0.5, ny - 0.5)
        self.canvas.draw_idle()

    def get_widget(self):
        """Retorna o widget Tkinter para packing/grid."""
        return self.canvas_widget

    def destroy(self):
        """Libera todos os recursos do viewer."""
        self._release_memory()
        plt.close(self.fig)
        self.canvas_widget.destroy()
        gc.collect()
