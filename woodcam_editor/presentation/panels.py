"""Layer management and exact numeric property panels."""

from __future__ import annotations

from dataclasses import replace
import math

from .compat import Signal, QtCore, QtWidgets, qt_enum
from woodcam_editor.application.pieces import piece_rotation_mode
from woodcam_editor.domain import CircleEntity, EllipseEntity, Vec2


class LayerPanel(QtWidgets.QGroupBox):
    message = Signal(str)

    def __init__(self, controller, parent=None):
        super(LayerPanel, self).__init__("Camadas", parent)
        self.controller = controller
        self._refreshing = False
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 8, 6, 6)
        self.tree = QtWidgets.QTreeWidget(self)
        self.tree.setHeaderLabels(("Ativa", "Camada", "Visível", "Bloq."))
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setMinimumHeight(130)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        layout.addWidget(self.tree)
        buttons = QtWidgets.QHBoxLayout()
        self.add_button = QtWidgets.QPushButton("+ Camada", self)
        self.add_button.clicked.connect(self._add_layer)
        self.move_button = QtWidgets.QPushButton("Mover seleção", self)
        self.move_button.clicked.connect(self._move_selection)
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.move_button)
        layout.addLayout(buttons)
        controller.subscribe_document(lambda _change=None: self.refresh())
        controller.selection.subscribe(lambda _ids: self._sync_buttons())
        self.refresh()

    def refresh(self):
        self._refreshing = True
        try:
            self.tree.clear()
            layers = sorted(
                self.controller.document.layers_by_id.values(),
                key=lambda layer: (layer.order, layer.name.lower(), layer.id),
            )
            used_layer_ids = {
                str(getattr(entity, "layer_id", ""))
                for entity in self.controller.document.entities_by_id.values()
            }
            layers = [
                layer
                for layer in layers
                if str(layer.id) in used_layer_ids
                or not str((getattr(layer, "metadata", {}) or {}).get("source_kind", ""))
            ]
            # ``Caixa SketchBody`` is provenance for the FreeCAD snapshot,
            # not an additional manufacturing operation. Once canonical CAM
            # role layers exist, keep this bookkeeping row out of the compact
            # production panel while leaving the source layer/data intact.
            has_role_layer = any(
                str(getattr(layer, "purpose", "") or "") in {"cut", "pocket", "drill"}
                or str(getattr(layer, "name", "") or "") in {"Corte externo", "Corte interno", "Furos"}
                for layer in layers
            )
            if has_role_layer:
                layers = [
                    layer
                    for layer in layers
                    if not (
                        str(getattr(layer, "purpose", "") or "") == "design"
                        and (
                            "sketch" in str(getattr(layer, "name", "") or "").lower()
                            or str((getattr(layer, "metadata", {}) or {}).get("source_kind", "") or "")
                            == "freecad_import"
                        )
                    )
                ]
            # Older imports created one manufacturing layer per source board
            # (``<source>:cut_external``).  Keep those documents compatible,
            # but present a single semantic row for each CAM role.  The row's
            # visibility/lock controls are applied to every underlying layer.
            grouped = {}
            for layer in layers:
                purpose = str(getattr(layer, "purpose", "design") or "design")
                name = str(getattr(layer, "name", "") or "")
                if purpose in {"cut", "pocket", "drill"} or name in {
                    "Corte externo", "Corte interno", "Furos"
                }:
                    group_key = ("cam-role", purpose, name)
                else:
                    group_key = ("layer", str(layer.id))
                grouped.setdefault(group_key, []).append(layer)
            groups = sorted(
                grouped.values(),
                key=lambda values: (
                    min(int(getattr(layer, "order", 0) or 0) for layer in values),
                    str(getattr(values[0], "name", "") or "").lower(),
                ),
            )
            role = qt_enum(QtCore.Qt, "UserRole", "ItemDataRole")
            for layer_group in groups:
                layer = layer_group[0]
                layer_ids = tuple(str(value.id) for value in layer_group)
                item = QtWidgets.QTreeWidgetItem(self.tree)
                item.setData(0, role, layer_ids)
                item.setText(1, layer.name)
                if len(layer_group) > 1:
                    item.setToolTip(
                        1,
                        "Camada CAM consolidada (%d origens importadas)" % len(layer_group),
                    )
                active = QtWidgets.QRadioButton(self.tree)
                active.setChecked(
                    self.controller.document.active_layer_id in layer_ids
                )
                active.setToolTip("Usar para novos vetores")
                active.toggled.connect(
                    lambda checked, values=layer_ids: self._set_active(values[0])
                    if checked else None
                )
                self.tree.setItemWidget(item, 0, active)
                visible = QtWidgets.QCheckBox(self.tree)
                visible.setChecked(all(bool(value.visible) for value in layer_group))
                visible.toggled.connect(
                    lambda checked, values=layer_ids: self._set_visible(values, checked)
                )
                self.tree.setItemWidget(item, 2, visible)
                locked = QtWidgets.QCheckBox(self.tree)
                locked.setChecked(all(bool(value.locked) for value in layer_group))
                locked.toggled.connect(
                    lambda checked, values=layer_ids: self._set_locked(values, checked)
                )
                self.tree.setItemWidget(item, 3, locked)
            self.tree.resizeColumnToContents(0)
            self.tree.resizeColumnToContents(2)
            self.tree.resizeColumnToContents(3)
        finally:
            self._refreshing = False
        self._sync_buttons()

    def _selected_layer_id(self):
        item = self.tree.currentItem()
        role = qt_enum(QtCore.Qt, "UserRole", "ItemDataRole")
        value = item.data(0, role) if item is not None else self.controller.document.active_layer_id
        if isinstance(value, (tuple, list)):
            return value[0] if value else None
        return value

    def _set_active(self, layer_id):
        if self._refreshing:
            return
        self.controller.set_active_layer(layer_id)

    def _set_visible(self, layer_id, visible):
        if self._refreshing:
            return
        layer_ids = layer_id if isinstance(layer_id, (tuple, list)) else (layer_id,)
        for value in layer_ids:
            self.controller.update_layer(value, visible=bool(visible))

    def _set_locked(self, layer_id, locked):
        if self._refreshing:
            return
        layer_ids = layer_id if isinstance(layer_id, (tuple, list)) else (layer_id,)
        if locked and self.controller.document.active_layer_id in layer_ids:
            self.message.emit("Ative outra camada antes de bloquear esta.")
            self.refresh()
            return
        for value in layer_ids:
            self.controller.update_layer(value, locked=bool(locked))

    def _add_layer(self):
        self.controller.add_layer()

    def _move_selection(self):
        layer_id = self._selected_layer_id()
        if layer_id:
            self.controller.move_selection_to_layer(layer_id)

    def _sync_buttons(self):
        self.move_button.setEnabled(bool(self.controller.selection.ids))


