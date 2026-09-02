import os

try:
    import FreeCADGui as Gui
except ImportError:
    Gui = None


COMMAND_NAME = "PanelNest_CreateDemoPanel"


class CreateDemoPanelCommand:
    def Activated(self):
        import panelnest

        panelnest.create_demo_panel()
        if Gui is not None:
            Gui.SendMsgToActiveView("ViewFit")

    def IsActive(self):
        return True

    def GetResources(self):
        return {
            "Pixmap": os.path.join(
                os.path.dirname(__file__), "..", "resources", "icons", "panelnest_panel.svg"
            ),
            "Accel": "",
            "MenuText": "Criar Painel de Teste",
            "ToolTip": "Cria um painel de MDF de exemplo para validar a bancada.",
        }


if Gui is not None:
    Gui.addCommand(COMMAND_NAME, CreateDemoPanelCommand())
