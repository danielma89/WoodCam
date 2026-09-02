import math
import unittest
from unittest.mock import patch

import woodcam_editor.application.common_line as common_line_module
from woodcam_editor.application.common_line import (
    CommonLineContour,
    CommonLineIssueCode,
    CommonLinePlanningError,
    build_common_line_cut_paths,
    build_common_line_cut_trails,
    plan_common_line_cut,
)
from woodcam_editor.domain import Vec2
from woodcam_editor.geometry.polygon_offset import round_offset_closed_polygon


def rectangle(contour_id, min_x, min_y, max_x, max_y, *, clockwise=False):
    points = [
        (min_x, min_y),
        (max_x, min_y),
        (max_x, max_y),
        (min_x, max_y),
    ]
    if clockwise:
        points.reverse()
    return CommonLineContour(contour_id, points)


def issue_codes(plan):
    return {issue.code for issue in plan.issues}


class CommonLinePlanTests(unittest.TestCase):
    def test_round_compensation_does_not_create_false_acute_miter_crossing(self):
        left = ((0, 0), (20, 100), (0, 200))
        right = ((44, 0), (24, 100), (44, 200))
        compensated = (
            CommonLineContour("left", round_offset_closed_polygon(left, 2.0)),
            CommonLineContour("right", round_offset_closed_polygon(right, 2.0)),
        )

        plan = plan_common_line_cut(compensated, tolerance=0.02)

        self.assertTrue(plan.is_valid)

    def test_round_compensation_keeps_shared_straight_centre_line(self):
        left = ((0, 0), (20, 0), (20, 20), (0, 20))
        right = ((24, 0), (44, 0), (44, 20), (24, 20))
        plan = plan_common_line_cut(
            (
                CommonLineContour("left", round_offset_closed_polygon(left, 2.0)),
                CommonLineContour("right", round_offset_closed_polygon(right, 2.0)),
            ),
            tolerance=0.02,
        )

        self.assertTrue(plan.is_valid)
        self.assertEqual(len(plan.shared_segments), 1)
        self.assertAlmostEqual(plan.shared_segments[0].length, 20.0)

    def test_matching_tolerance_does_not_flatten_compensated_round_corners(self):
        left = ((0, 0), (80, 0), (80, 40), (0, 40))
        right = ((86, 0), (166, 0), (166, 40), (86, 40))
        plan = plan_common_line_cut(
            (
                CommonLineContour("left", round_offset_closed_polygon(left, 3.0)),
                CommonLineContour("right", round_offset_closed_polygon(right, 3.0)),
            ),
            tolerance=0.2,
        )

        self.assertTrue(plan.is_valid)
        self.assertEqual(len(plan.shared_segments), 1)
        self.assertAlmostEqual(plan.shared_segments[0].length, 40.0)

    def test_matching_tolerance_does_not_reject_short_internal_orbit_segments(self):
        # portigo.FCStd has Ø5.299 mm polygonal holes.  A Ø4 mm cutter leaves
        # a real centre-line radius near 0.65 mm whose 36 chords are about
        # 0.11 mm: shorter than the 0.20 mm shared-line matching tolerance,
        # but neither duplicated nor physically null.
        radius = 0.649425
        internal_orbit = tuple(
            (
                radius * math.cos(math.tau * index / 36.0),
                radius * math.sin(math.tau * index / 36.0),
            )
            for index in range(36)
        )

        plan = plan_common_line_cut(
            (CommonLineContour("internal-0001", internal_orbit),),
            tolerance=0.2,
        )

        self.assertTrue(plan.is_valid, plan.issues)

    def test_single_polygon_remains_complete_perimeter(self):
        plan = plan_common_line_cut((rectangle("A", 0, 0, 10, 10),))

        self.assertTrue(plan.is_valid)
        self.assertFalse(plan.has_common_lines)
        self.assertEqual(len(plan.perimeter_segments), 4)
        self.assertEqual(len(plan.perimeter_for("A")), 4)
        self.assertAlmostEqual(plan.total_cut_length, 40.0)

    def test_full_common_boundary_is_cut_once(self):
        plan = plan_common_line_cut(
            (
                rectangle("left", 0, 0, 10, 10),
                rectangle("right", 10, 0, 20, 10),
            )
        )

        self.assertTrue(plan.is_valid)
        self.assertEqual(len(plan.perimeter_segments), 6)
        self.assertEqual(len(plan.shared_segments), 1)
        shared = plan.shared_segments[0]
        self.assertEqual(shared.owner_ids, ("left", "right"))
        self.assertEqual(shared.start, Vec2(10, 0))
        self.assertEqual(shared.end, Vec2(10, 10))
        self.assertAlmostEqual(plan.total_cut_length, 70.0)
        self.assertFalse(plan.touch_points)

    def test_reversed_orientation_and_extra_collinear_vertices_share_normally(self):
        left = CommonLineContour(
            "left",
            ((0, 0), (10, 0), (10, 4), (10, 10), (0, 10), (0, 0)),
        )
        right = CommonLineContour(
            "right",
            ((10, 10), (20, 10), (20, 0), (10, 0), (10, 5), (10, 10)),
        )

        plan = plan_common_line_cut((left, right))

        self.assertTrue(plan.is_valid)
        self.assertEqual(len(plan.shared_segments), 1)
        self.assertAlmostEqual(plan.shared_segments[0].length, 10.0)

    def test_partial_common_boundaries_split_only_the_owned_intervals(self):
        plan = plan_common_line_cut(
            (
                rectangle("main", 0, 0, 10, 10),
                rectangle("low", 10, 0, 20, 4),
                rectangle("high", 10, 6, 20, 10),
            )
        )

        self.assertTrue(plan.is_valid)
        self.assertEqual(
            [
                (segment.start.to_tuple(), segment.end.to_tuple(), segment.owner_ids)
                for segment in sorted(
                    plan.shared_segments,
                    key=lambda value: (value.start.x, value.start.y),
                )
            ],
            [
                ((10.0, 0.0), (10.0, 4.0), ("low", "main")),
                ((10.0, 6.0), (10.0, 10.0), ("high", "main")),
            ],
        )
        remainder = [
            segment
            for segment in plan.perimeter_for("main")
            if segment.start.x == segment.end.x == 10.0
        ]
        self.assertEqual(len(remainder), 1)
        self.assertEqual(remainder[0].start, Vec2(10, 4))
        self.assertEqual(remainder[0].end, Vec2(10, 6))

    def test_consecutive_shared_atoms_with_same_owners_are_coalesced(self):
        left = CommonLineContour(
            "left",
            ((0, 0), (10, 0), (10, 3), (10, 7), (10, 10), (0, 10)),
        )
        right = CommonLineContour(
            "right",
            ((10, 0), (20, 0), (20, 10), (10, 10), (10, 5)),
        )

        plan = plan_common_line_cut((left, right))

        self.assertTrue(plan.is_valid)
        self.assertEqual(len(plan.shared_segments), 1)
        self.assertEqual(plan.shared_segments[0].start, Vec2(10, 0))
        self.assertEqual(plan.shared_segments[0].end, Vec2(10, 10))

    def test_vertex_only_contact_is_allowed_but_not_common_line(self):
        plan = plan_common_line_cut(
            (
                rectangle("a", 0, 0, 10, 10),
                rectangle("b", 10, 10, 20, 20),
            )
        )

        self.assertTrue(plan.is_valid)
        self.assertFalse(plan.shared_segments)
        self.assertEqual(plan.touch_points, (Vec2(10, 10),))
        self.assertEqual(len(plan.perimeter_segments), 8)

    def test_endpoint_to_middle_t_contact_is_allowed(self):
        plan = plan_common_line_cut(
            (
                rectangle("plate", 0, 0, 10, 10),
                CommonLineContour("tip", ((10, 5), (15, 3), (15, 7))),
            )
        )

        self.assertTrue(plan.is_valid)
        self.assertFalse(plan.shared_segments)
        self.assertEqual(plan.touch_points, (Vec2(10, 5),))

    def test_near_coincident_boundary_within_tolerance_is_shared(self):
        plan = plan_common_line_cut(
            (
                rectangle("a", 0, 0, 10, 10),
                rectangle("b", 10.0000005, 0, 20, 10),
            ),
            tolerance=1.0e-6,
        )

        self.assertTrue(plan.is_valid)
        self.assertEqual(len(plan.shared_segments), 1)
        self.assertAlmostEqual(plan.shared_segments[0].length, 10.0)

    def test_gap_larger_than_tolerance_is_not_shared_or_touched(self):
        plan = plan_common_line_cut(
            (
                rectangle("a", 0, 0, 10, 10),
                rectangle("b", 10.01, 0, 20, 10),
            ),
            tolerance=1.0e-4,
        )

        self.assertTrue(plan.is_valid)
        self.assertFalse(plan.shared_segments)
        self.assertFalse(plan.touch_points)
        self.assertEqual(len(plan.perimeter_segments), 8)


