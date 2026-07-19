import os
import sys
import FreeCAD
import FreeCADGui

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from ui import show_woodcam2d_dialog


def run_woodcam2d():
    if not FreeCAD.GuiUp:
        FreeCAD.Console.PrintError("WoodCAM2D precisa ser executado dentro do FreeCAD com interface gráfica.\n")
        return

    show_woodcam2d_dialog(FreeCADGui.getMainWindow())


if __name__ == "__main__":
    run_woodcam2d()
