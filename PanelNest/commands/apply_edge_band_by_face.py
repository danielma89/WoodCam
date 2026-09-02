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


COMMAND_NAME = "PanelNest_ApplyEdgeBandByFace"


def _exec_dialog(dialog):
    if hasattr(dialog, "exec"):
        return dialog.exec()
    return dialog.exec_()


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
    QtWidgets.QMessageBox.critical(_main_window(), "PanelNest: Aplicar Fita por Face", message)


def _item_is_editable():
    if hasattr(QtCore.Qt, "ItemIsEditable"):
        return QtCore.Qt.ItemIsEditable
    return QtCore.Qt.ItemFlag.ItemIsEditable


def _read_only_item(text, tooltip=""):
    item = QtWidgets.QTableWidgetItem(str(text or ""))
    item.setFlags(item.flags() & ~_item_is_editable())
    if tooltip:
        item.setToolTip(tooltip)
    return item


def _stretch_mode():
    if hasattr(QtWidgets.QHeaderView, "Stretch"):
        return QtWidgets.QHeaderView.Stretch
    return QtWidgets.QHeaderView.ResizeMode.Stretch


def _mm_text(value):
    try:
        numeric_value = float(value)
    except Exception:
        numeric_value = 0.0
    if abs(numeric_value - round(numeric_value)) <= 0.05:
        return str(int(round(numeric_value)))
    return f"{numeric_value:.1f}".rstrip("0").rstrip(".")


