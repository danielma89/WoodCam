import unittest

from operations import build_global_cut_plan_moves
from woodcam_editor.application.common_line import CommonLineContour
from woodcam_editor.application.global_cut_plan import (
    CutDepthStrategy,
    PhysicalCutSegment,
    PhysicalSegmentKind,
    _schedule_piece_bidirectional_depths,
    build_global_cut_plan,
)
from woodcam_editor.domain.primitives import Vec2


def rectangle(owner_id, min_x, min_y, max_x, max_y):
    return CommonLineContour(
        owner_id,
        ((min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y)),
    )


def experimental(contours, **overrides):
    options = dict(
        depths=(5.0, 10.0, 15.5),
        through_depth=15.0,
        strategy="hybrid_piece_bidirectional",
        start_xy=(0.0, 0.0),
    )
    options.update(overrides)
    return build_global_cut_plan(contours, **options)


class HybridPieceBidirectionalTests(unittest.TestCase):
    def test_all_depths_direct_keeps_every_depth_in_the_same_piece_visit(self):
        plan = build_global_cut_plan(
            (
                rectangle("A", 0, 0, 80, 40),
                rectangle("B", 80, 0, 160, 40),
            ),
            depths=(5.0, 10.0, 15.5),
            through_depth=15.0,
            strategy="piece_bidirectional",
            start_xy=(0.0, 0.0),
        )
        operations = [
            operation
            for operation in plan.operations
            if operation.routing_mode == "piece_bidirectional"
        ]
        self.assertEqual(
            [
                (operation.executing_owner_id, operation.depth)
                for operation in operations
            ],
            [
                ("A", 5.0),
                ("A", 10.0),
                ("A", 15.5),
                ("B", 5.0),
                ("B", 10.0),
                ("B", 15.5),
            ],
        )
        self.assertFalse(
            any(
                operation.routing_mode == "final_sheet_pass"
                for operation in plan.operations
            )
        )
        b_operations = [
            operation
            for operation in operations
            if operation.executing_owner_id == "B"
        ]
        self.assertEqual(
            [operation.reverse_trail for operation in b_operations],
            [False, True, False],
        )
        moves = build_global_cut_plan_moves(plan, 8.0)
        b_operation_ids = {operation.operation_id for operation in b_operations}
        b_entries = [
            move
            for move in moves
            if move.get("type") == "rapid"
            and move.get("x") is not None
            and move.get("cut_operation_id") in b_operation_ids
        ]
        self.assertEqual(len(b_entries), 1)

    def test_strategy_is_explicit_and_does_not_replace_existing_hybrid(self):
        self.assertEqual(
            CutDepthStrategy.HYBRID_PIECE_BIDIRECTIONAL.value,
            "hybrid_piece_bidirectional",
        )
        contours = (
            rectangle("A", 0, 0, 80, 40),
            rectangle("B", 80, 0, 160, 40),
        )
        existing = build_global_cut_plan(
            contours,
            depths=(5.0, 10.0, 15.5),
            through_depth=15.0,
            strategy="hybrid_stability",
        )
        tested = experimental(contours)
        self.assertNotEqual(existing.strategy, tested.strategy)
        self.assertTrue(
            all(
                operation.routing_mode != "piece_bidirectional"
                for operation in existing.operations
            )
        )

    def test_intermediate_sequence_stays_on_piece_and_only_open_paths_reverse(self):
        plan = experimental(
            (
                rectangle("A", 0, 0, 80, 40),
                rectangle("B", 80, 0, 160, 40),
                rectangle("C", 160, 0, 240, 40),
            )
        )
        intermediate = [
            operation
            for operation in plan.operations
            if operation.routing_mode == "piece_bidirectional"
        ]
        self.assertEqual(
            [(operation.executing_owner_id, operation.depth) for operation in intermediate],
            [("A", 5.0), ("A", 10.0), ("B", 5.0), ("B", 10.0), ("C", 5.0), ("C", 10.0)],
        )
        self.assertEqual(
            [operation.reverse_trail for operation in intermediate],
            [False, False, False, True, False, True],
        )
        self.assertEqual(
            [len(operation.segment_ids) for operation in intermediate],
            [4, 4, 3, 3, 3, 3],
        )

    def test_open_remainder_goes_out_at_z1_and_returns_on_same_edges_at_z2(self):
        plan = experimental(
            (
                rectangle("A", 0, 0, 80, 40),
                rectangle("B", 80, 0, 160, 40),
            )
        )
        trails = {trail.trail_id: trail for trail in plan.trails}
        b_operations = [
            operation
            for operation in plan.operations
            if operation.executing_owner_id == "B"
            and operation.routing_mode == "piece_bidirectional"
        ]
        self.assertEqual(len(b_operations), 2)
        first = trails[b_operations[0].trail_id].oriented(
            reverse=b_operations[0].reverse_trail,
            start_edge_index=b_operations[0].start_edge_index,
        )
        second = trails[b_operations[1].trail_id].oriented(
            reverse=b_operations[1].reverse_trail,
            start_edge_index=b_operations[1].start_edge_index,
        )
        self.assertFalse(first.closed)
        self.assertEqual(first.points, tuple(reversed(second.points)))
        self.assertEqual(first.segment_ids, tuple(reversed(second.segment_ids)))
        shared = {
            segment.segment_id
            for segment in plan.segments
            if segment.kind == PhysicalSegmentKind.SHARED
        }
        self.assertTrue(shared)
        self.assertTrue(shared.isdisjoint(first.segment_ids))

    def test_shared_edges_are_done_once_independently_at_both_depths(self):
        plan = experimental(
            (
                rectangle("A", 0, 0, 80, 40),
                rectangle("B", 80, 0, 160, 40),
                rectangle("C", 160, 0, 240, 40),
            )
        )
        for depth in (5.0, 10.0, 15.5):
            hits = {}
            for operation in plan.operations:
                if operation.depth != depth:
                    continue
                for segment_id in operation.segment_ids:
                    hits[segment_id] = hits.get(segment_id, 0) + 1
            self.assertEqual(set(hits), {segment.segment_id for segment in plan.segments})
            self.assertTrue(all(count == 1 for count in hits.values()))

    def test_tabs_do_not_raise_on_non_through_bidirectional_layers(self):
        plan = experimental(
            (
                rectangle("A", 0, 0, 80, 40),
                rectangle("B", 80, 0, 160, 40),
            ),
            tabs_enabled=True,
            tab_count=3,
            tab_width=8.0,
            tab_thickness=3.0,
            best_fixation=True,
            tool_diameter=6.0,
        )
        moves = build_global_cut_plan_moves(plan, 8.0)
        intermediate = [
            move
            for move in moves
            if move.get("routing_mode") == "piece_bidirectional"
            and move.get("depth_pass") in {5.0, 10.0}
        ]
        self.assertTrue(intermediate)
        # Tabs already retain the work during intermediate layers; they are
        # not created only after the piece may have moved on the second pass.
        self.assertTrue(any(move.get("tab") for move in intermediate))
        self.assertFalse(
            any(
                move.get("tab") and float(move.get("z", 0.0)) < -10.0
                for move in intermediate
            )
        )

    def test_one_visit_has_no_retract_between_outbound_and_return_pass(self):
        plan = experimental(
            (
                rectangle("A", 0, 0, 80, 40),
                rectangle("B", 80, 0, 160, 40),
            )
        )
        moves = build_global_cut_plan_moves(plan, 8.0)
        operations = [
            operation
            for operation in plan.operations
            if operation.executing_owner_id == "B"
            and operation.routing_mode == "piece_bidirectional"
        ]
        first_indexes = [
            index
            for index, move in enumerate(moves)
            if move.get("cut_operation_id") == operations[0].operation_id
        ]
        second_indexes = [
            index
            for index, move in enumerate(moves)
            if move.get("cut_operation_id") == operations[1].operation_id
        ]
        between = moves[max(first_indexes) + 1:min(second_indexes)]
        self.assertFalse(any(move["type"] == "rapid" for move in between))
        second_moves = [moves[index] for index in second_indexes]
        self.assertEqual(second_moves[0]["type"], "feed_plunge")
        self.assertEqual(second_moves[0]["z"], -10.0)

    def test_smooth_ramp_is_one_continuous_slope_ending_at_entry(self):
        plan = experimental(
            (
                rectangle("A", 0, 0, 80, 40),
                rectangle("B", 80, 0, 160, 40),
            )
        )
        moves = build_global_cut_plan_moves(
            plan,
            8.0,
            ramp_length=20.0,
            ramp_type="smooth",
        )
        intermediate_operations = [
            operation
            for operation in plan.operations
            if operation.routing_mode == "piece_bidirectional"
        ]
        trails = {trail.trail_id: trail for trail in plan.trails}
        for operation in intermediate_operations:
            all_operation_moves = [
                move
                for move in moves
                if move.get("cut_operation_id") == operation.operation_id
            ]
            operation_moves = [
                move
                for move in all_operation_moves
                if not move.get("cleared_path_link")
            ]
            ramps = [move for move in operation_moves if move.get("entry_ramp")]
            self.assertTrue(ramps, operation.operation_id)
            self.assertTrue(
                all(
                    move.get("ramp_effective_type") == "smooth"
                    and move.get("ramp_geometry")
                    == "physical_trail_single_slope"
                    for move in ramps
                ),
                operation.operation_id,
            )
            first_ramp_index = all_operation_moves.index(ramps[0])
            previous_xy = next(
                (
                    (move["x"], move["y"])
                    for move in reversed(all_operation_moves[:first_ramp_index])
                    if move.get("x") is not None and move.get("y") is not None
                ),
                None,
            )
            self.assertEqual(
                previous_xy,
                (
                    ramps[0]["ramp_origin_x"],
                    ramps[0]["ramp_origin_y"],
                ),
            )
            self.assertNotEqual(
                previous_xy,
                (ramps[-1]["x"], ramps[-1]["y"]),
                "Suave deve partir adiante e descer uma vez até a entrada.",
            )
            self.assertTrue(
                all(
                    float(current["z"]) < float(previous["z"]) + 1.0e-9
                    for previous, current in zip(ramps, ramps[1:])
                )
            )
            self.assertEqual(ramps[-1]["z"], -operation.depth)
            cut_moves = [move for move in operation_moves if move["type"] == "feed_cut"]
            self.assertTrue(cut_moves)
            prepared = trails[operation.trail_id].oriented(
                reverse=operation.reverse_trail,
                start_edge_index=operation.start_edge_index,
            )
            self.assertEqual(
                (ramps[-1]["x"], ramps[-1]["y"]),
                prepared.points[0].to_tuple(),
            )
            pass_index = plan.depths.index(operation.depth)
            already_cleared_depth = (
                0.0 if pass_index == 0 else plan.depths[pass_index - 1]
            )
            self.assertFalse(
                any(
                    move["type"] == "feed_plunge"
                    and float(move.get("z", 0.0))
                    < -float(already_cleared_depth) - 1.0e-7
                    for move in operation_moves
                ),
                "A aproximação pode descer no kerf vazio, nunca no MDF ainda inteiro.",
            )

    def test_zigzag_ramp_keeps_the_explicit_out_and_back_motion(self):
        plan = experimental(
            (
                rectangle("A", 0, 0, 80, 40),
                rectangle("B", 80, 0, 160, 40),
            )
        )
        moves = build_global_cut_plan_moves(
            plan,
            8.0,
            ramp_length=20.0,
            ramp_type="zigzag",
        )
        ramps = [move for move in moves if move.get("entry_ramp")]
        self.assertTrue(ramps)
        self.assertTrue(
            all(
                move.get("ramp_effective_type") == "zigzag"
                and move.get("ramp_geometry")
                == "physical_trail_out_and_back"
                for move in ramps
            )
        )

    def test_spiral_is_used_on_safe_closed_profile_and_falls_back_on_open_trail(self):
        plan = build_global_cut_plan(
            (
                rectangle("A", 0, 0, 80, 40),
                rectangle("B", 80, 0, 160, 40),
            ),
            depths=(5.0, 10.0, 15.0),
            through_depth=15.0,
            strategy="piece_bidirectional",
        )
        moves = build_global_cut_plan_moves(
            plan,
            8.0,
            ramp_length=25.0,
            ramp_type="spiral",
            tool_diameter=4.0,
        )
        ramps = [move for move in moves if move.get("entry_ramp")]
        self.assertTrue(
            any(
                move.get("ramp_geometry") == "validated_tangent_spiral"
                and move.get("ramp_effective_type") == "spiral"
                for move in ramps
            )
        )
        self.assertTrue(
            any(
                move.get("ramp_geometry") == "physical_trail_out_and_back"
                and move.get("ramp_fallback")
                for move in ramps
            )
        )

    def test_first_piece_is_anchored_to_configured_work_zero(self):
        plan = experimental(
            (
                rectangle("far", 300, 0, 380, 40),
                rectangle("near", 10, 0, 90, 40),
                rectangle("middle", 160, 0, 240, 40),
            ),
            start_xy=(0.0, 0.0),
        )
        first = next(
            operation
            for operation in plan.operations
            if operation.routing_mode == "piece_bidirectional"
        )
        self.assertEqual(first.executing_owner_id, "near")

    def test_intermediate_scheduler_prefers_near_fragmented_piece(self):
        def segment(segment_id, start, end, owners, kind="external"):
            return PhysicalCutSegment(
                segment_id,
                Vec2(*start),
                Vec2(*end),
                PhysicalSegmentKind(kind),
                tuple(owners),
            )

        segments = (
            segment("shared-low", (0, 0), (10, 0), ("A", "B"), "shared"),
            segment("a-right", (10, 0), (10, 10), ("A",)),
            segment("shared-high", (10, 10), (0, 10), ("A", "B"), "shared"),
            segment("a-left", (0, 10), (0, 0), ("A",)),
            segment("b-right", (10, 0), (10, 10), ("B",)),
            segment("b-left", (0, 10), (0, 0), ("B",)),
            segment("c-low", (100, 0), (110, 0), ("C",)),
            segment("c-right", (110, 0), (110, 10), ("C",)),
            segment("c-high", (110, 10), (100, 10), ("C",)),
            segment("c-left", (100, 10), (100, 0), ("C",)),
        )
        scheduled, _trails, _coverage, _end = _schedule_piece_bidirectional_depths(
            segments,
            (5.0, 10.0),
            Vec2(0.0, 0.0),
            0.02,
        )
        owner_order = []
        for _trail, _depth, _route, owner in scheduled:
            if not owner_order or owner_order[-1] != owner:
                owner_order.append(owner)
        self.assertEqual(owner_order, ["A", "B", "C"])

    def test_best_fixation_does_not_create_intermediate_mid_edge_entry(self):
        plan = experimental(
            (
                rectangle("A", 0, 0, 80, 40),
                rectangle("B", 80, 0, 160, 40),
            ),
            tabs_enabled=True,
            tab_count=3,
            tab_width=8.0,
            tab_thickness=3.0,
            best_fixation=True,
            tool_diameter=6.0,
        )
        trails = {trail.trail_id: trail for trail in plan.trails}
        original_corners = {
            "A": {(0.0, 0.0), (80.0, 0.0), (80.0, 40.0), (0.0, 40.0)},
            "B": {(80.0, 0.0), (160.0, 0.0), (160.0, 40.0), (80.0, 40.0)},
        }
        first_by_owner = {}
        for operation in plan.operations:
            if operation.routing_mode != "piece_bidirectional":
                continue
            first_by_owner.setdefault(operation.executing_owner_id, operation)
        for owner, operation in first_by_owner.items():
            prepared = trails[operation.trail_id].oriented(
                reverse=operation.reverse_trail,
                start_edge_index=operation.start_edge_index,
            )
            self.assertIn(prepared.points[0].to_tuple(), original_corners[owner])
        self.assertTrue(
            all(
                operation.routing_mode == "final_sheet_pass"
                for operation in plan.operations
                if operation.depth == 15.5
            )
        )

    def test_piece_switch_retracts_instead_of_recutting_done_edge_as_connector(self):
        plan = experimental(
            (
                rectangle("A", 0, 0, 80, 40),
                rectangle("B", 80, 0, 160, 40),
                rectangle("C", 160, 0, 240, 40),
            )
        )
        moves = build_global_cut_plan_moves(plan, 8.0)
        intermediate = [
            move
            for move in moves
            if move.get("routing_mode") == "piece_bidirectional"
        ]
        self.assertFalse(any(move.get("cleared_path_link") for move in intermediate))
        xy_entries = [
            move
            for move in intermediate
            if move["type"] == "rapid"
            and move.get("x") is not None
            and move.get("y") is not None
        ]
        self.assertEqual(
            [(move["x"], move["y"]) for move in xy_entries],
            [(0.0, 0.0), (80.0, 0.0), (160.0, 0.0)],
        )
        for entry in xy_entries:
            entry_index = moves.index(entry)
            self.assertGreater(entry_index, 0)
            if entry is not xy_entries[0]:
                previous = moves[entry_index - 1]
                self.assertEqual(previous["type"], "rapid")
                self.assertIsNone(previous["x"])
                self.assertEqual(previous["z"], 8.0)

    def test_last_pass_is_separate_and_tab_release_stays_after_it(self):
        plan = experimental(
            (rectangle("A", 0, 0, 100, 60),),
            release_mode="automatic_release",
            tabs_enabled=True,
            tab_count=3,
            tab_width=10.0,
            tab_thickness=3.0,
            best_fixation=True,
            tool_diameter=4.0,
        )
        final_operations = [
            operation for operation in plan.operations if operation.depth == 15.5
        ]
        self.assertTrue(final_operations)
        self.assertTrue(
            all(
                operation.routing_mode == "final_sheet_pass"
                for operation in final_operations
            )
        )
        moves = build_global_cut_plan_moves(plan, 8.0)
        releases = [move for move in moves if move.get("tab_release")]
        self.assertTrue(releases)
        for operation_id in {
            move["tab_release_operation_id"] for move in releases
        }:
            operation_moves = [
                move
                for move in releases
                if move["tab_release_operation_id"] == operation_id
            ]
            self.assertEqual(operation_moves[-1]["type"], "rapid")
            self.assertIsNone(operation_moves[-1]["x"])

    def test_last_pass_never_reuses_its_own_segments_as_connectors(self):
        plan = experimental(
            (
                rectangle("A", 0, 0, 80, 40),
                rectangle("B", 80, 0, 160, 40),
                rectangle("C", 160, 0, 240, 40),
            )
        )
        moves = build_global_cut_plan_moves(
            plan,
            8.0,
            stay_down_max_distance=9.0,
        )
        final_links = [
            move
            for move in moves
            if move.get("cleared_path_link")
            and move.get("link_target_depth_pass") == 15.5
        ]
        self.assertFalse(
            any(move.get("depth_pass") == 15.5 for move in final_links)
        )
        self.assertTrue(
            all(move.get("cleared_path_link_length", 0.0) <= 9.0 for move in final_links)
        )
        self.assertTrue(
            any(
                move["type"] == "rapid"
                and move.get("x") is not None
                and move.get("routing_mode") == "final_sheet_pass"
                for move in moves
            )
        )

    def test_last_pass_keeps_down_only_for_a_near_previous_depth_kerf(self):
        plan = experimental(
            (
                rectangle("A", 0, 0, 5, 5),
                rectangle("B", 5, 0, 10, 5),
                rectangle("C", 10, 0, 15, 5),
            ),
            depths=(1.0, 2.0, 3.0),
            through_depth=3.0,
        )
        moves = build_global_cut_plan_moves(
            plan,
            8.0,
            stay_down_max_distance=9.0,
        )
        final_links = [
            move
            for move in moves
            if move.get("cleared_path_link")
            and move.get("link_target_depth_pass") == 3.0
        ]
        self.assertTrue(final_links)
        self.assertTrue(all(move.get("depth_pass") == 2.0 for move in final_links))
        self.assertTrue(
            all(move.get("cleared_path_link_length") == 5.0 for move in final_links)
        )


if __name__ == "__main__":
    unittest.main()
