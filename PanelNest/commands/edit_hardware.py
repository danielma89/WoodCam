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
        from PySide2 import QtCore, QtWidgets
    except ImportError:
        from PySide6 import QtCore, QtWidgets


COMMAND_NAME = "PanelNest_EditHardware"


def _exec_dialog(dialog):
    if hasattr(dialog, "exec_"):
        return dialog.exec_()
    return dialog.exec()


def _stretch_mode():
    if hasattr(QtWidgets.QHeaderView, "Stretch"):
        return QtWidgets.QHeaderView.Stretch
    return QtWidgets.QHeaderView.ResizeMode.Stretch


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


class HardwareEditorDialog(QtWidgets.QDialog):
    """Dialog para atribuir ferragens do catálogo a cada peça do projeto."""

    def __init__(self, parts, catalog, parent=None):
        super().__init__(parent)
        self._parts = parts
        self._catalog = catalog  # lista de HardwareItem ou dicts
        self._current_row = -1
        # mapa object_name -> lista de {hw_id, qty}
        self._hardware_map = {
            part.object_name: list(part.hardware or []) for part in parts
        }
        self.setWindowTitle("PanelNest: Ferragens por Peça")
        self.setModal(True)
        self.resize(900, 580)
        self._build_ui()

    def _catalog_item(self, hw_id):
        for item in self._catalog:
            item_id = getattr(item, "hw_id", None) or (item.get("hw_id") if isinstance(item, dict) else None)
            if item_id == hw_id:
                return item
        return None

    def _catalog_name(self, hw_id):
        item = self._catalog_item(hw_id)
        if item is None:
            return hw_id
        return getattr(item, "name", None) or (item.get("name", hw_id) if isinstance(item, dict) else hw_id)

    def _catalog_unit(self, hw_id):
        item = self._catalog_item(hw_id)
        if item is None:
            return "un"
        return getattr(item, "unit", "un") or (item.get("unit", "un") if isinstance(item, dict) else "un")

    def _build_ui(self):
        main = QtWidgets.QHBoxLayout(self)

        # ---- Painel esquerdo: lista de peças ----
        left = QtWidgets.QVBoxLayout()
        left.addWidget(QtWidgets.QLabel("<b>Peças</b>"))

        self._parts_list = QtWidgets.QListWidget()
        self._parts_list.setMinimumWidth(220)
        for part in self._parts:
            hw_count = len(self._hardware_map.get(part.object_name, []))
            label = f"{part.part_id} — {part.label}"
            if hw_count:
                label += f"  [{hw_count}]"
            item = QtWidgets.QListWidgetItem(label)
            item.setData(QtCore.Qt.UserRole, part.object_name)
            self._parts_list.addItem(item)
        self._parts_list.currentRowChanged.connect(self._on_part_selected)
        left.addWidget(self._parts_list)
        main.addLayout(left)

        # ---- Painel direito: ferragens da peça selecionada ----
        right = QtWidgets.QVBoxLayout()
        self._part_label = QtWidgets.QLabel("Selecione uma peça")
        self._part_label.setStyleSheet("font-weight: bold; color: #444;")
        right.addWidget(self._part_label)

        if not self._catalog:
            warn = QtWidgets.QLabel(
                "⚠ Nenhuma ferragem no catálogo. Configure o catálogo em "
                "Configurar Chapa → aba Ferragens."
            )
            warn.setWordWrap(True)
            warn.setStyleSheet("color: #a00;")
            right.addWidget(warn)

        # Tabela de ferragens desta peça
        self._hw_table = QtWidgets.QTableWidget(0, 3)
        self._hw_table.setHorizontalHeaderLabels(["Ferragem", "Unidade", "Qtd"])
        hw_header = self._hw_table.horizontalHeader()
        if hasattr(hw_header, "setSectionResizeMode"):
            hw_header.setSectionResizeMode(0, _stretch_mode())
        self._hw_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._hw_table.setMinimumWidth(400)
        right.addWidget(self._hw_table)

        # Botões de adicionar/remover
        btn_row = QtWidgets.QHBoxLayout()
        self._add_combo = QtWidgets.QComboBox()
        self._add_combo.setMinimumWidth(200)
        for item in self._catalog:
            hw_id = getattr(item, "hw_id", None) or (item.get("hw_id", "") if isinstance(item, dict) else "")
            name = getattr(item, "name", None) or (item.get("name", hw_id) if isinstance(item, dict) else hw_id)
            if hw_id:
                self._add_combo.addItem(f"{name}  ({hw_id})", hw_id)
        add_btn = QtWidgets.QPushButton("+ Adicionar")
        add_btn.clicked.connect(self._add_hardware_to_part)
        remove_btn = QtWidgets.QPushButton("Remover linha")
        remove_btn.clicked.connect(self._remove_hardware_from_part)
        btn_row.addWidget(self._add_combo)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(remove_btn)
        btn_row.addStretch(1)
        right.addLayout(btn_row)

        # Botões OK/Cancelar
        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        button_box.accepted.connect(self._on_accept)
        button_box.rejected.connect(self.reject)
        right.addWidget(button_box)
        main.addLayout(right)

        # Seleciona primeira peça automaticamente
        if self._parts_list.count() > 0:
            self._parts_list.setCurrentRow(0)

    def _on_part_selected(self, row):
        # Salva estado anterior
        if self._current_row >= 0 and self._current_row < len(self._parts):
            self._save_current_hw()

        self._current_row = row
        if row < 0 or row >= len(self._parts):
            return

        part = self._parts[row]
        self._part_label.setText(f"{part.part_id} — {part.label}  ({part.length_mm:.0f} × {part.width_mm:.0f} mm)")
        self._populate_hw_table(part.object_name)

    def _populate_hw_table(self, object_name):
        self._hw_table.setRowCount(0)
        for entry in self._hardware_map.get(object_name, []):
            hw_id = entry.get("hw_id", "")
            qty = int(entry.get("qty", 1))
            row = self._hw_table.rowCount()
            self._hw_table.insertRow(row)
            name_item = _read_only_item(self._catalog_name(hw_id))
            name_item.setData(QtCore.Qt.UserRole, hw_id)
            self._hw_table.setItem(row, 0, name_item)
            self._hw_table.setItem(row, 1, _read_only_item(self._catalog_unit(hw_id)))
            qty_spin = QtWidgets.QSpinBox()
            qty_spin.setRange(1, 9999)
            qty_spin.setValue(qty)
            self._hw_table.setCellWidget(row, 2, qty_spin)

    def _save_current_hw(self):
        if self._current_row < 0 or self._current_row >= len(self._parts):
            return
        object_name = self._parts[self._current_row].object_name
        hw_list = []
        for row in range(self._hw_table.rowCount()):
            name_item = self._hw_table.item(row, 0)
            qty_widget = self._hw_table.cellWidget(row, 2)
            if name_item is None:
                continue
            hw_id = name_item.data(QtCore.Qt.UserRole)
            qty = qty_widget.value() if qty_widget is not None else 1
            if hw_id:
                hw_list.append({"hw_id": hw_id, "qty": qty})
        self._hardware_map[object_name] = hw_list

    def _add_hardware_to_part(self):
        if self._current_row < 0:
            return
        hw_id = self._add_combo.currentData()
        if not hw_id:
            return
        # Verifica se já existe na tabela
        for row in range(self._hw_table.rowCount()):
            item = self._hw_table.item(row, 0)
            if item and item.data(QtCore.Qt.UserRole) == hw_id:
                qty_widget = self._hw_table.cellWidget(row, 2)
                if qty_widget:
                    qty_widget.setValue(qty_widget.value() + 1)
                return
        row = self._hw_table.rowCount()
        self._hw_table.insertRow(row)
        name_item = _read_only_item(self._catalog_name(hw_id))
        name_item.setData(QtCore.Qt.UserRole, hw_id)
        self._hw_table.setItem(row, 0, name_item)
        self._hw_table.setItem(row, 1, _read_only_item(self._catalog_unit(hw_id)))
        qty_spin = QtWidgets.QSpinBox()
        qty_spin.setRange(1, 9999)
        qty_spin.setValue(1)
        self._hw_table.setCellWidget(row, 2, qty_spin)

    def _remove_hardware_from_part(self):
        selected = self._hw_table.selectedRanges()
        if not selected:
            if self._hw_table.rowCount() > 0:
                self._hw_table.removeRow(self._hw_table.rowCount() - 1)
            return
        rows = sorted(
            {r for sr in selected for r in range(sr.topRow(), sr.bottomRow() + 1)},
            reverse=True,
        )
        for row in rows:
            self._hw_table.removeRow(row)

    def _on_accept(self):
        self._save_current_hw()
        self._update_list_labels()
        self.accept()

    def _update_list_labels(self):
        for i, part in enumerate(self._parts):
            hw_count = len(self._hardware_map.get(part.object_name, []))
            label = f"{part.part_id} — {part.label}"
            if hw_count:
                label += f"  [{hw_count}]"
            list_item = self._parts_list.item(i)
            if list_item:
                list_item.setText(label)

    def hardware_map(self):
        """Retorna {object_name: [{hw_id, qty}]} com o estado final."""
        return dict(self._hardware_map)


