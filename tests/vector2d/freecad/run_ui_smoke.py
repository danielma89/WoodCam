"""FreeCADCmd smoke for the complete WoodCAM dialog/editor integration.

Run headlessly with::

    env -u QT_QPA_PLATFORMTHEME QT_QPA_PLATFORM=offscreen \
      freecadcmd tests/vector2d/freecad/run_ui_smoke.py
"""

from __future__ import annotations

import os
import sys
import math
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import FreeCAD  # noqa: E402
import Part  # noqa: E402
import Sketcher  # noqa: E402
from pivy import coin  # noqa: E402
from PySide6 import QtCore, QtWidgets  # noqa: E402

import ui  # noqa: E402
from woodcam_3d.coin_toolpath import (  # noqa: E402
    CoinToolpathOverlay,
    CoinToolpathViewProvider,
    create_coin_toolpath_feature,
)
from woodcam_3d.storage import decode_moves  # noqa: E402

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

first = FreeCAD.newDocument("WoodCAMEditorUiSmokeA")
first.UndoMode = 1
dialog = ui.WoodCAM2DDialog()
dialog.show()
app.processEvents()
widget = dialog.vector_editor_widget
assert widget is not None
assert "Importar peças planas pelo PanelNest…" in widget._menu_actions["Arquivo"]

# Os botões 3D do fluxo Aspire são operações nativas do WoodCAM: cada um tem
# sua própria aba, configurações e persistência, sem esconder o Editor 2D.
tab_titles = [
    dialog._tab_title(index)
    for index in range(dialog.operation_tabs.count())
]
assert tab_titles.index("Desbaste 3D") == tab_titles.index("Preenchimento") + 1
assert tab_titles.index("Acabamento 3D") == tab_titles.index("Desbaste 3D") + 1
assert tab_titles.index("Editor 2D") == tab_titles.index("Acabamento 3D") + 1
assert "Sketch legado (diagnóstico)" not in tab_titles
visible_tab_texts = [
    dialog.operation_tabs.tabText(index)
    for index in range(dialog.operation_tabs.count())
]
assert [text for text in visible_tab_texts if text] == ["Trabalho", "Material"]
# A identificação estável da operação não pode depender do texto traduzido da
# aba. O menu diz "Furos", mas a aba de produção se chama "Furo".
hole_tab_index = dialog._operation_tab_index("holes")
assert hole_tab_index is not None
assert dialog._tab_title(hole_tab_index) == "Furo"
dialog._vector_editor_configure_toolpath("holes")
assert dialog.operation_tabs.currentIndex() == hole_tab_index
assert dialog._operation_mode_for_index(hole_tab_index) == "holes"
dialog.operation_tabs.setCurrentIndex(0)
app.processEvents()
tab_bar_image = dialog.operation_tabs.tabBar().grab().toImage()
for index, title in enumerate(tab_titles):
    assert not dialog.operation_tabs.tabIcon(index).isNull(), title
    assert dialog.operation_tabs.tabToolTip(index) == title
    if title not in {"Trabalho", "Material"}:
        tab_bar = dialog.operation_tabs.tabBar()
        assert (
            tab_bar.tabSizeHint(index).width()
            <= dialog.operation_tabs.iconSize().width() + 26
        ), title
        # O estilo Fusion reserva uma área de texto mesmo com o rótulo vazio.
        # A barra compacta deve ignorá-la e pintar no centro geométrico da aba.
        icon_center = tab_bar._icon_paint_rect(index).center()
        tab_center = tab_bar.tabRect(index).center()
        assert abs(icon_center.x() - tab_center.x()) <= 1, title
        assert abs(icon_center.y() - tab_center.y()) <= 1, title
        icon_rect = tab_bar._icon_paint_rect(index)
        painted_colors = {
            tab_bar_image.pixelColor(x_value, y_value).rgba()
            for x_value in range(icon_rect.left(), icon_rect.right() + 1)
            for y_value in range(icon_rect.top(), icon_rect.bottom() + 1)
        }
        # Mais que um simples gradiente de fundo: o desenho do ícone realmente
        # precisa ter chegado ao framebuffer da barra.
        assert len(painted_colors) > 8, title
assert dialog.rough3d_boundary_combo.count() == 4
assert dialog.rough3d_strategy_combo.count() == 2
assert dialog.finish3d_strategy_combo.count() == 2
assert dialog.rough3d_reverse_check.text()
assert dialog.finish3d_reverse_check.text()
assert dialog.operation_pass_labels["finish3d"].isHidden()

# O botão no canto direito destaca exatamente o mesmo Editor 2D numa janela
# nativa; fechar a janela devolve o widget e todo o seu estado à aba.
assert dialog.detach_editor_button is dialog.operation_tabs.cornerWidget()
editor_widget_identity = dialog.vector_editor_widget
dialog._detach_vector_editor()
app.processEvents()
assert dialog._detached_editor_window is not None
assert dialog.vector_editor_widget is editor_widget_identity
assert editor_widget_identity.window() is dialog._detached_editor_window
dialog._detached_editor_window.reject()
app.processEvents()
assert dialog.vector_editor_widget is editor_widget_identity
assert editor_widget_identity.parentWidget() is dialog._vector_editor_tab

# A vista CAM nunca pode continuar exibindo uma trajetória calculada com uma
# fresa/stepover anterior. O resumo também expõe o passo físico real.
truth_settings = {
    "operation_mode": "finish3d",
    "tool_name": "Topo esférico 4 mm",
    "tool_diameter": 4.0,
    "finish3d_stepover_percent": 45.0,
}
dialog._set_toolpath_truth(
    truth_settings,
    [{"type": "rapid"}, {"type": "feed_cut"}],
    "PRÉVIA EXATA",
)
assert "1.800 mm" in dialog.toolpath_truth_label.text()
stepover_field = dialog.operation_fields["finish3d"]["finish3d_stepover_percent"]
current_stepover = stepover_field.text().strip()
stepover_field.setText("43" if current_stepover != "43" else "44")
app.processEvents()
assert not dialog._toolpath_view_valid
assert "desatualizado" in dialog.toolpath_truth_label.text().lower()

