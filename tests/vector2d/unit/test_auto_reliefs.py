import math
import unittest

from woodcam_editor.domain import (
    Affine2D,
    ArcSpan,
    AutoCornerReliefsCommand,
    CommandStateError,
    InMemoryCommandHistory,
    Layer,
    LineSpan,
    MoveEntitiesCommand,
    PathEntity,
    Vec2,
    VectorDocument,
    deserialize_document,
    serialize_document,
    validate_document,
)
from woodcam_editor.geometry import (
    NoApplicableCornersError,
    preview_auto_corner_reliefs,
)


def closed_path(layer_id, points, entity_id, role):
    return PathEntity.from_points(
        layer_id,
        tuple(points),
        closed=True,
        id=entity_id,
        metadata={"contour_role": role},
    )


def square(layer_id, entity_id="square", size=100.0, role="inner", origin=Vec2(0, 0)):
    return closed_path(
        layer_id,
        (
            origin,
            origin + Vec2(size, 0),
            origin + Vec2(size, size),
            origin + Vec2(0, size),
        ),
        entity_id,
        role,
    )


def document_for(*paths):
    layer_id = paths[0].layer_id
    return VectorDocument(
        layers_by_id={layer_id: Layer(id=layer_id, name="Test")},
        entities_by_id={path.id: path for path in paths},
        active_layer_id=layer_id,
    )


