"""Active sheet overflow: reuse empty sheets, skip occupied ones, create/Undo."""
import sys
import time
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import FreeCAD
from PySide6 import QtTest, QtWidgets
import ui

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
doc = FreeCAD.newDocument('SheetOverflowSmoke')
dialog = ui.WoodCAM2DDialog()
widget = dialog.vector_editor_widget
bounds = [[0., 0., 200., 200.], [250., 0., 450., 200.], [500., 0., 700., 200.]]
widget.controller.execute(ui.SetWorkAreaCommand(ui.WorkArea(*bounds[0])))
widget.controller.execute(ui.SetDocumentMetadataCommand('organization_sheet_bounds', bounds))
layer = widget.document.active_layer_id

def rect(x, y, width, height):
    return ui.PathEntity.from_points(layer, tuple(ui.Vec2(x + dx, y + dy) for dx, dy in
        ((0, 0), (width, 0), (width, height), (0, height))), closed=True)

parts = [rect(5, 5, 130, 130), rect(140, 5, 130, 130), rect(5, 140, 130, 130)]
stationary = rect(350, 50, 40, 40)
hole = ui.CircleEntity(layer_id=layer, center=ui.Vec2(255, 50), radius=3)
remnant = ui.PathEntity.from_points(layer, (ui.Vec2(250, 150), ui.Vec2(450, 150)),
    metadata={'woodcam_role': 'remnant_cut', 'sheet_index': 1,
              'cut_start': [250, 150], 'cut_end': [450, 150]})
widget.controller.execute(ui.AddEntitiesCommand((*parts, stationary, hole, remnant)))
widget.controller.selection.clear()
widget.sheet_panel.list.setCurrentRow(0)
app.processEvents()
before = dict(widget.document.entities_by_id)
revision = widget.document.revision

def search():
    dialog._vector_editor_organize_pieces(search_mode='fast', spacing=4, time_budget_seconds=2)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and dialog._vector_editor_nesting_session is not None:
        app.processEvents()
        QtTest.QTest.qWait(10)
    assert widget.workflow_preview_bar.is_active, dialog.operation_hint.text()

search()
assert widget.document.revision == revision
assert widget.document.metadata['organization_sheet_bounds'] == bounds
widget.workflow_preview_bar.cancel_button.click()
assert dict(widget.document.entities_by_id) == before
search()
widget.workflow_preview_bar.apply_button.click()
app.processEvents()
after_bounds = widget.document.metadata['organization_sheet_bounds']
assert len(after_bounds) == 4, after_bounds
assert after_bounds[:3] == bounds
assert after_bounds[3] == [750., 0., 950., 200.], after_bounds
assert widget.document.get_entity(stationary.id) == stationary
assert widget.document.get_entity(remnant.id) == remnant
assigned = []
for part in parts:
    moved = widget.document.get_entity(part.id)
    matches = [i for i, sheet in enumerate(after_bounds) if dialog._entity_is_inside_sheet(moved, sheet)]
    assert len(matches) == 1, (part.id, matches, moved.bounds())
    assigned.extend(matches)
assert set(assigned) == {0, 2, 3}, assigned
assert sum(dialog._entity_is_inside_sheet(widget.document.get_entity(hole.id), sheet)
           for sheet in after_bounds) == 1
assert widget.document.get_entity(hole.id).center.distance_to(
    widget.document.get_entity(parts[1].id).spans[0].start) == hole.center.distance_to(parts[1].spans[0].start)
after = dict(widget.document.entities_by_id)
widget.controller.undo()
assert widget.document.metadata['organization_sheet_bounds'] == bounds
assert dict(widget.document.entities_by_id) == before
widget.controller.redo()
assert dict(widget.document.entities_by_id) == after
assert widget.document.metadata['organization_sheet_bounds'] == after_bounds
snapshot = doc.getObject('WoodCAM2D_VectorDocument').GeometryJSON
with tempfile.TemporaryDirectory(prefix='woodcam-overflow-') as tmp:
    path = str(Path(tmp) / 'overflow.FCStd')
    doc.saveAs(path)
    dialog.close()
    FreeCAD.closeDocument(doc.Name)
    reopened = FreeCAD.openDocument(path)
    assert reopened.getObject('WoodCAM2D_VectorDocument').GeometryJSON == snapshot
    FreeCAD.closeDocument(reopened.Name)
print('SHEET_OVERFLOW_SMOKE_OK')
