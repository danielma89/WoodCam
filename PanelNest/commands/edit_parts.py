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


COMMAND_NAME = "PanelNest_EditParts"


def _exec_dialog(dialog):
    if hasattr(dialog, "exec"):
        return dialog.exec()
    return dialog.exec_()


def _align_center():
    if hasattr(QtCore.Qt, "AlignCenter"):
        return QtCore.Qt.AlignCenter
    return QtCore.Qt.AlignmentFlag.AlignCenter


def _item_is_editable():
    if hasattr(QtCore.Qt, "ItemIsEditable"):
        return QtCore.Qt.ItemIsEditable
    return QtCore.Qt.ItemFlag.ItemIsEditable


def _read_only_item(text, tooltip=""):
    item = QtWidgets.QTableWidgetItem(text)
    item.setFlags(item.flags() & ~_item_is_editable())
    if tooltip:
        item.setToolTip(tooltip)
    return item


def _checkbox_widget(checked):
    container = QtWidgets.QWidget()
    layout = QtWidgets.QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setAlignment(_align_center())
    checkbox = QtWidgets.QCheckBox()
    checkbox.setChecked(checked)
    layout.addWidget(checkbox)
    container.checkbox = checkbox
    return container


def _stretch_mode():
    if hasattr(QtWidgets.QHeaderView, "Stretch"):
        return QtWidgets.QHeaderView.Stretch
    return QtWidgets.QHeaderView.ResizeMode.Stretch


class PartsEditorDialog(QtWidgets.QDialog):
    def __init__(self, parts, cut_method_options, grain_direction_options, parent=None):
        super().__init__(parent)
        import panelnest

        self._parts = parts
        self._cut_method_options = list(cut_method_options)
        self._grain_direction_options = list(grain_direction_options)
        self._dialog_size_key = "edit_parts"
        self.setWindowTitle("PanelNest: Editor de Pecas")
        self.setModal(True)

        main_layout = QtWidgets.QVBoxLayout(self)

        intro = QtWidgets.QLabel(
            "Edite material, metodo de corte, rotacao, veio/fibra e fita de borda por peca. "
            "Se a selecao nao tiver pecas validas, o PanelNest usa todas as pecas visiveis."
        )
        intro.setWordWrap(True)
        main_layout.addWidget(intro)

        self.table = QtWidgets.QTableWidget(len(parts), 13)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.table.setHorizontalHeaderLabels(
            [
                "ID",
                "Rotulo",
                "Compr. (mm)",
                "Larg. (mm)",
                "Esp. (mm)",
                "Material",
                "Metodo de corte",
                "Pode girar",
                "Veio/fibra",
                "Sup.",
                "Inf.",
                "Esq.",
                "Dir.",
            ]
        )

        for row_index, part in enumerate(parts):
            tooltip = f"Objeto interno: {part.object_name}"
            self.table.setItem(row_index, 0, _read_only_item(part.part_id, tooltip))
            self.table.setItem(row_index, 1, _read_only_item(part.label, tooltip))
            self.table.setItem(row_index, 2, _read_only_item(_format_number(part.length_mm)))
            self.table.setItem(row_index, 3, _read_only_item(_format_number(part.width_mm)))
            self.table.setItem(row_index, 4, _read_only_item(_format_number(part.thickness_mm)))

            material_item = QtWidgets.QTableWidgetItem(part.material or "")
            material_item.setToolTip(tooltip)
            self.table.setItem(row_index, 5, material_item)

            cut_method_combo = QtWidgets.QComboBox()
            for option in self._cut_method_options:
                cut_method_combo.addItem(option)
            index = cut_method_combo.findText(part.cut_method or "Auto")
            if index >= 0:
                cut_method_combo.setCurrentIndex(index)
            self.table.setCellWidget(row_index, 6, cut_method_combo)

            self.table.setCellWidget(row_index, 7, _checkbox_widget(part.allow_rotation))

            grain_direction_combo = QtWidgets.QComboBox()
            for option in self._grain_direction_options:
                grain_direction_combo.addItem(option)
            index = grain_direction_combo.findText(part.grain_direction or self._grain_direction_options[0])
            if index >= 0:
                grain_direction_combo.setCurrentIndex(index)
            self.table.setCellWidget(row_index, 8, grain_direction_combo)

            self.table.setCellWidget(row_index, 9, _checkbox_widget(part.edge_band_top))
            self.table.setCellWidget(row_index, 10, _checkbox_widget(part.edge_band_bottom))
            self.table.setCellWidget(row_index, 11, _checkbox_widget(part.edge_band_left))
            self.table.setCellWidget(row_index, 12, _checkbox_widget(part.edge_band_right))

        header = self.table.horizontalHeader()
        if hasattr(header, "setSectionResizeMode"):
            header.setSectionResizeMode(1, _stretch_mode())
        self.table.resizeColumnsToContents()
        main_layout.addWidget(self.table)

        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        main_layout.addWidget(button_box)
        self.finished.connect(self._persist_dialog_size)

        panelnest.restore_dialog_size(
            self,
            self._dialog_size_key,
            default_width=1220,
            default_height=560,
            minimum_width=900,
            minimum_height=420,
        )

    def row_data(self):
        data = []
        for row_index, part in enumerate(self._parts):
            material_item = self.table.item(row_index, 5)
            cut_method_combo = self.table.cellWidget(row_index, 6)
            allow_rotation_widget = self.table.cellWidget(row_index, 7)
            grain_direction_combo = self.table.cellWidget(row_index, 8)
            top_widget = self.table.cellWidget(row_index, 9)
            bottom_widget = self.table.cellWidget(row_index, 10)
            left_widget = self.table.cellWidget(row_index, 11)
            right_widget = self.table.cellWidget(row_index, 12)

            data.append(
                {
                    "object_name": part.object_name,
                    "material": material_item.text().strip() if material_item is not None else "",
                    "cut_method": cut_method_combo.currentText() if cut_method_combo is not None else "Auto",
                    "allow_rotation": bool(allow_rotation_widget.checkbox.isChecked()),
                    "grain_direction": (
                        grain_direction_combo.currentText()
                        if grain_direction_combo is not None
                        else self._grain_direction_options[0]
                    ),
                    "edge_band_top": bool(top_widget.checkbox.isChecked()),
                    "edge_band_bottom": bool(bottom_widget.checkbox.isChecked()),
                    "edge_band_left": bool(left_widget.checkbox.isChecked()),
                    "edge_band_right": bool(right_widget.checkbox.isChecked()),
                }
            )
        return data

    def _persist_dialog_size(self, _result):
        import panelnest

        panelnest.save_dialog_size(self, self._dialog_size_key)