class EdgeBandPreviewWidget(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._item = {}
        self._rectangles = {}
        self._active_sides = set()
        self._multi_select = False  # False = single-side mode, True = multi-toggle mode
        self.on_side_clicked = None
        self.setMinimumHeight(210)

    def set_multi_select(self, enabled: bool):
        self._multi_select = enabled
        if not enabled:
            self._active_sides.clear()
        self.update()

    def set_item(self, item):
        self._item = dict(item or {})
        if not self._multi_select:
            side_key = str(self._item.get("side_key", "") or "")
            self._active_sides = {side_key} if side_key else set()
        self.update()

    def set_active_sides(self, sides):
        self._active_sides = set(sides or [])
        self.update()

    def get_active_sides(self):
        return frozenset(self._active_sides)

    def mousePressEvent(self, event):
        position = event.pos() if hasattr(event, "pos") else None
        if position is None:
            return super().mousePressEvent(event)

        for side_key, rect in self._rectangles.items():
            if rect.contains(position):
                if self._multi_select:
                    if side_key in self._active_sides:
                        self._active_sides.discard(side_key)
                    else:
                        self._active_sides.add(side_key)
                    self.update()
                    if callable(self.on_side_clicked):
                        self.on_side_clicked(side_key)
                else:
                    if callable(self.on_side_clicked):
                        self.on_side_clicked(side_key)
                    self.update()
                return
        return super().mousePressEvent(event)

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)

        background_color = QtGui.QColor(247, 244, 240)
        outline_color = QtGui.QColor(96, 82, 74)
        board_color = QtGui.QColor(223, 213, 204)
        side_fill_color = QtGui.QColor(233, 227, 220)
        active_side_color = QtGui.QColor(72, 186, 201)
        active_text_color = QtGui.QColor(10, 52, 58)
        text_color = QtGui.QColor(70, 58, 52)
        hint_color = QtGui.QColor(118, 101, 92)

        painter.fillRect(self.rect(), background_color)

        if not self._item:
            painter.setPen(hint_color)
            painter.drawText(self.rect(), int(QtCore.Qt.AlignCenter), "Selecione uma linha para ver a peca em plano.")
            painter.end()
            return

        margin = 18
        available_rect = self.rect().adjusted(margin, margin, -margin, -margin)
        board_rect = QtCore.QRectF(
            available_rect.left() + 78,
            available_rect.top() + 36,
            max(120.0, available_rect.width() - 156.0),
            max(74.0, available_rect.height() - 92.0),
        )

        band_width = max(20.0, min(board_rect.width(), board_rect.height()) * 0.14)
        self._rectangles = {
            "top": QtCore.QRectF(
                board_rect.left(),
                board_rect.top() - band_width,
                board_rect.width(),
                band_width,
            ),
            "bottom": QtCore.QRectF(
                board_rect.left(),
                board_rect.bottom(),
                board_rect.width(),
                band_width,
            ),
            "left": QtCore.QRectF(
                board_rect.left() - band_width,
                board_rect.top(),
                band_width,
                board_rect.height(),
            ),
            "right": QtCore.QRectF(
                board_rect.right(),
                board_rect.top(),
                band_width,
                board_rect.height(),
            ),
        }

        painter.setPen(QtGui.QPen(outline_color, 1.4))
        painter.setBrush(board_color)
        painter.drawRoundedRect(board_rect, 6.0, 6.0)

        length_mm = _mm_text(self._item.get("length_mm", 0.0))
        width_mm = _mm_text(self._item.get("width_mm", 0.0))
        dimension_texts = {
            "top": length_mm,
            "bottom": length_mm,
            "left": width_mm,
            "right": width_mm,
        }
        band_labels = {
            "top": "SUP",
            "bottom": "INF",
            "left": "E\nS\nQ",
            "right": "D\nI\nR",
        }

        title_font = QtGui.QFont(painter.font())
        title_font.setBold(True)
        title_font.setPointSizeF(max(title_font.pointSizeF(), 9.8))
        painter.setFont(title_font)
        painter.setPen(text_color)
        title_rect = QtCore.QRectF(
            available_rect.left(),
            available_rect.top() - 2.0,
            available_rect.width(),
            24.0,
        )
        part_id = str(self._item.get("part_id", "") or "").strip()
        part_label = str(self._item.get("part_label", "") or "").strip()
        title_text = part_label if part_label else "Preview da peca"
        if part_id and part_id not in title_text:
            title_text = f"{part_id} | {title_text}"
        painter.drawText(title_rect, int(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter), title_text)

        side_font = QtGui.QFont(painter.font())
        side_font.setBold(False)
        side_font.setPointSizeF(max(side_font.pointSizeF() - 2.8, 7.0))
        vertical_side_font = QtGui.QFont(side_font)
        vertical_side_font.setBold(True)
        vertical_side_font.setPointSizeF(max(vertical_side_font.pointSizeF() - 0.4, 6.8))
        painter.setFont(side_font)
        for current_side_key, rect in self._rectangles.items():
            is_active = current_side_key in self._active_sides
            painter.setPen(QtGui.QPen(outline_color, 1.2))
            painter.setBrush(active_side_color if is_active else side_fill_color)
            painter.drawRoundedRect(rect, 4.0, 4.0)

            painter.setPen(active_text_color if is_active else text_color)
            side_text = band_labels[current_side_key]
            if current_side_key in {"left", "right"}:
                painter.save()
                painter.setFont(vertical_side_font)
                painter.drawText(
                    rect.adjusted(1.0, 6.0, -1.0, -6.0),
                    int(QtCore.Qt.AlignCenter),
                    side_text,
                )
                painter.restore()
            else:
                painter.drawText(rect.adjusted(2.0, 1.0, -2.0, -1.0), int(QtCore.Qt.AlignCenter), side_text)

        dimension_font = QtGui.QFont(side_font)
        dimension_font.setBold(False)
        dimension_font.setPointSizeF(max(dimension_font.pointSizeF() - 0.3, 6.8))
        painter.setFont(dimension_font)
        painter.setPen(hint_color)
        top_dimension_rect = QtCore.QRectF(
            board_rect.left() + 12.0,
            board_rect.top() + 6.0,
            board_rect.width() - 24.0,
            14.0,
        )
        painter.drawText(top_dimension_rect, int(QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop), f"{length_mm} mm")

        bottom_dimension_rect = QtCore.QRectF(
            board_rect.left() + 12.0,
            board_rect.bottom() - 20.0,
            board_rect.width() - 24.0,
            14.0,
        )
        painter.drawText(bottom_dimension_rect, int(QtCore.Qt.AlignHCenter | QtCore.Qt.AlignBottom), f"{length_mm} mm")

        vertical_dimension_length = max(20.0, board_rect.height() - 24.0)
        left_dimension_rect = QtCore.QRectF(
            -(vertical_dimension_length / 2.0),
            -7.0,
            vertical_dimension_length,
            14.0,
        )
        painter.save()
        painter.translate(board_rect.left() + 14.0, board_rect.center().y())
        painter.rotate(-90.0)
        painter.drawText(left_dimension_rect, int(QtCore.Qt.AlignCenter), f"{width_mm} mm")
        painter.restore()

        right_dimension_rect = QtCore.QRectF(
            -(vertical_dimension_length / 2.0),
            -7.0,
            vertical_dimension_length,
            14.0,
        )
        painter.save()
        painter.translate(board_rect.right() - 14.0, board_rect.center().y())
        painter.rotate(90.0)
        painter.drawText(right_dimension_rect, int(QtCore.Qt.AlignCenter), f"{width_mm} mm")
        painter.restore()

        hint_font = QtGui.QFont(painter.font())
        hint_font.setBold(False)
        hint_font.setPointSizeF(max(hint_font.pointSizeF() - 0.5, 9.0))
        painter.setFont(hint_font)
        painter.setPen(hint_color)
        hint_rect = QtCore.QRectF(
            available_rect.left(),
            available_rect.bottom() - 6.0,
            available_rect.width(),
            18.0,
        )
        hint_text = (
            "Clique nos lados para selecionar/desselecionar (multiplos permitidos)."
            if self._multi_select
            else "Clique na lateral desejada para corrigir o lado antes de aplicar."
        )
        painter.drawText(
            hint_rect,
            int(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter),
            hint_text,
        )
        painter.end()


