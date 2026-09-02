import dataclasses
import unittest

from woodcam_editor.application.common_line import CommonLineContour
from woodcam_editor.application.global_cut_plan import (
    CutDepthStrategy,
    CutPhase,
    GlobalCutPlanError,
    OwnedContour,
    GlobalCutTrail,
    PhysicalCutSegment,
    PhysicalSegmentKind,
    RetentionRules,
    RetentionGraph,
    RetentionTab,
    TabReleaseMode,
    TabRetentionKind,
    build_global_cut_plan,
    optimize_fast_trails,
    _schedule_operations,
    _balanced_waste_tab_placements,
)
from woodcam_editor.domain.primitives import Vec2
from woodcam_editor.domain import PathEntity, VectorDocument
from woodcam_editor.geometry.modifiers import preview_auto_corner_reliefs
from woodcam_editor.adapters.woodcam_geometry import path_points
from operations import (
    build_global_cut_plan_moves,
    compensated_polygon,
    validate_profile_cut_moves,
)


def rectangle(owner_id, min_x, min_y, max_x, max_y):
    return CommonLineContour(
        owner_id,
        (
            (min_x, min_y),
            (max_x, min_y),
            (max_x, max_y),
            (min_x, max_y),
        ),
    )


def plan_for(contours, **overrides):
    options = {
        "depths": (5.0, 10.0, 15.5),
        "strategy": CutDepthStrategy.GLOBAL_BY_DEPTH,
        "tabs_enabled": False,
        "tool_diameter": 6.0,
    }
    options.update(overrides)
    return build_global_cut_plan(contours, **options)


