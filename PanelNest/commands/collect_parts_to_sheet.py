import os

try:
    import FreeCAD as App
except ImportError:
    App = None

try:
    import FreeCADGui as Gui
except ImportError:
    Gui = None


COMMAND_NAME = "PanelNest_CollectPartsToSheet"


class CollectPartsToSheetCommand:
    def Activated(self):
        import panelnest

        try:
            parts, sheet = panelnest.collect_parts_to_spreadsheet()
            labels_sheet = panelnest.create_part_labels_spreadsheet(parts)
            validation_sheet = panelnest.create_project_validation_spreadsheet(parts=parts)
            edge_band_sheet = panelnest.create_edge_band_spreadsheet(parts)
        except ValueError as exc:
            if App is not None:
                App.Console.PrintError(f"PanelNest: {exc}\n")
            return

        if App is not None:
            message = f"PanelNest: exportou {len(parts)} peca(s) para a planilha '{sheet.Label}'."
            if labels_sheet is not None:
                message += f" Etiquetas em '{labels_sheet.Label}'."
            if validation_sheet is not None:
                message += f" Validacao em '{validation_sheet.Label}'."
            if edge_band_sheet is not None:
                message += f" Acabamento em '{edge_band_sheet.Label}'."
            App.Console.PrintMessage(f"{message}\n")

        if Gui is not None and App is not None and App.ActiveDocument is not None:
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(App.ActiveDocument.Name, sheet.Name)

    def IsActive(self):
        if App is None:
            return False
        return App.ActiveDocument is not None

    def GetResources(self):
        return {
            "Pixmap": os.path.join(
                os.path.dirname(__file__), "..", "resources", "icons", "panelnest_sheet.svg"
            ),
            "Accel": "",
            "MenuText": "Planilha de Pecas",
            "ToolTip": "Cria ou atualiza uma planilha de pecas a partir da selecao atual ou de todas as pecas visiveis.",
        }


if Gui is not None:
    Gui.addCommand(COMMAND_NAME, CollectPartsToSheetCommand())
