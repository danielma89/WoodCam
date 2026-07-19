"""Controller scoping for classified automatic corner reliefs."""

from __future__ import annotations

import unittest

from woodcam_editor.application import EditorController
from woodcam_editor.domain import PathEntity, Piece2D, Vec2, VectorDocument


def fixture(document):
    outer = PathEntity.from_points(
        document.active_layer_id,
        (
            Vec2(0, 0), Vec2(100, 0), Vec2(100, 100), Vec2(60, 100),
            Vec2(60, 40), Vec2(40, 40), Vec2(40, 100), Vec2(0, 100),
        ),
        closed=True,
        id="outer-notch",
    )
    inner = PathEntity.from_points(
        document.active_layer_id,
        (Vec2(70, 60), Vec2(90, 60), Vec2(90, 80), Vec2(70, 80)),
        closed=True,
        id="inner-cutout",
    )
    document.add_entities((outer, inner))
    document.add_pieces((Piece2D("Peça", outer.id, (inner.id,), id="piece"),))
    return outer, inner


class EditorAutomaticReliefTests(unittest.TestCase):
    def test_manual_relief_auto_detects_piece_contour_role(self):
        document = VectorDocument.create_default()
        outer, inner = fixture(document)
        controller = EditorController(document)
        self.assertEqual(controller.contour_role_for_entity(outer.id), "outer")
        self.assertEqual(controller.contour_role_for_entity(inner.id), "inner")
        outer_node = outer.node_ids[
            [node.point for node in outer.nodes()].index(Vec2(60, 40))
        ]
        inner_node = inner.node_ids[1]
        outer_preview = controller.preview_fillet(
            outer.id, outer_node, 5.0, kind="tbone", contour_role="auto"
        )
        inner_preview = controller.preview_fillet(
            inner.id, inner_node, 5.0, kind="dogbone", contour_role="auto"
        )
        self.assertEqual(outer_preview.metadata["contour_role"], "outer")
        self.assertEqual(inner_preview.metadata["contour_role"], "inner")

    def test_empty_selection_uses_classified_piece_roles_and_one_undo(self):
        document = VectorDocument.create_default()
        outer, inner = fixture(document)
        controller = EditorController(document)
        paths, roles = controller.automatic_relief_scope()
        self.assertEqual({path.id for path in paths}, {outer.id, inner.id})
        self.assertEqual(roles, {outer.id: "outer", inner.id: "inner"})
        preview = controller.preview_automatic_reliefs(5.0, kind="dogbone")
        self.assertEqual(preview.metadata["applied_count"], 6)
        outer_points = {
            tuple(item["point"])
            for item in preview.metadata["applied"]
            if item["path_id"] == outer.id
        }
        self.assertEqual(outer_points, {(60.0, 40.0), (40.0, 40.0)})
        before = dict(document.entities_by_id)
        controller.apply_modifier_preview(preview)
        self.assertNotEqual(document.entities_by_id, before)
        controller.undo()
        self.assertEqual(document.entities_by_id, before)


if __name__ == "__main__":
    unittest.main()
