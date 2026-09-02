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
    from PySide import QtCore, QtGui

    QtWidgets = QtGui
except ImportError:
    try:
        from PySide2 import QtCore, QtGui, QtWidgets
    except ImportError:
        from PySide6 import QtCore, QtGui, QtWidgets


COMMAND_NAME = "PanelNest_ValidateProject"


def _exec_dialog(dialog):
    if hasattr(dialog, "exec_"):
        return dialog.exec_()
    return dialog.exec()


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
    QtWidgets.QMessageBox.critical(_main_window(), "PanelNest: Validar Projeto", message)


def _stretch_mode():
    if hasattr(QtWidgets.QHeaderView, "Stretch"):
        return QtWidgets.QHeaderView.Stretch
    return QtWidgets.QHeaderView.ResizeMode.Stretch


def _item_is_editable():
    if hasattr(QtCore.Qt, "ItemIsEditable"):
        return QtCore.Qt.ItemIsEditable
    return QtCore.Qt.ItemFlag.ItemIsEditable


def _resize_to_contents_mode():
    if hasattr(QtWidgets.QHeaderView, "ResizeToContents"):
        return QtWidgets.QHeaderView.ResizeToContents
    return QtWidgets.QHeaderView.ResizeMode.ResizeToContents


def _severity_counts(issues):
    counts = {"Erro": 0, "Alerta": 0, "Info": 0, "OK": 0}
    for issue in issues or []:
        severity = str(issue.get("severity", "") or "")
        counts[severity] = counts.get(severity, 0) + 1
    return counts


def _severity_colors(severity):
    palette = {
        "Erro": ("#fdeceb", "#8c2d23"),
        "Alerta": ("#fff3dc", "#7a4d12"),
        "Info": ("#eaf3fb", "#215c7a"),
        "OK": ("#edf7ef", "#2d6a39"),
    }
    return palette.get(severity, ("#f5f5f5", "#434343"))


def _styled_read_only_item(text, severity=None, tooltip=""):
    item = QtWidgets.QTableWidgetItem(str(text or ""))
    if hasattr(item, "setFlags"):
        item.setFlags(item.flags() & ~_item_is_editable())
    if tooltip:
        item.setToolTip(tooltip)
    if severity:
        background_hex, foreground_hex = _severity_colors(severity)
        item.setBackground(QtGui.QColor(background_hex))
        item.setForeground(QtGui.QBrush(QtGui.QColor(foreground_hex)))
    return item