# Percursos 3D grandes conservam todos os movimentos persistidos/G-code, mas
# a projeção OCC e a animação têm limites independentes para não derrubar FPS.
large_frames = [
    {
        "type": "feed_cut",
        "x": float(index),
        "y": 0.0,
        "z": -1.0,
        "time_ms": float(index),
        "move_index": index,
    }
    for index in range(30000)
]
assert len(dialog._downsample_frames_preserving_moves(large_frames, 8000)) <= 8000
large_moves = [
    {"type": "feed_cut", "x": index * 0.01, "y": 0.0, "z": -1.0}
    for index in range(24050)
]
display_components = dialog._toolpath_components(large_moves)
assert 0 < sum(len(display_components[key]) for key in ("rapid", "ramp", "cut", "corner")) <= 12010
full_display_components = dialog._toolpath_components(
    large_moves,
    max_display_segments=None,
)
assert sum(
    len(full_display_components[key])
    for key in ("rapid", "ramp", "cut", "corner")
) == len(large_moves) - 1


class _FakeActiveView:
    def __init__(self):
        self.scene = coin.SoSeparator()

    def getSceneGraph(self):
        return self.scene


class _FakeGuiDocument:
    def __init__(self):
        self.ActiveView = _FakeActiveView()


fake_gui_document = _FakeGuiDocument()
overlay = CoinToolpathOverlay(fake_gui_document)
overlay.update(full_display_components)
assert overlay.root is not None
assert fake_gui_document.ActiveView.scene.getNumChildren() == 1
# Os 24 mil segmentos consecutivos viram uma única polilinha Coin, sem criar
# 24 mil objetos ou arestas OCC no documento.
cut_branch = overlay.root.getChild(1)
cut_line_set = cut_branch.getChild(3)
assert cut_line_set.numVertices.getNum() == 1
assert cut_line_set.numVertices[0] == len(large_moves)
overlay.clear()
assert fake_gui_document.ActiveView.scene.getNumChildren() == 0

# Desbaste, acabamento, Corte e Preenchimento usam o overlay integral acima;
# somente a posição animada da fresa é amostrada. Furo conserva o rastro
# progressivo tradicional.
assert dialog._simulation_uses_exact_static_path(
    {"operation_mode": "finish3d"}
)
assert dialog._simulation_uses_exact_static_path(
    {"operation_mode": "rough3d"}
)
assert dialog._simulation_uses_exact_static_path(
    {"operation_mode": "cut"}
)
assert not dialog._simulation_uses_exact_static_path(
    {"operation_mode": "holes"}
)
assert dialog._simulation_uses_exact_static_path(
    {"operation_mode": "pocket"}
)
captured_cut_overlay = {}
captured_pocket_overlay = {}
original_lightweight_overlay = dialog._set_lightweight_toolpath_overlay
try:
    dialog._set_lightweight_toolpath_overlay = lambda components: captured_cut_overlay.update(
        components=components
    )
    assert dialog._show_exact_simulation_toolpath(
        {"operation_mode": "cut", "tool_name": "Topo reto", "tool_diameter": 4.0},
        large_moves,
        "SIMULAÇÃO EXATA = G-CODE",
    )
    dialog._set_lightweight_toolpath_overlay = lambda components: captured_pocket_overlay.update(
        components=components
    )
    assert dialog._show_exact_simulation_toolpath(
        {"operation_mode": "pocket", "tool_name": "Topo reto", "tool_diameter": 4.0},
        large_moves,
        "SIMULAÇÃO EXATA = G-CODE",
    )
finally:
    dialog._set_lightweight_toolpath_overlay = original_lightweight_overlay
assert sum(
    len(captured_cut_overlay["components"][key])
    for key in ("rapid", "ramp", "cut", "corner")
) == len(large_moves) - 1
assert sum(
    len(captured_pocket_overlay["components"][key])
    for key in ("rapid", "ramp", "cut", "corner")
) == len(large_moves) - 1
interpolation_frames = [
    {"x": 0.0, "y": 2.0, "z": -1.0},
    {"x": 10.0, "y": 6.0, "z": -3.0},
]
frame_index, position, finished = dialog._interpolated_simulation_position(
    interpolation_frames,
    [0.0, 1000.0],
    250.0,
)
assert frame_index == 0
assert position == (2.5, 3.0, -1.5)
assert not finished
_index, final_position, finished = dialog._interpolated_simulation_position(
    interpolation_frames,
    [0.0, 1000.0],
    1200.0,
)
assert final_position == (10.0, 6.0, -3.0)
assert finished

# Em percursos 3D, a linha do tempo da fresa conserva cada destino real do
# G-code. A interpolacao ocorre somente dentro do segmento real atual; assim
# uma descida pequena de Z nao desaparece mesmo quando o percurso possui
# centenas de milhares de movimentos.
exact_moves = [
    {"type": "rapid", "x": 0.0, "y": 0.0, "z": 5.0},
    {"type": "feed_plunge", "x": 0.0, "y": 0.0, "z": -1.0},
    {"type": "feed_cut", "x": 10.0, "y": 0.0, "z": -3.0},
]
exact_settings = {
    "simulation_speed_multiplier": 1.0,
    "rapid_feed": 4000.0,
    "feed_z": 300.0,
    "feed_xy": 1200.0,
    "ramp_feed": 600.0,
}
exact_timeline = dialog._exact_3d_simulation_timeline(
    exact_moves,
    exact_settings,
)
assert len(exact_timeline["times"]) == len(exact_moves)
assert tuple(exact_timeline[axis][1] for axis in ("x", "y", "z")) == (
    0.0,
    0.0,
    -1.0,
)
cut_midpoint_ms = (
    exact_timeline["times"][1] + exact_timeline["times"][2]
) * 0.5
segment_index, exact_position, finished = dialog._exact_timeline_position(
    exact_timeline,
    cut_midpoint_ms,
)
assert segment_index == 1
assert all(
    abs(actual - expected) <= 1e-9
    for actual, expected in zip(exact_position, (5.0, 0.0, -2.0))
)
assert not finished
_index, exact_final_position, finished = dialog._exact_timeline_position(
    exact_timeline,
    exact_timeline["times"][-1] + 1.0,
)
assert exact_final_position == (10.0, 0.0, -3.0)
assert finished

