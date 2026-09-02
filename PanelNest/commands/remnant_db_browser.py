"""
Comando: Banco de Retalhos
Dialog para visualizar, filtrar, adicionar e importar retalhos do banco SQLite persistente.
"""
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

import panelnest

COMMAND_NAME = "PanelNest_RemnantDB"

_COLUMNS = ["ID", "Label", "Comp. (mm)", "Larg. (mm)", "Esp. (mm)", "Material", "Projeto", "Chapa", "Criado em", "Notas"]
_COL_ID, _COL_LABEL, _COL_LENGTH, _COL_WIDTH, _COL_THICK, _COL_MAT, _COL_PROJ, _COL_SHEET, _COL_DATE, _COL_NOTES = range(len(_COLUMNS))


def _exec_dialog(dialog):
    if hasattr(dialog, "exec_"):
        return dialog.exec_()
    return dialog.exec()


class _AddRemnantDialog(QtWidgets.QDialog):
    """Dialog para adicionar um retalho manualmente."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Adicionar Retalho Manualmente")
        self.setMinimumWidth(380)
        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QFormLayout(self)
        layout.setSpacing(8)

        self._label_edit = QtWidgets.QLineEdit()
        self._label_edit.setPlaceholderText("ex: MDF 18mm Branco")
        layout.addRow("Label:", self._label_edit)

        self._length_spin = QtWidgets.QDoubleSpinBox()
        self._length_spin.setRange(1, 9999)
        self._length_spin.setDecimals(0)
        self._length_spin.setSuffix(" mm")
        self._length_spin.setValue(1200)
        layout.addRow("Comprimento:", self._length_spin)

        self._width_spin = QtWidgets.QDoubleSpinBox()
        self._width_spin.setRange(1, 9999)
        self._width_spin.setDecimals(0)
        self._width_spin.setSuffix(" mm")
        self._width_spin.setValue(600)
        layout.addRow("Largura:", self._width_spin)

        self._thick_spin = QtWidgets.QDoubleSpinBox()
        self._thick_spin.setRange(0, 100)
        self._thick_spin.setDecimals(1)
        self._thick_spin.setSuffix(" mm")
        self._thick_spin.setValue(18.0)
        layout.addRow("Espessura:", self._thick_spin)

        self._material_edit = QtWidgets.QLineEdit()
        self._material_edit.setPlaceholderText("ex: MDF, MDP, Compensado")
        layout.addRow("Material:", self._material_edit)

        self._notes_edit = QtWidgets.QLineEdit()
        self._notes_edit.setPlaceholderText("Observações opcionais")
        layout.addRow("Notas:", self._notes_edit)

        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        btns.accepted.connect(self._validate_and_accept)
        btns.rejected.connect(self.reject)
        layout.addRow(btns)

    def _validate_and_accept(self):
        if not self._label_edit.text().strip():
            QtWidgets.QMessageBox.warning(self, "Campo obrigatório", "Informe um label para o retalho.")
            return
        self.accept()

    def values(self):
        return {
            "label": self._label_edit.text().strip(),
            "length_mm": self._length_spin.value(),
            "width_mm": self._width_spin.value(),
            "thickness_mm": self._thick_spin.value(),
            "material": self._material_edit.text().strip(),
            "notes": self._notes_edit.text().strip(),
        }


class RemnantDBDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Banco de Retalhos — PanelNest")
        self.resize(960, 560)
        self._conn = None
        self._rows = []
        self._setup_ui()
        self._load_data()

    def _setup_ui(self):
        layout = QtWidgets.QVBoxLayout(self)

        # Caminho do banco
        db_path = panelnest.remnant_db_path()
        path_label = QtWidgets.QLabel(f"Banco: {db_path}")
        path_label.setStyleSheet("color: gray; font-size: 10px;")
        layout.addWidget(path_label)

        # Filtros
        filter_group = QtWidgets.QGroupBox("Filtros")
        filter_layout = QtWidgets.QHBoxLayout(filter_group)

        filter_layout.addWidget(QtWidgets.QLabel("Material:"))
        self._material_edit = QtWidgets.QLineEdit()
        self._material_edit.setPlaceholderText("ex: MDF")
        self._material_edit.setMaximumWidth(120)
        self._material_edit.returnPressed.connect(self._load_data)
        filter_layout.addWidget(self._material_edit)

        filter_layout.addWidget(QtWidgets.QLabel("Espessura (mm):"))
        self._thickness_spin = QtWidgets.QDoubleSpinBox()
        self._thickness_spin.setRange(0, 100)
        self._thickness_spin.setDecimals(1)
        self._thickness_spin.setSpecialValueText("Qualquer")
        self._thickness_spin.setValue(0)
        self._thickness_spin.setMaximumWidth(80)
        filter_layout.addWidget(self._thickness_spin)

        filter_layout.addWidget(QtWidgets.QLabel("Área mínima (cm²):"))
        self._min_area_spin = QtWidgets.QDoubleSpinBox()
        self._min_area_spin.setRange(0, 100000)
        self._min_area_spin.setDecimals(0)
        self._min_area_spin.setValue(0)
        self._min_area_spin.setMaximumWidth(90)
        filter_layout.addWidget(self._min_area_spin)

        self._show_used_check = QtWidgets.QCheckBox("Mostrar usados")
        filter_layout.addWidget(self._show_used_check)

        self._search_btn = QtWidgets.QPushButton("Buscar")
        self._search_btn.clicked.connect(self._load_data)
        filter_layout.addWidget(self._search_btn)
        filter_layout.addStretch()
        layout.addWidget(filter_group)

        # Tabela
        self._table = QtWidgets.QTableWidget()
        self._table.setColumnCount(len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(True)
        # Esconder coluna ID (usada internamente)
        self._table.setColumnHidden(_COL_ID, True)
        # Ajustar larguras iniciais
        for col, width in [(_COL_LABEL, 160), (_COL_LENGTH, 80), (_COL_WIDTH, 80),
                            (_COL_THICK, 70), (_COL_MAT, 90), (_COL_PROJ, 120),
                            (_COL_SHEET, 100), (_COL_DATE, 100), (_COL_NOTES, 140)]:
            self._table.setColumnWidth(col, width)
        layout.addWidget(self._table)

        # Resumo
        self._summary_label = QtWidgets.QLabel("")
        self._summary_label.setStyleSheet("color: gray;")
        layout.addWidget(self._summary_label)

        # Botões
        btn_layout = QtWidgets.QHBoxLayout()

        self._add_btn = QtWidgets.QPushButton("+ Adicionar Retalho")
        self._add_btn.setToolTip("Cadastrar uma sobra manualmente no banco")
        self._add_btn.clicked.connect(self._add_manual)
        btn_layout.addWidget(self._add_btn)

        self._import_btn = QtWidgets.QPushButton("Importar Selecionados como Estoque")
        self._import_btn.setEnabled(False)
        self._import_btn.setToolTip("Adiciona os retalhos selecionados ao estoque do documento atual")
        self._import_btn.clicked.connect(self._import_selected)
        btn_layout.addWidget(self._import_btn)

        self._delete_btn = QtWidgets.QPushButton("Remover Selecionados")
        self._delete_btn.setEnabled(False)
        self._delete_btn.clicked.connect(self._delete_selected)
        btn_layout.addWidget(self._delete_btn)

        btn_layout.addStretch()

        self._purge_btn = QtWidgets.QPushButton("Purgar Usados (>180 dias)")
        self._purge_btn.setToolTip("Remove permanentemente retalhos marcados como usados há mais de 180 dias")
        self._purge_btn.clicked.connect(self._purge_old)
        btn_layout.addWidget(self._purge_btn)

        close_btn = QtWidgets.QPushButton("Fechar")
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)

        layout.addLayout(btn_layout)

        self._table.itemSelectionChanged.connect(self._on_selection_changed)

    def _get_conn(self):
        if self._conn is None:
            self._conn = panelnest.open_remnant_db()
        return self._conn

    def _load_data(self):
        material = self._material_edit.text().strip() or None
        thickness = self._thickness_spin.value() if self._thickness_spin.value() > 0 else None
        min_area_cm2 = self._min_area_spin.value()
        min_area_mm2 = min_area_cm2 * 100.0

        conn = self._get_conn()

        if self._show_used_check.isChecked():
            # Busca todos (incluindo usados) — consulta direta
            query = """
                SELECT id, label, length_mm, width_mm, thickness_mm, material,
                       source_project, source_sheet, created_at, notes, used_at
                FROM remnants
                WHERE (length_mm * width_mm) >= ?
            """
            params = [max(0.0, min_area_mm2)]
            if material:
                query += " AND LOWER(material) LIKE ?"
                params.append(f"%{material.lower()}%")
            if thickness is not None:
                query += " AND ABS(thickness_mm - ?) <= 0.75"
                params.append(float(thickness))
            query += " ORDER BY (length_mm * width_mm) DESC"
            rows = conn.execute(query, params).fetchall()
            self._rows = [dict(r) for r in rows]
        else:
            self._rows = panelnest.load_remnants(
                conn,
                material=material,
                min_area_mm2=min_area_mm2,
                thickness_mm=thickness,
            )
            for r in self._rows:
                r.setdefault("used_at", None)

        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(self._rows))
        for r, row in enumerate(self._rows):
            date_str = str(row.get("created_at") or "")[:10]
            vals = [
                str(row["id"]),
                row["label"],
                f"{row['length_mm']:.0f}",
                f"{row['width_mm']:.0f}",
                f"{row['thickness_mm']:.1f}",
                row["material"],
                row["source_project"],
                row["source_sheet"],
                date_str,
                row.get("notes") or "",
            ]
            is_used = bool(row.get("used_at"))
            for c, val in enumerate(vals):
                item = QtWidgets.QTableWidgetItem(val)
                item.setData(QtCore.Qt.UserRole, row["id"])
                if is_used:
                    item.setForeground(QtGui.QColor("#aaaaaa"))
                self._table.setItem(r, c, item)

        self._table.setSortingEnabled(True)

        total = panelnest.count_available_remnants(conn)
        self._summary_label.setText(
            f"{len(self._rows)} retalho(s) exibido(s) | {total} disponível(is) no banco"
        )

    def _on_selection_changed(self):
        has_sel = bool(self._table.selectionModel().selectedRows())
        self._import_btn.setEnabled(has_sel)
        self._delete_btn.setEnabled(has_sel)

    def _selected_ids(self):
        ids = []
        seen = set()
        for item in self._table.selectedItems():
            row_id = item.data(QtCore.Qt.UserRole)
            if row_id not in seen:
                seen.add(row_id)
                ids.append(row_id)
        return ids

    def _add_manual(self):
        dlg = _AddRemnantDialog(self)
        if _exec_dialog(dlg) != QtWidgets.QDialog.Accepted:
            return
        vals = dlg.values()

        class _FakeRemnant:
            pass

        r = _FakeRemnant()
        r.label = vals["label"]
        r.length_mm = vals["length_mm"]
        r.width_mm = vals["width_mm"]
        r.thickness_mm = vals["thickness_mm"]
        r.material = vals["material"]
        r.source_label = "manual"

        conn = self._get_conn()
        rowid = panelnest.save_remnant(conn, r, project_name="manual")

        # Salvar notas se informadas
        if vals["notes"]:
            conn.execute("UPDATE remnants SET notes = ? WHERE id = ?", (vals["notes"], rowid))
            conn.commit()

        self._load_data()
        QtWidgets.QMessageBox.information(
            self, "Retalho adicionado",
            f"Retalho '{vals['label']}' ({vals['length_mm']:.0f} x {vals['width_mm']:.0f} mm) "
            f"adicionado ao banco com sucesso."
        )

    def _import_selected(self):
        ids = self._selected_ids()
        if not ids:
            return

        id_map = {row["id"]: row for row in self._rows}
        settings = panelnest.get_sheet_settings()
        new_remnants = list(settings.remnants)

        imported_count = 0
        conn = self._get_conn()
        for rowid in ids:
            row = id_map.get(rowid)
            if row is None:
                continue
            if row.get("used_at"):
                continue  # Não reimportar já usados
            from panelnest.models import SheetStockPiece
            piece = SheetStockPiece(
                label=row["label"],
                length_mm=float(row["length_mm"]),
                width_mm=float(row["width_mm"]),
                thickness_mm=float(row["thickness_mm"]),
                material=row["material"],
                quantity=1,
                kind="Retalho",
            )
            new_remnants.append(piece)
            panelnest.mark_remnant_used(conn, rowid)
            imported_count += 1

        if imported_count == 0:
            QtWidgets.QMessageBox.warning(self, "Nada importado", "Nenhum retalho disponível selecionado.")
            return

        panelnest.update_sheet_settings(remnants=new_remnants)

        QtWidgets.QMessageBox.information(
            self,
            "Importação concluída",
            f"{imported_count} retalho(s) adicionado(s) ao estoque do documento.\n"
            "Acesse 'Configurar Chapa' para verificar.",
        )
        self._load_data()

    def _delete_selected(self):
        ids = self._selected_ids()
        if not ids:
            return
        reply = QtWidgets.QMessageBox.question(
            self,
            "Confirmar remoção",
            f"Remover permanentemente {len(ids)} retalho(s) do banco?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if reply != QtWidgets.QMessageBox.Yes:
            return
        conn = self._get_conn()
        for rowid in ids:
            panelnest.delete_remnant(conn, rowid)
        self._load_data()

    def _purge_old(self):
        conn = self._get_conn()
        count = panelnest.purge_old_remnants(conn, days=180)
        QtWidgets.QMessageBox.information(
            self,
            "Purga concluída",
            f"{count} retalho(s) antigo(s) removido(s) permanentemente.",
        )
        self._load_data()

    def closeEvent(self, event):
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
        super().closeEvent(event)


class PanelNestRemnantDB:
    def GetResources(self):
        icon_path = panelnest.get_icon_path("panelnest_remnant_db.svg")
        return {
            "Pixmap": icon_path,
            "MenuText": "Banco de Retalhos",
            "ToolTip": "Visualizar, adicionar e importar retalhos do banco persistente (SQLite)",
        }

    def IsActive(self):
        return App is not None

    def Activated(self):
        parent = Gui.getMainWindow() if Gui is not None else None
        dialog = RemnantDBDialog(parent)
        _exec_dialog(dialog)


if Gui is not None:
    Gui.addCommand(COMMAND_NAME, PanelNestRemnantDB())
