"""
Space-FindX — Entry Point
===========================
Inicializa a interface gráfica principal do pipeline de detecção de NEOs.
"""

import os
import sys

# Garante que o diretório raiz está no path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gui.main_window import SpaceFindXGUI

if __name__ == "__main__":
    app = SpaceFindXGUI()
    app.mainloop()
