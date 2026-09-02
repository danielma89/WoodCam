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
import time
from dataclasses import replace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import FreeCAD  # noqa: E402
import Part  # noqa: E402
import Sketcher  # noqa: E402
from pivy import coin  # noqa: E402
from PySide6 import QtCore, QtGui, QtTest, QtWidgets  # noqa: E402

import ui  # noqa: E402
from woodcam_editor.presentation.i18n import (  # noqa: E402
    set_language,
    translate_widget_tree,
)
from operations import (  # noqa: E402
    audit_profile_cut_moves,
    build_external_cut_job,
    build_external_cut_moves,
)
from woodcam_editor.application.piece_organizer import (  # noqa: E402
    ClassifiedPiece,
    organize_pieces,
)
from woodcam_3d.coin_toolpath import (  # noqa: E402
    CoinToolpathOverlay,
    CoinToolpathViewProvider,
    create_coin_toolpath_feature,
)
from woodcam_3d.storage import decode_moves  # noqa: E402

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
# The smoke assertions use the Portuguese source labels.  Language is a
# persisted user preference, so make this isolated test deterministic without
# changing the preference contract used by the application.
set_language("pt")

# The progressive window has an explicit, fixed-width layout because the
# native QProgressDialog left an unpainted black strip when its dynamic label
# widened inside FreeCAD.  It must also be born directly in the active language.
set_language("en")
progress_probe = ui._OrganizationProgressDialog(
    ui.translate_text("Preparando a primeira solução…"),
    ui.translate_text("Parar e manter a melhor prévia"),
    0,
    200,
)
progress_probe.show()
app.processEvents()
probe_width = progress_probe.width()
progress_probe.setLabelText(
    ui.translate_text("Tentativa %d/%d — %s\n%s • melhor: %s")
    % (
        1,
        4,
        ui.translate_text("Resposta rápida"),
        ui.translate_text("%.1f s restantes") % 15.1,
        ui.translate_text("sem solução completa ainda"),
    )
)
app.processEvents()
assert progress_probe.width() == probe_width == 440
assert "Attempt 1/4" in progress_probe.labelText()
assert "Tentativa" not in progress_probe.labelText()
assert progress_probe.autoFillBackground()
probe_metrics = QtGui.QFontMetrics(progress_probe._label.font())
assert progress_probe._label.minimumHeight() >= probe_metrics.lineSpacing() * 3
assert progress_probe._label.height() >= progress_probe._label.minimumHeight()
progress_probe.finish()
set_language("pt")


def picture_like_pieces():
    def shaped(piece_id, points):
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        return ClassifiedPiece(
            piece_id,
            piece_id + "-outer",
            (),
            (),
            (min(xs), min(ys), max(xs), max(ys)),
            1.0,
            tuple(points),
        )

    return (
        shaped("panel", ((90, 0), (690, 0), (690, 1800), (610, 1800),
                         (610, 1880), (0, 1880), (0, 110), (90, 110))),
        shaped("rail-1", ((0, 0), (62, 42), (58, 1660), (0, 1700))),
        shaped("rail-2", ((0, 40), (60, 0), (60, 1650), (3, 1600))),
        shaped("rail-3", ((0, 0), (58, 45), (58, 1510), (2, 1460))),
        shaped("rail-4", ((0, 35), (55, 0), (55, 1450), (0, 1400))),
        shaped("segment-1", ((0, 0), (55, 35), (55, 410), (2, 365))),
        shaped("segment-2", ((0, 30), (55, 0), (55, 390), (0, 350))),
        shaped("segment-3", ((0, 0), (55, 30), (55, 330), (3, 300))),
        shaped("cap-1", ((0, 0), (135, 0), (90, 55), (25, 48))),
        shaped("cap-2", ((0, 0), (120, 0), (80, 50), (20, 44))),
        shaped("wedge", ((0, 0), (80, 40), (0, 80))),
    )


def wait_until(predicate, timeout_seconds=15.0):
    deadline = time.monotonic() + float(timeout_seconds)
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        QtCore.QThread.msleep(10)
    app.processEvents()
    return bool(predicate())

first = FreeCAD.newDocument("WoodCAMEditorUiSmokeA")
first.UndoMode = 1
dialog = ui.WoodCAM2DDialog()
# User presets survive FreeCAD sessions; keep assertions about the default
# counterbore and dependent common-line workflow independent from the machine
# running this smoke.
dialog.hole_counterbore_enabled.setChecked(False)
dialog.cut_common_line_enabled.setChecked(False)
dialog.cut_tabs_best_fixation.setChecked(True)
dialog.show()
app.processEvents()
widget = dialog.vector_editor_widget
assert widget is not None
# Ocultar a bancada não pode deixar o polling de Undo/Redo competindo com a
# viewport do FreeCAD. Reexibir retoma a mesma instância e o mesmo timer.
sync_timer = dialog._vector_editor_sync_timer
assert sync_timer.isActive()
for _cycle in range(3):
    dialog.hide()
    app.processEvents()
    assert not sync_timer.isActive()
    dialog.show()
    app.processEvents()
    assert sync_timer.isActive()
assert dialog.vector_editor_widget is widget
assert "Importar peças planas pelo PanelNest…" in widget._menu_actions["Arquivo"]
assert not widget.measure_button.icon().isNull()
assert not widget.recognize_pieces_button.icon().isNull()
assert not widget.organize_pieces_button.icon().isNull()
assert dialog.work_area_preset_combo.objectName() == "workAreaPresetCombo"
assert not dialog.work_area_preset_save_button.icon().isNull()
assert not dialog.work_area_preset_delete_button.icon().isNull()

# Confirmar um vetor não pode reconstruir a lista de operações aplicadas. Em
# arquivos reais essa lista contém centenas de milhares de movimentos
# comprimidos; descompactá-los aqui era o bloqueio visível após cada criação.
original_operation_refresh = dialog._refresh_applied_operation_list
dialog._refresh_applied_operation_list = lambda *args, **kwargs: (_ for _ in ()).throw(
    AssertionError("vector commit rebuilt the applied operation list")
)
latency_probe_id = widget.controller.add_line(
    widget.controller.vec(1.0, 1.0),
    widget.controller.vec(2.0, 2.0),
)
dialog._refresh_applied_operation_list = original_operation_refresh
assert latency_probe_id in widget.document.entities_by_id
widget.controller.undo()
app.processEvents()
assert latency_probe_id not in widget.document.entities_by_id
dialog.applied_toolpath_list.clear()

# Export availability belongs to the active page and simulation state. Merely
# editing the G-code filename must not require a tab round-trip to recover it.
state_dialog = ui.WoodCAM2DDialog()
simulation_tab_index = state_dialog._tab_index(ui.ACTION_TAB_TITLE)
assert simulation_tab_index is not None
state_dialog.operation_tabs.setCurrentIndex(simulation_tab_index)
app.processEvents()
assert state_dialog.generate_button.isEnabled()
action_page = state_dialog.operation_tabs.widget(simulation_tab_index)
assert action_page is not None and action_page.isAncestorOf(
    state_dialog.generate_button
)
assert not state_dialog.browse_output_button.icon().isNull()
assert state_dialog.applied_toolpath_list.dragDropMode() == (
    QtWidgets.QAbstractItemView.InternalMove
)
state_dialog.output_path.setText("/tmp/woodcam_filename_state_smoke.nc")
app.processEvents()
assert state_dialog.generate_button.isEnabled()
state_dialog._set_simulation_running(True)
assert not state_dialog.generate_button.isEnabled()
state_dialog._set_simulation_running(False)
assert state_dialog.generate_button.isEnabled()
state_dialog.close()
app.processEvents()
# The language selector is presentation-only and must preserve one stable
# icon+label layout even after both translation timers have fired repeatedly.
widget._menu_actions["Idioma"]["English"].trigger()
app.processEvents()
assert widget.file_menu_button.text() == "File"
assert widget.language_menu_button.text() == "Language"
english_tab_texts = [
    "Job",
    "Material",
    "Tools",
    "Cut",
    "Hole",
    "Pocket",
    "3D Roughing",
    "3D Finishing",
    "2D Editor",
    "Simulation and Save",
]
for _iteration in range(3):
    QtTest.QTest.qWait(850)
    assert [
        dialog.operation_tabs.tabText(index)
        for index in range(dialog.operation_tabs.count())
    ] == english_tab_texts
assert dialog.windowTitle() == "WoodCAM 2D - CNC Woodworking"
assert "independent window" in dialog.window_mode_button.toolTip()
assert dialog.cut_2d_button.text() == "2D Toolpaths"
toolpath_menu_texts = [
    action.text()
    for action in dialog.cut_2d_button.menu().actions()
    if action.text()
]
assert toolpath_menu_texts[:3] == [
    "Show Cut toolpath in 2D",
    "Show Holes toolpath in 2D",
    "Show Pocket toolpath in 2D",
]

# Regression for the two production screens that previously mixed languages.
# Form labels, list items and tree headers are different Qt storage paths and
# all must follow the same selector without changing their model data.
assert dialog.applied_toolpath_list.count() == 0
simulation_empty_item = QtWidgets.QListWidgetItem(
    "Nenhum percurso aplicado ainda", dialog.applied_toolpath_list
)
simulation_empty_item.setData(QtCore.Qt.UserRole, "empty-state")
translate_widget_tree(dialog)
english_labels = {
    label.text()
    for label in dialog.findChildren(QtWidgets.QLabel)
}
assert "Select previously applied toolpaths below to simulate without keeping the original vectors selected." in english_labels
assert "Applied toolpaths" in english_labels
assert dialog.simulation_time_label.text().startswith("Estimated time:")
assert dialog.applied_toolpath_list.count() == 1
assert dialog.applied_toolpath_list.item(0).text() == "No applied toolpath yet"
assert dialog.applied_toolpath_list.item(0).data(QtCore.Qt.UserRole) == "empty-state"
assert dialog.rename_applied_button.text() == "Rename"
assert dialog.edit_applied_button.text() == "Edit"
assert dialog.delete_applied_button.text() == "Delete"
assert dialog.browse_output_button.text() == "Choose…"
assert "displayed order" in dialog.generate_button.toolTip()
assert widget.pieces_panel.title() == "Parts"
assert widget.sheet_panel.title() == "Sheets"
assert widget.sheet_panel.fit_button.text() == "Fit sheet"
assert widget.sheet_panel.list.count() == 0
assert widget.pieces_panel.apply_button.text() == "Apply to part"
assert widget.pieces_panel.rotation_mode.itemText(0) == "0° only"
assert widget.pieces_panel.summary_label.text() == "No classified part."
piece_labels = {
    label.text()
    for label in widget.pieces_panel.findChildren(QtWidgets.QLabel)
}
assert "Grain direction" in piece_labels
assert "Rotation" in piece_labels
assert widget.properties_panel.title() == "Exact position and dimensions"
exact_labels = {
    label.text()
    for label in widget.properties_panel.findChildren(QtWidgets.QLabel)
}
assert {"Minimum X (mm)", "Minimum Y (mm)", "Width (mm)", "Height (mm)"} <= exact_labels
assert [
    widget.layer_panel.tree.headerItem().text(column)
    for column in range(4)
] == ["Active", "Layer", "Visible", "Locked"]
layer_row = widget.layer_panel.tree.topLevelItem(0)
assert layer_row.text(1) == "Drawing"
widget.layer_panel._sync_layer_controls()
assert layer_row.text(1) == "Drawing"
widget._menu_actions["Idioma"]["Português"].trigger()
QtTest.QTest.qWait(1100)
assert widget.file_menu_button.text() == "Arquivo"
assert widget.sheet_panel.title() == "Chapas"
assert "janela independente" in dialog.window_mode_button.toolTip()
assert dialog.applied_toolpath_list.item(0).text() == "Nenhum percurso aplicado ainda"
dialog.applied_toolpath_list.clear()

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
assert visible_tab_texts == tab_titles
# As operações formam uma navegação lateral. O QTabWidget continua sendo a
# fonte da seleção, mas os nomes devem permanecer horizontais (uma linha baixa
# e larga), inclusive depois das trocas repetidas de idioma acima.
assert dialog.operation_tabs.tabPosition() == QtWidgets.QTabWidget.West
operation_tab_bar = dialog.operation_tabs.tabBar()
for index in range(operation_tab_bar.count()):
    size = operation_tab_bar.tabSizeHint(index)
    assert size.width() > size.height(), dialog.operation_tabs.tabText(index)
current_page = dialog.operation_tabs.currentWidget()
assert operation_tab_bar.mapTo(dialog, QtCore.QPoint(0, 0)).x() < (
    current_page.mapTo(dialog, QtCore.QPoint(0, 0)).x()
)
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
for index, title in enumerate(tab_titles):
    assert not dialog.operation_tabs.tabIcon(index).isNull(), title
    assert dialog.operation_tabs.tabToolTip(index) == title
    assert dialog.operation_tabs.tabBar().tabSizeHint(index).width() > (
        dialog.operation_tabs.iconSize().width() + 26
    ), title