def _format_number(value):
    formatted = f"{value:.2f}"
    return formatted.rstrip("0").rstrip(".")


class EditPartsCommand:
    def Activated(self):
        import panelnest

        try:
            source_objects = panelnest.get_part_source_objects()
            parts = panelnest.prepare_parts_metadata(source_objects)
        except ValueError as exc:
            if App is not None:
                App.Console.PrintError(f"PanelNest: {exc}\n")
            return

        dialog = PartsEditorDialog(
            parts,
            panelnest.CUT_METHOD_OPTIONS,
            panelnest.GRAIN_DIRECTION_OPTIONS,
            parent=Gui.getMainWindow() if Gui is not None and hasattr(Gui, "getMainWindow") else None,
        )
        if _exec_dialog(dialog) != QtWidgets.QDialog.Accepted:
            return

        try:
            updated_parts = panelnest.apply_part_rows(dialog.row_data())
        except ValueError as exc:
            if App is not None:
                App.Console.PrintError(f"PanelNest: {exc}\n")
            return

        if App is not None:
            App.Console.PrintMessage(
                f"PanelNest: atualizou os dados de {len(updated_parts)} peca(s) no editor.\n"
            )

        if Gui is not None and App is not None and App.ActiveDocument is not None and updated_parts:
            Gui.Selection.clearSelection()
            for part in updated_parts:
                Gui.Selection.addSelection(App.ActiveDocument.Name, part.object_name)

    def IsActive(self):
        if App is None:
            return False
        return App.ActiveDocument is not None

    def GetResources(self):
        return {
            "Pixmap": os.path.join(
                os.path.dirname(__file__), "..", "resources", "icons", "panelnest_editor.svg"
            ),
            "Accel": "",
            "MenuText": "Editar Dados das Pecas",
            "ToolTip": "Abre uma tabela para revisar e editar material, corte, rotacao, veio/fibra e fita de borda por peca.",
        }


if Gui is not None:
    Gui.addCommand(COMMAND_NAME, EditPartsCommand())