class CommonLineBlockingTests(unittest.TestCase):
    def test_duplicate_contours_are_blocked_even_reversed_and_resegmented(self):
        first = rectangle("first", 0, 0, 10, 10)
        second = CommonLineContour(
            "second",
            ((10, 10), (10, 5), (10, 0), (0, 0), (0, 10), (10, 10)),
        )

        plan = plan_common_line_cut((first, second))

        self.assertFalse(plan.is_valid)
        self.assertIn(CommonLineIssueCode.DUPLICATE_CONTOUR, issue_codes(plan))
        self.assertFalse(plan.perimeter_segments)
        self.assertFalse(plan.shared_segments)

    def test_real_boundary_crossing_is_blocked(self):
        plan = plan_common_line_cut(
            (
                rectangle("a", 0, 0, 10, 10),
                rectangle("b", 5, -5, 15, 5),
            )
        )

        self.assertFalse(plan.is_valid)
        self.assertIn(CommonLineIssueCode.CROSSING, issue_codes(plan))
        crossing = next(
            issue
            for issue in plan.issues
            if issue.code == CommonLineIssueCode.CROSSING
        )
        self.assertEqual(set(crossing.points), {Vec2(5, 0), Vec2(10, 5)})

    def test_contained_positive_area_is_blocked_without_boundary_crossing(self):
        plan = plan_common_line_cut(
            (
                rectangle("outer", 0, 0, 20, 20),
                rectangle("inside", 5, 5, 15, 15),
            )
        )

        self.assertFalse(plan.is_valid)
        self.assertIn(CommonLineIssueCode.AREA_OVERLAP, issue_codes(plan))

    def test_same_side_of_coincident_boundary_is_area_overlap_not_common_line(self):
        plan = plan_common_line_cut(
            (
                rectangle("large", 0, 0, 10, 10),
                rectangle("lower-half", 0, 0, 10, 5),
            )
        )

        self.assertFalse(plan.is_valid)
        self.assertIn(CommonLineIssueCode.AREA_OVERLAP, issue_codes(plan))
        self.assertFalse(plan.shared_segments)

    def test_self_crossing_polygon_is_blocked(self):
        plan = plan_common_line_cut(
            (CommonLineContour("bow", ((0, 0), (10, 10), (0, 10), (10, 0))),)
        )

        self.assertFalse(plan.is_valid)
        self.assertIn(CommonLineIssueCode.SELF_INTERSECTION, issue_codes(plan))

    def test_backtracking_collinear_edge_is_blocked(self):
        plan = plan_common_line_cut(
            (
                CommonLineContour(
                    "backtrack",
                    ((0, 0), (10, 0), (5, 0), (10, 10), (0, 10)),
                ),
            )
        )

        self.assertFalse(plan.is_valid)
        self.assertIn(CommonLineIssueCode.SELF_INTERSECTION, issue_codes(plan))

    def test_more_than_two_owners_on_same_atomic_line_is_ambiguous(self):
        plan = plan_common_line_cut(
            (
                rectangle("left", 0, 0, 10, 10),
                rectangle("right-wide", 10, 0, 20, 10),
                rectangle("right-narrow", 10, 0, 15, 10),
            )
        )

        self.assertFalse(plan.is_valid)
        self.assertIn(
            CommonLineIssueCode.AMBIGUOUS_SHARED_LINE, issue_codes(plan)
        )
        ambiguous = next(
            issue
            for issue in plan.issues
            if issue.code == CommonLineIssueCode.AMBIGUOUS_SHARED_LINE
        )
        self.assertEqual(
            ambiguous.contour_ids,
            ("left", "right-narrow", "right-wide"),
        )

    def test_require_valid_raises_structured_error(self):
        plan = plan_common_line_cut(
            (
                rectangle("outer", 0, 0, 20, 20),
                rectangle("inner", 2, 2, 3, 3),
            )
        )

        with self.assertRaises(CommonLinePlanningError) as caught:
            plan.require_valid()
        self.assertEqual(caught.exception.issues, plan.issues)

        with self.assertRaises(CommonLinePlanningError):
            plan_common_line_cut(
                (
                    rectangle("outer", 0, 0, 20, 20),
                    rectangle("inner", 2, 2, 3, 3),
                ),
                raise_on_error=True,
            )

    def test_repeated_identifier_and_invalid_tolerance_are_rejected(self):
        plan = plan_common_line_cut(
            (
                rectangle("same", 0, 0, 1, 1),
                rectangle("same", 2, 0, 3, 1),
            )
        )
        self.assertFalse(plan.is_valid)
        self.assertIn(CommonLineIssueCode.INVALID_CONTOUR, issue_codes(plan))

        for tolerance in (0.0, -1.0, float("inf"), float("nan")):
            with self.assertRaises(ValueError):
                plan_common_line_cut((), tolerance=tolerance)

    def test_invalid_polygon_with_too_few_distinct_points_is_blocked(self):
        plan = plan_common_line_cut(
            (CommonLineContour("line", ((0, 0), (10, 0), (0, 0))),)
        )

        self.assertFalse(plan.is_valid)
        self.assertIn(CommonLineIssueCode.INVALID_CONTOUR, issue_codes(plan))


