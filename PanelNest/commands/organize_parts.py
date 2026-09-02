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


COMMAND_NAME = "PanelNest_OrganizeParts"


def _exec_dialog(dialog):
    if hasattr(dialog, "exec"):
        return dialog.exec()
    return dialog.exec_()


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


def _stretch_mode():
    if hasattr(QtWidgets.QHeaderView, "Stretch"):
        return QtWidgets.QHeaderView.Stretch
    return QtWidgets.QHeaderView.ResizeMode.Stretch


def _format_number(value):
    formatted = f"{value:.2f}"
    return formatted.rstrip("0").rstrip(".")


class OrganizationPreviewDialog(QtWidgets.QDialog):
    def __init__(self, groups, parent=None):
        super().__init__(parent)
        import panelnest

        self._dialog_size_key = "organize_parts_preview"
        self.setWindowTitle("PanelNest: Organizar Pecas")
        self.setModal(True)

        main_layout = QtWidgets.QVBoxLayout(self)

        part_count = sum(len(group["parts"]) for group in groups)
        intro = QtWidgets.QLabel(
            "Confira a ordem de organizacao antes de gerar a planilha. "
            "A lista abaixo usa o mesmo agrupamento do layout: material, espessura e metodo de corte."
        )
        intro.setWordWrap(True)
        main_layout.addWidget(intro)

        summary = QtWidgets.QLabel(
            f"{part_count} peca(s) em {len(groups)} grupo(s). Clique em OK para gerar a planilha."
        )
        main_layout.addWidget(summary)

        self.table = QtWidgets.QTableWidget(part_count, 13)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.table.setHorizontalHeaderLabels(
            [
                "Grupo",
                "Material",
                "Esp. (mm)",
                "Metodo de corte",
                "Pode girar",
                "Veio/fibra",
                "ID",
                "Rotulo",
                "Compr. (mm)",
                "Larg. (mm)",
                "Qtd",
                "Fita de borda",
                "Objeto",
            ]
        )

        row_index = 0
        for group in groups:
            for part in group["parts"]:
                tooltip = f"Objeto interno: {part.object_name}"
                self.table.setItem(row_index, 0, _read_only_item(group["group_id"], tooltip))
                self.table.setItem(row_index, 1, _read_only_item(group["material"], tooltip))
                self.table.setItem(row_index, 2, _read_only_item(_format_number(group["thickness_mm"])))
                self.table.setItem(row_index, 3, _read_only_item(group["cut_method"], tooltip))
                self.table.setItem(row_index, 4, _read_only_item("Sim" if part.allow_rotation else "Nao"))
                self.table.setItem(row_index, 5, _read_only_item(part.grain_direction, tooltip))
                self.table.setItem(row_index, 6, _read_only_item(part.part_id, tooltip))
                self.table.setItem(row_index, 7, _read_only_item(part.label, tooltip))
                self.table.setItem(row_index, 8, _read_only_item(_format_number(part.length_mm)))
                self.table.setItem(
                    row_index,
                    9,
                    _read_only_item(_format_number(part.width_mm)),
                )
                self.table.setItem(row_index, 10, _read_only_item(str(part.quantity)))
                self.table.setItem(
                    row_index,
                    11,
                    _read_only_item(self._edge_band_summary(part)),
                )
                self.table.setItem(row_index, 12, _read_only_item(part.object_name, tooltip))
                row_index += 1

        header = self.table.horizontalHeader()
        if hasattr(header, "setSectionResizeMode"):
            header.setSectionResizeMode(7, _stretch_mode())
            header.setSectionResizeMode(12, _stretch_mode())
        self.table.resizeColumnsToContents()
        main_layout.addWidget(self.table)

        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        ok_button = button_box.button(QtWidgets.QDialogButtonBox.Ok)
        if ok_button is not None:
            ok_button.setText("Gerar Planilha")
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

    def _edge_band_summary(self, part):
        sides = []
        if part.edge_band_top:
            sides.append("Sup.")
        if part.edge_band_bottom:
            sides.append("Inf.")
        if part.edge_band_left:
            sides.append("Esq.")
        if part.edge_band_right:
            sides.append("Dir.")
        return ", ".join(sides) if sides else "-"

    def _persist_dialog_size(self, _result):
        import panelnest

        panelnest.save_dialog_size(self, self._dialog_size_key)


class OrganizePartsCommand:
    def Activated(self):
        import panelnest

        try:
            parts = panelnest.collect_parts()
        except ValueError as exc:
            if App is not None:
                App.Console.PrintError(f"PanelNest: {exc}\n")
            return

        if not parts:
            if App is not None:
                App.Console.PrintError(
                    "PanelNest: nenhuma peca valida foi encontrada para organizacao.\n"
                )
            return

        groups = panelnest.group_parts(parts)

        dialog = OrganizationPreviewDialog(
            groups,
            parent=Gui.getMainWindow() if Gui is not None and hasattr(Gui, "getMainWindow") else None,
        )
        if _exec_dialog(dialog) != QtWidgets.QDialog.Accepted:
            return

        sheet = panelnest.create_organization_spreadsheet(parts)
        labels_sheet = panelnest.create_part_labels_spreadsheet(parts)
        validation_sheet = panelnest.create_project_validation_spreadsheet(parts=parts)
        edge_band_sheet = panelnest.create_edge_band_spreadsheet(parts)

        if App is not None:
            message = f"PanelNest: organizou {len(parts)} peca(s) em {len(groups)} grupo(s)."
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
                os.path.dirname(__file__), "..", "resources", "icons", "panelnest_group.svg"
            ),
            "Accel": "",
            "MenuText": "Organizar para Layout",
            "ToolTip": "Agrupa as pecas por material, espessura e metodo de corte para preparar o layout das chapas.",
        }


if Gui is not None and hasattr(Gui, "addCommand"):
    Gui.addCommand(COMMAND_NAME, OrganizePartsCommand())
