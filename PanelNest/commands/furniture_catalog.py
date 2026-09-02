"""Catálogo de móveis do PanelNest.

Fluxo:
  - Importar .FCStd → copia para catálogo, agrupa peças soltas se necessário
  - Salvar seleção  → exporta seleção como FCStd e salva no catálogo
  - Inserir         → abre o FCStd em documento próprio (preserva nomes de objetos)
  - Thumbnail       → usuário escolhe uma foto para representar o modelo
  - Categorias      → criadas pelo usuário via botão "+"
"""
import os
import re

try:
    import FreeCAD as App
except ImportError:
    App = None

try:
    import FreeCADGui as Gui
except ImportError:
    Gui = None

try:
    from PySide import QtGui, QtCore
    QtWidgets = QtGui
except ImportError:
    try:
        from PySide2 import QtWidgets, QtCore, QtGui
    except ImportError:
        from PySide6 import QtWidgets, QtCore, QtGui

COMMAND_NAME = "PanelNest_FurnitureCatalog"
_COLS = 4


def _main_window():
    if Gui is not None and hasattr(Gui, "getMainWindow"):
        try:
            return Gui.getMainWindow()
        except Exception:
            return None
    return None


def _hline():
    line = QtWidgets.QFrame()
    line.setFrameShape(QtWidgets.QFrame.HLine)
    line.setFrameShadow(QtWidgets.QFrame.Sunken)
    return line


def _load_pixmap(path, size):
    """Carrega imagem (JPG/PNG) ou retorna placeholder."""
    if path and os.path.exists(path):
        px = QtGui.QPixmap(path)
        if not px.isNull():
            return px.scaled(size, size,
                             QtCore.Qt.KeepAspectRatio,
                             QtCore.Qt.SmoothTransformation)
    # Placeholder cinza com ícone de câmera
    px = QtGui.QPixmap(size, size)
    px.fill(QtGui.QColor("#e8edf2"))
    return px


# ---------------------------------------------------------------------------
# Card de modelo
# ---------------------------------------------------------------------------

class _ModelCard(QtWidgets.QFrame):
    clicked = QtCore.Signal(str)          # folder path
    double_clicked = QtCore.Signal(str)
    thumbnail_requested = QtCore.Signal(str)

    def __init__(self, entry, parent=None):
        super().__init__(parent)
        self._folder = entry.folder
        self.setFrameShape(QtWidgets.QFrame.StyledPanel)
        self.setFrameShadow(QtWidgets.QFrame.Raised)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFixedSize(164, 210)
        self._selected = False
        self._update_border()

        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(3)
        layout.setContentsMargins(6, 6, 6, 6)

        # Thumbnail — fundo semi-transparente neutro
        self._img_lbl = QtWidgets.QLabel()
        self._img_lbl.setFixedSize(150, 110)
        self._img_lbl.setAlignment(QtCore.Qt.AlignCenter)
        self._img_lbl.setStyleSheet(
            "QLabel{background:rgba(128,128,128,40);border-radius:4px;}"
        )
        self._refresh_thumbnail(entry.thumbnail_path)
        layout.addWidget(self._img_lbl)

        # Link "Adicionar foto"
        cam_btn = QtWidgets.QPushButton("Adicionar foto")
        cam_btn.setFixedHeight(18)
        cam_btn.setFlat(True)
        cam_btn.setToolTip("Escolher foto representativa para este modelo")
        cam_btn.setStyleSheet("font-size:9px; text-decoration:underline;")
        cam_btn.clicked.connect(lambda: self.thumbnail_requested.emit(self._folder))
        layout.addWidget(cam_btn)

        # Nome
        lbl = QtWidgets.QLabel(entry.name)
        lbl.setAlignment(QtCore.Qt.AlignCenter)
        lbl.setWordWrap(True)
        font = lbl.font()
        font.setBold(True)
        font.setPointSize(max(8, font.pointSize() - 1))
        lbl.setFont(font)
        layout.addWidget(lbl)

        # Data
        date_str = entry.date_added[:10] if entry.date_added else ""
        date_lbl = QtWidgets.QLabel(date_str)
        date_lbl.setAlignment(QtCore.Qt.AlignCenter)
        font2 = date_lbl.font()
        font2.setPointSize(max(7, font2.pointSize() - 2))
        date_lbl.setFont(font2)
        # Cor discreta usando paleta
        pal = date_lbl.palette()
        pal.setColor(
            QtGui.QPalette.WindowText,
            self.palette().color(QtGui.QPalette.Mid),
        )
        date_lbl.setPalette(pal)
        layout.addWidget(date_lbl)

    def _refresh_thumbnail(self, path):
        px = _load_pixmap(path, 150)
        self._img_lbl.setPixmap(px)

    def _update_border(self):
        if self._selected:
            self.setStyleSheet(
                "QFrame{border:2px solid palette(highlight);border-radius:8px;}"
            )
        else:
            self.setStyleSheet(
                "QFrame{border:1px solid palette(mid);border-radius:8px;}"
            )

    def set_selected(self, sel):
        self._selected = sel
        self._update_border()

    def mousePressEvent(self, event):
        self.clicked.emit(self._folder)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.double_clicked.emit(self._folder)
        super().mouseDoubleClickEvent(event)


