"""The progressive UI compares contour packing before deep rectangular trials."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from PySide6 import QtWidgets
import ui
from woodcam_editor.domain import VectorDocument, PathEntity, Vec2, AddEntitiesCommand
from woodcam_editor.application.piece_organizer import classify_document_pieces, validate_organization_result_geometry

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
document = VectorDocument()
entities = tuple(PathEntity.from_points(document.active_layer_id, (
    Vec2(dx, 0), Vec2(dx+60, 0), Vec2(dx, 60),
), closed=True) for dx in (0, 100))
AddEntitiesCommand(entities).apply(document)
pieces = classify_document_pieces(document).pieces
worker = ui._OrganizationSearchWorker(
    pieces, (0, 0, 69, 69), 2,
    {piece.piece_id: (0, 90, 180, 270) for piece in pieces},
    'balanced', 10, 'contour-test',
)
results = []
errors = []
worker.resultReady.connect(lambda token, result, label, elapsed, count: results.append((label, result)))
worker.failed.connect(lambda token, error: errors.append(error))
worker.run()
assert not errors, errors
assert len(results[0][1].sheet_bounds) == 2
contour = next(result for label, result in results
               if label in ('Contornos alinhados', 'Encaixe por contorno')
               and len(result.sheet_bounds) == 1)
assert len(contour.sheet_bounds) == 1
assert len(contour.placements) == 2
assert not validate_organization_result_geometry(pieces, contour, minimum_clearance=2)
assert document.revision == 1
print('CONTOUR_SEARCH_SMOKE_OK')
