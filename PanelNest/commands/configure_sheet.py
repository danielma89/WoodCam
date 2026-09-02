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


COMMAND_NAME = "PanelNest_ConfigureSheet"


def _exec_dialog(dialog):
    if hasattr(dialog, "exec_"):
        return dialog.exec_()
    return dialog.exec()


def _stretch_mode():
    if hasattr(QtWidgets.QHeaderView, "Stretch"):
        return QtWidgets.QHeaderView.Stretch
    return QtWidgets.QHeaderView.ResizeMode.Stretch


class _RemnantPickerDialog(QtWidgets.QDialog):
    """Dialog para selecionar retalhos do banco histórico com checkboxes."""

    def __init__(self, rows, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Histórico de Retalhos — selecione para adicionar")
        self.resize(860, 480)
        self._rows = rows
        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)

        info = QtWidgets.QLabel(
            f"<b>{len(self._rows)}</b> retalho(s) disponível(is) no histórico. "
            "Marque os que deseja adicionar ao estoque deste projeto."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        # Filtro rápido
        filter_row = QtWidgets.QHBoxLayout()
        filter_row.addWidget(QtWidgets.QLabel("Filtrar:"))
        self._filter_edit = QtWidgets.QLineEdit()
        self._filter_edit.setPlaceholderText("material, label ou dimensão...")
        self._filter_edit.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self._filter_edit)
        filter_row.addStretch()
        sel_all_btn = QtWidgets.QPushButton("Marcar todos")
        sel_all_btn.clicked.connect(self._select_all)
        sel_none_btn = QtWidgets.QPushButton("Desmarcar todos")
        sel_none_btn.clicked.connect(self._select_none)
        filter_row.addWidget(sel_all_btn)
        filter_row.addWidget(sel_none_btn)
        layout.addLayout(filter_row)

        # Tabela com checkbox na primeira coluna
        self._table = QtWidgets.QTableWidget()
        self._table.setColumnCount(7)
        self._table.setHorizontalHeaderLabels(
            ["", "Descricao", "Comp. (mm)", "Larg. (mm)", "Esp. (mm)", "Material", "Projeto"]
        )
        self._table.setColumnWidth(0, 32)
        self._table.setColumnWidth(1, 200)
        self._table.setColumnWidth(2, 80)
        self._table.setColumnWidth(3, 80)
        self._table.setColumnWidth(4, 70)
        self._table.setColumnWidth(5, 100)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self._table.setAlternatingRowColors(True)
        layout.addWidget(self._table)

        self._populate_table(self._rows)

        self._count_label = QtWidgets.QLabel("")
        self._count_label.setStyleSheet("color: gray;")
        layout.addWidget(self._count_label)
        self._update_count()

        bottom_row = QtWidgets.QHBoxLayout()
        del_btn = QtWidgets.QPushButton("🗑 Apagar marcados do banco")
        del_btn.setToolTip("Remove permanentemente os retalhos marcados do banco histórico")
        del_btn.clicked.connect(self._delete_checked)
        bottom_row.addWidget(del_btn)
        bottom_row.addStretch()
        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        btns.button(QtWidgets.QDialogButtonBox.Ok).setText("Adicionar Selecionados ao Projeto")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        bottom_row.addWidget(btns)
        layout.addLayout(bottom_row)

        # Tecla Delete apaga do banco
        shortcut = QtWidgets.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key_Delete), self)
        shortcut.activated.connect(self._delete_checked)

    def _populate_table(self, rows):
        self._table.setRowCount(0)
        for row in rows:
            r = self._table.rowCount()
            self._table.insertRow(r)

            chk = QtWidgets.QTableWidgetItem()
            chk.setCheckState(QtCore.Qt.Unchecked)
            chk.setData(QtCore.Qt.UserRole, row)
            self._table.setItem(r, 0, chk)

            area_cm2 = row["length_mm"] * row["width_mm"] / 100.0
            self._table.setItem(r, 1, QtWidgets.QTableWidgetItem(row["label"]))
            self._table.setItem(r, 2, QtWidgets.QTableWidgetItem(f"{row['length_mm']:.0f}"))
            self._table.setItem(r, 3, QtWidgets.QTableWidgetItem(f"{row['width_mm']:.0f}"))
            self._table.setItem(r, 4, QtWidgets.QTableWidgetItem(f"{row['thickness_mm']:.1f}"))
            self._table.setItem(r, 5, QtWidgets.QTableWidgetItem(row["material"]))
            self._table.setItem(r, 6, QtWidgets.QTableWidgetItem(row.get("source_project") or ""))

        self._table.itemChanged.connect(self._update_count)

    def _apply_filter(self, text):
        text = text.strip().lower()
        self._table.itemChanged.disconnect(self._update_count)
        filtered = [
            row for row in self._rows
            if not text
            or text in row["label"].lower()
            or text in row["material"].lower()
            or text in str(int(row["length_mm"]))
            or text in str(int(row["width_mm"]))
            or text in (row.get("source_project") or "").lower()
        ]
        self._populate_table(filtered)
        self._update_count()

    def _select_all(self):
        self._table.itemChanged.disconnect(self._update_count)
        for r in range(self._table.rowCount()):
            self._table.item(r, 0).setCheckState(QtCore.Qt.Checked)
        self._table.itemChanged.connect(self._update_count)
        self._update_count()

    def _select_none(self):
        self._table.itemChanged.disconnect(self._update_count)
        for r in range(self._table.rowCount()):
            self._table.item(r, 0).setCheckState(QtCore.Qt.Unchecked)
        self._table.itemChanged.connect(self._update_count)
        self._update_count()

    def _update_count(self):
        count = sum(
            1 for r in range(self._table.rowCount())
            if self._table.item(r, 0) and self._table.item(r, 0).checkState() == QtCore.Qt.Checked
        )
        self._count_label.setText(f"{count} selecionado(s) de {self._table.rowCount()} exibido(s)")

    def _delete_checked(self):
        to_delete = []
        for r in range(self._table.rowCount()):
            item = self._table.item(r, 0)
            if item and item.checkState() == QtCore.Qt.Checked:
                to_delete.append(item.data(QtCore.Qt.UserRole))
        if not to_delete:
            QtWidgets.QMessageBox.information(self, "Nenhum marcado", "Marque os retalhos que deseja apagar.")
            return
        reply = QtWidgets.QMessageBox.question(
            self, "Confirmar exclusão",
            f"Apagar permanentemente {len(to_delete)} retalho(s) do banco histórico?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if reply != QtWidgets.QMessageBox.Yes:
            return
        import panelnest as _pn
        try:
            conn = _pn.open_remnant_db()
            for row in to_delete:
                _pn.delete_remnant(conn, row["id"])
            conn.close()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Erro", f"Não foi possível apagar: {exc}")
            return
        # Recarregar do banco
        try:
            conn = _pn.open_remnant_db()
            self._rows = _pn.load_remnants(conn)
            conn.close()
        except Exception:
            self._rows = []
        self._populate_table(self._rows)
        self._update_count()

    def selected_rows(self):
        result = []
        for r in range(self._table.rowCount()):
            item = self._table.item(r, 0)
            if item and item.checkState() == QtCore.Qt.Checked:
                result.append(item.data(QtCore.Qt.UserRole))
        return result


class SheetSettingsDialog(QtWidgets.QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        import panelnest

        cloudflare_publish_prefs = panelnest.get_cloudflare_publish_preferences()
        resolved_project_name = panelnest.suggest_cloudflare_pages_project_name(settings)
        explicit_public_base_url = panelnest.normalize_assembly_guide_public_base_url(
            settings.assembly_guide_public_base_url or ""
        )
        resolved_public_base_url = explicit_public_base_url or ""
        self._dialog_size_key = "configure_sheet_v2"
        self._assembly_guide_public_base_url_custom = bool(explicit_public_base_url)
        self._assembly_guide_last_derived_public_base_url = ""
        self.setWindowTitle("PanelNest: Configurar Chapa")
        self.setModal(True)
        if hasattr(self, "setSizeGripEnabled"):
            self.setSizeGripEnabled(True)

        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(6)

        tabs = QtWidgets.QTabWidget()
        if hasattr(tabs, "setDocumentMode"):
            tabs.setDocumentMode(True)
        main_layout.addWidget(tabs)

        general_tab = QtWidgets.QWidget()
        general_layout = QtWidgets.QVBoxLayout(general_tab)
        general_layout.setContentsMargins(8, 8, 8, 8)
        general_layout.setSpacing(6)
        form_layout = QtWidgets.QFormLayout()
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setHorizontalSpacing(10)
        form_layout.setVerticalSpacing(8)

        self.length_spin = self._create_spinbox(settings.length_mm, 100.0, 10000.0)
        self.width_spin = self._create_spinbox(settings.width_mm, 100.0, 10000.0)
        self.margin_spin = self._create_spinbox(settings.margin_mm, 0.0, 500.0)
        self.spacing_spin = self._create_spinbox(settings.spacing_mm, 0.0, 500.0)
        self.full_sheet_count_spin = QtWidgets.QSpinBox()
        self.full_sheet_count_spin.setRange(0, 999)
        self.full_sheet_count_spin.setValue(settings.full_sheet_count)
        self.full_sheet_thickness_spin = self._create_spinbox(settings.full_sheet_thickness_mm, 0.0, 100.0)
        self.full_sheet_thickness_spin.setSpecialValueText("Generica")
        self.full_sheet_material_edit = QtWidgets.QLineEdit(settings.full_sheet_material or "")
        self.full_sheet_cost_spin = QtWidgets.QDoubleSpinBox()
        self.full_sheet_cost_spin.setDecimals(2)
        self.full_sheet_cost_spin.setRange(0.0, 99999.0)
        self.full_sheet_cost_spin.setValue(float(getattr(settings, "full_sheet_cost", 0.0) or 0.0))
        self.full_sheet_cost_spin.setPrefix("R$ ")
        self.full_sheet_cost_spin.setSpecialValueText("Sem custo")
        self.edge_band_thickness_spin = QtWidgets.QDoubleSpinBox()
        self.edge_band_thickness_spin.setDecimals(2)
        self.edge_band_thickness_spin.setRange(0.0, 5.0)
        self.edge_band_thickness_spin.setSingleStep(0.1)
        self.edge_band_thickness_spin.setValue(float(getattr(settings, "edge_band_thickness_mm", 0.0) or 0.0))
        self.edge_band_thickness_spin.setSuffix(" mm")
        self.edge_band_thickness_spin.setSpecialValueText("Sem compensacao")
        self.allow_extra_full_sheets_checkbox = QtWidgets.QCheckBox(
            "Permitir abrir novas chapas inteiras automaticamente\n"
            "(desmarque para usar apenas os retalhos configurados)"
        )
        self.allow_extra_full_sheets_checkbox.setChecked(settings.allow_extra_full_sheets)
        self.full_sheet_count_spin.valueChanged.connect(self._on_full_sheet_count_changed)
        self._set_compact_field_width(self.length_spin, 260, 320)
        self._set_compact_field_width(self.width_spin, 260, 320)
        self._set_compact_field_width(self.margin_spin, 220, 280)
        self._set_compact_field_width(self.spacing_spin, 220, 280)
        self._set_compact_field_width(self.full_sheet_count_spin, 180, 220)
        self._set_compact_field_width(self.full_sheet_thickness_spin, 220, 280)
        self._set_compact_field_width(self.full_sheet_material_edit, 280, 360)

        form_layout.addRow("Comprimento da chapa (mm)", self.length_spin)
        form_layout.addRow("Largura da chapa (mm)", self.width_spin)
        form_layout.addRow("Margem base da chapa (mm)", self.margin_spin)
        form_layout.addRow("Espacamento base entre pecas (mm)", self.spacing_spin)
        form_layout.addRow("Chapas inteiras disponiveis", self.full_sheet_count_spin)
        form_layout.addRow("Espessura das chapas inteiras", self.full_sheet_thickness_spin)
        form_layout.addRow("Material das chapas inteiras", self.full_sheet_material_edit)
        form_layout.addRow("Custo por chapa inteira (R$)", self.full_sheet_cost_spin)
        form_layout.addRow("Espessura da fita de borda (mm)", self.edge_band_thickness_spin)
        form_layout.addRow("", self.allow_extra_full_sheets_checkbox)
        general_layout.addLayout(form_layout)
        tabs.addTab(general_tab, "Chapa")

        process_tab = QtWidgets.QWidget()
        process_tab_layout = QtWidgets.QVBoxLayout(process_tab)
        process_tab_layout.setContentsMargins(8, 8, 8, 8)
        process_tab_layout.setSpacing(6)
        process_layout = QtWidgets.QFormLayout()
        process_layout.setContentsMargins(0, 0, 0, 0)
        process_layout.setHorizontalSpacing(10)
        process_layout.setVerticalSpacing(8)
        self.cnc_kerf_spin = self._create_spinbox(settings.cnc_kerf_mm, 0.0, 50.0)
        self.cnc_margin_spin = self._create_spinbox(settings.cnc_tech_margin_mm, 0.0, 500.0)
        self.saw_kerf_spin = self._create_spinbox(settings.saw_kerf_mm, 0.0, 50.0)
        self.saw_margin_spin = self._create_spinbox(settings.saw_tech_margin_mm, 0.0, 500.0)
        self.cnc_strategy_combo = QtWidgets.QComboBox()
        for option in panelnest.CNC_LAYOUT_STRATEGY_OPTIONS:
            self.cnc_strategy_combo.addItem(option)
        cnc_strategy_index = self.cnc_strategy_combo.findText(settings.cnc_layout_strategy)
        if cnc_strategy_index >= 0:
            self.cnc_strategy_combo.setCurrentIndex(cnc_strategy_index)
        self.cnc_nesting_mode_combo = QtWidgets.QComboBox()
        for option in panelnest.CNC_NESTING_MODE_OPTIONS:
            self.cnc_nesting_mode_combo.addItem(option)
        cnc_nesting_mode_index = self.cnc_nesting_mode_combo.findText(settings.cnc_nesting_mode)
        if cnc_nesting_mode_index >= 0:
            self.cnc_nesting_mode_combo.setCurrentIndex(cnc_nesting_mode_index)
        self.cnc_nesting_mode_combo.setToolTip(
            "Rápido usa os retângulos envolventes. Otimizado tenta reencaixar os "
            "contornos reais e mantém o layout rápido se não houver ganho seguro."
        )
        self.saw_strategy_combo = QtWidgets.QComboBox()
        for option in panelnest.SAW_LAYOUT_STRATEGY_OPTIONS:
            self.saw_strategy_combo.addItem(option)
        saw_strategy_index = self.saw_strategy_combo.findText(settings.saw_layout_strategy)
        if saw_strategy_index >= 0:
            self.saw_strategy_combo.setCurrentIndex(saw_strategy_index)
        self._set_compact_field_width(self.cnc_kerf_spin, 220, 260)
        self._set_compact_field_width(self.cnc_margin_spin, 220, 260)
        self._set_compact_field_width(self.saw_kerf_spin, 220, 260)
        self._set_compact_field_width(self.saw_margin_spin, 220, 260)
        self._set_compact_field_width(self.cnc_strategy_combo, 260, 320)
        self._set_compact_field_width(self.cnc_nesting_mode_combo, 260, 320)
        self._set_compact_field_width(self.saw_strategy_combo, 260, 320)
        process_layout.addRow("Diametro da fresa (mm)", self.cnc_kerf_spin)
        process_layout.addRow("Acrescimo de borda CNC (mm)", self.cnc_margin_spin)
        process_layout.addRow("Estrategia CNC", self.cnc_strategy_combo)
        process_layout.addRow("Encaixe CNC", self.cnc_nesting_mode_combo)
        process_layout.addRow("Espessura do disco (mm)", self.saw_kerf_spin)
        process_layout.addRow("Acrescimo de borda Serra (mm)", self.saw_margin_spin)
        process_layout.addRow("Estrategia Serra", self.saw_strategy_combo)

        # CNC avançado: Common-Line e Stay-Down
        cnc_advanced_label = QtWidgets.QLabel("<b>CNC Avancado</b>")
        process_layout.addRow(cnc_advanced_label)
        self.cnc_common_line_checkbox = QtWidgets.QCheckBox(
            "Common-Line (pecas compartilham linha de corte)"
        )
        self.cnc_common_line_checkbox.setChecked(getattr(settings, "cnc_common_line", False))
        self.cnc_common_line_checkbox.setToolTip(
            "Elimina o kerf entre pecas adjacentes alinhadas, economizando material."
        )
        process_layout.addRow(self.cnc_common_line_checkbox)
        self.cnc_stay_down_checkbox = QtWidgets.QCheckBox(
            "Stay-Down (fresa nao levanta entre cortes adjacentes)"
        )
        self.cnc_stay_down_checkbox.setChecked(getattr(settings, "cnc_stay_down", False))
        self.cnc_stay_down_checkbox.setToolTip(
            "Otimiza o percurso da fresa para minimizar movimentacoes em vazio."
        )
        process_layout.addRow(self.cnc_stay_down_checkbox)

        process_tab_layout.addLayout(process_layout)
        tabs.addTab(process_tab, "Perfis")

        layout_tab = QtWidgets.QWidget()
        layout_tab_layout = QtWidgets.QVBoxLayout(layout_tab)
        layout_tab_layout.setContentsMargins(12, 12, 12, 12)
        layout_tab_layout.setSpacing(10)
        self.show_layout_part_dimensions_checkbox = QtWidgets.QCheckBox("Mostrar medidas nas pecas")
        self.show_layout_part_dimensions_checkbox.setChecked(settings.show_layout_part_dimensions)
        self.show_layout_part_labels_checkbox = QtWidgets.QCheckBox("Mostrar etiqueta das pecas")
        self.show_layout_part_labels_checkbox.setChecked(settings.show_layout_part_labels)
        self.show_layout_edge_bands_checkbox = QtWidgets.QCheckBox(
            "Mostrar fitas de borda nas pecas"
        )
        self.show_layout_edge_bands_checkbox.setChecked(settings.show_layout_edge_bands)
        layout_tab_layout.addWidget(self.show_layout_part_dimensions_checkbox)
        layout_tab_layout.addWidget(self.show_layout_part_labels_checkbox)
        layout_tab_layout.addWidget(self.show_layout_edge_bands_checkbox)

        # Canto de origem do CNC — onde a máquina zera.
        origin_group = QtWidgets.QGroupBox("Canto de origem do CNC (zero-peca)")
        origin_group_layout = QtWidgets.QVBoxLayout(origin_group)
        origin_help = QtWidgets.QLabel(
            "Selecione o canto onde a sua CNC zera a peca. O PanelNest vai empacotar "
            "as pecas a partir desse canto, assim o primeiro corte e sempre o mais "
            "proximo do zero."
        )
        origin_help.setWordWrap(True)
        origin_group_layout.addWidget(origin_help)
        origin_grid = QtWidgets.QGridLayout()
        origin_grid.setHorizontalSpacing(40)
        origin_grid.setVerticalSpacing(6)
        self.cnc_origin_button_group = QtWidgets.QButtonGroup(self)
        self._cnc_origin_radios = {}
        _corners = [
            ("superior_esquerdo", "Superior esquerdo", 0, 0),
            ("superior_direito", "Superior direito", 0, 1),
            ("inferior_esquerdo", "Inferior esquerdo", 1, 0),
            ("inferior_direito", "Inferior direito", 1, 1),
        ]
        _current_corner = getattr(settings, "cnc_origin_corner", "inferior_esquerdo")
        for key, label, row, col in _corners:
            radio = QtWidgets.QRadioButton(label)
            radio.setChecked(key == _current_corner)
            self.cnc_origin_button_group.addButton(radio)
            self._cnc_origin_radios[key] = radio
            origin_grid.addWidget(radio, row, col)
        origin_group_layout.addLayout(origin_grid)
        layout_tab_layout.addWidget(origin_group)

        tabs.addTab(layout_tab, "Layout")

        assembly_tab = QtWidgets.QWidget()
        assembly_tab_layout = QtWidgets.QVBoxLayout(assembly_tab)
        assembly_tab_layout.setContentsMargins(12, 12, 12, 12)
        assembly_tab_layout.setSpacing(10)
        assembly_help = QtWidgets.QLabel(
            "Configure aqui o guia online. Se a publicacao automatica estiver ligada, o comando "
            "`Exportar Arquivos` passa a exportar e publicar no Cloudflare Pages com um clique."
        )
        assembly_help.setWordWrap(True)
        assembly_tab_layout.addWidget(assembly_help)
        assembly_hint = QtWidgets.QLabel(
            "Requisito para o modo automatico: a maquina precisa ter Node.js e credenciais locais "
            "da Cloudflare configuradas nesta propria tela."
        )
        assembly_hint.setWordWrap(True)
        assembly_tab_layout.addWidget(assembly_hint)
        assembly_local_hint = QtWidgets.QLabel(
            "O Account ID, o API Token e o projeto Cloudflare padrao ficam salvos apenas nesta maquina, nas preferencias locais do usuario."
        )
        assembly_local_hint.setWordWrap(True)
        assembly_tab_layout.addWidget(assembly_local_hint)
        assembly_form = QtWidgets.QFormLayout()
        assembly_form.setContentsMargins(0, 0, 0, 0)
        assembly_form.setHorizontalSpacing(10)
        assembly_form.setVerticalSpacing(8)
        self.assembly_guide_public_base_url_edit = QtWidgets.QLineEdit(
            resolved_public_base_url or ""
        )
        self.assembly_guide_cloudflare_project_name_edit = QtWidgets.QLineEdit(
            resolved_project_name or ""
        )
        self.assembly_guide_cloudflare_auto_publish_checkbox = QtWidgets.QCheckBox(
            "Publicar automaticamente no Cloudflare Pages via Wrangler"
        )
        self.assembly_guide_cloudflare_auto_publish_checkbox.setChecked(
            settings.assembly_guide_cloudflare_auto_publish
        )
        self.assembly_guide_preserve_current_visual_checkbox = QtWidgets.QCheckBox(
            "Preservar o visual atual do FreeCAD nas fotos do guia"
        )
        self.assembly_guide_preserve_current_visual_checkbox.setChecked(
            bool(getattr(settings, "assembly_guide_preserve_current_visual", False))
        )
        self.cloudflare_account_id_edit = QtWidgets.QLineEdit(
            cloudflare_publish_prefs.get("account_id", "")
        )
        self.cloudflare_api_token_edit = QtWidgets.QLineEdit(
            cloudflare_publish_prefs.get("api_token", "")
        )
        if hasattr(self.cloudflare_api_token_edit, "setEchoMode") and hasattr(
            QtWidgets.QLineEdit, "Password"
        ):
            self.cloudflare_api_token_edit.setEchoMode(QtWidgets.QLineEdit.Password)
        self._set_compact_field_width(self.assembly_guide_public_base_url_edit, 360, 760)
        self._set_compact_field_width(self.assembly_guide_cloudflare_project_name_edit, 260, 420)
        self._set_compact_field_width(self.cloudflare_account_id_edit, 300, 460)
        self._set_compact_field_width(self.cloudflare_api_token_edit, 300, 520)
        if hasattr(self.assembly_guide_public_base_url_edit, "setPlaceholderText"):
            self.assembly_guide_public_base_url_edit.setPlaceholderText(
                "Opcional. Se vazio, o PanelNest detecta a URL do projeto automaticamente."
            )
        if hasattr(self.assembly_guide_cloudflare_project_name_edit, "setPlaceholderText"):
            self.assembly_guide_cloudflare_project_name_edit.setPlaceholderText(
                "meu-projeto-pages"
            )
        if hasattr(self.cloudflare_account_id_edit, "setPlaceholderText"):
            self.cloudflare_account_id_edit.setPlaceholderText(
                "32 caracteres do Account ID"
            )
        if hasattr(self.cloudflare_api_token_edit, "setPlaceholderText"):
            self.cloudflare_api_token_edit.setPlaceholderText(
                "API token com permissao de Pages"
            )
        assembly_form.addRow(
            "URL publica base do guia (opcional)", self.assembly_guide_public_base_url_edit
        )
        assembly_form.addRow(
            "Projeto Cloudflare Pages",
            self.assembly_guide_cloudflare_project_name_edit,
        )
        assembly_form.addRow("Cloudflare Account ID", self.cloudflare_account_id_edit)
        assembly_form.addRow("Cloudflare API Token", self.cloudflare_api_token_edit)
        assembly_form.addRow("", self.assembly_guide_cloudflare_auto_publish_checkbox)
        assembly_form.addRow("", self.assembly_guide_preserve_current_visual_checkbox)
        assembly_tab_layout.addLayout(assembly_form)
        assembly_tab_layout.addStretch(1)
        tabs.addTab(assembly_tab, "Montagem")

        if hasattr(self.assembly_guide_cloudflare_project_name_edit, "textChanged"):
            self.assembly_guide_cloudflare_project_name_edit.textChanged.connect(
                self._sync_assembly_public_base_url_from_project
            )
        if hasattr(self.assembly_guide_public_base_url_edit, "textEdited"):
            self.assembly_guide_public_base_url_edit.textEdited.connect(
                self._on_assembly_public_base_url_edited
            )

        self._assembly_guide_last_derived_public_base_url = self._derived_public_base_url_for_project(
            resolved_project_name
        )

        remnants_tab = QtWidgets.QWidget()
        remnants_layout = QtWidgets.QVBoxLayout(remnants_tab)
        remnants_layout.setContentsMargins(8, 8, 8, 8)
        remnants_layout.setSpacing(6)

        remnants_help = QtWidgets.QLabel(
            "Adicione aqui chapas fracionadas ou sobras, por exemplo meia chapa 1375 x 1850 x 18 MDF Branco."
        )
        remnants_help.setWordWrap(True)
        if hasattr(remnants_help, "setMaximumWidth"):
            remnants_help.setMaximumWidth(980)
        remnants_layout.addWidget(remnants_help)

        self.remnants_table = QtWidgets.QTableWidget(0, 8)
        self.remnants_table.setAlternatingRowColors(True)
        self.remnants_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.remnants_table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.remnants_table.setHorizontalHeaderLabels(
            ["Descricao", "Compr. (mm)", "Larg. (mm)", "Esp. (mm)", "Material", "Qtd", "Custo R$", "_db_id"]
        )
        self.remnants_table.setColumnHidden(7, True)  # coluna oculta com ID do banco
        if hasattr(self.remnants_table, "setMinimumHeight"):
            self.remnants_table.setMinimumHeight(220)
        header = self.remnants_table.horizontalHeader()
        if hasattr(header, "setSectionResizeMode"):
            header.setSectionResizeMode(0, _stretch_mode())
        remnants_layout.addWidget(self.remnants_table)

        buttons_layout = QtWidgets.QHBoxLayout()
        self.add_remnant_button = QtWidgets.QPushButton("Adicionar Retalho")
        self.remove_remnant_button = QtWidgets.QPushButton("Remover Retalho")
        self.load_from_db_button = QtWidgets.QPushButton("📦 Carregar do Histórico")
        self.load_from_db_button.setToolTip(
            "Selecione retalhos salvos de projetos anteriores para adicionar ao estoque deste projeto"
        )
        buttons_layout.addWidget(self.add_remnant_button)
        buttons_layout.addWidget(self.remove_remnant_button)
        buttons_layout.addSpacing(12)
        buttons_layout.addWidget(self.load_from_db_button)
        buttons_layout.addStretch(1)
        remnants_layout.addLayout(buttons_layout)
        tabs.addTab(remnants_tab, "Retalhos")

        # --- Fita de Borda tab ---
        fita_tab = QtWidgets.QWidget()
        fita_layout = QtWidgets.QVBoxLayout(fita_tab)
        fita_layout.setContentsMargins(12, 12, 12, 12)
        fita_layout.setSpacing(8)

        fita_help = QtWidgets.QLabel(
            "Configure o tipo de fita de borda usada no projeto. "
            "O PanelNest calcula automaticamente o consumo total em metros lineares ao gerar o layout."
        )
        fita_help.setWordWrap(True)
        fita_layout.addWidget(fita_help)

        fita_group = QtWidgets.QGroupBox("Fita de Borda")
        fita_form = QtWidgets.QFormLayout(fita_group)
        fita_form.setLabelAlignment(QtCore.Qt.AlignRight)

        self.edge_band_label_edit = QtWidgets.QLineEdit()
        self.edge_band_label_edit.setPlaceholderText("Ex: Fita PVC 22mm Branca")
        self.edge_band_label_edit.setText(str(getattr(settings, "edge_band_label", "") or ""))
        fita_form.addRow("Descrição da fita", self.edge_band_label_edit)

        self.edge_band_price_spin = QtWidgets.QDoubleSpinBox()
        self.edge_band_price_spin.setDecimals(2)
        self.edge_band_price_spin.setRange(0.0, 9999.99)
        self.edge_band_price_spin.setSingleStep(0.5)
        self.edge_band_price_spin.setPrefix("R$ ")
        self.edge_band_price_spin.setSpecialValueText("Não informado")
        self.edge_band_price_spin.setValue(float(getattr(settings, "edge_band_price_per_m", 0.0) or 0.0))
        fita_form.addRow("Preço por metro (R$/m)", self.edge_band_price_spin)

        self.edge_band_waste_spin = QtWidgets.QDoubleSpinBox()
        self.edge_band_waste_spin.setDecimals(0)
        self.edge_band_waste_spin.setRange(0.0, 50.0)
        self.edge_band_waste_spin.setSingleStep(5.0)
        self.edge_band_waste_spin.setSuffix(" %")
        self.edge_band_waste_spin.setValue(
            float(getattr(settings, "edge_band_waste_factor", 0.10) or 0.10) * 100.0
        )
        fita_form.addRow("Fator de desperdício", self.edge_band_waste_spin)

        fita_layout.addWidget(fita_group)
        fita_layout.addStretch(1)
        tabs.addTab(fita_tab, "Fita de Borda")

        # --- Ferragens tab ---
        hw_tab = QtWidgets.QWidget()
        hw_layout = QtWidgets.QVBoxLayout(hw_tab)
        hw_layout.setContentsMargins(12, 12, 12, 12)
        hw_layout.setSpacing(8)

        hw_help = QtWidgets.QLabel(
            "Defina o catálogo de ferragens do projeto (dobradiças, corrediças, puxadores, parafusos...). "
            "Use o comando 'Editar Ferragens' para associar quantidades a cada peça."
        )
        hw_help.setWordWrap(True)
        hw_layout.addWidget(hw_help)

        self.hardware_table = QtWidgets.QTableWidget(0, 5)
        self.hardware_table.setAlternatingRowColors(True)
        self.hardware_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.hardware_table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.hardware_table.setHorizontalHeaderLabels(["ID", "Nome", "Unidade", "Custo unit. R$", "Ø furo (mm)"])
        hw_header = self.hardware_table.horizontalHeader()
        if hasattr(hw_header, "setSectionResizeMode"):
            hw_header.setSectionResizeMode(1, _stretch_mode())
        hw_layout.addWidget(self.hardware_table)

        hw_detect_help = QtWidgets.QLabel(
            "💡 Preencha 'Ø furo' para detectar ferragens automaticamente pelos furos das peças "
            "(ex: 35 = dobradiça, 15 = minifix, 8 = cavilha)."
        )
        hw_detect_help.setWordWrap(True)
        hw_detect_help.setStyleSheet("color: #555; font-size: 11px; margin: 2px 0 6px 0;")
        hw_layout.addWidget(hw_detect_help)

        # Pré-popular com catálogo existente
        for hw in getattr(settings, "hardware_catalog", []):
            self._add_hardware_row(
                hw_id=getattr(hw, "hw_id", hw.get("hw_id", "") if isinstance(hw, dict) else ""),
                name=getattr(hw, "name", hw.get("name", "") if isinstance(hw, dict) else ""),
                unit=getattr(hw, "unit", hw.get("unit", "un") if isinstance(hw, dict) else "un"),
                cost=getattr(hw, "cost", hw.get("cost", 0.0) if isinstance(hw, dict) else 0.0),
                hole_diameter_mm=getattr(hw, "hole_diameter_mm", hw.get("hole_diameter_mm", 0.0) if isinstance(hw, dict) else 0.0),
            )

        hw_btn_row = QtWidgets.QHBoxLayout()
        add_hw_btn = QtWidgets.QPushButton("+ Adicionar")
        add_hw_btn.clicked.connect(self._add_hardware_row)
        remove_hw_btn = QtWidgets.QPushButton("Remover selecionados")
        remove_hw_btn.clicked.connect(self._remove_selected_hardware_row)
        hw_btn_row.addWidget(add_hw_btn)
        hw_btn_row.addWidget(remove_hw_btn)
        hw_btn_row.addStretch(1)
        hw_layout.addLayout(hw_btn_row)
        hw_layout.addStretch(1)
        tabs.addTab(hw_tab, "Ferragens")

        # --- Empresa / Projeto tab ---
        empresa_tab = QtWidgets.QWidget()
        empresa_layout = QtWidgets.QVBoxLayout(empresa_tab)
        empresa_layout.setContentsMargins(12, 12, 12, 12)
        empresa_layout.setSpacing(8)

        empresa_group = QtWidgets.QGroupBox("Empresa")
        empresa_form = QtWidgets.QFormLayout(empresa_group)
        empresa_form.setHorizontalSpacing(10)
        empresa_form.setVerticalSpacing(6)
        self.company_name_edit = QtWidgets.QLineEdit(settings.company_name or "")
        self.company_contact_edit = QtWidgets.QLineEdit(settings.company_contact or "")
        self.company_address_edit = QtWidgets.QLineEdit(settings.company_address or "")
        if hasattr(self.company_name_edit, "setPlaceholderText"):
            self.company_name_edit.setPlaceholderText("Ex: Marcenaria Exemplo Ltda")
            self.company_contact_edit.setPlaceholderText("Ex: (11) 99999-9999 / email@exemplo.com")
            self.company_address_edit.setPlaceholderText("Ex: Rua das Flores, 123 — São Paulo/SP")
        empresa_form.addRow("Nome da empresa", self.company_name_edit)
        empresa_form.addRow("Contato", self.company_contact_edit)
        empresa_form.addRow("Endereço", self.company_address_edit)
        empresa_layout.addWidget(empresa_group)

        projeto_group = QtWidgets.QGroupBox("Projeto")
        projeto_form = QtWidgets.QFormLayout(projeto_group)
        projeto_form.setHorizontalSpacing(10)
        projeto_form.setVerticalSpacing(6)
        self.project_client_edit = QtWidgets.QLineEdit(settings.project_client or "")
        self.project_responsible_edit = QtWidgets.QLineEdit(settings.project_responsible or "")
        self.project_notes_edit = QtWidgets.QTextEdit(settings.project_notes or "")
        self.project_notes_edit.setMaximumHeight(80)
        if hasattr(self.project_client_edit, "setPlaceholderText"):
            self.project_client_edit.setPlaceholderText("Nome do cliente")
            self.project_responsible_edit.setPlaceholderText("Responsável técnico / projetista")
        projeto_form.addRow("Cliente", self.project_client_edit)
        projeto_form.addRow("Responsável", self.project_responsible_edit)
        projeto_form.addRow("Observações", self.project_notes_edit)
        empresa_layout.addWidget(projeto_group)
        empresa_layout.addStretch(1)

        tabs.addTab(empresa_tab, "Empresa")

        self.add_remnant_button.clicked.connect(self._add_remnant_row)
        self.remove_remnant_button.clicked.connect(self._remove_selected_remnant_row)
        self.load_from_db_button.clicked.connect(self._load_remnants_from_db)

        for remnant in settings.remnants:
            self._add_remnant_row(
                remnant.label,
                remnant.length_mm,
                remnant.width_mm,
                remnant.thickness_mm,
                remnant.material,
                remnant.quantity,
                getattr(remnant, "cost_per_sheet", 0.0),
                db_id=getattr(remnant, "db_id", None),
            )

        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        ok_button = button_box.button(QtWidgets.QDialogButtonBox.Ok)
        cancel_button = button_box.button(QtWidgets.QDialogButtonBox.Cancel)
        if ok_button is not None:
            ok_button.setAutoDefault(False)
            ok_button.setDefault(False)
        if cancel_button is not None:
            cancel_button.setAutoDefault(False)
            cancel_button.setDefault(False)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        main_layout.addWidget(button_box)
        self.finished.connect(self._persist_dialog_size)

        QtCore.QTimer.singleShot(0, self._focus_first_field)
        QtCore.QTimer.singleShot(0, self._apply_initial_size)

    def _apply_initial_size(self):
        import panelnest

        app = QtWidgets.QApplication.instance()
        available_width = 1200
        available_height = 800
        if app is not None:
            if hasattr(app, "primaryScreen") and app.primaryScreen() is not None:
                try:
                    geometry = app.primaryScreen().availableGeometry()
                    available_width = geometry.width()
                    available_height = geometry.height()
                except Exception:
                    pass
            elif hasattr(app, "desktop"):
                try:
                    geometry = app.desktop().availableGeometry(self)
                    available_width = geometry.width()
                    available_height = geometry.height()
                except Exception:
                    pass

        target_width = min(max(620, self.sizeHint().width()), int(available_width * 0.56))
        target_height = min(max(320, self.sizeHint().height()), int(available_height * 0.52))
        panelnest.restore_dialog_size(
            self,
            self._dialog_size_key,
            default_width=target_width,
            default_height=target_height,
            minimum_width=600,
            minimum_height=300,
        )

    def _on_full_sheet_count_changed(self, value):
        if value == 0 and self.allow_extra_full_sheets_checkbox.isChecked():
            self.allow_extra_full_sheets_checkbox.setChecked(False)
            self.allow_extra_full_sheets_checkbox.setStyleSheet("color: #c0392b; font-weight: bold;")
        elif value > 0:
            self.allow_extra_full_sheets_checkbox.setStyleSheet("")

    def _create_spinbox(self, value, minimum, maximum):
        spinbox = QtWidgets.QDoubleSpinBox()
        spinbox.setDecimals(2)
        spinbox.setRange(minimum, maximum)
        spinbox.setSingleStep(1.0)
        spinbox.setSuffix(" mm")
        spinbox.setValue(value)
        return spinbox

    def _set_compact_field_width(self, widget, minimum_width, maximum_width):
        if hasattr(widget, "setMinimumWidth"):
            widget.setMinimumWidth(int(minimum_width))
        if hasattr(widget, "setMaximumWidth"):
            widget.setMaximumWidth(int(maximum_width))

    def payload(self):
        public_base_url = self.assembly_guide_public_base_url_edit.text().strip()
        project_name = self.assembly_guide_cloudflare_project_name_edit.text().strip()
        derived_public_base_url = self._derived_public_base_url_for_project(project_name)
        if not public_base_url or public_base_url == derived_public_base_url:
            public_base_url = ""
        return {
            "length_mm": float(self.length_spin.value()),
            "width_mm": float(self.width_spin.value()),
            "margin_mm": float(self.margin_spin.value()),
            "spacing_mm": float(self.spacing_spin.value()),
            "full_sheet_count": int(self.full_sheet_count_spin.value()),
            "full_sheet_thickness_mm": float(self.full_sheet_thickness_spin.value()),
            "full_sheet_material": self.full_sheet_material_edit.text().strip(),
            "full_sheet_cost": float(self.full_sheet_cost_spin.value()),
            "edge_band_thickness_mm": float(self.edge_band_thickness_spin.value()),
            "edge_band_label": self.edge_band_label_edit.text().strip(),
            "edge_band_price_per_m": float(self.edge_band_price_spin.value()),
            "edge_band_waste_factor": float(self.edge_band_waste_spin.value()) / 100.0,
            "cnc_kerf_mm": float(self.cnc_kerf_spin.value()),
            "cnc_tech_margin_mm": float(self.cnc_margin_spin.value()),
            "cnc_layout_strategy": self.cnc_strategy_combo.currentText(),
            "cnc_nesting_mode": self.cnc_nesting_mode_combo.currentText(),
            "saw_kerf_mm": float(self.saw_kerf_spin.value()),
            "saw_tech_margin_mm": float(self.saw_margin_spin.value()),
            "saw_layout_strategy": self.saw_strategy_combo.currentText(),
            "cnc_common_line": bool(self.cnc_common_line_checkbox.isChecked()),
            "cnc_stay_down": bool(self.cnc_stay_down_checkbox.isChecked()),
            "cnc_origin_corner": next(
                (key for key, radio in self._cnc_origin_radios.items() if radio.isChecked()),
                "inferior_esquerdo",
            ),
            "show_layout_part_dimensions": bool(self.show_layout_part_dimensions_checkbox.isChecked()),
            "show_layout_part_labels": bool(self.show_layout_part_labels_checkbox.isChecked()),
            "show_layout_edge_bands": bool(self.show_layout_edge_bands_checkbox.isChecked()),
            "assembly_guide_public_base_url": public_base_url,
            "assembly_guide_cloudflare_project_name": project_name,
            "assembly_guide_cloudflare_auto_publish": bool(
                self.assembly_guide_cloudflare_auto_publish_checkbox.isChecked()
            ),
            "assembly_guide_preserve_current_visual": bool(
                self.assembly_guide_preserve_current_visual_checkbox.isChecked()
            ),
            "allow_extra_full_sheets": bool(self.allow_extra_full_sheets_checkbox.isChecked()),
            "remnants": self._collect_remnants(),
            "hardware_catalog": self._collect_hardware_catalog(),
            "company_name": self.company_name_edit.text().strip(),
            "company_contact": self.company_contact_edit.text().strip(),
            "company_address": self.company_address_edit.text().strip(),
            "project_client": self.project_client_edit.text().strip(),
            "project_responsible": self.project_responsible_edit.text().strip(),
            "project_notes": self.project_notes_edit.toPlainText().strip(),
        }

    def cloudflare_publish_payload(self):
        return {
            "account_id": self.cloudflare_account_id_edit.text().strip(),
            "api_token": self.cloudflare_api_token_edit.text().strip(),
            "default_project_name": self.assembly_guide_cloudflare_project_name_edit.text().strip(),
        }

    def _derived_public_base_url_for_project(self, project_name):
        return ""

    def _sync_assembly_public_base_url_from_project(self, text):
        derived_public_base_url = self._derived_public_base_url_for_project(text)
        current_text = self.assembly_guide_public_base_url_edit.text().strip()
        should_sync = (
            not self._assembly_guide_public_base_url_custom
            or current_text == self._assembly_guide_last_derived_public_base_url
        )
        self._assembly_guide_last_derived_public_base_url = derived_public_base_url
        if should_sync:
            previous_block_state = (
                self.assembly_guide_public_base_url_edit.blockSignals(True)
                if hasattr(self.assembly_guide_public_base_url_edit, "blockSignals")
                else False
            )
            self.assembly_guide_public_base_url_edit.setText(derived_public_base_url)
            if hasattr(self.assembly_guide_public_base_url_edit, "blockSignals"):
                self.assembly_guide_public_base_url_edit.blockSignals(previous_block_state)
            self._assembly_guide_public_base_url_custom = False

    def _on_assembly_public_base_url_edited(self, text):
        normalized_text = str(text or "").strip()
        derived_public_base_url = self._derived_public_base_url_for_project(
            self.assembly_guide_cloudflare_project_name_edit.text()
        )
        self._assembly_guide_public_base_url_custom = bool(
            normalized_text and normalized_text != derived_public_base_url
        )

    def _focus_first_field(self):
        self.length_spin.setFocus()
        if hasattr(self.length_spin, "selectAll"):
            self.length_spin.selectAll()

    def _persist_dialog_size(self, _result):
        import panelnest

        panelnest.save_dialog_size(self, self._dialog_size_key)

    def _load_remnants_from_db(self):
        """Abre dialog para selecionar retalhos do banco histórico e adicionar ao projeto."""
        import panelnest as _pn
        try:
            conn = _pn.open_remnant_db()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Erro", f"Não foi possível abrir o banco: {exc}")
            return

        try:
            rows = _pn.load_remnants(conn)
        finally:
            conn.close()

        if not rows:
            QtWidgets.QMessageBox.information(
                self, "Banco vazio",
                "Nenhum retalho disponível no histórico.\n\n"
                "Os retalhos são salvos automaticamente ao gerar o layout de qualquer projeto."
            )
            return

        dlg = _RemnantPickerDialog(rows, parent=self)
        if _exec_dialog(dlg) != QtWidgets.QDialog.Accepted:
            return

        selected = dlg.selected_rows()
        if not selected:
            return

        try:
            conn = _pn.open_remnant_db()
            for row in selected:
                _pn.mark_remnant_used(conn, row["id"])
            conn.close()
        except Exception:
            pass

        for row in selected:
            self._add_remnant_row(
                label=row["label"],
                length_mm=row["length_mm"],
                width_mm=row["width_mm"],
                thickness_mm=row["thickness_mm"],
                material=row["material"],
                quantity=1,
                cost_per_sheet=0.0,
                db_id=row["id"],
            )

        QtWidgets.QMessageBox.information(
            self, "Retalhos adicionados",
            f"{len(selected)} retalho(s) adicionado(s) à lista.\nClique em OK para salvar as configurações."
        )

    def _add_hardware_row(self, hw_id="", name="Nova ferragem", unit="un", cost=0.0, hole_diameter_mm=0.0):
        row = self.hardware_table.rowCount()
        self.hardware_table.insertRow(row)
        self.hardware_table.setItem(row, 0, QtWidgets.QTableWidgetItem(str(hw_id)))
        self.hardware_table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(name)))
        self.hardware_table.setItem(row, 2, QtWidgets.QTableWidgetItem(str(unit)))
        cost_text = f"{float(cost):.2f}" if float(cost) > 0 else "0.00"
        self.hardware_table.setItem(row, 3, QtWidgets.QTableWidgetItem(cost_text))
        diam = float(hole_diameter_mm or 0.0)
        diam_text = f"{diam:.1f}" if diam > 0 else ""
        self.hardware_table.setItem(row, 4, QtWidgets.QTableWidgetItem(diam_text))
        self.hardware_table.resizeColumnsToContents()

    def _remove_selected_hardware_row(self):
        selected_ranges = self.hardware_table.selectedRanges()
        if not selected_ranges:
            if self.hardware_table.rowCount() > 0:
                self.hardware_table.removeRow(self.hardware_table.rowCount() - 1)
            return
        rows_to_remove = sorted(
            {r for sr in selected_ranges for r in range(sr.topRow(), sr.bottomRow() + 1)},
            reverse=True,
        )
        for row in rows_to_remove:
            self.hardware_table.removeRow(row)

    def _collect_hardware_catalog(self):
        catalog = []
        for row in range(self.hardware_table.rowCount()):
            hw_id_item = self.hardware_table.item(row, 0)
            name_item = self.hardware_table.item(row, 1)
            unit_item = self.hardware_table.item(row, 2)
            cost_item = self.hardware_table.item(row, 3)
            diam_item = self.hardware_table.item(row, 4)
            hw_id = hw_id_item.text().strip() if hw_id_item else ""
            name = name_item.text().strip() if name_item else ""
            unit = unit_item.text().strip() if unit_item else "un"
            cost_text = cost_item.text().strip() if cost_item else "0"
            diam_text = diam_item.text().strip() if diam_item else ""
            try:
                cost = float(cost_text.replace(",", "."))
            except ValueError:
                cost = 0.0
            try:
                hole_d = float(diam_text.replace(",", ".")) if diam_text else 0.0
            except ValueError:
                hole_d = 0.0
            if not hw_id or not name:
                continue
            entry = {"hw_id": hw_id, "name": name, "unit": unit or "un", "cost": cost}
            if hole_d > 0:
                entry["hole_diameter_mm"] = hole_d
            catalog.append(entry)
        return catalog

    def _add_remnant_row(
        self,
        label="Retalho",
        length_mm=1000.0,
        width_mm=500.0,
        thickness_mm=18.0,
        material="",
        quantity=1,
        cost_per_sheet=0.0,
        db_id=None,
    ):
        row_index = self.remnants_table.rowCount()
        self.remnants_table.insertRow(row_index)
        self.remnants_table.setItem(row_index, 0, QtWidgets.QTableWidgetItem(str(label)))
        self.remnants_table.setItem(row_index, 1, QtWidgets.QTableWidgetItem(f"{float(length_mm):.2f}"))
        self.remnants_table.setItem(row_index, 2, QtWidgets.QTableWidgetItem(f"{float(width_mm):.2f}"))
        thickness_text = "" if float(thickness_mm) <= 0 else f"{float(thickness_mm):.2f}"
        self.remnants_table.setItem(row_index, 3, QtWidgets.QTableWidgetItem(thickness_text))
        self.remnants_table.setItem(row_index, 4, QtWidgets.QTableWidgetItem(str(material or "")))
        self.remnants_table.setItem(row_index, 5, QtWidgets.QTableWidgetItem(str(int(quantity))))
        cost_text = "" if float(cost_per_sheet) <= 0 else f"{float(cost_per_sheet):.2f}"
        self.remnants_table.setItem(row_index, 6, QtWidgets.QTableWidgetItem(cost_text))
        self.remnants_table.setItem(row_index, 7, QtWidgets.QTableWidgetItem(str(db_id) if db_id is not None else ""))
        self.remnants_table.resizeColumnsToContents()

    def _remove_selected_remnant_row(self):
        selected_ranges = self.remnants_table.selectedRanges()
        if not selected_ranges:
            if self.remnants_table.rowCount() > 0:
                self.remnants_table.removeRow(self.remnants_table.rowCount() - 1)
            return
        # Coletar linhas únicas e remover de baixo para cima (evita shift de índices)
        rows_to_remove = sorted(
            {r for sr in selected_ranges for r in range(sr.topRow(), sr.bottomRow() + 1)},
            reverse=True,
        )
        # Restaurar retalhos do banco (marcar como não-usado) antes de remover
        db_ids_to_restore = []
        for row in rows_to_remove:
            db_id_item = self.remnants_table.item(row, 7)
            if db_id_item and db_id_item.text().strip():
                try:
                    db_ids_to_restore.append(int(db_id_item.text().strip()))
                except ValueError:
                    pass
        if db_ids_to_restore:
            try:
                import panelnest as _pn
                conn = _pn.open_remnant_db()
                for db_id in db_ids_to_restore:
                    conn.execute("UPDATE remnants SET used_at = NULL WHERE id = ?", (db_id,))
                conn.commit()
                conn.close()
            except Exception:
                pass
        for row in rows_to_remove:
            self.remnants_table.removeRow(row)

    def _collect_remnants(self):
        remnants = []
        for row_index in range(self.remnants_table.rowCount()):
            label_item = self.remnants_table.item(row_index, 0)
            length_item = self.remnants_table.item(row_index, 1)
            width_item = self.remnants_table.item(row_index, 2)
            thickness_item = self.remnants_table.item(row_index, 3)
            material_item = self.remnants_table.item(row_index, 4)
            quantity_item = self.remnants_table.item(row_index, 5)

            label = label_item.text().strip() if label_item is not None else ""
            length_mm = float((length_item.text() if length_item is not None else "0").replace(",", "."))
            width_mm = float((width_item.text() if width_item is not None else "0").replace(",", "."))
            thickness_text = thickness_item.text().strip() if thickness_item is not None else ""
            thickness_mm = float(thickness_text.replace(",", ".")) if thickness_text else 0.0
            material = material_item.text().strip() if material_item is not None else ""
            quantity = int(float((quantity_item.text() if quantity_item is not None else "0").replace(",", ".")))
            cost_item = self.remnants_table.item(row_index, 6)
            cost_text = cost_item.text().strip() if cost_item is not None else ""
            cost_per_sheet = float(cost_text.replace(",", ".")) if cost_text else 0.0

            if not label:
                label = f"Retalho {row_index + 1:02d}"
            if length_mm <= 0 or width_mm <= 0 or quantity <= 0:
                continue

            db_id_item = self.remnants_table.item(row_index, 7)
            db_id_text = db_id_item.text().strip() if db_id_item is not None else ""
            db_id = int(db_id_text) if db_id_text else None

            remnants.append(
                {
                    "label": label,
                    "length_mm": length_mm,
                    "width_mm": width_mm,
                    "thickness_mm": thickness_mm,
                    "material": material,
                    "quantity": quantity,
                    "cost_per_sheet": cost_per_sheet,
                    "db_id": db_id,
                }
            )
        return remnants