class ExactPropertiesPanel(QtWidgets.QGroupBox):
    message = Signal(str)

    def __init__(self, controller, parent=None):
        super(ExactPropertiesPanel, self).__init__("Posição e dimensões exatas", parent)
        self.controller = controller
        self._bounds = None
        self._syncing = False
        self._dimension_driver = "width"
        form = QtWidgets.QFormLayout(self)
        form.setContentsMargins(6, 8, 6, 6)
        self.x_field = self._field()
        self.y_field = self._field()
        self.width_field = self._field(minimum=0.0)
        self.height_field = self._field(minimum=0.0)
        form.addRow("X mínimo (mm)", self.x_field)
        form.addRow("Y mínimo (mm)", self.y_field)
        self.width_label = QtWidgets.QLabel("Largura (mm)", self)
        self.height_label = QtWidgets.QLabel("Altura (mm)", self)
        form.addRow(self.width_label, self.width_field)
        form.addRow(self.height_label, self.height_field)
        self.keep_ratio = QtWidgets.QCheckBox(
            "Manter proporção ao alterar dimensões", self
        )
        self.keep_ratio.setChecked(False)
        self.keep_ratio.setToolTip(
            "Quando marcado, a última dimensão editada (largura ou altura) "
            "define a outra. Desmarcado permite medidas independentes."
        )
        form.addRow("", self.keep_ratio)

        self.primitive_box = QtWidgets.QGroupBox("Propriedades da forma", self)
        primitive_form = QtWidgets.QFormLayout(self.primitive_box)
        primitive_form.setContentsMargins(5, 6, 5, 5)
        self.radius_label = QtWidgets.QLabel("Raio (mm)", self.primitive_box)
        self.radius_field = self._positive_field(self.primitive_box)
        self.diameter_label = QtWidgets.QLabel("Diâmetro (mm)", self.primitive_box)
        self.diameter_field = self._positive_field(self.primitive_box)
        self.radius_x_label = QtWidgets.QLabel("Raio X (mm)", self.primitive_box)
        self.radius_x_field = self._positive_field(self.primitive_box)
        self.radius_y_label = QtWidgets.QLabel("Raio Y (mm)", self.primitive_box)
        self.radius_y_field = self._positive_field(self.primitive_box)
        self.rotation_label = QtWidgets.QLabel("Rotação (°)", self.primitive_box)
        self.rotation_field = self._field(parent=self.primitive_box)
        self.rotation_field.setRange(-3600.0, 3600.0)
        self.rotation_field.setSuffix("°")
        for label, field in (
            (self.radius_label, self.radius_field),
            (self.diameter_label, self.diameter_field),
            (self.radius_x_label, self.radius_x_field),
            (self.radius_y_label, self.radius_y_field),
            (self.rotation_label, self.rotation_field),
        ):
            primitive_form.addRow(label, field)
        form.addRow(self.primitive_box)

        self.width_field.valueChanged.connect(
            lambda _value: self._dimension_changed("width")
        )
        self.height_field.valueChanged.connect(
            lambda _value: self._dimension_changed("height")
        )
        self.radius_field.valueChanged.connect(self._radius_changed)
        self.diameter_field.valueChanged.connect(self._diameter_changed)
        self.apply_button = QtWidgets.QPushButton("Aplicar medidas", self)
        self.apply_button.clicked.connect(self.apply)
        form.addRow(self.apply_button)
        self.info_label = QtWidgets.QLabel("Selecione um vetor.", self)
        self.info_label.setWordWrap(True)
        form.addRow(self.info_label)
        controller.selection.subscribe(lambda _ids: self.refresh())
        controller.subscribe_document(lambda _change=None: self.refresh())
        self.refresh()

    def _field(self, minimum=-1.0e9, parent=None):
        field = QtWidgets.QDoubleSpinBox(parent or self)
        field.setDecimals(4)
        field.setRange(minimum, 1.0e9)
        field.setSingleStep(1.0)
        field.setKeyboardTracking(False)
        return field

    def _positive_field(self, parent=None):
        field = self._field(minimum=0.0001, parent=parent)
        field.setSuffix(" mm")
        return field

    @staticmethod
    def _set_row_visible(label, field, visible):
        label.setVisible(bool(visible))
        field.setVisible(bool(visible))

    def _dimension_changed(self, driver):
        if self._syncing:
            return
        self._dimension_driver = str(driver)
        bounds = self._bounds
        if not self.keep_ratio.isChecked() or bounds is None:
            return
        if bounds.width <= 1.0e-12 or bounds.height <= 1.0e-12:
            return
        self._syncing = True
        try:
            if driver == "height":
                self.width_field.setValue(
                    self.height_field.value() * bounds.width / bounds.height
                )
            else:
                self.height_field.setValue(
                    self.width_field.value() * bounds.height / bounds.width
                )
        finally:
            self._syncing = False

    def _radius_changed(self, value):
        if self._syncing:
            return
        self._syncing = True
        try:
            self.diameter_field.setValue(float(value) * 2.0)
        finally:
            self._syncing = False

    def _diameter_changed(self, value):
        if self._syncing:
            return
        self._syncing = True
        try:
            self.radius_field.setValue(float(value) * 0.5)
        finally:
            self._syncing = False

    def refresh(self):
        bounds = self.controller.selection_bounds()
        self._bounds = bounds
        enabled = bounds is not None
        for widget in (
            self.x_field,
            self.y_field,
            self.width_field,
            self.height_field,
            self.keep_ratio,
            self.apply_button,
        ):
            widget.setEnabled(enabled)
        if not enabled:
            self.primitive_box.hide()
            self._set_generic_dimensions_visible(True)
            self.info_label.setText("Selecione um vetor ou grupo de vetores.")
            return
        entities = [
            self.controller.get_entity(value)
            for value in self.controller.selection.ids
        ]
        primitive = entities[0] if len(entities) == 1 else None
        if not isinstance(primitive, (CircleEntity, EllipseEntity)):
            primitive = None
        self._set_generic_dimensions_visible(primitive is None)
        self.primitive_box.setVisible(primitive is not None)

        widgets = (
            self.x_field,
            self.y_field,
            self.width_field,
            self.height_field,
            self.radius_field,
            self.diameter_field,
            self.radius_x_field,
            self.radius_y_field,
            self.rotation_field,
        )
        self._syncing = True
        for widget in widgets:
            widget.blockSignals(True)
        self.x_field.setValue(bounds.min_x)
        self.y_field.setValue(bounds.min_y)
        self.width_field.setValue(bounds.width)
        self.height_field.setValue(bounds.height)
        self.width_field.setEnabled(bounds.width > 1.0e-12)
        self.height_field.setEnabled(bounds.height > 1.0e-12)
        is_circle = isinstance(primitive, CircleEntity)
        is_ellipse = isinstance(primitive, EllipseEntity)
        self._set_row_visible(self.radius_label, self.radius_field, is_circle)
        self._set_row_visible(self.diameter_label, self.diameter_field, is_circle)
        self._set_row_visible(self.radius_x_label, self.radius_x_field, is_ellipse)
        self._set_row_visible(self.radius_y_label, self.radius_y_field, is_ellipse)
        self._set_row_visible(self.rotation_label, self.rotation_field, is_ellipse)
        if is_circle:
            self.radius_field.setValue(primitive.radius)
            self.diameter_field.setValue(primitive.radius * 2.0)
        elif is_ellipse:
            self.radius_x_field.setValue(primitive.radius_x)
            self.radius_y_field.setValue(primitive.radius_y)
            self.rotation_field.setValue(math.degrees(primitive.rotation))
        for widget in widgets:
            widget.blockSignals(False)
        self._syncing = False
        if primitive is None:
            description = "%d vetor(es)" % len(entities)
        elif is_circle:
            description = "Círculo: edite raio ou diâmetro."
        else:
            description = "Elipse: raios locais e rotação exatos."
        self.info_label.setText(
            "%s Uma aplicação gera um único Undo." % description
        )

    def _set_generic_dimensions_visible(self, visible):
        self._set_row_visible(self.width_label, self.width_field, visible)
        self._set_row_visible(self.height_label, self.height_field, visible)
        self.keep_ratio.setVisible(bool(visible))

    def apply(self):
        try:
            entities = [
                self.controller.get_entity(value)
                for value in self.controller.selection.ids
            ]
            primitive = entities[0] if len(entities) == 1 else None
            if isinstance(primitive, CircleEntity):
                replacement = replace(primitive, radius=self.radius_field.value())
                replacement_bounds = replacement.bounds()
                replacement = replace(
                    replacement,
                    center=replacement.center
                    + Vec2(
                        self.x_field.value() - replacement_bounds.min_x,
                        self.y_field.value() - replacement_bounds.min_y,
                    ),
                )
                if replacement != primitive:
                    self.controller.replace_entities_exact((replacement,))
            elif isinstance(primitive, EllipseEntity):
                replacement = replace(
                    primitive,
                    radius_x=self.radius_x_field.value(),
                    radius_y=self.radius_y_field.value(),
                    rotation=math.radians(self.rotation_field.value()),
                )
                replacement_bounds = replacement.bounds()
                replacement = replace(
                    replacement,
                    center=replacement.center
                    + Vec2(
                        self.x_field.value() - replacement_bounds.min_x,
                        self.y_field.value() - replacement_bounds.min_y,
                    ),
                )
                if replacement != primitive:
                    self.controller.replace_entities_exact((replacement,))
            else:
                width = self.width_field.value()
                height = self.height_field.value()
                bounds = self._bounds
                if (
                    self.keep_ratio.isChecked()
                    and bounds is not None
                    and bounds.width > 1.0e-12
                    and bounds.height > 1.0e-12
                ):
                    if self._dimension_driver == "height":
                        width = height * bounds.width / bounds.height
                        self.width_field.setValue(width)
                    else:
                        height = width * bounds.height / bounds.width
                        self.height_field.setValue(height)
                self.controller.set_selection_bounds(
                    self.x_field.value(),
                    self.y_field.value(),
                    width,
                    height,
                    preserve_ratio=False,
                )
        except Exception as error:
            self.message.emit("Não foi possível aplicar as medidas: %s" % error)
        self.refresh()


