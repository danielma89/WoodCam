"""Regressao: fonte 3D deslocada conserva XY e parte do datum da area."""

from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import FreeCAD  # noqa: E402
import Mesh  # noqa: E402
import Part  # noqa: E402
from pivy import coin  # noqa: E402
from PySide6 import QtWidgets  # noqa: E402

import ui  # noqa: E402


app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
document = FreeCAD.newDocument("WoodCAM3DXYSmoke")
dialog = ui.WoodCAM2DDialog()

dialog.fields["job_width"].setText("321")
dialog.fields["job_height"].setText("654")
dialog.fields["job_origin_x"].setText("40")
dialog.fields["job_origin_y"].setText("25")

source = document.addObject("Mesh::Feature", "ReliefOffset")
source.Mesh = Mesh.Mesh(
    [
        [
            FreeCAD.Vector(140.0, 140.0, 0.0),
            FreeCAD.Vector(160.0, 140.0, 0.0),
            FreeCAD.Vector(140.0, 160.0, 2.0),
        ],
        [
            FreeCAD.Vector(160.0, 140.0, 0.0),
            FreeCAD.Vector(160.0, 160.0, 2.0),
            FreeCAD.Vector(140.0, 160.0, 2.0),
        ],
    ]
)
document.recompute()
dialog.operation_tabs.setCurrentIndex(dialog._tab_index("Acabamento 3D"))
settings = dialog._collect_settings()
settings.update(
    boundary_mode="model",
    material_thickness=15.0,
    model_position_mode="gap_above",
    model_gap_above=0.0,
    safe_height=5.0,
    tool_type="ball_nose",
    tool_diameter=6.0,
    finish3d_stepover_percent=50.0,
    return_to_start=False,
)
original_selected_3d_source = dialog._selected_3d_source
try:
    dialog._selected_3d_source = lambda: source
    moves = dialog._build_moves_from_selection(settings)
finally:
    dialog._selected_3d_source = original_selected_3d_source

assert settings["_xy_origin_offset"] == (0.0, 0.0), settings["_xy_origin_offset"]
assert settings["_3d_coordinates_absolute"] is True
components = dialog._toolpath_components(
    ui.moves_for_preview(settings, moves),
    max_display_segments=None,
)
assert components["rapid"], "O datum nao foi ligado ao primeiro ponto 3D."
assert components["rapid"][0][0][:2] == (40.0, 25.0), components["rapid"][0]
assert components["origin"] == [components["rapid"][0]]

# A regressao visual estava depois do calculo acima: a conversao de exibicao
# removia Z e o SoLineSet ignorava todos os pontos. Verifique o buffer que vai
# efetivamente ao scene graph, inclusive com deslocamento XY nulo.
display_components = dialog._shift_toolpath_components_xy(components, 0.0, 0.0)
assert len(display_components["rapid"][0][0]) == 3
assert display_components["origin"][0][0] == components["origin"][0][0]


class _FakeActiveView:
    def __init__(self):
        self.scene = coin.SoSeparator()

    def getSceneGraph(self):
        return self.scene


class _FakeGuiDocument:
    def __init__(self):
        self.ActiveView = _FakeActiveView()


fake_gui_document = _FakeGuiDocument()
overlay = ui.CoinToolpathOverlay(fake_gui_document)
overlay.update(display_components)
assert overlay.root is not None
assert fake_gui_document.ActiveView.scene.getNumChildren() == 1
# PickStyle + rapido + rampa/corte presentes + origem magenta destacada.
assert overlay.root.getNumChildren() >= 4
origin_branch = overlay.root.getChild(overlay.root.getNumChildren() - 1)
origin_coordinates = origin_branch.getChild(2)
assert origin_coordinates.point.getNum() == 2
assert tuple(origin_coordinates.point[0].getValue()) == components["origin"][0][0]
origin_style = origin_branch.getChild(1)
assert int(origin_style.linePattern.getValue()) == 0xF0F0
overlay.clear()

feed_xy = [
    (float(move["x"]), float(move["y"]))
    for move in moves
    if str(move.get("type", "")).startswith("feed_")
    and move.get("x") is not None
    and move.get("y") is not None
]
assert feed_xy, "O acabamento 3D nao produziu movimentos de corte."
assert min(point[0] for point in feed_xy) >= 140.0, min(feed_xy)
assert max(point[0] for point in feed_xy) <= 160.0, max(feed_xy)
assert min(point[1] for point in feed_xy) >= 140.0
assert max(point[1] for point in feed_xy) <= 160.0

# O caso visual mais comum do FreeCAD não grava os vértices no XY final:
# mantém a Shape local e posiciona o objeto com Placement. A regressão que
# motivou este smoke aparecia apenas nessa combinação.
placed_source = document.addObject("Part::Feature", "ReliefPlaced")
placed_source.Shape = Part.makeBox(20.0, 20.0, 2.0)
placed_source.Placement.Base = FreeCAD.Vector(240.0, 180.0, 0.0)
document.recompute()
placed_settings = dialog._collect_settings()
placed_settings.update(settings)
placed_settings.pop("_mesh_source_object", None)
placed_settings.pop("_mesh_source_hash", None)
original_selected_3d_source = dialog._selected_3d_source
try:
    dialog._selected_3d_source = lambda: placed_source
    placed_moves = dialog._build_moves_from_selection(placed_settings)
finally:
    dialog._selected_3d_source = original_selected_3d_source

placed_feed_xy = [
    (float(move["x"]), float(move["y"]))
    for move in placed_moves
    if str(move.get("type", "")).startswith("feed_")
    and move.get("x") is not None
    and move.get("y") is not None
]
assert placed_feed_xy
assert min(point[0] for point in placed_feed_xy) >= 240.0, min(placed_feed_xy)
assert max(point[0] for point in placed_feed_xy) <= 260.0, max(placed_feed_xy)
assert min(point[1] for point in placed_feed_xy) >= 180.0
assert max(point[1] for point in placed_feed_xy) <= 200.0

dialog.hide()
FreeCAD.closeDocument(document.Name)
print("WoodCAM 3D XY absolute smoke: OK")