assert dialog.rough3d_boundary_combo.count() == 4
assert dialog.rough3d_strategy_combo.count() == 2
assert dialog.finish3d_strategy_combo.count() == 2
assert dialog.rough3d_reverse_check.text()
assert dialog.finish3d_reverse_check.text()
assert dialog.operation_pass_labels["finish3d"].isHidden()
semantic_icons = {
    diagram.semantic_icon_name
    for diagram in dialog.setup_diagrams
    if isinstance(diagram, ui.SemanticSetupIcon)
}
assert {
    "model_3d",
    "finish_model_3d",
    "machining_boundary",
    "rough_strategy",
    "finish_strategy",
    "operation_name",
}.issubset(semantic_icons), semantic_icons
hole_depth_group = dialog.use_model_hole_depths.parentWidget()
hole_depth_grid = hole_depth_group._woodcam_grid
hole_depth_index = hole_depth_grid.indexOf(dialog.use_model_hole_depths)
assert hole_depth_grid.getItemPosition(hole_depth_index)[0] == 3
assert dialog.cut_tabs_best_fixation.isChecked()
assert "quatro regiões" in dialog.cut_tabs_best_fixation.toolTip()
assert "obrigatórias" in dialog.cut_tabs_best_fixation.toolTip()
assert dialog.cut_depth_strategy_combo.findData("piece_bidirectional") >= 0
assert dialog.cut_depth_strategy_combo.findData("global_by_depth") >= 0
assert dialog.cut_depth_strategy_combo.findData("hybrid_stability") >= 0
assert dialog.cut_depth_strategy_combo.findData("hybrid_piece_bidirectional") >= 0
assert dialog.cut_tab_release_combo.findData("keep_tabs") >= 0
assert dialog.cut_tab_release_combo.findData("automatic_release") >= 0
assert dialog.cut_tab_release_combo.isHidden()
assert dialog.cut_tab_release_checkbox.text() == "Remover tabs automaticamente ao final"
assert not dialog.cut_tab_release_checkbox.isChecked()
assert "hole_counterbore_diameter" in dialog.operation_fields["holes"]
assert "hole_counterbore_depth" in dialog.operation_fields["holes"]
assert not dialog.hole_counterbore_enabled.isChecked()
assert "Não é diâmetro" in dialog.operation_fields["holes"]["start_depth"].toolTip()
assert dialog.simulation_speed_slider.objectName() == "simulationSpeedSlider"
assert dialog.fields["simulation_speed_multiplier"].isHidden()
assert "Arraste durante" in dialog.simulation_speed_slider.toolTip()

# A navegação termina depois da última aba; o espaço inferior não recebe uma
# faixa lateral artificial. O controle geral fica acima da primeira aba e o
# destaque do Editor 2D pertence à própria linha do Editor.
tab_bar = dialog.operation_tabs.tabBar()
editor_tab_index = dialog._tab_index("Editor 2D")
assert editor_tab_index is not None
assert dialog.operation_tabs.tabActionWidget() is dialog.detach_editor_button
assert dialog.detach_editor_button.isVisible()
assert dialog.operation_tabs.headerActionWidget() is dialog.window_mode_button
assert dialog.window_mode_button.isVisible()
assert dialog.window_mode_button.isChecked()
assert not dialog.window_mode_button.isEnabled()  # sem host no smoke autônomo
first_tab_top = tab_bar.mapTo(
    dialog.operation_tabs, tab_bar.visualTabRect(0).topLeft()
).y()
assert first_tab_top >= dialog.window_mode_button.geometry().bottom()
dialog.operation_tabs.setCurrentIndex(dialog._tab_index("Corte"))
QtTest.QTest.mouseClick(tab_bar, QtCore.Qt.LeftButton, pos=QtCore.QPoint(4, 4))
assert dialog._tab_title(dialog.operation_tabs.currentIndex()) == "Corte"
last_tab_bottom = tab_bar.mapTo(
    dialog.operation_tabs,
    tab_bar.tabRect(dialog.operation_tabs.count() - 1).bottomLeft(),
).y()
assert last_tab_bottom < dialog.operation_tabs.height()

# O WoodCAM inteiro alterna usando a mesma instância e preserva página e
# Editor. O ciclo cobre o comportamento painel -> janela -> painel usado pelo
# FreeCAD em Wayland/Niri, sem recriar controles ou perder estado.
host = QtWidgets.QWidget()
host.resize(1400, 1000)
host.show()
dialog._woodcam_host_parent = host
dialog._update_woodcam_window_mode_button()
preserved_tab_index = dialog.operation_tabs.currentIndex()
preserved_editor_identity = dialog.vector_editor_widget
dialog._attach_woodcam_to_freecad()
app.processEvents()
assert dialog.parentWidget() is host
assert not dialog.isWindow()
assert not dialog.window_mode_button.isChecked()
# A área vazia abaixo da navegação pertence ao fundo neutro da janela. Ela não
# pode ser transparente e revelar a vista 3D ou o papel de parede no modo
# independente.
dialog_image = dialog.grab().toImage()
empty_sidebar_point = dialog.operation_tabs.mapTo(
    dialog,
    QtCore.QPoint(
        10,
        min(dialog.operation_tabs.height() - 10, last_tab_bottom + 30),
    ),
)
empty_sidebar_color = dialog_image.pixelColor(empty_sidebar_point)
assert empty_sidebar_color.alpha() == 255
assert empty_sidebar_color.name().lower() == "#e6ebf0"
dialog._detach_woodcam_from_freecad()
app.processEvents()
assert dialog.parentWidget() is None
assert dialog.isWindow()
assert dialog.window_mode_button.isChecked()
assert dialog.operation_tabs.currentIndex() == preserved_tab_index
assert dialog.vector_editor_widget is preserved_editor_identity
dialog._attach_woodcam_to_freecad()
app.processEvents()
assert dialog.parentWidget() is host
assert not dialog.isWindow()
assert dialog.operation_tabs.currentIndex() == preserved_tab_index
dialog._detach_woodcam_from_freecad()
app.processEvents()
dialog._woodcam_host_parent = None
dialog._update_woodcam_window_mode_button()
host.close()

# O botão na linha do Editor destaca exatamente o mesmo Editor 2D numa janela
# nativa; fechar devolve o widget e todo o estado à aba.
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

# O mesmo contrato vale para Corte 2D. Antes desta regressão, desmarcar tabs
# deixava a trajetória antiga — ainda com pontes — desenhada na tela.
previous_tabs_enabled = dialog.cut_tabs_enabled.isChecked()
dialog._set_toolpath_truth(
    {"operation_mode": "cut", "tool_name": "Fresa 4 mm", "tool_diameter": 4.0},
    [{"type": "feed_cut"}],
    "PRÉVIA EXATA",
)
dialog.last_preview_settings = {"operation_mode": "cut"}
dialog.last_preview_moves = [{"type": "feed_cut"}]
dialog.cut_tabs_enabled.setChecked(not previous_tabs_enabled)
app.processEvents()
assert not dialog._toolpath_view_valid
assert dialog.last_preview_settings is None
assert dialog.last_preview_moves is None
dialog.cut_tabs_enabled.setChecked(previous_tabs_enabled)

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

# Reproduz a prévia denunciada: uma trajetória ortogonal densa tem uma quina
# em todo movimento. Se Preenchimento cair no limite antigo de 4.000
# segmentos, pares de movimentos são religados por diagonais inexistentes. O
# caminho integral precisa conservar cada reta e cada quina da lista real.
dense_right_angle_moves = []
dense_x = 0.0
dense_y = 0.0
for dense_index in range(5002):
    if dense_index % 2:
        dense_y += 1.0
    else:
        dense_x += 1.0
    dense_right_angle_moves.append(
        {"type": "feed_cut", "x": dense_x, "y": dense_y, "z": -1.0}
    )
pocket_preview_limit = (
    None
    if dialog._uses_lightweight_toolpath_overlay({"operation_mode": "pocket"})
    else 4000
)
pocket_preview_components = dialog._toolpath_components(
    dense_right_angle_moves,
    max_display_segments=pocket_preview_limit,
)
assert len(pocket_preview_components["cut"]) == len(dense_right_angle_moves) - 1
assert all(
    start[0] == end[0] or start[1] == end[1]
    for start, end in pocket_preview_components["cut"]
)


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

# Desbaste, acabamento, Corte e Preenchimento usam o overlay integral tanto na
# prévia quanto na simulação; somente a posição animada da fresa é amostrada.
# Furo conserva o rastro progressivo tradicional. Em particular, o rebaixo não
# pode voltar à projeção reduzida que religava pontos por diagonais falsas.
assert dialog._uses_lightweight_toolpath_overlay(
    {"operation_mode": "pocket"}
)
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
fast_timeline = dialog._exact_3d_simulation_timeline(
    exact_moves,
    dict(exact_settings, simulation_speed_multiplier=100.0),
)
assert list(fast_timeline["times"]) == list(exact_timeline["times"])
interpolated_settings = dict(exact_settings, tool_diameter=4.0)
normal_frames = dialog._interpolate_simulation_frames(
    exact_moves,
    interpolated_settings,
)
fast_frames = dialog._interpolate_simulation_frames(
    exact_moves,
    dict(interpolated_settings, simulation_speed_multiplier=100.0),
)
assert [frame["time_ms"] for frame in fast_frames] == [
    frame["time_ms"] for frame in normal_frames
]
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

# O relógio acumula tempo físico e aplica a velocidade somente durante o play.
# Mudar de 1x para 10x preserva a posição no instante da mudança.
clock_state = {
    "last_wall_time": 10.0,
    "playback_time_ms": 500.0,
    "speed_multiplier": 1.0,
}
assert dialog._simulation_clock_time(clock_state, 11.0) == 1500.0
assert dialog._set_simulation_clock_speed(clock_state, 10.0, 11.0) == 1500.0
assert dialog._simulation_clock_time(clock_state, 11.5) == 6500.0

original_speed_index = dialog.simulation_speed_slider.value()
speed_20_index = ui.SIMULATION_SPEED_STEPS.index(20.0)
dialog.simulation_speed_slider.setValue(speed_20_index)
app.processEvents()
assert dialog.simulation_speed_value_label.text() == "20x"
assert dialog._settings_with_live_simulation_speed(
    {"simulation_speed_multiplier": 1.0}
)["simulation_speed_multiplier"] == 20.0

# Exercita o controle sobre uma simulação realmente ativa no FreeCAD.
runtime_settings = dict(
    exact_settings,
    operation_mode="holes",
    tool_diameter=4.0,
    tool_type="end_mill",
    rpm=18000.0,
)
dialog._show_simulation(exact_moves, runtime_settings)
assert dialog.simulation_state["speed_multiplier"] == 1.0
dialog.simulation_speed_slider.setValue(
    ui.SIMULATION_SPEED_STEPS.index(50.0)
)
app.processEvents()
assert dialog.simulation_state["speed_multiplier"] == 50.0
assert dialog.simulation_state["settings"]["simulation_speed_multiplier"] == 50.0
dialog.stop_simulation()
assert dialog.simulation_state is None
dialog.simulation_speed_slider.setValue(original_speed_index)
app.processEvents()

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
assert applied_3d.EstimatedMachiningSeconds >= 0.0
assert decode_moves(applied_3d.MovesCompressedBase64) == large_moves
assert list(applied_3d.Group) == []

# A lista de Simulação e Salvar é também o gerenciador persistente das
# operações. Sua ordem visual é a ordem do arquivo único, e renomear/excluir
# participam do Undo do FreeCAD sem tocar na geometria-fonte.
applied_holes = dialog._create_applied_operation(
    {
        "operation_mode": "holes",
        "operation_name": "Furos smoke",
        "tool_diameter": 4.0,
    },
    exact_moves,
    selection_snapshot=[],
)
applied_holes_name = applied_holes.Name
dialog.operation_tabs.setCurrentIndex(dialog._tab_index(ui.ACTION_TAB_TITLE))
dialog._refresh_applied_operation_list()
original_ui_decode_moves = ui.decode_moves
try:
    ui.decode_moves = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("selecionar operação não pode descompactar movimentos")
    )
    for row in range(dialog.applied_toolpath_list.count()):
        dialog.applied_toolpath_list.item(row).setSelected(False)
    dialog.applied_toolpath_list.item(0).setSelected(True)
    app.processEvents()
finally:
    ui.decode_moves = original_ui_decode_moves
assert [
    operation.Name for operation in dialog._iter_applied_operation_objects()
] == [applied_3d.Name, applied_holes.Name]
assert dialog.applied_toolpath_list.model().moveRow(
    QtCore.QModelIndex(),
    1,
    QtCore.QModelIndex(),
    0,
)
app.processEvents()
assert [
    operation.Name for operation in dialog._iter_applied_operation_objects()
] == [applied_holes.Name, applied_3d.Name]
assert [
    entry[2] for entry in dialog._all_applied_entries()
] == ["Furos smoke", "Acabamento smoke"]

for row in range(dialog.applied_toolpath_list.count()):
    dialog.applied_toolpath_list.item(row).setSelected(False)
captured_export_labels = []
original_save_entries = dialog._save_applied_gcode_entries
original_information = QtWidgets.QMessageBox.information
try:
    dialog._save_applied_gcode_entries = lambda entries, _path: (
        captured_export_labels.extend(entry[2] for entry in entries)
        or ["/tmp/woodcam_manager_order_smoke.nc"]
    )
    QtWidgets.QMessageBox.information = staticmethod(
        lambda *_args, **_kwargs: None
    )
    dialog.output_path.setText("/tmp/woodcam_manager_order_smoke.nc")
    dialog.generate_gcode()
finally:
    dialog._save_applied_gcode_entries = original_save_entries
    QtWidgets.QMessageBox.information = original_information
assert captured_export_labels == ["Furos smoke", "Acabamento smoke"]

dialog.applied_toolpath_list.item(0).setSelected(True)
original_get_text = QtWidgets.QInputDialog.getText
try:
    QtWidgets.QInputDialog.getText = staticmethod(
        lambda *_args, **_kwargs: ("Furos renomeados", True)
    )
    dialog._rename_selected_applied_operation()
finally:
    QtWidgets.QInputDialog.getText = original_get_text
assert applied_holes.Label == "Furos renomeados"
assert json.loads(str(applied_holes.SettingsJSON))["operation_name"] == (
    "Furos renomeados"
)
dialog._edit_selected_applied_operation()
assert dialog._editing_operation is applied_holes
assert dialog._operation_mode_for_index(dialog.operation_tabs.currentIndex()) == "holes"
assert dialog.cancel_operation_edit_button.isVisible()
settings_before_cancel = str(applied_holes.SettingsJSON)
moves_before_cancel = str(applied_holes.MovesCompressedBase64)
dialog._cancel_operation_editing()
assert dialog._editing_operation is None
assert not dialog.cancel_operation_edit_button.isVisible()
assert dialog.apply_button.text() == "Aplicar"
assert str(applied_holes.SettingsJSON) == settings_before_cancel
assert str(applied_holes.MovesCompressedBase64) == moves_before_cancel
dialog._load_selected_operation_for_editing(applied_holes)
assert dialog._editing_operation is applied_holes