class AutoReliefSelectionTests(unittest.TestCase):
    def setUp(self):
        self.document = VectorDocument.create_default()
        self.layer_id = self.document.active_layer_id

    def test_inner_square_accepts_all_corners_in_ccw_and_cw_orientation(self):
        ccw = square(self.layer_id, "ccw")
        cw = square(self.layer_id, "cw").reversed()
        for source in (ccw, cw):
            preview = preview_auto_corner_reliefs((source,), 5, kind="dogbone")
            result = preview.result_entities[0]
            self.assertEqual(preview.metadata["applied_count"], 4)
            self.assertEqual(preview.metadata["rejected_count"], 0)
            self.assertEqual(len(result.spans), 8)
            arcs = [s for s in result.spans if isinstance(s, ArcSpan)]
            self.assertEqual(len(arcs), 4)
            self.assertTrue(
                all(abs(abs(arc.sweep_angle) - math.pi) <= 1e-9 for arc in arcs)
            )
            self.assertTrue(
                {span.id for span in source.spans}
                <= {span.id for span in result.spans}
            )
            self.assertTrue(
                all(item["continuous_relief"] for item in preview.metadata["applied"])
            )
            for item in preview.metadata["applied"]:
                center = Vec2.from_sequence(item["center"])
                corner = Vec2.from_sequence(item["point"])
                self.assertAlmostEqual(center.distance_to(corner), 5.0)
                arc = next(span for span in arcs if span.id == item["relief_arc_id"])
                self.assertTrue(arc.point_at(0.5).almost_equals(corner, 1e-9))
            self.assertNotIn("intentional_relief_touch_points", result.metadata)
            self.assertEqual(
                result.metadata["last_woodcam_modifier"],
                "auto_corner_reliefs",
            )
            report = validate_document(document_for(result))
            self.assertTrue(report.is_valid_for_cam)
            self.assertFalse(report.by_code("SELF_INTERSECTION"))
            self.assertFalse(report.by_code("BRANCH_NODE"))

    def test_outer_convex_corners_are_never_modified(self):
        outer = square(self.layer_id, "outer", role="outer")
        with self.assertRaises(NoApplicableCornersError) as caught:
            preview_auto_corner_reliefs((outer,), 5)
        metadata = caught.exception.metadata
        self.assertEqual(metadata["applied_count"], 0)
        self.assertEqual(metadata["rejected_count"], 4)
        self.assertEqual(
            {item["code"] for item in metadata["rejected"]},
            {"EXTERNAL_CORNER"},
        )

    def test_outer_path_accepts_only_concave_reentrant_corners(self):
        notch = closed_path(
            self.layer_id,
            (
                Vec2(0, 0),
                Vec2(10, 0),
                Vec2(10, 10),
                Vec2(6, 10),
                Vec2(6, 4),
                Vec2(4, 4),
                Vec2(4, 10),
                Vec2(0, 10),
            ),
            "notched-part",
            "outer",
        )
        for candidate in (notch, notch.reversed()):
            preview = preview_auto_corner_reliefs((candidate,), 0.5)
            result = preview.result_entities[0]
            self.assertEqual(preview.metadata["applied_count"], 2)
            self.assertEqual(preview.metadata["rejected_count"], 6)
            self.assertEqual(
                {tuple(item["point"]) for item in preview.metadata["applied"]},
                {(6.0, 4.0), (4.0, 4.0)},
            )
            self.assertTrue(
                all(item["code"] == "EXTERNAL_CORNER" for item in preview.metadata["rejected"])
            )
            arcs = [span for span in result.spans if isinstance(span, ArcSpan)]
            self.assertEqual(len(arcs), 2)
            self.assertTrue(
                all(abs(abs(arc.sweep_angle) - math.pi) <= 1e-9 for arc in arcs)
            )
            for item in preview.metadata["applied"]:
                arc = next(span for span in arcs if span.id == item["relief_arc_id"])
                self.assertTrue(
                    arc.point_at(0.5).almost_equals(
                        Vec2.from_sequence(item["point"]),
                        1e-9,
                    )
                )
            self.assertTrue(validate_document(document_for(result)).is_valid_for_cam)

    def test_role_is_mandatory_because_orientation_cannot_infer_piece_or_hole(self):
        path = PathEntity.from_points(
            self.layer_id,
            (Vec2(0, 0), Vec2(10, 0), Vec2(10, 10), Vec2(0, 10)),
            closed=True,
            id="unknown-role",
        )
        with self.assertRaises(NoApplicableCornersError) as caught:
            preview_auto_corner_reliefs((path,), 1)
        rejected = caught.exception.metadata["rejected"]
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["code"], "ROLE_NOT_DECLARED")

    def test_contour_roles_accepts_piece_classifier_result_without_mutating_path(self):
        path = PathEntity.from_points(
            self.layer_id,
            (Vec2(0, 0), Vec2(10, 0), Vec2(10, 10), Vec2(0, 10)),
            closed=True,
            id="classified-hole",
        )
        preview = preview_auto_corner_reliefs(
            (path,),
            1,
            contour_roles={path.id: "inner"},
        )
        self.assertEqual(preview.metadata["applied_count"], 4)
        self.assertEqual(
            preview.metadata["resolved_contour_roles"], {path.id: "inner"}
        )
        self.assertEqual(
            preview.metadata["contour_role_sources"],
            {path.id: "contour_roles"},
        )
        self.assertNotIn("contour_role", path.metadata)

    def test_contour_roles_override_takes_precedence_over_entity_metadata(self):
        metadata_says_inner = square(
            self.layer_id,
            "override-role",
            role="inner",
        )
        with self.assertRaises(NoApplicableCornersError) as caught:
            preview_auto_corner_reliefs(
                (metadata_says_inner,),
                1,
                contour_roles={metadata_says_inner.id: "outer"},
            )
        report = caught.exception.metadata
        self.assertEqual(report["applied_count"], 0)
        self.assertEqual(
            {item["code"] for item in report["rejected"]},
            {"EXTERNAL_CORNER"},
        )
        self.assertEqual(
            report["resolved_contour_roles"], {metadata_says_inner.id: "outer"}
        )
        self.assertEqual(
            report["contour_role_sources"],
            {metadata_says_inner.id: "contour_roles"},
        )

    def test_contour_roles_rejects_invalid_classifier_value(self):
        path = square(self.layer_id, "invalid-override")
        with self.assertRaises(ValueError):
            preview_auto_corner_reliefs(
                (path,),
                1,
                contour_roles={path.id: "maybe"},
            )

    def test_radius_that_does_not_fit_reports_every_rejection_and_refuses_noop(self):
        small = square(self.layer_id, "small", size=10)
        with self.assertRaises(NoApplicableCornersError) as caught:
            preview_auto_corner_reliefs((small,), 6)
        metadata = caught.exception.metadata
        self.assertEqual(metadata["applied_count"], 0)
        self.assertEqual(metadata["rejected_count"], 4)
        self.assertEqual(
            {item["code"] for item in metadata["rejected"]},
            {"RADIUS_DOES_NOT_FIT"},
        )
        self.assertEqual(len(caught.exception.warnings), 4)

    def test_mixed_selection_applies_safe_path_and_reports_rejected_triangle(self):
        valid = square(self.layer_id, "valid", origin=Vec2(0, 0))
        triangle = closed_path(
            self.layer_id,
            (Vec2(200, 0), Vec2(300, 0), Vec2(250, 60)),
            "triangle",
            "inner",
        )
        preview = preview_auto_corner_reliefs((valid, triangle), 5)
        self.assertEqual([entity.id for entity in preview.result_entities], ["valid"])
        self.assertEqual(preview.metadata["applied_count"], 4)
        self.assertEqual(preview.metadata["rejected_count"], 3)
        self.assertEqual(
            {item["code"] for item in preview.metadata["rejected"]},
            {"ANGLE_NOT_90"},
        )
        warning_codes = {warning.code for warning in preview.warnings}
        self.assertIn("ANGLE_NOT_90", warning_codes)
        self.assertIn("SLOT_WIDTH_NOT_VERIFIED", warning_codes)

    def test_tbone_auto_records_selected_longest_side(self):
        rectangle = closed_path(
            self.layer_id,
            (Vec2(0, 0), Vec2(100, 0), Vec2(100, 40), Vec2(0, 40)),
            "rectangle",
            "inner",
        )
        for candidate in (rectangle, rectangle.reversed()):
            preview = preview_auto_corner_reliefs(
                (candidate,), 5, kind="tbone", tbone_side="auto"
            )
            self.assertEqual(preview.metadata["applied_count"], 4)
            result = preview.result_entities[0]
            arcs = [span for span in result.spans if isinstance(span, ArcSpan)]
            self.assertEqual(len(arcs), 4)
            self.assertTrue(
                all(abs(abs(arc.sweep_angle) - math.pi) <= 1e-9 for arc in arcs)
            )
            self.assertTrue(validate_document(document_for(result)).is_valid_for_cam)
            self.assertEqual(
                {item["placement_side"] for item in preview.metadata["applied"]},
                {"incoming", "outgoing"},
            )
        with self.assertRaises(ValueError):
            preview_auto_corner_reliefs(
                (rectangle,), 5, kind="tbone", tbone_side="diagonal"
            )