class ConfigureSheetCommand:
    def Activated(self):
        import panelnest

        try:
            settings = panelnest.get_sheet_settings()
        except ValueError as exc:
            if App is not None:
                App.Console.PrintError(f"PanelNest: {exc}\n")
            return

        dialog = SheetSettingsDialog(
            settings,
            parent=Gui.getMainWindow() if Gui is not None and hasattr(Gui, "getMainWindow") else None,
        )
        if _exec_dialog(dialog) != QtWidgets.QDialog.Accepted:
            return

        try:
            payload = dialog.payload()
            updated_settings, config = panelnest.update_sheet_settings(**payload)
            cloudflare_publish_prefs = panelnest.update_cloudflare_publish_preferences(
                **dialog.cloudflare_publish_payload()
            )
        except ValueError as exc:
            if App is not None:
                App.Console.PrintError(f"PanelNest: {exc}\n")
            return

        if App is not None:
            sheet_dimensions = (
                f"{updated_settings.length_mm:.0f} x {updated_settings.width_mm:.0f} x "
                f"{updated_settings.full_sheet_thickness_mm:.0f} mm"
                if updated_settings.full_sheet_thickness_mm > 0
                else f"{updated_settings.length_mm:.0f} x {updated_settings.width_mm:.0f} mm"
            )
            App.Console.PrintMessage(
                "PanelNest: chapa configurada para "
                f"{sheet_dimensions}, "
                f"margem {updated_settings.margin_mm:.0f} mm, espacamento {updated_settings.spacing_mm:.0f} mm, "
                f"{updated_settings.full_sheet_count} chapa(s) inteira(s), espessura padrao "
                f"'{updated_settings.full_sheet_thickness_mm or 0:.0f} mm', material padrao "
                f"'{updated_settings.full_sheet_material or 'Generico'}', perfil CNC "
                f"({updated_settings.cnc_layout_strategy}, {updated_settings.cnc_nesting_mode}, "
                f"kerf {updated_settings.cnc_kerf_mm:.0f} mm / "
                f"margem {updated_settings.cnc_tech_margin_mm:.0f} mm), perfil Serra "
                f"({updated_settings.saw_layout_strategy}, kerf {updated_settings.saw_kerf_mm:.0f} mm / "
                f"margem {updated_settings.saw_tech_margin_mm:.0f} mm), layout com medidas "
                f"{'ligadas' if updated_settings.show_layout_part_dimensions else 'desligadas'}, "
                f"etiquetas {'ligadas' if updated_settings.show_layout_part_labels else 'desligadas'}, "
                f"fitas {'ligadas' if updated_settings.show_layout_edge_bands else 'desligadas'} e "
                f"{len(updated_settings.remnants)} tipo(s) de retalho. Guia publico: "
                f"'{panelnest.resolve_assembly_guide_public_base_url(updated_settings) or 'nao configurado'}', "
                f"projeto Cloudflare '{updated_settings.assembly_guide_cloudflare_project_name or 'nao configurado'}' "
                f"e publicacao automatica "
                f"{'ligada' if updated_settings.assembly_guide_cloudflare_auto_publish else 'desligada'}. "
                f"Captura do guia "
                f"{'preservando o visual atual' if updated_settings.assembly_guide_preserve_current_visual else 'no modo automatico'}. "
                f"Credenciais locais Cloudflare: "
                f"{'configuradas' if cloudflare_publish_prefs.get('account_id') and cloudflare_publish_prefs.get('api_token') else 'incompletas'}.\n"
            )

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
                os.path.dirname(__file__), "..", "resources", "icons", "panelnest_sheet_setup.svg"
            ),
            "Accel": "",
            "MenuText": "Configurar Chapa",
            "ToolTip": "Define chapa, estoque, espacamentos, estrategias de corte e exibicao do layout para o documento atual.",
        }


if Gui is not None:
    Gui.addCommand(COMMAND_NAME, ConfigureSheetCommand())