dialog.operation_tabs.setCurrentIndex(dialog._tab_index(ui.ACTION_TAB_TITLE))
dialog._refresh_applied_operation_list()
for row in range(dialog.applied_toolpath_list.count()):
    dialog.applied_toolpath_list.item(row).setSelected(
        dialog.applied_toolpath_list.item(row).data(QtCore.Qt.UserRole)
        == applied_holes.Name
    )
original_question = QtWidgets.QMessageBox.question
try:
    QtWidgets.QMessageBox.question = staticmethod(
        lambda *_args, **_kwargs: QtWidgets.QMessageBox.Yes
    )
    dialog._delete_selected_applied_operations()
finally:
    QtWidgets.QMessageBox.question = original_question
assert first.getObject(applied_holes_name) is None
first.undo()
first.recompute()
dialog._refresh_applied_operation_list()
assert first.getObject(applied_holes_name) is not None

first.removeObject(applied_3d.Name)
first.removeObject(applied_holes_name)
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
    cut_depth_strategy="per_piece",
    tab_release_mode="keep_tabs",
    cut_separate_last_pass=False,
)
original_active_geometry = dialog._active_geometry
original_editor_source = dialog._use_vector_editor_for_cam
original_vector_document = dialog._vector_editor_document
try:
    dialog._active_geometry = lambda _operation_mode=None: editor_cut_geometry
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

# Fenda escalonada que termina com 2 mm para uma fresa de 4 mm: a compensação
# externa normal passaria reto. O pipeline completo precisa bloquear primeiro
# e, após autorização, manter o exterior compensado e acrescentar somente o
# eixo médio local da fenda até os movimentos finais.
narrow_feature_geometry = {
    "contours": [[
        (80.0, 80.0),
        (240.0, 80.0),
        (240.0, 240.0),
        (164.0, 240.0),
        (164.0, 220.0),
        (163.0, 220.0),
        (163.0, 195.0),
        (162.0, 195.0),
        (162.0, 160.0),
        (160.0, 160.0),
        (160.0, 240.0),
        (80.0, 240.0),
        (80.0, 80.0),
    ]],
    "holes": [],
}
narrow_feature_settings = dict(editor_cut_settings)
narrow_feature_settings.update(
    outer_cut_side=ui.CUT_SIDE_OUTSIDE,
    cut_side=ui.CUT_SIDE_OUTSIDE,
    tool_diameter=4.0,
    cut_allowance_offset=0.0,
    common_line_enabled=False,
    allow_narrow_feature_overcut=False,
)
try:
    dialog._active_geometry = lambda _operation_mode=None: narrow_feature_geometry
    dialog._use_vector_editor_for_cam = True
    dialog._vector_editor_document = widget.document
    try:
        dialog._build_moves_from_selection(narrow_feature_settings)
        raise AssertionError("fenda menor que a fresa desapareceu sem confirmação")
    except ValueError as error:
        assert str(error).startswith(
            "__WOODCAM_CONFIRM_NARROW_FEATURE_OVERCUT__"
        ), error
    narrow_feature_settings["allow_narrow_feature_overcut"] = True
    narrow_feature_moves = dialog._build_moves_from_selection(
        narrow_feature_settings
    )
finally:
    dialog._active_geometry = original_active_geometry
    dialog._use_vector_editor_for_cam = original_editor_source
    dialog._vector_editor_document = original_vector_document
assert narrow_feature_settings["_narrow_feature_override_summary"][0][
    "strategy"
] == "supplemental_local_cleanup"
assert narrow_feature_settings["_narrow_feature_cleanup_paths"] == [
    {
        "contour_index": 0,
        "points": [
            [162.0, 240.0],
            [162.0, 220.0],
            [161.5, 207.5],
            [161.5, 195.0],
            [161.0, 177.5],
            [161.0, 160.0],
        ],
        "mode": "slot_medial_axis",
    }
]
narrow_feed_xy = {
    (round(float(move["x"]), 6), round(float(move["y"]), 6))
    for move in narrow_feature_moves
    if move.get("profile_id") == "external-0001"
    and move.get("x") is not None
    and move.get("y") is not None
}
assert (164.0, 220.0) not in narrow_feed_xy
assert (163.0, 195.0) not in narrow_feed_xy
assert (162.0, 160.0) not in narrow_feed_xy
assert (160.0, 160.0) not in narrow_feed_xy
assert min(point[0] for point in narrow_feed_xy) == 78.0
assert max(point[0] for point in narrow_feed_xy) == 242.0
narrow_cleanup_xy = {
    (round(float(move["x"]), 6), round(float(move["y"]), 6))
    for move in narrow_feature_moves
    if move.get("narrow_feature_cleanup")
    and move.get("x") is not None
    and move.get("y") is not None
}
assert (162.0, 240.0) in narrow_cleanup_xy
assert (161.5, 207.5) in narrow_cleanup_xy
assert (161.0, 160.0) in narrow_cleanup_xy
first_narrow_cleanup = next(
    move for move in narrow_feature_moves if move.get("narrow_feature_cleanup")
)
assert first_narrow_cleanup["type"] == "rapid"
assert (first_narrow_cleanup["x"], first_narrow_cleanup["y"]) == (162.0, 240.0)

# O mesmo consentimento precisa sobreviver ao planejador global de Linha comum.
# Mesmo sem uma aresta compartilhada nesta peça isolada, esse caminho percorre
# o contrato completo de segmentos físicos usado quando a opção está ativa.
common_narrow_settings = dict(narrow_feature_settings)
common_narrow_settings.update(
    common_line_enabled=True,
    common_line_mode="preserve_dimensions",
    cut_depth_strategy="per_piece",
)
try:
    dialog._active_geometry = lambda _operation_mode=None: narrow_feature_geometry
    dialog._use_vector_editor_for_cam = True
    dialog._vector_editor_document = widget.document
    common_narrow_moves = dialog._build_moves_from_selection(
        common_narrow_settings
    )
finally:
    dialog._active_geometry = original_active_geometry
    dialog._use_vector_editor_for_cam = original_editor_source
    dialog._vector_editor_document = original_vector_document
common_narrow_feed_xy = {
    (round(float(move["x"]), 6), round(float(move["y"]), 6))
    for move in common_narrow_moves
    if str(move.get("type", "")).startswith("feed_")
    and move.get("x") is not None
    and move.get("y") is not None
}
assert (161.0, 160.0) in common_narrow_feed_xy
assert any(
    move.get("narrow_feature_cleanup") for move in common_narrow_moves
)
assert any(
    move.get("cut_phase") == "external"
    and move.get("x") is not None
    and abs(float(move["x"]) - 78.0) < 1.0e-6
    for move in common_narrow_moves
)
assert feed_xy
assert min(point[0] for point in feed_xy) == 140.0
assert max(point[0] for point in feed_xy) == 240.0
assert min(point[1] for point in feed_xy) == 140.0
assert max(point[1] for point in feed_xy) == 240.0

# A organização pode manter várias chapas no mesmo VectorDocument, mas cada
# chapa física possui seu próprio X0/Y0. Uma operação da Chapa 02 não pode
# partir do zero global da Chapa 01 nem incluir as duas em um mesmo plano.
two_sheet_ring_geometry = {
    "contours": [
        [(0, 0), (100, 0), (100, 30), (0, 30), (0, 0)],
        [(0, 70), (100, 70), (100, 100), (0, 100), (0, 70)],
        [(0, 30), (30, 30), (30, 70), (0, 70), (0, 30)],
        [(70, 30), (100, 30), (100, 70), (70, 70), (70, 30)],
        [(150, 0), (250, 0), (250, 30), (150, 30), (150, 0)],
        [(150, 70), (250, 70), (250, 100), (150, 100), (150, 70)],
        [(150, 30), (180, 30), (180, 70), (150, 70), (150, 30)],
        [(220, 30), (250, 30), (250, 70), (220, 70), (220, 30)],
    ],
    "holes": [],
}
two_sheet_settings = dict(editor_cut_settings)
two_sheet_settings.update(
    common_line_enabled=True,
    cut_depth_strategy="hybrid_piece_bidirectional",
    cut_tabs_enabled=True,
    tab_count=4,
    tab_length=8.0,
    tab_thickness=15.0,
    tab_best_fixation=True,
    loose_waste_fixation="tabs",
    material_thickness=15.0,
    final_depth=15.5,
    cut_depth=15.5,
    stepdown=5.0,
    pass_depths=[5.0, 10.0, 15.5],
    tool_diameter=4.0,
    effective_tool_diameter=4.0,
    common_line_tolerance=0.2,
)
two_sheet_settings.pop("_work_area_bounds", None)
two_sheet_settings.pop("_organization_sheet_bounds", None)
widget.controller.execute(
    ui.SetDocumentMetadataCommand(
        "organization_sheet_bounds",
        [[0.0, 0.0, 100.0, 100.0], [150.0, 0.0, 250.0, 100.0]],
    )
)
widget.sheet_panel.list.setCurrentRow(1)
app.processEvents()
assert widget.sheet_panel.current_index() == 1
original_active_geometry = dialog._active_geometry
original_editor_source = dialog._use_vector_editor_for_cam
original_vector_document = dialog._vector_editor_document
try:
    dialog._active_geometry = lambda _operation_mode=None: {
        "contours": two_sheet_ring_geometry["contours"][4:],
        "holes": [],
    }
    dialog._use_vector_editor_for_cam = True
    dialog._vector_editor_document = widget.document
    two_sheet_moves = dialog._build_moves_from_selection(two_sheet_settings)
finally:
    dialog._active_geometry = original_active_geometry
    dialog._use_vector_editor_for_cam = original_editor_source
    dialog._vector_editor_document = original_vector_document
    widget.controller.undo()
assert two_sheet_settings["_organization_sheet_bounds"] == [
    [150.0, 0.0, 250.0, 100.0],
]
assert two_sheet_settings["_work_area_bounds"] == [0.0, 0.0, 100.0, 100.0]
assert two_sheet_settings["_active_sheet_bounds_global"] == [
    150.0, 0.0, 250.0, 100.0,
]
assert two_sheet_settings["_xy_origin_offset"] == (-150.0, 0.0)
two_sheet_xy = [
    (float(move["x"]), float(move["y"]))
    for move in two_sheet_moves
    if move.get("x") is not None and move.get("y") is not None
]
assert two_sheet_xy
assert min(point[0] for point in two_sheet_xy) >= -1.0e-9, two_sheet_xy
assert max(point[0] for point in two_sheet_xy) <= 100.0 + 1.0e-9, two_sheet_xy
assert min(point[1] for point in two_sheet_xy) >= -1.0e-9, two_sheet_xy
assert max(point[1] for point in two_sheet_xy) <= 100.0 + 1.0e-9, two_sheet_xy
two_sheet_summary = two_sheet_settings["_global_cut_plan_summary"]
assert two_sheet_summary["waste_region_count"] == 1
two_sheet_waste_tabs = [
    tab for tab in two_sheet_summary["tabs"] if tab.get("waste_id")
]
assert len(two_sheet_waste_tabs) == 2

# Estratégias globais/SharedEdge são exclusivas da opção Linha comum. Uma
# preferência experimental antiga não pode alterar nem um movimento do CAM
# padrão quando a opção está desligada (inclusive não pode ativar TabRelease).
legacy_control_settings = dict(editor_cut_settings)
legacy_control_settings.update(
    common_line_enabled=False,
    cut_depth_strategy="per_piece",
    cut_tabs_enabled=True,
    tab_count=3,
    tab_length=12.0,
    tab_thickness=3.0,
    tab_release_mode="keep_tabs",
)
stale_common_line_settings = dict(legacy_control_settings)
stale_common_line_settings.update(
    cut_depth_strategy="hybrid_piece_bidirectional",
    tab_release_mode="automatic_release",
    _global_cut_plan_summary={"stale": True},
)
original_active_geometry = dialog._active_geometry
original_editor_source = dialog._use_vector_editor_for_cam
original_vector_document = dialog._vector_editor_document
try:
    dialog._active_geometry = lambda _operation_mode=None: editor_cut_geometry
    dialog._use_vector_editor_for_cam = True
    dialog._vector_editor_document = widget.document
    legacy_control_moves = dialog._build_moves_from_selection(
        legacy_control_settings
    )
    stale_common_line_moves = dialog._build_moves_from_selection(
        stale_common_line_settings
    )
finally:
    dialog._active_geometry = original_active_geometry
    dialog._use_vector_editor_for_cam = original_editor_source
    dialog._vector_editor_document = original_vector_document
assert stale_common_line_moves == legacy_control_moves
assert "_global_cut_plan_summary" not in stale_common_line_settings
assert not any(move.get("global_cut") for move in stale_common_line_moves)
assert not any(move.get("tab_release") for move in stale_common_line_moves)

# Se somente a liberação automática entrar em impasse, o corte principal e as
# tabs manuais continuam válidos. A UI deve preservar as tabs e aplicar a
# operação, nunca rejeitar todo o trabalho.
release_fallback_settings = dict(legacy_control_settings)
release_fallback_settings.update(
    common_line_enabled=True,
    cut_depth_strategy="piece_bidirectional",
    tab_release_mode="automatic_release",
)
original_global_plan_builder = ui.build_global_cut_plan
release_attempt_modes = []