# ---------------------------------------------------------------------------
# Dialog: informações ao adicionar modelo
# ---------------------------------------------------------------------------

class _AddModelDialog(QtWidgets.QDialog):
    def __init__(self, suggested_name, existing_categories, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PanelNest — Adicionar ao Catálogo")
        self.setMinimumWidth(400)
        self._build_ui(suggested_name, existing_categories)

    def _build_ui(self, suggested_name, cats):
        layout = QtWidgets.QVBoxLayout(self)

        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(QtCore.Qt.AlignRight)

        self._name_edit = QtWidgets.QLineEdit(suggested_name)
        self._name_edit.setPlaceholderText("Ex: Armário da Cozinha 800mm")
        form.addRow("Nome:", self._name_edit)

        self._cat_combo = QtWidgets.QComboBox()
        self._cat_combo.setEditable(True)
        sorted_cats = sorted(cats) if cats else []
        if not sorted_cats:
            sorted_cats = ["Geral"]
        self._cat_combo.addItems(sorted_cats)
        form.addRow("Categoria:", self._cat_combo)

        self._desc_edit = QtWidgets.QLineEdit()
        self._desc_edit.setPlaceholderText("Descrição opcional")
        form.addRow("Descrição:", self._desc_edit)

        layout.addLayout(form)
        layout.addWidget(_hline())

        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        btns.button(QtWidgets.QDialogButtonBox.Ok).setText("Salvar no catálogo")
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _on_accept(self):
        if not self._name_edit.text().strip():
            QtWidgets.QMessageBox.warning(self, "PanelNest", "Informe um nome.")
            return
        self.accept()

    def model_name(self):
        return self._name_edit.text().strip()

    def category(self):
        return self._cat_combo.currentText().strip() or "Geral"

    def description(self):
        return self._desc_edit.text().strip()


# ---------------------------------------------------------------------------
# Dialog principal
# ---------------------------------------------------------------------------

class FurnitureCatalogDialog(QtWidgets.QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PanelNest — Catálogo de Móveis")
        self.setMinimumSize(760, 560)
        self._selected_entry = None
        self._all_entries = []
        self._cards = {}          # folder -> _ModelCard
        self._active_category = "Todas"
        self._result_entry = None
        self._build_ui()
        self._reload()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setSpacing(6)

        # Barra superior
        top = QtWidgets.QHBoxLayout()

        btn_import = QtWidgets.QPushButton("⊕  Importar .FCStd")
        btn_import.setToolTip("Copia um arquivo FreeCAD para o catálogo")
        btn_import.clicked.connect(self._on_import_fcstd)
        top.addWidget(btn_import)

        btn_parametric = QtWidgets.QPushButton("⊕  Gerar Gabinete")
        btn_parametric.setToolTip(
            "Gera um gabinete parametrico (armario, estante, gaveteiro)\n"
            "com dimensoes ajustaveis diretamente no documento"
        )
        btn_parametric.clicked.connect(self._on_generate_parametric)
        top.addWidget(btn_parametric)

        top.addStretch()

        self._remove_btn = QtWidgets.QPushButton("Remover modelo")
        self._remove_btn.setStyleSheet("color:#c0392b;")
        self._remove_btn.setEnabled(False)
        self._remove_btn.clicked.connect(self._on_remove)
        top.addWidget(self._remove_btn)

        root.addLayout(top)
        root.addWidget(_hline())

        # Corpo: categorias + grid
        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)

        # Painel esquerdo — categorias
        left = QtWidgets.QWidget()
        lv = QtWidgets.QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 4, 0)

        cat_header = QtWidgets.QHBoxLayout()
        cat_header.setSpacing(4)
        cat_header.addWidget(QtWidgets.QLabel("<b>Categorias</b>"))
        cat_header.addStretch()

        btn_add_cat = QtWidgets.QPushButton("+")
        btn_add_cat.setFixedWidth(28)
        btn_add_cat.setToolTip("Nova categoria")
        btn_add_cat.clicked.connect(self._on_add_category)
        cat_header.addWidget(btn_add_cat)

        btn_del_cat = QtWidgets.QPushButton("-")
        btn_del_cat.setFixedWidth(28)
        btn_del_cat.setToolTip("Remover categoria selecionada (apenas se vazia)")
        btn_del_cat.clicked.connect(self._on_remove_category)
        cat_header.addWidget(btn_del_cat)

        lv.addLayout(cat_header)

        self._cat_list = QtWidgets.QListWidget()
        self._cat_list.setMaximumWidth(170)
        self._cat_list.setMinimumWidth(130)
        self._cat_list.currentItemChanged.connect(self._on_category_changed)
        lv.addWidget(self._cat_list)
        splitter.addWidget(left)

        # Painel direito — grid de cards
        self._scroll = QtWidgets.QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self._grid_widget = QtWidgets.QWidget()
        self._grid_layout = QtWidgets.QGridLayout(self._grid_widget)
        self._grid_layout.setSpacing(12)
        self._grid_layout.setContentsMargins(10, 10, 10, 10)
        self._scroll.setWidget(self._grid_widget)
        splitter.addWidget(self._scroll)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)

        root.addWidget(_hline())

        # Botões inferiores
        btns = QtWidgets.QHBoxLayout()
        btns.addStretch()

        close_btn = QtWidgets.QPushButton("Fechar")
        close_btn.clicked.connect(self.reject)
        btns.addWidget(close_btn)

        self._insert_btn = QtWidgets.QPushButton("Inserir no documento →")
        self._insert_btn.setEnabled(False)
        self._insert_btn.setDefault(True)
        self._insert_btn.clicked.connect(self._on_insert)
        self._insert_btn.setStyleSheet(
            "QPushButton:enabled{background:#2d5a8e;color:white;font-weight:bold;"
            "padding:6px 18px;border-radius:4px;}"
            "QPushButton:disabled{color:#aaa;padding:6px 18px;}"
        )
        btns.addWidget(self._insert_btn)
        root.addLayout(btns)

    # ------------------------------------------------------------------
    # Carregamento
    # ------------------------------------------------------------------

    def _reload(self):
        from panelnest.furniture_catalog import load_catalog
        self._all_entries = load_catalog()
        self._update_categories()
        self._rebuild_grid()

    def _update_categories(self):
        self._cat_list.blockSignals(True)
        self._cat_list.clear()
        self._cat_list.addItem("Todas")

        from panelnest.furniture_catalog import list_categories
        for cat in list_categories():
            self._cat_list.addItem(cat)

        items = self._cat_list.findItems(
            self._active_category, QtCore.Qt.MatchExactly
        )
        if items:
            self._cat_list.setCurrentItem(items[0])
        else:
            self._cat_list.setCurrentRow(0)
            self._active_category = "Todas"
        self._cat_list.blockSignals(False)

    def _on_category_changed(self, current, _prev):
        if current:
            self._active_category = current.text()
            self._rebuild_grid()

    def _rebuild_grid(self):
        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._cards = {}

        from panelnest.furniture_catalog import _slug
        if self._active_category == "Todas":
            entries = self._all_entries
        else:
            entries = [
                e for e in self._all_entries
                if _slug(e.category) == _slug(self._active_category)
                or e.category == self._active_category
            ]

        if not entries:
            msg = (
                "Catálogo vazio.\n\n"
                "Clique em \"⊕ Importar arquivo .FCStd\" para adicionar\n"
                "seus projetos FreeCAD ao catálogo.\n\n"
                "Ou selecione peças no documento e clique \"⊕ Salvar seleção\"."
            )
            empty = QtWidgets.QLabel(msg)
            empty.setAlignment(QtCore.Qt.AlignCenter)
            empty.setStyleSheet("color:#aaa; font-size:13px; padding:50px;")
            self._grid_layout.addWidget(empty, 0, 0, 1, _COLS)
        else:
            for idx, entry in enumerate(entries):
                row, col = divmod(idx, _COLS)
                card = _ModelCard(entry)
                card.clicked.connect(self._on_card_clicked)
                card.double_clicked.connect(self._on_card_double_clicked)
                card.thumbnail_requested.connect(self._on_set_thumbnail)
                self._grid_layout.addWidget(card, row, col)
                self._cards[entry.folder] = card
            self._grid_layout.setRowStretch(
                (len(entries) - 1) // _COLS + 1, 1
            )

        self._selected_entry = None
        self._insert_btn.setEnabled(False)
        self._remove_btn.setEnabled(False)

    # ------------------------------------------------------------------
    # Seleção
    # ------------------------------------------------------------------

    def _on_card_clicked(self, folder):
        self._selected_entry = next(
            (e for e in self._all_entries if e.folder == folder), None
        )
        for f, card in self._cards.items():
            card.set_selected(f == folder)
        self._insert_btn.setEnabled(True)
        self._remove_btn.setEnabled(True)

    def _on_card_double_clicked(self, folder):
        self._on_card_clicked(folder)
        self._on_insert()

    # ------------------------------------------------------------------
    # Inserir no documento
    # ------------------------------------------------------------------

    def _on_insert(self):
        if self._selected_entry is None or App is None:
            return

        import shutil, tempfile, os
        entry = self._selected_entry
        try:
            # Abre o modelo em documento próprio (não faz merge no doc atual).
            # mergeProject renomeia objetos ao resolver conflitos, quebrando
            # as referências de attachment (AttachEngine3D: subshape not found).
            # Abrir como documento separado preserva todos os nomes intactos.
            suffix = os.path.splitext(entry.fcstd_path)[1] or ".FCStd"
            safe_name = re.sub(r"[^\w\-]", "_", entry.name)[:40] or "modelo"
            tmp_dir = tempfile.mkdtemp(prefix="pn_cat_")
            tmp_path = os.path.join(tmp_dir, f"{safe_name}{suffix}")
            shutil.copy2(entry.fcstd_path, tmp_path)

            doc = App.openDocument(tmp_path)
            doc.recompute()
            if Gui is not None:
                Gui.activeDocument().activeView().viewIsometric()
                Gui.SendMsgToActiveView("ViewFit")
            App.Console.PrintMessage(
                f"PanelNest: modelo \"{entry.name}\" aberto em novo documento.\n"
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self, "Erro ao abrir modelo", str(exc)
            )
            return

        self.accept()

    # ------------------------------------------------------------------
    # Importar .FCStd externo
    # ------------------------------------------------------------------

    def _on_import_fcstd(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Selecionar arquivo FreeCAD",
            os.path.expanduser("~"),
            "FreeCAD (*.FCStd *.fcstd);;Todos os arquivos (*)",
        )
        if not path:
            return

        suggested = os.path.splitext(os.path.basename(path))[0]
        from panelnest.furniture_catalog import list_categories
        dlg = _AddModelDialog(suggested, list_categories(), self)
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return

        from panelnest.furniture_catalog import add_entry
        try:
            entry = add_entry(
                path,
                dlg.model_name(),
                dlg.category(),
                dlg.description(),
            )
            self._active_category = entry.category
            self._reload()
            QtWidgets.QMessageBox.information(
                self, "PanelNest",
                f"Modelo \"{entry.name}\" adicionado ao catálogo."
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Erro ao importar", str(exc))

    # ------------------------------------------------------------------
    # Thumbnail
    # ------------------------------------------------------------------

    def _on_set_thumbnail(self, folder):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Escolher foto do modelo",
            os.path.expanduser("~"),
            "Imagens (*.png *.jpg *.jpeg);;Todos os arquivos (*)",
        )
        if not path:
            return

        entry = next((e for e in self._all_entries if e.folder == folder), None)
        if entry is None:
            return

        from panelnest.furniture_catalog import set_thumbnail
        try:
            set_thumbnail(entry, path)
            self._reload()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Erro", str(exc))

    # ------------------------------------------------------------------
    # Nova categoria
    # ------------------------------------------------------------------

    def _on_add_category(self):
        name, ok = QtWidgets.QInputDialog.getText(
            self, "Nova Categoria",
            "Nome da categoria (ex: Cozinha, Quarto, Sala):"
        )
        if not ok or not name.strip():
            return

        from panelnest.furniture_catalog import create_category
        try:
            create_category(name.strip())
            self._active_category = name.strip()
            self._reload()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Erro", str(exc))

    def _on_remove_category(self):
        current = self._cat_list.currentItem()
        if current is None or current.text() == "Todas":
            QtWidgets.QMessageBox.information(
                self, "PanelNest", "Selecione uma categoria para remover."
            )
            return

        cat_name = current.text()

        # Verifica se há modelos na categoria
        has_models = any(
            e.category == cat_name or
            e.folder.split(os.sep)[-2] == cat_name
            for e in self._all_entries
        )
        if has_models:
            QtWidgets.QMessageBox.warning(
                self, "PanelNest",
                f"A categoria \"{cat_name}\" contém modelos.\n"
                "Remova os modelos antes de excluir a categoria."
            )
            return

        reply = QtWidgets.QMessageBox.question(
            self, "Remover categoria",
            f"Remover a categoria \"{cat_name}\"?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if reply != QtWidgets.QMessageBox.Yes:
            return

        import shutil
        from panelnest.furniture_catalog import catalog_root, _slug
        cat_path = os.path.join(catalog_root(), _slug(cat_name))
        try:
            if os.path.isdir(cat_path):
                shutil.rmtree(cat_path)
            self._active_category = "Todas"
            self._reload()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Erro ao remover categoria", str(exc))

    # ------------------------------------------------------------------
    # Remover
    # ------------------------------------------------------------------

    def _on_remove(self):
        if self._selected_entry is None:
            return
        entry = self._selected_entry
        reply = QtWidgets.QMessageBox.question(
            self, "Remover modelo",
            f"Remover \"{entry.name}\" do catálogo?\n"
            "O arquivo FCStd e a foto serão excluídos.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if reply != QtWidgets.QMessageBox.Yes:
            return

        from panelnest.furniture_catalog import remove_entry
        try:
            remove_entry(entry)
            self._selected_entry = None
            self._reload()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Erro ao remover", str(exc))


    # ------------------------------------------------------------------
    # Gabinete paramétrico
    # ------------------------------------------------------------------

    def _on_generate_parametric(self):
        dlg = _ParametricCabinetDialog(self)
        if hasattr(dlg, "exec_"):
            result = dlg.exec_()
        else:
            result = dlg.exec()
        if result != QtWidgets.QDialog.Accepted:
            return
        data = dlg.payload()
        if data is None:
            return

        from panelnest.parametric_cabinet import (
            get_template_by_id,
            generate_cabinet_parts,
            create_cabinet_in_freecad,
        )
        template = get_template_by_id(data["template_id"])
        if template is None:
            QtWidgets.QMessageBox.warning(self, "PanelNest", "Template nao encontrado.")
            return

        try:
            parts = generate_cabinet_parts(
                template,
                width_mm=data["width"],
                height_mm=data["height"],
                depth_mm=data["depth"],
                thickness_mm=data["thickness"],
                back_thickness_mm=data["back_thickness"],
                material=data["material"],
                shelf_count=data["shelves"],
                door_count=data["doors"],
                drawer_count=data["drawers"],
            )
            container = create_cabinet_in_freecad(template, parts, data["name"])
            if App is not None:
                App.Console.PrintMessage(
                    f"PanelNest: gabinete \"{data['name']}\" criado com {len(parts)} pecas.\n"
                )
            QtWidgets.QMessageBox.information(
                self, "PanelNest",
                f"Gabinete \"{data['name']}\" gerado com {len(parts)} pecas.\n"
                "As pecas ja possuem propriedades PanelNest configuradas."
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Erro ao gerar gabinete", str(exc))


# ---------------------------------------------------------------------------
# Dialog: gabinete paramétrico
# ---------------------------------------------------------------------------

class _ParametricCabinetDialog(QtWidgets.QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PanelNest — Gerar Gabinete Parametrico")
        self.setMinimumWidth(500)
        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)

        # Template selector
        from panelnest.parametric_cabinet import CABINET_TEMPLATES, list_template_categories

        template_group = QtWidgets.QGroupBox("Tipo de gabinete")
        tg_layout = QtWidgets.QVBoxLayout(template_group)

        self._template_combo = QtWidgets.QComboBox()
        for t in CABINET_TEMPLATES:
            self._template_combo.addItem(f"{t.name}  ({t.category})", t.template_id)
        self._template_combo.currentIndexChanged.connect(self._on_template_changed)
        tg_layout.addWidget(self._template_combo)

        self._desc_label = QtWidgets.QLabel("")
        self._desc_label.setWordWrap(True)
        self._desc_label.setStyleSheet("color: gray; font-style: italic;")
        tg_layout.addWidget(self._desc_label)

        layout.addWidget(template_group)

        # Dimensões
        dims_group = QtWidgets.QGroupBox("Dimensoes (mm)")
        dims_layout = QtWidgets.QFormLayout(dims_group)

        self._name_edit = QtWidgets.QLineEdit("Gabinete")
        dims_layout.addRow("Nome:", self._name_edit)

        self._width_spin = self._make_spin(600, 100, 3000)
        dims_layout.addRow("Largura:", self._width_spin)

        self._height_spin = self._make_spin(720, 100, 3000)
        dims_layout.addRow("Altura:", self._height_spin)

        self._depth_spin = self._make_spin(560, 100, 1000)
        dims_layout.addRow("Profundidade:", self._depth_spin)

        self._thickness_spin = self._make_spin(18, 3, 50)
        dims_layout.addRow("Espessura do painel:", self._thickness_spin)

        self._back_thickness_spin = self._make_spin(3, 0, 18)
        dims_layout.addRow("Espessura do fundo:", self._back_thickness_spin)

        self._material_edit = QtWidgets.QLineEdit("")
        self._material_edit.setPlaceholderText("Ex: MDF 18mm, MDP Branco")
        dims_layout.addRow("Material:", self._material_edit)

        layout.addWidget(dims_group)

        # Opções
        opts_group = QtWidgets.QGroupBox("Opcoes")
        opts_layout = QtWidgets.QFormLayout(opts_group)

        self._shelves_spin = self._make_spin(1, 0, 10)
        opts_layout.addRow("Prateleiras:", self._shelves_spin)

        self._doors_spin = self._make_spin(1, 0, 4)
        opts_layout.addRow("Portas:", self._doors_spin)

        self._drawers_spin = self._make_spin(0, 0, 8)
        opts_layout.addRow("Gavetas:", self._drawers_spin)

        layout.addWidget(opts_group)

        # Botões
        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        btns.button(QtWidgets.QDialogButtonBox.Ok).setText("Gerar no documento")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        # Inicializar com o primeiro template
        self._on_template_changed(0)

    def _make_spin(self, default, minimum, maximum):
        spin = QtWidgets.QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(default)
        return spin

    def _on_template_changed(self, _index):
        from panelnest.parametric_cabinet import get_template_by_id
        tid = self._template_combo.currentData()
        t = get_template_by_id(tid)
        if t is None:
            return
        self._desc_label.setText(t.description)
        self._width_spin.setValue(int(t.default_width))
        self._height_spin.setValue(int(t.default_height))
        self._depth_spin.setValue(int(t.default_depth))
        self._thickness_spin.setValue(int(t.default_thickness))
        self._back_thickness_spin.setValue(int(t.back_thickness))
        self._shelves_spin.setValue(t.shelf_count)
        self._doors_spin.setValue(t.door_count)
        self._drawers_spin.setValue(t.drawer_count)
        self._name_edit.setText(t.name)

    def payload(self):
        tid = self._template_combo.currentData()
        if tid is None:
            return None
        return {
            "template_id": tid,
            "name": self._name_edit.text().strip() or "Gabinete",
            "width": self._width_spin.value(),
            "height": self._height_spin.value(),
            "depth": self._depth_spin.value(),
            "thickness": self._thickness_spin.value(),
            "back_thickness": self._back_thickness_spin.value(),
            "material": self._material_edit.text().strip(),
            "shelves": self._shelves_spin.value(),
            "doors": self._doors_spin.value(),
            "drawers": self._drawers_spin.value(),
        }


# ---------------------------------------------------------------------------
# Comando FreeCAD
# ---------------------------------------------------------------------------

class FurnitureCatalogCommand:
    def Activated(self):
        dlg = FurnitureCatalogDialog(_main_window())
        dlg.exec_()

    def IsActive(self):
        return App is not None

    def GetResources(self):
        return {
            "Pixmap": os.path.join(
                os.path.dirname(__file__), "..", "resources", "icons",
                "panelnest_catalog.svg"
            ),
            "Accel": "",
            "MenuText": "Catálogo de Móveis",
            "ToolTip": (
                "Catálogo pessoal de móveis.\n"
                "Importe projetos FreeCAD ou salve a seleção atual para reutilizar."
            ),
        }


if Gui is not None:
    Gui.addCommand(COMMAND_NAME, FurnitureCatalogCommand())
