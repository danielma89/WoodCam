"""FreeCAD/OCC smoke for exact Editor 2D closed-vector booleans."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from woodcam_editor.adapters.freecad_boolean import preview_boolean
from woodcam_editor.domain import (
    ApplyBooleanPreviewCommand,
    CircleEntity,
    PathEntity,
    Vec2,
    VectorDocument,
)


def rectangle(layer_id, x0, y0, x1, y1):
    return PathEntity.from_points(
        layer_id,
        (Vec2(x0, y0), Vec2(x1, y0), Vec2(x1, y1), Vec2(x0, y1)),
        closed=True,
    )


document = VectorDocument.create_default()
layer = document.active_layer_id
outer = rectangle(layer, 0, 0, 100, 50)
hole = CircleEntity(layer, Vec2(50, 25), 8)
document.add_entities((outer, hole))

# The hole may be selected before the panel.  The adapter recognizes the
# containing outer contour and produces the same exact compound result.
preview = preview_boolean("difference", (hole, outer))
assert len(preview.result_entities) == 2, preview
assert preview.metadata["inferred_difference_base"] is True
assert any(type(item).__name__ == "CircleEntity" for item in preview.result_entities)
command = ApplyBooleanPreviewCommand(preview)
command.apply(document)
assert len(document.entities_by_id) == 3
assert outer.id not in document.entities_by_id
assert hole.id not in document.entities_by_id
group = document.get_entity(command.group_id)
assert set(group.child_ids) == {entity.id for entity in preview.result_entities}

left = rectangle(layer, 0, 0, 30, 30)
right = rectangle(layer, 20, 0, 50, 30)
union = preview_boolean("union", (left, right))
assert len(union.result_entities) == 1, union
assert union.result_entities[0].bounds().width == 50.0

intersection = preview_boolean("intersection", (left, right))
assert len(intersection.result_entities) == 1, intersection
assert intersection.result_entities[0].bounds().width == 10.0

# Aspire's Overlap Vectors clips *each* earlier selected vector against the
# last-selected vector. It is not an N-way intersection: two circles can both
# survive independently inside one clipping rectangle.
first_circle = CircleEntity(layer, Vec2(10, 15), 12)
second_circle = CircleEntity(layer, Vec2(30, 15), 12)
clipper = rectangle(layer, 5, 0, 35, 30)
overlap = preview_boolean("overlap", (first_circle, second_circle, clipper))
assert len(overlap.result_entities) == 2, overlap
assert overlap.metadata["overlap_clipper_id"] == clipper.id
assert all(entity.bounds().min_x >= 5.0 - 1.0e-6 for entity in overlap.result_entities)
assert all(entity.bounds().max_x <= 35.0 + 1.0e-6 for entity in overlap.result_entities)

print("editor boolean smoke: OK")