class TransformPanel(QtWidgets.QGroupBox):
    """Compact production transforms; every button commits one command."""

    message = Signal(str)

    def __init__(self, controller, parent=None):
        super(TransformPanel, self).__init__("Transformar / alinhar", parent)
        self.controller = controller
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(6, 8, 6, 6)
        root.setSpacing(4)

        rotate_row = QtWidgets.QHBoxLayout()
        rotate_row.addWidget(QtWidgets.QLabel("Ângulo", self))
        self.angle_field = QtWidgets.QDoubleSpinBox(self)
        self.angle_field.setRange(-3600.0, 3600.0)
        self.angle_field.setDecimals(3)
        self.angle_field.setSuffix("°")
        self.angle_field.setValue(90.0)
        rotate_row.addWidget(self.angle_field, 1)
        self.rotate_button = QtWidgets.QPushButton("Girar", self)
        self.rotate_button.clicked.connect(
            lambda: self._run(self.controller.rotate_selection, self.angle_field.value())
        )
        rotate_row.addWidget(self.rotate_button)
        root.addLayout(rotate_row)

        mirror_row = QtWidgets.QHBoxLayout()
        self.mirror_h_button = QtWidgets.QPushButton("Espelho H ↕", self)
        self.mirror_h_button.setToolTip("Espelhar sobre a linha horizontal no centro da seleção")
        self.mirror_h_button.clicked.connect(
            lambda: self._run(self.controller.mirror_selection, "horizontal")
        )
        self.mirror_v_button = QtWidgets.QPushButton("Espelho V ↔", self)
        self.mirror_v_button.setToolTip("Espelhar sobre a linha vertical no centro da seleção")
        self.mirror_v_button.clicked.connect(
            lambda: self._run(self.controller.mirror_selection, "vertical")
        )
        mirror_row.addWidget(self.mirror_h_button)
        mirror_row.addWidget(self.mirror_v_button)
        root.addLayout(mirror_row)

        root.addWidget(QtWidgets.QLabel("Alinhar seleção", self))
        align_grid = QtWidgets.QGridLayout()
        align_specs = (
            ("Esq.", "left", 0, 0),
            ("Centro", "center", 0, 1),
            ("Dir.", "right", 0, 2),
            ("Baixo", "bottom", 1, 0),
            ("Meio", "middle", 1, 1),
            ("Cima", "top", 1, 2),
        )
        self.align_buttons = []
        for label, alignment, row, column in align_specs:
            button = QtWidgets.QPushButton(label, self)
            button.clicked.connect(
                lambda _checked=False, value=alignment: self._run(
                    self.controller.align_selection, value
                )
            )
            align_grid.addWidget(button, row, column)
            self.align_buttons.append(button)
        root.addLayout(align_grid)

        distribute_row = QtWidgets.QHBoxLayout()
        distribute_row.addWidget(QtWidgets.QLabel("Distribuir", self))
        self.distribute_h_button = QtWidgets.QPushButton("Horizontal", self)
        self.distribute_h_button.clicked.connect(
            lambda: self._run(self.controller.distribute_selection, "horizontal")
        )
        self.distribute_v_button = QtWidgets.QPushButton("Vertical", self)
        self.distribute_v_button.clicked.connect(
            lambda: self._run(self.controller.distribute_selection, "vertical")
        )
        distribute_row.addWidget(self.distribute_h_button)
        distribute_row.addWidget(self.distribute_v_button)
        root.addLayout(distribute_row)

        # O Aspire chama esta operação de Array Copy.  Ela gera vetores novos
        # (nunca instâncias gráficas soltas) e portanto entra no Undo normal.
        root.addWidget(QtWidgets.QLabel("Copiar em matriz", self))
        array_grid = QtWidgets.QGridLayout()
        array_grid.setContentsMargins(0, 0, 0, 0)
        array_grid.addWidget(QtWidgets.QLabel("Colunas", self), 0, 0)
        self.array_columns = QtWidgets.QSpinBox(self)
        self.array_columns.setRange(1, 200)
        self.array_columns.setValue(2)
        self.array_columns.setToolTip("Quantidade total de colunas, incluindo o original")
        array_grid.addWidget(self.array_columns, 0, 1)
        array_grid.addWidget(QtWidgets.QLabel("Linhas", self), 0, 2)
        self.array_rows = QtWidgets.QSpinBox(self)
        self.array_rows.setRange(1, 200)
        self.array_rows.setValue(1)
        self.array_rows.setToolTip("Quantidade total de linhas, incluindo o original")
        array_grid.addWidget(self.array_rows, 0, 3)
        array_grid.addWidget(QtWidgets.QLabel("ΔX", self), 1, 0)
        self.array_dx = QtWidgets.QDoubleSpinBox(self)
        self.array_dx.setRange(-1000000.0, 1000000.0)
        self.array_dx.setDecimals(3)
        self.array_dx.setValue(100.0)
        self.array_dx.setSuffix(" mm")
        array_grid.addWidget(self.array_dx, 1, 1)
        array_grid.addWidget(QtWidgets.QLabel("ΔY", self), 1, 2)
        self.array_dy = QtWidgets.QDoubleSpinBox(self)
        self.array_dy.setRange(-1000000.0, 1000000.0)
        self.array_dy.setDecimals(3)
        self.array_dy.setValue(0.0)
        self.array_dy.setSuffix(" mm")
        array_grid.addWidget(self.array_dy, 1, 3)
        root.addLayout(array_grid)
        self.array_button = QtWidgets.QPushButton("Copiar matriz", self)
        self.array_button.setToolTip(
            "Cria cópias exatas da seleção; furos e recortes de uma peça agrupada acompanham a chapa."
        )
        self.array_button.clicked.connect(self._copy_array)
        root.addWidget(self.array_button)

        controller.selection.subscribe(lambda _ids: self.refresh())
        controller.subscribe_document(lambda _change=None: self.refresh())
        self.refresh()

    def _run(self, callback, *args):
        try:
            changed = callback(*args)
            if changed is False:
                self.message.emit("A seleção não exige essa transformação.")
        except Exception as error:
            self.message.emit("Transformação não aplicada: %s" % error)
        self.refresh()

    def _copy_array(self):
        columns = self.array_columns.value()
        rows = self.array_rows.value()
        if columns * rows < 2:
            self.message.emit("Informe ao menos 2 posições na matriz.")
            return
        self._run(
            self.controller.array_copy_selection,
            columns,
            rows,
            self.array_dx.value(),
            self.array_dy.value(),
        )

    def refresh(self):
        count = len(self.controller.selection)
        for button in (self.rotate_button, self.mirror_h_button, self.mirror_v_button):
            button.setEnabled(count >= 1)
        work_area_available = self.controller.document.work_area is not None
        for button in self.align_buttons:
            button.setEnabled(count >= 2 or (count == 1 and work_area_available))
            if count == 1 and work_area_available:
                button.setToolTip("Alinhar o vetor selecionado à área de Trabalho")
            else:
                button.setToolTip("Alinhar os vetores selecionados entre si")
        self.distribute_h_button.setEnabled(count >= 3)
        self.distribute_v_button.setEnabled(count >= 3)
        self.array_button.setEnabled(count >= 1)


