import os

try:
    import FreeCAD as App
except ImportError:
    App = None

try:
    import FreeCADGui as Gui
except ImportError:
    Gui = None


COMMAND_NAME = "PanelNest_LabelParts"


class LabelPartsCommand:
    def Activated(self):
        import panelnest

        try:
            parts = panelnest.apply_part_labels()
        except ValueError as exc:
            if App is not None:
                App.Console.PrintError(f"PanelNest: {exc}\n")
            return

        if App is not None:
            App.Console.PrintMessage(
                f"PanelNest: etiquetou {len(parts)} peca(s) com IDs do PanelNest.\n"
            )

    def IsActive(self):
        if App is None:
            return False
        return App.ActiveDocument is not None

    def GetResources(self):
        return {
            "Pixmap": os.path.join(
                os.path.dirname(__file__), "..", "resources", "icons", "panelnest_label.svg"
            ),
            "Accel": "",
            "MenuText": "Etiquetar Pecas",
            "ToolTip": "Gera ou atualiza os codigos PN-xxx das pecas da selecao ou de todas as pecas visiveis.",
        }


if Gui is not None:
    Gui.addCommand(COMMAND_NAME, LabelPartsCommand())