class ProjectValidationDialog(QtWidgets.QDialog):
    def __init__(self, issues, sheet_label, parent=None):
        super().__init__(parent)
        import panelnest

        self._dialog_size_key = "validate_project_v1"
        self.setWindowTitle("PanelNest: Validar Projeto")
        self.setModal(True)
        if hasattr(self, "setSizeGripEnabled"):
            self.setSizeGripEnabled(True)

        counts = _severity_counts(issues)
        has_pending = counts.get("Erro", 0) > 0 or counts.get("Alerta", 0) > 0

        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(6)

        intro = QtWidgets.QLabel(
            "Revise as pendencias abaixo antes de seguir para layout, etiquetas e exportacao."
        )
        intro.setWordWrap(True)
        main_layout.addWidget(intro)

        summary = QtWidgets.QLabel(
            (
                f"Planilha: {sheet_label} | "
                f"{counts.get('Erro', 0)} erro(s), "
                f"{counts.get('Alerta', 0)} alerta(s), "
                f"{counts.get('Info', 0)} info(s)."
            )
        )
        summary.setWordWrap(True)
        if has_pending:
            summary.setStyleSheet("color: #7a4d12; font-weight: 700;")
        else:
            summary.setStyleSheet("color: #2d6a39; font-weight: 700;")
        main_layout.addWidget(summary)

        self.table = QtWidgets.QTableWidget(len(issues), 8)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        if hasattr(self.table, "setEditTriggers"):
            self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        if hasattr(self.table, "verticalHeader"):
            try:
                self.table.verticalHeader().setVisible(False)
            except Exception:
                pass
        self.table.setWordWrap(True)
        self.table.setHorizontalHeaderLabels(
            [
                "Severidade",
                "Categoria",
                "Grupo",
                "ID",
                "Rotulo/Referencia",
                "Problema",
                "Sugestao",
                "Objeto",
            ]
        )

        for row_index, issue in enumerate(issues):
            severity = issue.get("severity", "")
            tooltip = issue.get("object_name", "")
            values = (
                severity,
                issue.get("category", ""),
                issue.get("group_id", ""),
                issue.get("part_id", ""),
                issue.get("part_label", ""),
                issue.get("problem", ""),
                issue.get("suggestion", ""),
                issue.get("object_name", ""),
            )
            for column_index, value in enumerate(values):
                self.table.setItem(
                    row_index,
                    column_index,
                    _styled_read_only_item(value, severity=severity, tooltip=tooltip),
                )

        header = self.table.horizontalHeader()
        if hasattr(header, "setSectionResizeMode"):
            for column_index in (0, 1, 2, 3):
                header.setSectionResizeMode(column_index, _resize_to_contents_mode())
            for column_index in (4, 5, 6, 7):
                header.setSectionResizeMode(column_index, _stretch_mode())

        self.table.resizeRowsToContents()
        main_layout.addWidget(self.table, 1)

        button_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        close_button = button_box.button(QtWidgets.QDialogButtonBox.Close)
        if close_button is not None:
            close_button.setText("Fechar")
            close_button.setAutoDefault(False)
            close_button.setDefault(False)
        button_box.rejected.connect(self.reject)
        button_box.accepted.connect(self.accept)
        main_layout.addWidget(button_box)

        self.finished.connect(self._persist_dialog_size)
        panelnest.restore_dialog_size(
            self,
            self._dialog_size_key,
            default_width=1180,
            default_height=560,
            minimum_width=900,
            minimum_height=420,
        )

    def _persist_dialog_size(self, _result):
        import panelnest

        panelnest.save_dialog_size(self, self._dialog_size_key)


class ValidateProjectCommand:
    def Activated(self):
        import panelnest

        try:
            parts = panelnest.collect_parts()
            issues = panelnest.collect_project_validation_issues(parts=parts)
            sheet = panelnest.create_project_validation_spreadsheet(parts=parts, issues=issues)
        except ValueError as exc:
            if App is not None:
                App.Console.PrintError(f"PanelNest: {exc}\n")
            _show_error_dialog(str(exc))
            return

        counts = _severity_counts(issues)
        message = (
            "PanelNest: validacao concluida. "
            f"{counts.get('Erro', 0)} erro(s), "
            f"{counts.get('Alerta', 0)} alerta(s), "
            f"{counts.get('Info', 0)} info(s). "
            f"Planilha em '{sheet.Label}'."
        )
        if App is not None:
            if counts.get("Erro", 0) or counts.get("Alerta", 0):
                if hasattr(App.Console, "PrintWarning"):
                    App.Console.PrintWarning(f"{message}\n")
                else:
                    App.Console.PrintMessage(f"{message}\n")
            else:
                App.Console.PrintMessage(f"{message}\n")

        if Gui is not None:
            dialog = ProjectValidationDialog(
                issues,
                sheet.Label,
                parent=_main_window(),
            )
            _exec_dialog(dialog)

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
                os.path.dirname(__file__), "..", "resources", "icons", "panelnest_validate.svg"
            ),
            "Accel": "",
            "MenuText": "Validar Projeto",
            "ToolTip": (
                "Verifica configuracao, pecas, estoque e layout para apontar pendencias antes da producao."
            ),
        }


if Gui is not None:
    Gui.addCommand(COMMAND_NAME, ValidateProjectCommand())
