"""FreeCADCmd smoke for the final common-line pass and screw counterbore UI."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import FreeCAD  # noqa: E402
from PySide6 import QtWidgets  # noqa: E402

import ui  # noqa: E402
from gcode_writer import build_gcode  # noqa: E402
from operations import (  # noqa: E402
    build_global_cut_plan_moves,
    build_machining_stages,
    generate_depth_steps,
)
from validators import validate_settings  # noqa: E402
from woodcam_editor.application.common_line import CommonLineContour  # noqa: E402
from woodcam_editor.application.global_cut_plan import (  # noqa: E402
    OwnedContour,
    build_global_cut_plan,
)
from woodcam_editor.presentation.i18n import set_language  # noqa: E402


app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
set_language("pt")
document = FreeCAD.newDocument("WoodCAMCommonLineCounterboreSmoke")
dialog = ui.WoodCAM2DDialog()
# Preferências de usinagem sobrevivem entre sessões; este roteiro verifica o
# fluxo padrão por peça e não pode depender da última escolha do operador.
dialog.cut_tab_release_supervision_combo.setCurrentIndex(
    dialog.cut_tab_release_supervision_combo.findData("per_piece")
)
dialog.show()
app.processEvents()

# Início e final são cotas absolutas em todas as operações 2D. O contador
# deve considerar somente o material restante, não somar a profundidade final
# novamente depois da cota inicial.
saved_depth_fields = {}
for operation_mode in ("cut", "holes", "pocket"):
    fields = dialog.operation_fields[operation_mode]
    saved_depth_fields[operation_mode] = {
        name: fields[name].text()
        for name in ("start_depth", "cut_depth", "stepdown")
    }
    fields["start_depth"].setText("6.3")
    fields["cut_depth"].setText("7")
    fields["stepdown"].setText("0.4")
    dialog._update_passes_label(operation_mode)
    assert dialog.operation_pass_labels[operation_mode].text() == "2 passagens"
    dialog.operation_tabs.setCurrentIndex(
        dialog._operation_tab_index(operation_mode)
    )
    settings = dialog._collect_settings()
    assert settings["start_depth"] == 6.3
    assert settings["cut_depth"] == 7.0
    assert settings["final_depth"] == 7.0
    assert settings["depth_input_mode"] == "absolute_final"
    assert generate_depth_steps(
        settings["final_depth"],
        settings["stepdown"],
        start_depth=settings["start_depth"],
    ) == [6.7, 7.0]

for operation_mode, values in saved_depth_fields.items():
    for name, value in values.items():
        dialog.operation_fields[operation_mode][name].setText(value)

# A liberação é uma caixa visível; o combo antigo fica somente como ponte para
# SettingsJSON gravado antes desta mudança.
assert not dialog.cut_tab_release_checkbox.isHidden()
assert dialog.cut_tab_release_combo.isHidden()
dialog.cut_tabs_enabled.setChecked(True)
dialog.cut_common_line_enabled.setChecked(False)
assert dialog.cut_tab_release_checkbox.isEnabled()
dialog.cut_tab_release_checkbox.click()
assert dialog.cut_common_line_enabled.isChecked()
assert dialog.cut_tab_release_supervision_combo.isEnabled()
assert dialog.cut_tab_release_combo.currentData() == "automatic_release"
dialog.cut_common_line_enabled.setChecked(False)
assert not dialog.cut_tab_release_checkbox.isChecked()
assert dialog.cut_loose_waste_fixation_combo.currentData() == "disabled"
waste_tabs_index = dialog.cut_loose_waste_fixation_combo.findData("tabs")
dialog.cut_loose_waste_fixation_combo.setCurrentIndex(waste_tabs_index)
dialog.cut_loose_waste_fixation_combo.activated.emit(waste_tabs_index)
assert dialog.cut_common_line_enabled.isChecked()
dialog.cut_loose_waste_fixation_combo.setCurrentIndex(
    dialog.cut_loose_waste_fixation_combo.findData("disabled")
)
dialog.cut_tab_release_checkbox.click()
dialog.operation_tabs.setCurrentIndex(dialog._tab_index("Corte"))
cut_settings = dialog._collect_settings()
assert cut_settings["tab_release_mode"] == "automatic_release"
assert dialog.cut_tab_release_supervision_combo.currentData() == "per_piece"
# O smoke precisa ser independente da última espessura salva pelo operador.
# A tab integral de 15 mm testada abaixo pertence deliberadamente a MDF 15 mm.
dialog.fields["material_thickness"].setText("15")
dialog.operation_fields["cut"]["tab_thickness"].setText("15")
dialog.operation_fields["cut"]["start_depth"].setText("6")
screw_index = dialog.cut_loose_waste_fixation_combo.findData("screws")
dialog.cut_loose_waste_fixation_combo.setCurrentIndex(screw_index)
assert dialog.operation_fields["cut"]["screw_head_diameter"].isEnabled()
cut_settings = dialog._collect_settings()
assert cut_settings["tab_thickness"] == 15.0
assert cut_settings["start_depth"] == 6.0
assert cut_settings["loose_waste_fixation"] == "screws"
validate_settings(cut_settings)

# Profundidade inicial Z continua sendo uma cota vertical. O assento da cabeça
# recebe diâmetro e profundidade próprios na aba Furo.
dialog.operation_tabs.setCurrentIndex(dialog._tab_index("Furo"))
dialog.hole_counterbore_enabled.setChecked(True)
dialog.operation_fields["holes"]["hole_counterbore_diameter"].setText("10")
dialog.operation_fields["holes"]["hole_counterbore_depth"].setText("3")
hole_settings = dialog._collect_settings()
assert hole_settings["hole_counterbore_enabled"]
assert hole_settings["hole_counterbore_diameter"] == 10.0
assert hole_settings["hole_counterbore_depth"] == 3.0
assert "Não é diâmetro" in dialog.operation_fields["holes"]["start_depth"].toolTip()

# Os controles novos pertencem ao tradutor global da bancada, sem trocar IDs
# nem os valores já coletados da operação.
set_language("en")
app.processEvents()
assert dialog.cut_tab_release_checkbox.text() == "Remove tabs automatically at end"
assert dialog.cut_tab_release_supervision_combo.currentText() == "Pause before each part"
assert dialog.cut_loose_waste_fixation_combo.currentText() == "Loose waste: screws"
assert any(
    label.text() == "Untouched MDF height (mm)"
    for label in dialog.findChildren(QtWidgets.QLabel)
)
assert dialog.hole_counterbore_enabled.text() == "Create a larger seat at the hole entrance"
assert "It is not a diameter" in dialog.operation_fields["holes"]["start_depth"].toolTip()
set_language("pt")
app.processEvents()

stages = build_machining_stages(
    [],
    [{"x": 20.0, "y": 30.0, "diameter_mm": 5.0, "depth_mm": 12.0}],
    15.5,
    3.0,
    0.0,
    8.0,
    tool_diameter=4.0,
    material_thickness=15.0,
    drill_holes=True,
    cut_enabled=False,
    counterbore_enabled=True,
    counterbore_diameter=10.0,
    counterbore_depth=3.0,
    tool_type="end_mill",
)
hole_moves = stages["holes"]
seat_moves = [move for move in hole_moves if move.get("counterbore")]
assert seat_moves
assert min(
    move["z"]
    for move in seat_moves
    if move["type"] in {"feed_plunge", "feed_helix", "feed_cut"}
) == -3.0


def rectangle(owner, min_x, min_y, max_x, max_y):
    return CommonLineContour(
        owner,
        ((min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y)),
    )


plan = build_global_cut_plan(
    (
        rectangle("A", 0.0, 0.0, 80.0, 40.0),
        rectangle("B", 80.0, 0.0, 160.0, 40.0),
        rectangle("C", 160.0, 0.0, 240.0, 40.0),
    ),
    depths=(5.0, 10.0, 15.5),
    through_depth=15.0,
    strategy="hybrid_piece_bidirectional",
)
assert all(
    operation.routing_mode == "piece_bidirectional"
    for operation in plan.operations
    if operation.depth < 15.5
)
final_operations = [
    operation for operation in plan.operations if operation.depth == 15.5
]
assert final_operations
assert all(
    operation.routing_mode == "final_sheet_pass"
    for operation in final_operations
)
final_segment_ids = [
    segment_id
    for operation in final_operations
    for segment_id in operation.segment_ids
]
assert len(final_segment_ids) == len(set(final_segment_ids)) == len(plan.segments)

moves = build_global_cut_plan_moves(
    plan,
    8.0,
    stay_down_max_distance=9.0,
)
assert not any(
    move.get("cleared_path_link")
    and move.get("depth_pass") == move.get("link_target_depth_pass") == 15.5
    for move in moves
)
assert all(
    move.get("cleared_path_link_length", 0.0) <= 9.0
    for move in moves
    if move.get("cleared_path_link")
    and move.get("link_target_depth_pass") == 15.5
)
assert any(
    move["type"] == "rapid"
    and move.get("x") is not None
    and move.get("routing_mode") == "final_sheet_pass"
    for move in moves
)

# Altura de 15 mm numa chapa de 15 mm mantém a fresa acima do MDF em todas as
# passadas, mesmo com 0,5 mm de sobrecorte no spoilboard.
full_height_plan = build_global_cut_plan(
    (rectangle("full", 0.0, 0.0, 100.0, 60.0),),
    depths=(5.0, 10.0, 15.5),
    tabs_enabled=True,
    tab_count=4,
    tab_width=8.0,
    tab_thickness=15.0,
    material_thickness=15.0,
    tool_diameter=6.0,
)
full_height_moves = build_global_cut_plan_moves(
    full_height_plan,
    8.0,
    material_thickness=15.0,
)
assert any(move.get("tab") for move in full_height_moves)
assert all(
    move["z"] >= 0.2
    for move in full_height_moves
    if move.get("tab")
)

# A prévia mostra cada ponte física uma vez. As linhas laranjas do percurso
# continuam representando entradas/subidas em cada passe e não são usadas
# como contador visual de tabs.
physical_tab_group = document.addObject(
    "App::DocumentObjectGroup",
    "PhysicalTabPreviewSmoke",
)
physical_tab_settings = dict(cut_settings)
physical_tab_settings["_global_cut_plan_summary"] = {
    "tabs": [
        {
            "id": tab.tab_id,
            "kind": tab.kind.value,
            "owner_ids": tuple(tab.owner_ids),
            "x1": tab.start.x,
            "y1": tab.start.y,
            "x2": tab.end.x,
            "y2": tab.end.y,
            "width": tab.width,
            "height": tab.thickness,
        }
        for tab in full_height_plan.tabs
    ]
}
set_language("en")
physical_markers = dialog._add_physical_tabs_preview(
    document,
    physical_tab_group,
    physical_tab_settings,
)
assert len(physical_markers) == 1
assert physical_markers[0].Label == "Physical StockTabs — %d" % len(
    full_height_plan.tabs
)
assert len(physical_markers[0].Shape.Edges) == len(full_height_plan.tabs)
set_language("pt")

# Todos os pilotos precedem um único M0; só depois começa qualquer contorno.
screw_plan = build_global_cut_plan(
    (rectangle("screw-part", 0.0, 0.0, 100.0, 100.0),),
    internal_contours=(
        OwnedContour(
            "loose-cutout",
            "screw-part",
            ((30.0, 30.0), (70.0, 30.0), (70.0, 70.0), (30.0, 70.0)),
        ),
    ),
    depths=(5.0, 10.0, 15.5),
    loose_waste_fixation="screws",
    material_thickness=15.0,
    tool_diameter=6.0,
    screw_head_diameter=8.0,
    screw_safety_margin=2.0,
)
screw_moves = build_global_cut_plan_moves(screw_plan, 8.0)
pause_indexes = [
    index for index, move in enumerate(screw_moves)
    if move["type"] == "operator_pause"
]
assert len(pause_indexes) == 1
first_main_cut = next(
    index for index, move in enumerate(screw_moves)
    if move.get("cut_phase") in {"internal", "shared", "external"}
)
assert pause_indexes[0] < first_main_cut
gcode = build_gcode(18000, 500, 1800, 4000, 8.0, 15.0, screw_moves)
m0_index = gcode.index("M0")
assert gcode[m0_index - 2] == "M5"
assert gcode[m0_index + 1] == "M3 S18000"

# O keep-out persiste XY de máquina e soma o raio da ferramenta posterior.
keepout_settings = {
    "safe_height": 8.0,
    "tool_diameter": 6.0,
    "_global_cut_plan_summary": {
        "screw_anchors": [
            {
                "id": "screw-check",
                "x": 5.0,
                "y": 5.0,
                "keepout_radius": 6.0,
                "head_height": 3.0,
            }
        ]
    },
}
dialog._synchronise_screw_anchor_machine_positions(
    keepout_settings,
    [
        {
            "type": "rapid",
            "x": 15.0,
            "y": 5.0,
            "z": 8.0,
            "cut_phase": "screw_pilot",
            "screw_anchor_id": "screw-check",
        }
    ],
)
anchor_summary = keepout_settings["_global_cut_plan_summary"]["screw_anchors"][0]
assert (anchor_summary["machine_x"], anchor_summary["machine_y"]) == (15.0, 5.0)
try:
    dialog._validate_screw_keepouts(
        keepout_settings,
        [
            {"type": "rapid", "x": 0.0, "y": 5.0, "z": 8.0},
            {"type": "feed_cut", "x": 30.0, "y": 5.0, "z": -3.0},
        ],
    )
except ValueError as error:
    assert "região proibida" in str(error)
else:
    raise AssertionError("trajetória posterior atravessou o keep-out do parafuso")

# Uma operação antiga guardava ``cut_depth`` como incremento. Ao editar, a
# tela absoluta usa o ``final_depth`` persistido e conserva a usinagem antiga.
dialog.operation_tabs.setCurrentIndex(dialog._operation_tab_index("cut"))
legacy_settings = dialog._collect_settings()
legacy_settings.update(
    operation_mode="cut",
    start_depth=6.0,
    cut_depth=7.0,
    final_depth=13.0,
)
legacy_settings.pop("depth_input_mode", None)
legacy_operation = SimpleNamespace(
    Name="LegacyRelativeDepth",
    Label="Profundidade antiga",
    SettingsJSON=json.dumps(legacy_settings),
)
dialog._load_selected_operation_for_editing(legacy_operation)
assert float(dialog.operation_fields["cut"]["cut_depth"].text()) == 13.0

dialog.close()
FreeCAD.closeDocument(document.Name)
print("Common-line final pass and counterbore smoke: OK")
