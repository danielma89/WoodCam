import unittest

from woodcam_editor.domain import (
    AddEntitiesCommand,
    Affine2D,
    CircleEntity,
    ClosePathCommand,
    CommandStateError,
    CompositeCommand,
    DeleteEntitiesCommand,
    GeometryError,
    InMemoryCommandHistory,
    JoinPathsCommand,
    Layer,
    MoveEntitiesCommand,
    MoveNodeCommand,
    PathEntity,
    Piece2D,
    ReplacePiecesCommand,
    SplitSpanCommand,
    TransformEntitiesCommand,
    Vec2,
    VectorDocument,
    WorkArea,
    build_containment_tree,
    build_topology,
    document_to_dict,
    join_path_entities,
    join_path_entities_with_line,
    validate_document,
)


def path(document, points, closed=False, id=None):
    return PathEntity.from_points(document.active_layer_id, points, closed, id=id)


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.document = VectorDocument.create_default(WorkArea(0, 0, 100, 100))
        self.history = InMemoryCommandHistory(self.document)

    def test_add_move_undo_redo_and_revision_monotonicity(self):
        entity = path(self.document, (Vec2(0, 0), Vec2(10, 0)), id="path")
        self.history.execute(AddEntitiesCommand((entity,)))
        self.history.execute(MoveEntitiesCommand((entity.id,), Vec2(5, 2)))
        self.assertEqual(self.document.get_entity(entity.id).start, Vec2(5, 2))
        revisions = [self.document.revision]
        self.history.undo()
        revisions.append(self.document.revision)
        self.assertEqual(self.document.get_entity(entity.id).start, Vec2(0, 0))
        self.history.redo()
        revisions.append(self.document.revision)
        self.assertEqual(self.document.get_entity(entity.id).start, Vec2(5, 2))
        self.assertEqual(revisions, sorted(revisions))
        self.assertEqual(len(set(revisions)), 3)

    def test_move_node_changes_only_on_apply_and_is_one_undo(self):
        entity = path(self.document, (Vec2(0, 0), Vec2(10, 0), Vec2(20, 0)), id="path")
        self.history.execute(AddEntitiesCommand((entity,)))
        node_id = entity.node_ids[1]
        command = MoveNodeCommand(entity.id, node_id, Vec2(10, 5))
        self.assertEqual(self.document.get_entity(entity.id).node_position(node_id), Vec2(10, 0))
        self.history.execute(command)
        moved = self.document.get_entity(entity.id)
        self.assertEqual(moved.node_position(node_id), Vec2(10, 5))
        self.history.undo()
        self.assertEqual(self.document.get_entity(entity.id).node_position(node_id), Vec2(10, 0))

    def test_delete_restores_piece_and_geometry(self):
        outer = path(self.document, (Vec2(0, 0), Vec2(50, 0), Vec2(50, 50), Vec2(0, 50)), True, "outer")
        hole = CircleEntity(self.document.active_layer_id, Vec2(25, 25), 5, id="hole")
        self.document.add_entities((outer, hole), bump_revision=False)
        self.document.add_pieces((Piece2D("P", outer.id, (hole.id,), id="piece"),), bump_revision=False)
        command = DeleteEntitiesCommand((outer.id,))
        self.history.execute(command)
        self.assertNotIn("piece", self.document.pieces_by_id)
        self.history.undo()
        self.assertIn("outer", self.document.entities_by_id)
        self.assertIn("piece", self.document.pieces_by_id)

    def test_replace_piece_classification_is_one_reversible_command(self):
        outer = path(self.document, (Vec2(0, 0), Vec2(50, 0), Vec2(50, 50), Vec2(0, 50)), True, "outer")
        hole = CircleEntity(self.document.active_layer_id, Vec2(25, 25), 5, id="hole")
        self.document.add_entities((outer, hole), bump_revision=False)
        piece = Piece2D("P", outer.id, (hole.id,), id="piece")
        self.history.execute(ReplacePiecesCommand((piece,)))
        self.assertEqual(tuple(self.document.pieces_by_id), ("piece",))
        self.history.undo()
        self.assertFalse(self.document.pieces_by_id)
        self.history.redo()
        self.assertEqual(self.document.pieces_by_id["piece"].inner_path_ids, ("hole",))

    def test_referenced_entity_change_marks_piece_stale_and_reclassify_clears_it(self):
        outer = path(self.document, (Vec2(0, 0), Vec2(50, 0), Vec2(50, 50), Vec2(0, 50)), True, "outer")
        hole = CircleEntity(self.document.active_layer_id, Vec2(25, 25), 5, id="hole")
        self.document.add_entities((outer, hole), bump_revision=False)
        piece = Piece2D("P", outer.id, (hole.id,), id="piece")
        self.document.add_pieces((piece,), bump_revision=False)
        self.history.execute(MoveEntitiesCommand((hole.id,), Vec2(1, 0)))
        self.assertTrue(self.document.pieces_by_id["piece"].stale)
        self.history.undo()
        self.assertFalse(self.document.pieces_by_id["piece"].stale)
        stale_classification = Piece2D(
            "P revisada", outer.id, (hole.id,), id="piece", stale=True
        )
        self.history.execute(ReplacePiecesCommand((stale_classification,)))
        self.assertFalse(self.document.pieces_by_id["piece"].stale)

    def test_failed_transform_is_atomic(self):
        circle = CircleEntity(self.document.active_layer_id, Vec2(10, 10), 5, id="circle")
        self.document.add_entities((circle,), bump_revision=False)
        before = document_to_dict(self.document)
        with self.assertRaises(GeometryError):
            self.history.execute(TransformEntitiesCommand((circle.id,), Affine2D.scaling(2, 3)))
        self.assertEqual(document_to_dict(self.document), before)
        self.assertFalse(self.history.can_undo)

    def test_split_and_close_are_reversible(self):
        entity = path(self.document, (Vec2(0, 0), Vec2(10, 0), Vec2(10, 10)), id="path")
        self.document.add_entities((entity,), bump_revision=False)
        self.history.execute(SplitSpanCommand(entity.id, entity.spans[0].id, 0.5))
        self.assertEqual(len(self.document.get_entity(entity.id).spans), 3)
        self.history.execute(ClosePathCommand(entity.id, "line"))
        self.assertTrue(self.document.get_entity(entity.id).closed)
        self.history.undo()
        self.assertFalse(self.document.get_entity(entity.id).closed)
        self.history.undo()
        self.assertEqual(len(self.document.get_entity(entity.id).spans), 2)

    def test_composite_is_one_history_entry(self):
        first = CircleEntity(self.document.active_layer_id, Vec2(10, 10), 2, id="a")
        second = CircleEntity(self.document.active_layer_id, Vec2(20, 10), 2, id="b")
        composite = CompositeCommand((AddEntitiesCommand((first, second)), MoveEntitiesCommand(("a", "b"), Vec2(5, 0))))
        self.history.execute(composite)
        self.assertEqual(self.document.get_entity("a").center, Vec2(15, 10))
        self.history.undo()
        self.assertFalse(self.document.entities_by_id)
        self.assertFalse(self.history.can_undo)

    def test_redo_stack_clears_after_new_command(self):
        first = CircleEntity(self.document.active_layer_id, Vec2(10, 10), 2, id="a")
        self.history.execute(AddEntitiesCommand((first,)))
        self.history.undo()
        second = CircleEntity(self.document.active_layer_id, Vec2(20, 10), 2, id="b")
        self.history.execute(AddEntitiesCommand((second,)))
        self.assertFalse(self.history.can_redo)
        with self.assertRaises(CommandStateError):
            self.history.redo()