# A ferramenta visual conserva o diametro/ponta de contato, recebe helices
# leves para que a rotacao seja perceptivel e gira sem recomputar o documento.
realistic_cutter = dialog._make_cutter_shape(6.0, "end_mill")
assert not realistic_cutter.isNull()
assert len(realistic_cutter.Edges) > len(Part.makeCylinder(3.0, 30.0).Edges)
half_turn = dialog._visual_cutter_angle({"rpm": 9000}, 1000.0)
assert abs(half_turn - math.pi) <= 1e-9

# Aplicar uma operação 3D persiste todos os movimentos compactados, mas não
# duplica o percurso como propriedades/objetos no FCStd. A visualização
# completa continua sendo o overlay testado acima.
applied_3d = dialog._create_applied_operation(
    {
        "operation_mode": "finish3d",
        "operation_name": "Acabamento smoke",
        "tool_diameter": 3.0,
    },
    large_moves,
    selection_snapshot=[],
)
assert applied_3d.MoveCount == len(large_moves)
assert decode_moves(applied_3d.MovesCompressedBase64) == large_moves
assert list(applied_3d.Group) == []

first.removeObject(applied_3d.Name)
operations_root = first.getObject("WoodCAM2D_Operations")
if operations_root is not None:
    first.removeObject(operations_root.Name)

coin_path = create_coin_toolpath_feature(
    first,
    "WoodCAMCoinPathSmoke",
    "Percurso Coin leve",
    display_components["cut"][:20],
)
assert len(coin_path.Points) == 21
assert list(coin_path.LineCounts) == [21]
class _FakePathView:
    def __init__(self, obj):
        self.Object = obj
        self.mode = None

    def addDisplayMode(self, _root, mode):
        self.mode = mode


fake_path_view = _FakePathView(coin_path)
coin_provider = CoinToolpathViewProvider()
coin_provider.attach(fake_path_view)
assert coin_provider.root is not None
assert fake_path_view.mode == "Percurso"
assert coin_provider.lines.numVertices.getNum() == 1
assert coin_provider.pick_style.style.getValue() == coin.SoPickStyle.UNPICKABLE
coin_provider.set_segment_pairs(
    [((float(index), 0.0, 0.0), (float(index + 1), 0.0, 0.0)) for index in range(1000)]
)
assert coin_provider.coordinates.point.getNum() == 2000
assert coin_provider.lines.numVertices.getNum() == 1000
first.removeObject(coin_path.Name)

# A prévia 3D mantém o relevo/STL visível sob o percurso. Fontes 2D
# substituídas pela cópia posicionada continuam sendo ocultadas.
class _FakeVisibilityView:
    def __init__(self, visible):
        self.Visibility = visible


class _FakeVisibilitySource:
    def __init__(self, name, document, visible, parents=()):
        self.Name = name
        self.Document = document
        self.ViewObject = _FakeVisibilityView(visible)
        self.InList = list(parents)


relief_root = _FakeVisibilitySource("ReliefRoot", first, False)
relief_group = _FakeVisibilitySource(
    "ReliefGroup",
    first,
    False,
    parents=(relief_root,),
)
kept_source = _FakeVisibilitySource(
    "ReliefSource",
    first,
    False,
    parents=(relief_group,),
)
hidden_source = _FakeVisibilitySource("VectorSource", first, True)
states = dialog._preview_source_visibility_changes(
    first,
    [kept_source, hidden_source],
    keep_visible=(kept_source.Name,),
)
assert kept_source.ViewObject.Visibility is True
assert relief_group.ViewObject.Visibility is True
assert relief_root.ViewObject.Visibility is True
assert hidden_source.ViewObject.Visibility is False
assert states == [
    ("ReliefSource", False),
    ("VectorSource", True),
    ("ReliefGroup", False),
    ("ReliefRoot", False),
]

# Trabalho é apenas preview durante a digitação e vira um comando persistente
# no editingFinished, mesmo antes de existir qualquer vetor.
dialog.fields["job_origin_x"].setText("0")
dialog.fields["job_origin_y"].setText("0")
dialog.fields["job_width"].setText("321")
dialog.fields["job_height"].setText("654")
assert widget.document.work_area is None
dialog.fields["job_height"].editingFinished.emit()
app.processEvents()
assert widget.document.work_area.width == 321.0
assert widget.document.work_area.height == 654.0