def forced_release_deadlock(*args, **kwargs):
    release_attempt_modes.append(kwargs.get("release_mode"))
    if kwargs.get("release_mode") == "automatic_release":
        raise ui.GlobalCutPlanError(
            "não existe ordem de TabRelease que preserve a retenção das peças restantes"
        )
    return original_global_plan_builder(*args, **kwargs)

original_active_geometry = dialog._active_geometry
original_editor_source = dialog._use_vector_editor_for_cam
original_vector_document = dialog._vector_editor_document
try:
    ui.build_global_cut_plan = forced_release_deadlock
    dialog._active_geometry = lambda _operation_mode=None: editor_cut_geometry
    dialog._use_vector_editor_for_cam = True
    dialog._vector_editor_document = widget.document
    release_fallback_moves = dialog._build_moves_from_selection(
        release_fallback_settings
    )
finally:
    ui.build_global_cut_plan = original_global_plan_builder
    dialog._active_geometry = original_active_geometry
    dialog._use_vector_editor_for_cam = original_editor_source
    dialog._vector_editor_document = original_vector_document
assert release_fallback_moves
assert release_attempt_modes[:2] == ["automatic_release", "keep_tabs"]
assert release_fallback_settings["tab_release_mode"] == "keep_tabs"
assert release_fallback_settings["_global_cut_plan_summary"][
    "tab_release_count"
] == 0
assert "tab_release_fallback" in release_fallback_settings[
    "_global_cut_plan_summary"
]

# O contrato também fica explícito na UI e nos settings coletados: desligar
# Linha comum desativa o plano global sem apagar a estratégia escolhida. Assim
# "todas direto" não volta silenciosamente para "última no final".
previous_common_enabled = dialog.cut_common_line_enabled.isChecked()
previous_depth_index = dialog.cut_depth_strategy_combo.currentIndex()
previous_tabs_enabled = dialog.cut_tabs_enabled.isChecked()
previous_release_index = dialog.cut_tab_release_combo.currentIndex()
previous_operation_tab = dialog.operation_tabs.currentIndex()
try:
    dialog.operation_tabs.setCurrentIndex(dialog._tab_index("Corte"))
    dialog.cut_common_line_enabled.setChecked(True)
    dialog.cut_depth_strategy_combo.setCurrentIndex(
        dialog.cut_depth_strategy_combo.findData("piece_bidirectional")
    )
    dialog.cut_tabs_enabled.setChecked(True)
    dialog.cut_tab_release_checkbox.setChecked(True)
    assert dialog.cut_tab_release_combo.currentData() == "automatic_release"
    dialog.cut_common_line_enabled.setChecked(False)
    app.processEvents()
    assert not dialog.cut_depth_strategy_combo.isEnabled()
    assert not dialog.cut_tab_release_combo.isEnabled()
    assert dialog.cut_tab_release_checkbox.isEnabled()
    assert dialog.cut_loose_waste_fixation_combo.isEnabled()
    assert not dialog.cut_tab_release_checkbox.isChecked()
    assert dialog.cut_depth_strategy_combo.currentData() == "piece_bidirectional"
    assert dialog.cut_tab_release_combo.currentData() == "keep_tabs"
    standard_cam_settings = dialog._collect_settings()
    assert standard_cam_settings["cut_depth_strategy"] == "per_piece"
    assert standard_cam_settings["tab_release_mode"] == "keep_tabs"
    dialog.cut_common_line_enabled.setChecked(True)
    assert dialog.cut_depth_strategy_combo.currentData() == "piece_bidirectional"
finally:
    dialog.cut_common_line_enabled.setChecked(previous_common_enabled)
    dialog.cut_tabs_enabled.setChecked(previous_tabs_enabled)
    dialog.cut_depth_strategy_combo.setCurrentIndex(previous_depth_index)
    if previous_common_enabled:
        dialog.cut_tab_release_combo.setCurrentIndex(previous_release_index)
    dialog.operation_tabs.setCurrentIndex(previous_operation_tab)

# Desmarcar tabs é um estado terminal no contrato coletado: quantidade e
# posições antigas não podem continuar escondidas no CAM. Restos desativados
# também não criam WasteTabs por efeito colateral.
previous_operation_tab = dialog.operation_tabs.currentIndex()
previous_common_enabled = dialog.cut_common_line_enabled.isChecked()
previous_tabs_enabled = dialog.cut_tabs_enabled.isChecked()
previous_tabs_manual = dialog.cut_tabs_manual_enabled.isChecked()
previous_tabs_auto = dialog.cut_tabs_auto_enabled.isChecked()
previous_tab_positions = list(dialog.cut_tab_positions)
previous_waste_index = dialog.cut_loose_waste_fixation_combo.currentIndex()
try:
    dialog.operation_tabs.setCurrentIndex(dialog._tab_index("Corte"))
    dialog.cut_common_line_enabled.setChecked(True)
    dialog.cut_tabs_enabled.setChecked(True)
    dialog.cut_tabs_manual_enabled.setChecked(True)
    dialog.cut_tab_positions = [{"x": 10.0, "y": 0.0}]
    dialog.cut_loose_waste_fixation_combo.setCurrentIndex(
        dialog.cut_loose_waste_fixation_combo.findData("disabled")
    )
    dialog.cut_tabs_enabled.setChecked(False)
    disabled_tab_settings = dialog._collect_settings()
    assert not disabled_tab_settings["cut_tabs_enabled"]
    assert disabled_tab_settings["tab_count"] == 0
    assert disabled_tab_settings["tab_positions"] == []
    assert disabled_tab_settings["loose_waste_fixation"] == "disabled"
finally:
    dialog.cut_common_line_enabled.setChecked(previous_common_enabled)
    dialog.cut_tabs_enabled.setChecked(previous_tabs_enabled)
    dialog.cut_tabs_manual_enabled.setChecked(previous_tabs_manual)
    dialog.cut_tabs_auto_enabled.setChecked(previous_tabs_auto)
    dialog.cut_tab_positions = previous_tab_positions
    dialog.cut_loose_waste_fixation_combo.setCurrentIndex(previous_waste_index)
    dialog.operation_tabs.setCurrentIndex(previous_operation_tab)

# Regressão do perfil com tabs no preview real. A desaceleração do último
# canto não pode continuar sobre a primeira aresta depois de fechar a volta.
tabbed_preview_moves = build_external_cut_moves(
    [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)],
    final_depth=15.0,
    stepdown=5.0,
    ramp_length=0.0,
    safe_height=8.0,
    compensate_external=False,
    smart_entry=False,
    tabs_enabled=True,
    tab_length=12.0,
    tab_thickness=3.0,
    tab_count=3,
    corner_slowdown_enabled=True,
)
tabbed_reports = audit_profile_cut_moves(tabbed_preview_moves)
assert len(tabbed_reports) == 3
assert all(report["loop_count"] == 1 for report in tabbed_reports)
assert all(report["closed_loop_count"] == 1 for report in tabbed_reports)
assert all(abs(report["coverage_ratio"] - 1.0) <= 1.0e-7 for report in tabbed_reports)
tabbed_components = dialog._toolpath_components(
    tabbed_preview_moves,
    max_display_segments=None,
)
source_segment_count = 0
source_position = (None, None, None)
source_previous = None
for move in tabbed_preview_moves:
    frame, source_position = dialog._move_to_frame(move, source_position)
    if frame is None:
        continue
    point = (round(frame["x"], 4), round(frame["y"], 4), round(frame["z"], 4))
    if source_previous is not None and source_previous != point:
        source_segment_count += 1
    source_previous = point
rendered_segment_count = sum(
    len(tabbed_components[category])
    for category in ("rapid", "ramp", "cut", "corner")
)
assert rendered_segment_count == source_segment_count

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
    dialog._active_geometry = lambda _operation_mode=None: panelnest_geometry
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
    dialog._active_geometry = lambda _operation_mode=None: plain_freecad_geometry
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
    dialog._active_geometry = lambda _operation_mode=None: plain_freecad_geometry
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

# Os cabeçalhos laterais recolhem somente apresentação: nenhum clique na seta
# pode criar uma transação ou mudar a seleção persistida.
panel_revision = widget.document.revision
panel_selection = widget.controller.selection.ids
assert widget.layer_panel._collapse_button.text() == "▼"
assert widget.layer_panel._collapse_button.width() >= 27
widget.layer_panel._collapse_button.click()
app.processEvents()
assert widget.layer_panel.collapsed
assert widget.layer_panel._collapse_button.text() == "▶"
widget.layer_panel._collapse_button.click()
app.processEvents()
assert not widget.layer_panel.collapsed
assert widget.layer_panel._collapse_button.text() == "▼"
assert widget.document.revision == panel_revision
assert widget.controller.selection.ids == panel_selection

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

# Um clique em qualquer fragmento de um contorno importado deve descobrir a
# cadeia conectada, preservar um detalhe real menor que a tolerância de união
# e eliminar somente a folga numérica final. Tudo continua preview-first e vira
# uma única transação no Undo do FreeCAD.
fragment_layer_id = restored_widget.document.active_layer_id
fragment_specs = (
    (
        "ui-smoke-fragment-first",
        ((1400.0, 1400.0), (1410.0, 1400.0)),
    ),
    (
        "ui-smoke-fragment-second",
        ((1410.0, 1400.0), (1410.0, 1410.0)),
    ),
    (
        "ui-smoke-fragment-third",
        ((1410.0, 1410.0), (1400.198, 1410.0), (1400.0, 1410.0)),
    ),
    (
        "ui-smoke-fragment-fourth",
        ((1400.0, 1410.0), (1400.0, 1400.0000003)),
    ),
)
fragment_entities = tuple(
    ui.PathEntity.from_points(
        fragment_layer_id,
        tuple(
            restored_widget.controller.vec(x_value, y_value)
            for x_value, y_value in points
        ),
        id=entity_id,
    )
    for entity_id, points in fragment_specs
)
fragment_ids = tuple(entity.id for entity in fragment_entities)
restored_widget.controller.execute(ui.AddEntitiesCommand(fragment_entities))
previous_join_tolerance = restored_widget.join_tolerance.value()
restored_widget.join_tolerance.setValue(0.2)
restored_widget.controller.selection.select_only(fragment_ids[0])
fragment_revision = restored_widget.document.revision
dialog._vector_editor_repair_selection()
app.processEvents()
assert restored_widget.workflow_preview_bar.is_active, "fragment-preview-not-opened"
assert restored_widget.document.revision == fragment_revision, "fragment-preview-mutated"
assert all(
    restored_widget.document.get_entity(entity_id) is not None
    for entity_id in fragment_ids
), "fragment-preview-removed-source"
restored_widget.workflow_preview_bar.apply_button.click()
app.processEvents()
fragment_result = restored_widget.document.get_entity(fragment_ids[0])
assert fragment_result.closed, "fragment-result-open"
assert all(
    entity_id not in restored_widget.document.entities_by_id
    for entity_id in fragment_ids[1:]
), "fragment-join-kept-sources"
assert abs(min(span.length() for span in fragment_result.spans) - 0.198) < 1.0e-9, (
    "fragment-short-detail-lost",
    min(span.length() for span in fragment_result.spans),
)
fragment_issue_codes = {
    issue.code
    for issue in ui.validate_document(
    restored_widget.document,
    join_tolerance=0.2,
    entity_ids=(fragment_result.id,),
    ).issues
}
assert not fragment_issue_codes.intersection(
    {"BRANCH_NODE", "OPEN_PATH", "ZERO_LENGTH_SPAN"}
), ("fragment-result-invalid", fragment_issue_codes)
restored_widget.controller.undo()
app.processEvents()
assert all(
    restored_widget.document.get_entity(entity_id) is not None
    for entity_id in fragment_ids
), "fragment-undo-did-not-restore"
restored_widget.controller.execute(ui.DeleteEntitiesCommand(fragment_ids))
restored_widget.join_tolerance.setValue(previous_join_tolerance)

# Organização também mostra a posição final antes de executar o comando.
outer_before = restored_widget.document.get_entity(outer_id)
unselected_before = restored_widget.document.get_entity(late_outer_id)
restored_widget.controller.selection.select_only(outer_id)
outer_layer_id = outer_before.layer_id
restored_widget.controller.update_layer(outer_layer_id, locked=True)
dialog._vector_editor_organize_pieces(time_budget_seconds=2)
app.processEvents()
assert not restored_widget.workflow_preview_bar.is_active
assert restored_widget.document.get_entity(outer_id) == outer_before
restored_widget.controller.update_layer(outer_layer_id, locked=False)
organize_revision = restored_widget.document.revision
dialog._vector_editor_organize_pieces(time_budget_seconds=2)
assert wait_until(lambda: restored_widget.workflow_preview_bar.is_active)
assert restored_widget.workflow_preview_bar.is_active
assert restored_widget.document.revision == organize_revision
assert restored_widget.document.get_entity(outer_id) == outer_before
progress = dialog.findChild(
    ui._OrganizationProgressDialog,
    "vectorEditorNestingProgress",
)
assert progress is not None
assert progress.maximum() == 20
assert "restantes" in progress.labelText() or "atingido" in progress.labelText()
dialog._cancel_vector_editor_nesting_search(keep_preview=True)
assert wait_until(
    lambda: getattr(dialog, "_vector_editor_nesting_session", None) is None
)
restored_widget.workflow_preview_bar.cancel_button.click()
app.processEvents()
assert not restored_widget.workflow_preview_bar.is_active
assert restored_widget.document.get_entity(outer_id) == outer_before
dialog._vector_editor_organize_pieces(time_budget_seconds=2)
assert wait_until(lambda: restored_widget.workflow_preview_bar.is_active)
assert restored_widget.workflow_preview_bar.is_active
restored_widget.workflow_preview_bar.apply_button.click()
assert wait_until(
    lambda: getattr(dialog, "_vector_editor_nesting_session", None) is None
)
assert not restored_widget.workflow_preview_bar.is_active
assert restored_widget.document.get_entity(outer_id) != outer_before
assert restored_widget.document.get_entity(late_outer_id) == unselected_before
assert set(restored_widget.controller.selection.ids) >= {outer_id}
assert "organization_remnant_cuts" in restored_widget.document.metadata
organized_remnants = restored_widget.document.metadata["organization_remnant_cuts"]
assert organized_remnants
for remnant_record in organized_remnants:
    remnant_entity = restored_widget.document.get_entity(
        remnant_record["entity_id"]
    )
    assert ui.entity_is_remnant_cut(remnant_entity)
    assert not remnant_entity.closed
    assert remnant_entity.id in restored_widget.adapter.items_by_id