class JoinTests(unittest.TestCase):
    def test_all_four_endpoint_orientations(self):
        for first_endpoint in ("start", "end"):
            for second_endpoint in ("start", "end"):
                document = VectorDocument.create_default()
                first_points = (Vec2(0, 0), Vec2(-1, 0)) if first_endpoint == "start" else (Vec2(-1, 0), Vec2(0, 0))
                second_points = (Vec2(0, 0), Vec2(1, 0)) if second_endpoint == "start" else (Vec2(1, 0), Vec2(0, 0))
                first = path(document, first_points, id="first")
                second = path(document, second_points, id="second")
                joined = join_path_entities(first, second, first_endpoint, second_endpoint, tolerance=0.001)
                self.assertEqual(joined.start, Vec2(-1, 0))
                self.assertEqual(joined.end, Vec2(1, 0))
                self.assertEqual(len(joined.spans), 2)
                self.assertEqual(len(joined.node_ids), 3)

    def test_join_command_removes_second_and_undo_restores_both(self):
        document = VectorDocument.create_default()
        first = path(document, (Vec2(0, 0), Vec2(10, 0)), id="first")
        second = path(document, (Vec2(10.1, 0), Vec2(20, 0)), id="second")
        document.add_entities((first, second), bump_revision=False)
        history = InMemoryCommandHistory(document)
        history.execute(JoinPathsCommand("first", "second", tolerance=0.2))
        self.assertEqual(set(document.entities_by_id), {"first"})
        history.undo()
        self.assertEqual(set(document.entities_by_id), {"first", "second"})

    def test_explicit_join_with_line_bridges_large_gap_without_moving_sources(self):
        document = VectorDocument.create_default()
        first = path(document, (Vec2(0, 0), Vec2(10, 0)), id="first")
        second = path(document, (Vec2(20, 5), Vec2(30, 5)), id="second")
        joined = join_path_entities_with_line(
            first, second, "end", "start", tolerance=0.2
        )
        self.assertEqual(joined.start, Vec2(0, 0))
        self.assertEqual(joined.end, Vec2(30, 5))
        self.assertEqual(len(joined.spans), 3)
        self.assertEqual(joined.spans[1].start, Vec2(10, 0))
        self.assertEqual(joined.spans[1].end, Vec2(20, 5))
        self.assertAlmostEqual(
            joined.metadata["joined_with_line_gap_mm"], (125.0) ** 0.5
        )

    def test_join_command_line_mode_is_one_undoable_operation(self):
        document = VectorDocument.create_default()
        first = path(document, (Vec2(0, 0), Vec2(10, 0)), id="first")
        second = path(document, (Vec2(20, 0), Vec2(30, 0)), id="second")
        document.add_entities((first, second), bump_revision=False)
        history = InMemoryCommandHistory(document)
        history.execute(JoinPathsCommand("first", "second", tolerance=0.2, mode="line"))
        self.assertEqual(len(document.get_entity("first").spans), 3)
        self.assertNotIn("second", document.entities_by_id)
        history.undo()
        self.assertEqual(set(document.entities_by_id), {"first", "second"})