# Mudar X/Y em Trabalho move o retângulo tracejado em coordenadas de cena sem
# reenquadrar a câmera (o desenho não pode parecer saltar no sentido oposto).
camera_probe = QtCore.QPointF(25.0, 30.0)
probe_screen_before_origin = widget.view.mapFromScene(camera_probe)
dialog.fields["job_origin_x"].setText("40")
dialog.fields["job_origin_y"].setText("25")
preview_rect = widget.adapter.work_area_item.rect()
assert (preview_rect.x(), preview_rect.y()) == (40.0, 25.0)
dialog.fields["job_origin_y"].editingFinished.emit()
app.processEvents()
assert widget.document.work_area.min_x == 40.0
assert widget.document.work_area.min_y == 25.0
persisted_rect = widget.adapter.work_area_item.rect()
assert (persisted_rect.x(), persisted_rect.y()) == (40.0, 25.0)
assert widget.view.mapFromScene(camera_probe) == probe_screen_before_origin
first.undo()
dialog._sync_vector_editor_from_freecad_history()
app.processEvents()
assert widget.document.work_area.min_x == 0.0
assert widget.document.work_area.min_y == 0.0
assert first.getObject("WoodCAM2D_VectorDocument") is not None
first.undo()
dialog._sync_vector_editor_from_freecad_history()
app.processEvents()
assert widget.document.work_area is None
first.redo()
dialog._sync_vector_editor_from_freecad_history()
app.processEvents()
assert widget.document.work_area.width == 321.0
assert widget.document.work_area.height == 654.0
assert widget.document.work_area.min_x == 0.0
assert widget.document.work_area.min_y == 0.0
first.redo()
dialog._sync_vector_editor_from_freecad_history()
app.processEvents()
assert widget.document.work_area.min_x == 40.0
assert widget.document.work_area.min_y == 25.0

# O Editor 2D ja fornece coordenadas absolutas dentro da area tracejada. O CAM
# nao pode alinhar novamente os bounds do vetor pelo datum da aba Material:
# isso separava visualmente o contorno de corte do vetor original. A origem da
# maquina, entretanto, continua sendo o anchor da area de Trabalho.
editor_cut_geometry = {
    "contours": [[
        (140.0, 140.0),
        (240.0, 140.0),
        (240.0, 240.0),
        (140.0, 240.0),
        (140.0, 140.0),
    ]],
    "holes": [],
}
previous_tab_index = dialog.operation_tabs.currentIndex()
dialog.operation_tabs.setCurrentIndex(dialog._tab_index("Corte"))
editor_cut_settings = dialog._collect_settings()
dialog.operation_tabs.setCurrentIndex(previous_tab_index)
editor_cut_settings.update(
    outer_cut_side=ui.CUT_SIDE_ON_LINE,
    cut_side=ui.CUT_SIDE_ON_LINE,
    smart_entry=False,
    use_ramp=False,
    ramp_length=0.0,
    return_to_start=False,
    cut_tabs_enabled=False,
    cut_separate_last_pass=False,
)
original_active_geometry = dialog._active_geometry
original_editor_source = dialog._use_vector_editor_for_cam
original_vector_document = dialog._vector_editor_document
try:
    dialog._active_geometry = lambda: editor_cut_geometry
    dialog._use_vector_editor_for_cam = True
    dialog._vector_editor_document = widget.document
    editor_cut_moves = dialog._build_moves_from_selection(editor_cut_settings)
finally:
    dialog._active_geometry = original_active_geometry
    dialog._use_vector_editor_for_cam = original_editor_source
    dialog._vector_editor_document = original_vector_document
assert editor_cut_settings["_xy_origin_offset"] == (0.0, 0.0)
assert editor_cut_settings["_work_area_bounds"] == [40.0, 25.0, 361.0, 679.0]
editor_cut_components = dialog._toolpath_components(
    editor_cut_moves,
    max_display_segments=None,
)
assert editor_cut_components["rapid"]
assert editor_cut_components["rapid"][0][0][:2] == (40.0, 25.0)
assert editor_cut_components["rapid"][0][1][:2] == (140.0, 240.0)
feed_xy = [
    (float(move["x"]), float(move["y"]))
    for move in editor_cut_moves
    if str(move.get("type", "")).startswith("feed_")
    and move.get("x") is not None
    and move.get("y") is not None
]
assert feed_xy
assert min(point[0] for point in feed_xy) == 140.0
assert max(point[0] for point in feed_xy) == 240.0
assert min(point[1] for point in feed_xy) == 140.0
assert max(point[1] for point in feed_xy) == 240.0

# O caminho real relatado pelo usuario passa pelo intercâmbio
# Editor 2D -> PanelNest e depois usa Corte/Aplicar com o Part::Feature
# materializado selecionado. Esse Shape já está no XY final do Editor e não
# pode ser ancorado outra vez no zero da área de Trabalho.
panelnest_exchange_part = first.addObject(
    "Part::Feature",
    "WoodCAM2DPanelNestAbsoluteXYSmoke",
)
panelnest_exchange_part.Shape = Part.makeBox(100.0, 100.0, 15.0)
panelnest_exchange_part.Placement.Base = FreeCAD.Vector(140.0, 140.0, 0.0)
panelnest_exchange_part.addProperty(
    "App::PropertyString",
    "WoodCAMExchangeLayoutMode",
)
panelnest_exchange_part.WoodCAMExchangeLayoutMode = "preserve_editor_xy"
first.recompute()
panelnest_cut_settings = dict(editor_cut_settings)
panelnest_cut_settings.pop("_editor_coordinates_absolute", None)
panelnest_cut_settings.pop("_work_area_bounds", None)
panelnest_geometry = ui._extract_geometry_from_object(panelnest_exchange_part)
original_active_geometry = dialog._active_geometry
original_editor_source = dialog._use_vector_editor_for_cam
had_selection_api = hasattr(ui.FreeCADGui, "Selection")
original_selection_api = getattr(ui.FreeCADGui, "Selection", None)


class _PanelNestSmokeSelection:
    @staticmethod
    def getSelection():
        return [panelnest_exchange_part]


try:
    dialog._active_geometry = lambda: panelnest_geometry
    dialog._use_vector_editor_for_cam = False
    # FreeCADCmd não cria a API Selection; o substituto contém exatamente o
    # Part::Feature que a interface gráfica entrega nesse fluxo.
    ui.FreeCADGui.Selection = _PanelNestSmokeSelection()
    assert dialog._selected_freecad_geometry_is_absolute()
    panelnest_cut_moves = dialog._build_moves_from_selection(
        panelnest_cut_settings
    )
