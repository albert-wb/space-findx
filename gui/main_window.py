"""
Space-FindX — Interface Principal com 4 Abas
=============================================
GUI CustomTkinter: System Logs, Candidates, ADES XML, FITS Viewer.
"""

import os
import sys
import gc
import yaml
import threading
import datetime
import webbrowser
import tkinter as tk
from pathlib import Path
from typing import Optional, List, Dict

import numpy as np
import customtkinter as ctk

from gui.fits_viewer import FITSViewerWidget

# ── TEMA ──
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

C = {
    "bg": "#0a0e17", "surface": "#111827", "surface_alt": "#0d1321",
    "border": "#1e2d4a", "accent": "#00e5ff", "accent_dim": "#007a8a",
    "ok": "#00e676", "warn": "#ffab00", "error": "#ff1744",
    "text": "#c8dce8", "text_dim": "#5a7a94",
    "term_bg": "#050a12", "term_fg": "#00e5ff",
}
FONT_MONO = ("Consolas", 11)
FONT_TITLE = ("Segoe UI", 18, "bold")
FONT_LABEL = ("Segoe UI", 11)
FONT_BTN = ("Segoe UI", 12, "bold")
FONT_TAB = ("Segoe UI", 11, "bold")


class SpaceFindXGUI(ctk.CTk):
    """Interface principal do pipeline com 4 abas."""

    def __init__(self):
        super().__init__()
        self.title("◈ SPACE-FINDX — Pipeline Controller")
        self.geometry("1100x720")
        self.minsize(900, 600)
        self.configure(fg_color=C["bg"])

        self.is_running = False
        self.pipeline_thread = None
        self.candidates_data: List[Dict] = []
        self.current_fits_path: Optional[Path] = None
        self.fits_viewer: Optional[FITSViewerWidget] = None

        self._build_header()
        self._build_toolbar()
        self._build_tabs()
        self._build_footer()
        self._log("SYS", "Controlador inicializado. Aguardando comando.")

    # ── HEADER ──
    def _build_header(self):
        hdr = ctk.CTkFrame(self, fg_color=C["surface"], corner_radius=0, height=54)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        ctk.CTkLabel(hdr, text="◈  SPACE-FINDX", font=FONT_TITLE,
                     text_color=C["accent"]).pack(side="left", padx=20)
        ctk.CTkLabel(hdr, text="Astrometric NEO Detection Pipeline",
                     font=("Segoe UI", 11), text_color=C["text_dim"]).pack(side="left")
        self.status_lbl = ctk.CTkLabel(hdr, text="● IDLE", font=("Segoe UI", 12, "bold"),
                                       text_color=C["text_dim"])
        self.status_lbl.pack(side="right", padx=20)
        ctk.CTkFrame(self, fg_color=C["accent_dim"], height=1).pack(fill="x")

    # ── TOOLBAR ──
    def _build_toolbar(self):
        tb = ctk.CTkFrame(self, fg_color=C["bg"], height=50)
        tb.pack(fill="x", padx=14, pady=(10, 0))
        tb.pack_propagate(False)
        self.btn_start = ctk.CTkButton(tb, text="▶  Iniciar Pipeline", font=FONT_BTN,
            width=170, height=36, fg_color=C["ok"], hover_color="#00c853",
            text_color="#0a0e17", corner_radius=6, command=self.start_pipeline)
        self.btn_start.pack(side="left", padx=(0, 6))
        self.btn_stop = ctk.CTkButton(tb, text="⏹  Parar", font=FONT_BTN,
            width=110, height=36, fg_color=C["error"], hover_color="#d50000",
            text_color="white", corner_radius=6, state="disabled", command=self.stop_pipeline)
        self.btn_stop.pack(side="left", padx=(0, 6))
        self.btn_clear = ctk.CTkButton(tb, text="⌫  Limpar", font=FONT_LABEL,
            width=100, height=36, fg_color=C["surface"], hover_color=C["border"],
            text_color=C["text_dim"], corner_radius=6, border_width=1,
            border_color=C["border"], command=self.clear_log)
        self.btn_clear.pack(side="left", padx=(0, 6))
        ctk.CTkButton(tb, text="🌐 Interface Web", font=FONT_LABEL, width=150,
            height=36, fg_color=C["accent_dim"], hover_color=C["accent"],
            text_color="white", corner_radius=6, command=self._open_web).pack(side="right")

    # ── TABS ──
    def _build_tabs(self):
        self.tabview = ctk.CTkTabview(self, fg_color=C["surface"],
            segmented_button_fg_color=C["surface_alt"],
            segmented_button_selected_color=C["accent_dim"],
            segmented_button_selected_hover_color=C["accent"],
            segmented_button_unselected_color=C["surface_alt"],
            segmented_button_unselected_hover_color=C["border"],
            corner_radius=8, border_width=1, border_color=C["border"])
        self.tabview.pack(fill="both", expand=True, padx=14, pady=10)

        self.tab_logs = self.tabview.add("System Logs")
        self.tab_cand = self.tabview.add("Candidates")
        self.tab_ades = self.tabview.add("ADES XML")
        self.tab_fits = self.tabview.add("FITS Viewer")

        self._build_tab_logs()
        self._build_tab_candidates()
        self._build_tab_ades()
        self._build_tab_fits()

    def _build_tab_logs(self):
        hdr = ctk.CTkFrame(self.tab_logs, fg_color=C["surface_alt"], height=28)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        ctk.CTkLabel(hdr, text="  ⌖  TERMINAL DE PROCESSOS", font=("Consolas", 10, "bold"),
                     text_color=C["text_dim"]).pack(side="left", padx=8)
        self.terminal = ctk.CTkTextbox(self.tab_logs, font=FONT_MONO,
            fg_color=C["term_bg"], text_color=C["term_fg"], corner_radius=0,
            border_width=0, wrap="word", state="disabled")
        self.terminal.pack(fill="both", expand=True, padx=2, pady=(0, 2))
        for tag, color in [("INFO", C["term_fg"]), ("OK", C["ok"]), ("WARN", C["warn"]),
                           ("ERROR", C["error"]), ("SYS", C["text_dim"]), ("TIME", C["text_dim"])]:
            self.terminal._textbox.tag_config(tag, foreground=color)

    def _build_tab_candidates(self):
        ctrl = ctk.CTkFrame(self.tab_cand, fg_color=C["surface_alt"], height=36)
        ctrl.pack(fill="x", pady=(0, 4))
        ctrl.pack_propagate(False)
        ctk.CTkLabel(ctrl, text="  ⌖  OBJETOS DETECTADOS", font=("Consolas", 10, "bold"),
                     text_color=C["text_dim"]).pack(side="left", padx=8)
        self.cand_count_lbl = ctk.CTkLabel(ctrl, text="0 candidatos",
            font=("Consolas", 10), text_color=C["accent"])
        self.cand_count_lbl.pack(side="right", padx=12)

        # Tabela usando Treeview do tkinter
        tree_frame = ctk.CTkFrame(self.tab_cand, fg_color=C["term_bg"])
        tree_frame.pack(fill="both", expand=True, padx=2, pady=2)

        cols = ("ID", "Frame", "X", "Y", "SNR", "FWHM", "Elong", "RA", "Dec", "Status")
        style = tk.ttk.Style()
        style.theme_use("clam")
        style.configure("Dark.Treeview", background=C["term_bg"],
            foreground=C["term_fg"], fieldbackground=C["term_bg"],
            font=("Consolas", 10), rowheight=22)
        style.configure("Dark.Treeview.Heading", background=C["surface_alt"],
            foreground=C["accent"], font=("Consolas", 10, "bold"))
        style.map("Dark.Treeview", background=[("selected", C["accent_dim"])],
                  foreground=[("selected", "white")])

        self.tree = tk.ttk.Treeview(tree_frame, columns=cols, show="headings",
                                     style="Dark.Treeview")
        widths = {"ID": 50, "Frame": 50, "X": 70, "Y": 70, "SNR": 70,
                  "FWHM": 60, "Elong": 60, "RA": 120, "Dec": 120, "Status": 80}
        for col in cols:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=widths.get(col, 80), anchor="center")

        scroll = tk.ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_candidate_select)

    def _build_tab_ades(self):
        hdr = ctk.CTkFrame(self.tab_ades, fg_color=C["surface_alt"], height=28)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        ctk.CTkLabel(hdr, text="  ⌖  ADES XML — Relatório de Submissão MPC",
                     font=("Consolas", 10, "bold"), text_color=C["text_dim"]).pack(side="left", padx=8)
        self.ades_text = ctk.CTkTextbox(self.tab_ades, font=("Consolas", 11),
            fg_color=C["term_bg"], text_color="#80cbc4", corner_radius=0,
            border_width=0, wrap="none", state="disabled")
        self.ades_text.pack(fill="both", expand=True, padx=2, pady=(0, 2))

    def _build_tab_fits(self):
        ctrl = ctk.CTkFrame(self.tab_fits, fg_color=C["surface_alt"], height=36)
        ctrl.pack(fill="x")
        ctrl.pack_propagate(False)
        ctk.CTkLabel(ctrl, text="  ⌖  FITS VIEWER — Projeção Astrométrica WCS",
                     font=("Consolas", 10, "bold"), text_color=C["text_dim"]).pack(side="left", padx=8)
        self.btn_zoom_reset = ctk.CTkButton(ctrl, text="⟲ Reset Zoom", font=("Consolas", 9),
            width=90, height=24, fg_color=C["border"], hover_color=C["accent_dim"],
            text_color=C["text"], corner_radius=4, command=self._reset_fits_zoom)
        self.btn_zoom_reset.pack(side="right", padx=8)
        self.contrast_var = ctk.StringVar(value="zscale")
        ctk.CTkOptionMenu(ctrl, values=["zscale", "sigma"], variable=self.contrast_var,
            width=90, height=24, fg_color=C["border"], button_color=C["accent_dim"],
            font=("Consolas", 9), command=self._on_contrast_change).pack(side="right", padx=4)
        ctk.CTkLabel(ctrl, text="Contraste:", font=("Consolas", 9),
                     text_color=C["text_dim"]).pack(side="right")

        viewer_frame = ctk.CTkFrame(self.tab_fits, fg_color=C["bg"])
        viewer_frame.pack(fill="both", expand=True, padx=2, pady=2)
        self.fits_viewer = FITSViewerWidget(viewer_frame, figsize=(9, 6), dpi=90)
        self.fits_viewer.get_widget().pack(fill="both", expand=True)

    # ── FOOTER ──
    def _build_footer(self):
        ft = ctk.CTkFrame(self, fg_color=C["surface"], corner_radius=0, height=26)
        ft.pack(fill="x")
        ft.pack_propagate(False)
        self.footer_lbl = ctk.CTkLabel(ft, text="space-findx v1.0  ·  Pipeline Offline",
            font=("Consolas", 10), text_color=C["text_dim"])
        self.footer_lbl.pack(side="left", padx=12)

    # ━━━ LOGGING ━━━
    def _log(self, level: str, message: str):
        def _append():
            tag = level.upper()
            if tag not in ("INFO", "OK", "WARN", "ERROR", "SYS"):
                tag = "INFO"
            now = datetime.datetime.now().strftime("%H:%M:%S")
            pmap = {"INFO": "[INFO] ", "OK": "[ OK ] ", "WARN": "[WARN] ",
                    "ERROR": "[ERR!] ", "SYS": "[ ◈◈ ] "}
            self.terminal.configure(state="normal")
            self.terminal._textbox.insert("end", f" {now}  ", "TIME")
            self.terminal._textbox.insert("end", f"{pmap.get(tag, '[INFO] ')}{message}\n", tag)
            self.terminal.configure(state="disabled")
            self.terminal.see("end")
        self.after(0, _append)

    def clear_log(self):
        self.terminal.configure(state="normal")
        self.terminal._textbox.delete("1.0", "end")
        self.terminal.configure(state="disabled")
        self._log("SYS", "Terminal limpo pelo operador.")

    # ━━━ AÇÕES ━━━
    def _open_web(self):
        p = os.path.abspath("index.html")
        webbrowser.open(f"file://{p}")
        self._log("INFO", f"Interface web aberta: {p}")

    def start_pipeline(self):
        if self.is_running:
            return
        self.is_running = True
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.status_lbl.configure(text="● COMPUTING", text_color=C["warn"])
        self.footer_lbl.configure(text="space-findx v1.0  ·  Pipeline em Execução...")
        self._log("SYS", "━━━ PIPELINE INICIADO ━━━")
        self.pipeline_thread = threading.Thread(target=self._run_pipeline, daemon=True)
        self.pipeline_thread.start()

    def stop_pipeline(self):
        if not self.is_running:
            return
        self._log("WARN", "Interrupção solicitada.")
        self.destroy()
        sys.exit(0)

    def _pipeline_finished(self, success: bool):
        self.is_running = False
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        if success:
            self.status_lbl.configure(text="● READY", text_color=C["ok"])
            self.footer_lbl.configure(text="space-findx v1.0  ·  Pipeline Concluído ✓")
        else:
            self.status_lbl.configure(text="● ERROR", text_color=C["error"])
            self.footer_lbl.configure(text="space-findx v1.0  ·  Pipeline Falhou")

    def _run_pipeline(self):
        """Executa o pipeline em thread separada, populando as abas."""
        success = False
        try:
            cfg_path = "config/pipeline_config.yaml"
            if not os.path.exists(cfg_path):
                self._log("ERROR", f"Config não encontrada: {cfg_path}")
                return

            with open(cfg_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)

            from pipeline.pipeline import SpaceFindXPipeline
            pipeline = SpaceFindXPipeline(config)

            science_dir = Path("dados/ciencia/")
            reference_fits = Path("dados/referencia.fits")
            output_dir = Path("saida/")

            if not science_dir.exists():
                self._log("WARN", f"'{science_dir}' não encontrado. Criando...")
                science_dir.mkdir(parents=True, exist_ok=True)
            if not reference_fits.exists():
                self._log("WARN", f"Referência '{reference_fits}' não encontrada.")
                reference_fits.parent.mkdir(parents=True, exist_ok=True)

            def log_cb(level, msg):
                self._log(level, msg)

            ades_path = pipeline.run(
                science_dir=science_dir, reference_fits=reference_fits,
                output_dir=output_dir, log_callback=log_cb,
            )

            if ades_path:
                self._log("OK", f"ADES gerada: {ades_path}")
                self.after(0, lambda: self._load_ades_xml(ades_path))
                success = True
            else:
                self._log("WARN", "Nenhuma tracklet válida confirmada.")

        except Exception as e:
            self._log("ERROR", f"Erro fatal: {e}")
        finally:
            self._log("SYS", "━━━ PIPELINE FINALIZADO ━━━")
            self.after(0, lambda: self._pipeline_finished(success))

    # ━━━ CANDIDATOS ━━━
    def populate_candidates(self, candidates: list, wcs_map: dict = None):
        """Popula a tabela Candidates com detecções do pipeline."""
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.candidates_data.clear()

        for cand in candidates:
            ra_str, dec_str = "—", "—"
            if wcs_map and cand.frame_index in wcs_map:
                try:
                    wcs = wcs_map[cand.frame_index]
                    sky = wcs.pixel_to_world(cand.x_pixel, cand.y_pixel)
                    ra_str = f"{sky.ra.deg:.6f}°"
                    dec_str = f"{sky.dec.deg:.6f}°"
                except Exception:
                    pass

            fwhm_est = 3.0  # default do config
            status = "✓ VALID" if cand.is_valid else "✗ REJ"
            row = {
                "id": cand.id, "frame": cand.frame_index,
                "x": cand.x_pixel, "y": cand.y_pixel,
                "snr": cand.peak_significance, "fwhm": fwhm_est,
                "elong": cand.elongation, "ra": ra_str, "dec": dec_str,
                "status": status, "is_valid": cand.is_valid,
            }
            self.candidates_data.append(row)
            tag = "valid" if cand.is_valid else "rejected"
            self.tree.insert("", "end", values=(
                cand.id, cand.frame_index,
                f"{cand.x_pixel:.1f}", f"{cand.y_pixel:.1f}",
                f"{cand.peak_significance:.2f}σ", f"{fwhm_est:.1f}",
                f"{cand.elongation:.2f}", ra_str, dec_str, status,
            ), tags=(tag,))

        self.tree.tag_configure("valid", foreground=C["ok"])
        self.tree.tag_configure("rejected", foreground=C["text_dim"])
        self.cand_count_lbl.configure(
            text=f"{sum(1 for c in candidates if c.is_valid)} válidos / {len(candidates)} total")

    def _on_candidate_select(self, event):
        """Ao clicar em candidato na tabela, destaca no FITS Viewer."""
        sel = self.tree.selection()
        if not sel:
            return
        idx = self.tree.index(sel[0])
        if idx >= len(self.candidates_data):
            return
        cand = self.candidates_data[idx]

        # Muda para aba FITS Viewer
        self.tabview.set("FITS Viewer")

        if self.fits_viewer and self.fits_viewer._current_data is not None:
            self.fits_viewer.clear_highlights()
            self.fits_viewer.display(contrast_method=self.contrast_var.get())
            self.fits_viewer.highlight_candidate(
                x_centroid=cand["x"], y_centroid=cand["y"],
                fwhm=cand["fwhm"], box_scale=4.0,
                label=f"ID:{cand['id']} F:{cand['frame']}",
            )
            self.fits_viewer.zoom_to_candidate(cand["x"], cand["y"], zoom_radius=80)

    # ━━━ ADES XML ━━━
    def _load_ades_xml(self, path: Path):
        """Carrega e exibe o XML ADES na aba correspondente."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            self.ades_text.configure(state="normal")
            self.ades_text.delete("1.0", "end")
            self.ades_text.insert("1.0", content)
            self.ades_text.configure(state="disabled")
            self._log("OK", f"ADES XML carregado na aba: {path.name}")
        except Exception as e:
            self._log("ERROR", f"Falha ao carregar ADES: {e}")

    def update_ades_preview(self, xml_content: str):
        """Atualiza preview do ADES XML em tempo real."""
        self.ades_text.configure(state="normal")
        self.ades_text.delete("1.0", "end")
        self.ades_text.insert("1.0", xml_content)
        self.ades_text.configure(state="disabled")

    # ━━━ FITS VIEWER ━━━
    def load_fits_image(self, path: Path):
        """Carrega FITS e exibe no viewer."""
        if self.fits_viewer:
            ok = self.fits_viewer.load_fits(path)
            if ok:
                self.fits_viewer.display(contrast_method=self.contrast_var.get())
                self.current_fits_path = path
                self._log("OK", f"FITS carregado: {path.name}")

    def _on_contrast_change(self, value):
        if self.fits_viewer and self.fits_viewer._current_data is not None:
            self.fits_viewer.display(contrast_method=value)

    def _reset_fits_zoom(self):
        if self.fits_viewer:
            self.fits_viewer.reset_zoom()


# ━━━ ENTRY POINT ━━━
if __name__ == "__main__":
    app = SpaceFindXGUI()
    app.mainloop()
