"""Complete embeddable Editor 2D widget.

``Editor2DWidget`` is the only class the legacy WoodCAM UI needs to know.  It
keeps the QGraphicsScene as a projection of ``VectorDocument`` and delegates
every persistent mutation to ``EditorController`` commands.
"""

from __future__ import annotations

from woodcam_editor.application import (
    CommandExecutionCancelled,
    EditorController,
    EditorMode,
)

from .compat import Signal, QtCore, QtGui, QtWidgets, qt_enum
from .icons import tool_icon
from .overlays import OverlayLayer
from .panels import (
    ExactPropertiesPanel,
    LayerPanel,
    ModifierParametersPanel,
    PiecesPanel,
    TransformPanel,
)
from .scene_adapter import SceneAdapter
from .tools import ToolManager
from .view import VectorGraphicsView
from .workflows import WorkflowPreviewBar


class Editor2DWidget(QtWidgets.QWidget):
    documentChanged = Signal(object)
    selectionChanged = Signal(object)
    modeChanged = Signal(str)
    importSketchRequested = Signal()
    diagnoseRequested = Signal()
    repairRequested = Signal()
    cleanupDuplicatesRequested = Signal()
    createPiecesRequested = Signal()
    organizePiecesRequested = Signal()
    organizePiecesFastRequested = Signal()
    organizePiecesThoroughRequested = Signal()
    useInCamRequested = Signal()
    sendPanelNestRequested = Signal()
    pieceSelected = Signal(str)
    trimRequested = Signal()
    extendRequested = Signal()
    offsetRequested = Signal()
    filletRequested = Signal()
    dogboneRequested = Signal()
    tboneRequested = Signal()
    importRequested = Signal()
    traceBitmapRequested = Signal()
    createReliefRequested = Signal()
    exportRequested = Signal()

    def __init__(
        self,
        document=None,
        parent=None,
        history=None,
        execute_command=None,
        undo=None,
        redo=None,
    ):
        super(Editor2DWidget, self).__init__(parent)
        if document is None:
            from woodcam_editor.domain import VectorDocument

            document = VectorDocument.create_default()
        self.controller = EditorController(
            document,
            history=history,
            execute_command=execute_command,
            undo=undo,
            redo=redo,
        )
        self.scene = QtWidgets.QGraphicsScene(self)
        self.view = VectorGraphicsView(self.scene, self)
        self.adapter = SceneAdapter(self.scene, document, self.controller.selection)
        self.overlays = OverlayLayer(self.scene)
        self.tool_manager = ToolManager(
            self.view,
            self.controller,
            self.adapter,
            self.overlays,
            self,
        )
        self._workflow_apply_callback = None
        self._workflow_revision = None
        self._mode_buttons = {}
        self._build_ui()
        self._connect_session()
        self.tool_manager.activate(EditorMode.SELECT)
        QtCore.QTimer.singleShot(0, self.fit_work_area)

    @property
    def document(self):
        return self.controller.document

    @property
    def selected_entity_ids(self):
        return self.controller.selection.ids

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(3)

        self.toolbar = QtWidgets.QToolBar(self)
        self.toolbar.setObjectName("editorTopToolbar")
        self.toolbar.setMovable(False)
        self.toolbar.setFloatable(False)
        self.toolbar.setIconSize(QtCore.QSize(16, 16))
        self.toolbar.setStyleSheet(
            "QToolBar { spacing: 2px; padding: 1px; border: 0; }"
            "QToolButton { padding: 2px; margin: 0; }"
        )
        root.addWidget(self.toolbar)

        self._menu_actions = {}
        self._button_group = QtWidgets.QButtonGroup(self)
        self._button_group.setExclusive(True)
        self.file_menu_button = self._add_menu_button(
            "Arquivo",
            (
                ("Importar itens da árvore…", self.importSketchRequested.emit),
                ("Importar arquivo", self.importRequested.emit),
                ("Vetorizar imagem…", self.traceBitmapRequested.emit),
                ("Criar relevo 3D por imagem…", self.createReliefRequested.emit),
                ("Exportar", self.exportRequested.emit),
            ),
        )
        self.repair_menu_button = self._add_menu_button(
            "Reparar",
            (
                ("Diagnosticar", self.diagnoseRequested.emit),
                ("Limpar sobrelinhas/duplicados…", self.cleanupDuplicatesRequested.emit),
                ("Fechar caminho / unir próximas", self.repairRequested.emit),
                (
                    "Unir 2 pontas (reta)",
                    lambda: self._activate_modifier(EditorMode.JOIN_ENDPOINTS),
                ),
                (
                    "Projetar ponta na geometria",
                    lambda: self._activate_modifier(EditorMode.CONNECT),
                ),
                (
                    "Emendar em contorno (Splice)",
                    lambda: self._activate_modifier(EditorMode.SPLICE),
                ),
                ("Trim interativo", lambda: self._activate_modifier(EditorMode.TRIM)),
                ("Estender", lambda: self._activate_modifier(EditorMode.EXTEND)),
                ("Offset", lambda: self._activate_modifier(EditorMode.OFFSET)),
            ),
        )
        self.fillet_menu_button = self._add_menu_button(
            "Filetes",
            (
                ("Filete normal", lambda: self._activate_modifier(EditorMode.FILLET)),
                ("Dogbone", lambda: self._activate_modifier(EditorMode.DOGBONE)),
                ("T-bone", lambda: self._activate_modifier(EditorMode.TBONE)),
                (
                    "Dogbone automático",
                    lambda: self._activate_modifier(EditorMode.AUTO_DOGBONE),
                ),
                (
                    "T-bone automático",
                    lambda: self._activate_modifier(EditorMode.AUTO_TBONE),
                ),
            ),
        )
        self.pieces_menu_button = self._add_menu_button(
            "Peças",
            (
                ("Reconhecer peças e furos", self.createPiecesRequested.emit),
                ("Organizar inteligente", self.organizePiecesRequested.emit),
                ("Organizar rápido", self.organizePiecesFastRequested.emit),
                ("Organizar profundo", self.organizePiecesThoroughRequested.emit),
            ),
        )
        recognize_action = self._menu_actions["Peças"][
            "Reconhecer peças e furos"
        ]
        recognize_action.setToolTip(
            "Reconhece a topologia existente: cada contorno externo vira uma peça "
            "e os contornos contidos viram furos ou recortes. Não cria geometria."
        )
        recognize_action.setStatusTip(recognize_action.toolTip())
        nesting_tips = {
            "Organizar inteligente": (
                "Perfil equilibrado: compara múltiplas ordens com MaxRects e "
                "contorno real, sempre com prévia antes de alterar o documento."
            ),
            "Organizar rápido": (
                "Compara menos layouts para responder mais rápido; mantém "
                "contorno real, furos vinculados, prévia e Undo."
            ),
            "Organizar profundo": (
                "Explora mais ordens de encaixe de forma determinística; pode "
                "demorar mais em conjuntos grandes."
            ),
        }
        for action_name, tip in nesting_tips.items():
            action = self._menu_actions["Peças"][action_name]
            action.setToolTip(tip)
            action.setStatusTip(tip)
        self.cam_menu_button = self._add_menu_button(
            "CAM",
            (
                ("Usar Editor 2D como fonte", self.useInCamRequested.emit),
                ("Enviar PanelNest", self.sendPanelNestRequested.emit),
            ),
        )
        self.use_cam_action = self._menu_actions["CAM"][
            "Usar Editor 2D como fonte"
        ]
        self.use_cam_action.setCheckable(True)
        # Compatibility for legacy integrations that only call setChecked/text.
        self.use_cam_button = self.use_cam_action

        self.toolbar.addSeparator()
        self.undo_button = self._add_compact_action_button(
            "Desfazer", "undo", self.controller.undo, "Desfazer (Ctrl+Z)"
        )
        self.redo_button = self._add_compact_action_button(
            "Refazer", "redo", self.controller.redo, "Refazer (Ctrl+Shift+Z)"
        )
        self.delete_button = self._add_compact_action_button(
            "Excluir", "delete", self.delete_selected, "Excluir selecionados (Delete)"
        )
        self.fit_button = self._add_compact_action_button(
            "Enquadrar", "fit", self.fit_work_area, "Enquadrar área de Trabalho (F)"
        )
        self.toolbar.addSeparator()

        self.snap_checkbox = QtWidgets.QCheckBox("Imã (Snap)", self)
        self.snap_checkbox.setToolTip(
            "Atrai o cursor para pontas, centros, interseções, geometria e grade. "
            "Desmarcar não oculta a grade."
        )
        self.snap_checkbox.setChecked(True)
        self.snap_checkbox.toggled.connect(self._toggle_snap)
        self.toolbar.addWidget(self.snap_checkbox)
        self.grid_spacing = QtWidgets.QDoubleSpinBox(self)
        self.grid_spacing.setObjectName("gridSpacing")
        self.grid_spacing.setAccessibleName("Espaçamento da grade")
        self.grid_spacing.setToolTip(
            "Espaçamento visual da grade e do encaixe na grade, em milímetros."
        )
        self.grid_spacing.setRange(0.01, 1000.0)
        self.grid_spacing.setDecimals(2)
        self.grid_spacing.setSuffix(" mm")
        self.grid_spacing.setValue(self.controller.snap_engine.settings.grid_spacing_mm)
        self.grid_spacing.setFixedWidth(68)
        self.grid_spacing.valueChanged.connect(self._set_grid_spacing)
        self.toolbar.addWidget(self.grid_spacing)
        self.toolbar.addSeparator()
        self.toolbar.addWidget(QtWidgets.QLabel(" Unir ", self))
        self.join_tolerance = QtWidgets.QDoubleSpinBox(self)
        self.join_tolerance.setRange(0.001, 1000.0)
        self.join_tolerance.setDecimals(3)
        self.join_tolerance.setValue(0.2)
        self.join_tolerance.setSuffix(" mm")
        self.join_tolerance.setToolTip(
            "Tolerância geométrica para unir pontas abertas; não é o raio de captura do mouse."
        )
        self.join_tolerance.setFixedWidth(68)
        self.join_tolerance.valueChanged.connect(
            lambda value: self.tool_manager.set_modifier_parameters(
                {"join_tolerance": float(value)}
            )
        )
        self.tool_manager.set_modifier_parameters(
            {"join_tolerance": float(self.join_tolerance.value())}
        )
        self.toolbar.addWidget(self.join_tolerance)
        self.use_cam_action.setToolTip(
            "Usa os vetores persistidos do Editor 2D como fonte das operações CAM; "
            "desmarcado, o WoodCAM continua usando a seleção do FreeCAD."
        )
        self.use_cam_action.setStatusTip(self.use_cam_action.toolTip())

        self.drawing_toolbar = QtWidgets.QToolBar("Desenho", self)
        self.drawing_toolbar.setObjectName("drawingToolBar")
        self.drawing_toolbar.setOrientation(
            qt_enum(QtCore.Qt, "Vertical", "Orientation")
        )
        self.drawing_toolbar.setMovable(False)
        self.drawing_toolbar.setFloatable(False)
        self.drawing_toolbar.setIconSize(QtCore.QSize(22, 22))
        self.drawing_toolbar.setFixedWidth(40)
        self.drawing_toolbar.setStyleSheet(
            "QToolBar { spacing: 2px; padding: 2px; border: 0; "
            "border-right: 1px solid #dbe4ef; }"
            "QToolButton { margin: 0; padding: 4px; }"
        )
        self._add_mode_button("Selecionar", EditorMode.SELECT, "S", "select")
        self._add_mode_button("Nós", EditorMode.NODE_EDIT, "N", "nodes")
        self.drawing_toolbar.addSeparator()
        self._add_mode_button("Linha", EditorMode.DRAW_LINE, "L", "line")
        self._add_mode_button("Polilinha", EditorMode.DRAW_POLYLINE, "P", "polyline")
        self._add_mode_button("Retângulo", EditorMode.DRAW_RECTANGLE, "R", "rectangle")
        self._add_mode_button("Círculo", EditorMode.DRAW_CIRCLE, "C", "circle")
        self._add_mode_button("Elipse", EditorMode.DRAW_ELLIPSE, "E", "ellipse")
        self._add_mode_button("Arco", EditorMode.DRAW_ARC, "A", "arc")
        self._add_mode_button("Polígono", EditorMode.DRAW_POLYGON, "G", "polygon")

        # Tool-specific options live above the canvas instead of consuming the
        # narrow vertical drawing rail.  This prevents the sides control from
        # being clipped on short task panels.
        self.polygon_context = QtWidgets.QFrame(self)
        self.polygon_context.setObjectName("polygonContextBar")
        polygon_context_layout = QtWidgets.QHBoxLayout(self.polygon_context)
        polygon_context_layout.setContentsMargins(6, 2, 6, 2)
        polygon_context_layout.setSpacing(5)
        polygon_context_layout.addWidget(QtWidgets.QLabel("Polígono — lados", self))
        self.polygon_sides = QtWidgets.QSpinBox(self.polygon_context)
        self.polygon_sides.setObjectName("polygonSides")
        self.polygon_sides.setRange(3, 64)
        self.polygon_sides.setValue(3)
        self.polygon_sides.setToolTip("Número de lados do polígono")
        self.polygon_sides.setFixedWidth(58)
        self.polygon_sides.valueChanged.connect(self.tool_manager.set_polygon_sides)
        polygon_context_layout.addWidget(self.polygon_sides)
        polygon_context_layout.addStretch(1)
        self.polygon_context.hide()

        self.workflow_preview_bar = WorkflowPreviewBar(self)
        self.workflow_preview_bar.applyRequested.connect(
            self._apply_workflow_preview
        )
        self.workflow_preview_bar.cancelRequested.connect(
            self._cancel_workflow_preview
        )

        self.content_splitter = QtWidgets.QSplitter(
            qt_enum(QtCore.Qt, "Horizontal", "Orientation"), self
        )
        self.content_splitter.setChildrenCollapsible(True)
        self.canvas_host = QtWidgets.QWidget(self.content_splitter)
        self.canvas_host.setObjectName("editorCanvasHost")
        canvas_layout = QtWidgets.QHBoxLayout(self.canvas_host)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        canvas_layout.setSpacing(2)
        canvas_layout.addWidget(self.drawing_toolbar, 0)
        self.canvas_surface = QtWidgets.QWidget(self.canvas_host)
        self.canvas_surface.setObjectName("editorCanvasSurface")
        canvas_surface_layout = QtWidgets.QVBoxLayout(self.canvas_surface)
        canvas_surface_layout.setContentsMargins(0, 0, 0, 0)
        canvas_surface_layout.setSpacing(2)
        # Preview confirmation is deliberately pinned above the viewport.  It
        # never enters the side-panel scroll area or falls below the canvas.
        canvas_surface_layout.addWidget(self.workflow_preview_bar, 0)
        canvas_surface_layout.addWidget(self.polygon_context, 0)
        canvas_surface_layout.addWidget(self.view, 1)
        canvas_layout.addWidget(self.canvas_surface, 1)
        self.content_splitter.addWidget(self.canvas_host)
        self.side_scroll = QtWidgets.QScrollArea(self.content_splitter)
        self.side_scroll.setWidgetResizable(True)
        self.side_scroll.setHorizontalScrollBarPolicy(
            qt_enum(QtCore.Qt, "ScrollBarAlwaysOff", "ScrollBarPolicy")
        )
        self.side_panel = QtWidgets.QWidget(self.side_scroll)
        side_layout = QtWidgets.QVBoxLayout(self.side_panel)
        side_layout.setContentsMargins(3, 0, 0, 0)
        self.layer_panel = LayerPanel(self.controller, self.side_panel)
        self.pieces_panel = PiecesPanel(self.controller, self.side_panel)
        self.properties_panel = ExactPropertiesPanel(self.controller, self.side_panel)
        self.transform_panel = TransformPanel(self.controller, self.side_panel)
        self.modifier_panel = ModifierParametersPanel(self.side_panel)
        self.layer_panel.message.connect(self._show_panel_message)
        self.pieces_panel.message.connect(self._show_panel_message)
        self.pieces_panel.pieceSelected.connect(self._on_piece_selected)
        self.properties_panel.message.connect(self._show_panel_message)
        self.transform_panel.message.connect(self._show_panel_message)
        self.modifier_panel.parametersChanged.connect(
            self.tool_manager.set_modifier_parameters
        )
        self.modifier_panel.applyAutomaticRequested.connect(
            self.tool_manager.confirm_active_preview
        )
        self.tool_manager.previewAvailabilityChanged.connect(
            self.modifier_panel.set_auto_preview_available
        )
        self.tool_manager.set_modifier_parameters(self.modifier_panel.parameters())
        side_layout.addWidget(self.layer_panel)
        side_layout.addWidget(self.pieces_panel)
        side_layout.addWidget(self.properties_panel)
        side_layout.addWidget(self.transform_panel)
        side_layout.addWidget(self.modifier_panel)
        self.modifier_panel.hide()
        side_layout.addStretch(1)
        self.side_scroll.setMinimumWidth(250)
        self.side_scroll.setMaximumWidth(370)
        self.side_scroll.setWidget(self.side_panel)
        self.content_splitter.addWidget(self.side_scroll)
        self.content_splitter.setStretchFactor(0, 1)
        self.content_splitter.setStretchFactor(1, 0)
        root.addWidget(self.content_splitter, 1)

        status = QtWidgets.QHBoxLayout()
        status.setContentsMargins(6, 1, 6, 3)
        self.mode_label = QtWidgets.QLabel(self)
        self.mode_label.setMinimumWidth(260)
        self.selection_label = QtWidgets.QLabel("0 selecionados", self)
        self.snap_label = QtWidgets.QLabel("", self)
        self.position_label = QtWidgets.QLabel("X 0,000   Y 0,000 mm", self)
        status.addWidget(self.mode_label, 1)
        status.addWidget(self.selection_label)
        status.addSpacing(12)
        status.addWidget(self.snap_label)
        status.addSpacing(12)
        status.addWidget(self.position_label)
        root.addLayout(status)

    def _add_mode_button(self, label, mode, shortcut=None, icon_name=None):
        button = QtWidgets.QToolButton(self)
        button.setObjectName("drawingTool_" + mode.value)
        button.setText(label)
        button.setAccessibleName(label)
        button.setCheckable(True)
        button.setAutoRaise(False)
        button.setToolButtonStyle(
            qt_enum(QtCore.Qt, "ToolButtonIconOnly", "ToolButtonStyle")
        )
        button.setIcon(tool_icon(icon_name or mode.value))
        button.setIconSize(QtCore.QSize(22, 22))
        button.setFixedSize(34, 34)
        if shortcut:
            button.setToolTip("%s (%s)" % (label, shortcut))
        button.clicked.connect(lambda checked=False, value=mode: self.tool_manager.activate(value))
        self._button_group.addButton(button)
        self.drawing_toolbar.addWidget(button)
        self._mode_buttons[mode] = button
        return button

    def _add_compact_action_button(self, label, icon_name, callback, tooltip=None):
        button = QtWidgets.QToolButton(self)
        button.setObjectName("action" + label.replace(" ", ""))
        button.setText(label)
        button.setAccessibleName(label)
        button.setToolButtonStyle(
            qt_enum(QtCore.Qt, "ToolButtonIconOnly", "ToolButtonStyle")
        )
        button.setIcon(tool_icon(icon_name, 20))
        button.setIconSize(QtCore.QSize(18, 18))
        button.setFixedSize(26, 26)
        button.setToolTip(str(tooltip or label))
        button.clicked.connect(lambda _checked=False: callback())
        self.toolbar.addWidget(button)
        return button

    def _add_action_button(self, label, callback):
        button = QtWidgets.QToolButton(self)
        button.setText(label)
        button.clicked.connect(lambda _checked=False: callback())
        self.toolbar.addWidget(button)
        return button

    def _add_menu_button(self, label, entries):
        button = QtWidgets.QToolButton(self)
        button.setObjectName("menu" + label.replace(" ", ""))
        button.setText(label)
        menu = QtWidgets.QMenu(button)
        action_class = getattr(QtGui, "QAction", None)
        if action_class is None:
            action_class = QtWidgets.QAction
        actions = {}
        for action_label, callback in entries:
            action = action_class(action_label, menu)
            action.triggered.connect(lambda _checked=False, handler=callback: handler())
            menu.addAction(action)
            actions[str(action_label)] = action
        button.setMenu(menu)
        popup_mode = getattr(QtWidgets.QToolButton, "InstantPopup", None)
        if popup_mode is None:
            popup_mode = QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup
        button.setPopupMode(popup_mode)
        self.toolbar.addWidget(button)
        self._menu_actions[str(label)] = actions
        return button

    def _show_panel_message(self, text):
        self.mode_label.setText(str(text))

    def _on_piece_selected(self, piece_id):
        self.fit_selection()
        self.pieceSelected.emit(str(piece_id))

    def _activate_modifier(self, mode):
        self.tool_manager.activate(mode)
        self.modifier_panel.set_mode(mode)
        self.modifier_panel.show()
        self.view.setFocus()

    def _connect_session(self):
        self.controller.subscribe_document(self._document_changed)
        self.controller.subscribe_mode(self._mode_changed)
        self.controller.selection.subscribe(self._selection_changed)
        self.tool_manager.statusChanged.connect(self.mode_label.setText)
        self.tool_manager.snapChanged.connect(self._snap_status)
        self.view.cursorMoved.connect(self._cursor_moved)
        self.view.keyPressed.connect(self._workflow_key_press)
        self.view.cancelRequested.connect(self._cancel_to_select)

    def _document_changed(self, change_set=None):
        if change_set is None:
            # A host Undo/Redo reload may also replace layers, pieces and the
            # work area while retaining the VectorDocument object identity.
            self.adapter.set_document(self.controller.document)
        else:
            self.adapter.refresh(change_set)
        self._sync_nodes()
        if (
            self.workflow_preview_bar.is_active
            and self._workflow_revision is not None
            and self.document.revision != self._workflow_revision
        ):
            self.workflow_preview_bar.set_summary(
                "Prévia desatualizada porque o documento mudou; cancele e gere novamente."
            )
            self.workflow_preview_bar.set_apply_enabled(False)
        self.documentChanged.emit(change_set)

    def _mode_changed(self, mode):
        if (
            getattr(self, "workflow_preview_bar", None) is not None
            and self.workflow_preview_bar.is_active
            and mode != EditorMode.SELECT
        ):
            self.cancel_workflow_preview()
        button = self._mode_buttons.get(mode)
        if button is not None:
            button.setChecked(True)
        self._sync_nodes()
        modifier_modes = {
            EditorMode.TRIM,
            EditorMode.EXTEND,
            EditorMode.OFFSET,
            EditorMode.FILLET,
            EditorMode.DOGBONE,
            EditorMode.TBONE,
            EditorMode.JOIN_ENDPOINTS,
            EditorMode.CONNECT,
            EditorMode.SPLICE,
            EditorMode.AUTO_DOGBONE,
            EditorMode.AUTO_TBONE,
        }
        self.modifier_panel.setVisible(mode in modifier_modes)
        self.polygon_context.setVisible(mode == EditorMode.DRAW_POLYGON)
        if mode in modifier_modes:
            self.modifier_panel.set_mode(mode)
        self.modeChanged.emit(mode.value)

    def _selection_changed(self, ids):
        count = len(ids)
        self.selection_label.setText("%d selecionado%s" % (count, "" if count == 1 else "s"))
        self.delete_button.setEnabled(bool(count))
        self._sync_nodes()
        if self.controller.mode in (
            EditorMode.AUTO_DOGBONE,
            EditorMode.AUTO_TBONE,
        ):
            recompute = getattr(self.tool_manager.active, "recompute_last_hover", None)
            if callable(recompute):
                recompute()
        self.selectionChanged.emit(ids)

    def _sync_nodes(self):
        if self.controller.mode != EditorMode.NODE_EDIT or not self.controller.node_entity_id:
            self.overlays.clear_nodes()
            return
        self.overlays.show_nodes(
            self.controller.node_entity_id,
            self.controller.node_positions(self.controller.node_entity_id),
            self.controller.selected_node_ids,
        )

    def _cursor_moved(self, point):
        self.position_label.setText("X %.3f   Y %.3f mm" % (point.x(), point.y()))

    def _snap_status(self, text):
        self.snap_label.setText(("Snap: " + text) if text else "")

    def _cancel_to_select(self):
        """CAD-style right click: abandon transient state and return to select."""
        self.cancel_workflow_preview()
        self.tool_manager.activate(EditorMode.SELECT)
        self.controller.selection.clear()
        self.overlays.clear_transient()
        self.view.setFocus()

    def _workflow_key_press(self, event):
        if not self.workflow_preview_bar.is_active:
            return
        key = event.key()
        if key == qt_enum(QtCore.Qt, "Key_Escape", "Key"):
            self.cancel_workflow_preview()
            event.accept()
        elif key in (
            qt_enum(QtCore.Qt, "Key_Return", "Key"),
            qt_enum(QtCore.Qt, "Key_Enter", "Key"),
        ):
            self._apply_workflow_preview(self.workflow_preview_bar.payload)
            event.accept()

    def _toggle_snap(self, enabled):
        settings = self.controller.snap_engine.settings
        for name in ("endpoint", "intersection", "midpoint", "center", "quadrant", "on_geometry", "grid"):
            setattr(settings, name, bool(enabled))

    def _set_grid_spacing(self, value):
        spacing = float(value)
        self.controller.snap_engine.settings.grid_spacing_mm = spacing
        self.view.set_grid_spacing(spacing)

    def activate_tool(self, mode):
        """Public integration hook; accepts ``EditorMode`` or its string value."""
        self.tool_manager.activate(mode)
        self.view.setFocus()

    def begin_workflow_preview(
        self,
        result_entities,
        summary,
        apply_callback,
        *,
        apply_label="Aplicar prévia",
        sheet_bounds=(),
    ):
        """Mostra resultado transitório e aguarda Aplicar/Cancelar/Enter/Esc."""
        if not callable(apply_callback):
            raise TypeError("apply_callback precisa ser chamável")
        self.cancel_workflow_preview()
        self.tool_manager.activate(EditorMode.SELECT)
        self._workflow_apply_callback = apply_callback
        self._workflow_revision = int(self.document.revision)
        self.overlays.show_issue(tuple(result_entities or ()), ())
        self.adapter.show_preview_sheet_bounds(sheet_bounds)
        self.workflow_preview_bar.begin(
            summary,
            payload=self._workflow_revision,
            apply_label=apply_label,
            cancel_label="Cancelar",
        )
        self.view.setFocus()

    def _apply_workflow_preview(self, payload=None):
        if not self.workflow_preview_bar.is_active:
            return False
        if (
            payload != self._workflow_revision
            or self.document.revision != self._workflow_revision
        ):
            self.workflow_preview_bar.set_summary(
                "Prévia desatualizada; cancele e gere novamente."
            )
            self.workflow_preview_bar.set_apply_enabled(False)
            return False
        callback = self._workflow_apply_callback
        try:
            callback()
        except Exception as error:
            self.workflow_preview_bar.set_summary(
                "Não foi possível aplicar: %s" % error
            )
            return False
        self.cancel_workflow_preview()
        return True

    def _cancel_workflow_preview(self, _payload=None):
        self.cancel_workflow_preview()

    def cancel_workflow_preview(self):
        self._workflow_apply_callback = None
        self._workflow_revision = None
        self.workflow_preview_bar.clear()
        self.overlays.clear_transient()
        self.adapter.clear_preview_sheet_bounds()

    def focus_validation_issue(self, issue):
        """Seleciona, destaca e enquadra uma ocorrência do validador."""
        self.cancel_workflow_preview()
        entity_ids = tuple(
            entity_id
            for entity_id in tuple(getattr(issue, "entity_ids", ()) or ())
            if entity_id in self.document.entities_by_id
        )
        self.controller.selection.replace(entity_ids)
        entities = tuple(
            self.document.entities_by_id[entity_id] for entity_id in entity_ids
        )
        points = tuple(getattr(issue, "points", ()) or ())
        self.overlays.show_issue(entities, points)
        if entity_ids:
            self.fit_selection()
        elif points:
            xs = [float(point.x) for point in points]
            ys = [float(point.y) for point in points]
            rect = QtCore.QRectF(
                min(xs), min(ys), max(1.0, max(xs) - min(xs)), max(1.0, max(ys) - min(ys))
            )
            self.view.fit_model_rect(rect, margin=0.35)
        self.mode_label.setText(str(getattr(issue, "message", "Ocorrência selecionada.")))

    def set_work_area(self, bounds, *, fit=False):
        """Update visual work bounds without writing geometry.

        ``bounds`` may be the domain ``WorkArea`` or ``(min_x,min_y,max_x,max_y)``.
        Updating X/Y must not reframe the camera: otherwise the dashed area
        stays visually centred while all entities appear to jump in the
        opposite direction.  Initial/open-document framing is already handled
        by the constructor timer and the explicit ``fit_work_area`` action.
        """
        self.adapter.set_work_area(bounds)
        if fit:
            self.fit_work_area()

    def fit_work_area(self):
        self.adapter._update_scene_rect()
        rect = self.adapter.work_area_item.rect()
        if not self.adapter.work_area_item.isVisible() or rect.isNull():
            rect = self.scene.itemsBoundingRect()
        self.view.fit_model_rect(rect)

    def fit_selection(self):
        rect = QtCore.QRectF()
        for entity_id in self.controller.selection.ids:
            item = self.adapter.items_by_id.get(entity_id)
            if item is not None:
                bounds = item.mapToScene(item.path()).boundingRect()
                rect = bounds if rect.isNull() else rect.united(bounds)
        if not rect.isNull():
            self.view.fit_model_rect(rect, margin=0.18)

    def delete_selected(self):
        try:
            return self.controller.delete_selected()
        except CommandExecutionCancelled as error:
            self.mode_label.setText(str(error))
            return False

    def undo(self):
        return self.controller.undo()

    def redo(self):
        return self.controller.redo()

    def refresh_from_document(self):
        """Use after a FreeCAD Undo/Redo observer reloads GeometryJSON."""
        self.controller.document_reloaded()

    def set_cam_source_active(self, active):
        self.use_cam_button.setChecked(bool(active))
        self.use_cam_button.setText(
            "Editor 2D é a fonte do CAM"
            if active
            else "Usar Editor 2D como fonte"
        )
        self.use_cam_button.setStatusTip(
            "O CAM está usando o documento persistido do Editor 2D."
            if active
            else "O CAM está usando a seleção do FreeCAD."
        )


__all__ = ["Editor2DWidget"]
