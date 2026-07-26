"""Modal tool manager."""

from __future__ import annotations

from woodcam_editor.application import CommandExecutionCancelled, EditorMode

from ..compat import CTRL_MODIFIER, SHIFT_MODIFIER, Signal, QtCore, QtWidgets, qt_enum
from .base import has_modifier
from .draw import ArcTool, BezierTool, CircleTool, EllipseTool, LineTool, PolygonTool, StarTool, PolylineTool, RectangleTool
from .node import NodeTool
from .modifier import (
    AutomaticDogboneTool,
    AutomaticTBoneTool,
    DogboneTool,
    ExtendTool,
    FilletTool,
    OffsetTool,
    TBoneTool,
    TrimTool,
)
from .connect import ConnectTool, JoinEndpointsSmoothTool, JoinEndpointsTool, SpliceTool
from .measure import MeasureTool
from .select import SelectTool


class ToolManager(QtCore.QObject):
    statusChanged = Signal(str)
    snapChanged = Signal(str)
    previewAvailabilityChanged = Signal(bool)
    editSelectionRequested = Signal()

    def __init__(self, view, controller, adapter, overlays, parent=None):
        super(ToolManager, self).__init__(parent)
        self.view = view
        self.controller = controller
        self.adapter = adapter
        self.overlays = overlays
        self.tools = {
            EditorMode.SELECT: SelectTool(self),
            EditorMode.MEASURE: MeasureTool(self),
            EditorMode.NODE_EDIT: NodeTool(self),
            EditorMode.DRAW_LINE: LineTool(self),
            EditorMode.DRAW_POLYLINE: PolylineTool(self),
            EditorMode.DRAW_RECTANGLE: RectangleTool(self),
            EditorMode.DRAW_CIRCLE: CircleTool(self),
            EditorMode.DRAW_ELLIPSE: EllipseTool(self),
            EditorMode.DRAW_ARC: ArcTool(self),
            EditorMode.DRAW_BEZIER: BezierTool(self),
            EditorMode.DRAW_POLYGON: PolygonTool(self),
            EditorMode.DRAW_STAR: StarTool(self),
            EditorMode.TRIM: TrimTool(self),
            EditorMode.EXTEND: ExtendTool(self),
            EditorMode.OFFSET: OffsetTool(self),
            EditorMode.FILLET: FilletTool(self),
            EditorMode.DOGBONE: DogboneTool(self),
            EditorMode.TBONE: TBoneTool(self),
            EditorMode.JOIN_ENDPOINTS: JoinEndpointsTool(self),
            EditorMode.JOIN_ENDPOINTS_SMOOTH: JoinEndpointsSmoothTool(self),
            EditorMode.CONNECT: ConnectTool(self),
            EditorMode.SPLICE: SpliceTool(self),
            EditorMode.AUTO_DOGBONE: AutomaticDogboneTool(self),
            EditorMode.AUTO_TBONE: AutomaticTBoneTool(self),
        }
        self.modifier_parameters = {
            "offset_distance": 5.0,
            "fillet_radius": 3.0,
            "contour_role": "auto",
            "tbone_side": "auto",
            "join_tolerance": 0.2,
            "splice_route": "short",
        }
        self.active = None
        view.pointerPressed.connect(self.pointer_press)
        view.pointerMoved.connect(self.pointer_move)
        view.pointerReleased.connect(self.pointer_release)
        view.pointerDoubleClicked.connect(self.pointer_double_click)
        view.keyPressed.connect(self.key_press)
        self.activate(EditorMode.SELECT)

    def activate(self, mode, entity_id=None):
        mode = mode if isinstance(mode, EditorMode) else EditorMode(mode)
        if self.active is not None:
            self.active.deactivate()
        self.controller.set_mode(mode, entity_id)
        actual = self.controller.mode
        self.active = self.tools[actual]
        self.active.activate(self.controller.node_entity_id if actual == EditorMode.NODE_EDIT else entity_id)
        self.statusChanged.emit(self._mode_label(actual))

    def set_polygon_sides(self, sides):
        self.tools[EditorMode.DRAW_POLYGON].set_sides(sides)

    def set_star_points(self, points):
        self.tools[EditorMode.DRAW_STAR].set_points(points)

    def set_star_inner_ratio(self, ratio):
        self.tools[EditorMode.DRAW_STAR].set_inner_ratio(ratio)

    def set_snap_status(self, text):
        self.snapChanged.emit(text)

    def set_status(self, text):
        self.statusChanged.emit(str(text))

    def set_preview_available(self, available):
        self.previewAvailabilityChanged.emit(bool(available))

    def confirm_active_preview(self):
        confirm = getattr(self.active, "confirm_preview", None)
        try:
            return bool(confirm()) if callable(confirm) else False
        except CommandExecutionCancelled as error:
            self.set_status(str(error))
            return False

    def set_modifier_parameters(self, parameters):
        self.modifier_parameters.update(dict(parameters or {}))
        recompute = getattr(self.active, "recompute_last_hover", None)
        if callable(recompute):
            recompute()

    def pointer_press(self, event):
        self._dispatch(self.active.pointer_press, event)

    def pointer_move(self, event):
        self._dispatch(self.active.pointer_move, event)

    def pointer_release(self, event):
        self._dispatch(self.active.pointer_release, event)

    def pointer_double_click(self, event):
        self._dispatch(self.active.pointer_double_click, event)

    def _dispatch(self, callback, *args):
        try:
            return callback(*args)
        except CommandExecutionCancelled as error:
            self.set_status(str(error))
            return None

    def key_press(self, event):
        key = event.key()
        modifiers = event.modifiers()
        owner = self.parent()
        preview_bar = getattr(owner, "workflow_preview_bar", None)
        if preview_bar is not None and preview_bar.is_active:
            if key == qt_enum(QtCore.Qt, "Key_Escape", "Key"):
                owner.cancel_workflow_preview()
                event.accept()
                return
            if key in (
                qt_enum(QtCore.Qt, "Key_Return", "Key"),
                qt_enum(QtCore.Qt, "Key_Enter", "Key"),
            ):
                owner._apply_workflow_preview(preview_bar.payload)
                event.accept()
                return
        if key == qt_enum(QtCore.Qt, "Key_Z", "Key") and has_modifier(modifiers, CTRL_MODIFIER):
            if has_modifier(modifiers, SHIFT_MODIFIER):
                self._dispatch(self.controller.redo)
            else:
                self._dispatch(self.controller.undo)
            event.accept()
            return
        if key == qt_enum(QtCore.Qt, "Key_Y", "Key") and has_modifier(modifiers, CTRL_MODIFIER):
            self._dispatch(self.controller.redo)
            event.accept()
            return
        shortcuts = {
            qt_enum(QtCore.Qt, "Key_S", "Key"): EditorMode.SELECT,
            qt_enum(QtCore.Qt, "Key_L", "Key"): EditorMode.DRAW_LINE,
            qt_enum(QtCore.Qt, "Key_P", "Key"): EditorMode.DRAW_POLYLINE,
            qt_enum(QtCore.Qt, "Key_R", "Key"): EditorMode.DRAW_RECTANGLE,
            qt_enum(QtCore.Qt, "Key_C", "Key"): EditorMode.DRAW_CIRCLE,
            qt_enum(QtCore.Qt, "Key_E", "Key"): EditorMode.DRAW_ELLIPSE,
            qt_enum(QtCore.Qt, "Key_A", "Key"): EditorMode.DRAW_ARC,
            qt_enum(QtCore.Qt, "Key_B", "Key"): EditorMode.DRAW_BEZIER,
            qt_enum(QtCore.Qt, "Key_G", "Key"): EditorMode.DRAW_POLYGON,
        }
        if key in shortcuts and modifiers == QtCore.Qt.NoModifier:
            self.activate(shortcuts[key])
            event.accept()
            return
        self._dispatch(self.active.key_press, event)

    @staticmethod
    def _mode_label(mode):
        return {
            EditorMode.SELECT: "Selecionar — 1 clique seleciona; arraste move o corpo",
            EditorMode.MEASURE: "Medir — dois pontos com Snap; não altera o desenho",
            EditorMode.NODE_EDIT: "Nós — clique seleciona; somente arrastar move",
            EditorMode.DRAW_LINE: "Linha — clique no início e no fim",
            EditorMode.DRAW_POLYLINE: "Polilinha — cliques; Enter termina; Tab fecha",
            EditorMode.DRAW_RECTANGLE: "Retângulo — dois cantos",
            EditorMode.DRAW_CIRCLE: "Círculo — centro e raio",
            EditorMode.DRAW_ELLIPSE: "Elipse — centro, raio X e raio Y",
            EditorMode.DRAW_ARC: "Arco — início, ponto intermediário e fim",
            EditorMode.DRAW_BEZIER: "Bézier — início, controle 1, controle 2 e fim",
            EditorMode.DRAW_POLYGON: "Polígono — centro e raio",
            EditorMode.DRAW_STAR: "Estrela — centro e ponta externa",
            EditorMode.TRIM: "Trim — prévia no hover; clique aplica",
            EditorMode.EXTEND: "Extend — prévia na ponta; clique aplica",
            EditorMode.OFFSET: "Offset — prévia do contorno; clique aplica",
            EditorMode.FILLET: "Filete — prévia no canto; clique aplica",
            EditorMode.DOGBONE: "Dogbone — somente canto interno de 90°",
            EditorMode.TBONE: "T-bone — somente canto interno de 90°",
            EditorMode.JOIN_ENDPOINTS: "Unir 2 pontas — escolha exatamente as duas pontas",
            EditorMode.JOIN_ENDPOINTS_SMOOTH: "Unir 2 pontas suave — escolha exatamente as duas pontas",
            EditorMode.CONNECT: "Projetar ponta — escolha a ponta e depois uma reta/curva alvo",
            EditorMode.SPLICE: "Emendar — escolha caminho aberto e contorno alvo",
            EditorMode.AUTO_DOGBONE: "Dogbone automático — revisar prévia total",
            EditorMode.AUTO_TBONE: "T-bone automático — revisar prévia total",
        }.get(mode, str(mode))


__all__ = ["ToolManager"]
