import os

import FreeCAD
import FreeCADGui


COMMAND_OPEN_DIALOG = "WoodCAM2D_OpenDialog"


def icon_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "Resources", "icons", "woodcam2d.svg")


class OpenWoodCAMDialogCommand:
    def GetResources(self):
        return {
            "Pixmap": icon_path(),
            "MenuText": "Abrir WoodCAM 2D",
            "ToolTip": "Gerar G-code 2.5D simples para CNC router em MDF",
        }

    def IsActive(self):
        return FreeCAD.ActiveDocument is not None

    def Activated(self):
        from ui import show_woodcam2d_dialog

        show_woodcam2d_dialog(FreeCADGui.getMainWindow())


def register_commands():
    FreeCADGui.addCommand(COMMAND_OPEN_DIALOG, OpenWoodCAMDialogCommand())