class ApplyEdgeBandByFaceDialog(QtWidgets.QDialog):
    def __init__(self, resolution, parent=None):
        super().__init__(parent)
        import panelnest

        self._resolution = resolution
        self._resolved_items = [dict(item) for item in list(resolution.get("items", []) or [])]
        self._whole_objects = list(resolution.get("whole_objects", []) or [])
        self._whole_obj_index = 0  # índice do objeto exibido no preview multi-select
        self._dialog_size_key = "apply_edge_band_by_face"
        self.setWindowTitle("PanelNest: Aplicar Fita por Face")
        self.setModal(True)

        rejected_messages = list(resolution.get("rejected_messages", []) or [])
        self._whole_mode = bool(self._whole_objects)

        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setSpacing(6)

        # --- Controles comuns (ação + material) ---
        controls_layout = QtWidgets.QHBoxLayout()
        controls_layout.setSpacing(8)

        action_label = QtWidgets.QLabel("Acao:")
        self.action_combo = QtWidgets.QComboBox()
        self.action_combo.addItem("Marcar fita", "mark")
        self.action_combo.addItem("Remover fita", "remove")
        self.action_combo.addItem("Alternar fita", "toggle")
        controls_layout.addWidget(action_label)
        controls_layout.addWidget(self.action_combo)
        controls_layout.addSpacing(16)

        tape_label = QtWidgets.QLabel("Material da fita:")
        self.tape_material_combo = QtWidgets.QComboBox()
        self._tape_color_swatch = QtWidgets.QPushButton()
        self._tape_color_swatch.setFixedSize(28, 28)
        self._tape_color_swatch.setToolTip("Escolher cor da fita visualmente")
        self._tape_color_swatch.setCursor(QtCore.Qt.PointingHandCursor)
        self._tape_color_swatch.clicked.connect(self._open_tape_color_picker)
        self.tape_material_combo.addItem("— padrão (azul) —", "")
        try:
            from panelnest.materials import MATERIALS
            for m in MATERIALS:
                self.tape_material_combo.addItem(m.name, m.id)
        except Exception:
            pass
        self.tape_material_combo.currentIndexChanged.connect(self._update_tape_swatch)
        controls_layout.addWidget(tape_label)
        controls_layout.addWidget(self._tape_color_swatch)
        controls_layout.addWidget(self.tape_material_combo)
        controls_layout.addStretch(1)
        main_layout.addLayout(controls_layout)
        self._update_tape_swatch()

        if self._whole_mode:
            # --- Modo seleção interativa (objeto inteiro selecionado) ---
            # Navegação entre objetos (se múltiplos)
            nav_layout = QtWidgets.QHBoxLayout()
            nav_layout.setSpacing(6)
            if len(self._whole_objects) > 1:
                self._prev_btn = QtWidgets.QPushButton("<")
                self._prev_btn.setFixedWidth(32)
                self._next_btn = QtWidgets.QPushButton(">")
                self._next_btn.setFixedWidth(32)
                self._prev_btn.clicked.connect(self._on_prev_object)
                self._next_btn.clicked.connect(self._on_next_object)
                nav_layout.addWidget(self._prev_btn)
            else:
                self._prev_btn = None
                self._next_btn = None
            self._obj_label = QtWidgets.QLabel()
            nav_layout.addWidget(self._obj_label, 1)
            if len(self._whole_objects) > 1:
                nav_layout.addWidget(self._next_btn)
            main_layout.addLayout(nav_layout)

            self.table = None  # não há tabela no modo whole

        else:
            # --- Modo face individual (faces específicas selecionadas) ---
            resolved_items = self._resolved_items
            summary = QtWidgets.QLabel(
                f"{len(resolved_items)} lado(s) resolvido(s) em "
                f"{len({item['object_name'] for item in resolved_items})} peca(s). "
                f"{len(rejected_messages)} selecao(oes) ignorada(s)."
            )
            summary.setWordWrap(True)
            main_layout.addWidget(summary)

            self.table = QtWidgets.QTableWidget(len(resolved_items), 4)
            self.table.setAlternatingRowColors(True)
            self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
            self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
            self.table.setHorizontalHeaderLabels(["ID", "Rotulo", "Face", "Lado para aplicar"])
            for row_index, item in enumerate(resolved_items):
                tooltip = item.get("object_label", "")
                self.table.setItem(row_index, 0, _read_only_item(item.get("part_id", ""), tooltip))
                self.table.setItem(row_index, 1, _read_only_item(item.get("part_label", ""), tooltip))
                self.table.setItem(row_index, 2, _read_only_item(item.get("face_name", ""), tooltip))
                side_combo = QtWidgets.QComboBox()
                side_combo.addItem("Superior", "top")
                side_combo.addItem("Inferior", "bottom")
                side_combo.addItem("Esquerda", "left")
                side_combo.addItem("Direita", "right")
                current_index = side_combo.findData(item.get("side_key", ""))
                if current_index < 0:
                    current_index = 0
                side_combo.setCurrentIndex(current_index)
                side_combo.setToolTip(tooltip)
                self.table.setCellWidget(row_index, 3, side_combo)
            header = self.table.horizontalHeader()
            if hasattr(header, "setSectionResizeMode"):
                header.setSectionResizeMode(1, _stretch_mode())
            self.table.resizeColumnsToContents()
            main_layout.addWidget(self.table)

        # --- Preview ---
        if self._whole_mode:
            preview_label = QtWidgets.QLabel(
                "Clique nos lados para selecionar/desmarcar onde aplicar a fita:"
            )
        else:
            preview_label = QtWidgets.QLabel("Preview plano da peca")
            preview_label_font = preview_label.font()
            preview_label_font.setBold(True)
            preview_label.setFont(preview_label_font)
        main_layout.addWidget(preview_label)

        self.preview = EdgeBandPreviewWidget()
        self.preview.set_multi_select(self._whole_mode)
        if self._whole_mode:
            self._refresh_whole_preview()
        else:
            self.preview.on_side_clicked = self._set_current_side_from_preview
        main_layout.addWidget(self.preview)

        if rejected_messages:
            rejected_preview = "\n".join(f"- {message}" for message in rejected_messages[:4])
            if len(rejected_messages) > 4:
                rejected_preview += f"\n- mais {len(rejected_messages) - 4} item(ns) ignorado(s)"
            rejected_label = QtWidgets.QLabel("Itens ignorados na selecao:\n" + rejected_preview)
            rejected_label.setWordWrap(True)
            main_layout.addWidget(rejected_label)

        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        self._ok_btn = button_box.button(QtWidgets.QDialogButtonBox.Ok)
        if self._ok_btn is not None:
            self._ok_btn.setText("Aplicar")
            if self._whole_mode:
                self._ok_btn.setEnabled(False)  # habilita quando algum lado for selecionado
                self.preview.on_side_clicked = self._on_whole_side_clicked
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        main_layout.addWidget(button_box)
        self.finished.connect(self._persist_dialog_size)

        panelnest.restore_dialog_size(
            self,
            self._dialog_size_key,
            default_width=860,
            default_height=420 if self._whole_mode else 560,
            minimum_width=600,
            minimum_height=340 if self._whole_mode else 420,
        )

        if not self._whole_mode:
            self._connect_preview_updates()
            self._select_initial_row()

    # --- Modo whole-object helpers ---

    def _refresh_whole_preview(self):
        if not self._whole_objects:
            return
        idx = max(0, min(self._whole_obj_index, len(self._whole_objects) - 1))
        obj_data = self._whole_objects[idx]
        self.preview.set_item(obj_data)
        # não resetar active_sides ao navegar entre objetos
        if self._obj_label is not None:
            self._obj_label.setText(
                f"{idx + 1} / {len(self._whole_objects)}: {obj_data.get('part_label', obj_data.get('object_label', ''))}"
            )
        if self._prev_btn:
            self._prev_btn.setEnabled(idx > 0)
        if self._next_btn:
            self._next_btn.setEnabled(idx < len(self._whole_objects) - 1)

    def _on_prev_object(self):
        self._whole_obj_index = max(0, self._whole_obj_index - 1)
        self._refresh_whole_preview()

    def _on_next_object(self):
        self._whole_obj_index = min(len(self._whole_objects) - 1, self._whole_obj_index + 1)
        self._refresh_whole_preview()

    def _on_whole_side_clicked(self, _side_key):
        if self._ok_btn is not None:
            self._ok_btn.setEnabled(bool(self.preview.get_active_sides()))

    # --- Interface pública ---

    def selected_operation(self):
        current_data = self.action_combo.currentData()
        if current_data:
            return str(current_data)
        current_text = self.action_combo.currentText().strip().lower()
        if "remover" in current_text:
            return "remove"
        if "alternar" in current_text:
            return "toggle"
        return "mark"

    def resolved_items(self):
        from panelnest.constants import EDGE_BAND_SIDE_LABELS
        if self._whole_mode:
            active_sides = self.preview.get_active_sides()
            items = []
            for obj_data in self._whole_objects:
                for side_key in active_sides:
                    items.append(
                        {
                            "object_name": obj_data["object_name"],
                            "object_label": obj_data.get("object_label", ""),
                            "part_id": obj_data.get("part_id", ""),
                            "part_label": obj_data.get("part_label", ""),
                            "face_name": f"whole-{side_key}",
                            "length_mm": obj_data.get("length_mm", 0.0),
                            "width_mm": obj_data.get("width_mm", 0.0),
                            "occurrence_index": 1,
                            "occurrence_count": obj_data.get("occurrence_count", 1),
                            "side_key": side_key,
                            "side_label": EDGE_BAND_SIDE_LABELS.get(side_key, side_key),
                        }
                    )
            return items

        items = []
        for row_index, item in enumerate(self._resolved_items):
            resolved_item = dict(item)
            side_widget = self.table.cellWidget(row_index, 3)
            if side_widget is not None and hasattr(side_widget, "currentData"):
                side_key = side_widget.currentData()
                side_label = side_widget.currentText()
                if side_key:
                    resolved_item["side_key"] = str(side_key)
                resolved_item["side_label"] = str(side_label or resolved_item.get("side_label", ""))
            items.append(resolved_item)
        return items

    # --- Modo face individual helpers ---

    def _connect_preview_updates(self):
        if self.table is None:
            return
        if hasattr(self.table, "currentCellChanged"):
            self.table.currentCellChanged.connect(self._on_current_cell_changed)
        elif hasattr(self.table, "itemSelectionChanged"):
            self.table.itemSelectionChanged.connect(self._sync_preview_from_table)
        for row_index in range(self.table.rowCount()):
            side_widget = self.table.cellWidget(row_index, 3)
            if side_widget is not None and hasattr(side_widget, "currentIndexChanged"):
                side_widget.currentIndexChanged.connect(self._sync_preview_from_table)

    def _select_initial_row(self):
        if self.table is None or self.table.rowCount() <= 0:
            self.preview.set_item({})
            return
        self.table.setCurrentCell(0, 0)
        self.table.selectRow(0)
        self._sync_preview_from_table()

    def _current_row_index(self):
        if self.table is None:
            return -1
        row_index = self.table.currentRow()
        if row_index < 0 and self.table.rowCount() > 0:
            row_index = 0
        return row_index

    def _resolved_item_for_row(self, row_index):
        if row_index < 0 or row_index >= len(self._resolved_items):
            return {}
        item = dict(self._resolved_items[row_index])
        side_widget = self.table.cellWidget(row_index, 3) if self.table else None
        if side_widget is not None and hasattr(side_widget, "currentData"):
            side_key = side_widget.currentData()
            side_label = side_widget.currentText()
            if side_key:
                item["side_key"] = str(side_key)
            if side_label:
                item["side_label"] = str(side_label)
        return item

    def _sync_preview_from_table(self, *_args):
        self.preview.set_item(self._resolved_item_for_row(self._current_row_index()))

    def _on_current_cell_changed(self, current_row, _current_column, _previous_row, _previous_column):
        if current_row >= 0:
            self.table.selectRow(current_row)
        self._sync_preview_from_table()

    def _set_current_side_from_preview(self, side_key):
        row_index = self._current_row_index()
        if row_index < 0:
            return
        side_widget = self.table.cellWidget(row_index, 3) if self.table else None
        if side_widget is None or not hasattr(side_widget, "findData"):
            return
        target_index = side_widget.findData(side_key)
        if target_index >= 0:
            side_widget.setCurrentIndex(target_index)
        self._sync_preview_from_table()

    def _update_tape_swatch(self, *_args):
        mat_id = self.tape_material_combo.currentData()
        if mat_id:
            try:
                from panelnest.materials import get_by_id
                mat = get_by_id(mat_id)
                if mat:
                    r, g, b = [int(c * 255) for c in mat.color]
                    self._tape_color_swatch.setStyleSheet(
                        f"QPushButton{{background:rgb({r},{g},{b});border:1px solid rgba(0,0,0,60);border-radius:4px;}}"
                    )
                    return
            except Exception:
                pass
        # padrão: azul ciano da fita
        self._tape_color_swatch.setStyleSheet(
            "QPushButton{background:rgb(59,201,214);border:1px solid rgba(0,0,0,60);border-radius:4px;}"
        )

    def _open_tape_color_picker(self):
        try:
            from commands.apply_material import ApplyMaterialDialog
        except ImportError:
            from apply_material import ApplyMaterialDialog
        dlg = ApplyMaterialDialog(
            parent=self,
            title="PanelNest — Material da Fita de Borda",
            ok_label="Selecionar",
        )
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return
        mat = dlg.selected_material()
        if mat is None:
            return
        idx = self.tape_material_combo.findData(mat.id)
        if idx >= 0:
            self.tape_material_combo.setCurrentIndex(idx)

    def _persist_dialog_size(self, _result):
        import panelnest

        panelnest.save_dialog_size(self, self._dialog_size_key)