class ModifierParametersPanel(QtWidgets.QGroupBox):
    """Live parameters consumed by preview-first modifier tools."""

    parametersChanged = Signal(object)
    applyAutomaticRequested = Signal()

    def __init__(self, parent=None):
        super(ModifierParametersPanel, self).__init__("Reparos / filetes", parent)
        form = QtWidgets.QFormLayout(self)
        form.setContentsMargins(6, 8, 6, 6)
        self.offset_field = QtWidgets.QDoubleSpinBox(self)
        self.offset_field.setRange(-10000.0, 10000.0)
        self.offset_field.setDecimals(3)
        self.offset_field.setValue(5.0)
        self.offset_field.setSuffix(" mm")
        self.offset_field.setToolTip("Positivo: externo; negativo: interno")
        form.addRow("Offset", self.offset_field)
        self.radius_field = QtWidgets.QDoubleSpinBox(self)
        self.radius_field.setRange(0.001, 10000.0)
        self.radius_field.setDecimals(3)
        self.radius_field.setValue(3.0)
        self.radius_field.setSuffix(" mm")
        form.addRow("Raio", self.radius_field)
        self.contour_role = QtWidgets.QComboBox(self)
        self.contour_role.addItem("Detectar pela peça", "auto")
        self.contour_role.addItem("Recorte / furo interno", "inner")
        self.contour_role.addItem("Contorno externo / entalhe", "outer")
        self.contour_role.setToolTip(
            "Automático usa a peça classificada ou a contenção dos contornos. "
            "Escolha manualmente somente quando o desenho ainda não estiver classificado."
        )
        form.addRow("Papel do contorno", self.contour_role)
        self.tbone_side = QtWidgets.QComboBox(self)
        self.tbone_side.addItem("Automático", "auto")
        self.tbone_side.addItem("Lado de entrada", "incoming")
        self.tbone_side.addItem("Lado de saída", "outgoing")
        form.addRow("Lado T-bone", self.tbone_side)
        self.splice_route = QtWidgets.QComboBox(self)
        self.splice_route.addItem("Curto", "short")
        self.splice_route.addItem("Longo", "long")
        self.splice_route.setToolTip(
            "Escolhe qual trecho do contorno fechado será mantido na emenda."
        )
        form.addRow("Rota da emenda", self.splice_route)
        # This tolerance belongs to the endpoint-join tools.  Keep it beside
        # the active repair parameters instead of consuming the permanent
        # Editor toolbar with a control that is unrelated to most operations.
        self.join_tolerance_row = QtWidgets.QWidget(self)
        join_layout = QtWidgets.QHBoxLayout(self.join_tolerance_row)
        join_layout.setContentsMargins(0, 0, 0, 0)
        self.join_tolerance = QtWidgets.QDoubleSpinBox(self.join_tolerance_row)
        self.join_tolerance.setRange(0.001, 1000.0)
        self.join_tolerance.setDecimals(3)
        self.join_tolerance.setValue(0.2)
        self.join_tolerance.setSuffix(" mm")
        self.join_tolerance.setToolTip(
            "Tolerância para unir pontas; não é o raio de captura do mouse."
        )
        join_layout.addWidget(QtWidgets.QLabel("Tolerância unir", self.join_tolerance_row))
        join_layout.addWidget(self.join_tolerance, 1)
        form.addRow(self.join_tolerance_row)
        self.join_tolerance_row.hide()
        self.info_label = QtWidgets.QLabel(
            "Passe o mouse na geometria; magenta é somente prévia. Clique aplica.", self
        )
        self.info_label.setWordWrap(True)
        form.addRow(self.info_label)
        self.apply_automatic_button = QtWidgets.QPushButton(
            "Aplicar prévia automática", self
        )
        self.apply_automatic_button.setEnabled(False)
        self.apply_automatic_button.hide()
        self.apply_automatic_button.clicked.connect(self.applyAutomaticRequested.emit)
        form.addRow(self.apply_automatic_button)
        self.offset_field.valueChanged.connect(self._emit)
        self.radius_field.valueChanged.connect(self._emit)
        self.contour_role.currentIndexChanged.connect(self._emit)
        self.tbone_side.currentIndexChanged.connect(self._emit)
        self.splice_route.currentIndexChanged.connect(self._emit)
        self.join_tolerance.valueChanged.connect(self._emit)

    def parameters(self):
        return {
            "offset_distance": float(self.offset_field.value()),
            "fillet_radius": float(self.radius_field.value()),
            "contour_role": str(self.contour_role.currentData()),
            "tbone_side": str(self.tbone_side.currentData()),
            "splice_route": str(self.splice_route.currentData()),
            "join_tolerance": float(self.join_tolerance.value()),
        }

    def _emit(self, *_args):
        self.parametersChanged.emit(self.parameters())

    def set_mode(self, mode):
        value = getattr(mode, "value", str(mode))
        messages = {
            "trim": "Trim: aponte o trecho entre duas interseções e clique.",
            "extend": "Extend: aponte uma ponta aberta e clique.",
            "offset": "Offset: aponte um contorno linear fechado e clique.",
            "fillet": "Filete: aponte um canto fechado e clique.",
            "dogbone": "Dogbone: aponte o canto; o papel interno/externo é detectado pela peça.",
            "tbone": "T-bone: aponte o canto; escolha o lado ou mantenha Automático.",
            "join_endpoints": "Unir 2 pontas: clique uma ponta e depois a ponta exata do outro caminho.",
            "join_endpoints_smooth": "Unir 2 pontas suave: preserva as pontas e cria uma curva tangente entre elas.",
            "connect": "Projetar ponta: clique a ponta fonte; depois uma reta, arco ou círculo alvo.",
            "splice": "Emendar: clique o caminho aberto; depois escolha o contorno alvo.",
            "auto_dogbone": "Dogbone automático: revise a prévia total e confirme.",
            "auto_tbone": "T-bone automático: revise a prévia total e confirme.",
        }
        self.info_label.setText(messages.get(value, "Magenta é prévia; clique aplica; Esc cancela."))
        self.join_tolerance_row.setVisible(
            value in ("join_endpoints", "join_endpoints_smooth")
        )
        automatic = value in ("auto_dogbone", "auto_tbone")
        self.apply_automatic_button.setVisible(automatic)
        if not automatic:
            self.apply_automatic_button.setEnabled(False)

    def set_auto_preview_available(self, available):
        self.apply_automatic_button.setEnabled(bool(available))