class CommonLinePurityTests(unittest.TestCase):
    def test_source_contours_are_not_mutated_by_normalisation(self):
        source_points = (
            Vec2(0, 0),
            Vec2(10, 0),
            Vec2(10, 5),
            Vec2(10, 10),
            Vec2(0, 10),
            Vec2(0, 0),
        )
        source = CommonLineContour("source", source_points)

        plan = plan_common_line_cut((source,))

        self.assertTrue(plan.is_valid)
        self.assertEqual(source.points, source_points)
        self.assertEqual(len(source.points), 6)

    def test_owner_order_and_shared_direction_are_deterministic(self):
        first = rectangle("z-piece", 0, 0, 10, 10, clockwise=True)
        second = rectangle("a-piece", 10, 0, 20, 10)

        direct = plan_common_line_cut((first, second))
        reversed_input = plan_common_line_cut((second, first))

        self.assertTrue(direct.is_valid)
        self.assertTrue(reversed_input.is_valid)
        self.assertEqual(direct.shared_segments, reversed_input.shared_segments)
        self.assertEqual(
            direct.shared_segments[0].owner_ids,
            ("a-piece", "z-piece"),
        )
        self.assertLessEqual(
            direct.shared_segments[0].start.to_tuple(),
            direct.shared_segments[0].end.to_tuple(),
        )


