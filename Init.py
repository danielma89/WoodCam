"""Inicialização sem GUI do pacote unificado PanelNest/WoodCAM."""

import os
import sys

import FreeCAD


_PROJECT_ROOT = os.path.realpath(
    os.environ.get("WOODCAM_UNIFIED_ROOT", "").strip()
    or os.path.join(FreeCAD.getUserAppDataDir(), "Mod", "PanelNest")
)
_PANELNEST_ROOT = os.path.join(_PROJECT_ROOT, "PanelNest")
for _path in (_PANELNEST_ROOT, _PROJECT_ROOT):
    if _path not in sys.path:
        sys.path.insert(0, _path)

FreeCAD.Console.PrintLog("Loading unified PanelNest/WoodCAM workbench\n")
