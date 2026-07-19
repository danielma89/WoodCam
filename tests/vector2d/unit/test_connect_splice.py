import unittest

from woodcam_editor.domain import (
    ArcSpan,
    CircleEntity,
    CommandStateError,
    ConnectEndpointToGeometryCommand,
    EllipseEntity,
    InMemoryCommandHistory,
    Layer,
    LineSpan,
    MoveEntitiesCommand,
    PathEntity,
    SplicePathToContourCommand,
    Vec2,
    VectorDocument,
    build_topology,
    validate_document,
    deserialize_document,
    serialize_document,
)
from woodcam_editor.geometry.modifiers import (
    ModifierError,
    preview_connect_endpoint_to_geometry,
    preview_splice_open_path_to_contour,
    project_point_to_geometry,
)


def document_with(*entities):
    if not entities:
        return VectorDocument.create_default()
    layer_ids = sorted({entity.layer_id for entity in entities})
    layers = {
        layer_id: Layer(name="Test %s" % layer_id, id=layer_id, order=index)
        for index, layer_id in enumerate(layer_ids)
    }
    return VectorDocument(
        layers_by_id=layers,
        entities_by_id={entity.id: entity for entity in entities},
        active_layer_id=layer_ids[0],
    )


class EndpointProjectionTests(unittest.TestCase):
    def test_exact_line_arc_circle_projection(self):
        line = LineSpan(Vec2(0, 0), Vec2(10, 0), id="line")
        line_projection = project_point_to_geometry(Vec2(4, 3), line)
        self.assertEqual(line_projection.point, Vec2(4, 0))
        self.assertAlmostEqual(line_projection.parameter, 0.4)
        arc = ArcSpan(Vec2(10, 0), Vec2(0, 10), Vec2(0, 0), id="arc")
        arc_projection = project_point_to_geometry(Vec2(8, 8), arc)
        self.assertAlmostEqual(arc_projection.parameter, 0.5, places=6)
        circle = CircleEntity("layer", Vec2(0, 0), 10, id="circle")
        circle_projection = project_point_to_geometry(Vec2(12, 0), circle)
        self.assertEqual(circle_projection.point, Vec2(10, 0))
        self.assertAlmostEqual(circle_projection.distance, 2)

    def test_only_geometrically_circular_ellipse_is_supported(self):
        circular = EllipseEntity("layer", Vec2(0, 0), 10, 10, id="round")
        self.assertEqual(
            project_point_to_geometry(Vec2(20, 0), circular).point,
            Vec2(10, 0),
        )
        ellipse = EllipseEntity("layer", Vec2(0, 0), 10, 5, id="ellipse")
        with self.assertRaisesRegex(ModifierError, "cannot be split exactly"):
            project_point_to_geometry(Vec2(20, 0), ellipse)

    def test_projection_from_circle_center_is_explicitly_ambiguous(self):
        circle = CircleEntity("layer", Vec2(0, 0), 10)
        with self.assertRaises(ModifierError):
            project_point_to_geometry(Vec2(0, 0), circle)