organized_outer_after = restored_widget.document.get_entity(outer_id)
first.undo()
dialog._sync_vector_editor_from_freecad_history()
app.processEvents()
assert restored_widget.document.get_entity(outer_id) == outer_before
assert restored_widget.document.get_entity(late_outer_id) == unselected_before
first.redo()
dialog._sync_vector_editor_from_freecad_history()
app.processEvents()
assert restored_widget.document.get_entity(outer_id) == organized_outer_after
assert restored_widget.document.get_entity(late_outer_id) == unselected_before

# Linha comum não pode transformar silenciosamente um corte externo em corte
# sobre a linha. Somente uma fronteira realmente coincidente chega à pergunta
# de confirmação; uma folga estreita comum continua bloqueada.
common_line_settings = dict(editor_cut_settings)
common_line_settings.update(
    common_line_enabled=True,
    common_line_mode="on_vector",
    common_line_tolerance=0.02,
    outer_cut_side=ui.CUT_SIDE_OUTSIDE,
    cut_side=ui.CUT_SIDE_OUTSIDE,
    tool_diameter=6.0,
    cut_allowance_offset=0.0,
)
common_first = [
    (0.0, 0.0),
    (100.0, 0.0),
    (100.0, 50.0),
    (0.0, 50.0),
]
common_touching = [
    (0.0, 50.0),
    (100.0, 50.0),
    (100.0, 100.0),
    (0.0, 100.0),
]
preserve_common_settings = dict(common_line_settings)
preserve_common_settings["common_line_mode"] = "preserve_dimensions"
try:
    dialog._build_common_line_outer_moves(
        [common_first, common_touching],
        preserve_common_settings,
        6.0,
        (0.0, 0.0),
    )
    raise AssertionError("folga incompatível com a compensação não foi bloqueada")
except ValueError as error:
    message = str(error)
    assert "contornos originais são válidos" in message
    assert "diâmetro efetivo de 6.00 mm" in message

# O mesmo contrato é aplicado antes do nesting. Assim, organizar e cortar em
# seguida não produz um layout aceito no desenho e recusado após a compensação.
original_common_enabled = dialog.cut_common_line_enabled.isChecked()
original_common_mode = dialog.cut_common_line_mode_combo.currentIndex()
original_cut_side = dialog.operation_combo.currentIndex()
original_tool_diameter = dialog.operation_fields["cut"]["tool_diameter"].text()
original_allowance = dialog.operation_fields["cut"]["cut_allowance_offset"].text()
try:
    dialog.cut_common_line_enabled.setChecked(True)
    dialog.operation_combo.setCurrentIndex(0)
    dialog.operation_fields["cut"]["tool_diameter"].setText("6")
    dialog.operation_fields["cut"]["cut_allowance_offset"].setText("0")
    preserve_index = dialog.cut_common_line_mode_combo.findData("preserve_dimensions")
    dialog.cut_common_line_mode_combo.setCurrentIndex(preserve_index)
    contract = dialog._vector_editor_nesting_cut_contract()
    assert contract["recommended_spacing"] == 6.0
    adjusted_spacing, adjustment = dialog._vector_editor_safe_nesting_spacing(0.0)
    assert adjusted_spacing == 6.0
    assert "ajustada" in adjustment

    on_vector_index = dialog.cut_common_line_mode_combo.findData("on_vector")
    dialog.cut_common_line_mode_combo.setCurrentIndex(on_vector_index)
    contract = dialog._vector_editor_nesting_cut_contract()
    assert contract["recommended_spacing"] == 0.0
    assert dialog._vector_editor_safe_nesting_spacing(0.0)[0] == 0.0
    assert dialog._vector_editor_safe_nesting_spacing(3.0)[0] == 6.0
finally:
    dialog.cut_common_line_enabled.setChecked(original_common_enabled)
    dialog.cut_common_line_mode_combo.setCurrentIndex(original_common_mode)
    dialog.operation_combo.setCurrentIndex(original_cut_side)
    dialog.operation_fields["cut"]["tool_diameter"].setText(original_tool_diameter)
    dialog.operation_fields["cut"]["cut_allowance_offset"].setText(original_allowance)

# Fluxo integrado: o resultado realmente emitido pelo nesting com folga de
# Ø6 mm precisa atravessar a compensação externa e gerar linha comum válida.
flow_pieces = (
    ClassifiedPiece(
        "flow-a", "flow-a-o", (), (), (0.0, 0.0, 80.0, 40.0), 3200.0,
        ((0.0, 0.0), (80.0, 0.0), (80.0, 40.0), (0.0, 40.0)),
    ),
    ClassifiedPiece(
        "flow-b", "flow-b-o", (), (), (0.0, 0.0, 80.0, 40.0), 3200.0,
        ((0.0, 0.0), (80.0, 0.0), (80.0, 40.0), (0.0, 40.0)),
    ),
)
flow_result = organize_pieces(
    flow_pieces,
    (0.0, 0.0, 178.0, 52.0),
    spacing=6.0,
    rotations={"flow-a": (0.0,), "flow-b": (0.0,)},
    search_mode="fast",
    toolpath_offset=3.0,
)
assert len(flow_result.placements) == 2
flow_by_id = {piece.piece_id: piece for piece in flow_pieces}
flow_contours = [
    [
        (x_value + placement.dx, y_value + placement.dy)
        for x_value, y_value in flow_by_id[placement.piece_id].outer_points
    ]
    for placement in flow_result.placements
]
flow_settings = dict(preserve_common_settings)
flow_settings.update(
    cut_tabs_enabled=True,
    tab_count=3,
    tab_length=8.0,
    tab_thickness=3.0,
    tab_best_fixation=True,
    return_to_start=False,
)
flow_moves = dialog._build_common_line_outer_moves(
    flow_contours,
    flow_settings,
    6.0,
    (0.0, 0.0),
)
assert flow_moves
assert any(move.get("tab") for move in flow_moves)

# Reproduz a topologia da captura relatada dentro do diálogo real: painel com
# entalhe, ripas trapezoidais altas e peças inclinadas curtas. O mesmo resultado
# do auto-organizador precisa entrar imediatamente no gerador de percurso.
reported_pieces = picture_like_pieces()
reported_result = organize_pieces(
    reported_pieces,
    (0.0, 0.0, 1000.0, 2050.0),
    spacing=4.0,
    rotations={piece.piece_id: (0.0,) for piece in reported_pieces},
    search_mode="fast",
    search_budget=(4, 2, 1, 1),
    toolpath_offset=2.0,
)
assert len(reported_result.placements) == 11
reported_by_id = {piece.piece_id: piece for piece in reported_pieces}
reported_contours = [
    [
        (x_value + placement.dx, y_value + placement.dy)
        for x_value, y_value in reported_by_id[placement.piece_id].outer_points
    ]
    for placement in reported_result.placements
]
reported_settings = dict(preserve_common_settings)
reported_settings.update(
    tool_diameter=4.0,
    cut_tabs_enabled=False,
    return_to_start=False,
)
reported_moves = dialog._build_common_line_outer_moves(
    reported_contours,
    reported_settings,
    4.0,
    (0.0, 0.0),
)
assert reported_moves
reported_global_settings = dict(reported_settings)
reported_global_settings.update(
    cut_depth_strategy="hybrid_stability",
    tab_release_mode="keep_tabs",
    final_depth=15.5,
    stepdown=5.0,
    material_thickness=15.0,
    start_depth=0.0,
    pass_depths=None,
    tool_type="end_mill",
)
reported_global_moves = dialog._build_global_cut_stage_moves(
    reported_contours,
    [],
    reported_global_settings,
    4.0,
    (0.0, 0.0),
)
reported_piece_summary = dict(reported_global_settings["_global_cut_plan_summary"])
assert reported_global_moves
assert reported_piece_summary["hybrid_intermediate_mode"] == "per_piece_common_line"
assert reported_global_settings["_global_cut_plan_summary"]["shared_segment_count"] >= 5
assert all(
    move.get("cut_phase") != "tab_release"
    for move in reported_global_moves
)
reported_fast_settings = dict(reported_global_settings)
reported_fast_settings["_hybrid_intermediate_mode"] = "fast"
reported_fast_moves = dialog._build_global_cut_stage_moves(
    reported_contours,
    [],
    reported_fast_settings,
    4.0,
    (0.0, 0.0),
)
reported_fast_summary = reported_fast_settings["_global_cut_plan_summary"]
assert reported_fast_summary["hybrid_intermediate_mode"] == "fast"
piece_intermediate_metrics = [
    metric
    for metric in reported_piece_summary["route_metrics"]
    if metric["depth"] < reported_global_settings["material_thickness"]
]
fast_intermediate_metrics = [
    metric
    for metric in reported_fast_summary["route_metrics"]
    if metric["depth"] < reported_global_settings["material_thickness"]
]
assert sum(metric["trail_count"] for metric in piece_intermediate_metrics) < sum(
    metric["trail_count"] for metric in fast_intermediate_metrics
)
assert sum(metric["piece_switch_count"] for metric in piece_intermediate_metrics) < sum(
    metric["piece_switch_count"] for metric in fast_intermediate_metrics
)
assert sum(metric["fragmented_piece_count"] for metric in piece_intermediate_metrics) < sum(
    metric["fragmented_piece_count"] for metric in fast_intermediate_metrics
)
assert all(
    metric["duplicate_physical_segment_count"] == 0
    for metric in piece_intermediate_metrics
)
assert all(
    abs(
        metric["sum_of_independent_piece_perimeters"]
        - metric["cut_distance"]
        - metric["shared_cut_savings"]
    ) <= 1.0e-6
    for metric in piece_intermediate_metrics
)
reported_baseline_settings = dict(reported_global_settings)
reported_baseline_settings["cut_depth_strategy"] = "global_by_depth"
reported_baseline_moves = dialog._build_global_cut_stage_moves(
    reported_contours,
    [],
    reported_baseline_settings,
    4.0,
    (0.0, 0.0),
)

def intermediate_rapid_distance(moves, through_depth):
    current = (0.0, 0.0)
    total = 0.0
    for move in moves:
        if move.get("x") is None or move.get("y") is None:
            continue
        target = (float(move["x"]), float(move["y"]))
        depth = move.get("depth_pass")
        if (
            move.get("type") == "rapid"
            and depth is not None
            and float(depth) < float(through_depth) - 1.0e-9
        ):
            total += math.hypot(target[0] - current[0], target[1] - current[1])
        current = target
    return total

reported_hybrid_rapid = intermediate_rapid_distance(
    reported_global_moves,
    reported_global_settings["material_thickness"],
)
reported_baseline_rapid = intermediate_rapid_distance(
    reported_baseline_moves,
    reported_global_settings["material_thickness"],
)
assert reported_hybrid_rapid < reported_baseline_rapid

# Bicos agudos como os das ripas/trapézios do caso real devem contornar pelo
# raio físico da fresa. Um miter ilimitado inventaria uma ponta além do raio e
# faria estes dois percursos se cruzarem apesar da folga correta de Ø4 mm.
pointed_settings = dict(preserve_common_settings)
pointed_settings.update(tool_diameter=4.0, cut_tabs_enabled=False)
pointed_common_moves = dialog._build_common_line_outer_moves(
    [
        [(0.0, 0.0), (20.0, 100.0), (0.0, 200.0)],
        [(44.0, 0.0), (24.0, 100.0), (44.0, 200.0)],
    ],
    pointed_settings,
    4.0,
    (0.0, 0.0),
)
assert pointed_common_moves is None
pointed_moves = ui.build_contour_cut_stage(
    [
        [(0.0, 0.0), (20.0, 100.0), (0.0, 200.0)],
        [(44.0, 0.0), (24.0, 100.0), (44.0, 200.0)],
    ],
    [],
    final_depth=3.0,
    stepdown=3.0,
    ramp_length=0.0,
    safe_height=5.0,
    tool_diameter=4.0,
    outer_cut_side=ui.CUT_SIDE_OUTSIDE,
    material_thickness=6.0,
)
assert pointed_moves

try:
    dialog._build_common_line_outer_moves(
        [common_first, common_touching],
        common_line_settings,
        6.0,
        (0.0, 0.0),
    )
    raise AssertionError("linha comum sobre o vetor não pediu confirmação")
except ValueError as error:
    assert str(error).startswith("__WOODCAM_CONFIRM_COMMON_LINE_ON_VECTOR__")

confirmed_common_settings = dict(common_line_settings)
confirmed_common_settings.update(
    _common_line_on_vector_confirmed=True,
    cut_tabs_enabled=True,
    tab_count=2,
    tab_length=8.0,
    tab_thickness=3.0,
    tab_best_fixation=True,
)
confirmed_common_moves = dialog._build_common_line_outer_moves(
    [common_first, common_touching],
    confirmed_common_settings,
    6.0,
    (0.0, 0.0),
)
# Uma rede de duas peças tem três trilhas físicas: a fronteira comum e um
# perímetro exclusivo contínuo para cada peça. Tabs não viram novas entradas.
assert sum(
    move["type"] == "rapid" and move.get("x") is not None
    for move in confirmed_common_moves
) == 3
assert any(move.get("tab") for move in confirmed_common_moves)