class CommonLinePerformanceRegressionTests(unittest.TestCase):
    def test_dense_polygon_uses_spatial_broad_phase_for_self_intersections(self):
        point_count = 720
        contour = CommonLineContour(
            "dense-circle",
            (
                (
                    100.0 * math.cos(math.tau * index / point_count),
                    100.0 * math.sin(math.tau * index / point_count),
                )
                for index in range(point_count)
            ),
        )

        with patch.object(
            common_line_module,
            "_segment_relation",
            wraps=common_line_module._segment_relation,
        ) as relation:
            plan = plan_common_line_cut((contour,), tolerance=0.02)

        self.assertTrue(plan.is_valid, plan.issues)
        self.assertLess(relation.call_count, point_count * 10)

    def test_atom_grouping_does_not_compare_every_unrelated_segment(self):
        contours = tuple(
            rectangle("piece-%03d" % index, index * 20.0, 0.0, index * 20.0 + 10.0, 10.0)
            for index in range(80)
        )

        with patch.object(
            common_line_module,
            "_same_unordered_segment",
            wraps=common_line_module._same_unordered_segment,
        ) as same_segment:
            plan = plan_common_line_cut(contours, tolerance=0.02)

        self.assertTrue(plan.is_valid, plan.issues)
        self.assertLess(same_segment.call_count, 1000)