class ConnectEndpointPreviewTests(unittest.TestCase):
    def setUp(self):
        self.document = VectorDocument.create_default()
        self.layer_id = self.document.active_layer_id

    def test_connect_to_line_interior_splits_target_and_reports_branch(self):
        source = PathEntity.from_points(
            self.layer_id, (Vec2(5, 0.1), Vec2(5, 5)), id="source"
        )
        target = PathEntity.from_points(
            self.layer_id, (Vec2(0, 0), Vec2(10, 0)), id="target"
        )
        preview = preview_connect_endpoint_to_geometry(
            source, "start", target, tolerance=0.2
        )
        moved, split = preview.result_entities
        self.assertEqual(moved.start, Vec2(5, 0))
        self.assertEqual(len(split.spans), 2)
        self.assertEqual(split.spans[0].id, target.spans[0].id)
        self.assertTrue(preview.metadata["target_split"])
        self.assertTrue(preview.metadata["creates_branch"])
        self.assertEqual(preview.warnings[0].code, "CREATES_BRANCH")
        result_document = document_with(moved, split)
        graph = build_topology(result_document)
        self.assertEqual(len(graph.branch_nodes), 1)
        self.assertEqual(graph.branch_nodes[0].degree, 3)
        self.assertEqual(source.start, Vec2(5, 0.1))
        self.assertEqual(len(target.spans), 1)

    def test_connect_to_open_target_endpoint_does_not_create_branch(self):
        source = PathEntity.from_points(
            self.layer_id, (Vec2(0.05, 0.05), Vec2(0, 5)), id="source"
        )
        target = PathEntity.from_points(
            self.layer_id, (Vec2(0, 0), Vec2(10, 0)), id="target"
        )
        preview = preview_connect_endpoint_to_geometry(
            source, "start", target, tolerance=0.2
        )
        self.assertFalse(preview.metadata["target_split"])
        self.assertFalse(preview.metadata["creates_branch"])
        result_document = document_with(*preview.result_entities)
        graph = build_topology(result_document)
        self.assertFalse(graph.branch_nodes)
        joined_node = graph.node_for("source", source.node_ids[0])
        self.assertEqual(joined_node.degree, 2)

    def test_connect_to_arc_interior_preserves_exact_arc_and_splits(self):
        first = ArcSpan(Vec2(10, 0), Vec2(-10, 0), Vec2(0, 0), False, id="upper")
        second = ArcSpan(Vec2(-10, 0), Vec2(10, 0), Vec2(0, 0), False, id="lower")
        target = PathEntity(
            self.layer_id,
            (first, second),
            closed=True,
            id="arc-target",
            node_ids=("right", "left"),
        )
        source = PathEntity.from_points(
            self.layer_id, (Vec2(0, 10.1), Vec2(0, 20)), id="source"
        )
        preview = preview_connect_endpoint_to_geometry(
            source,
            "start",
            target,
            target_span_id="upper",
            tolerance=0.2,
        )
        split = preview.result_entities[1]
        self.assertEqual(len(split.spans), 3)
        self.assertTrue(all(isinstance(span, ArcSpan) for span in split.spans))
        self.assertAlmostEqual(split.spans[0].radius, 10)

    def test_connect_to_circle_explicitly_converts_and_splits_full_circle(self):
        source = PathEntity.from_points(
            self.layer_id, (Vec2(-10.1, 0), Vec2(-20, 0)), id="source"
        )
        circle = CircleEntity(self.layer_id, Vec2(0, 0), 10, id="circle")
        preview = preview_connect_endpoint_to_geometry(
            source, "start", circle, tolerance=0.2
        )
        target = preview.result_entities[1]
        self.assertIsInstance(target, PathEntity)
        self.assertEqual(target.id, circle.id)
        self.assertTrue(target.closed)
        self.assertEqual(len(target.spans), 2)
        self.assertTrue(all(isinstance(span, ArcSpan) for span in target.spans))
        self.assertEqual(target.metadata["converted_from"], "circle")
        self.assertTrue(preview.metadata["creates_branch"])

    def test_connection_tolerance_and_explicit_span_choice_are_enforced(self):
        source = PathEntity.from_points(
            self.layer_id, (Vec2(5, 2), Vec2(5, 5)), id="source"
        )
        target = PathEntity.from_points(
            self.layer_id, (Vec2(0, 0), Vec2(10, 0)), id="target"
        )
        with self.assertRaisesRegex(ModifierError, "tolerance"):
            preview_connect_endpoint_to_geometry(source, "start", target, tolerance=1)
        with self.assertRaises(KeyError):
            preview_connect_endpoint_to_geometry(
                source, "start", target, target_span_id="missing", tolerance=3
            )

    def test_connection_command_undo_redo_restores_circle_type(self):
        source = PathEntity.from_points(
            self.layer_id, (Vec2(-10.1, 0), Vec2(-20, 0)), id="source"
        )
        circle = CircleEntity(self.layer_id, Vec2(0, 0), 10, id="circle")
        self.document.add_entities((source, circle), bump_revision=False)
        history = InMemoryCommandHistory(self.document)
        preview = preview_connect_endpoint_to_geometry(
            source, "start", circle, tolerance=0.2
        )
        history.execute(ConnectEndpointToGeometryCommand(preview))
        self.assertIsInstance(self.document.get_entity("circle"), PathEntity)
        history.undo()
        self.assertIsInstance(self.document.get_entity("circle"), CircleEntity)
        history.redo()
        self.assertIsInstance(self.document.get_entity("circle"), PathEntity)


