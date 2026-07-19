import math
import unittest

from woodcam_editor.domain import (
    AddEntitiesCommand,
    ApplyModifierPreviewCommand,
    ArcSpan,
    CircleEntity,
    CommandStateError,
    ExtendLineSpanCommand,
    FilletCornerCommand,
    InMemoryCommandHistory,
    LineSpan,
    MoveEntitiesCommand,
    OffsetPathCommand,
    PathEntity,
    TrimLineSpanCommand,
    Vec2,
    VectorDocument,
    validate_document,
)
from woodcam_editor.geometry.modifiers import (
    FilletKind,
    ModifierError,
    preview_corner_fillet,
    preview_dogbone,
    preview_extend_line_span,
    preview_offset_closed_path,
    preview_tbone,
    preview_trim_at_point,
    preview_trim_line_span,
)


def square(document, size=100.0, entity_id="square"):
    return PathEntity.from_points(
        document.active_layer_id,
        (Vec2(0, 0), Vec2(size, 0), Vec2(size, size), Vec2(0, size)),
        closed=True,
        id=entity_id,
    )


class TrimPreviewTests(unittest.TestCase):
    def setUp(self):
        self.document = VectorDocument.create_default()
        self.path = PathEntity.from_points(
            self.document.active_layer_id,
            (Vec2(0, 0), Vec2(10, 0), Vec2(20, 0), Vec2(30, 0)),
            id="open",
        )

    def test_middle_trim_splits_open_path_without_mutating_source(self):
        source_points = self.path.flatten()
        preview = preview_trim_line_span(self.path, self.path.spans[1].id, 0.2, 0.8)
        self.assertEqual(len(preview.result_entities), 2)
        first, second = preview.result_entities
        self.assertEqual(first.id, self.path.id)
        self.assertEqual((first.start, first.end), (Vec2(0, 0), Vec2(12, 0)))
        self.assertEqual((second.start, second.end), (Vec2(18, 0), Vec2(30, 0)))
        self.assertEqual(self.path.flatten(), source_points)
        self.assertAlmostEqual(preview.metadata["removed_length_mm"], 6)

    def test_trimming_closed_path_opens_it_in_correct_order(self):
        path = square(self.document)
        preview = preview_trim_line_span(path, path.spans[0].id, 0.25, 0.75)
        result = preview.result_entities[0]
        self.assertFalse(result.closed)
        self.assertEqual(result.start, Vec2(75, 0))
        self.assertEqual(result.end, Vec2(25, 0))
        self.assertEqual(len(result.spans), 5)

    def test_interactive_trim_uses_neighbouring_intersections(self):
        target = PathEntity.from_points(
            self.document.active_layer_id, (Vec2(0, 0), Vec2(100, 0)), id="target"
        )
        cutters = (
            LineSpan(Vec2(20, -10), Vec2(20, 10), id="cut-a"),
            LineSpan(Vec2(70, -10), Vec2(70, 10), id="cut-b"),
        )
        preview = preview_trim_at_point(
            target, target.spans[0].id, Vec2(40, 1), cutters
        )
        self.assertEqual(preview.construction_points, (Vec2(20, 0), Vec2(70, 0)))
        self.assertEqual(preview.metadata["click_parameter"], 0.4)

    def test_ambiguous_or_zero_trim_is_rejected(self):
        with self.assertRaises(ModifierError):
            preview_trim_line_span(self.path, self.path.spans[0].id, 0.5, 0.5)
        with self.assertRaises(ModifierError):
            preview_trim_at_point(
                self.path,
                self.path.spans[0].id,
                Vec2(5, 0),
                (LineSpan(Vec2(0, 5), Vec2(10, 5)),),
            )


class ExtendPreviewTests(unittest.TestCase):
    def setUp(self):
        self.document = VectorDocument.create_default()
        self.path = PathEntity.from_points(
            self.document.active_layer_id,
            (Vec2(0, 0), Vec2(10, 0), Vec2(20, 0)),
            id="open",
        )

    def test_extend_chooses_nearest_forward_boundary(self):
        boundaries = (
            LineSpan(Vec2(40, -10), Vec2(40, 10), id="far"),
            LineSpan(Vec2(30, -10), Vec2(30, 10), id="near"),
        )
        preview = preview_extend_line_span(
            self.path, self.path.spans[-1].id, "end", boundaries
        )
        result = preview.result_entities[0]
        self.assertEqual(result.end, Vec2(30, 0))
        self.assertEqual(preview.metadata["boundary_span_id"], "near")
        self.assertAlmostEqual(preview.metadata["extension_length_mm"], 10)

    def test_extend_start_and_reject_non_endpoint_or_parallel(self):
        boundary = LineSpan(Vec2(-5, -10), Vec2(-5, 10), id="left")
        preview = preview_extend_line_span(
            self.path, self.path.spans[0].id, "start", (boundary,)
        )
        self.assertEqual(preview.result_entities[0].start, Vec2(-5, 0))
        with self.assertRaises(ModifierError):
            preview_extend_line_span(
                self.path, self.path.spans[0].id, "end", (boundary,)
            )
        with self.assertRaises(ModifierError):
            preview_extend_line_span(
                self.path,
                self.path.spans[-1].id,
                "end",
                (LineSpan(Vec2(25, 5), Vec2(40, 5)),),
            )