class EditHardwareCommand:
    def Activated(self):
        import panelnest

        try:
            parts = panelnest.collect_parts()
            settings = panelnest.get_sheet_settings()
        except ValueError as exc:
            if App is not None:
                App.Console.PrintError(f"PanelNest: {exc}\n")
            return

        catalog = getattr(settings, "hardware_catalog", [])

        dialog = HardwareEditorDialog(
            parts,
            catalog,
            parent=Gui.getMainWindow() if Gui is not None and hasattr(Gui, "getMainWindow") else None,
        )
        if _exec_dialog(dialog) != QtWidgets.QDialog.Accepted:
            return

        hw_map = dialog.hardware_map()
        doc = App.ActiveDocument if App is not None else None
        updated = 0
        for part in parts:
            hw_list = hw_map.get(part.object_name, [])
            obj = doc.getObject(part.object_name) if doc is not None else None
            if obj is not None:
                try:
                    panelnest.save_part_hardware(obj, hw_list)
                    updated += 1
                except Exception as exc:
                    if App is not None:
                        App.Console.PrintWarning(f"PanelNest: ferragens nao salvas em '{part.object_name}': {exc}\n")

        if doc is not None:
            doc.recompute()

        if App is not None:
            App.Console.PrintMessage(f"PanelNest: ferragens salvas em {updated} peca(s).\n")

    def IsActive(self):
        if App is None:
            return False
        return App.ActiveDocument is not None

    def GetResources(self):
        return {
            "Pixmap": os.path.join(
                os.path.dirname(__file__), "..", "resources", "icons", "panelnest_hardware.svg"
            ),
            "Accel": "",
            "MenuText": "Editar Ferragens",
            "ToolTip": "Associa dobraças, corrediças, parafusos e outros acessórios a cada peça do projeto.",
        }


if Gui is not None:
    Gui.addCommand(COMMAND_NAME, EditHardwareCommand())
