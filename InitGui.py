import os

import FreeCAD

if FreeCAD.GuiUp:
    import FreeCADGui
    from FreeCADGui import Workbench
else:
    class Workbench:
        pass


class WoodCAM2DWorkbench(Workbench):
    Icon = os.path.join(
        FreeCAD.getUserAppDataDir(),
        "Mod",
        "WoodCAM2D",
        "resources",
        "diagrams",
        "botao2.png",
    )
    MenuText = "WoodCAM 2D"
    ToolTip = "CNC Marcenaria 2D para FreeCAD"

    def Initialize(self):
        import woodcam2d_commands

        woodcam2d_commands.register_commands()
        command_list = [woodcam2d_commands.COMMAND_OPEN_DIALOG]
        self.appendToolbar("WoodCAM 2D", command_list)
        self.appendMenu("WoodCAM 2D", command_list)

    def GetClassName(self):
        return "Gui::PythonWorkbench"


def _panelnest_is_installed():
    """Verifica a presença do módulo-irmão sem precisar importá-lo."""
    candidates = []
    try:
        candidates.append(os.path.join(FreeCAD.getUserAppDataDir(), "Mod", "PanelNest"))
    except Exception:
        pass
    return any(os.path.isfile(os.path.join(path, "InitGui.py")) for path in candidates)


if FreeCAD.GuiUp:
    import woodcam2d_commands

    woodcam2d_commands.register_commands()
    if not _panelnest_is_installed():
        FreeCADGui.addWorkbench(WoodCAM2DWorkbench())