finally:
    dialog._active_geometry = original_active_geometry
    dialog._use_vector_editor_for_cam = original_editor_source
    if had_selection_api:
        ui.FreeCADGui.Selection = original_selection_api
    else:
        delattr(ui.FreeCADGui, "Selection")
assert panelnest_cut_settings["_xy_origin_offset"] == (0.0, 0.0)
panelnest_feed_xy = [
    (float(move["x"]), float(move["y"]))
    for move in panelnest_cut_moves
    if str(move.get("type", "")).startswith("feed_")
    and move.get("x") is not None
    and move.get("y") is not None
]
assert panelnest_feed_xy
assert min(point[0] for point in panelnest_feed_xy) == 140.0
assert max(point[0] for point in panelnest_feed_xy) == 240.0
assert min(point[1] for point in panelnest_feed_xy) == 140.0
assert max(point[1] for point in panelnest_feed_xy) == 240.0
first.removeObject(panelnest_exchange_part.Name)

# Um Sketch/Part comum selecionado diretamente no FreeCAD também já entrega
# coordenadas de documento. O datum define somente início/retorno; nunca pode
# reposicionar o contorno para o zero global.
plain_freecad_sketch = first.addObject(
    "Sketcher::SketchObject",
    "PlainAbsoluteXYSketchSmoke",
)
plain_freecad_sketch.addGeometry(
    [
        Part.LineSegment(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(100, 0, 0)),
        Part.LineSegment(FreeCAD.Vector(100, 0, 0), FreeCAD.Vector(100, 100, 0)),
        Part.LineSegment(FreeCAD.Vector(100, 100, 0), FreeCAD.Vector(0, 100, 0)),
        Part.LineSegment(FreeCAD.Vector(0, 100, 0), FreeCAD.Vector(0, 0, 0)),
    ],
    False,
)
plain_freecad_sketch.Placement.Base = FreeCAD.Vector(140.0, 140.0, 0.0)
first.recompute()
plain_freecad_geometry = ui._extract_geometry_from_object(plain_freecad_sketch)
plain_bounds = dialog._geometry_bounds(plain_freecad_geometry)
assert plain_bounds == (140.0, 140.0, 240.0, 240.0), plain_bounds
freecad_cut_settings = dict(editor_cut_settings)
freecad_cut_settings.pop("_editor_coordinates_absolute", None)
freecad_cut_settings.pop("_work_area_bounds", None)
original_active_geometry = dialog._active_geometry
original_editor_source = dialog._use_vector_editor_for_cam
try:
    dialog._active_geometry = lambda: plain_freecad_geometry
    dialog._use_vector_editor_for_cam = False
    freecad_cut_moves = dialog._build_moves_from_selection(freecad_cut_settings)
finally:
    dialog._active_geometry = original_active_geometry
    dialog._use_vector_editor_for_cam = original_editor_source
assert freecad_cut_settings["_xy_origin_offset"] == (0.0, 0.0)
freecad_feed_xy = [
    (float(move["x"]), float(move["y"]))
    for move in freecad_cut_moves
    if str(move.get("type", "")).startswith("feed_")
    and move.get("x") is not None
    and move.get("y") is not None
]
assert min(point[0] for point in freecad_feed_xy) == 140.0
assert max(point[0] for point in freecad_feed_xy) == 240.0

# A mesma invariável vale para Rebaixo/Preenchimento — o caso visual relatado
# em que um Sketch criado na vista 3D era transportado para a origem.
dialog.operation_tabs.setCurrentIndex(dialog._tab_index("Preenchimento"))
freecad_pocket_settings = dialog._collect_settings()
freecad_pocket_settings.update(
    tool_diameter=4.0,
    stepdown=2.0,
    final_depth=2.0,
    cut_depth=2.0,
    safe_height=5.0,
    return_to_start=False,
)
original_active_geometry = dialog._active_geometry
original_editor_source = dialog._use_vector_editor_for_cam
try:
    dialog._active_geometry = lambda: plain_freecad_geometry
    dialog._use_vector_editor_for_cam = False
    freecad_pocket_moves = dialog._build_moves_from_selection(
        freecad_pocket_settings
    )
finally:
    dialog._active_geometry = original_active_geometry
    dialog._use_vector_editor_for_cam = original_editor_source
assert freecad_pocket_settings["_xy_origin_offset"] == (0.0, 0.0)
pocket_feed_xy = [
    (float(move["x"]), float(move["y"]))
    for move in freecad_pocket_moves
    if str(move.get("type", "")).startswith("feed_")
    and move.get("x") is not None
    and move.get("y") is not None
]
assert pocket_feed_xy
assert min(point[0] for point in pocket_feed_xy) >= 140.0
assert max(point[0] for point in pocket_feed_xy) <= 240.0
first.removeObject(plain_freecad_sketch.Name)

# Configurar/aplicar a área não exige nenhuma seleção 3D e deixa somente um
# limite persistente sob a pasta canônica Área de trabalho.
dialog.operation_tabs.setCurrentIndex(dialog._tab_index("Trabalho"))
setup_settings = dialog._collect_work_setup_settings()
original_selected_3d_source = dialog._selected_3d_source
try:
    dialog._selected_3d_source = lambda: (_ for _ in ()).throw(
        ValueError("leitor 3D não deveria ser chamado")
    )
    dialog._show_work_area_preview(setup_settings)
finally:
    dialog._selected_3d_source = original_selected_3d_source
preview_group = first.getObject("WoodCAM2D_Preview")
assert preview_group is not None
assert preview_group.Label == "Prévia da configuração"
dialog._clear_existing_preview(first)
dialog._ensure_persistent_work_area(first, setup_settings)
work_area_group = first.getObject("WoodCAM2D_WorkArea")
assert [child.Label for child in work_area_group.Group] == [
    "Limite da área de trabalho",
    "Datum XY do material",
]