class GlobalCutPlanTests(unittest.TestCase):
    def test_common_line_tolerance_does_not_erase_compensated_relief_arcs(self):
        document = VectorDocument.create_default()
        notched = PathEntity.from_points(
            document.active_layer_id,
            (
                Vec2(0, 0), Vec2(100, 0), Vec2(100, 100), Vec2(0, 100),
                Vec2(0, 70), Vec2(20, 70), Vec2(20, 30), Vec2(0, 30),
            ),
            closed=True,
            id="short-compensated-reliefs",
            metadata={"contour_role": "outer"},
        )

        for kind in ("dogbone", "tbone"):
            with self.subTest(kind=kind):
                relieved = preview_auto_corner_reliefs(
                    (notched,), 3.0, kind=kind
                ).result_entities[0]
                centre_line = compensated_polygon(
                    path_points(relieved),
                    4.0,
                    "outside",
                    common_line_join=True,
                )
                expected_length = sum(
                    Vec2.from_sequence(start).distance_to(
                        Vec2.from_sequence(end)
                    )
                    for start, end in zip(
                        centre_line,
                        centre_line[1:] + centre_line[:1],
                    )
                )
                plan = build_global_cut_plan(
                    (CommonLineContour("A", centre_line),),
                    depths=(5.0,),
                    strategy="per_piece",
                    tabs_enabled=False,
                    tool_diameter=4.0,
                    tolerance=0.2,
                )

                self.assertEqual(len(plan.trails), 1)
                self.assertTrue(plan.trails[0].closed)
                actual_length = sum(segment.length for segment in plan.segments)
                self.assertAlmostEqual(actual_length, expected_length, places=6)
                moves = build_global_cut_plan_moves(
                    plan,
                    8.0,
                    ramp_length=25.0,
                )
                reports = validate_profile_cut_moves(moves)
                self.assertEqual(len(reports), 1)
                self.assertAlmostEqual(reports[0]["coverage_ratio"], 1.0)

    def test_dogbone_and_tbone_do_not_fragment_one_contour_into_fake_entries(self):
        document = VectorDocument.create_default()
        source = PathEntity.from_points(
            document.active_layer_id,
            (Vec2(0, 0), Vec2(100, 0), Vec2(100, 100), Vec2(0, 100)),
            closed=True,
            id="relief-source",
            metadata={"contour_role": "inner"},
        )
        for kind in ("dogbone", "tbone"):
            with self.subTest(kind=kind):
                relieved = preview_auto_corner_reliefs(
                    (source,), 5.0, kind=kind
                ).result_entities[0]
                plan = build_global_cut_plan(
                    (CommonLineContour("A", path_points(relieved)),),
                    depths=(5.0, 10.0, 15.0),
                    strategy="per_piece",
                    tabs_enabled=False,
                    tab_count=4,
                    manual_tab_positions=({"x": 50.0, "y": 0.0},),
                    loose_waste_fixation="disabled",
                    tool_diameter=4.0,
                )

                external = [
                    operation
                    for operation in plan.operations
                    if operation.phase == CutPhase.EXTERNAL
                ]
                self.assertEqual(len(external), 3)
                self.assertEqual(
                    [operation.depth for operation in external],
                    [5.0, 10.0, 15.0],
                )
                self.assertEqual(plan.tabs, ())

    def test_reliefs_do_not_add_start_end_routes_to_a_common_line_network(self):
        document = VectorDocument.create_default()
        notched = PathEntity.from_points(
            document.active_layer_id,
            (
                Vec2(0, 0), Vec2(100, 0), Vec2(100, 100), Vec2(0, 100),
                Vec2(0, 70), Vec2(20, 70), Vec2(20, 30), Vec2(0, 30),
            ),
            closed=True,
            id="notched",
            metadata={"contour_role": "outer"},
        )
        neighbour = rectangle("B", 100, 0, 200, 100)
        for kind in ("dogbone", "tbone"):
            with self.subTest(kind=kind):
                relieved = preview_auto_corner_reliefs(
                    (notched,), 5.0, kind=kind
                ).result_entities[0]
                plan = build_global_cut_plan(
                    (
                        CommonLineContour("A", path_points(relieved)),
                        neighbour,
                    ),
                    depths=(5.0, 10.0, 15.0),
                    strategy="per_piece",
                    tabs_enabled=False,
                    loose_waste_fixation="disabled",
                    tool_diameter=4.0,
                )

                # One shared trail plus one owner-only remainder per part,
                # repeated at three depths. The relief tessellation itself
                # must never become a collection of starts/ends.
                self.assertEqual(len(plan.trails), 3)
                self.assertEqual(len(plan.operations), 9)
                self.assertEqual(
                    sum(
                        segment.kind == PhysicalSegmentKind.SHARED
                        for segment in plan.segments
                    ),
                    1,
                )
                self.assertEqual(plan.tabs, ())
    def test_single_piece_three_depths_and_manual_tab_dimensions(self):
        plan = plan_for(
            (rectangle("A", 0, 0, 100, 60),),
            strategy="per_piece",
            tabs_enabled=True,
            tab_count=3,
            tab_width=4.0,
            tab_thickness=6.0,
        )
        self.assertEqual(plan.strategy, CutDepthStrategy.PER_PIECE)
        self.assertEqual(plan.depths, (5.0, 10.0, 15.5))
        self.assertTrue(plan.tabs)
        self.assertTrue(all(abs(tab.width - 4.0) < 1e-7 for tab in plan.tabs))
        self.assertTrue(all(tab.thickness == 6.0 for tab in plan.tabs))

    def test_explicit_manual_tabs_are_not_supplemented_on_other_pieces(self):
        plan = plan_for(
            (
                rectangle("A", 0, 0, 100, 60),
                rectangle("B", 300, 0, 400, 60),
            ),
            strategy="per_piece",
            tabs_enabled=True,
            tab_count=0,
            tab_width=8.0,
            tab_thickness=3.0,
            manual_tab_positions=({"x": 50.0, "y": 0.0},),
        )

        self.assertEqual(len(plan.tabs), 1)
        self.assertEqual(plan.tabs[0].owner_ids, ("A",))
        self.assertEqual(plan.stability_reports, ())

    def test_manual_common_edge_click_is_one_shared_tab_without_auto_extras(self):
        plan = plan_for(
            (
                rectangle("A", 0, 0, 100, 60),
                rectangle("B", 100, 0, 200, 60),
            ),
            strategy="per_piece",
            tabs_enabled=True,
            tab_count=12,
            tab_width=8.0,
            manual_tab_positions=({"x": 100.0, "y": 30.0},),
        )

        self.assertEqual(len(plan.tabs), 1)
        self.assertEqual(plan.tabs[0].kind, TabRetentionKind.SHARED)
        self.assertEqual(plan.tabs[0].owner_ids, ("A", "B"))

    def test_disabled_tabs_and_disabled_waste_produce_no_retention(self):
        plan = plan_for(
            (
                rectangle("A", 0, 0, 100, 60),
                rectangle("B", 100, 0, 200, 60),
            ),
            tabs_enabled=False,
            tab_count=12,
            tab_width=8.0,
            manual_tab_positions=({"x": 100.0, "y": 30.0},),
            loose_waste_fixation="disabled",
        )

        self.assertEqual(plan.tabs, ())
        self.assertFalse(any(segment.retains_tab for segment in plan.segments))

    def test_per_piece_finishes_all_depths_before_the_next_piece(self):
        plan = plan_for(
            (
                rectangle("A", 0, 0, 100, 60),
                rectangle("B", 300, 0, 400, 60),
            ),
            strategy="per_piece",
            tabs_enabled=False,
        )

        owner_depths = [
            (operation.executing_owner_id, operation.depth)
            for operation in plan.operations
            if operation.phase != CutPhase.INTERNAL
        ]
        self.assertEqual(
            owner_depths,
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

    def test_tab_release_starts_with_the_nearest_topologically_safe_piece(self):
        plan = plan_for(
            (
                rectangle("A", 1000, 0, 1100, 60),
                rectangle("B", 0, 0, 100, 60),
            ),
            strategy="hybrid_piece_bidirectional",
            release_mode="automatic_release",
            tabs_enabled=True,
            tab_count=3,
            tab_width=8.0,
            tab_thickness=3.0,
            tool_diameter=6.0,
            through_depth=15.0,
            start_xy=(0.0, 0.0),
        )

        last_operation = plan.operations[-1]
        last_trail = next(
            trail
            for trail in plan.trails
            if trail.trail_id == last_operation.trail_id
        ).oriented(
            reverse=last_operation.reverse_trail,
            start_edge_index=last_operation.start_edge_index,
        )
        first_release = plan.tab_release_operations[0]
        self.assertEqual(first_release.owner_id, "B")
        self.assertLess(
            last_trail.points[-1].distance_to(first_release.plunge_point),
            100.0,
        )

    def test_two_pieces_total_common_line_once_per_depth(self):
        plan = plan_for(
            (
                rectangle("A", 0, 0, 100, 60),
                rectangle("B", 100, 0, 200, 60),
            )
        )
        shared = [
            segment
            for segment in plan.segments
            if segment.kind == PhysicalSegmentKind.SHARED
        ]
        self.assertEqual(len(shared), 1)
        self.assertEqual(shared[0].owner_ids, ("A", "B"))
        for depth in plan.depths:
            hits = [
                operation
                for operation in plan.operations
                if shared[0].segment_id in operation.segment_ids
                and operation.depth == depth
            ]
            self.assertEqual(len(hits), 1)

    def test_common_line_remains_optional_when_shared_resolution_is_disabled(self):
        plan = build_global_cut_plan(
            (
                rectangle("A", 0, 0, 100, 60),
                rectangle("B", 100, 0, 200, 60),
            ),
            depths=(5.0,),
            strategy="global_by_depth",
            resolve_shared_edges=False,
        )
        self.assertFalse(
            any(
                segment.kind == PhysicalSegmentKind.SHARED
                for segment in plan.segments
            )
        )
        self.assertEqual(len(plan.segments), 8)

    def test_partial_common_line_splits_normal_shared_normal(self):
        plan = plan_for(
            (
                rectangle("A", 0, 0, 20, 10),
                rectangle("B", 20, 2, 30, 8),
            ),
            depths=(5.0,),
        )
        shared = [s for s in plan.segments if s.kind == PhysicalSegmentKind.SHARED]
        right_a = [
            s
            for s in plan.segments
            if s.kind == PhysicalSegmentKind.EXTERNAL
            and s.owner_ids == ("A",)
            and abs(s.start.x - 20.0) < 1e-7
            and abs(s.end.x - 20.0) < 1e-7
        ]
        self.assertEqual([round(s.length, 6) for s in shared], [6.0])
        self.assertEqual(sorted(round(s.length, 6) for s in right_a), [2.0, 2.0])

    def test_grid_2x2_has_four_unambiguous_shared_edges(self):
        plan = plan_for(
            (
                rectangle("A", 0, 10, 10, 20),
                rectangle("B", 10, 10, 20, 20),
                rectangle("C", 0, 0, 10, 10),
                rectangle("D", 10, 0, 20, 10),
            ),
            depths=(5.0,),
        )
        shared = [s for s in plan.segments if s.kind == PhysicalSegmentKind.SHARED]
        self.assertEqual(len(shared), 4)
        self.assertTrue(all(len(segment.owner_ids) == 2 for segment in shared))

    def test_global_by_depth_finishes_entire_sheet_at_each_depth(self):
        plan = plan_for(
            (rectangle("A", 0, 0, 40, 40), rectangle("B", 50, 0, 90, 40)),
            internal_contours=(
                OwnedContour("hole-A", "A", ((10, 10), (20, 10), (20, 20), (10, 20))),
            ),
        )
        depths = [operation.depth for operation in plan.operations]
        self.assertEqual(depths, sorted(depths))
        for depth in plan.depths:
            phases = [op.phase for op in plan.operations if op.depth == depth]
            self.assertEqual(phases, sorted(phases, key=lambda p: (CutPhase.INTERNAL, CutPhase.SHARED, CutPhase.EXTERNAL).index(p)))
        self.assertTrue(
            all(
                operation.routing_mode == "defined"
                and not operation.reverse_trail
                and operation.start_edge_index == 0
                for operation in plan.operations
            )
        )

    def test_per_piece_preserves_all_depths_before_next_piece(self):
        plan = plan_for(
            (rectangle("A", 0, 0, 40, 40), rectangle("B", 50, 0, 90, 40)),
            strategy="per_piece",
        )
        owners = [operation.owner_ids[0] for operation in plan.operations]
        last_a = max(index for index, owner in enumerate(owners) if owner == "A")
        first_b = min(index for index, owner in enumerate(owners) if owner == "B")
        self.assertLess(last_a, first_b)
        self.assertEqual(
            [op.depth for op in plan.operations if op.owner_ids == ("A",)],
            [5.0, 10.0, 15.5],
        )
        self.assertTrue(
            all(operation.routing_mode == "defined" for operation in plan.operations)
        )

    def test_hybrid_final_pass_keeps_safe_phase_order(self):
        plan = plan_for(
            (rectangle("A", 0, 0, 100, 60), rectangle("B", 100, 0, 200, 60)),
            strategy="hybrid_stability",
            internal_contours=(
                OwnedContour("hole-A", "A", ((10, 10), (20, 10), (20, 20), (10, 20))),
            ),
        )
        final = [operation for operation in plan.operations if operation.final_pass]
        phases = [operation.phase for operation in final]
        self.assertEqual(phases, sorted(phases, key=lambda p: (CutPhase.INTERNAL, CutPhase.SHARED, CutPhase.EXTERNAL).index(p)))
        self.assertTrue(all(operation.depth == 15.5 for operation in final))

    def test_hybrid_final_external_order_leaves_elongated_piece_later(self):
        plan = plan_for(
            (
                rectangle("long", 0, 0, 200, 20),
                rectangle("square", 220, 0, 280, 60),
            ),
            strategy="hybrid_stability",
            start_xy=(0.0, 0.0),
        )
        final_external = [
            operation
            for operation in plan.operations
            if operation.final_pass and operation.phase == CutPhase.EXTERNAL
        ]
        self.assertEqual(
            [operation.owner_ids[0] for operation in final_external],
            ["square", "long"],
        )
        self.assertIn(
            final_external[0].operation_id,
            final_external[1].dependencies,
        )
        self.assertTrue(
            all(operation.routing_mode == "stability" for operation in final_external)
        )
        first_index = plan.operations.index(final_external[0])
        second_index = plan.operations.index(final_external[1])
        reordered = list(plan.operations)
        reordered[first_index], reordered[second_index] = (
            reordered[second_index],
            reordered[first_index],
        )
        with self.assertRaisesRegex(
            GlobalCutPlanError,
            "dependência|cadeia híbrida",
        ):
            dataclasses.replace(plan, operations=tuple(reordered)).validate()

    def test_hybrid_intermediate_depths_route_piece_by_piece_without_phase_barrier(self):
        plan = plan_for(
            (
                rectangle("A", 0, 0, 100, 100),
                rectangle("B", 1000, 0, 1100, 100),
                rectangle("C", 2000, 0, 2100, 100),
            ),
            strategy="hybrid_stability",
            start_xy=(0.0, 0.0),
            internal_contours=(
                OwnedContour("iA", "A", ((20, 20), (40, 20), (40, 40), (20, 40))),
                OwnedContour("iB", "B", ((1020, 20), (1040, 20), (1040, 40), (1020, 40))),
                OwnedContour("iC", "C", ((2020, 20), (2040, 20), (2040, 40), (2020, 40))),
            ),
        )
        first = [operation for operation in plan.operations if operation.depth == 5.0]
        second = [operation for operation in plan.operations if operation.depth == 10.0]
        self.assertTrue(
            all(
                operation.routing_mode == "per_piece_common_line"
                for operation in first + second
            )
        )
        # The first layer walks A -> B -> C.  Continuity from that endpoint
        # makes the second layer start at C instead of returning to A/origin.
        self.assertEqual(first[0].owner_ids, ("A",))
        self.assertEqual(first[-1].owner_ids, ("C",))
        self.assertEqual(second[0].owner_ids, ("C",))
        self.assertEqual(second[-1].owner_ids, ("A",))
        # Internal/external are interleaved spatially: no artificial phase wall.
        self.assertNotEqual(
            [operation.phase for operation in first],
            sorted(
                [operation.phase for operation in first],
                key=lambda phase: (
                    CutPhase.INTERNAL,
                    CutPhase.SHARED,
                    CutPhase.EXTERNAL,
                ).index(phase),
            ),
        )

    def test_hybrid_switches_to_stability_at_first_through_depth(self):
        plan = build_global_cut_plan(
            (rectangle("A", 0, 0, 100, 60),),
            depths=(5.0, 10.0, 15.0, 15.5),
            through_depth=15.0,
            strategy="hybrid_stability",
        )
        modes_by_depth = {
            depth: {
                operation.routing_mode
                for operation in plan.operations
                if operation.depth == depth
            }
            for depth in plan.depths
        }
        self.assertEqual(modes_by_depth[5.0], {"per_piece_common_line"})
        self.assertEqual(modes_by_depth[10.0], {"per_piece_common_line"})
        self.assertEqual(modes_by_depth[15.0], {"stability"})
        self.assertEqual(modes_by_depth[15.5], {"stability"})

    def test_fast_route_reverses_open_shared_trail_for_nearest_endpoint(self):
        trail = GlobalCutTrail(
            "shared-test",
            (Vec2(0, 0), Vec2(100, 0)),
            ("shared-segment",),
            (False,),
            PhysicalSegmentKind.SHARED,
            ("A", "B"),
        )
        routed = optimize_fast_trails((trail,), (100.0, 0.0))
        self.assertEqual(len(routed), 1)
        self.assertTrue(routed[0].reverse)
        self.assertEqual(routed[0].start, Vec2(100, 0))
        self.assertEqual(routed[0].end, Vec2(0, 0))

    def test_safe_group_routing_anticipates_next_group_entry(self):
        first = GlobalCutTrail(
            "shared-first",
            (Vec2(0, 0), Vec2(0, 10)),
            ("shared-first-segment",),
            (False,),
            PhysicalSegmentKind.SHARED,
            ("A", "B"),
        )
        second = GlobalCutTrail(
            "shared-second",
            (Vec2(0, 20), Vec2(10, 0)),
            ("shared-second-segment",),
            (False,),
            PhysicalSegmentKind.SHARED,
            ("B", "C"),
        )
        without_lookahead = optimize_fast_trails((first, second), (0, 0))
        with_lookahead = optimize_fast_trails(
            (first, second),
            (0, 0),
            end_targets=(Vec2(0, 20),),
        )
        self.assertEqual(without_lookahead[-1].end, Vec2(10, 0))
        self.assertEqual(with_lookahead[-1].end, Vec2(0, 20))

    def test_final_distance_cannot_advance_retention_critical_piece(self):
        trail_a = GlobalCutTrail(
            "external-A",
            (Vec2(0, 0), Vec2(10, 0)),
            ("segment-A",),
            (False,),
            PhysicalSegmentKind.EXTERNAL,
            ("A",),
        )
        trail_b = GlobalCutTrail(
            "external-B",
            (Vec2(1000, 0), Vec2(1010, 0)),
            ("segment-B",),
            (False,),
            PhysicalSegmentKind.EXTERNAL,
            ("B",),
        )
        graph = RetentionGraph(
            (
                RetentionTab(
                    "stock-A", "tab-A", TabRetentionKind.STOCK, ("A",),
                    Vec2(0, 0), Vec2(4, 0), 4.0, 3.0,
                ),
                RetentionTab(
                    "shared-AB", "tab-AB", TabRetentionKind.SHARED, ("A", "B"),
                    Vec2(500, 0), Vec2(504, 0), 4.0, 3.0,
                ),
            )
        )
        self.assertEqual(graph.critical_piece_ids(), ("A",))
        operations = _schedule_operations(
            (trail_a, trail_b),
            (5.0, 10.0, 15.0),
            CutDepthStrategy.HYBRID_STABILITY,
            start_xy=(0.0, 0.0),
            retention_graph=graph,
        )
        second_layer = [operation for operation in operations if operation.depth == 10.0]
        final_layer = [operation for operation in operations if operation.depth == 15.0]
        # FAST ends close to A; distance alone would choose A again. Retention
        # marks A as the support of B, so B must finish first.
        self.assertEqual(second_layer[-1].owner_ids, ("A",))
        self.assertEqual([operation.owner_ids for operation in final_layer], [("B",), ("A",)])
        self.assertIn(final_layer[0].operation_id, final_layer[1].dependencies)

    def test_small_piece_uses_deterministic_minimum_retention(self):
        plan = plan_for(
            (rectangle("small", 0, 0, 20, 15),),
            depths=(5.0,),
            tabs_enabled=True,
            tab_count=1,
            tab_width=3.0,
        )
        report = plan.stability_reports[0]
        self.assertEqual(report.required_tabs, 4)
        self.assertGreaterEqual(report.actual_tabs, 4)
        self.assertTrue(report.valid)

    def test_long_narrow_piece_distributes_at_least_four_tabs(self):
        plan = plan_for(
            (rectangle("strip", 0, 0, 200, 20),),
            depths=(5.0,),
            tabs_enabled=True,
            tab_count=2,
            tab_width=4.0,
            best_fixation=True,
        )
        report = plan.stability_reports[0]
        self.assertEqual(report.required_tabs, 4)
        self.assertGreaterEqual(report.actual_tabs, 4)

    def test_long_strip_respects_maximum_unsupported_perimeter(self):
        plan = plan_for(
            (rectangle("strip", 0, 0, 1000, 50),),
            depths=(5.0,),
            tabs_enabled=True,
            tab_count=4,
            tab_width=10.0,
        )
        report = plan.stability_reports[0]
        self.assertEqual(report.required_tabs, 7)
        self.assertGreaterEqual(report.actual_tabs, 7)
        self.assertLessEqual(report.maximum_free_span, 300.0)
        tab_y = {round(tab.centre.y, 6) for tab in plan.tabs}
        self.assertIn(0.0, tab_y)
        self.assertIn(50.0, tab_y)

    def test_high_physical_tabs_reduce_density_without_losing_balance(self):
        plan = plan_for(
            (rectangle("strip", 0, 0, 1000, 50),),
            depths=(5.0, 10.0, 15.5),
            tabs_enabled=True,
            tab_count=4,
            tab_width=10.0,
            tab_thickness=14.0,
            material_thickness=15.0,
        )
        report = plan.stability_reports[0]
        self.assertEqual(report.required_tabs, 5)
        self.assertEqual(report.actual_tabs, 5)
        self.assertLessEqual(report.maximum_free_span, 500.0)
        self.assertTrue(report.valid, report.reasons)

    def test_long_common_edges_receive_multiple_shared_tabs_automatically(self):
        plan = plan_for(
            (
                rectangle("left", 0, 0, 100, 1000),
                rectangle("middle", 100, 0, 200, 1000),
                rectangle("right", 200, 0, 300, 1000),
            ),
            depths=(5.0, 10.0, 15.5),
            strategy="hybrid_piece_bidirectional",
            release_mode="automatic_release",
            tabs_enabled=True,
            tab_count=4,
            tab_width=10.0,
            tab_thickness=14.0,
            tool_diameter=4.0,
            through_depth=15.0,
        )

        shared_tabs = [
            tab for tab in plan.tabs if tab.kind == TabRetentionKind.SHARED
        ]
        self.assertGreaterEqual(len(shared_tabs), 4)
        for report in plan.stability_reports:
            self.assertTrue(report.valid, report.reasons)
            self.assertLessEqual(report.maximum_free_span, 300.0)

    def test_adjacent_long_parts_share_balanced_tabs_without_outer_excess(self):
        plan = plan_for(
            (
                rectangle("lower", 0, 0, 453, 35),
                rectangle("upper", 0, 35, 453, 70),
            ),
            tabs_enabled=True,
            tab_count=4,
            tab_width=7.0,
            tab_thickness=6.0,
            material_thickness=15.0,
            tool_diameter=4.0,
        )

        shared = [tab for tab in plan.tabs if tab.kind == TabRetentionKind.SHARED]
        stock = [tab for tab in plan.tabs if tab.kind == TabRetentionKind.STOCK]
        self.assertEqual(len(plan.tabs), 6)
        self.assertEqual(len(shared), 2)
        self.assertEqual(len(stock), 4)
        self.assertTrue(all(report.actual_tabs == 4 for report in plan.stability_reports))
        self.assertTrue(all(report.valid for report in plan.stability_reports))
        # A peça estreita recebe duas pontes em cada lado comprido; nenhuma é
        # empurrada para os lados curtos ou para as quinas.
        self.assertEqual(
            {round(tab.centre.y, 6) for tab in plan.tabs},
            {0.0, 35.0, 70.0},
        )
        corners = (Vec2(0, 0), Vec2(453, 0), Vec2(0, 35), Vec2(453, 35),
                   Vec2(0, 70), Vec2(453, 70))
        self.assertGreater(
            min(tab.centre.distance_to(corner) for tab in plan.tabs for corner in corners),
            50.0,
        )

    def test_regular_part_places_four_tabs_on_four_opposite_sides(self):
        plan = plan_for(
            (rectangle("regular", 0, 0, 200, 200),),
            tabs_enabled=True,
            tab_count=4,
            tab_width=10.0,
            tab_thickness=14.0,
            material_thickness=15.0,
            tool_diameter=4.0,
        )

        self.assertEqual(len(plan.tabs), 4)
        self.assertEqual(
            {(round(tab.centre.x, 6), round(tab.centre.y, 6)) for tab in plan.tabs},
            {(100.0, 0.0), (200.0, 100.0), (100.0, 200.0), (0.0, 100.0)},
        )
        self.assertEqual(plan.stability_reports[0].actual_tabs, 4)

    def test_dense_long_strip_network_does_not_double_middle_piece_tabs(self):
        plan = plan_for(
            (
                rectangle("left", 0, 0, 100, 1000),
                rectangle("middle", 100, 0, 200, 1000),
                rectangle("right", 200, 0, 300, 1000),
            ),
            tabs_enabled=True,
            tab_count=4,
            tab_width=7.0,
            tab_thickness=6.0,
            material_thickness=15.0,
            tool_diameter=4.0,
        )

        # O fluxo owner-first anterior produzia 32 tabs físicas: 24 StockTabs
        # e oito SharedTabs. A seleção conjunta mantém todas as três peças sob
        # o vão de 300 mm sem despejar retenções nas bordas externas.
        self.assertLessEqual(len(plan.tabs), 20)
        self.assertLessEqual(
            sum(tab.kind == TabRetentionKind.STOCK for tab in plan.tabs),
            8,
        )
        self.assertGreaterEqual(
            sum(tab.kind == TabRetentionKind.SHARED for tab in plan.tabs),
            10,
        )
        for report in plan.stability_reports:
            self.assertTrue(report.valid, report.reasons)
            self.assertLessEqual(report.maximum_free_span, 300.0)
            self.assertLessEqual(report.actual_tabs, report.required_tabs + 3)

    def test_unsafe_screw_position_falls_back_to_two_full_height_waste_tabs(self):
        plan = plan_for(
            (rectangle("A", 0, 0, 100, 100),),
            internal_contours=(
                OwnedContour(
                    "tiny",
                    "A",
                    ((45, 45), (55, 45), (55, 55), (45, 55)),
                ),
            ),
            tab_width=3.0,
            loose_waste_fixation="screws",
            material_thickness=15.0,
            screw_head_diameter=20.0,
            screw_safety_margin=10.0,
        )
        self.assertFalse(plan.screw_anchors)
        self.assertEqual(plan.waste_regions[0].fixation, "tabs")
        waste_tabs = [
            tab for tab in plan.tabs if tab.kind == TabRetentionKind.WASTE
        ]
        self.assertEqual(len(waste_tabs), 2)
        self.assertTrue(all(tab.thickness == 15.0 for tab in waste_tabs))
        self.assertTrue(all(not tab.releasable for tab in waste_tabs))
        self.assertFalse(plan.tab_release_operations)

    def test_tiny_waste_uses_two_adaptive_full_height_tabs_without_blocking_job(self):
        plan = plan_for(
            (rectangle("A", 0, 0, 100, 100),),
            internal_contours=(
                OwnedContour(
                    "tiny",
                    "A",
                    ((47.5, 47.5), (52.5, 47.5), (52.5, 52.5), (47.5, 52.5)),
                ),
            ),
            tab_width=10.0,
            loose_waste_fixation="tabs",
            material_thickness=15.0,
        )

        waste_tabs = [tab for tab in plan.tabs if tab.waste_id]
        self.assertEqual(len(waste_tabs), 2)
        self.assertTrue(all(tab.width <= 5.0 + 1.0e-7 for tab in waste_tabs))
        self.assertTrue(all(tab.thickness == 15.0 for tab in waste_tabs))
        self.assertTrue(all(not tab.releasable for tab in waste_tabs))

        incomplete = dataclasses.replace(
            plan,
            tabs=tuple(tab for tab in plan.tabs if tab is not waste_tabs[-1]),
        )
        with self.assertRaisesRegex(
            GlobalCutPlanError,
            "pelo menos duas WasteTabs",
        ):
            incomplete.validate()

    def test_nesting_ring_detects_the_enclosed_waste_between_parts(self):
        pieces = (
            rectangle("bottom", 0, 0, 100, 30),
            rectangle("top", 0, 70, 100, 100),
            rectangle("left", 0, 30, 30, 70),
            rectangle("right", 70, 30, 100, 70),
        )
        stock = ((0, 0), (100, 0), (100, 100), (0, 100))
        screw_plan = plan_for(
            pieces,
            stock_boundary=stock,
            loose_waste_fixation="screws",
            material_thickness=15.0,
            tool_diameter=6.0,
            tab_width=8.0,
            screw_head_diameter=8.0,
            screw_safety_margin=2.0,
        )
        self.assertEqual(len(screw_plan.waste_regions), 1)
        self.assertEqual(screw_plan.waste_regions[0].fixation, "screw")
        self.assertEqual(len(screw_plan.screw_anchors), 1)
        anchor = screw_plan.screw_anchors[0]
        self.assertGreater(anchor.point.x, 30.0)
        self.assertLess(anchor.point.x, 70.0)
        self.assertGreater(anchor.point.y, 30.0)
        self.assertLess(anchor.point.y, 70.0)

        tab_plan = plan_for(
            pieces,
            stock_boundary=stock,
            loose_waste_fixation="tabs",
            material_thickness=15.0,
            tool_diameter=6.0,
            tab_width=8.0,
        )
        waste_tabs = [tab for tab in tab_plan.tabs if tab.waste_id]
        self.assertGreaterEqual(len(waste_tabs), 2)
        self.assertTrue(all(tab.thickness == 15.0 for tab in waste_tabs))
        self.assertTrue(all(not tab.releasable for tab in waste_tabs))

    def test_nesting_waste_is_detected_on_every_organized_sheet(self):
        first_ring = (
            rectangle("first-bottom", 0, 0, 100, 30),
            rectangle("first-top", 0, 70, 100, 100),
            rectangle("first-left", 0, 30, 30, 70),
            rectangle("first-right", 70, 30, 100, 70),
        )
        second_ring = (
            rectangle("second-bottom", 150, 0, 250, 30),
            rectangle("second-top", 150, 70, 250, 100),
            rectangle("second-left", 150, 30, 180, 70),
            rectangle("second-right", 220, 30, 250, 70),
        )

        plan = plan_for(
            first_ring + second_ring,
            stock_boundaries=(
                ((0, 0), (100, 0), (100, 100), (0, 100)),
                ((150, 0), (250, 0), (250, 100), (150, 100)),
            ),
            loose_waste_fixation="tabs",
            material_thickness=15.0,
            tool_diameter=6.0,
            tab_width=8.0,
        )

        nesting_regions = tuple(
            region
            for region in plan.waste_regions
            if region.waste_id.startswith("waste-nesting-")
        )
        waste_tabs = tuple(tab for tab in plan.tabs if tab.waste_id)
        self.assertEqual(len(nesting_regions), 2)
        self.assertEqual(len(waste_tabs), 4)
        self.assertTrue(any(tab.start.x < 100.0 for tab in waste_tabs))
        self.assertTrue(any(tab.start.x > 150.0 for tab in waste_tabs))

    def test_waste_tabs_prefer_long_edges_and_spread_along_them(self):
        segments = (
            PhysicalCutSegment(
                "short-first", Vec2(0, 0), Vec2(0, 4),
                PhysicalSegmentKind.EXTERNAL, ("A",),
            ),
            PhysicalCutSegment(
                "long-bottom", Vec2(0, 0), Vec2(100, 0),
                PhysicalSegmentKind.EXTERNAL, ("B",),
            ),
            PhysicalCutSegment(
                "short-second", Vec2(100, 0), Vec2(100, 5),
                PhysicalSegmentKind.EXTERNAL, ("C",),
            ),
            PhysicalCutSegment(
                "long-top", Vec2(0, 20), Vec2(100, 20),
                PhysicalSegmentKind.EXTERNAL, ("D",),
            ),
        )

        placements = _balanced_waste_tab_placements(
            segments,
            range(len(segments)),
            2,
            10.0,
            4.0,
            RetentionRules(),
            0.02,
        )

        self.assertEqual({index for index, _parameter in placements}, {1, 3})
        parameters = sorted(parameter for _index, parameter in placements)
        self.assertLessEqual(parameters[0], 0.20 + 1.0e-9)
        self.assertGreaterEqual(parameters[1], 0.80 - 1.0e-9)

        elongated_waste_plan = plan_for(
            (
                rectangle("bottom", 0, 0, 200, 40),
                rectangle("top", 0, 60, 200, 100),
                rectangle("left", 0, 40, 40, 60),
                rectangle("right", 160, 40, 200, 60),
            ),
            stock_boundary=((0, 0), (200, 0), (200, 100), (0, 100)),
            loose_waste_fixation="tabs",
            material_thickness=15.0,
            tool_diameter=4.0,
            tab_width=10.0,
        )
        waste_tabs = tuple(
            tab for tab in elongated_waste_plan.tabs if tab.waste_id
        )
        self.assertEqual(len(waste_tabs), 2)
        self.assertTrue(all(tab.width == 10.0 for tab in waste_tabs))
        self.assertTrue(
            all(abs(tab.start.y - tab.end.y) <= 1.0e-9 for tab in waste_tabs)
        )
        self.assertGreater(
            waste_tabs[0].centre.distance_to(waste_tabs[1].centre),
            80.0,
        )

    def test_stock_tab_connects_piece_directly_to_stock(self):
        tab = RetentionTab(
            "stock-A", "s1", TabRetentionKind.STOCK, ("A",),
            Vec2(0, 0), Vec2(4, 0), 4.0, 3.0,
        )
        graph = RetentionGraph((tab,))
        self.assertTrue(graph.connected_to_stock("A"))

    def test_shared_tab_is_only_indirect_retention(self):
        stock = RetentionTab(
            "stock-A", "s1", TabRetentionKind.STOCK, ("A",),
            Vec2(0, 0), Vec2(4, 0), 4.0, 3.0,
        )
        shared = RetentionTab(
            "shared-AB", "s2", TabRetentionKind.SHARED, ("A", "B"),
            Vec2(10, 0), Vec2(14, 0), 4.0, 3.0,
        )
        graph = RetentionGraph((stock, shared))
        self.assertTrue(graph.connected_to_stock("B"))
        self.assertFalse(graph.connected_to_stock("B", removed_tabs=("shared-AB",)))

    def test_release_rejects_loss_of_retention_for_other_piece(self):
        stock = RetentionTab(
            "stock-A", "s1", TabRetentionKind.STOCK, ("A",),
            Vec2(0, 0), Vec2(4, 0), 4.0, 3.0,
        )
        shared = RetentionTab(
            "shared-AB", "s2", TabRetentionKind.SHARED, ("A", "B"),
            Vec2(10, 0), Vec2(14, 0), 4.0, 3.0,
        )
        graph = RetentionGraph((stock, shared))
        self.assertFalse(graph.removal_preserves_other_pieces("A", ("A", "B")))
        self.assertTrue(graph.removal_preserves_other_pieces("B", ("A", "B")))

    def test_tab_narrower_than_tool_releases_with_one_plunge_no_sweep(self):
        plan = plan_for(
            (rectangle("A", 0, 0, 100, 60),),
            depths=(15.5,),
            tabs_enabled=True,
            tab_count=3,
            tab_width=4.0,
            tab_thickness=3.0,
            tool_diameter=6.0,
            release_mode="automatic_release",
        )
        self.assertTrue(plan.tab_release_operations)
        self.assertTrue(all(op.sweep_length == 0.0 for op in plan.tab_release_operations))
        self.assertTrue(all(op.plunge_point == op.sweep_end for op in plan.tab_release_operations))

    def test_tab_equal_to_tool_has_no_sweep(self):
        plan = plan_for(
            (rectangle("A", 0, 0, 100, 60),),
            depths=(15.5,),
            tabs_enabled=True,
            tab_count=3,
            tab_width=6.0,
            tool_diameter=6.0,
            release_mode=TabReleaseMode.AUTOMATIC_RELEASE,
        )
        self.assertTrue(all(op.sweep_length == 0.0 for op in plan.tab_release_operations))

    def test_tab_wider_than_tool_uses_exact_minimum_sweep(self):
        plan = plan_for(
            (rectangle("A", 0, 0, 100, 60),),
            depths=(15.5,),
            tabs_enabled=True,
            tab_count=3,
            tab_width=10.0,
            tool_diameter=4.0,
            release_mode="automatic_release",
        )
        self.assertTrue(all(abs(op.sweep_length - 6.0) < 1e-7 for op in plan.tab_release_operations))

    def test_automatic_release_rejects_tool_without_vertical_plunge_support(self):
        with self.assertRaisesRegex(GlobalCutPlanError, "mergulho vertical"):
            plan_for(
                (rectangle("A", 0, 0, 100, 60),),
                depths=(15.5,),
                tabs_enabled=True,
                tab_count=3,
                tab_width=6.0,
                tool_diameter=6.0,
                tool_type="surfacing",
                release_mode="automatic_release",
            )

    def test_last_tab_is_explicit_and_terminates_piece_release(self):
        plan = plan_for(
            (rectangle("A", 0, 0, 100, 60),),
            depths=(15.5,),
            tabs_enabled=True,
            tab_count=3,
            tab_width=10.0,
            tool_diameter=4.0,
            release_mode="automatic_release",
        )
        piece = [op for op in plan.tab_release_operations if op.owner_id == "A"]
        self.assertFalse(any(op.last_for_piece for op in piece[:-1]))
        self.assertTrue(piece[-1].last_for_piece)

    def test_duplicate_segment_and_depth_is_rejected_structurally(self):
        plan = plan_for((rectangle("A", 0, 0, 40, 40),), depths=(5.0,))
        duplicate = dataclasses.replace(
            plan.operations[0],
            operation_id="duplicate-operation",
            dependencies=(plan.operations[0].operation_id,),
        )
        invalid = dataclasses.replace(plan, operations=plan.operations + (duplicate,))
        with self.assertRaisesRegex(GlobalCutPlanError, "corte duplicado"):
            invalid.validate()

    def test_more_than_two_owners_is_rejected(self):
        plan = plan_for((rectangle("A", 0, 0, 40, 40),), depths=(5.0,))
        invalid_segment = dataclasses.replace(
            plan.segments[0],
            kind=PhysicalSegmentKind.SHARED,
            owner_ids=("A", "B", "C"),
        )
        invalid = dataclasses.replace(
            plan,
            segments=(invalid_segment,) + plan.segments[1:],
        )
        with self.assertRaisesRegex(GlobalCutPlanError, "exatamente dois owners|mais de dois owners"):
            invalid.validate()

    def test_internal_contour_must_belong_strictly_to_its_owner(self):
        with self.assertRaisesRegex(GlobalCutPlanError, "estritamente dentro"):
            plan_for(
                (rectangle("A", 0, 0, 40, 40),),
                depths=(5.0,),
                internal_contours=(
                    OwnedContour(
                        "outside",
                        "A",
                        ((50, 50), (60, 50), (60, 60), (50, 60)),
                    ),
                ),
            )

    def test_crossing_internal_contours_are_rejected_before_scheduling(self):
        with self.assertRaisesRegex(GlobalCutPlanError, "se tocam, cruzam"):
            plan_for(
                (rectangle("A", 0, 0, 100, 100),),
                depths=(5.0,),
                internal_contours=(
                    OwnedContour("i1", "A", ((10, 40), (90, 40), (90, 60), (10, 60))),
                    OwnedContour("i2", "A", ((40, 10), (60, 10), (60, 90), (40, 90))),
                ),
            )


if __name__ == "__main__":
    unittest.main()