class CommonLineTabPlanningTests(unittest.TestCase):
    def test_tabs_are_edges_of_continuous_owner_trails_not_separate_paths(self):
        plan = plan_common_line_cut(
            (
                rectangle("left", 0, 0, 20, 10),
                rectangle("right", 20, 0, 40, 10),
            )
        )
        trails = build_common_line_cut_trails(
            plan,
            tab_count=2,
            tab_length=2.0,
        )

        self.assertEqual(len(trails), 3)
        self.assertEqual(sum(trail.length for trail in trails), plan.total_cut_length)
        self.assertEqual(sum(any(trail.edge_tabs) for trail in trails), 2)
        self.assertTrue(all(len(trail.edge_tabs) == len(trail.points) - 1 for trail in trails))
        self.assertFalse(any(trails[0].edge_tabs))

    def test_best_fixation_uses_separated_non_collinear_regions_per_piece(self):
        plan = plan_common_line_cut((rectangle("board", 0, 0, 100, 60),))
        paths = build_common_line_cut_paths(
            plan,
            tab_count=2,
            tab_length=8.0,
            best_fixation=True,
        )
        tab_paths = [path for path in paths if path.tab]
        centres = [
            (path.points[0] + path.points[1]) * 0.5
            for path in tab_paths
        ]

        self.assertGreaterEqual(len(tab_paths), 3)
        self.assertGreater(
            max(
                abs((second - first).cross(third - first))
                for first in centres
                for second in centres
                for third in centres
            ),
            1.0,
        )

    def test_every_interval_is_edge_disjoint_and_shared_edge_occurs_once(self):
        plan = plan_common_line_cut(
            (
                rectangle("left", 0, 0, 20, 10),
                rectangle("right", 20, 0, 40, 10),
            )
        )
        paths = build_common_line_cut_paths(
            plan,
            tab_count=2,
            tab_length=4.0,
        )

        shared = [path for path in paths if path.shared]
        self.assertEqual(len(shared), 1)
        self.assertEqual(shared[0].points, (Vec2(20, 0), Vec2(20, 10)))
        self.assertFalse(shared[0].tab)
        self.assertEqual(
            sum(path.points[0].distance_to(path.points[-1]) for path in paths),
            plan.total_cut_length,
        )
        self.assertTrue(any(path.tab for path in paths))
        self.assertFalse(any(path.tab and path.shared for path in paths))

    def test_manual_tab_is_projected_to_owner_only_perimeter(self):
        plan = plan_common_line_cut(
            (
                rectangle("left", 0, 0, 20, 10),
                rectangle("right", 20, 0, 40, 10),
            )
        )
        paths = build_common_line_cut_paths(
            plan,
            tab_length=6.0,
            manual_tab_positions=({"x": 8.0, "y": 0.0},),
        )
        tab_paths = [path for path in paths if path.tab]
        self.assertEqual(len(tab_paths), 1)
        self.assertAlmostEqual(
            tab_paths[0].points[0].distance_to(tab_paths[0].points[-1]),
            6.0,
        )
        self.assertEqual(tab_paths[0].owner_ids, ("left",))

    def test_manual_tabs_replace_automatic_distribution(self):
        plan = plan_common_line_cut(
            (
                rectangle("left", 0, 0, 20, 10),
                rectangle("right", 20, 0, 40, 10),
            )
        )
        paths = build_common_line_cut_paths(
            plan,
            tab_count=3,
            tab_length=2.0,
            manual_tab_positions=((0.0, 5.0),),
        )

        self.assertEqual(sum(1 for path in paths if path.tab), 1)

    def test_manual_click_on_common_edge_creates_one_shared_tab_at_click(self):
        plan = plan_common_line_cut(
            (
                rectangle("left", 0, 0, 20, 10),
                rectangle("right", 20, 0, 40, 10),
            )
        )

        paths = build_common_line_cut_paths(
            plan,
            tab_count=4,
            tab_length=4.0,
            manual_tab_positions=({"x": 20.0, "y": 5.0},),
        )

        tabs = [path for path in paths if path.tab]
        self.assertEqual(len(tabs), 1)
        self.assertTrue(tabs[0].shared)
        self.assertEqual(tabs[0].owner_ids, ("left", "right"))
        self.assertAlmostEqual(tabs[0].points[0].y, 3.0)
        self.assertAlmostEqual(tabs[0].points[-1].y, 7.0)


if __name__ == "__main__":
    unittest.main()