# A árvore de produção tem uma única raiz e somente as três pastas pedidas.
woodcam_root = first.getObject("WoodCAM")
assert woodcam_root is not None
assert [child.Label for child in woodcam_root.Group] == [
    "Peças",
    "Operações",
    "Área de trabalho",
]
parts_group = first.getObject("WoodCAM_Parts")
assert parts_group is not None
assert first.getObject("WoodCAM2D_VectorDrawing") is None
internal_vector_document = first.getObject("WoodCAM2D_VectorDocument")
assert internal_vector_document in list(parts_group.Group)
if hasattr(internal_vector_document.ViewObject, "ShowInTree"):
    assert internal_vector_document.ViewObject.ShowInTree is False

line_id = widget.controller.add_line(
    widget.controller.vec(10.0, 20.0),
    widget.controller.vec(110.0, 20.0),
)
assert line_id
assert len(widget.document.entities_by_id) == 1
assert first.getObject("WoodCAM2D_VectorDocument") is not None
assert widget.properties_panel.apply_button.isEnabled()
assert widget.transform_panel.rotate_button.isEnabled()

# Simula o botão global de Undo/Redo do FreeCAD, fora do canvas.
first.undo()
dialog._sync_vector_editor_from_freecad_history()
app.processEvents()
assert len(widget.document.entities_by_id) == 0
assert not widget.properties_panel.apply_button.isEnabled()
assert not widget.transform_panel.rotate_button.isEnabled()
first.redo()
dialog._sync_vector_editor_from_freecad_history()
app.processEvents()
assert len(widget.document.entities_by_id) == 1

# Todos os painéis recebem o mesmo refresh do controller após reload externo.
widget.controller.selection.select_only(line_id)
layer_id = widget.controller.add_layer("Smoke layer")
assert widget.layer_panel.tree.topLevelItemCount() == 2
first.undo()
dialog._sync_vector_editor_from_freecad_history()
app.processEvents()
assert widget.layer_panel.tree.topLevelItemCount() == 1
first.redo()
dialog._sync_vector_editor_from_freecad_history()
app.processEvents()
assert widget.layer_panel.tree.topLevelItemCount() == 2
assert layer_id in widget.document.layers_by_id

outer_id = widget.controller.add_rectangle(
    widget.controller.vec(20.0, 40.0),
    widget.controller.vec(120.0, 90.0),
)
piece = ui.Piece2D("Smoke piece", outer_id, (), id="piece-ui-smoke")
widget.controller.execute(ui.ReplacePiecesCommand((piece,)))
app.processEvents()
assert widget.pieces_panel.list.count() == 1
assert widget.properties_panel.apply_button.isEnabled()
assert widget.transform_panel.rotate_button.isEnabled()
first.undo()
dialog._sync_vector_editor_from_freecad_history()
app.processEvents()
assert widget.pieces_panel.list.count() == 0
first.redo()
dialog._sync_vector_editor_from_freecad_history()
app.processEvents()
assert widget.pieces_panel.list.count() == 1

# Uma peça importada é selecionada como grupo. O booleano deve expandir essa
# identidade apenas para o cálculo OCC: um furo isolado da borda continua
# pertencendo ao perfil, sem obrigar o operador a desagrupar a chapa.
boolean_outer = widget.controller.add_rectangle(
    widget.controller.vec(160.0, 40.0),
    widget.controller.vec(260.0, 90.0),
)
boolean_hole = widget.controller.add_circle(
    widget.controller.vec(210.0, 65.0), 8.0,
)
boolean_group = ui.GroupEntity(
    layer_id=widget.document.active_layer_id,
    child_ids=(boolean_outer, boolean_hole),
    id="boolean-piece-group",
)
widget.controller.execute(ui.AddEntitiesCommand((boolean_group,)))
widget.controller.selection.select_only(boolean_group.id)
dialog._vector_editor_boolean_selection("difference")
assert widget.workflow_preview_bar.is_active
assert "Subtrair" in widget.workflow_preview_bar.summary_label.text()
assert widget._workflow_revision == widget.document.revision, (
    widget._workflow_revision,
    widget.document.revision,
)
widget._apply_workflow_preview(widget.workflow_preview_bar.payload)
assert not widget.workflow_preview_bar.is_active, widget.workflow_preview_bar.summary_label.text()
assert boolean_group.id not in widget.document.entities_by_id
assert any(
    getattr(entity, "metadata", {}).get("source_kind") == "editor_boolean_compound"
    for entity in widget.document.entities_by_id.values()
)

# Estado direto de UI (ex.: área de trabalho) também não pode se perder na troca.
widget.document.metadata["ui_smoke_memory_marker"] = "document-A"

# A mesma janela visível deve trocar pelo timer/sync, sem show_config_dialog.
second = FreeCAD.newDocument("WoodCAMEditorUiSmokeB")
second.UndoMode = 1
FreeCAD.setActiveDocument(first.Name)
first_count_before_race = len(widget.document.entities_by_id)
# Se a edição de Trabalho começou em A, um editingFinished tardio não pode
# aplicar os valores em B.
dialog.fields["job_width"].setText("777")
assert dialog._vector_editor_area_edit_pending
FreeCAD.setActiveDocument(second.Name)
# Antes do tick de 350 ms, um comando no widget A deve apenas forçar a troca e
# ser cancelado — nunca gravar no documento anterior.
cancelled_race_id = None
try:
    widget.controller.add_circle(widget.controller.vec(5.0, 5.0), 2.0)
except ui.CommandExecutionCancelled:
    pass
