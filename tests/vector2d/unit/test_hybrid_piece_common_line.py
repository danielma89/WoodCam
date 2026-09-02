import unittest

from operations import build_global_cut_plan_moves
from woodcam_editor.application.common_line import CommonLineContour
from woodcam_editor.application.global_cut_plan import (
    GlobalCutTrail,
    PhysicalCutSegment,
    PhysicalSegmentKind,
    TabReleaseMode,
    _route_piece_trails,
    _schedule_per_piece_common_line_depth,
    build_global_cut_plan,
)
from woodcam_editor.domain.primitives import Vec2


def rectangle(owner_id, min_x, min_y, max_x, max_y):
    return CommonLineContour(
        owner_id,
        ((min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y)),
    )


def hybrid(contours, **overrides):
    options = dict(
        depths=(5.0, 10.0, 15.0, 15.5),
        through_depth=15.0,
        strategy="hybrid_stability",
        start_xy=(0.0, 0.0),
    )
    options.update(overrides)
    return build_global_cut_plan(contours, **options)


def operations_at(plan, depth):
    return [operation for operation in plan.operations if operation.depth == depth]


class HybridPieceCommonLineTests(unittest.TestCase):
    def assert_physical_once(self, plan, depth):
        hits = {}
        for operation in operations_at(plan, depth):
            for segment_id in operation.segment_ids:
                hits[segment_id] = hits.get(segment_id, 0) + 1
        self.assertEqual(set(hits), {segment.segment_id for segment in plan.segments})
        self.assertTrue(all(count == 1 for count in hits.values()))
        self.assertEqual(
            plan.metrics_for_depth(depth).duplicate_physical_segment_count,
            0,
        )

    def test_t01_two_rectangles_cut_four_then_three_segments(self):
        plan = hybrid(
            (rectangle("A", 0, 0, 10, 10), rectangle("B", 10, 0, 20, 10))
        )
        first = operations_at(plan, 5.0)
        self.assertEqual([operation.executing_owner_id for operation in first], ["A", "B"])
        self.assertEqual([len(operation.segment_ids) for operation in first], [4, 3])
        shared_id = next(
            segment.segment_id
            for segment in plan.segments
            if segment.kind == PhysicalSegmentKind.SHARED
        )
        self.assertEqual(
            sum(shared_id in operation.segment_ids for operation in first),
            1,
        )
        self.assert_physical_once(plan, 5.0)

    def test_t02_three_pieces_reuse_both_divisions(self):
        plan = hybrid(
            (
                rectangle("A", 0, 0, 10, 10),
                rectangle("B", 10, 0, 20, 10),
                rectangle("C", 20, 0, 30, 10),
            )
        )
        first = operations_at(plan, 5.0)
        self.assertEqual([len(operation.segment_ids) for operation in first], [4, 3, 3])
        self.assertEqual(plan.metrics_for_depth(5.0).shared_segments_reused, 2)
        self.assert_physical_once(plan, 5.0)

    def test_t03_grid_2x2_has_complete_coverage_without_duplicates(self):
        plan = hybrid(
            (
                rectangle("A", 0, 10, 10, 20),
                rectangle("B", 10, 10, 20, 20),
                rectangle("C", 0, 0, 10, 10),
                rectangle("D", 10, 0, 20, 10),
            )
        )
        self.assertEqual(plan.metrics_for_depth(5.0).shared_segments_reused, 4)
        self.assertEqual(plan.metrics_for_depth(5.0).fragmented_piece_count, 0)
        self.assert_physical_once(plan, 5.0)
        for owner in ("A", "B", "C", "D"):
            self.assertEqual(
                set(plan.covered_segment_ids_for_piece(owner, 5.0)),
                {
                    segment.segment_id
                    for segment in plan.segments
                    if owner in segment.owner_ids
                },
            )

    def test_t04_l_piece_leaves_open_trails_without_artificial_closure(self):
        contours = (
            CommonLineContour(
                "L",
                ((0, 0), (20, 0), (20, 10), (10, 10), (10, 20), (0, 20)),
            ),
            rectangle("A", 20, 0, 30, 10),
            rectangle("B", 10, 10, 20, 20),
        )
        plan = hybrid(contours)
        trail_by_id = {trail.trail_id: trail for trail in plan.trails}
        later = [
            operation
            for operation in operations_at(plan, 5.0)
            if operation.executing_owner_id != "L"
        ]
        self.assertTrue(later)
        self.assertTrue(all(not trail_by_id[operation.trail_id].closed for operation in later))
        moves = build_global_cut_plan_moves(plan, 8.0)
        for operation in later:
            operation_moves = [
                move
                for move in moves
                if move.get("cut_operation_id") == operation.operation_id
            ]
            self.assertFalse(any(move.get("profile_loop") for move in operation_moves))
            self.assertTrue(all(not move.get("physical_trail_closed") for move in operation_moves))

    def test_t05_piece_with_two_shared_edges_done_cuts_only_two_remaining_edges(self):
        plan = hybrid(
            (
                rectangle("A", 0, 10, 10, 20),
                rectangle("B", 10, 10, 20, 20),
                rectangle("C", 0, 0, 10, 10),
                rectangle("D", 10, 0, 20, 10),
            )
        )
        final_piece = operations_at(plan, 5.0)[-1]
        self.assertEqual(len(final_piece.segment_ids), 2)
        owned_shared = {
            segment.segment_id
            for segment in plan.segments
            if final_piece.executing_owner_id in segment.owner_ids
            and segment.kind == PhysicalSegmentKind.SHARED
        }
        self.assertTrue(owned_shared)
        self.assertTrue(
            owned_shared.isdisjoint(final_piece.segment_ids)
        )

    def test_t06_partial_common_line_removes_only_the_overlap(self):
        plan = hybrid(
            (
                rectangle("A", 0, 0, 20, 10),
                rectangle("B", 20, 2, 30, 8),
            )
        )
        shared = [
            segment for segment in plan.segments
            if segment.kind == PhysicalSegmentKind.SHARED
        ]
        self.assertEqual(len(shared), 1)
        self.assertAlmostEqual(shared[0].length, 6.0)
        second = operations_at(plan, 5.0)[1]
        self.assertNotIn(shared[0].segment_id, second.segment_ids)
        self.assertAlmostEqual(
            plan.metrics_for_depth(5.0).shared_cut_savings,
            6.0,
        )

    def test_t07_scheduler_penalizes_fragmented_candidate_before_distance(self):
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
        scheduled, _trails, _coverage, _end = _schedule_per_piece_common_line_depth(
            segments, 5.0, 0, Vec2(0, 0), 0.02
        )
        owner_order = []
        for _trail, _route, owner in scheduled:
            if not owner_order or owner_order[-1] != owner:
                owner_order.append(owner)
        self.assertEqual(owner_order, ["A", "C", "B"])
        self.assertEqual(sum(owner == "B" for _trail, _route, owner in scheduled), 2)

    def test_t08_equal_fragmentation_prefers_local_next_piece(self):
        plan = hybrid(
            (
                rectangle("A", 0, 0, 10, 10),
                rectangle("B", 20, 0, 30, 10),
                rectangle("C", 1000, 0, 1010, 10),
            )
        )
        self.assertEqual(
            [operation.executing_owner_id for operation in operations_at(plan, 5.0)],
            ["A", "B", "C"],
        )

    def test_t09_reversible_open_trail_uses_nearest_endpoint(self):
        trail = GlobalCutTrail(
            "neutral-open",
            (Vec2(0, 0), Vec2(100, 0)),
            ("shared",),
            (False,),
            PhysicalSegmentKind.SHARED,
            ("A", "B"),
            True,
        )
        routed = _route_piece_trails((trail,), Vec2(100, 0))
        self.assertTrue(routed[0][1].reverse)
        self.assertEqual(routed[0][1].start, Vec2(100, 0))

    def test_t10_done_state_is_independent_for_every_depth(self):
        plan = hybrid(
            (rectangle("A", 0, 0, 10, 10), rectangle("B", 10, 0, 20, 10))
        )
        shared_id = next(
            segment.segment_id
            for segment in plan.segments
            if segment.kind == PhysicalSegmentKind.SHARED
        )
        hits = [
            item for item in plan.segment_depth_coverage
            if item.segment_id == shared_id
        ]
        self.assertEqual([item.depth for item in hits], [5.0, 10.0, 15.0, 15.5])

    def test_t11_other_owner_cut_counts_as_complete_piece_coverage(self):
        plan = hybrid(
            (rectangle("A", 0, 0, 10, 10), rectangle("B", 10, 0, 20, 10))
        )
        shared = next(
            segment for segment in plan.segments
            if segment.kind == PhysicalSegmentKind.SHARED
        )
        executing = next(
            item.executing_owner_id
            for item in plan.segment_depth_coverage
            if item.segment_id == shared.segment_id and item.depth == 5.0
        )
        other = next(owner for owner in shared.owner_ids if owner != executing)
        self.assertIn(
            shared.segment_id,
            plan.covered_segment_ids_for_piece(other, 5.0),
        )
        self.assertFalse(
            any(
                operation.executing_owner_id == other
                and shared.segment_id in operation.segment_ids
                for operation in operations_at(plan, 5.0)
            )
        )

    def test_t12_all_depth_metrics_report_zero_physical_duplicates(self):
        plan = hybrid(
            (
                rectangle("A", 0, 0, 10, 10),
                rectangle("B", 10, 0, 20, 10),
                rectangle("C", 20, 0, 30, 10),
            )
        )
        self.assertTrue(
            all(
                metric.duplicate_physical_segment_count == 0
                for metric in plan.route_metrics
            )
        )
        for depth in plan.depths:
            self.assert_physical_once(plan, depth)

    def test_t13_per_piece_signature_ignores_hybrid_development_flag(self):
        contours = (rectangle("A", 0, 0, 10, 10), rectangle("B", 20, 0, 30, 10))
        first = build_global_cut_plan(
            contours, depths=(5.0, 10.0), strategy="per_piece",
            hybrid_intermediate_mode="fast",
        )
        second = build_global_cut_plan(
            contours, depths=(5.0, 10.0), strategy="per_piece",
            hybrid_intermediate_mode="per_piece_common_line",
        )
        self.assertEqual(first.operations, second.operations)

    def test_t14_global_by_depth_signature_is_preserved(self):
        contours = (rectangle("A", 0, 0, 10, 10), rectangle("B", 10, 0, 20, 10))
        first = build_global_cut_plan(
            contours, depths=(5.0, 10.0), strategy="global_by_depth",
            hybrid_intermediate_mode="fast",
        )
        second = build_global_cut_plan(
            contours, depths=(5.0, 10.0), strategy="global_by_depth",
            hybrid_intermediate_mode="per_piece_common_line",
        )
        self.assertEqual(first.operations, second.operations)

    def test_t15_through_layers_keep_current_stability_route(self):
        contours = (rectangle("A", 0, 0, 10, 10), rectangle("B", 10, 0, 20, 10))
        old = hybrid(contours, hybrid_intermediate_mode="fast")
        new = hybrid(contours)
        old_final = tuple(
            operation for operation in old.operations
            if operation.depth >= 15.0
        )
        new_final = tuple(
            operation for operation in new.operations
            if operation.depth >= 15.0
        )
        signature = lambda operations: tuple(
            (
                operation.trail_id,
                operation.segment_ids,
                operation.owner_ids,
                operation.depth,
                operation.phase,
                operation.routing_mode,
            )
            for operation in operations
        )
        self.assertEqual(signature(old_final), signature(new_final))
        self.assertTrue(all(operation.routing_mode == "stability" for operation in new_final))

    def test_t16_tab_release_is_byte_for_byte_unchanged(self):
        options = dict(
            tabs_enabled=True,
            tab_count=3,
            tab_width=10.0,
            tab_thickness=3.0,
            tool_diameter=4.0,
            release_mode=TabReleaseMode.AUTOMATIC_RELEASE,
        )
        contours = (rectangle("A", 0, 0, 100, 60),)
        old = hybrid(contours, hybrid_intermediate_mode="fast", **options)
        new = hybrid(contours, **options)
        self.assertEqual(old.tab_release_operations, new.tab_release_operations)
        old_release_moves = [
            move for move in build_global_cut_plan_moves(old, 8.0)
            if move.get("tab_release")
        ]
        new_release_moves = [
            move for move in build_global_cut_plan_moves(new, 8.0)
            if move.get("tab_release")
        ]
        self.assertEqual(old_release_moves, new_release_moves)

    def test_complex_seven_piece_layout_is_local_complete_and_edge_disjoint(self):
        contours = (
            CommonLineContour(
                "L",
                ((0, 0), (20, 0), (20, 10), (10, 10), (10, 20), (0, 20)),
            ),
            rectangle("A", 20, 0, 30, 10),
            rectangle("B", 10, 10, 20, 20),
            rectangle("C", 30, 2, 40, 8),
            rectangle("D", 40, 2, 50, 8),
            rectangle("E", 50, 2, 60, 8),
            rectangle("F", 60, 2, 90, 8),
        )
        new = hybrid(contours)
        old = hybrid(contours, hybrid_intermediate_mode="fast")
        self.assert_physical_once(new, 5.0)
        metric = new.metrics_for_depth(5.0)
        self.assertGreaterEqual(metric.shared_segments_reused, 6)
        self.assertLessEqual(metric.fragmented_piece_count, 1)
        self.assertEqual(metric.duplicate_physical_segment_count, 0)
        # The experiment optimizes visual locality, not an absolute promise of
        # shorter rapid travel than the old global FAST route.
        self.assertLessEqual(metric.long_rapid_count, old.metrics_for_depth(5.0).long_rapid_count)


if __name__ == "__main__":
    unittest.main()
