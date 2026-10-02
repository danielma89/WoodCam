"""Regression: organizing sheet 3 preserves sheets 1/2 and their remnants."""
import sys
import time
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import FreeCAD
from PySide6 import QtTest, QtWidgets
import ui

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
doc = FreeCAD.newDocument("ActiveSheetOrganizerSmoke")
dialog = ui.WoodCAM2DDialog()
widget = dialog.vector_editor_widget
bounds = [[0., 0., 200., 200.], [250., 0., 450., 200.], [500., 0., 700., 200.]]
widget.controller.execute(ui.SetWorkAreaCommand(ui.WorkArea(*bounds[0])))
widget.controller.execute(ui.SetDocumentMetadataCommand("organization_sheet_bounds", bounds))
layer = widget.document.active_layer_id
parts = [ui.PathEntity.from_points(layer, tuple(ui.Vec2(x + dx, y) for x, y in
         ((40, 40), (80, 40), (80, 80), (40, 80))), closed=True)
         for dx in (0, 250, 500)]
hole = ui.CircleEntity(layer_id=layer, center=ui.Vec2(560, 60), radius=3)
remnant = ui.PathEntity.from_points(layer, (ui.Vec2(0, 150), ui.Vec2(200, 150)),
    metadata={"woodcam_role": "remnant_cut", "sheet_index": 0,
              "cut_start": [0, 150], "cut_end": [200, 150]})
widget.controller.execute(ui.AddEntitiesCommand((*parts, hole, remnant)))
widget.controller.selection.clear()
widget.sheet_panel.list.setCurrentRow(2)
app.processEvents()
assert widget.active_sheet_index == 2
before = dict(widget.document.entities_by_id)
revision = widget.document.revision
dialog._vector_editor_organize_pieces(search_mode="fast", spacing=4, time_budget_seconds=2)
deadline = time.monotonic() + 20
while time.monotonic() < deadline and dialog._vector_editor_nesting_session is not None:
    app.processEvents()
    QtTest.QTest.qWait(10)
assert widget.workflow_preview_bar.is_active
assert widget.document.revision == revision
widget.workflow_preview_bar.apply_button.click()
app.processEvents()
assert widget.document.get_entity(parts[0].id) == before[parts[0].id], "sheet 1 moved"
assert widget.document.get_entity(parts[1].id) == before[parts[1].id], "sheet 2 moved"
assert widget.document.get_entity(remnant.id) == remnant, "sheet 1 remnant removed"
assert widget.document.metadata["organization_sheet_bounds"] == bounds
after = widget.document.get_entity(parts[2].id)
assert after != parts[2]
assert after.bounds().min_x >= 500, "sheet 3 sent to sheet 1"
delta = after.bounds().min_x - parts[2].bounds().min_x
assert abs(widget.document.get_entity(hole.id).center.x - hole.center.x - delta) < 1e-7
assert widget.active_sheet_index == 2
widget.controller.undo()
assert dict(widget.document.entities_by_id) == before
widget.controller.redo()
assert widget.document.get_entity(parts[2].id) == after
# A hole selection still organizes its whole piece; cancel stays pure.
widget.controller.selection.select_only(hole.id)
widget.controller.execute(ui.MoveEntitiesCommand((parts[2].id, hole.id), ui.Vec2(30, 30)))
cancel_before = dict(widget.document.entities_by_id)
dialog._vector_editor_organize_pieces(search_mode="fast", spacing=4, time_budget_seconds=2)
deadline = time.monotonic() + 20
while time.monotonic() < deadline and dialog._vector_editor_nesting_session is not None:
    app.processEvents()
    QtTest.QTest.qWait(10)
assert widget.workflow_preview_bar.is_active
widget.setParent(None)
widget.resize(1100, 750)
widget.show()
widget._fit_sheet_bounds(bounds[2])
app.processEvents()
widget.grab().save("/tmp/woodcam-active-sheet-preview.png")
widget.workflow_preview_bar.cancel_button.click()
assert dict(widget.document.entities_by_id) == cancel_before
snapshot = doc.getObject("WoodCAM2D_VectorDocument").GeometryJSON
with tempfile.TemporaryDirectory(prefix="woodcam-active-sheet-") as temporary:
    filename = str(Path(temporary) / "sheets.FCStd")
    doc.saveAs(filename)
    widget.close()
    dialog.close()
    FreeCAD.closeDocument(doc.Name)
    reopened = FreeCAD.openDocument(filename)
    assert reopened.getObject("WoodCAM2D_VectorDocument").GeometryJSON == snapshot
    FreeCAD.closeDocument(reopened.Name)
print("ACTIVE_SHEET_ORGANIZER_SMOKE_OK")
