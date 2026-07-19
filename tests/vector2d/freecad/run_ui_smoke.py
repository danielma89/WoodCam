"""FreeCADCmd smoke for the complete WoodCAM dialog/editor integration.

Run headlessly with::

    env -u QT_QPA_PLATFORMTHEME QT_QPA_PLATFORM=offscreen \
      freecadcmd tests/vector2d/freecad/run_ui_smoke.py
"""

from __future__ import annotations

import os
import sys
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import FreeCAD  # noqa: E402
import Part  # noqa: E402
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

# A simulação 3D usa o overlay integral acima; somente a posição animada da
# fresa é amostrada. Operações 2D conservam o rastro progressivo tradicional.
assert dialog._simulation_uses_exact_static_path(
    {"operation_mode": "finish3d"}
)
assert dialog._simulation_uses_exact_static_path(
    {"operation_mode": "rough3d"}
)
assert not dialog._simulation_uses_exact_static_path(
    {"operation_mode": "cut"}
)
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
assert len(restored_widget.document.entities_by_id) == 2
assert restored_widget.document.work_area.width == 321.0
assert restored_widget.document.work_area.height == 654.0
assert restored_widget.document.work_area.min_x == 40.0
assert restored_widget.document.work_area.min_y == 25.0
assert next(iter(restored_widget.document.entities_by_id.values())).__class__.__name__ == "PathEntity"

# Diagnóstico abre lista detalhada e a ocorrência selecionada fica localizada.
report = dialog._vector_editor_diagnose()
app.processEvents()
assert report.by_code("OPEN_PATH")
assert dialog._vector_validation_dialog is not None
assert dialog._vector_validation_dialog.issues
assert restored_widget.overlays.preview_item.isVisible()
dialog._vector_validation_dialog.close()

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
