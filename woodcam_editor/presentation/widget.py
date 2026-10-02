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

from .compat import CTRL_MODIFIER, Signal, QtCore, QtGui, QtWidgets, qt_enum
from .icons import tool_icon
from .i18n import register_widget, set_language, translate_text
from .overlays import OverlayLayer
from .panels import (
    ExactPropertiesPanel,
    LayerPanel,
    ModifierParametersPanel,
    PiecesPanel,
    SheetPanel,
    TransformPanel,
)
from .scene_adapter import SceneAdapter
from .tools import ToolManager
from .view import RulerWidget, VectorGraphicsView
from .workflows import WorkflowPreviewBar


_MENU_ICON_NAMES = {
    "Editar": {
        "Copiar": "copy",
        "Colar": "paste",
        "Agrupar objetos": "group",
        "Desagrupar objetos": "ungroup",
        "Medir / inspecionar": "measure",
        "Soldar vetores sobrepostos": "weld",
        "Subtrair vetores (criar furo/recorte interno)": "subtract",
        "Interseção de vetores": "intersection",
        "Sobrepor vetores (último recorta os anteriores)": "overlap",
        "Inverter direção dos vetores": "reverse",
        "Editar texto vetorial…": "text_edit",
        "Ajustar arcos/círculos aos vetores…": "fit_curves",
        "Criar contorno (offset)…": "contour",
    },
    "Reparar": {
        "Diagnosticar": "diagnose",
        "Limpar sobrelinhas/duplicados…": "cleanup",
        "Fechar caminho / unir próximas": "repair_close",
        "Unir vetores abertos (por tolerância)": "join_paths",
        "Fechar caminho com reta": "close_line",
        "Fechar caminho com curva suave": "close_smooth",
        "Fechar aproximando as pontas": "close_midpoint",
        "Unir 2 pontas (reta)": "join_line",
        "Unir 2 pontas (curva suave)": "join_smooth",
        "Projetar ponta na geometria": "project",
        "Emendar em contorno (Splice)": "splice",
        "Trim interativo": "trim",
        "Estender": "extend",
        "Offset": "offset",
    },
}

_MENU_SECTION_BREAKS = {
    "Editar": {
        "Agrupar objetos",
        "Soldar vetores sobrepostos",
        "Editar texto vetorial…",
    },
    "Reparar": {
        "Fechar caminho / unir próximas",
        "Unir 2 pontas (reta)",
        "Trim interativo",
    },
}


class _ResponsiveTopToolBar(QtWidgets.QToolBar):
    """Keeps a dense Aspire-like command strip usable in a narrow task panel.

    The toolbar itself retains its natural minimum width inside a horizontal
    scroll host, so no command disappears.  Reporting the available viewport
    width as the size hint prevents Qt from forcing the whole editor wider than
    a docked FreeCAD task panel.
    """

    def sizeHint(self):  # noqa: N802 - Qt virtual name
        hint = super(_ResponsiveTopToolBar, self).sizeHint()
        parent = self.parentWidget()
        available = parent.width() if parent is not None else 0
        if available > 0:
            hint.setWidth(min(hint.width(), available))
        return hint

    def minimumSizeHint(self):  # noqa: N802 - Qt virtual name
        # QScrollArea needs the natural minimum width to expose a horizontal
        # scrollbar.  It must not receive the capped reporting size above.
        return super(_ResponsiveTopToolBar, self).minimumSizeHint()