class AutoReliefCommandTests(unittest.TestCase):
    def setUp(self):
        temporary = VectorDocument.create_default()
        self.layer_id = temporary.active_layer_id
        self.first = square(self.layer_id, "first", origin=Vec2(0, 0))
        self.second = square(self.layer_id, "second", origin=Vec2(150, 0))
        self.document = document_for(self.first, self.second)
        self.history = InMemoryCommandHistory(self.document)

    def test_multiple_paths_apply_as_one_undo_and_redo(self):
        preview = preview_auto_corner_reliefs((self.first, self.second), 5)
        self.assertEqual(preview.metadata["applied_count"], 8)
        self.assertEqual(len(preview.original_entities), 2)
        self.history.execute(AutoCornerReliefsCommand(preview))
        self.assertEqual(len(self.document.get_entity("first").spans), 8)
        self.assertEqual(len(self.document.get_entity("second").spans), 8)
        self.history.undo()
        self.assertEqual(len(self.document.get_entity("first").spans), 4)
        self.assertEqual(len(self.document.get_entity("second").spans), 4)
        self.assertFalse(self.history.can_undo)
        self.history.redo()
        self.assertEqual(len(self.document.get_entity("first").spans), 8)
        self.assertEqual(len(self.document.get_entity("second").spans), 8)

    def test_stale_preview_is_rejected_after_source_change(self):
        preview = preview_auto_corner_reliefs((self.first, self.second), 5)
        self.history.execute(MoveEntitiesCommand((self.first.id,), Vec2(1, 0)))
        before = self.document.clone()
        with self.assertRaises(CommandStateError):
            self.history.execute(AutoCornerReliefsCommand(preview))
        self.assertEqual(self.document.entities_by_id, before.entities_by_id)

    def test_auto_relief_metadata_and_ids_survive_serialization(self):
        preview = preview_auto_corner_reliefs((self.first,), 5)
        result_document = document_for(preview.result_entities[0])
        serialized = serialize_document(result_document)
        restored = deserialize_document(serialized.json_text, serialized.checksum)
        result = preview.result_entities[0]
        loaded = restored.get_entity(result.id)
        self.assertEqual([span.id for span in loaded.spans], [span.id for span in result.spans])
        self.assertEqual(loaded.node_ids, result.node_ids)
        self.assertEqual(
            loaded.metadata["last_woodcam_modifier"], "auto_corner_reliefs"
        )


if __name__ == "__main__":
    unittest.main()