app.processEvents()
second_widget = dialog.vector_editor_widget
assert second_widget is not widget
assert dialog._vector_editor_bound_freecad_document is second
assert dialog._vector_editor_store.document is second
assert dialog._vector_editor_session.document is second
assert second_widget.document.document_uuid != widget.document.document_uuid
assert len(second_widget.document.entities_by_id) == 0
assert len(widget.document.entities_by_id) == first_count_before_race
assert cancelled_race_id is None
dialog.fields["job_width"].editingFinished.emit()
app.processEvents()
assert second_widget.document.work_area is None
assert second_widget.controller.add_circle(
    second_widget.controller.vec(50.0, 50.0), 12.5
)
assert len(second_widget.document.entities_by_id) == 1

# Contornos fechados de peças apenas encostados podem ser reconhecidos para o
# organizador separá-los; continuam bloqueados para CAM enquanto ainda tocam.
second_widget.controller.add_rectangle(
    second_widget.controller.vec(0.0, 0.0),
    second_widget.controller.vec(20.0, 20.0),
)
second_widget.controller.add_rectangle(
    second_widget.controller.vec(20.0, 0.0),
    second_widget.controller.vec(40.0, 20.0),
)
touching_report = ui.validate_document(second_widget.document)
assert touching_report.by_code("TOUCHING_CONTOURS")
assert not touching_report.by_code("BRANCH_NODE")
recognized_touching = dialog._vector_editor_create_pieces()
assert len(recognized_touching) == 3

FreeCAD.setActiveDocument(first.Name)
dialog._sync_vector_editor_from_freecad_history()
app.processEvents()
restored_widget = dialog.vector_editor_widget
assert restored_widget is not widget and restored_widget is not second_widget
assert dialog._vector_editor_bound_freecad_document is first
assert dialog._vector_editor_store.document is first
assert dialog._vector_editor_session.document is first
assert restored_widget.document.metadata["ui_smoke_memory_marker"] == "document-A"
assert len(restored_widget.document.entities_by_id) == first_count_before_race
assert restored_widget.document.work_area.width == 321.0
assert restored_widget.document.work_area.height == 654.0
assert restored_widget.document.work_area.min_x == 40.0
assert restored_widget.document.work_area.min_y == 25.0
assert any(
    entity.__class__.__name__ == "PathEntity"
    for entity in restored_widget.document.entities_by_id.values()
)

# Diagnóstico abre lista detalhada e a ocorrência selecionada fica localizada.
report = dialog._vector_editor_diagnose()
app.processEvents()
assert report.by_code("OPEN_PATH")
assert dialog._vector_validation_dialog is not None
assert dialog._vector_validation_dialog.issues
assert restored_widget.overlays.preview_item.isVisible()
dialog._vector_validation_dialog.close()

# Um caminho aberto independente continua no desenho e no diagnóstico, mas
# não pode impedir que contornos fechados válidos virem relações Piece2D.
# O reconhecimento não redesenha nem apaga o caminho aberto.
open_entity_ids_before = {
    issue.entity_ids[0]
    for issue in report.by_code("OPEN_PATH")
    if issue.entity_ids
}
recognized_with_open_path = dialog._vector_editor_create_pieces()
assert recognized_with_open_path
assert open_entity_ids_before <= set(restored_widget.document.entities_by_id)
assert all(
    open_entity_id not in piece.inner_path_ids
    and open_entity_id != piece.outer_path_id
    for open_entity_id in open_entity_ids_before
    for piece in restored_widget.document.pieces_by_id.values()
)
assert "aberto(s) não entraram" in restored_widget.mode_label.text()

# O envio precisa reconstruir as relações Piece2D imediatamente antes de
# materializar. Um desenho pode ganhar novas chapas/furos depois do último
# "Reconhecer peças e furos"; reutilizar relações antigas fazia exatamente
# essas chapas e seus internos desaparecerem no PanelNest.
stale_piece_count = len(restored_widget.document.pieces_by_id)
late_outer_id = restored_widget.controller.add_rectangle(
    restored_widget.controller.vec(270.0, 300.0),
    restored_widget.controller.vec(340.0, 380.0),
)
late_hole_id = restored_widget.controller.add_circle(
    restored_widget.controller.vec(305.0, 340.0), 4.0
)
assert len(restored_widget.document.pieces_by_id) == stale_piece_count
# "Enviar PanelNest" é o intercâmbio do layout inteiro; uma seleção residual
# de edição não pode transformar silenciosamente o comando em "enviar só a
# seleção" e fazer as demais chapas desaparecerem.
restored_widget.controller.selection.replace((late_outer_id, late_hole_id))
expected_fresh_classification = ui.classify_document_pieces(restored_widget.document)
assert any(piece.outer_id == late_outer_id for piece in expected_fresh_classification.pieces)


class _EditorSendSelection:
    @staticmethod
    def clearSelection():
        return None

    @staticmethod
    def addSelection(_object):
        return None


had_selection_api = hasattr(ui.FreeCADGui, "Selection")
original_selection_api = getattr(ui.FreeCADGui, "Selection", None)
try:
    ui.FreeCADGui.Selection = _EditorSendSelection()
    dialog._vector_editor_send_panelnest()
finally:
    if had_selection_api:
        ui.FreeCADGui.Selection = original_selection_api
    else:
        delattr(ui.FreeCADGui, "Selection")
exchange_root = first.getObject("WoodCAM_Parts")
assert exchange_root is not None, restored_widget.mode_label.text()
exchange_manifest = json.loads(exchange_root.WoodCAMExchangeManifestJson)
assert len(exchange_manifest["parts"]) == len(expected_fresh_classification.pieces)
late_payload = next(
    part
    for part in exchange_manifest["parts"]
    if late_hole_id in {
        entity_id
        for piece in restored_widget.document.pieces_by_id.values()
        if piece.id == part["id"]
        for entity_id in piece.inner_path_ids
    }
)
assert len(late_payload["circular_holes"]) == 1
assert all(first.getObject(name) is not None for name in exchange_manifest["object_names"])
assert set(exchange_manifest["object_names"]) <= {
    child.Name for child in exchange_root.Group
}

