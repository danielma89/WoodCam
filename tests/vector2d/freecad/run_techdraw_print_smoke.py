"""FreeCADCmd smoke for the non-destructive Editor 2D -> TechDraw bridge."""

from __future__ import annotations

import os
import sys
import tempfile

import FreeCAD

sys.path.insert(
    0,
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")),
)

from woodcam_editor.adapters.techdraw_print import create_techdraw_print_pages
from woodcam_editor.domain.document import VectorDocument, WorkArea
from woodcam_editor.domain.entities import CircleEntity, PathEntity
from woodcam_editor.domain.primitives import Vec2


host = FreeCAD.newDocument("WoodCAMTechDrawPrintSmoke")
host.UndoMode = 1
vector = VectorDocument.create_default(WorkArea(0, 0, 600, 400))
layer_id = vector.active_layer_id
outer = PathEntity.from_points(
    layer_id,
    (Vec2(10, 10), Vec2(190, 10), Vec2(190, 100), Vec2(10, 100)),
    closed=True,
)
hole = CircleEntity(layer_id, Vec2(60, 55), 8)
second = PathEntity.from_points(
    layer_id,
    (Vec2(320, 20), Vec2(540, 20), Vec2(540, 180), Vec2(320, 180)),
    closed=True,
)
vector.add_entities((outer, hole, second))
revision_before = vector.revision
ids_before = tuple(sorted(vector.entities_by_id))

result = create_techdraw_print_pages(
    host,
    vector,
    ((0, 0, 250, 200), (300, 0, 600, 200)),
    sheet_indices=(0, 1),
    paper_name="A3",
    scale_mode="fit",
    toolpath_components={"cut": (((10, 10), (190, 10)),)},
)

assert len(result.pages) == 2
assert len(result.symbols) == 2
assert vector.revision == revision_before
assert tuple(sorted(vector.entities_by_id)) == ids_before
assert not host.findObjects("Part::Feature")
for index, (page, symbol) in enumerate(zip(result.pages, result.symbols)):
    assert page.TypeId == "TechDraw::DrawPage"
    assert symbol.TypeId == "TechDraw::DrawViewSymbol"
    assert symbol in tuple(page.Views)
    assert "woodcam-sheet-boundary" in symbol.Symbol
    assert "woodcam-sheet-content" in symbol.Symbol
    assert page.WoodCAMManagedType == "techdraw_print_page"
    assert int(page.WoodCAMSheetIndex) == index

assert outer.id in result.symbols[0].Symbol
assert hole.id in result.symbols[0].Symbol
assert second.id not in result.symbols[0].Symbol
assert second.id in result.symbols[1].Symbol

target = tempfile.mktemp(prefix="woodcam-techdraw-print-", suffix=".FCStd")
host.recompute()
host.saveAs(target)
FreeCAD.closeDocument(host.Name)
reopened = FreeCAD.openDocument(target)
assert len(reopened.findObjects("TechDraw::DrawPage")) == 2
assert len(reopened.findObjects("TechDraw::DrawViewSymbol")) == 2
assert not reopened.findObjects("Part::Feature")
FreeCAD.closeDocument(reopened.Name)
os.unlink(target)
print("Editor 2D TechDraw print smoke: OK")