class TopologyAndValidationTests(unittest.TestCase):
    def test_topology_open_component_and_branch_degree(self):
        document = VectorDocument.create_default()
        paths = (
            path(document, (Vec2(-1, 0), Vec2(0, 0)), id="left"),
            path(document, (Vec2(0, 0), Vec2(1, 0)), id="right"),
            path(document, (Vec2(0, 0), Vec2(0, 1)), id="up"),
        )
        document.add_entities(paths, bump_revision=False)
        graph = build_topology(document)
        self.assertEqual(len(graph.components), 1)
        self.assertEqual(len(graph.open_nodes), 3)
        self.assertEqual(len(graph.branch_nodes), 1)
        self.assertEqual(graph.branch_nodes[0].degree, 3)

    def test_containment_tree_outer_hole_and_island(self):
        document = VectorDocument.create_default()
        outer = path(document, (Vec2(0, 0), Vec2(100, 0), Vec2(100, 100), Vec2(0, 100)), True, "outer")
        hole = CircleEntity(document.active_layer_id, Vec2(50, 50), 30, id="hole")
        island = CircleEntity(document.active_layer_id, Vec2(50, 50), 10, id="island")
        document.add_entities((outer, hole, island), bump_revision=False)
        tree = build_containment_tree(document)
        self.assertIsNone(tree.parent_by_id["outer"])
        self.assertEqual(tree.parent_by_id["hole"], "outer")
        self.assertEqual(tree.parent_by_id["island"], "hole")
        self.assertEqual(tree.depth_by_id, {"outer": 0, "hole": 1, "island": 2})

    def test_validator_reports_open_near_duplicate_outside_and_branch(self):
        document = VectorDocument.create_default(WorkArea(0, 0, 50, 50))
        open_a = path(document, (Vec2(0, 0), Vec2(10, 0)), id="a")
        open_b = path(document, (Vec2(10.1, 0), Vec2(20, 0)), id="b")
        circle_a = CircleEntity(document.active_layer_id, Vec2(20, 20), 3, id="c1")
        circle_b = CircleEntity(document.active_layer_id, Vec2(20, 20), 3, id="c2")
        outside = CircleEntity(document.active_layer_id, Vec2(60, 60), 2, id="outside")
        document.add_entities((open_a, open_b, circle_a, circle_b, outside), bump_revision=False)
        report = validate_document(document)
        codes = {issue.code for issue in report.issues}
        self.assertTrue({"OPEN_PATH", "NEAR_OPEN_ENDPOINTS", "DUPLICATE_ENTITY", "OUTSIDE_WORK_AREA"} <= codes)
        self.assertFalse(report.is_valid_for_cam)
        self.assertEqual(
            [issue.id for issue in report.issues],
            [issue.id for issue in validate_document(document).issues],
        )

    def test_validator_reports_bowtie_self_intersection(self):
        document = VectorDocument.create_default()
        bowtie = path(
            document,
            (Vec2(0, 0), Vec2(10, 10), Vec2(0, 10), Vec2(10, 0)),
            True,
            "bowtie",
        )
        document.add_entities((bowtie,), bump_revision=False)
        codes = {issue.code for issue in validate_document(document).issues}
        self.assertIn("SELF_INTERSECTION", codes)
        self.assertIn("ZERO_AREA_PATH", codes)

    def test_exact_duplicate_groups_are_scoped_and_keep_partial_overlaps(self):
        from woodcam_editor.domain import exact_duplicate_entity_groups

        document = VectorDocument.create_default()
        original = path(document, (Vec2(0, 0), Vec2(10, 0)), id="original")
        duplicate_a = path(document, (Vec2(10, 0), Vec2(0, 0)), id="duplicate-a")
        duplicate_b = path(document, (Vec2(0, 0), Vec2(10, 0)), id="duplicate-b")
        partial = path(document, (Vec2(5, 0), Vec2(15, 0)), id="partial")
        document.add_entities(
            (original, duplicate_a, duplicate_b, partial),
            bump_revision=False,
        )

        self.assertEqual(
            exact_duplicate_entity_groups(document),
            (("duplicate-a", "duplicate-b", "original"),),
        )
        self.assertEqual(
            exact_duplicate_entity_groups(
                document,
                entity_ids=("original", "duplicate-b", "partial"),
            ),
            (("duplicate-b", "original"),),
        )
        self.assertEqual(
            exact_duplicate_entity_groups(document, entity_ids=()),
            (),
        )

    def test_adjacent_closed_pieces_are_touching_not_branched_or_crossing(self):
        from woodcam_editor.domain import entities_have_boundary_only_contact

        document = VectorDocument.create_default()
        rectangles = (
            path(document, (Vec2(0, 0), Vec2(10, 0), Vec2(10, 10), Vec2(0, 10)), True, "a"),
            path(document, (Vec2(10, 0), Vec2(20, 0), Vec2(20, 10), Vec2(10, 10)), True, "b"),
            path(document, (Vec2(0, 10), Vec2(10, 10), Vec2(10, 20), Vec2(0, 20)), True, "c"),
            path(document, (Vec2(10, 10), Vec2(20, 10), Vec2(20, 20), Vec2(10, 20)), True, "d"),
        )
        document.add_entities(rectangles, bump_revision=False)

        report = validate_document(document)
        self.assertTrue(entities_have_boundary_only_contact(document, "a", "b"))
        self.assertTrue(report.by_code("TOUCHING_CONTOURS"))
        self.assertFalse(report.by_code("BRANCH_NODE"))
        self.assertFalse(report.by_code("CONTOUR_INTERSECTION"))

    def test_overlapping_closed_pieces_remain_intersection_blockers(self):
        document = VectorDocument.create_default()
        first = path(
            document,
            (Vec2(0, 0), Vec2(10, 0), Vec2(10, 10), Vec2(0, 10)),
            True,
            "first",
        )
        overlap = path(
            document,
            (Vec2(5, 5), Vec2(15, 5), Vec2(15, 15), Vec2(5, 15)),
            True,
            "overlap",
        )
        document.add_entities((first, overlap), bump_revision=False)

        report = validate_document(document)
        self.assertTrue(report.by_code("CONTOUR_INTERSECTION"))
        self.assertFalse(report.by_code("TOUCHING_CONTOURS"))

    def test_open_line_copies_covered_by_closed_contours_are_safe_overlines(self):
        from woodcam_editor.domain import redundant_open_overline_entity_ids

        document = VectorDocument.create_default()
        rectangle = path(
            document,
            (Vec2(0, 0), Vec2(10, 0), Vec2(10, 10), Vec2(0, 10)),
            True,
            "rectangle",
        )
        copied_sides = tuple(
            path(
                document,
                (span.end, span.start),
                id="copy-%d" % index,
            )
            for index, span in enumerate(rectangle.spans)
        )
        unrelated = path(
            document,
            (Vec2(20, 0), Vec2(30, 0)),
            id="unrelated",
        )
        document.add_entities(
            (rectangle,) + copied_sides + (unrelated,),
            bump_revision=False,
        )

        self.assertEqual(
            set(redundant_open_overline_entity_ids(document)),
            {entity.id for entity in copied_sides},
        )
        self.assertEqual(
            redundant_open_overline_entity_ids(
                document,
                entity_ids=("rectangle", "copy-0", "unrelated"),
            ),
            ("copy-0",),
        )
        report = validate_document(document)
        overline_issues = report.by_code("REDUNDANT_OPEN_OVERLINES")
        self.assertEqual(len(overline_issues), 1)
        self.assertEqual(
            set(overline_issues[0].entity_ids),
            {entity.id for entity in copied_sides},
        )
        open_issue_ids = {
            issue.entity_ids[0]
            for issue in report.by_code("OPEN_PATH")
        }
        self.assertEqual(open_issue_ids, {"unrelated"})

    def test_piece_inner_outside_is_blocker(self):
        document = VectorDocument.create_default()
        outer = path(document, (Vec2(0, 0), Vec2(20, 0), Vec2(20, 20), Vec2(0, 20)), True, "outer")
        outside = CircleEntity(document.active_layer_id, Vec2(40, 40), 2, id="hole")
        document.add_entities((outer, outside), bump_revision=False)
        document.add_pieces((Piece2D("P", outer.id, (outside.id,), id="piece"),), bump_revision=False)
        report = validate_document(document)
        self.assertEqual(len(report.by_code("INNER_OUTSIDE_PIECE")), 1)


if __name__ == "__main__":
    unittest.main()
