"""Work dimensions edit only the active sheet; edit/delete survive FCStd Undo."""
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import FreeCAD
from PySide6 import QtCore, QtWidgets
import ui

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
doc = FreeCAD.newDocument('SheetManagementSmoke')
dialog = ui.WoodCAM2DDialog()
widget = dialog.vector_editor_widget
panel = widget.sheet_panel
bounds = [[0.,0.,200.,200.], [250.,0.,450.,200.], [500.,0.,700.,200.]]
widget.controller.execute(ui.SetWorkAreaCommand(ui.WorkArea(*bounds[0])))
widget.controller.execute(ui.SetDocumentMetadataCommand('organization_sheet_bounds', bounds))
layer = widget.document.active_layer_id
part = ui.PathEntity.from_points(layer, (ui.Vec2(510,10),ui.Vec2(550,10),ui.Vec2(550,50),ui.Vec2(510,50)), closed=True)
hole = ui.CircleEntity(layer_id=layer, center=ui.Vec2(530,30), radius=3)
widget.controller.execute(ui.AddEntitiesCommand((part, hole)))
panel.list.setCurrentRow(1)
app.processEvents()
assert float(dialog.fields['job_width'].text()) == 200, (dialog.fields['job_width'].text(), widget.active_sheet_index, dialog._vector_editor_area_edit_pending)
before = dict(widget.document.entities_by_id)
dialog.fields['job_width'].setText('400')
dialog.fields['job_height'].setText('300')
dialog._commit_vector_editor_area_from_setup()
app.processEvents()
assert widget.document.metadata['organization_sheet_bounds'] == [[0.,0.,200.,200.],[250.,0.,650.,300.],[700.,0.,900.,200.]]
assert widget.document.get_entity(part.id).bounds().min_x == 710
assert widget.document.get_entity(hole.id).center == ui.Vec2(730,30)
assert widget.document.get_entity(part.id).bounds().width == 40
widget.controller.undo()
assert dict(widget.document.entities_by_id) == before
assert widget.document.metadata['organization_sheet_bounds'] == bounds
widget.controller.redo()
panel.list.setCurrentRow(0)
app.processEvents()
assert float(dialog.fields['job_width'].text()) == 200
panel.list.setCurrentRow(1)
assert float(dialog.fields['job_width'].text()) == 400

def edit():
    popup = app.activeModalWidget()
    popup.findChild(QtWidgets.QDoubleSpinBox, 'sheetWidth').setValue(350)
    popup.accept()
QtCore.QTimer.singleShot(0, edit)
panel.edit_button.click()
assert widget.document.metadata['organization_sheet_bounds'][1] == [250.,0.,600.,300.]
assert float(dialog.fields['job_width'].text()) == 350
assert widget.document.metadata['organization_sheet_bounds'][2] == [700.,0.,900.,200.]
# Occupied sheets cannot be deleted, including their holes.
panel.list.setCurrentRow(2)
revision = widget.document.revision
panel.delete_button.click()
assert widget.document.revision == revision
# Empty middle sheet deletion renumbers only; geometry does not move.
panel.list.setCurrentRow(1)
entities = dict(widget.document.entities_by_id)
panel.delete_button.click()
assert len(panel.document_bounds()) == 2
assert dict(widget.document.entities_by_id) == entities
widget.controller.undo()
assert len(panel.document_bounds()) == 3
widget.controller.redo()
assert len(panel.document_bounds()) == 2
snapshot = doc.getObject('WoodCAM2D_VectorDocument').GeometryJSON
with tempfile.TemporaryDirectory(prefix='woodcam-sheet-management-') as tmp:
    path = str(Path(tmp) / 'sheets.FCStd')
    doc.saveAs(path)
    dialog.close()
    FreeCAD.closeDocument(doc.Name)
    reopened = FreeCAD.openDocument(path)
    assert reopened.getObject('WoodCAM2D_VectorDocument').GeometryJSON == snapshot
    FreeCAD.closeDocument(reopened.Name)
print('SHEET_MANAGEMENT_SMOKE_OK')