class SpliceRealCaseTests(unittest.TestCase):
    def setUp(self):
        self.document = VectorDocument.create_default()
        self.layer_id = self.document.active_layer_id
        # Regression fixture matching the reported "open neck + circle" shape:
        # an open U/neck approaches the left side of a circular body twice.
        self.neck = PathEntity.from_points(
            self.layer_id,
            (Vec2(80, 40), Vec2(40, 40), Vec2(40, -40), Vec2(80, -40)),
            id="open-neck",
        )
        self.circle = CircleEntity(
            self.layer_id, Vec2(100, 0), 50, id="body-circle"
        )

    def test_short_and_long_circle_routes_are_explicit_and_branch_free(self):
        short = preview_splice_open_path_to_contour(
            self.neck, self.circle, route="short", tolerance=10
        )
        long = preview_splice_open_path_to_contour(
            self.neck, self.circle, route="long", tolerance=10
        )
        self.assertLess(
            short.metadata["route_length_mm"], long.metadata["route_length_mm"]
        )
        self.assertEqual(short.metadata["result"], "single_closed_path")
        self.assertFalse(short.metadata["creates_branch"])
        short_result = short.result_entities[0]
        long_result = long.result_entities[0]
        self.assertTrue(short_result.closed)
        self.assertTrue(long_result.closed)
        self.assertEqual(short_result.id, self.neck.id)
        self.assertEqual(
            short_result.node_ids[: len(self.neck.node_ids)],
            self.neck.node_ids,
        )
        self.assertEqual(
            [span.id for span in short_result.spans[: len(self.neck.spans)]],
            [span.id for span in self.neck.spans],
        )
        self.assertEqual(short.removed_ids, frozenset((self.circle.id,)))
        self.assertLess(short_result.bounds().max_x, 100)
        self.assertAlmostEqual(long_result.bounds().max_x, 150)
        for result in (short_result, long_result):
            result_document = document_with(result)
            self.assertFalse(build_topology(result_document).branch_nodes)
            codes = {issue.code for issue in validate_document(result_document).issues}
            self.assertNotIn("OPEN_PATH", codes)
            self.assertNotIn("BRANCH_NODE", codes)

    def test_guide_point_chooses_the_expected_circle_side(self):
        left = preview_splice_open_path_to_contour(
            self.neck,
            self.circle,
            guide_point=Vec2(50, 0),
            tolerance=10,
        )
        right = preview_splice_open_path_to_contour(
            self.neck,
            self.circle,
            guide_point=Vec2(150, 0),
            tolerance=10,
        )
        self.assertLess(
            left.metadata["route_length_mm"], right.metadata["route_length_mm"]
        )
        self.assertIn("guide", left.metadata["route_selection"])
        self.assertIn("guide", right.metadata["route_selection"])

    def test_splice_closed_linear_target_preserves_short_or_long_route(self):
        target = PathEntity.from_points(
            self.layer_id,
            (Vec2(0, 0), Vec2(100, 0), Vec2(100, 100), Vec2(0, 100)),
            closed=True,
            id="target-square",
        )
        source = PathEntity.from_points(
            self.layer_id,
            (Vec2(20, -0.1), Vec2(20, -20), Vec2(80, -20), Vec2(80, -0.1)),
            id="source-u",
        )
        short = preview_splice_open_path_to_contour(
            source, target, route="short", tolerance=1
        )
        long = preview_splice_open_path_to_contour(
            source, target, route="long", tolerance=1
        )
        self.assertAlmostEqual(short.metadata["route_length_mm"], 60)
        self.assertAlmostEqual(long.metadata["route_length_mm"], 340)
        self.assertEqual(short.result_entities[0].bounds().max_y, 0)
        self.assertEqual(long.result_entities[0].bounds().max_y, 100)
        self.assertEqual(len(set(span.id for span in long.result_entities[0].spans)), len(long.result_entities[0].spans))
        result_ids = {span.id for span in long.result_entities[0].spans}
        self.assertTrue({span.id for span in target.spans} <= result_ids)

    def test_equal_routes_require_guide_and_non_circular_ellipse_is_rejected(self):
        source = PathEntity.from_points(
            self.layer_id,
            (Vec2(0, 10.1), Vec2(-20, 0), Vec2(0, -10.1)),
            id="diameter-source",
        )
        circle = CircleEntity(self.layer_id, Vec2(0, 0), 10, id="circle")
        with self.assertRaisesRegex(ModifierError, "equal length"):
            preview_splice_open_path_to_contour(
                source, circle, route="short", tolerance=0.2
            )
        guided = preview_splice_open_path_to_contour(
            source,
            circle,
            guide_point=Vec2(10, 0),
            tolerance=0.2,
        )
        self.assertTrue(guided.result_entities[0].closed)
        ellipse = EllipseEntity(self.layer_id, Vec2(0, 0), 10, 5, id="ellipse")
        with self.assertRaisesRegex(ModifierError, "cannot be split exactly"):
            preview_splice_open_path_to_contour(
                source, ellipse, guide_point=Vec2(10, 0), tolerance=1
            )

    def test_splice_command_consumes_target_and_undo_restores_both(self):
        self.document.add_entities((self.neck, self.circle), bump_revision=False)
        history = InMemoryCommandHistory(self.document)
        preview = preview_splice_open_path_to_contour(
            self.neck, self.circle, route="long", tolerance=10
        )
        history.execute(SplicePathToContourCommand(preview))
        self.assertEqual(set(self.document.entities_by_id), {self.neck.id})
        self.assertTrue(self.document.get_entity(self.neck.id).closed)
        history.undo()
        self.assertEqual(
            set(self.document.entities_by_id), {self.neck.id, self.circle.id}
        )
        self.assertFalse(self.document.get_entity(self.neck.id).closed)
        self.assertIsInstance(self.document.get_entity(self.circle.id), CircleEntity)
        history.redo()
        self.assertEqual(set(self.document.entities_by_id), {self.neck.id})

    def test_splice_result_survives_canonical_serialization(self):
        preview = preview_splice_open_path_to_contour(
            self.neck, self.circle, route="long", tolerance=10
        )
        result_document = document_with(preview.result_entities[0])
        serialized = serialize_document(result_document)
        restored = deserialize_document(serialized.json_text, serialized.checksum)
        restored_path = restored.get_entity(self.neck.id)
        self.assertTrue(restored_path.closed)
        self.assertEqual(
            [span.id for span in restored_path.spans],
            [span.id for span in preview.result_entities[0].spans],
        )
        self.assertEqual(
            restored_path.metadata["last_woodcam_modifier"],
            "splice_open_path_to_contour",
        )

    def test_splice_stale_guard_rejects_changed_source(self):
        self.document.add_entities((self.neck, self.circle), bump_revision=False)
        history = InMemoryCommandHistory(self.document)
        preview = preview_splice_open_path_to_contour(
            self.neck, self.circle, route="long", tolerance=10
        )
        history.execute(MoveEntitiesCommand((self.neck.id,), Vec2(1, 0)))
        with self.assertRaises(CommandStateError):
            history.execute(SplicePathToContourCommand(preview))
        self.assertEqual(len(self.document.entities_by_id), 2)


if __name__ == "__main__":
    unittest.main()
