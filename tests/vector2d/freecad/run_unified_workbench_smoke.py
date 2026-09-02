"""Valida que um único clone fornece PanelNest, Editor 2D e WoodCAM."""

import os
from pathlib import Path

import FreeCAD as App
import FreeCADGui as Gui


expected_root = Path(os.environ["WOODCAM_EXPECTED_ROOT"]).resolve()
workbenches = Gui.listWorkbenches()
if "PanelNestWorkbench" not in workbenches:
    raise AssertionError("A bancada PanelNest não foi registrada.")
if "WoodCAM2DWorkbench" in workbenches:
    raise AssertionError("A instalação unificada registrou uma segunda bancada WoodCAM.")

Gui.activateWorkbench("PanelNestWorkbench")

import panelnest
from commands.open_woodcam2d import find_woodcam_root, _load_woodcam_ui


panelnest_path = Path(panelnest.__file__).resolve()
if expected_root not in panelnest_path.parents:
    raise AssertionError("PanelNest foi carregado de fora do clone: %s" % panelnest_path)
if find_woodcam_root() != expected_root:
    raise AssertionError("O WoodCAM integrado não foi encontrado na raiz do clone.")

woodcam_ui = _load_woodcam_ui()
ui_path = Path(woodcam_ui.__file__).resolve()
if ui_path.parent != expected_root:
    raise AssertionError("WoodCAM foi carregado de fora do clone: %s" % ui_path)
if "PanelNest_OpenWoodCAM2D" not in Gui.listCommands():
    raise AssertionError("O comando WoodCAM não foi registrado na bancada PanelNest.")

print("UNIFIED_WORKBENCH_SMOKE=OK")
print("UNIFIED_PANELNEST_PATH=%s" % panelnest_path)
print("UNIFIED_WOODCAM_PATH=%s" % ui_path)

try:
    from PySide import QtCore
except ImportError:
    from PySide6 import QtCore

QtCore.QTimer.singleShot(100, Gui.getMainWindow().close)