class Editor2DWidget(QtWidgets.QWidget):
    documentChanged = Signal(object)
    selectionChanged = Signal(object)
    modeChanged = Signal(str)
    importSketchRequested = Signal()
    importPanelNestPartsRequested = Signal()
    diagnoseRequested = Signal()
    repairRequested = Signal()
    cleanupDuplicatesRequested = Signal()
    booleanUnionRequested = Signal()
    booleanDifferenceRequested = Signal()
    booleanIntersectionRequested = Signal()
    booleanOverlapRequested = Signal()
    reverseDirectionRequested = Signal()
    createTextRequested = Signal()
    editTextRequested = Signal()
    groupRequested = Signal()
    ungroupRequested = Signal()
    closePathRequested = Signal(str)
    joinOpenPathsRequested = Signal()
    fitCurvesRequested = Signal()
    createContourRequested = Signal()
    createPiecesRequested = Signal()
    organizePiecesRequested = Signal()
    organizePiecesFastRequested = Signal()
    organizePiecesThoroughRequested = Signal()
    useInCamRequested = Signal()
    sendPanelNestRequested = Signal()
    showCutToolpathRequested = Signal()
    showToolpathRequested = Signal(str)
    configureToolpathRequested = Signal(str)
    clearCutToolpathRequested = Signal()
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
    printTechDrawRequested = Signal()

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
        self._application = QtWidgets.QApplication.instance()
        if self._application is not None:
            # FreeCAD registers global Ctrl+A/Delete actions.  An application
            # filter lets the focused canvas own these editor operations
            # before the host tree has a chance to consume them.
            self._application.installEventFilter(self)
        self._workflow_apply_callback = None
        self._workflow_revision = None
        self._focus_mode = False
        self._side_panel_hidden = False
        self._active_sheet_origin = QtCore.QPointF(0.0, 0.0)
        self._mode_buttons = {}
        self._build_ui()
        # The language layer only changes Qt presentation properties.  The
        # document, command history and all menu callbacks remain untouched.
        register_widget(self)
        self._connect_session()
        self.tool_manager.activate(EditorMode.SELECT)
        QtCore.QTimer.singleShot(0, self.fit_work_area)

    @property
    def document(self):
        return self.controller.document

    @property
    def selected_entity_ids(self):
        return self.controller.selection.ids

    @property
    def sheet_bounds(self):
        """Physical/virtual sheets available for presentation adapters."""
        panel = getattr(self, "sheet_panel", None)
        return tuple(panel.document_bounds()) if panel is not None else ()

    @property
    def active_sheet_index(self):
        panel = getattr(self, "sheet_panel", None)
        return panel.current_index() if panel is not None else -1

    @property
    def visible_toolpath_components(self):
        """Copy the currently displayed CAM overlay for optional printing."""
        values = getattr(self.overlays, "_toolpath_components", {}) or {}
        return {str(key): tuple(value) for key, value in values.items()}

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(3)

        # Keep the window-like expand/retract control outside the horizontally
        # scrollable command strip.  It therefore remains visible even when
        # the editor is docked in a narrow FreeCAD task panel.
        toolbar_row = QtWidgets.QHBoxLayout()
        toolbar_row.setContentsMargins(0, 0, 0, 0)
        toolbar_row.setSpacing(2)

        self.top_toolbar_scroll = QtWidgets.QScrollArea(self)
        self.top_toolbar_scroll.setObjectName("editorTopToolbarScroll")
        self.top_toolbar_scroll.setWidgetResizable(False)
        self.top_toolbar_scroll.setSizeAdjustPolicy(
            qt_enum(QtWidgets.QAbstractScrollArea, "AdjustIgnored", "SizeAdjustPolicy")
        )
        self.top_toolbar_scroll.setMinimumWidth(0)
        self.top_toolbar_scroll.setSizePolicy(
            QtWidgets.QSizePolicy.Ignored,
            QtWidgets.QSizePolicy.Fixed,
        )
        self.top_toolbar_scroll.setFrameShape(
            qt_enum(QtWidgets.QFrame, "NoFrame", "Shape")
        )
        self.top_toolbar_scroll.setHorizontalScrollBarPolicy(
            qt_enum(QtCore.Qt, "ScrollBarAsNeeded", "ScrollBarPolicy")
        )
        self.top_toolbar_scroll.setVerticalScrollBarPolicy(
            qt_enum(QtCore.Qt, "ScrollBarAlwaysOff", "ScrollBarPolicy")
        )
        self.toolbar = _ResponsiveTopToolBar(self.top_toolbar_scroll)
        self.toolbar.setObjectName("editorTopToolbar")
        self.toolbar.setMovable(False)
        self.toolbar.setFloatable(False)
        self.toolbar.setIconSize(QtCore.QSize(16, 16))
        # The scroll host also reserves room for its horizontal scrollbar.
        # A 31 px host left only a thin strip of the command row visible in a
        # narrow FreeCAD task panel, making menu labels and spin boxes appear
        # vertically clipped.  Keep the natural toolbar on one line and give
        # the viewport enough height for both the controls and the scrollbar.
        self.toolbar.setMinimumHeight(30)
        self.toolbar.setStyleSheet(
            "QToolBar { spacing: 2px; padding: 1px; border: 0; }"
            "QToolButton { padding: 2px; margin: 0; }"
        )
        self.top_toolbar_scroll.setWidget(self.toolbar)
        self.top_toolbar_scroll.setFixedHeight(48)

        self.focus_button = QtWidgets.QToolButton(self)
        self.focus_button.setObjectName("focusModeButton")
        self.focus_button.setAccessibleName("Ocultar painel lateral")
        self.focus_button.setCheckable(True)
        # The old native maximize glyph looked like a second window control and
        # hid the fact that this action only folds the right-hand inspector.
        # The actual control is the narrow tab on the splitter; keep this
        # legacy button out of the crowded toolbar row.
        self.focus_button.setFixedSize(22, 30)
        self.focus_button.hide()
        self.focus_button.clicked.connect(self._toggle_focus_mode)
        toolbar_row.addWidget(self.top_toolbar_scroll, 1)
        toolbar_row.addWidget(self.focus_button, 0, QtCore.Qt.AlignTop)
        root.addLayout(toolbar_row)
        self._update_focus_button()

        self._menu_actions = {}
        self._button_group = QtWidgets.QButtonGroup(self)
        self._button_group.setExclusive(True)
        self.file_menu_button = self._add_menu_button(
            "Arquivo",
            (
                ("Importar itens da árvore…", self.importSketchRequested.emit),
                ("Importar peças planas pelo PanelNest…", self.importPanelNestPartsRequested.emit),
                ("Importar arquivo", self.importRequested.emit),
                ("Vetorizar imagem…", self.traceBitmapRequested.emit),
                ("Criar relevo 3D por imagem…", self.createReliefRequested.emit),
                ("Exportar", self.exportRequested.emit),
                ("Enviar para impressão (TechDraw)…", self.printTechDrawRequested.emit),
            ),
        )
        self.edit_menu_button = self._add_menu_button(
            "Editar",
            (
                ("Copiar", self.copy_selected),
                ("Colar", self.paste_copied),
                ("Agrupar objetos", self.groupRequested.emit),
                ("Desagrupar objetos", self.ungroupRequested.emit),
                ("Medir / inspecionar", lambda: self.activate_tool(EditorMode.MEASURE)),
                # Aspire distinguishes a geometric weld from a compound part:
                # profiles that overlap are welded, while a contour entirely
                # inside a board becomes an exact internal loop of that board.
                ("Soldar vetores sobrepostos", self.booleanUnionRequested.emit),
                ("Subtrair vetores (criar furo/recorte interno)", self.booleanDifferenceRequested.emit),
                ("Interseção de vetores", self.booleanIntersectionRequested.emit),
                ("Sobrepor vetores (último recorta os anteriores)", self.booleanOverlapRequested.emit),
                ("Inverter direção dos vetores", self.reverseDirectionRequested.emit),
                ("Editar texto vetorial…", self.editTextRequested.emit),
                ("Ajustar arcos/círculos aos vetores…", self.fitCurvesRequested.emit),
                ("Criar contorno (offset)…", self.createContourRequested.emit),
            ),
        )
        self._menu_actions["Editar"]["Copiar"].setShortcut(
            QtGui.QKeySequence.Copy
        )
        self._menu_actions["Editar"]["Colar"].setShortcut(
            QtGui.QKeySequence.Paste
        )
        self.repair_menu_button = self._add_menu_button(
            "Reparar",
            (
                ("Diagnosticar", self.diagnoseRequested.emit),
                ("Limpar sobrelinhas/duplicados…", self.cleanupDuplicatesRequested.emit),
                ("Fechar caminho / unir próximas", self.repairRequested.emit),
                ("Unir vetores abertos (por tolerância)", self.joinOpenPathsRequested.emit),
                (
                    "Fechar caminho com reta",
                    lambda: self.closePathRequested.emit("line"),
                ),
                (
                    "Fechar caminho com curva suave",
                    lambda: self.closePathRequested.emit("smooth"),
                ),
                (
                    "Fechar aproximando as pontas",
                    lambda: self.closePathRequested.emit("midpoint"),
                ),
                (
                    "Unir 2 pontas (reta)",
                    lambda: self._activate_modifier(EditorMode.JOIN_ENDPOINTS),
                ),
                (
                    "Unir 2 pontas (curva suave)",
                    lambda: self._activate_modifier(EditorMode.JOIN_ENDPOINTS_SMOOTH),
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
                "Mostra rapidamente a primeira prévia e continua comparando "
                "MaxRects e contorno real em segundo plano até o tempo-alvo."
            ),
            "Organizar rápido": (
                "Produz uma prévia com menos tentativas e tempo-alvo curto; "
                "mantém contorno real, furos vinculados e Undo."
            ),
            "Organizar profundo": (
                "Explora mais ordens de encaixe e refinamentos enquanto mostra "
                "a melhor prévia encontrada; o tempo-alvo pode ser ajustado."
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
                ("Configurar/criar Corte…", lambda: self.configureToolpathRequested.emit("cut")),
                ("Configurar/criar Furos…", lambda: self.configureToolpathRequested.emit("holes")),
                ("Configurar/criar Rebaixo…", lambda: self.configureToolpathRequested.emit("pocket")),
                ("Ver percurso de Corte aqui", self.showCutToolpathRequested.emit),
                ("Ver percurso de Furos aqui", lambda: self.showToolpathRequested.emit("holes")),
                ("Ver percurso de Rebaixo aqui", lambda: self.showToolpathRequested.emit("pocket")),
                ("Ocultar percurso", self.clearCutToolpathRequested.emit),
                ("Enviar PanelNest", self.sendPanelNestRequested.emit),
            ),
        )
        # Keep language selection in the same discreet command strip as the
        # existing File/Edit menus.  The setting is shared with the CAM dialog
        # and persisted in the WoodCAM preferences.
        self.language_menu_button = self._add_menu_button(
            "Idioma",
            (
                ("Português", lambda: set_language("pt")),
                ("English", lambda: set_language("en")),
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
            "Atrai o cursor para pontas, centros, interseções e geometria. "
            "A Grade possui controle separado."
        )
        self.snap_checkbox.setChecked(True)
        self.snap_checkbox.toggled.connect(self._toggle_snap)
        self.toolbar.addWidget(self.snap_checkbox)
        self.grid_checkbox = QtWidgets.QCheckBox("Grade", self)
        self.grid_checkbox.setObjectName("gridVisible")
        self.grid_checkbox.setToolTip(
            "Mostrar os quadradinhos e encaixar na grade; desmarque para fundo branco e movimento livre."
        )
        self.grid_checkbox.setChecked(
            bool(self.controller.snap_engine.settings.grid)
        )
        self.grid_checkbox.toggled.connect(self._toggle_grid)
        self.toolbar.addWidget(self.grid_checkbox)
        self.smart_snap_checkbox = QtWidgets.QCheckBox("Orto/ângulo", self)
        self.smart_snap_checkbox.setObjectName("smartSnap")
        self.smart_snap_checkbox.setToolTip(
            "Durante desenho e medição, prende ao horizontal, vertical e a "
            "múltiplos de 15° a partir do último ponto. Shift ignora todo snap."
        )
        self.smart_snap_checkbox.setChecked(True)
        self.smart_snap_checkbox.toggled.connect(self._toggle_smart_snap)
        self.toolbar.addWidget(self.smart_snap_checkbox)
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
        self.grid_spacing.setEnabled(self.grid_checkbox.isChecked())
        self.toolbar.addWidget(self.grid_spacing)

        self.nesting_spacing = QtWidgets.QDoubleSpinBox(self)
        self.nesting_spacing.setObjectName("nestingSpacing")
        self.nesting_spacing.setAccessibleName("Espaçamento entre peças para nesting")
        self.nesting_spacing.setToolTip(
            "Folga mínima entre peças no nesting, em milímetros. "
            "Use a folga que precisa sobrar entre os cortes/fresa. Quando "
            "linha comum preservando medidas está ativa, o diâmetro efetivo "
            "da fresa define o menor valor fisicamente usinável."
        )
        self.nesting_spacing.setRange(0.0, 1000.0)
        self.nesting_spacing.setDecimals(3)
        self.nesting_spacing.setSingleStep(0.125)
        self.nesting_spacing.setSuffix(" mm entre peças")
        self.nesting_spacing.setValue(10.0)
        self.nesting_spacing.setFixedWidth(132)
        # Nesting clearance is a job-specific decision, so it is requested at
        # the moment the user starts organizing instead of permanently
        # consuming the narrow command strip.
        self.nesting_spacing.hide()
        self.toolbar.addSeparator()
        # Keep the natural command-strip width as scrollable content.  The
        # toolbar reports the dock width to its layout, while this minimum is
        # what lets a narrow FreeCAD panel reveal remaining commands by
        # horizontal scrolling instead of clipping them.
        self.toolbar.setMinimumWidth(QtWidgets.QToolBar.sizeHint(self.toolbar).width())
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
        self.edit_tools_button = self._add_drawing_menu_button(
            "Editar",
            "edit_tools",
            self._menu_actions["Editar"],
            "Editar — abrir ferramentas",
        )
        self.repair_tools_button = self._add_drawing_menu_button(
            "Reparar",
            "repair_tools",
            self._menu_actions["Reparar"],
            "Reparar — abrir ferramentas",
        )
        self.drawing_toolbar.addSeparator()
        self._add_mode_button("Linha", EditorMode.DRAW_LINE, "L", "line")
        self._add_mode_button("Polilinha", EditorMode.DRAW_POLYLINE, "P", "polyline")
        self._add_mode_button("Retângulo", EditorMode.DRAW_RECTANGLE, "R", "rectangle")
        self._add_mode_button("Círculo", EditorMode.DRAW_CIRCLE, "C", "circle")
        self._add_mode_button("Elipse", EditorMode.DRAW_ELLIPSE, "E", "ellipse")
        self._add_mode_button("Arco", EditorMode.DRAW_ARC, "A", "arc")
        self._add_mode_button("Bézier", EditorMode.DRAW_BEZIER, "B", "bezier")
        self._add_mode_button("Polígono", EditorMode.DRAW_POLYGON, "G", "polygon")
        self._add_mode_button("Estrela", EditorMode.DRAW_STAR, None, "star")
        self.drawing_toolbar.addSeparator()
        self._add_drawing_action_button(
            "Texto vetorial", "text", self.createTextRequested.emit,
            "Criar texto vetorial em curvas",
        )
        self.drawing_toolbar.addSeparator()
        self.measure_button = self._add_drawing_action_button(
            "Medir / inspecionar",
            "measure",
            lambda: self.activate_tool(EditorMode.MEASURE),
            "Medir entre dois pontos com Snap; não altera o desenho",
        )
        self.recognize_pieces_button = self._add_drawing_action_button(
            "Reconhecer peças e furos",
            "recognize_parts",
            self.createPiecesRequested.emit,
            "Reconhecer contornos externos como peças e manter furos e recortes vinculados",
        )
        self.organize_pieces_button = self._add_drawing_action_button(
            "Organizar inteligente",
            "nest",
            self.organizePiecesRequested.emit,
            "Organizar peças automaticamente usando o nesting inteligente",
        )

        # Tool-specific options live above the canvas instead of consuming the
        # narrow vertical drawing rail.  This prevents the sides control from
        # being clipped on short task panels.
        self.polygon_context = QtWidgets.QFrame(self)
        self.polygon_context.setObjectName("polygonContextBar")
        polygon_context_layout = QtWidgets.QHBoxLayout(self.polygon_context)
        polygon_context_layout.setContentsMargins(6, 2, 6, 2)
        polygon_context_layout.setSpacing(5)
        self.polygon_context_label = QtWidgets.QLabel("Polígono — lados", self)
        polygon_context_layout.addWidget(self.polygon_context_label)
        self.polygon_sides = QtWidgets.QSpinBox(self.polygon_context)
        self.polygon_sides.setObjectName("polygonSides")
        self.polygon_sides.setRange(3, 64)
        self.polygon_sides.setValue(3)
        self.polygon_sides.setToolTip("Número de lados do polígono")
        self.polygon_sides.setFixedWidth(58)
        self.polygon_sides.valueChanged.connect(self._set_shape_sides)
        polygon_context_layout.addWidget(self.polygon_sides)
        self._star_points_initialized = False
        self.star_inner_ratio_label = QtWidgets.QLabel("Interno (%)", self.polygon_context)
        polygon_context_layout.addWidget(self.star_inner_ratio_label)
        self.star_inner_ratio = QtWidgets.QSpinBox(self.polygon_context)
        self.star_inner_ratio.setObjectName("starInnerRatio")
        self.star_inner_ratio.setRange(5, 95)
        self.star_inner_ratio.setValue(45)
        self.star_inner_ratio.setSuffix(" %")
        self.star_inner_ratio.setToolTip("Profundidade das pontas internas da estrela")
        self.star_inner_ratio.setFixedWidth(72)
        self.star_inner_ratio.valueChanged.connect(
            lambda value: self.tool_manager.set_star_inner_ratio(float(value) / 100.0)
        )
        polygon_context_layout.addWidget(self.star_inner_ratio)
        self.star_inner_ratio_label.hide()
        self.star_inner_ratio.hide()
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
        self.content_splitter.setHandleWidth(8)
        self.content_splitter.setOpaqueResize(True)
        self.content_splitter.setStyleSheet(
            "QSplitter::handle { background: #cbd5e1; }"
            "QSplitter::handle:hover { background: #60a5fa; }"
        )
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
        self.ruler_surface = QtWidgets.QWidget(self.canvas_surface)
        ruler_grid = QtWidgets.QGridLayout(self.ruler_surface)
        ruler_grid.setContentsMargins(0, 0, 0, 0)
        ruler_grid.setSpacing(0)
        self.ruler_corner = QtWidgets.QFrame(self.ruler_surface)
        self.ruler_corner.setFixedSize(22, 22)
        self.ruler_corner.setStyleSheet("background: #e2e8f0; border: 0;")
        self.horizontal_ruler = RulerWidget(self.view, True, self.ruler_surface)
        self.vertical_ruler = RulerWidget(self.view, False, self.ruler_surface)
        ruler_grid.addWidget(self.ruler_corner, 0, 0)
        ruler_grid.addWidget(self.horizontal_ruler, 0, 1)
        ruler_grid.addWidget(self.vertical_ruler, 1, 0)
        ruler_grid.addWidget(self.view, 1, 1)
        ruler_grid.setColumnStretch(1, 1)
        ruler_grid.setRowStretch(1, 1)
        canvas_surface_layout.addWidget(self.ruler_surface, 1)
        canvas_layout.addWidget(self.canvas_surface, 1)
        self.side_toggle_button = QtWidgets.QToolButton(self.canvas_host)
        self.side_toggle_button.setObjectName("sidePanelToggleButton")
        self.side_toggle_button.setFixedWidth(22)
        self.side_toggle_button.setText("‹")
        self.side_toggle_button.setAccessibleName("Ocultar painel lateral")
        self.side_toggle_button.setToolTip("Ocultar somente o painel lateral")
        self.side_toggle_button.clicked.connect(self._toggle_side_panel)
        self.side_toggle_button.setStyleSheet(
            "QToolButton { background: #e2e8f0; border: 1px solid #94a3b8; "
            "border-radius: 3px; font-size: 17px; font-weight: bold; }"
            "QToolButton:hover { background: #bfdbfe; }"
        )
        canvas_layout.addWidget(self.side_toggle_button, 0)
        self.content_splitter.addWidget(self.canvas_host)
        self.side_scroll = QtWidgets.QScrollArea(self.content_splitter)
        self.side_scroll.setWidgetResizable(True)
        self.side_scroll.setHorizontalScrollBarPolicy(
            qt_enum(QtCore.Qt, "ScrollBarAlwaysOff", "ScrollBarPolicy")
        )
        self.side_panel = QtWidgets.QWidget(self.side_scroll)
        side_layout = QtWidgets.QVBoxLayout(self.side_panel)
        side_layout.setContentsMargins(3, 0, 0, 0)
        self.sheet_panel = SheetPanel(self.controller, self.side_panel)
        self.layer_panel = LayerPanel(self.controller, self.side_panel)
        self.pieces_panel = PiecesPanel(self.controller, self.side_panel)
        self.properties_panel = ExactPropertiesPanel(self.controller, self.side_panel)
        self.transform_panel = TransformPanel(self.controller, self.side_panel)
        self.modifier_panel = ModifierParametersPanel(self.side_panel)
        self.sheet_panel.sheetSelected.connect(self._on_sheet_selected)
        self.sheet_panel.fitRequested.connect(self._fit_sheet_bounds)
        self.sheet_panel.message.connect(self._show_panel_message)
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
        self.tool_manager.editSelectionRequested.connect(
            self._show_editing_panels
        )
        self.tool_manager.set_modifier_parameters(self.modifier_panel.parameters())
        side_layout.addWidget(self.sheet_panel)
        side_layout.addWidget(self.layer_panel)
        side_layout.addWidget(self.pieces_panel)
        side_layout.addWidget(self.properties_panel)
        side_layout.addWidget(self.transform_panel)
        side_layout.addWidget(self.modifier_panel)
        self.modifier_panel.hide()
        side_layout.addStretch(1)
        current_sheet = self.sheet_panel.current_bounds()
        if current_sheet is not None:
            self._on_sheet_selected(self.sheet_panel.current_index(), current_sheet)
        self.side_scroll.setMinimumWidth(340)
        self.side_scroll.setMaximumWidth(480)
        self.side_panel.setMinimumWidth(340)
        self.side_scroll.setWidget(self.side_panel)
        self.content_splitter.addWidget(self.side_scroll)
        # The tolerance is contextual: it appears in the repair panel only
        # while one of the endpoint-join tools is active.
        self.join_tolerance = self.modifier_panel.join_tolerance
        self.tool_manager.set_modifier_parameters(self.modifier_panel.parameters())
        # The indices are valid only after both panes have been inserted.
        # Keeping both panes non-collapsible also prevents a divider drag from
        # turning into a page/window drag when the task panel is narrow.
        self.content_splitter.setCollapsible(0, False)
        self.content_splitter.setCollapsible(1, False)
        self.content_splitter.setStretchFactor(0, 1)
        self.content_splitter.setStretchFactor(1, 0)
        self.content_splitter.handle(1).setToolTip(
            "Arraste para aumentar ou reduzir o painel lateral"
        )
        self.content_splitter.handle(1).setCursor(
            qt_enum(QtCore.Qt, "SizeHorCursor", "CursorShape")
        )
        root.addWidget(self.content_splitter, 1)

        status = QtWidgets.QHBoxLayout()
        status.setContentsMargins(6, 1, 6, 3)
        self.mode_label = QtWidgets.QLabel(self)
        self.mode_label.setMinimumWidth(260)
        self.selection_label = QtWidgets.QLabel("0 selecionados", self)
        self.snap_label = QtWidgets.QLabel("", self)
        self.position_label = QtWidgets.QLabel("X 0,000   Y 0,000 mm", self)
        # Do not let changing digit counts renegotiate the complete root
        # layout on every mouse packet. The coordinate readout owns a stable
        # cell, which is also easier to scan visually.
        self.position_label.setFixedWidth(220)
        self.position_label.setAlignment(
            qt_enum(QtCore.Qt, "AlignRight", "AlignmentFlag")
            | qt_enum(QtCore.Qt, "AlignVCenter", "AlignmentFlag")
        )
        status.addWidget(self.mode_label, 1)
        status.addWidget(self.selection_label)
        status.addSpacing(12)
        status.addWidget(self.snap_label)
        status.addSpacing(12)
        status.addWidget(self.position_label)
        root.addLayout(status)
        QtCore.QTimer.singleShot(0, self._restore_side_panel_size)

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
        button.setToolTip("%s (%s)" % (label, shortcut) if shortcut else label)
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

    def _add_drawing_action_button(self, label, icon_name, callback, tooltip=None):
        """Add a one-shot action to the compact vertical drawing rail."""

        button = QtWidgets.QToolButton(self)
        button.setObjectName("drawingAction" + label.replace(" ", ""))
        button.setText(label)
        button.setAccessibleName(label)
        button.setToolButtonStyle(
            qt_enum(QtCore.Qt, "ToolButtonIconOnly", "ToolButtonStyle")
        )
        button.setIcon(tool_icon(icon_name))
        button.setIconSize(QtCore.QSize(22, 22))
        button.setFixedSize(34, 34)
        button.setToolTip(str(tooltip or label))
        button.clicked.connect(lambda _checked=False: callback())
        self.drawing_toolbar.addWidget(button)
        return button

    def _add_drawing_menu_button(
        self,
        label,
        icon_name,
        actions,
        tooltip=None,
    ):
        """Add an icon-only flyout that reuses the canonical menu actions."""

        button = QtWidgets.QToolButton(self)
        button.setObjectName("drawingGroup" + label.replace(" ", ""))
        button.setText(label)
        button.setAccessibleName(label)
        button.setToolButtonStyle(
            qt_enum(QtCore.Qt, "ToolButtonIconOnly", "ToolButtonStyle")
        )
        button.setIcon(tool_icon(icon_name))
        button.setIconSize(QtCore.QSize(22, 22))
        button.setFixedSize(34, 34)
        button.setToolTip(str(tooltip or label))
        menu = QtWidgets.QMenu(button)
        self._populate_action_menu(menu, str(label), actions)
        button.setMenu(menu)
        popup_mode = getattr(QtWidgets.QToolButton, "InstantPopup", None)
        if popup_mode is None:
            popup_mode = QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup
        button.setPopupMode(popup_mode)
        self.drawing_toolbar.addWidget(button)
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
            icon_name = _MENU_ICON_NAMES.get(str(label), {}).get(str(action_label))
            if icon_name:
                action.setIcon(tool_icon(icon_name, 20))
            actions[str(action_label)] = action
        self._populate_action_menu(menu, str(label), actions)
        button.setMenu(menu)
        popup_mode = getattr(QtWidgets.QToolButton, "InstantPopup", None)
        if popup_mode is None:
            popup_mode = QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup
        button.setPopupMode(popup_mode)
        self.toolbar.addWidget(button)
        self._menu_actions[str(label)] = actions
        return button

    @staticmethod
    def _populate_action_menu(menu, label, actions):
        breaks = _MENU_SECTION_BREAKS.get(str(label), set())
        for action_label, action in actions.items():
            if action_label in breaks and not menu.isEmpty():
                menu.addSeparator()
            menu.addAction(action)

    def _toggle_focus_mode(self, checked=False):
        """Fold only the inspector; the drawing toolbar must remain usable."""
        self._focus_mode = not self._focus_mode
        self._set_side_panel_visible(not self._focus_mode)
        self._update_focus_button()
        self.view.setFocus()

    def _restore_side_panel_size(self):
        if self._focus_mode:
            return
        self._set_side_panel_visible(True)

    def _set_side_panel_visible(self, visible):
        visible = bool(visible)
        self._side_panel_hidden = not visible
        self.side_scroll.setVisible(visible)
        # The tab stays on the splitter edge in both states.  Hiding it when
        # the panel is open made the only available control look like a
        # maximize button and also made it impossible to discover the drawer.
        self.side_toggle_button.setVisible(True)
        if visible:
            width = max(self.content_splitter.width(), 1)
            panel_width = min(400, max(340, width // 3))
            self.content_splitter.setSizes(
                [max(120, width - panel_width), panel_width]
            )
            self.side_toggle_button.setText("›")
            self.side_toggle_button.setAccessibleName(
                translate_text("Ocultar painel lateral")
            )
            self.side_toggle_button.setToolTip(
                translate_text("Ocultar somente o painel lateral")
            )
        else:
            self.content_splitter.setSizes([max(self.content_splitter.width(), 1), 0])
            self.side_toggle_button.setText("‹")
            self.side_toggle_button.setAccessibleName(
                translate_text("Mostrar painel lateral")
            )
            self.side_toggle_button.setToolTip(
                translate_text("Mostrar somente o painel lateral")
            )

    def _toggle_side_panel(self):
        self._set_side_panel_visible(self._side_panel_hidden)
        self._focus_mode = bool(self._side_panel_hidden)
        self.focus_button.setChecked(self._focus_mode)
        self._update_focus_button()
        if not self._side_panel_hidden:
            self.side_scroll.setFocus()

    def _update_focus_button(self):
        """Keep the hidden compatibility button semantically in sync."""
        self.focus_button.setIcon(QtGui.QIcon())
        self.focus_button.setText("‹" if self._focus_mode else "›")
        self.focus_button.setAccessibleName(
            translate_text(
                "Mostrar painel lateral"
                if self._focus_mode
                else "Ocultar painel lateral"
            )
        )
        self.focus_button.setToolTip(
            translate_text(
                "Mostrar somente o painel lateral"
                if self._focus_mode
                else "Ocultar somente o painel lateral"
            )
        )

    def _show_panel_message(self, text):
        self._set_mode_status(text)

    def _set_mode_status(self, text):
        """Render dynamic editor status immediately in the active language."""
        self.mode_label.setText(translate_text(str(text)))

    def _on_piece_selected(self, piece_id):
        self.fit_selection()
        self.pieceSelected.emit(str(piece_id))

    def _activate_modifier(self, mode):
        self.tool_manager.activate(mode)
        self.modifier_panel.set_mode(mode)
        self.modifier_panel.show()
        self.side_scroll.ensureWidgetVisible(self.modifier_panel)
        self.view.setFocus()

    def _show_editing_panels(self):
        """Keep numeric transforms visible after Aspire-style double-click."""
        self.properties_panel.show()
        self.transform_panel.show()
        self.side_scroll.ensureWidgetVisible(self.properties_panel)

    def _connect_session(self):
        self.controller.subscribe_document(self._document_changed)
        self.controller.subscribe_mode(self._mode_changed)
        self.controller.selection.subscribe(self._selection_changed)
        self.tool_manager.statusChanged.connect(self._set_mode_status)
        self.tool_manager.snapChanged.connect(self._snap_status)
        self.view.cursorMoved.connect(self._cursor_moved)
        self.view.keyPressed.connect(self._workflow_key_press)
        self.view.cancelRequested.connect(self._cancel_to_select)
        self.view.viewChanged.connect(self._sync_selection_transform)

    def _sync_selection_transform(self):
        if self.controller.mode == EditorMode.TRANSFORM and self.controller.selection.ids:
            self.overlays.show_selection_transform(self.controller.selection_bounds())
        else:
            self.overlays.clear_selection_transform()

    def eventFilter(self, watched, event):
        key_press = qt_enum(QtCore.QEvent, "KeyPress", "Type")
        if event.type() == key_press and self._canvas_has_keyboard_focus():
            key = event.key()
            modifiers = event.modifiers()
            if (
                key == qt_enum(QtCore.Qt, "Key_A", "Key")
                and bool(modifiers & CTRL_MODIFIER)
            ):
                self.controller.selection.replace(
                    self.controller.canonical_group_selection(
                        self.adapter.editable_visible_entity_ids()
                    )
                )
                event.accept()
                return True
            if key == qt_enum(QtCore.Qt, "Key_C", "Key") and bool(
                modifiers & CTRL_MODIFIER
            ):
                self.copy_selected()
                event.accept()
                return True
            if key == qt_enum(QtCore.Qt, "Key_V", "Key") and bool(
                modifiers & CTRL_MODIFIER
            ):
                self.paste_copied()
                event.accept()
                return True
            if key in (
                qt_enum(QtCore.Qt, "Key_Delete", "Key"),
                qt_enum(QtCore.Qt, "Key_Backspace", "Key"),
            ) and modifiers == qt_enum(QtCore.Qt, "NoModifier", "KeyboardModifier"):
                self.delete_selected()
                event.accept()
                return True
        return super(Editor2DWidget, self).eventFilter(watched, event)

    def copy_selected(self):
        count = self.controller.copy_selection()
        if count:
            message = translate_text(
                "%d objeto(s) copiado(s). Ctrl+V cola a cópia sob o mouse."
            ) % count
            self._set_mode_status(message)
        else:
            self._set_mode_status("Selecione uma peça ou vetor antes de copiar.")
        return bool(count)

    def paste_copied(self):
        viewport = self.view.viewport()
        pointer = self.view.last_pointer_viewport_position()
        if pointer is None:
            global_pointer = viewport.mapFromGlobal(QtGui.QCursor.pos())
            pointer = (global_pointer if viewport.rect().contains(global_pointer)
                       else viewport.rect().center())
        count = self.controller.paste_copied(self.view.mapToScene(pointer))
        if count:
            message = translate_text(
                "%d objeto(s) colado(s). A peça inteira foi preservada; Ctrl+Z desfaz."
            ) % count
            self._set_mode_status(message)
        else:
            self._set_mode_status("Nada copiado ainda. Selecione e use Ctrl+C primeiro.")
        return bool(count)

    def _canvas_has_keyboard_focus(self):
        app = self._application or QtWidgets.QApplication.instance()
        focus = app.focusWidget() if app is not None else None
        return focus is self.view or (
            focus is not None and self.view.isAncestorOf(focus)
        )

    def _document_changed(self, change_set=None):
        if change_set is None:
            # A host Undo/Redo reload may also replace layers, pieces and the
            # work area while retaining the VectorDocument object identity.
            self.adapter.set_document(self.controller.document)
        else:
            self.adapter.refresh(change_set)
        # A plan-view toolpath is derived from the previous vector geometry.
        # Never leave it looking valid after a geometric/layer mutation.
        if change_set is None or any(
            bool(getattr(change_set, name, ()))
            for name in ("added", "changed", "removed", "layers_changed")
        ):
            self.clear_cut_toolpath_preview()
        self._sync_nodes()
        # Commands such as move/resize update the document before the
        # selection signal is emitted.  Refresh the transient frame here too
        # so it never lags one operation behind the vector.
        self._sync_selection_transform()
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
            EditorMode.JOIN_ENDPOINTS_SMOOTH,
            EditorMode.CONNECT,
            EditorMode.SPLICE,
            EditorMode.AUTO_DOGBONE,
            EditorMode.AUTO_TBONE,
        }
        self.modifier_panel.setVisible(mode in modifier_modes)
        polygon_or_star = mode in (EditorMode.DRAW_POLYGON, EditorMode.DRAW_STAR)
        self.polygon_context.setVisible(polygon_or_star)
        if mode == EditorMode.DRAW_STAR:
            self.polygon_context_label.setText("Estrela — pontas")
            if not self._star_points_initialized:
                # The standard Aspire-style star starts with five tips; later
                # visits keep the user-selected count in this shared field.
                self.polygon_sides.setValue(5)
                self._star_points_initialized = True
            self.polygon_sides.setToolTip("Número de pontas da estrela")
            self.star_inner_ratio_label.show()
            self.star_inner_ratio.show()
            self.tool_manager.set_star_points(self.polygon_sides.value())
            self.tool_manager.set_star_inner_ratio(self.star_inner_ratio.value() / 100.0)
        else:
            self.polygon_context_label.setText("Polígono — lados")
            self.polygon_sides.setToolTip("Número de lados do polígono")
            self.star_inner_ratio_label.hide()
            self.star_inner_ratio.hide()
        if mode in modifier_modes:
            self.modifier_panel.set_mode(mode)
            self.side_scroll.ensureWidgetVisible(self.modifier_panel)
        self._sync_selection_transform()
        self.modeChanged.emit(mode.value)

    def _selection_changed(self, ids):
        count = len(ids)
        self.selection_label.setText("%d selecionado%s" % (count, "" if count == 1 else "s"))
        self.delete_button.setEnabled(bool(count))
        self._sync_nodes()
        bounds = self.controller.selection_bounds() if ids else None
        if self.controller.mode == EditorMode.TRANSFORM and ids:
            self.overlays.show_selection_transform(bounds)
        else:
            self.overlays.clear_selection_transform()
        if bounds is not None:
            self.sheet_panel.select_sheet_for_point(
                (bounds.min_x + bounds.max_x) * 0.5,
                (bounds.min_y + bounds.max_y) * 0.5,
            )
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
        # Side-by-side virtual sheets have independent local coordinates.
        # When no object owns the current editing context, entering another
        # sheet must activate its datum before the next drawing click.  Merely
        # hovering never mutates VectorDocument geometry or selection.
        if not self.controller.selection.ids:
            self.sheet_panel.select_sheet_for_point(point.x(), point.y())
        self.position_label.setText(
            "X %.3f   Y %.3f mm"
            % (
                point.x() - self._active_sheet_origin.x(),
                point.y() - self._active_sheet_origin.y(),
            )
        )

    def _on_sheet_selected(self, _index, bounds):
        try:
            min_x, min_y, _max_x, _max_y = map(float, bounds)
        except Exception:
            min_x = min_y = 0.0
        self._active_sheet_origin = QtCore.QPointF(min_x, min_y)
        self.view.set_grid_origin(min_x, min_y)
        self.horizontal_ruler.set_coordinate_origin(min_x, min_y)
        self.vertical_ruler.set_coordinate_origin(min_x, min_y)
        settings = self.controller.snap_engine.settings
        settings.grid_origin_x_mm = min_x
        settings.grid_origin_y_mm = min_y
        self.properties_panel.set_coordinate_origin(min_x, min_y)

    def _fit_sheet_bounds(self, bounds):
        try:
            min_x, min_y, max_x, max_y = map(float, bounds)
        except Exception:
            return
        rect = QtCore.QRectF(min_x, min_y, max_x - min_x, max_y - min_y)
        if rect.width() > 0.0 and rect.height() > 0.0:
            self.view.fit_model_rect(rect)

    def show_toolpath_preview(self, components, operation_label="Corte"):
        """Show a non-destructive, exact-XY CAM overlay in the 2D editor."""
        shown = self.overlays.show_toolpath_preview(components)
        if shown:
            self._show_panel_message(
                "Percurso de %s: cinza = usinagem, magenta tracejado = rápido, "
                "setas = sentido. É só uma vista; vetores e G-code não foram alterados."
                % str(operation_label)
            )
        else:
            self._show_panel_message("O percurso não possui deslocamentos XY para mostrar no plano.")
        self.view.setFocus()
        return shown

    def show_cut_toolpath_preview(self, components):
        """Compatibility wrapper for integrations written before generic CAM."""
        return self.show_toolpath_preview(components, "Corte")

    def clear_cut_toolpath_preview(self):
        self.overlays.clear_toolpath_preview()

    def begin_point_capture(self, callback, cancel_callback=None, status=""):
        """Let a host CAM operation capture canvas points non-destructively."""
        self.tool_manager.begin_point_capture(
            callback,
            cancel_callback=cancel_callback,
            status=status,
        )
        self.view.setFocus()

    def finish_point_capture(self, cancelled=False):
        return self.tool_manager.finish_point_capture(cancelled=cancelled)

    def show_tab_markers(self, positions):
        self.overlays.show_tab_markers(positions)

    def clear_tab_markers(self):
        self.overlays.clear_tab_markers()

    def set_operation_status(self, text):
        self.tool_manager.set_status(text)

    def _snap_status(self, text):
        self.snap_label.setText(("Snap: " + text) if text else "")

    def _cancel_to_select(self):
        """CAD-style right click: abandon transient state and return to select."""
        self.cancel_workflow_preview()
        point_capture_finished = self.tool_manager.finish_point_capture(
            cancelled=True
        )
        if not point_capture_finished:
            self.tool_manager.activate(EditorMode.SELECT)
            self.controller.selection.clear()
        # A host CAM capture consumes the selected vectors as its operation
        # source. Right-click concludes that capture, so clearing selection
        # here would make Apply fall back to the entire document and lose the
        # selected GroupEntity. Normal editor tools keep the established
        # right-click contract above and still clear their selection.
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
        for name in ("endpoint", "intersection", "midpoint", "center", "quadrant", "on_geometry"):
            setattr(settings, name, bool(enabled))

    def _toggle_grid(self, enabled):
        enabled = bool(enabled)
        self.controller.snap_engine.settings.grid = enabled
        self.view.set_grid_visible(enabled)
        self.adapter.set_work_area_fill_visible(enabled)
        self.grid_spacing.setEnabled(enabled)

    def _toggle_smart_snap(self, enabled):
        settings = self.controller.snap_engine.settings
        for name in ("horizontal", "vertical", "angle", "perpendicular", "tangent"):
            setattr(settings, name, bool(enabled))

    def _set_grid_spacing(self, value):
        spacing = float(value)
        self.controller.snap_engine.settings.grid_spacing_mm = spacing
        self.view.set_grid_spacing(spacing)

    def _set_shape_sides(self, value):
        """Route the shared count field to the active regular/star polygon tool."""
        if self.controller.mode == EditorMode.DRAW_STAR:
            self.tool_manager.set_star_points(value)
        else:
            self.tool_manager.set_polygon_sides(value)

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
        remnant_cuts=(),
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
        self.adapter.show_preview_remnant_cuts(remnant_cuts)
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
        self.adapter.clear_preview_remnant_cuts()

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
        self._set_mode_status(
            getattr(issue, "message", "Ocorrência selecionada.")
        )

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
        self.fit_entities(self.controller.selection.ids)

    def fit_entities(self, entity_ids):
        """Frame entities without changing the current selection state."""
        rect = QtCore.QRectF()
        for entity_id in entity_ids:
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
            self._set_mode_status(error)
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