# O caminho novo precisa atravessar a integração real do diálogo, não somente
# a camada pura: híbrido, linha comum, tabs mantidas no corte principal e uma
# fase posterior de plunge/retração. Os IDs estruturais permitem comprovar que
# nenhuma aresta física reaparece na mesma profundidade.
global_common_settings = dict(confirmed_common_settings)
global_common_settings.update(
    cut_depth_strategy="hybrid_stability",
    tab_release_mode="automatic_release",
    tab_count=3,
    tab_length=4.0,
    tool_type="end_mill",
    final_depth=15.5,
    stepdown=5.0,
    material_thickness=15.0,
    start_depth=0.0,
    pass_depths=None,
    return_to_start=False,
)
global_common_moves = dialog._build_global_cut_stage_moves(
    [common_first, common_touching],
    [],
    global_common_settings,
    6.0,
    (0.0, 0.0),
)
assert global_common_moves
assert {move.get("cut_strategy") for move in global_common_moves if move.get("global_cut")} == {
    "hybrid_stability"
}
assert any(move.get("cut_phase") == "shared" for move in global_common_moves)
assert any(move.get("cut_phase") == "tab_release" for move in global_common_moves)
seen_physical_passes = set()
for move in global_common_moves:
    operation_id = move.get("cut_operation_id")
    if not operation_id or move.get("type") != "feed_cut":
        continue
    for segment_id in move.get("segment_ids", ()):
        key = (operation_id, segment_id, move.get("depth_pass"))
        seen_physical_passes.add(key)
last_release_cut = max(
    index
    for index, move in enumerate(global_common_moves)
    if move.get("tab_release_last_for_piece")
    and move.get("type") in {"feed_plunge", "feed_cut"}
)
assert global_common_moves[last_release_cut + 1]["type"] == "rapid"
assert global_common_moves[last_release_cut + 1]["x"] is None

common_narrow_gap = [
    (0.0, 53.0),
    (100.0, 53.0),
    (100.0, 103.0),
    (0.0, 103.0),
]
try:
    dialog._build_common_line_outer_moves(
        [common_first, common_narrow_gap],
        common_line_settings,
        6.0,
        (0.0, 0.0),
    )
    raise AssertionError("folga menor que a fresa não foi bloqueada")
except ValueError as error:
    assert "não forma uma fronteira comum" in str(error)

# Cruzamento/sobreposição real não pode ser confundido com uma junção T. A
# mensagem precisa orientar a correção do layout e localizar o primeiro defeito.
common_overlapping = [
    (50.0, 25.0),
    (150.0, 25.0),
    (150.0, 75.0),
    (50.0, 75.0),
]
try:
    dialog._build_common_line_outer_moves(
        [common_first, common_overlapping],
        confirmed_common_settings,
        6.0,
        (0.0, 0.0),
    )
    raise AssertionError("sobreposição real não foi bloqueada")
except ValueError as error:
    message = str(error)
    assert "Reorganize as peças" in message
    assert "próximo de X" in message

# A confirmação consciente recompõe o percurso com um marcador transitório;
# ela não altera o preset persistido nem ignora outras validações.
original_build_moves = dialog._build_moves_from_selection
original_common_warning = QtWidgets.QMessageBox.warning
confirmation_messages = []


def _confirmed_common_line_build(settings):
    if not settings.get("_common_line_on_vector_confirmed"):
        raise ValueError(
            "__WOODCAM_CONFIRM_COMMON_LINE_ON_VECTOR__perda dimensional smoke"
        )
    return ["confirmed-common-line"]


try:
    dialog._build_moves_from_selection = _confirmed_common_line_build
    QtWidgets.QMessageBox.warning = staticmethod(
        lambda _parent, title, message, *_buttons: (
            confirmation_messages.append((title, message))
            or QtWidgets.QMessageBox.Yes
        )
    )
    confirmation_settings = {}
    assert dialog._build_moves_with_intersection_confirmation(
        confirmation_settings
    ) == ["confirmed-common-line"]
    assert confirmation_settings["_common_line_on_vector_confirmed"] is True
finally:
    dialog._build_moves_from_selection = original_build_moves
    QtWidgets.QMessageBox.warning = original_common_warning
assert confirmation_messages
assert confirmation_messages[0][0] == "Linha comum altera as medidas"

# Uma fenda do próprio contorno menor que a fresa não pode desaparecer
# silenciosamente na compensação externa. A confirmação afeta somente os
# contornos marcados e é reutilizada entre Pré-visualizar/Aplicar enquanto a
# geometria, a ferramenta e a região de risco continuam idênticas.
original_narrow_build = dialog._build_moves_from_selection
original_narrow_warning = QtWidgets.QMessageBox.warning
narrow_confirmation_messages = []
narrow_signature = ("document-smoke", "geometry-smoke", 4.0, ((0, 51.0, 60.0),))


def _confirmed_narrow_feature_build(settings):
    settings["_narrow_feature_confirmation_signature"] = narrow_signature
    if not settings.get("allow_narrow_feature_overcut"):
        raise ValueError(
            "__WOODCAM_CONFIRM_NARROW_FEATURE_OVERCUT__fenda estreita smoke"
        )
    settings["_narrow_feature_cleanup_paths"] = [
        {
            "contour_index": 0,
            "points": [[51.0, 100.0], [51.0, 60.0]],
            "mode": "slot_centerline",
        }
    ]
    return ["confirmed-narrow-feature"]


try:
    if hasattr(dialog, "_confirmed_narrow_feature_signature"):
        del dialog._confirmed_narrow_feature_signature
    dialog._build_moves_from_selection = _confirmed_narrow_feature_build
    QtWidgets.QMessageBox.warning = staticmethod(
        lambda _parent, title, message, *_buttons: (
            narrow_confirmation_messages.append((title, message))
            or QtWidgets.QMessageBox.Yes
        )
    )
    narrow_settings = {}
    assert dialog._build_moves_with_intersection_confirmation(
        narrow_settings
    ) == ["confirmed-narrow-feature"]
    assert narrow_settings["allow_narrow_feature_overcut"] is True
    assert narrow_settings["_narrow_feature_cleanup_paths"][0]["mode"] == (
        "slot_centerline"
    )
    repeated_settings = {}
    assert dialog._build_moves_with_intersection_confirmation(
        repeated_settings
    ) == ["confirmed-narrow-feature"]
    assert repeated_settings["allow_narrow_feature_overcut"] is True
finally:
    dialog._build_moves_from_selection = original_narrow_build
    QtWidgets.QMessageBox.warning = original_narrow_warning
assert len(narrow_confirmation_messages) == 1
assert narrow_confirmation_messages[0][0] == "Região menor que a fresa"

# Fluxo literal relatado pelo operador: desenhar quatro quadrados no Editor 2D,
# reconhecê-los como peças, organizar com o contrato da fresa e pedir o Corte
# híbrido pela própria fonte do Editor. Este caso não pode ser substituído por
# uma chamada direta ao planner, porque isso esconderia erros na ponte UI→CAM.
workflow_doc = FreeCAD.newDocument("WoodCAMEditorUiHybridWorkflow")
workflow_doc.UndoMode = 1
FreeCAD.setActiveDocument(workflow_doc.Name)
dialog._sync_vector_editor_from_freecad_history()
app.processEvents()
workflow_widget = dialog.vector_editor_widget
workflow_widget.cancel_workflow_preview()
assert not workflow_widget.workflow_preview_bar.is_active
workflow_widget.controller.execute(
    ui.SetWorkAreaCommand(ui.WorkArea(0.0, 0.0, 178.0, 98.0))
)
workflow_outer_ids = (
    workflow_widget.controller.add_rectangle(
        workflow_widget.controller.vec(220.0, 120.0),
        workflow_widget.controller.vec(300.0, 160.0),
    ),
    workflow_widget.controller.add_rectangle(
        workflow_widget.controller.vec(0.0, 0.0),
        workflow_widget.controller.vec(80.0, 40.0),
    ),
    workflow_widget.controller.add_rectangle(
        workflow_widget.controller.vec(220.0, 0.0),
        workflow_widget.controller.vec(300.0, 40.0),
    ),
    workflow_widget.controller.add_rectangle(
        workflow_widget.controller.vec(0.0, 120.0),
        workflow_widget.controller.vec(80.0, 160.0),
    ),
)

# Regressão literal do tab manual iniciado pela configuração de Corte do
# Editor 2D. A seleção 3D do FreeCAD fica vazia de propósito: o botão não pode
# voltar a pedir Sketch/face/objeto 3D, nem registrar o clique na câmera 3D.
previous_tab_index = dialog.operation_tabs.currentIndex()
previous_editor_source = dialog._use_vector_editor_for_cam
previous_tabs_enabled = dialog.cut_tabs_enabled.isChecked()
previous_tabs_manual = dialog.cut_tabs_manual_enabled.isChecked()
previous_tabs_auto = dialog.cut_tabs_auto_enabled.isChecked()
previous_tab_positions = list(dialog.cut_tab_positions)
previous_tab_scope = getattr(dialog, "_cut_tab_positions_scope", None)
previous_tab_operation = getattr(dialog, "_cut_tab_positions_operation", None)
manual_tab_group = ui.GroupEntity(
    layer_id=workflow_widget.document.active_layer_id,
    child_ids=(workflow_outer_ids[1],),
    id="ui-smoke-manual-tab-group",
)
workflow_widget.controller.execute(ui.AddEntitiesCommand((manual_tab_group,)))
try:
    if hasattr(ui.FreeCADGui, "Selection"):
        ui.FreeCADGui.Selection.clearSelection()
    workflow_widget.controller.selection.select_only(manual_tab_group.id)
    dialog._vector_editor_configure_toolpath("cut")
    dialog.cut_tabs_enabled.setChecked(True)
    dialog.cut_tabs_manual_enabled.setChecked(True)
    assert not dialog.cut_tabs_auto_enabled.isChecked()
    # Reproduz o estado mostrado pelo operador: posições de outra chapa foram
    # restauradas como preferência global e a prévia anterior continuou
    # desenhando rápidos magenta durante a marcação manual.
    dialog.cut_tab_positions = [
        {"x": 107.0, "y": 97.0},
        {"x": 107.0, "y": 230.0},
        {"x": 112.0, "y": 230.0},
    ]
    dialog._cut_tab_positions_scope = "stale-other-contour"
    dialog._cut_tab_positions_operation = None
    assert "tab_positions" not in dialog._operation_preferences_snapshot()
    assert workflow_widget.show_toolpath_preview(
        {"rapid": [((0.0, 0.0, 8.0), (80.0, 40.0, 8.0))]},
        "Corte",
    )
    assert workflow_widget.overlays.toolpath_items["rapid"].isVisible()
    revision_before_tabs = workflow_widget.document.revision

    dialog._start_tab_marker_mode()
    app.processEvents()
    assert not dialog.cut_tab_positions
    assert not workflow_widget.overlays.tab_marker_items
    assert not workflow_widget.overlays.toolpath_items["rapid"].isVisible()
    assert dialog._tab_marker_source == "editor2d"
    assert workflow_widget.tool_manager.point_capture_active
    assert dialog._tab_title(dialog.operation_tabs.currentIndex()) == "Editor 2D"
    workflow_widget.fit_entities((workflow_outer_ids[1],))
    app.processEvents()

    # O interior distante não vira tab: a tolerância é de tela, independente
    # do zoom. Um clique perto da aresta projeta exatamente sobre o contorno.
    interior = workflow_widget.view.mapFromScene(QtCore.QPointF(40.0, 20.0))
    QtTest.QTest.mouseClick(
        workflow_widget.view.viewport(),
        QtCore.Qt.LeftButton,
        QtCore.Qt.NoModifier,
        interior,
    )
    app.processEvents()
    assert not dialog.cut_tab_positions
    edge = workflow_widget.view.mapFromScene(QtCore.QPointF(60.0, 0.0))
    QtTest.QTest.mouseClick(
        workflow_widget.view.viewport(),
        QtCore.Qt.LeftButton,
        QtCore.Qt.NoModifier,
        edge,
    )
    app.processEvents()
    assert len(dialog.cut_tab_positions) == 1, dialog.cut_tab_positions
    assert abs(float(dialog.cut_tab_positions[0]["y"])) <= 1.0e-8
    assert len(workflow_widget.overlays.tab_marker_items) == 1
    assert workflow_widget.document.revision == revision_before_tabs

    # Clicar na própria marca remove; recolocar e botão direito conclui,
    # preservando o GroupEntity que é a fonte do Corte.
    QtTest.QTest.mouseClick(
        workflow_widget.view.viewport(),
        QtCore.Qt.LeftButton,
        QtCore.Qt.NoModifier,
        edge,
    )
    app.processEvents()
    assert not dialog.cut_tab_positions
    QtTest.QTest.mouseClick(
        workflow_widget.view.viewport(),
        QtCore.Qt.LeftButton,
        QtCore.Qt.NoModifier,
        edge,
    )
    QtTest.QTest.mouseClick(
        workflow_widget.view.viewport(),
        QtCore.Qt.RightButton,
        QtCore.Qt.NoModifier,
        edge,
    )
    app.processEvents()
    assert len(dialog.cut_tab_positions) == 1
    assert dialog._tab_marker_source is None
    assert not workflow_widget.tool_manager.point_capture_active
    assert dialog._tab_title(dialog.operation_tabs.currentIndex()) == "Corte"
    assert workflow_widget.controller.selection.ids == (manual_tab_group.id,)
    assert workflow_widget.document.revision == revision_before_tabs

    # Reabrir na mesma configuração conserva somente a tab colocada agora;
    # não transforma o botão em uma ação destrutiva de "começar do zero".
    dialog._start_tab_marker_mode()
    app.processEvents()
    assert len(dialog.cut_tab_positions) == 1
    assert len(workflow_widget.overlays.tab_marker_items) == 1
    dialog._finish_tab_marker_mode()
    app.processEvents()
    grouped_geometry = dialog._vector_editor_geometry_for_cam("cut")
    assert len(grouped_geometry["contours"]) == 1, grouped_geometry
    manual_settings = dialog._collect_settings()
    assert manual_settings["tab_count"] == 0
    assert manual_settings["tab_positions"] == dialog.cut_tab_positions
    manual_tab_moves = build_external_cut_moves(
        [(0.0, 0.0), (80.0, 0.0), (80.0, 40.0), (0.0, 40.0)],
        final_depth=15.0,
        stepdown=5.0,
        ramp_length=0.0,
        safe_height=8.0,
        compensate_external=False,
        smart_entry=False,
        tabs_enabled=True,
        tab_length=8.0,
        tab_thickness=3.0,
        tab_count=manual_settings["tab_count"],
        tab_positions=manual_settings["tab_positions"],
    )
    assert any(move.get("tab") for move in manual_tab_moves)

    # A linha de separação de retalho é geometria real e selecionável, mas
    # entra no CAM somente quando escolhida explicitamente em Corte. O percurso
    # é aberto e segue o centro do vetor, sem fechamento ou compensação.
    remnant_cut = ui.PathEntity.from_points(
        workflow_widget.document.active_layer_id,
        (ui.Vec2(10.0, 60.0), ui.Vec2(70.0, 60.0)),
        metadata={
            "woodcam_role": "remnant_cut",
            "sheet_index": 0,
            "cut_start": [10.0, 60.0],
            "cut_end": [70.0, 60.0],
            "remnant_bounds": [10.0, 60.0, 80.0, 100.0],
            "remnant_area_mm2": 2800.0,
        },
    )
    workflow_widget.controller.execute(ui.AddEntitiesCommand((remnant_cut,)))
    workflow_widget.controller.selection.select_only(remnant_cut.id)
    dialog._use_vector_editor_for_cam = False
    remnant_geometry_from_active_source = dialog._active_geometry("cut")
    assert remnant_geometry_from_active_source["remnant_cut_entity_ids"] == [
        remnant_cut.id
    ]
    assert dialog._use_vector_editor_for_cam
    remnant_geometry = dialog._vector_editor_geometry_for_cam("cut")
    assert not remnant_geometry["contours"]
    assert not remnant_geometry["holes"]
    assert remnant_geometry["remnant_cut_entity_ids"] == [remnant_cut.id]
    remnant_settings = dialog._collect_settings()
    remnant_stages = dialog._build_stage_moves_from_selection(remnant_settings)
    remnant_moves = remnant_stages["cut"]
    assert remnant_moves
    assert all(move.get("remnant_cut") for move in remnant_moves)
    cutting_xy = {
        (round(float(move["x"]), 6), round(float(move["y"]), 6))
        for move in remnant_moves
        if "x" in move and "y" in move and float(move.get("z", 1.0)) <= 0.0
    }
    assert (10.0, 60.0) in cutting_xy, cutting_xy
    assert (70.0, 60.0) in cutting_xy, cutting_xy

    # Regressão literal: depois que o nesting criou linhas laranjas de
    # separação, Ctrl+A também as selecionava e impedia tanto ativar a fonte
    # do Editor quanto cortar as peças normais. Em seleção mista, essas linhas
    # especializadas ficam fora do Corte comum; continuam entrando somente
    # quando são a seleção exclusiva, como validado acima.
    workflow_widget.view.setFocus()
    QtTest.QTest.keyClick(
        workflow_widget.view,
        QtCore.Qt.Key_A,
        QtCore.Qt.ControlModifier,
    )
    app.processEvents()
    expanded_select_all = workflow_widget.controller.expand_group_children(
        workflow_widget.controller.selection.ids
    )
    assert remnant_cut.id in expanded_select_all
    assert set(workflow_outer_ids).issubset(set(expanded_select_all))
    dialog._use_vector_editor_for_cam = False
    workflow_widget.set_cam_source_active(False)
    workflow_widget.use_cam_action.trigger()
    assert dialog._use_vector_editor_for_cam
    assert workflow_widget.use_cam_action.isChecked()
    select_all_geometry = dialog._active_geometry("cut")
    assert len(select_all_geometry["contours"]) == len(workflow_outer_ids), (
        select_all_geometry
    )
    assert not select_all_geometry.get("remnant_cuts")

    workflow_widget.controller.execute(ui.DeleteEntitiesCommand((remnant_cut.id,)))
    workflow_widget.controller.selection.select_only(manual_tab_group.id)

    # Reproduz a chapa inteira mostrada na captura: três marcas em uma peça
    # continuam três tabs físicas, sem serem projetadas sobre cada perfil.
    manual_sheet_moves = build_external_cut_job(
        (
            [(0.0, 0.0), (100.0, 0.0), (100.0, 60.0), (0.0, 60.0)],
            [(300.0, 0.0), (400.0, 0.0), (400.0, 60.0), (300.0, 60.0)],
        ),
        final_depth=15.0,
        stepdown=15.0,
        ramp_length=0.0,
        safe_height=8.0,
        compensate_external=False,
        smart_entry=False,
        tabs_enabled=True,
        tab_length=8.0,
        tab_thickness=3.0,
        tab_count=0,
        tab_positions=(
            {"x": 20.0, "y": 0.0},
            {"x": 50.0, "y": 0.0},
            {"x": 80.0, "y": 0.0},
        ),
    )
    manual_sheet_reports = {
        report["profile_id"]: report["tab_crossing_count"]
        for report in audit_profile_cut_moves(manual_sheet_moves)
    }
    assert manual_sheet_reports == {
        "profile-0001": 3,
        "profile-0002": 0,
    }, manual_sheet_reports
