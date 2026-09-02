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


COMMAND_NAME = "PanelNest_ApplyMetadata"


def _exec_dialog(dialog):
    if hasattr(dialog, "exec"):
        return dialog.exec()
    return dialog.exec_()


class ApplyMetadataDialog(QtWidgets.QDialog):
    def __init__(
        self,
        target_count,
        initial_material,
        initial_cut_method,
        initial_allow_rotation,
        initial_grain_direction,
        apply_prefs,
        edge_band_flags,
        cut_method_options,
        grain_direction_options,
        parent=None,
    ):
        super().__init__(parent)
        import panelnest

        self._grain_direction_options = list(grain_direction_options)
        self._cut_method_options = [
            option
            for option in cut_method_options
            if str(option).strip().lower() in {"cnc", "seccionadora"}
        ]
        self._dialog_size_key = "apply_metadata"
        if not self._cut_method_options:
            self._cut_method_options = ["CNC", "Seccionadora"]
        self.setWindowTitle("PanelNest: Aplicar Dados")
        self.setModal(True)

        main_layout = QtWidgets.QVBoxLayout(self)

        material_value = apply_prefs.get("material") or initial_material or "MDF"
        cut_method_value = apply_prefs.get("cut_method") or initial_cut_method or self._cut_method_options[0]
        if cut_method_value not in self._cut_method_options:
            cut_method_value = self._cut_method_options[0]
        rotation_value = bool(apply_prefs.get("allow_rotation", initial_allow_rotation))
        grain_direction_value = (
            apply_prefs.get("grain_direction")
            or initial_grain_direction
            or grain_direction_options[0]
        )
        edge_band_values = (
            bool(apply_prefs.get("edge_band_top", edge_band_flags[0])),
            bool(apply_prefs.get("edge_band_bottom", edge_band_flags[1])),
            bool(apply_prefs.get("edge_band_left", edge_band_flags[2])),
            bool(apply_prefs.get("edge_band_right", edge_band_flags[3])),
        )

        intro = QtWidgets.QLabel(
            f"Aplicar os mesmos dados em {target_count} peca(s). "
            "Se a selecao nao tiver pecas validas, o PanelNest usa todas as pecas visiveis. "
            "So os campos marcados serao alterados; campos desmarcados nao mudam nada."
        )
        intro.setWordWrap(True)
        main_layout.addWidget(intro)

        current_values = QtWidgets.QLabel(
            "Referencia da primeira peca: rotacao "
            f"{'permitida' if initial_allow_rotation else 'bloqueada'} | "
            f"veio/fibra {initial_grain_direction or grain_direction_options[0]}"
        )
        current_values.setWordWrap(True)
        main_layout.addWidget(current_values)

        self.apply_material_checkbox = QtWidgets.QCheckBox("Aplicar material")
        self.apply_material_checkbox.setChecked(bool(apply_prefs.get("apply_material", True)))
        self.material_edit = QtWidgets.QLineEdit(material_value)
        material_layout = QtWidgets.QHBoxLayout()
        material_layout.addWidget(self.apply_material_checkbox)
        material_layout.addWidget(self.material_edit)
        main_layout.addLayout(material_layout)

        self.apply_cut_method_checkbox = QtWidgets.QCheckBox("Aplicar metodo de corte")
        self.apply_cut_method_checkbox.setChecked(bool(apply_prefs.get("apply_cut_method", True)))
        self.cut_method_combo = QtWidgets.QComboBox()
        for option in self._cut_method_options:
            self.cut_method_combo.addItem(option)
        index = self.cut_method_combo.findText(cut_method_value)
        if index >= 0:
            self.cut_method_combo.setCurrentIndex(index)
        cut_method_layout = QtWidgets.QHBoxLayout()
        cut_method_layout.addWidget(self.apply_cut_method_checkbox)
        cut_method_layout.addWidget(self.cut_method_combo)
        main_layout.addLayout(cut_method_layout)

        self.apply_rotation_checkbox = QtWidgets.QCheckBox("Aplicar rotacao no layout")
        self.apply_rotation_checkbox.setChecked(bool(apply_prefs.get("apply_rotation", False)))
        self.rotation_combo = QtWidgets.QComboBox()
        self.rotation_combo.addItem("Permitir girar as pecas no layout", True)
        self.rotation_combo.addItem("Nao permitir girar as pecas no layout", False)
        self.rotation_combo.setCurrentIndex(0 if rotation_value else 1)
        rotation_layout = QtWidgets.QHBoxLayout()
        rotation_layout.addWidget(self.apply_rotation_checkbox)
        rotation_layout.addWidget(self.rotation_combo)
        main_layout.addLayout(rotation_layout)

        self.apply_grain_checkbox = QtWidgets.QCheckBox("Aplicar veio/fibra")
        self.apply_grain_checkbox.setChecked(bool(apply_prefs.get("apply_grain", False)))
        self.grain_direction_combo = QtWidgets.QComboBox()
        for option in grain_direction_options:
            self.grain_direction_combo.addItem(option)
        grain_index = self.grain_direction_combo.findText(grain_direction_value)
        self.grain_direction_combo.setCurrentIndex(grain_index if grain_index >= 0 else 0)
        grain_layout = QtWidgets.QHBoxLayout()
        grain_layout.addWidget(self.apply_grain_checkbox)
        grain_layout.addWidget(self.grain_direction_combo)
        main_layout.addLayout(grain_layout)

        self.apply_edge_band_checkbox = QtWidgets.QCheckBox("Atualizar fita de borda")
        self.apply_edge_band_checkbox.setChecked(bool(apply_prefs.get("apply_edge_band", False)))
        main_layout.addWidget(self.apply_edge_band_checkbox)

        edge_band_info = QtWidgets.QLabel(
            "A fita de borda e um metadado de acabamento da peca. "
            "Ela nao altera o encaixe nem a sequencia de corte; hoje serve para consulta e planilhas."
        )
        edge_band_info.setWordWrap(True)
        main_layout.addWidget(edge_band_info)

        edge_band_group = QtWidgets.QGroupBox("Fita de Borda")
        edge_band_layout = QtWidgets.QGridLayout(edge_band_group)
        self.edge_top_checkbox = QtWidgets.QCheckBox("Superior")
        self.edge_bottom_checkbox = QtWidgets.QCheckBox("Inferior")
        self.edge_left_checkbox = QtWidgets.QCheckBox("Esquerda")
        self.edge_right_checkbox = QtWidgets.QCheckBox("Direita")
        self.edge_top_checkbox.setChecked(edge_band_values[0])
        self.edge_bottom_checkbox.setChecked(edge_band_values[1])
        self.edge_left_checkbox.setChecked(edge_band_values[2])
        self.edge_right_checkbox.setChecked(edge_band_values[3])
        edge_band_layout.addWidget(self.edge_top_checkbox, 0, 0)
        edge_band_layout.addWidget(self.edge_bottom_checkbox, 0, 1)
        edge_band_layout.addWidget(self.edge_left_checkbox, 1, 0)
        edge_band_layout.addWidget(self.edge_right_checkbox, 1, 1)
        main_layout.addWidget(edge_band_group)

        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        self._accept_button = button_box.button(QtWidgets.QDialogButtonBox.Ok)
        self._accept_button.setToolTip(
            "Marque pelo menos um campo para aplicar alguma alteracao nas pecas."
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        main_layout.addWidget(button_box)

        self.material_edit.textEdited.connect(lambda _text: self.apply_material_checkbox.setChecked(True))
        self.cut_method_combo.currentIndexChanged.connect(
            lambda _index: self.apply_cut_method_checkbox.setChecked(True)
        )
        self.rotation_combo.currentIndexChanged.connect(
            lambda _index: self.apply_rotation_checkbox.setChecked(True)
        )
        self.grain_direction_combo.currentIndexChanged.connect(
            lambda _index: self.apply_grain_checkbox.setChecked(True)
        )
        self.edge_top_checkbox.toggled.connect(lambda _checked: self.apply_edge_band_checkbox.setChecked(True))
        self.edge_bottom_checkbox.toggled.connect(lambda _checked: self.apply_edge_band_checkbox.setChecked(True))
        self.edge_left_checkbox.toggled.connect(lambda _checked: self.apply_edge_band_checkbox.setChecked(True))
        self.edge_right_checkbox.toggled.connect(lambda _checked: self.apply_edge_band_checkbox.setChecked(True))

        self.apply_material_checkbox.toggled.connect(self.material_edit.setEnabled)
        self.apply_cut_method_checkbox.toggled.connect(self.cut_method_combo.setEnabled)
        self.apply_rotation_checkbox.toggled.connect(self.rotation_combo.setEnabled)
        self.apply_grain_checkbox.toggled.connect(self.grain_direction_combo.setEnabled)
        self.apply_edge_band_checkbox.toggled.connect(edge_band_group.setEnabled)

        self.apply_material_checkbox.toggled.connect(self._update_accept_state)
        self.apply_cut_method_checkbox.toggled.connect(self._update_accept_state)
        self.apply_rotation_checkbox.toggled.connect(self._update_accept_state)
        self.apply_grain_checkbox.toggled.connect(self._update_accept_state)
        self.apply_edge_band_checkbox.toggled.connect(self._update_accept_state)

        self.material_edit.setEnabled(self.apply_material_checkbox.isChecked())
        self.cut_method_combo.setEnabled(self.apply_cut_method_checkbox.isChecked())
        self.rotation_combo.setEnabled(self.apply_rotation_checkbox.isChecked())
        self.grain_direction_combo.setEnabled(self.apply_grain_checkbox.isChecked())
        edge_band_group.setEnabled(self.apply_edge_band_checkbox.isChecked())
        self._update_accept_state()
        self.finished.connect(self._persist_dialog_size)

        panelnest.restore_dialog_size(
            self,
            self._dialog_size_key,
            default_width=max(620, self.sizeHint().width() + 24),
            default_height=max(420, self.sizeHint().height() + 24),
            minimum_width=540,
            minimum_height=360,
        )

    def _has_any_checked_field(self):
        return any(
            (
                self.apply_material_checkbox.isChecked(),
                self.apply_cut_method_checkbox.isChecked(),
                self.apply_rotation_checkbox.isChecked(),
                self.apply_grain_checkbox.isChecked(),
                self.apply_edge_band_checkbox.isChecked(),
            )
        )

    def _update_accept_state(self):
        if self._accept_button is not None:
            self._accept_button.setEnabled(self._has_any_checked_field())

    def payload(self):
        data = {}
        if self.apply_material_checkbox.isChecked():
            data["material"] = self.material_edit.text().strip()
        if self.apply_cut_method_checkbox.isChecked():
            data["cut_method"] = self.cut_method_combo.currentText()
        if self.apply_rotation_checkbox.isChecked():
            data["allow_rotation"] = bool(self.rotation_combo.currentData())
        if self.apply_grain_checkbox.isChecked():
            data["grain_direction"] = self.grain_direction_combo.currentText()
        if self.apply_edge_band_checkbox.isChecked():
            data["edge_band_top"] = self.edge_top_checkbox.isChecked()
            data["edge_band_bottom"] = self.edge_bottom_checkbox.isChecked()
            data["edge_band_left"] = self.edge_left_checkbox.isChecked()
            data["edge_band_right"] = self.edge_right_checkbox.isChecked()
        return data

    def dialog_prefs(self):
        return {
            "apply_material": self.apply_material_checkbox.isChecked(),
            "apply_cut_method": self.apply_cut_method_checkbox.isChecked(),
            "apply_rotation": self.apply_rotation_checkbox.isChecked(),
            "apply_grain": self.apply_grain_checkbox.isChecked(),
            "apply_edge_band": self.apply_edge_band_checkbox.isChecked(),
            "material": self.material_edit.text().strip(),
            "cut_method": self.cut_method_combo.currentText(),
            "allow_rotation": bool(self.rotation_combo.currentData()),
            "grain_direction": self.grain_direction_combo.currentText(),
            "edge_band_top": self.edge_top_checkbox.isChecked(),
            "edge_band_bottom": self.edge_bottom_checkbox.isChecked(),
            "edge_band_left": self.edge_left_checkbox.isChecked(),
            "edge_band_right": self.edge_right_checkbox.isChecked(),
        }

    def _persist_dialog_size(self, _result):
        import panelnest

        panelnest.save_dialog_size(self, self._dialog_size_key)


class PreparePartMetadataCommand:
    def Activated(self):
        import panelnest

        try:
            source_objects = panelnest.get_part_source_objects()
            prepared_parts = panelnest.prepare_parts_metadata(source_objects)
            apply_prefs = panelnest.get_apply_dialog_prefs()
        except ValueError as exc:
            if App is not None:
                App.Console.PrintError(f"PanelNest: {exc}\n")
            return

        first_part = prepared_parts[0]
        first_object = App.ActiveDocument.getObject(first_part.object_name)
        edge_band_flags = (
            first_part.edge_band_top,
            first_part.edge_band_bottom,
            first_part.edge_band_left,
            first_part.edge_band_right,
        )
        dialog = ApplyMetadataDialog(
            target_count=len(prepared_parts),
            initial_material=first_part.material or getattr(first_object, "PanelNestMaterial", "MDF"),
            initial_cut_method=first_part.cut_method or str(getattr(first_object, "PanelNestCutMethod", "CNC")),
            initial_allow_rotation=first_part.allow_rotation,
            initial_grain_direction=panelnest.normalize_grain_direction(first_part.grain_direction),
            apply_prefs=apply_prefs,
            edge_band_flags=edge_band_flags,
            cut_method_options=panelnest.CUT_METHOD_OPTIONS,
            grain_direction_options=panelnest.GRAIN_DIRECTION_OPTIONS,
            parent=Gui.getMainWindow() if Gui is not None and hasattr(Gui, "getMainWindow") else None,
        )
        if _exec_dialog(dialog) != QtWidgets.QDialog.Accepted:
            return

        panelnest.update_apply_dialog_prefs(**dialog.dialog_prefs())

        payload = dialog.payload()
        if not payload:
            if App is not None:
                App.Console.PrintMessage("PanelNest: nenhuma alteracao foi selecionada.\n")
            return

        try:
            updated_parts = panelnest.apply_metadata_values(source_objects, **payload)
        except ValueError as exc:
            if App is not None:
                App.Console.PrintError(f"PanelNest: {exc}\n")
            return

        if App is not None:
            App.Console.PrintMessage(
                f"PanelNest: aplicou dados em {len(updated_parts)} peca(s).\n"
            )

        # Detecção automática de ferragens por diâmetro de furo
        try:
            from panelnest.parts import apply_detected_hardware
            settings = panelnest.get_sheet_settings()
            hw_count, hw_parts = apply_detected_hardware(updated_parts, settings.hardware_catalog)
            if hw_count > 0 and App is not None:
                App.Console.PrintMessage(
                    f"PanelNest: detectou {hw_count} ferragem(ns) automaticamente em {hw_parts} peça(s).\n"
                )
        except Exception:
            pass

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
                os.path.dirname(__file__), "..", "resources", "icons", "panelnest_metadata.svg"
            ),
            "Accel": "",
            "MenuText": "Aplicar Dados",
            "ToolTip": "Aplica material, metodo de corte, rotacao, veio/fibra e fita de borda nas pecas selecionadas ou visiveis.",
        }


if Gui is not None:
    Gui.addCommand(COMMAND_NAME, PreparePartMetadataCommand())