# A limpeza de sobrelinhas exatas é escopada, preview-first e um único Undo.
duplicate_circle_a = restored_widget.controller.add_circle(
    restored_widget.controller.vec(180.0, 80.0), 8.0
)
duplicate_circle_b = restored_widget.controller.add_circle(
    restored_widget.controller.vec(180.0, 80.0), 8.0
)
restored_widget.controller.selection.replace(
    (duplicate_circle_a, duplicate_circle_b)
)
duplicate_count_before = len(restored_widget.document.entities_by_id)
duplicate_revision_before = restored_widget.document.revision
restored_widget._menu_actions["Reparar"]["Limpar sobrelinhas/duplicados…"].trigger()
app.processEvents()
assert restored_widget.workflow_preview_bar.is_active
assert restored_widget.document.revision == duplicate_revision_before
assert len(restored_widget.document.entities_by_id) == duplicate_count_before
restored_widget.workflow_preview_bar.apply_button.click()
app.processEvents()
assert not restored_widget.workflow_preview_bar.is_active
assert len(restored_widget.document.entities_by_id) == duplicate_count_before - 1
first.undo()
dialog._sync_vector_editor_from_freecad_history()
app.processEvents()
assert len(restored_widget.document.entities_by_id) == duplicate_count_before

# Fechar/Unir é preview-first: nenhuma geometria muda antes de Aplicar.
restored_widget.controller.selection.select_only(line_id)
before_repair = restored_widget.document.get_entity(line_id)
line_layer_id = before_repair.layer_id
restored_widget.controller.update_layer(line_layer_id, locked=True)
dialog._vector_editor_repair_selection()
app.processEvents()
assert not restored_widget.workflow_preview_bar.is_active
assert restored_widget.document.get_entity(line_id) == before_repair
restored_widget.controller.update_layer(line_layer_id, locked=False)
before_repair_revision = restored_widget.document.revision
dialog._vector_editor_repair_selection()
app.processEvents()
assert restored_widget.workflow_preview_bar.is_active
assert restored_widget.document.revision == before_repair_revision
assert restored_widget.document.get_entity(line_id) == before_repair
restored_widget.workflow_preview_bar.apply_button.click()
app.processEvents()
assert not restored_widget.workflow_preview_bar.is_active
assert restored_widget.document.get_entity(line_id).closed

# Organização também mostra a posição final antes de executar o comando.
outer_before = restored_widget.document.get_entity(outer_id)
outer_layer_id = outer_before.layer_id
restored_widget.controller.update_layer(outer_layer_id, locked=True)
dialog._vector_editor_organize_pieces()
app.processEvents()
assert not restored_widget.workflow_preview_bar.is_active
assert restored_widget.document.get_entity(outer_id) == outer_before
restored_widget.controller.update_layer(outer_layer_id, locked=False)
organize_revision = restored_widget.document.revision
dialog._vector_editor_organize_pieces()
app.processEvents()
assert restored_widget.workflow_preview_bar.is_active
assert restored_widget.document.revision == organize_revision
assert restored_widget.document.get_entity(outer_id) == outer_before
restored_widget.workflow_preview_bar.cancel_button.click()
app.processEvents()
assert not restored_widget.workflow_preview_bar.is_active
assert restored_widget.document.get_entity(outer_id) == outer_before
dialog._vector_editor_organize_pieces()
app.processEvents()
assert restored_widget.workflow_preview_bar.is_active
restored_widget.workflow_preview_bar.apply_button.click()
app.processEvents()
assert not restored_widget.workflow_preview_bar.is_active
assert restored_widget.document.get_entity(outer_id) != outer_before

dialog._vector_editor_sync_timer.stop()
assert dialog.cam_advisor_button.text().startswith("Assistente CAM")
advisor_settings = dialog._collect_settings()
advisor_report = dialog._update_cam_advisor_summary(advisor_settings)
assert advisor_report is not None
assert dialog.cam_advisor_button.toolTip()
advisor_messages = []
original_critical = QtWidgets.QMessageBox.critical
original_warning = QtWidgets.QMessageBox.warning
original_information = QtWidgets.QMessageBox.information
try:
    QtWidgets.QMessageBox.critical = staticmethod(
        lambda _parent, title, message: advisor_messages.append((title, message))
    )
    QtWidgets.QMessageBox.warning = staticmethod(
        lambda _parent, title, message: advisor_messages.append((title, message))
    )
    QtWidgets.QMessageBox.information = staticmethod(
        lambda _parent, title, message: advisor_messages.append((title, message))
    )
    # O Editor 2D não pode herdar silenciosamente a última operação CAM.
    dialog.operation_tabs.setCurrentIndex(dialog._tab_index("Editor 2D"))
    dialog.show_cam_advisor()
    assert "aba visível" in advisor_messages[-1][1]
    # Uma aba CAM explícita identifica operação e fresa no cabeçalho.
    dialog.operation_tabs.setCurrentIndex(dialog._tab_index("Acabamento 3D"))
    dialog.show_cam_advisor()
    assert "ANALISANDO AGORA" in advisor_messages[-1][1]
    assert "Acabamento 3D" in advisor_messages[-1][1]
    assert "Ø" in advisor_messages[-1][1]
finally:
    QtWidgets.QMessageBox.critical = original_critical
    QtWidgets.QMessageBox.warning = original_warning
    QtWidgets.QMessageBox.information = original_information
dialog.hide()
app.processEvents()
FreeCAD.closeDocument(second.Name)
FreeCAD.closeDocument(first.Name)
print("WoodCAM Editor 2D UI smoke: OK")