class OffsetPreviewTests(unittest.TestCase):
    def setUp(self):
        self.document = VectorDocument.create_default()
        self.path = square(self.document)

    def test_outward_and_inward_square_offsets_are_exact(self):
        outward = preview_offset_closed_path(self.path, 10).result_entities[0]
        self.assertTrue(outward.bounds().contains_point(Vec2(-10, -10), 1e-9))
        self.assertAlmostEqual(outward.bounds().width, 120)
        self.assertAlmostEqual(outward.signed_area(), 14400)
        inward = preview_offset_closed_path(self.path, -10).result_entities[0]
        self.assertAlmostEqual(inward.bounds().width, 80)
        self.assertAlmostEqual(inward.signed_area(), 6400)
        self.assertEqual(
            [span.id for span in inward.spans],
            [span.id for span in self.path.spans],
        )
        self.assertEqual(inward.node_ids, self.path.node_ids)
        self.assertEqual(inward.metadata["last_woodcam_modifier"], "offset")

    def test_outward_offset_is_independent_of_path_direction(self):
        clockwise = self.path.reversed()
        outward = preview_offset_closed_path(clockwise, 10).result_entities[0]
        self.assertAlmostEqual(outward.bounds().width, 120)
        self.assertLess(outward.signed_area(), 0)

    def test_collapsed_or_curved_offset_is_rejected(self):
        with self.assertRaises(ModifierError):
            preview_offset_closed_path(self.path, -60)
        curved = preview_corner_fillet(
            self.path, self.path.node_ids[1], 5
        ).result_entities[0]
        with self.assertRaises(ModifierError):
            preview_offset_closed_path(curved, 5)


class FilletPreviewTests(unittest.TestCase):
    def setUp(self):
        self.document = VectorDocument.create_default()
        self.path = square(self.document)
        self.corner_id = self.path.node_ids[1]

    def test_normal_fillet_is_tangent_and_exact_arc(self):
        preview = preview_corner_fillet(self.path, self.corner_id, 10)
        result = preview.result_entities[0]
        arcs = [span for span in result.spans if isinstance(span, ArcSpan)]
        self.assertEqual(len(arcs), 1)
        self.assertAlmostEqual(arcs[0].radius, 10)
        self.assertEqual(arcs[0].start, Vec2(90, 0))
        self.assertTrue(arcs[0].end.almost_equals(Vec2(100, 10)))
        self.assertEqual(preview.metadata["kind"], FilletKind.NORMAL.value)
        self.assertEqual(len(result.spans), 5)
        with self.assertRaises(ModifierError):
            preview_corner_fillet(self.path, self.corner_id, 150)

    def test_dogbone_requires_inner_90_corner_and_records_relief(self):
        with self.assertRaises(ModifierError):
            preview_dogbone(self.path, self.corner_id, 5, contour_role="outer")
        preview = preview_dogbone(self.path, self.corner_id, 5)
        result = preview.result_entities[0]
        arcs = [span for span in result.spans if isinstance(span, ArcSpan)]
        self.assertEqual(len(arcs), 1)
        self.assertEqual(len(result.spans), 5)
        arc = arcs[0]
        self.assertAlmostEqual(arc.center.distance_to(Vec2(100, 0)), 5.0)
        self.assertTrue(
            arc.center.almost_equals(
                Vec2(100 - 5 / math.sqrt(2), 5 / math.sqrt(2)),
                1e-9,
            )
        )
        self.assertTrue(arc.start.almost_equals(Vec2(100 - 5 * math.sqrt(2), 0)))
        self.assertTrue(arc.end.almost_equals(Vec2(100, 5 * math.sqrt(2))))
        self.assertAlmostEqual(abs(arc.sweep_angle), math.pi)
        self.assertTrue(arc.point_at(0.5).almost_equals(Vec2(100, 0), 1e-9))
        self.assertEqual(result.spans[0].end, arc.start)
        self.assertEqual(result.spans[2].start, arc.end)
        self.assertEqual(preview.metadata["placement_side"], "bisector")
        self.assertTrue(preview.metadata["continuous_relief"])
        self.assertAlmostEqual(preview.metadata["trim_distance_mm"], 5 * math.sqrt(2))
        self.assertNotIn("intentional_relief_loop", preview.metadata)
        self.assertNotIn("intentional_relief_touch_points", result.metadata)
        self.assertEqual(preview.warnings[0].code, "SLOT_WIDTH_NOT_VERIFIED")
        temp = VectorDocument(
            layers_by_id=self.document.layers_by_id,
            entities_by_id={result.id: result},
            active_layer_id=self.document.active_layer_id,
        )
        codes = {issue.code for issue in validate_document(temp).issues}
        self.assertNotIn("BRANCH_NODE", codes)
        self.assertNotIn("SELF_INTERSECTION", codes)

    def test_tbone_side_is_explicit_or_uses_longest_span(self):
        preview = preview_tbone(self.path, self.corner_id, 5, side="incoming")
        self.assertEqual(preview.metadata["placement_side"], "incoming")
        self.assertEqual(preview.metadata["center"], [95.0, 0.0])
        incoming_arc = next(
            span
            for span in preview.result_entities[0].spans
            if isinstance(span, ArcSpan)
        )
        self.assertEqual(incoming_arc.start, Vec2(90, 0))
        self.assertEqual(incoming_arc.end, Vec2(100, 0))
        self.assertAlmostEqual(abs(incoming_arc.sweep_angle), math.pi)
        self.assertAlmostEqual(incoming_arc.tangent_at(1.0).dot(Vec2(0, 1)), 1.0)
        self.assertEqual(preview.metadata["tangent_points"], [[100.0, 0.0]])
        outgoing = preview_tbone(self.path, self.corner_id, 5, side="outgoing")
        self.assertEqual(outgoing.metadata["center"], [100.0, 5.0])
        outgoing_arc = next(
            span
            for span in outgoing.result_entities[0].spans
            if isinstance(span, ArcSpan)
        )
        self.assertEqual(outgoing_arc.start, Vec2(100, 0))
        self.assertEqual(outgoing_arc.end, Vec2(100, 10))
        self.assertAlmostEqual(outgoing_arc.tangent_at(0.0).dot(Vec2(1, 0)), 1.0)
        automatic = preview_tbone(self.path, self.corner_id, 5)
        self.assertEqual(automatic.metadata["placement_side"], "incoming")
        with self.assertRaises(ValueError):
            preview_tbone(self.path, self.corner_id, 5, side="diagonal")

    def test_relief_rejects_non_right_corner(self):
        triangle = PathEntity.from_points(
            self.document.active_layer_id,
            (Vec2(0, 0), Vec2(100, 0), Vec2(50, 50)),
            closed=True,
        )
        with self.assertRaises(ModifierError):
            preview_dogbone(triangle, triangle.node_ids[1], 5)


