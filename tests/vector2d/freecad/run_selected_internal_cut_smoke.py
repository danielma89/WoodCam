"""FreeCADCmd regression for a selected internal slot plus circular holes."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import FreeCAD  # noqa: E402
from PySide6 import QtWidgets  # noqa: E402

import ui  # noqa: E402
from woodcam_editor.domain import (  # noqa: E402
    AddEntitiesCommand,
    CircleEntity,
    PathEntity,
    Vec2,
)

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
host = FreeCAD.newDocument("WoodCAMSelectedInternalCutSmoke")
dialog = ui.WoodCAM2DDialog()
dialog.show()
app.processEvents()
widget = dialog.vector_editor_widget
layer_id = widget.document.active_layer_id

outer = PathEntity.from_points(
    layer_id,
    (Vec2(20, 20), Vec2(260, 20), Vec2(260, 180), Vec2(20, 180)),
    closed=True,
)
slot = PathEntity.from_points(
    layer_id,
    (Vec2(70, 70), Vec2(100, 70), Vec2(100, 90), Vec2(70, 90)),
    closed=True,
)
holes = (
    CircleEntity(layer_id, Vec2(150, 80), 4.0),
    CircleEntity(layer_id, Vec2(190, 80), 4.0),
)
widget.controller.execute(AddEntitiesCommand((outer, slot, *holes)))
widget.controller.selection.replace((slot.id, *(hole.id for hole in holes)))
dialog._use_vector_editor_for_cam = True
widget.set_cam_source_active(True)
dialog.operation_tabs.setCurrentIndex(dialog._operation_tab_index("cut"))
dialog.operation_tool_combos["cut"].setCurrentText("Fresa 4 mm MDF")
dialog.operation_fields["cut"]["tool_diameter"].setText("6")
dialog.operation_fields["cut"]["cut_allowance_offset"].setText("0")
dialog.operation_combo.setCurrentIndex(0)
dialog.cut_common_line_enabled.setChecked(True)

settings = dialog._collect_settings()
assert settings["tool_diameter"] == 4.0
geometry = dialog._vector_editor_geometry_for_cam("cut")
assert len(geometry["contours"]) == 1
assert len(geometry["holes"]) == 2
stages = dialog._build_stage_moves_from_selection(settings)
assert stages.get("cut")
assert not settings.get("_global_cut_plan_summary")
assert widget.document.get_entity(outer.id) == outer

# A single circular hole inside a board also keeps the inside compensation;
# only a standalone circle may use the operator's external cut side.
widget.controller.selection.select_only(holes[0].id)
single_settings = dialog._collect_settings()
single_stages = dialog._build_stage_moves_from_selection(single_settings)
assert single_stages.get("cut")
assert not single_settings.get("_global_cut_plan_summary")

dialog.close()
FreeCAD.closeDocument(host.Name)
print("WoodCAM selected internal cut smoke: OK")
