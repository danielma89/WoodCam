"""The selected format controls both extension and actual exported content."""
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import FreeCAD
from PySide6 import QtWidgets
import ui
from woodcam_editor.presentation.i18n import set_language

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
doc = FreeCAD.newDocument('VectorExportFormatSmoke')
dialog = ui.WoodCAM2DDialog()
widget = dialog.vector_editor_widget
part = ui.PathEntity.from_points(widget.document.active_layer_id,
    (ui.Vec2(0,0),ui.Vec2(100,0),ui.Vec2(100,50),ui.Vec2(0,50)), closed=True)
widget.controller.execute(ui.AddEntitiesCommand((part,)))
widget.controller.selection.select_only(part.id)
before = dict(widget.document.entities_by_id)
revision = widget.document.revision
with tempfile.TemporaryDirectory(prefix='woodcam-export-format-') as tmp:
    root = Path(tmp)
    for name, selected, expected, marker in (
        ('drawing.svg', 'DXF (*.dxf)', 'drawing.dxf', 'SECTION'),
        ('reverse.dxf', 'SVG (*.svg)', 'reverse.svg', '<svg'),
        ('no-extension', 'DXF (*.dxf)', 'no-extension.dxf', 'SECTION'),
        ('UPPER.SVG', 'DXF (*.dxf)', 'UPPER.dxf', 'SECTION'),
    ):
        with patch.object(QtWidgets.QFileDialog, 'getSaveFileName', return_value=(str(root/name), selected)):
            dialog._vector_editor_export_file()
        target = root/expected
        assert target.exists(), (name, selected, expected)
        assert marker in target.read_text(), expected
        if name != expected:
            assert not (root/name).exists(), name
    # A corrected filename must never bypass confirmation on an existing file.
    target = root/'existing.dxf'
    target.write_text('PRESERVE')
    with patch.object(QtWidgets.QFileDialog, 'getSaveFileName', return_value=(str(root/'existing.svg'), 'DXF (*.dxf)')):
        with patch.object(QtWidgets.QMessageBox, 'question', return_value=QtWidgets.QMessageBox.No) as question:
            dialog._vector_editor_export_file()
            assert question.called
            set_language('en')
            try:
                dialog._vector_editor_export_file()
                assert question.call_args.args[1] == 'Replace file?'
                assert question.call_args.args[2] == 'The file %s already exists. Replace it?' % target
            finally:
                set_language('pt')
        assert target.read_text() == 'PRESERVE'
        with patch.object(QtWidgets.QMessageBox, 'question', return_value=QtWidgets.QMessageBox.Yes):
            dialog._vector_editor_export_file()
        assert 'SECTION' in target.read_text()
    with patch.object(QtWidgets.QFileDialog, 'getSaveFileName', return_value=('', 'DXF (*.dxf)')):
        dialog._vector_editor_export_file()
assert widget.document.revision == revision
assert dict(widget.document.entities_by_id) == before
dialog.close()
FreeCAD.closeDocument(doc.Name)
print('VECTOR_EXPORT_FORMAT_SMOKE_OK')
