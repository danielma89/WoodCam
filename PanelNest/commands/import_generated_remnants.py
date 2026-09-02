import os

try:
    import FreeCAD as App
except ImportError:
    App = None

try:
    import FreeCADGui as Gui
except ImportError:
    Gui = None

try:
    from PySide import QtGui

    QtWidgets = QtGui
except ImportError:
    try:
        from PySide2 import QtWidgets
    except ImportError:
        from PySide6 import QtWidgets


COMMAND_NAME = "PanelNest_ImportGeneratedRemnants"


def _main_window():
    if Gui is not None and hasattr(Gui, "getMainWindow"):
        try:
            return Gui.getMainWindow()
        except Exception:
            return None
    return None


def _show_error_dialog(message):
    if Gui is None:
        return
    QtWidgets.QMessageBox.critical(_main_window(), "PanelNest: Atualizar Estoque de Retalhos", message)


def _show_info_dialog(message):
    if Gui is None:
        return
    QtWidgets.QMessageBox.information(_main_window(), "PanelNest: Atualizar Estoque de Retalhos", message)


class ImportGeneratedRemnantsCommand:
    def Activated(self):
        import panelnest

        try:
            result = panelnest.sync_generated_remnants_to_stock()
        except ValueError as exc:
            if App is not None:
                App.Console.PrintError(f"PanelNest: {exc}\n")
            _show_error_dialog(str(exc))
            return

        updated_settings = result["updated_settings"]
        config = result["config"]
        generated_count = result["generated_count"]
        generated_types = result["generated_types"]
        replaced_generated_types = result["replaced_generated_types"]

        message = (
            "PanelNest atualizou o estoque de retalhos do documento.\n\n"
            f"Retalhos encontrados: {generated_count}\n"
            f"Tipos consolidados: {generated_types}\n"
            f"Tipos [Gerado] substituidos: {replaced_generated_types}\n"
            f"Tipos atuais no estoque: {len(updated_settings.remnants)}"
        )

        if App is not None:
            App.Console.PrintMessage(f"{message}\n")

        _show_info_dialog(message)

        if Gui is not None and App is not None and App.ActiveDocument is not None:
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(App.ActiveDocument.Name, config.Name)

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
            "MenuText": "Atualizar Estoque com Retalhos",
            "ToolTip": (
                "Le os retalhos gerados pelo ultimo layout e atualiza o estoque de retalhos do documento, preservando os retalhos manuais."
            ),
        }


if Gui is not None:
    Gui.addCommand(COMMAND_NAME, ImportGeneratedRemnantsCommand())