finally:
    if dialog._tab_marker_callback is not None:
        dialog._finish_tab_marker_mode()
    dialog.cut_tab_positions = previous_tab_positions
    dialog._cut_tab_positions_scope = previous_tab_scope
    dialog._cut_tab_positions_operation = previous_tab_operation
    dialog.cut_tabs_enabled.setChecked(previous_tabs_enabled)
    dialog.cut_tabs_manual_enabled.setChecked(previous_tabs_manual)
    dialog.cut_tabs_auto_enabled.setChecked(previous_tabs_auto)
    dialog._use_vector_editor_for_cam = previous_editor_source
    workflow_widget.set_cam_source_active(previous_editor_source)
    dialog.operation_tabs.setCurrentIndex(previous_tab_index)
    workflow_widget.controller.selection.replace(workflow_outer_ids)
    workflow_widget.clear_tab_markers()
    if workflow_widget.document.get_entity(manual_tab_group.id) is not None:
        workflow_widget.controller.execute(
            ui.DeleteEntitiesCommand((manual_tab_group.id,))
        )
    app.processEvents()

dialog.operation_tabs.setCurrentIndex(dialog._tab_index("Corte"))
dialog.operation_combo.setCurrentIndex(0)
dialog.operation_fields["cut"]["tool_diameter"].setText("6")
dialog.operation_fields["cut"]["cut_allowance_offset"].setText("0")
dialog.operation_fields["cut"]["start_depth"].setText("0")
dialog.operation_fields["cut"]["cut_depth"].setText("15.5")
dialog.operation_fields["cut"]["stepdown"].setText("5")
dialog.operation_ramp_checks["cut"].setChecked(True)
dialog.operation_fields["cut"]["ramp_length"].setText("20")
dialog.cut_ramp_type_buttons[0].setChecked(True)
dialog.fields["material_thickness"].setText("15")
dialog.job_origin_buttons["bottom_left"].setChecked(True)
dialog.fields["job_origin_x"].setText("0")
dialog.fields["job_origin_y"].setText("0")
dialog.fields["start_x"].setText("0")
dialog.fields["start_y"].setText("0")
dialog.return_to_start.setChecked(False)
dialog.cut_common_line_enabled.setChecked(True)
dialog.cut_common_line_mode_combo.setCurrentIndex(
    dialog.cut_common_line_mode_combo.findData("preserve_dimensions")
)
dialog.cut_depth_strategy_combo.setCurrentIndex(
    dialog.cut_depth_strategy_combo.findData("hybrid_piece_bidirectional")
)
dialog.cut_tabs_enabled.setChecked(True)
dialog.cut_tabs_auto_enabled.setChecked(True)
dialog.cut_tabs_best_fixation.setChecked(True)
dialog.cut_tabs_3d.setChecked(False)
dialog.cut_tab_release_checkbox.setChecked(False)
dialog.operation_fields["cut"]["tab_count"].setText("3")
dialog.operation_fields["cut"]["tab_length"].setText("8")
dialog.operation_fields["cut"]["tab_thickness"].setText("3")
dialog._vector_editor_organize_pieces(
    search_mode="fast",
    spacing=0.0,
    time_budget_seconds=2,
)
assert wait_until(lambda: workflow_widget.workflow_preview_bar.is_active)
workflow_widget.workflow_preview_bar.apply_button.click()
app.processEvents()
# Aplicar a primeira solução pede interrupção, mas o worker pode ainda estar
# encerrando uma tentativa imutável. O operador já pode pedir Corte nesse
# intervalo; essa é justamente a sequência literal validada aqui.
assert not workflow_widget.document.pieces_by_id
assert set(workflow_widget.controller.selection.ids) == set(workflow_outer_ids)
workflow_geometry = dialog._vector_editor_geometry_for_cam()
assert len(workflow_geometry["contours"]) == 4, workflow_geometry
workflow_settings = dialog._collect_settings()
assert workflow_settings["tool_diameter"] == 6.0, workflow_settings["tool_diameter"]
assert workflow_settings["outer_cut_side"] == ui.CUT_SIDE_OUTSIDE
assert workflow_settings["common_line_enabled"]
assert workflow_settings["ramp_type"] == "smooth"
workflow_preflight, workflow_centre_lines = dialog._build_common_line_outer_moves(
    workflow_geometry["contours"],
    workflow_settings,
    6.0,
    (0.0, 0.0),
    return_plan_data=True,
)
assert len(workflow_preflight.shared_segments) >= 4, (
    len(workflow_preflight.shared_segments),
    workflow_settings["common_line_tolerance"],
)
previous_editor_source = dialog._use_vector_editor_for_cam
try:
    dialog._use_vector_editor_for_cam = True
    workflow_moves = dialog._build_moves_with_intersection_confirmation(
        workflow_settings
    )
finally:
    dialog._use_vector_editor_for_cam = previous_editor_source
workflow_smooth_ramps = [
    move for move in workflow_moves if move.get("entry_ramp")
]
assert workflow_smooth_ramps
assert all(
    move.get("ramp_effective_type") == "smooth"
    and move.get("ramp_geometry") == "physical_trail_single_slope"
    for move in workflow_smooth_ramps
), workflow_smooth_ramps
assert not any(
    move.get("ramp_geometry") == "physical_trail_out_and_back"
    for move in workflow_smooth_ramps
)
workflow_summary = workflow_settings["_global_cut_plan_summary"]
workflow_xy_rapids = [
    (
        move.get("depth_pass"),
        move.get("cut_operation_id"),
        float(move["x"]),
        float(move["y"]),
        move.get("routing_mode"),
    )
    for move in workflow_moves
    if move.get("type") == "rapid"
    and move.get("x") is not None
    and move.get("y") is not None
]
workflow_cut_entries = [
    item for item in workflow_xy_rapids if item[0] is not None
]
workflow_intermediate_entries = [
    item for item in workflow_cut_entries if item[4] == "piece_bidirectional"
]
workflow_intermediate_owner_ids = {
    operation.get("executing_owner_id")
    for operation in workflow_summary["operation_sequence"]
    if operation.get("routing_mode") == "piece_bidirectional"
    and operation.get("executing_owner_id")
}
workflow_final_entries = [
    item for item in workflow_cut_entries if item[4] == "final_sheet_pass"
]
# Every piece is visited in the paired intermediate depths. Usually that means
# one safe-Z entry per piece, but an adjacent piece may be reached while low
# through a proven short kerf from an earlier depth. Therefore operation
# coverage is mandatory while the number of rapid entries may be smaller.
# The final depth starts only after those visits and uses its own sheet pass.
assert len(workflow_intermediate_owner_ids) == len(workflow_outer_ids), (
    workflow_intermediate_owner_ids,
    workflow_intermediate_entries,
)
assert 1 <= len(workflow_intermediate_entries) <= len(workflow_outer_ids), (
    workflow_intermediate_entries
)
assert workflow_final_entries, workflow_cut_entries
first_intermediate_ramp_end = next(
    move
    for move in workflow_moves
    if move.get("entry_ramp")
    and move.get("routing_mode") == "piece_bidirectional"
)
# A aproximação segura da rampa suave começa adiante no trail; quem deve
# permanecer ancorado à origem configurada é o fim da rampa/início do corte.
assert math.hypot(
    float(first_intermediate_ramp_end["x"]),
    float(first_intermediate_ramp_end["y"]),
) < 10.0, (
    first_intermediate_ramp_end
)
ramp_end_by_operation = {}
for move in workflow_moves:
    if move.get("entry_ramp") and move.get("routing_mode") == "piece_bidirectional":
        ramp_end_by_operation[move.get("cut_operation_id")] = move