class ApplyEdgeBandByFaceCommand:
    def Activated(self):
        import panelnest

        try:
            resolution = panelnest.resolve_edge_band_face_selection()
        except ValueError as exc:
            if App is not None:
                App.Console.PrintError(f"PanelNest: {exc}\n")
            _show_error_dialog(str(exc))
            return

        dialog = ApplyEdgeBandByFaceDialog(
            resolution,
            parent=_main_window(),
        )
        if _exec_dialog(dialog) != QtWidgets.QDialog.Accepted:
            return

        try:
            selected_items = dialog.resolved_items()

            # Salvar material da fita POR LADO antes do apply
            tape_mat_id = dialog.tape_material_combo.currentData()
            if tape_mat_id and App is not None and App.ActiveDocument is not None:
                try:
                    import json as _json
                    from panelnest.materials import get_by_id
                    tape_mat = get_by_id(tape_mat_id)
                    if tape_mat:
                        # Agrupar lados por objeto
                        sides_by_obj: dict = {}
                        for item in selected_items:
                            sides_by_obj.setdefault(item["object_name"], set()).add(item["side_key"])
                        for obj_name, sides in sides_by_obj.items():
                            o = App.ActiveDocument.getObject(obj_name)
                            if o is None:
                                continue
                            # Ler JSON existente e atualizar só os lados desta operação
                            existing = {}
                            if hasattr(o, "PanelNestEdgeBandMaterialJson"):
                                raw = str(getattr(o, "PanelNestEdgeBandMaterialJson", "") or "").strip()
                                if raw:
                                    try:
                                        existing = _json.loads(raw)
                                    except Exception:
                                        existing = {}
                            operation = dialog.selected_operation()
                            for side_key in sides:
                                if operation == "remove":
                                    existing.pop(side_key, None)
                                else:
                                    existing[side_key] = tape_mat.name
                            if hasattr(o, "PanelNestEdgeBandMaterialJson"):
                                o.PanelNestEdgeBandMaterialJson = _json.dumps(existing)
                except Exception:
                    pass

            updated_parts = panelnest.apply_edge_band_face_selection(
                selected_items,
                operation=dialog.selected_operation(),
            )
            all_source_objects = []
            if App is not None and App.ActiveDocument is not None:
                all_source_objects = panelnest.get_part_source_objects(objects=App.ActiveDocument.Objects)
            all_parts = panelnest.collect_parts(all_source_objects or None)
            refreshed_sheets = panelnest.refresh_part_reporting_sheets(parts=all_parts)
        except ValueError as exc:
            if App is not None:
                App.Console.PrintError(f"PanelNest: {exc}\n")
            _show_error_dialog(str(exc))
            return

        if App is not None:
            operation_labels = {
                "mark": "marcou",
                "remove": "removeu",
                "toggle": "alternou",
            }
            operation_key = dialog.selected_operation()
            message = (
                f"PanelNest: {operation_labels.get(operation_key, 'atualizou')} "
                f"{len(selected_items)} lado(s) em {len(updated_parts)} peca(s)."
            )
            edge_band_sheet = refreshed_sheets.get("edge_band")
            if edge_band_sheet is not None:
                message += f" Acabamento em '{edge_band_sheet.Label}'."
            App.Console.PrintMessage(f"{message}\n")

        if Gui is not None and App is not None and App.ActiveDocument is not None:
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
                os.path.dirname(__file__), "..", "resources", "icons", "panelnest_edge_band.svg"
            ),
            "Accel": "",
            "MenuText": "Aplicar Fita por Face",
            "ToolTip": "Resolve faces laterais selecionadas para os lados da peca e marca, remove ou alterna a fita de borda.",
        }


if Gui is not None:
    Gui.addCommand(COMMAND_NAME, ApplyEdgeBandByFaceCommand())