class ModifierCommandTests(unittest.TestCase):
    def setUp(self):
        self.document = VectorDocument.create_default()
        self.path = square(self.document)
        self.document.add_entities((self.path,), bump_revision=False)
        self.history = InMemoryCommandHistory(self.document)

    def test_offset_command_apply_undo_redo(self):
        preview = preview_offset_closed_path(self.path, 5)
        self.history.execute(OffsetPathCommand(preview))
        self.assertAlmostEqual(self.document.get_entity(self.path.id).bounds().width, 110)
        self.history.undo()
        self.assertAlmostEqual(self.document.get_entity(self.path.id).bounds().width, 100)
        self.history.redo()
        self.assertAlmostEqual(self.document.get_entity(self.path.id).bounds().width, 110)

    def test_stale_preview_is_rejected_atomically(self):
        preview = preview_corner_fillet(self.path, self.path.node_ids[1], 5)
        self.history.execute(MoveEntitiesCommand((self.path.id,), Vec2(1, 0)))
        before = self.document.clone()
        with self.assertRaises(CommandStateError):
            self.history.execute(FilletCornerCommand(preview))
        self.assertEqual(self.document.entities_by_id, before.entities_by_id)

    def test_trim_command_can_add_second_path_and_undo_it(self):
        self.history.execute(ApplyModifierPreviewCommand(
            preview_trim_line_span(self.path, self.path.spans[0].id, 0.25, 0.75)
        ))
        self.assertEqual(len(self.document.entities_by_id), 1)
        self.assertFalse(self.document.get_entity(self.path.id).closed)
        self.history.undo()
        self.assertTrue(self.document.get_entity(self.path.id).closed)

    def test_specialized_command_types_accept_matching_previews(self):
        open_path = PathEntity.from_points(
            self.document.active_layer_id, (Vec2(0, 0), Vec2(10, 0)), id="open"
        )
        self.document.add_entities((open_path,), bump_revision=False)
        trim = preview_trim_line_span(open_path, open_path.spans[0].id, 0.2, 0.8)
        self.assertIsInstance(TrimLineSpanCommand(trim), ApplyModifierPreviewCommand)
        boundary = LineSpan(Vec2(20, -5), Vec2(20, 5))
        extend = preview_extend_line_span(open_path, open_path.spans[0].id, "end", (boundary,))
        self.assertIsInstance(ExtendLineSpanCommand(extend), ApplyModifierPreviewCommand)
        fillet = preview_corner_fillet(self.path, self.path.node_ids[1], 5)
        self.assertIsInstance(FilletCornerCommand(fillet), ApplyModifierPreviewCommand)
        with self.assertRaises(ValueError):
            OffsetPathCommand(fillet)


if __name__ == "__main__":
    unittest.main()