workflow_intermediate_ramp_ends = [
    ramp_end_by_operation[operation["operation_id"]]
    for operation in workflow_summary["operation_sequence"]
    if operation.get("routing_mode") == "piece_bidirectional"
    and operation["operation_id"] in ramp_end_by_operation
]
assert max(
    math.hypot(
        float(current["x"]) - float(previous["x"]),
        float(current["y"]) - float(previous["y"]),
    )
    for previous, current in zip(
        workflow_intermediate_ramp_ends,
        workflow_intermediate_ramp_ends[1:],
    )
) < 100.0, workflow_cut_entries
assert workflow_summary["strategy"] == "hybrid_piece_bidirectional"
assert workflow_summary["hybrid_intermediate_mode"] == "piece_bidirectional"
assert workflow_summary["shared_segment_count"] >= 4, workflow_geometry
assert len(workflow_summary["tabs"]) == workflow_summary["tab_count"]
assert {
    tab["kind"] for tab in workflow_summary["tabs"]
} <= {"stock_tab", "shared_tab", "waste_tab"}
assert workflow_summary["tab_release_count"] == 0
intermediate_workflow_metrics = [
    metric
    for metric in workflow_summary["route_metrics"]
    if metric["depth"] < 15.0
]
assert intermediate_workflow_metrics
assert all(
    metric["routing_mode"] == "piece_bidirectional"
    for metric in intermediate_workflow_metrics
)
assert all(metric["duplicate_physical_segment_count"] == 0 for metric in intermediate_workflow_metrics)
bidirectional_sequence = [
    operation
    for operation in workflow_summary["operation_sequence"]
    if operation["routing_mode"] == "piece_bidirectional"
]
assert len(bidirectional_sequence) % 2 == 0
bidirectional_by_trail = {}
for operation in bidirectional_sequence:
    bidirectional_by_trail.setdefault(operation["trail_id"], []).append(operation)
for trail_operations in bidirectional_by_trail.values():
    assert len(trail_operations) == 2, trail_operations
    first_operation, second_operation = sorted(
        trail_operations,
        key=lambda operation: operation["depth"],
    )
    assert first_operation["executing_owner_id"] == second_operation["executing_owner_id"]
    assert first_operation["trail_id"] == second_operation["trail_id"]
    assert (first_operation["depth"], second_operation["depth"]) == (5.0, 10.0)
    first_operation_moves = [
        move
        for move in workflow_moves
        if move.get("cut_operation_id") == first_operation["operation_id"]
    ]
    assert first_operation_moves
    if any(move.get("physical_trail_closed") for move in first_operation_moves):
        assert first_operation["reverse_trail"] == second_operation["reverse_trail"]
    else:
        assert first_operation["reverse_trail"] != second_operation["reverse_trail"]
assert any(
    move.get("tab")
    for move in workflow_moves
    if move.get("routing_mode") == "piece_bidirectional"
)
intermediate_ramps = [
    move
    for move in workflow_moves
    if move.get("routing_mode") == "piece_bidirectional"
    and move.get("entry_ramp")
]
assert intermediate_ramps
assert {move.get("depth_pass") for move in intermediate_ramps} == {5.0, 10.0}
assert not any(
    move.get("tab")
    and move.get("depth_pass") == 5.0
    and float(move.get("z", 0.0)) < -1.0e-7
    for move in workflow_moves
    if move.get("routing_mode") == "piece_bidirectional"
)
final_workflow_metrics = [
    metric
    for metric in workflow_summary["route_metrics"]
    if metric["routing_mode"] == "final_sheet_pass"
]
assert final_workflow_metrics
assert final_workflow_metrics[-1]["rapid_distance"] < 400.0
assert not any(
    move.get("cleared_path_link")
    and move.get("depth_pass") == move.get("link_target_depth_pass") == 15.5
    for move in workflow_moves
)
assert all(
    float(move.get("cleared_path_link_length", 0.0)) <= 9.0
    for move in workflow_moves
    if move.get("cleared_path_link")
    and not move.get("entry_ramp_approach")
    and move.get("link_target_depth_pass") == 15.5
)
for depth in {operation["depth"] for operation in workflow_summary["operation_sequence"]}:
    segment_ids = [
        segment_id
        for operation in workflow_summary["operation_sequence"]
        if operation["depth"] == depth
        for segment_id in operation["segment_ids"]
    ]
    assert len(segment_ids) == len(set(segment_ids)), depth

# "Todas direto" usa o mesmo scheduler ida/volta, mas inclui também a última
# profundidade na visita da peça. Ele não pode regressar ao ``per_piece``
# legado, nem criar uma fase final de chapa inteira.
direct_settings = dict(workflow_settings)
direct_settings["cut_depth_strategy"] = "piece_bidirectional"
direct_settings["ramp_type"] = "spiral"
previous_editor_source = dialog._use_vector_editor_for_cam
try:
    dialog._use_vector_editor_for_cam = True
    direct_moves = dialog._build_moves_with_intersection_confirmation(
        direct_settings
    )
finally:
    dialog._use_vector_editor_for_cam = previous_editor_source
direct_summary = direct_settings["_global_cut_plan_summary"]
assert direct_summary["strategy"] == "piece_bidirectional"
assert not any(
    operation["routing_mode"] == "final_sheet_pass"
    for operation in direct_summary["operation_sequence"]
)
direct_by_owner = {}
for operation in direct_summary["operation_sequence"]:
    direct_by_owner.setdefault(operation["executing_owner_id"], []).append(
        operation["depth"]
    )
assert set(direct_by_owner) == workflow_intermediate_owner_ids
assert all(depths == [5.0, 10.0, 15.5] for depths in direct_by_owner.values())
direct_entries = [
    move
    for move in direct_moves
    if move.get("type") == "rapid"
    and move.get("x") is not None
    and move.get("cut_operation_id")
]
assert 1 <= len(direct_entries) <= len(direct_by_owner), direct_entries
direct_ramps = [move for move in direct_moves if move.get("entry_ramp")]
assert direct_ramps
assert all(move.get("ramp_requested_type") == "spiral" for move in direct_ramps)
assert any(
    move.get("ramp_geometry") == "validated_tangent_spiral"
    for move in direct_ramps
)
assert any(move.get("ramp_fallback") for move in direct_ramps)

for previous, current in zip(workflow_moves, workflow_moves[1:]):
    assert not (
        previous.get("type") == current.get("type") == "feed_plunge"
        and previous.get("x") == current.get("x")
        and previous.get("y") == current.get("y")
    ), (previous, current)
tab_cut_lengths = []
previous_xy = None
for move in workflow_moves:
    if move.get("x") is None or move.get("y") is None:
        continue
    current_xy = (float(move["x"]), float(move["y"]))
    if (
        move.get("tab")
        and move.get("final_pass")
        and move.get("type") == "feed_cut"
        and previous_xy is not None
    ):
        tab_cut_lengths.append(math.dist(previous_xy, current_xy))
    previous_xy = current_xy
assert tab_cut_lengths and min(tab_cut_lengths) > 1.0e-7
assert len(tab_cut_lengths) == workflow_summary["tab_count"], (
    len(tab_cut_lengths),
    workflow_summary["tab_count"],
)
components = dialog._toolpath_components(workflow_moves, max_display_segments=None)
assert workflow_widget.show_toolpath_preview(components, "Corte")
workflow_widget.view.resetTransform()
workflow_widget.view.scale(1.0, -1.0)
workflow_widget.view.centerOn(QtCore.QPointF(89.0, 49.0))
app.processEvents()
assert workflow_widget.grab().save("/tmp/woodcam_hybrid_editor_flow.png")

# Regressão relatada pelo operador: depois de aplicar um Corte, voltar ao
# Editor 2D e pedir Percursos 2D deve reutilizar os movimentos persistidos.
# A seleção vetorial atual pode ter mudado e não deve disparar um recálculo.
persisted_workflow_operation = dialog._create_applied_operation(
    workflow_settings,
    workflow_moves,
    selection_snapshot=[],
)
persisted_physical_markers = [
    child
    for child in persisted_workflow_operation.Group
    if hasattr(child, "WoodCAMTabKind")
]
assert persisted_physical_markers
assert sum(
    len(marker.Shape.Edges) for marker in persisted_physical_markers
) == workflow_summary["tab_count"]
dialog._refresh_applied_operation_list()
previous_workflow_tab = dialog.operation_tabs.currentIndex()
original_workflow_builder = dialog._build_moves_with_intersection_confirmation
try:
    dialog.operation_tabs.setCurrentIndex(dialog._tab_index("Editor 2D"))
    dialog._build_moves_with_intersection_confirmation = lambda _settings: (
        (_ for _ in ()).throw(
            AssertionError("Percurso aplicado foi recalculado pela seleção atual")
        )
    )
    persisted_settings, persisted_moves = (
        dialog._operation_settings_and_moves_for_editor_preview("cut")
    )
finally:
    dialog._build_moves_with_intersection_confirmation = original_workflow_builder
    dialog.operation_tabs.setCurrentIndex(previous_workflow_tab)
assert persisted_settings["operation_mode"] == "cut"
assert persisted_moves == json.loads(json.dumps(workflow_moves))
assert persisted_workflow_operation.MoveCount == len(workflow_moves)

if getattr(dialog, "_vector_editor_nesting_session", None) is not None:
    dialog._cancel_vector_editor_nesting_search(keep_preview=True)
    assert wait_until(
        lambda: getattr(dialog, "_vector_editor_nesting_session", None) is None,
        timeout_seconds=60.0,
    )

# CAM valida o escopo explícito. Uma cópia coincidente selecionada sozinha é
# uma intenção válida; as duas cópias selecionadas juntas continuam bloqueadas
# para não gerar duas passadas acidentais no mesmo vetor.
copy_original_id = workflow_widget.controller.add_rectangle(
    workflow_widget.controller.vec(900.0, 900.0),
    workflow_widget.controller.vec(940.0, 930.0),
)
copy_selected_id = workflow_widget.controller.add_rectangle(
    workflow_widget.controller.vec(900.0, 900.0),
    workflow_widget.controller.vec(940.0, 930.0),
)
workflow_widget.controller.selection.select_only(copy_selected_id)
single_copy_geometry = dialog._vector_editor_geometry_for_cam("cut")
assert len(single_copy_geometry["contours"]) == 1, single_copy_geometry
workflow_widget.controller.selection.replace(
    (copy_original_id, copy_selected_id)
)
try:
    dialog._vector_editor_geometry_for_cam("cut")
except ui.GeometryAdapterError as error:
    assert "duplicada" in str(error).lower(), error
else:
    raise AssertionError("Duas cópias coincidentes selecionadas deveriam bloquear o CAM")

# Selecionar a hachura de um rebaixo importado envia somente sua região ao
# Preenchimento e pré-carrega a profundidade detectada para confirmação.
pocket_entity = replace(
    ui.PathEntity.from_points(
        workflow_widget.document.active_layer_id,
        (
            workflow_widget.controller.vec(960.0, 900.0),
            workflow_widget.controller.vec(1000.0, 900.0),
            workflow_widget.controller.vec(1000.0, 925.0),
            workflow_widget.controller.vec(960.0, 925.0),
        ),
        closed=True,
        id="ui-smoke-pocket-region",
    ),
    metadata={
        "import_role": "pocket_region",
        "pocket_depth_mm": 5.0,
        "source_shape_component_id": "ui-smoke-pocket-owner",
    },
)
workflow_widget.controller.execute(ui.AddEntitiesCommand((pocket_entity,)))
workflow_widget.controller.selection.select_only(pocket_entity.id)
pocket_geometry = dialog._vector_editor_geometry_for_cam("pocket")
assert len(pocket_geometry["contours"]) == 1, pocket_geometry

# Um círculo pequeno selecionado é furo nas demais operações, mas a própria
# área circular fechada quando o operador pede Preenchimento.
pocket_circle_id = workflow_widget.controller.add_circle(
    workflow_widget.controller.vec(920.0, 915.0),
    5.0,
)
workflow_widget.controller.selection.select_only(pocket_circle_id)
circle_as_hole = dialog._vector_editor_geometry_for_cam("holes")
circle_as_pocket = dialog._vector_editor_geometry_for_cam("pocket")
assert len(circle_as_hole["holes"]) == 1, circle_as_hole
assert not circle_as_hole["contours"], circle_as_hole
assert not circle_as_pocket["holes"], circle_as_pocket
assert len(circle_as_pocket["contours"]) == 1, circle_as_pocket
workflow_widget.controller.selection.select_only(pocket_entity.id)
dialog._vector_editor_configure_toolpath("pocket")
assert float(dialog.operation_fields["pocket"]["cut_depth"].text()) == 5.0

dialog._vector_editor_sync_timer.stop()
set_language("en")
app.processEvents()
assert dialog.cam_advisor_button.text().startswith("CAM assistant")
assert dialog.cut_depth_strategy_combo.itemText(
    dialog.cut_depth_strategy_combo.findData("piece_bidirectional")
).startswith("All passes directly")
assert dialog.cut_depth_strategy_combo.itemText(
    dialog.cut_depth_strategy_combo.findData("hybrid_piece_bidirectional")
).startswith("Final pass at the end")
assert workflow_widget.properties_panel.apply_scale_button.text() == "Apply scale"
advisor_settings = dialog._collect_settings()
advisor_report = dialog._update_cam_advisor_summary(advisor_settings)
assert advisor_report is not None
assert dialog.cam_advisor_button.toolTip()
assert "parameter" in dialog.cam_advisor_button.toolTip().lower()
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
    assert "visible tab" in advisor_messages[-1][1]
    # Uma aba CAM explícita identifica operação e fresa no cabeçalho.
    dialog.operation_tabs.setCurrentIndex(dialog._tab_index("Acabamento 3D"))
    dialog.show_cam_advisor()
    assert "ANALYZING NOW" in advisor_messages[-1][1]
    assert "3D Finishing" in advisor_messages[-1][1]
    assert "[SUGGESTION]" in advisor_messages[-1][1]
    assert "SUGESTÃO" not in advisor_messages[-1][1]
    assert "Ø" in advisor_messages[-1][1]
finally:
    set_language("pt")
    app.processEvents()
    QtWidgets.QMessageBox.critical = original_critical
    QtWidgets.QMessageBox.warning = original_warning
    QtWidgets.QMessageBox.information = original_information
dialog.hide()
app.processEvents()
FreeCAD.closeDocument(workflow_doc.Name)
FreeCAD.closeDocument(second.Name)
FreeCAD.closeDocument(first.Name)
print("WoodCAM Editor 2D UI smoke: OK")