class PiecesPanel(QtWidgets.QGroupBox):
    """Piece metadata editor; inner contours are never listed as pieces."""

    pieceSelected = Signal(str)
    pieceUpdated = Signal(str)
    message = Signal(str)

    def __init__(self, controller, parent=None):
        super(PiecesPanel, self).__init__("Peças", parent)
        self.controller = controller
        self._refreshing = False
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(6, 8, 6, 6)
        root.setSpacing(4)
        self.list = QtWidgets.QListWidget(self)
        self.list.setMinimumHeight(100)
        self.list.setToolTip(
            "Cada linha é uma peça externa; furos e recortes internos seguem junto."
        )
        self.list.itemSelectionChanged.connect(self._selection_changed)
        root.addWidget(self.list)

        form = QtWidgets.QFormLayout()
        self.name_field = QtWidgets.QLineEdit(self)
        self.quantity_field = QtWidgets.QSpinBox(self)
        self.quantity_field.setRange(1, 100000)
        self.material_field = QtWidgets.QLineEdit(self)
        self.thickness_field = QtWidgets.QDoubleSpinBox(self)
        self.thickness_field.setRange(0.0, 10000.0)
        self.thickness_field.setDecimals(3)
        self.thickness_field.setSuffix(" mm")
        form.addRow("Nome", self.name_field)
        form.addRow("Quantidade", self.quantity_field)
        form.addRow("Material", self.material_field)
        form.addRow("Espessura", self.thickness_field)

        grain_widget = QtWidgets.QWidget(self)
        grain_layout = QtWidgets.QHBoxLayout(grain_widget)
        grain_layout.setContentsMargins(0, 0, 0, 0)
        self.grain_enabled = QtWidgets.QCheckBox("Definir", grain_widget)
        self.grain_angle = QtWidgets.QDoubleSpinBox(grain_widget)
        self.grain_angle.setRange(-360.0, 360.0)
        self.grain_angle.setDecimals(1)
        self.grain_angle.setSuffix("°")
        self.grain_enabled.toggled.connect(self.grain_angle.setEnabled)
        grain_layout.addWidget(self.grain_enabled)
        grain_layout.addWidget(self.grain_angle, 1)
        form.addRow("Direção do veio", grain_widget)

        self.rotation_mode = QtWidgets.QComboBox(self)
        self.rotation_mode.addItem("Somente 0°", "zero")
        self.rotation_mode.addItem("0° / 90°", "zero_ninety")
        self.rotation_mode.addItem("Livre", "free")
        self.rotation_mode.addItem("Bloqueada", "locked")
        form.addRow("Rotação", self.rotation_mode)
        root.addLayout(form)
        self.apply_button = QtWidgets.QPushButton("Aplicar à peça", self)
        self.apply_button.clicked.connect(self.apply)
        root.addWidget(self.apply_button)
        self.summary_label = QtWidgets.QLabel("Nenhuma peça classificada.", self)
        self.summary_label.setWordWrap(True)
        root.addWidget(self.summary_label)

        controller.subscribe_document(lambda _change=None: self.refresh())
        self.refresh()

    def _current_piece_id(self):
        item = self.list.currentItem()
        if item is None:
            return None
        return item.data(qt_enum(QtCore.Qt, "UserRole", "ItemDataRole"))

    def refresh(self):
        current_id = self._current_piece_id()
        self._refreshing = True
        try:
            self.list.clear()
            pieces = sorted(
                self.controller.document.pieces_by_id.values(),
                key=lambda piece: (piece.name.lower(), piece.id),
            )
            target_row = -1
            for row, piece in enumerate(pieces):
                item = QtWidgets.QListWidgetItem(
                    "%s  ×%d" % (piece.name, piece.quantity), self.list
                )
                item.setData(
                    qt_enum(QtCore.Qt, "UserRole", "ItemDataRole"), piece.id
                )
                item.setToolTip(
                    "1 externo + %d interno(s)" % len(piece.inner_path_ids)
                )
                if piece.id == current_id:
                    target_row = row
            if target_row >= 0:
                self.list.setCurrentRow(target_row)
            elif pieces:
                self.list.setCurrentRow(0)
        finally:
            self._refreshing = False
        self._load_current()

    def _selection_changed(self):
        if self._refreshing:
            return
        self._load_current()
        piece_id = self._current_piece_id()
        if piece_id:
            self.controller.select_piece(piece_id)
            self.pieceSelected.emit(str(piece_id))

    def _load_current(self):
        piece_id = self._current_piece_id()
        piece = self.controller.document.pieces_by_id.get(piece_id) if piece_id else None
        enabled = piece is not None
        for widget in (
            self.name_field,
            self.quantity_field,
            self.material_field,
            self.thickness_field,
            self.grain_enabled,
            self.grain_angle,
            self.rotation_mode,
            self.apply_button,
        ):
            widget.setEnabled(enabled)
        if not enabled:
            self.summary_label.setText("Nenhuma peça classificada.")
            return
        self.name_field.setText(piece.name)
        self.quantity_field.setValue(piece.quantity)
        self.material_field.setText(piece.material)
        self.thickness_field.setValue(piece.thickness)
        has_grain = piece.grain_direction is not None
        self.grain_enabled.setChecked(has_grain)
        self.grain_angle.setEnabled(has_grain)
        self.grain_angle.setValue(float(piece.grain_direction or 0.0))
        mode = piece_rotation_mode(piece)
        index = self.rotation_mode.findData(mode)
        self.rotation_mode.setCurrentIndex(max(0, index))
        self.summary_label.setText(
            "Contorno externo + %d furo(s)/recorte(s) interno(s)."
            % len(piece.inner_path_ids)
        )

    def apply(self):
        piece_id = self._current_piece_id()
        if not piece_id:
            return
        try:
            self.controller.update_piece_metadata(
                piece_id,
                name=self.name_field.text(),
                quantity=self.quantity_field.value(),
                material=self.material_field.text(),
                thickness=self.thickness_field.value(),
                grain_direction=(
                    self.grain_angle.value() if self.grain_enabled.isChecked() else None
                ),
                rotation_mode=str(self.rotation_mode.currentData()),
            )
        except Exception as error:
            self.message.emit("Não foi possível atualizar a peça: %s" % error)
            return
        self.pieceUpdated.emit(str(piece_id))


__all__ = [
    "ExactPropertiesPanel",
    "LayerPanel",
    "ModifierParametersPanel",
    "PiecesPanel",
    "TransformPanel",
]
