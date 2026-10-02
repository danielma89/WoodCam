import unittest
import math

from gcode_writer import build_gcode
from operations import (
    CUT_SIDE_INSIDE,
    CUT_SIDE_ON_LINE,
    CUT_SIDE_OUTSIDE,
    build_common_line_cut_job,
    build_profile_cut_moves,
    build_drill_moves,
    build_machining_job,
    build_machining_stages,
    build_contour_cut_stage,
    build_pocket_stage,
    build_external_cut_job,
    build_external_cut_moves,
    compensated_polygon,
    external_compensation_detail_loss,
    generate_depth_steps,
    offset_closed_polygon,
    order_contours_by_nearest,
    select_entry_distance,
    split_nested_contours,
    tab_retained_cut_depth,
    _best_fixation_tab_ranges,
    _tab_ranges,
)


class OperationsTest(unittest.TestCase):
    def test_depth_steps_include_final_depth(self):
        self.assertEqual(generate_depth_steps(10.0, 3.0), [3.0, 6.0, 9.0, 10.0])

    def test_depth_steps_only_machine_the_interval_after_initial_depth(self):
        self.assertEqual(
            generate_depth_steps(7.0, 0.4, start_depth=6.3),
            [6.7, 7.0],
        )

    def test_depth_extra_is_merged_into_last_material_pass(self):
        self.assertEqual(
            generate_depth_steps(16.0, 6.0, material_thickness=15.0),
            [6.0, 12.0, 16.0],
        )
        self.assertEqual(
            generate_depth_steps(16.0, 5.0, material_thickness=15.0),
            [5.0, 10.0, 16.0],
        )

    def test_external_offset_expands_ccw_rectangle(self):
        rectangle = [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)]
        offset = offset_closed_polygon(rectangle, 3.0)
        xs = [point[0] for point in offset]
        ys = [point[1] for point in offset]
        self.assertAlmostEqual(min(xs), -3.0)
        self.assertAlmostEqual(max(xs), 103.0)
        self.assertAlmostEqual(min(ys), -3.0)
        self.assertAlmostEqual(max(ys), 53.0)

    def test_cut_sides_apply_the_expected_tool_radius(self):
        rectangle = [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)]

        outside = compensated_polygon(rectangle, 6.0, CUT_SIDE_OUTSIDE)
        on_line = compensated_polygon(rectangle, 6.0, CUT_SIDE_ON_LINE)
        inside = compensated_polygon(rectangle, 6.0, CUT_SIDE_INSIDE)

        self.assertEqual(on_line, rectangle)
        self.assertAlmostEqual(min(point[0] for point in outside), -3.0)
        self.assertAlmostEqual(max(point[0] for point in outside), 103.0)
        self.assertAlmostEqual(min(point[0] for point in inside), 3.0)
        self.assertAlmostEqual(max(point[0] for point in inside), 97.0)
        self.assertAlmostEqual(min(point[1] for point in inside), 3.0)
        self.assertAlmostEqual(max(point[1] for point in inside), 47.0)

    def test_cut_sides_work_with_clockwise_contour(self):
        rectangle = [(0.0, 50.0), (100.0, 50.0), (100.0, 0.0), (0.0, 0.0)]

        outside = compensated_polygon(rectangle, 6.0, CUT_SIDE_OUTSIDE)
        inside = compensated_polygon(rectangle, 6.0, CUT_SIDE_INSIDE)

        self.assertAlmostEqual(min(point[0] for point in outside), -3.0)
        self.assertAlmostEqual(max(point[0] for point in outside), 103.0)
        self.assertAlmostEqual(min(point[0] for point in inside), 3.0)
        self.assertAlmostEqual(max(point[0] for point in inside), 97.0)

    def test_external_compensation_rounds_acute_tip_at_physical_tool_radius(self):
        pointed = [(0.0, 0.0), (20.0, 100.0), (0.0, 200.0)]

        outside = compensated_polygon(pointed, 4.0, CUT_SIDE_OUTSIDE)

        self.assertLessEqual(max(point[0] for point in outside), 22.0 + 1e-9)
        self.assertGreater(len(outside), len(pointed))

    def test_internal_cut_shrinks_a_discretized_circle(self):
        circle = [
            (
                50.0 * math.cos(2.0 * math.pi * index / 72.0),
                50.0 * math.sin(2.0 * math.pi * index / 72.0),
            )
            for index in range(72)
        ]

        inside = compensated_polygon(circle, 6.0, CUT_SIDE_INSIDE)
        radii = [math.hypot(point[0], point[1]) for point in inside]

        self.assertAlmostEqual(sum(radii) / len(radii), 47.0, delta=0.02)

    def test_profile_cut_moves_follow_selected_cut_side(self):
        rectangle = [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)]

        outside_moves = build_profile_cut_moves(
            rectangle, 3.0, 3.0, 0.0, 8.0,
            tool_diameter=6.0,
            cut_side=CUT_SIDE_OUTSIDE,
            smart_entry=False,
        )
        line_moves = build_profile_cut_moves(
            rectangle, 3.0, 3.0, 0.0, 8.0,
            tool_diameter=6.0,
            cut_side=CUT_SIDE_ON_LINE,
            smart_entry=False,
        )
        inside_moves = build_profile_cut_moves(
            rectangle, 3.0, 3.0, 0.0, 8.0,
            tool_diameter=6.0,
            cut_side=CUT_SIDE_INSIDE,
            smart_entry=False,
        )

        def x_extent(moves):
            xs = [move["x"] for move in moves if move.get("x") is not None]
            return min(xs), max(xs)

        self.assertEqual(x_extent(outside_moves), (-3.0, 103.0))
        self.assertEqual(x_extent(line_moves), (0.0, 100.0))
        self.assertEqual(x_extent(inside_moves), (3.0, 97.0))

    def test_internal_cut_rejects_contour_smaller_than_tool(self):
        square = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)]
        with self.assertRaisesRegex(ValueError, "estreito demais"):
            compensated_polygon(square, 6.0, CUT_SIDE_INSIDE)

    def test_external_compensation_reports_a_slot_narrower_than_the_tool(self):
        narrow_slot = [
            (0.0, 0.0),
            (100.0, 0.0),
            (100.0, 100.0),
            (52.0, 100.0),
            (52.0, 60.0),
            (50.0, 60.0),
            (50.0, 100.0),
            (0.0, 100.0),
        ]
        issue = external_compensation_detail_loss(narrow_slot, 4.0)
        self.assertIsNotNone(issue)
        self.assertGreater(issue["distance_to_compensated_path"], 2.5)
        self.assertGreaterEqual(issue["point"][1], 60.0)
        self.assertEqual(issue["cleanup_modes"], ("slot_centerline",))
        self.assertEqual(
            issue["cleanup_paths"],
            (((51.0, 100.0), (51.0, 60.0)),),
        )
        # A fresa Ø4 centrada na fenda de 2 mm excede cada parede em apenas
        # 1 mm. Seguir as duas paredes excederia 2 mm em cada lado.
        centre_x = issue["cleanup_paths"][0][0][0]
        self.assertEqual(centre_x - 4.0 * 0.5, 49.0)
        self.assertEqual(centre_x + 4.0 * 0.5, 53.0)

    def test_external_compensation_centres_tapered_and_stepped_slot(self):
        narrow_slot = [
            (0.0, 0.0),
            (100.0, 0.0),
            (100.0, 100.0),
            (54.0, 100.0),
            (54.0, 80.0),
            (53.0, 80.0),
            (53.0, 55.0),
            (52.0, 55.0),
            (52.0, 30.0),
            (50.0, 30.0),
            (50.0, 100.0),
            (0.0, 100.0),
        ]

        issue = external_compensation_detail_loss(narrow_slot, 4.0)

        self.assertIsNotNone(issue)
        self.assertEqual(issue["cleanup_modes"], ("slot_medial_axis",))
        self.assertEqual(
            issue["cleanup_paths"],
            (
                (
                    (52.0, 100.0),
                    (52.0, 80.0),
                    (51.5, 67.5),
                    (51.5, 55.0),
                    (51.0, 42.5),
                    (51.0, 30.0),
                ),
            ),
        )
        source_trace = tuple(narrow_slot[3:11])
        self.assertNotEqual(issue["cleanup_paths"][0], source_trace)

        angle = math.radians(31.0)
        cosine = math.cos(angle)
        sine = math.sin(angle)
        rotated = [
            (
                point[0] * cosine - point[1] * sine,
                point[0] * sine + point[1] * cosine,
            )
            for point in narrow_slot
        ]
        rotated_issue = external_compensation_detail_loss(rotated, 4.0)
        self.assertIsNotNone(rotated_issue)
        self.assertEqual(
            rotated_issue["cleanup_modes"],
            ("slot_medial_axis",),
        )
        rotated_path = rotated_issue["cleanup_paths"][0]
        restored_path = [
            (
                point[0] * cosine + point[1] * sine,
                -point[0] * sine + point[1] * cosine,
            )
            for point in rotated_path
        ]
        self.assertEqual(len(restored_path), 6)
        for actual, expected in zip(
            restored_path,
            issue["cleanup_paths"][0],
        ):
            self.assertAlmostEqual(actual[0], expected[0], places=6)
            self.assertAlmostEqual(actual[1], expected[1], places=6)

        reversed_issue = external_compensation_detail_loss(
            list(reversed(narrow_slot)),
            4.0,
        )
        self.assertIsNotNone(reversed_issue)
        self.assertEqual(
            reversed_issue["cleanup_paths"],
            issue["cleanup_paths"],
        )

    def test_external_compensation_accepts_an_ordinary_rectangle(self):
        rectangle = [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)]
        self.assertIsNone(external_compensation_detail_loss(rectangle, 4.0))

    def test_external_compensation_accepts_a_slot_wider_than_the_tool(self):
        wide_slot = [
            (0.0, 0.0),
            (100.0, 0.0),
            (100.0, 100.0),
            (56.0, 100.0),
            (56.0, 60.0),
            (50.0, 60.0),
            (50.0, 100.0),
            (0.0, 100.0),
        ]
        self.assertIsNone(external_compensation_detail_loss(wide_slot, 4.0))

    def test_external_compensation_keeps_svg_dogbone_on_main_profile(self):
        # Reduced copy of the 3.175 mm Dogbone used by the reported table SVG.
        # With a Ø4 cutter it must not become an independent plunge/cleanup
        # cycle; the caller splices its residual stroke into the engaged
        # outside profile at the same Z.
        dogbone_relief = [
            (0.0, 0.0), (0.0, 15.5), (47.754936, 15.5),
            (48.032867, 15.278357), (48.35315, 15.124117),
            (48.699724, 15.045014), (49.055212, 15.045014),
            (49.401786, 15.124117), (49.722069, 15.278357),
            (50.0, 15.5), (50.221643, 15.777931),
            (50.375883, 16.098214), (50.454986, 16.444788),
            (50.454986, 16.800276), (50.375883, 17.14685),
            (50.221643, 17.467133), (50.0, 17.745064),
            (50.0, 28.754936), (50.221643, 29.032867),
            (50.375883, 29.35315), (50.454986, 29.699724),
            (50.454986, 30.055212), (50.375883, 30.401786),
            (50.221643, 30.722069), (50.0, 31.0),
            (49.722069, 31.221643), (49.401786, 31.375883),
            (49.055212, 31.454986), (48.699724, 31.454986),
            (48.35315, 31.375883), (48.032867, 31.221643),
            (47.754936, 31.0), (0.0, 31.0), (0.0, 46.5),
            (100.0, 46.5), (100.0, 0.0),
        ]

        issue = external_compensation_detail_loss(dogbone_relief, 4.0)

        self.assertIsNotNone(issue)
        self.assertEqual(issue["cleanup_paths"], ())
        self.assertEqual(len(issue["profile_relief_paths"]), 2)
        self.assertTrue(
            all(
                len(path) == 2
                and sum(
                    math.hypot(
                        path[index + 1][0] - path[index][0],
                        path[index + 1][1] - path[index][1],
                    )
                    for index in range(len(path) - 1)
                ) < 2.0
                for path in issue["profile_relief_paths"]
            )
        )

    def test_external_compensation_still_reports_short_straight_slot(self):
        short_slot = [
            (0.0, 0.0),
            (100.0, 0.0),
            (100.0, 100.0),
            (52.0, 100.0),
            (52.0, 99.0),
            (50.0, 99.0),
            (50.0, 100.0),
            (0.0, 100.0),
        ]

        issue = external_compensation_detail_loss(short_slot, 4.0)

        self.assertIsNotNone(issue)
        self.assertEqual(issue["cleanup_modes"], ("slot_centerline",))
        self.assertEqual(
            issue["cleanup_paths"],
            (((51.0, 100.0), (51.0, 99.0)),),
        )

    def test_drilling_ignores_hole_diameter_and_uses_center(self):
        holes = [
            {"x": 20.0, "y": 30.0, "diameter_mm": 3.0, "depth_mm": 10.0},
        ]
        moves = build_drill_moves(
            holes,
            final_depth=15.5,
            stepdown=4.0,
            safe_height=8.0,
            material_thickness=15.0,
            peck_enabled=True,
            peck_step=4.0,
        )

        drill_moves = [move for move in moves if move["type"] == "feed_drill"]
        self.assertEqual([move["z"] for move in drill_moves], [-4.0, -8.0, -10.0])
        self.assertTrue(all(move["x"] == 20.0 and move["y"] == 30.0 for move in drill_moves))

    def test_through_hole_includes_material_depth_extra(self):
        holes = [
            {"x": 20.0, "y": 30.0, "diameter_mm": 35.0, "depth_mm": 15.0},
        ]
        moves = build_drill_moves(
            holes,
            final_depth=15.5,
            stepdown=6.0,
            safe_height=8.0,
            material_thickness=15.0,
            peck_enabled=True,
            peck_step=6.0,
        )

        drill_depths = [move["z"] for move in moves if move["type"] == "feed_drill"]
        self.assertEqual(drill_depths, [-6.0, -12.0, -15.5])

    def test_smaller_tool_uses_helical_entry_and_opens_hole_diameter(self):
        holes = [
            {"x": 20.0, "y": 30.0, "diameter_mm": 10.0, "depth_mm": 6.0},
        ]
        moves = build_drill_moves(
            holes,
            final_depth=15.5,
            stepdown=3.0,
            safe_height=8.0,
            tool_diameter=6.0,
            material_thickness=15.0,
            use_helical=True,
            peck_enabled=True,
            peck_step=3.0,
        )

        self.assertTrue(any(move["type"] == "feed_helix" for move in moves))
        self.assertFalse(any(move["type"] == "feed_drill" for move in moves))
        self.assertGreaterEqual(
            sum(move["type"] == "feed_helix" for move in moves),
            144,
        )
        cutting_moves = [
            move for move in moves if move["type"] in {"feed_helix", "feed_cut"}
        ]
        max_radius = max(
            math.hypot(move["x"] - 20.0, move["y"] - 30.0)
            for move in cutting_moves
        )
        self.assertAlmostEqual(max_radius, 2.0, places=6)
        self.assertAlmostEqual(min(move["z"] for move in cutting_moves), -6.0)

    def test_nominal_import_rounding_does_not_create_microscopic_helix(self):
        moves = build_drill_moves(
            [{"x": 20.0, "y": 30.0, "diameter_mm": 3.1755}],
            final_depth=10.0,
            stepdown=3.0,
            safe_height=8.0,
            tool_diameter=3.175,
            material_thickness=10.0,
            use_helical=True,
        )

        self.assertFalse(any(move["type"] == "feed_helix" for move in moves))
        self.assertEqual(
            [(move["x"], move["y"]) for move in moves if move["type"] == "feed_drill"],
            [(20.0, 30.0)],
        )

    def test_equal_or_larger_tool_keeps_vertical_stepdown(self):
        holes = [
            {"x": 20.0, "y": 30.0, "diameter_mm": 6.0, "depth_mm": 6.0},
        ]
        moves = build_drill_moves(
            holes,
            final_depth=15.5,
            stepdown=3.0,
            safe_height=8.0,
            tool_diameter=6.0,
            material_thickness=15.0,
            use_helical=True,
            peck_enabled=True,
            peck_step=3.0,
        )

        self.assertFalse(any(move["type"] == "feed_helix" for move in moves))
        self.assertEqual(
            [move["z"] for move in moves if move["type"] == "feed_drill"],
            [-3.0, -6.0],
        )

    def test_counterbore_opens_flat_screw_head_seat_before_main_hole(self):
        moves = build_drill_moves(
            [{"x": 20.0, "y": 30.0, "diameter_mm": 5.0, "depth_mm": 12.0}],
            final_depth=15.5,
            stepdown=3.0,
            safe_height=8.0,
            tool_diameter=4.0,
            material_thickness=15.0,
            use_helical=True,
            counterbore_enabled=True,
            counterbore_diameter=10.0,
            counterbore_depth=3.0,
            tool_type="end_mill",
        )

        seat_moves = [move for move in moves if move.get("counterbore")]
        self.assertTrue(any(move["type"] == "feed_helix" for move in seat_moves))
        seat_cutting = [
            move
            for move in seat_moves
            if move["type"] in {"feed_helix", "feed_cut"}
        ]
        self.assertAlmostEqual(min(move["z"] for move in seat_cutting), -3.0)
        self.assertAlmostEqual(
            max(
                math.hypot(move["x"] - 20.0, move["y"] - 30.0)
                for move in seat_cutting
            ),
            3.0,
        )
        main_cutting = [
            move
            for move in moves
            if not move.get("counterbore")
            and move["type"] in {"feed_drill", "feed_helix", "feed_cut"}
        ]
        self.assertAlmostEqual(min(move["z"] for move in main_cutting), -12.0)
        self.assertLess(
            max(index for index, move in enumerate(moves) if move.get("counterbore")),
            min(
                index
                for index, move in enumerate(moves)
                if not move.get("counterbore")
                and move["type"] in {"feed_drill", "feed_helix", "feed_cut"}
            ),
        )

    def test_counterbore_depth_is_relative_to_initial_z(self):
        moves = build_drill_moves(
            [{"x": 0.0, "y": 0.0, "diameter_mm": 5.0, "depth_mm": 10.0}],
            final_depth=15.5,
            stepdown=3.0,
            safe_height=8.0,
            tool_diameter=4.0,
            start_depth=2.0,
            counterbore_enabled=True,
            counterbore_diameter=10.0,
            counterbore_depth=3.0,
        )
        seat_cutting = [
            move
            for move in moves
            if move.get("counterbore")
            and move["type"] in {"feed_plunge", "feed_helix", "feed_cut"}
        ]
        self.assertEqual(min(move["z"] for move in seat_cutting), -5.0)

    def test_counterbore_rejects_non_flat_tool_and_invalid_diameter(self):
        hole = [{"x": 0.0, "y": 0.0, "diameter_mm": 5.0, "depth_mm": 10.0}]
        common = dict(
            final_depth=15.5,
            stepdown=3.0,
            safe_height=8.0,
            tool_diameter=6.0,
            counterbore_enabled=True,
            counterbore_depth=3.0,
        )
        with self.assertRaisesRegex(ValueError, "menor que a fresa"):
            build_drill_moves(hole, counterbore_diameter=4.0, **common)
        with self.assertRaisesRegex(ValueError, "fresa de topo"):
            build_drill_moves(
                hole,
                counterbore_diameter=10.0,
                tool_type="drill",
                **common,
            )

    def test_non_peck_drilling_descends_directly_to_final_depth(self):
        holes = [{"x": 20.0, "y": 30.0, "diameter_mm": 3.0, "depth_mm": 10.0}]
        moves = build_drill_moves(
            holes,
            final_depth=15.5,
            stepdown=3.0,
            safe_height=8.0,
            tool_diameter=6.0,
            material_thickness=15.0,
            peck_enabled=False,
        )
        self.assertEqual(
            [move["z"] for move in moves if move["type"] == "feed_drill"],
            [-10.0],
        )

    def test_peck_drilling_retracts_to_previous_step_and_dwells(self):
        holes = [{"x": 20.0, "y": 30.0, "diameter_mm": 3.0, "depth_mm": 6.0}]
        moves = build_drill_moves(
            holes,
            final_depth=8.0,
            stepdown=3.0,
            safe_height=8.0,
            tool_diameter=6.0,
            material_thickness=8.0,
            start_depth=2.0,
            use_model_depths=False,
            hole_depth_override=6.0,
            peck_enabled=True,
            peck_step=2.0,
            retract_mode="previous_step",
            retract_clearance=0.5,
            dwell_seconds=0.25,
        )
        self.assertEqual(
            [move["z"] for move in moves if move["type"] == "feed_drill"],
            [-4.0, -6.0, -8.0],
        )
        retracts = [
            move["z"]
            for move in moves
            if move["type"] == "rapid" and move.get("x") is None
        ]
        self.assertEqual(retracts, [-1.5, -3.5, 8.0])
        self.assertEqual(
            [move["seconds"] for move in moves if move["type"] == "dwell"],
            [0.25, 0.25, 0.25],
        )

    def test_hole_order_can_follow_selection_or_nearest_neighbor(self):
        holes = [
            {"x": 100.0, "y": 0.0, "diameter_mm": 3.0, "depth_mm": 3.0},
            {"x": 5.0, "y": 0.0, "diameter_mm": 3.0, "depth_mm": 3.0},
        ]
        selected_order = build_drill_moves(
            holes,
            final_depth=3.0,
            stepdown=3.0,
            safe_height=8.0,
            preserve_order=True,
        )
        optimized_order = build_drill_moves(
            holes,
            final_depth=3.0,
            stepdown=3.0,
            safe_height=8.0,
            preserve_order=False,
        )
        self.assertEqual(selected_order[0]["x"], 100.0)
        self.assertEqual(optimized_order[0]["x"], 5.0)

    def test_profile_cut_respects_initial_depth(self):
        rectangle = [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)]
        moves = build_profile_cut_moves(
            rectangle,
            final_depth=5.0,
            stepdown=3.0,
            ramp_length=0.0,
            safe_height=8.0,
            tool_diameter=0.0,
            cut_side=CUT_SIDE_ON_LINE,
            smart_entry=False,
            start_depth=2.0,
        )
        plunges = [move["z"] for move in moves if move["type"] == "feed_plunge"]
        self.assertEqual(plunges, [-2.0, -5.0])

    def test_machining_job_drills_before_cutting(self):
        rectangle = [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)]
        holes = [{"x": 25.0, "y": 25.0, "diameter_mm": 2.0, "depth_mm": 5.0}]
        moves = build_machining_job(
            [rectangle],
            holes,
            final_depth=15.5,
            stepdown=3.0,
            ramp_length=0.0,
            safe_height=8.0,
            tool_diameter=6.0,
            cut_side=CUT_SIDE_OUTSIDE,
            material_thickness=15.0,
            drill_holes=True,
        )

        last_drill = max(index for index, move in enumerate(moves) if move["type"] == "feed_drill")
        first_cut = min(index for index, move in enumerate(moves) if move["type"] == "feed_cut")
        self.assertLess(last_drill, first_cut)

    def test_machining_job_can_disable_drilling(self):
        rectangle = [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)]
        holes = [{"x": 25.0, "y": 25.0, "diameter_mm": 2.0, "depth_mm": 5.0}]
        moves = build_machining_job(
            [rectangle],
            holes,
            final_depth=15.5,
            stepdown=3.0,
            ramp_length=0.0,
            safe_height=8.0,
            tool_diameter=6.0,
            cut_side=CUT_SIDE_OUTSIDE,
            material_thickness=15.0,
            drill_holes=False,
        )

        self.assertFalse(any(move["type"] == "feed_drill" for move in moves))

    def test_stages_start_and_return_at_configured_point(self):
        rectangle = [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)]
        stages = build_machining_stages(
            [rectangle],
            [],
            final_depth=6.0,
            stepdown=3.0,
            ramp_length=0.0,
            safe_height=8.0,
            tool_diameter=6.0,
            cut_side=CUT_SIDE_OUTSIDE,
            material_thickness=6.0,
            drill_holes=False,
            cut_enabled=True,
            start_xy=(12.0, 34.0),
            return_to_start=True,
        )

        self.assertEqual(
            stages["cut"][0],
            {"type": "rapid", "x": 12.0, "y": 34.0, "z": 8.0},
        )
        self.assertEqual(
            stages["cut"][-1],
            {"type": "rapid", "x": 12.0, "y": 34.0, "z": 8.0},
        )

    def test_stages_are_ordered_and_roughing_leaves_allowance(self):
        rectangle = [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)]
        holes = [{"x": 20.0, "y": 20.0, "diameter_mm": 10.0, "depth_mm": 3.0}]
        stages = build_machining_stages(
            [rectangle],
            holes,
            final_depth=6.0,
            stepdown=3.0,
            ramp_length=0.0,
            safe_height=8.0,
            tool_diameter=6.0,
            cut_side=CUT_SIDE_OUTSIDE,
            material_thickness=6.0,
            drill_holes=True,
            roughing_enabled=True,
            roughing_allowance=1.0,
            cut_enabled=True,
        )

        self.assertEqual(list(stages), ["holes", "roughing", "cut"])

        def x_extent(moves):
            xs = [
                move["x"]
                for move in moves
                if move.get("x") is not None and move["type"] == "feed_cut"
            ]
            return min(xs), max(xs)

        self.assertEqual(x_extent(stages["roughing"]), (-4.0, 104.0))
        self.assertEqual(x_extent(stages["cut"]), (-3.0, 103.0))

    def test_nested_contours_are_classified_as_internal(self):
        outer = [(0.0, 0.0), (100.0, 0.0), (100.0, 80.0), (0.0, 80.0)]
        hole = [(20.0, 20.0), (40.0, 20.0), (40.0, 40.0), (20.0, 40.0)]
        separate = [(150.0, 0.0), (200.0, 0.0), (200.0, 50.0), (150.0, 50.0)]

        external, internal = split_nested_contours([outer, hole, separate])

        self.assertEqual(external, [outer, separate])
        self.assertEqual(internal, [hole])

        # Cutting only the slot must retain its role from the full drawing;
        # otherwise CAM offsets it outward and treats it as a separate piece.
        external, internal = split_nested_contours(
            [hole], reference_contours=[outer, hole, separate]
        )
        self.assertEqual(external, [])
        self.assertEqual(internal, [hole])

    def test_offset_pocket_clears_area_at_every_depth_layer(self):
        outer = [(0.0, 0.0), (100.0, 0.0), (100.0, 60.0), (0.0, 60.0)]
        moves = build_pocket_stage(
            [outer],
            [],
            final_depth=4.0,
            stepdown=2.0,
            ramp_length=0.0,
            safe_height=8.0,
            tool_diameter=10.0,
            stepover_percent=50.0,
            strategy="offset",
            profile_pass=False,
            material_thickness=15.0,
            return_to_start=False,
        )
        cuts = [move for move in moves if move["type"] == "feed_cut"]
        self.assertEqual(sorted(set(move["z"] for move in cuts)), [-4.0, -2.0])
        self.assertEqual(min(move["x"] for move in cuts), 5.0)
        self.assertEqual(max(move["x"] for move in cuts), 95.0)
        self.assertGreater(len(cuts), 16)
        xy_rapids = [
            move
            for move in moves
            if move["type"] == "rapid" and move.get("x") is not None
        ]
        z_rapids = [
            move
            for move in moves
            if move["type"] == "rapid" and move.get("x") is None
        ]
        self.assertEqual(len(xy_rapids), 3)
        self.assertEqual(len(z_rapids), 2)
        self.assertTrue(any(move.get("pocket_link") for move in moves))

    def test_raster_pocket_preserves_internal_island(self):
        outer = [(0.0, 0.0), (100.0, 0.0), (100.0, 60.0), (0.0, 60.0)]
        island = [(40.0, 20.0), (60.0, 20.0), (60.0, 40.0), (40.0, 40.0)]
        moves = build_pocket_stage(
            [outer],
            [island, island],
            final_depth=2.0,
            stepdown=2.0,
            ramp_length=0.0,
            safe_height=8.0,
            tool_diameter=10.0,
            stepover_percent=50.0,
            strategy="raster",
            profile_pass=False,
            return_to_start=False,
        )
        cuts = [move for move in moves if move["type"] == "feed_cut"]
        self.assertTrue(cuts)
        self.assertFalse(
            any(
                35.0 < move["x"] < 65.0 and 15.0 < move["y"] < 45.0
                for move in cuts
            )
        )
        self.assertTrue(any(move["x"] == 35.0 for move in cuts))
        self.assertTrue(any(move["x"] == 65.0 for move in cuts))
        self.assertTrue(any(move.get("pocket_link") for move in moves))
        self.assertGreater(
            len(
                [
                    move
                    for move in moves
                    if move["type"] == "rapid" and move.get("x") is None
                ]
            ),
            1,
        )

    def test_pocket_finishes_all_depths_before_moving_to_next_area(self):
        left = [(0.0, 0.0), (60.0, 0.0), (60.0, 40.0), (0.0, 40.0)]
        right = [(500.0, 0.0), (560.0, 0.0), (560.0, 40.0), (500.0, 40.0)]
        moves = build_pocket_stage(
            [right, left],
            [],
            final_depth=4.0,
            stepdown=2.0,
            ramp_length=0.0,
            safe_height=8.0,
            tool_diameter=10.0,
            stepover_percent=50.0,
            strategy="offset",
            profile_pass=False,
            start_xy=(0.0, 0.0),
            return_to_start=False,
        )
        area_starts = [
            "left" if move["x"] < 250.0 else "right"
            for move in moves
            if move["type"] == "rapid"
            and move.get("x") is not None
        ]
        self.assertEqual(area_starts, ["left", "left", "left", "right", "right"])

    def test_pocket_profile_pass_runs_after_area_fill(self):
        outer = [(0.0, 0.0), (100.0, 0.0), (100.0, 60.0), (0.0, 60.0)]
        without_profile = build_pocket_stage(
            [outer],
            [],
            2.0,
            2.0,
            0.0,
            8.0,
            10.0,
            strategy="raster",
            profile_pass=False,
            return_to_start=False,
        )
        with_profile = build_pocket_stage(
            [outer],
            [],
            2.0,
            2.0,
            0.0,
            8.0,
            10.0,
            strategy="raster",
            profile_pass=True,
            return_to_start=False,
        )
        self.assertGreater(len(with_profile), len(without_profile))
        final_cuts = [
            move for move in with_profile if move["type"] == "feed_cut"
        ]
        self.assertIn((5.0, 5.0), [(move["x"], move["y"]) for move in final_cuts])

    def test_cut_mode_processes_internals_first_and_uses_selected_outer_side(self):
        outer = [(0.0, 0.0), (100.0, 0.0), (100.0, 80.0), (0.0, 80.0)]
        hole = [(20.0, 20.0), (40.0, 20.0), (40.0, 40.0), (20.0, 40.0)]
        moves = build_contour_cut_stage(
            [outer],
            [hole],
            final_depth=3.0,
            stepdown=3.0,
            ramp_length=0.0,
            safe_height=8.0,
            tool_diameter=6.0,
            outer_cut_side=CUT_SIDE_ON_LINE,
            material_thickness=3.0,
            smart_entry=False,
            return_to_start=False,
        )
        cut_moves = [move for move in moves if move["type"] == "feed_cut"]
        internal_indexes = [
            index
            for index, move in enumerate(cut_moves)
            if 23.0 <= move["x"] <= 37.0 and 23.0 <= move["y"] <= 37.0
        ]
        external_indexes = [
            index
            for index, move in enumerate(cut_moves)
            if move["x"] in {0.0, 100.0} or move["y"] in {0.0, 80.0}
        ]

        self.assertTrue(internal_indexes)
        self.assertTrue(external_indexes)
        self.assertLess(max(internal_indexes), min(external_indexes))

    def test_cut_mode_falls_back_to_center_drilling_when_tool_does_not_fit(self):
        outer = [(0.0, 0.0), (100.0, 0.0), (100.0, 80.0), (0.0, 80.0)]
        small_hole = {
            "x": 20.0,
            "y": 20.0,
            "diameter_mm": 3.0,
            "depth_mm": 3.0,
            "points": [(18.5, 20.0), (20.0, 21.5), (21.5, 20.0), (20.0, 18.5)],
        }
        moves = build_contour_cut_stage(
            [outer],
            [],
            final_depth=3.0,
            stepdown=3.0,
            ramp_length=0.0,
            safe_height=8.0,
            tool_diameter=6.0,
            outer_cut_side=CUT_SIDE_OUTSIDE,
            material_thickness=3.0,
            inner_holes=[small_hole],
            return_to_start=False,
        )

        drill_index = next(
            index for index, move in enumerate(moves) if move["type"] == "feed_drill"
        )
        outer_index = next(
            index
            for index, move in enumerate(moves)
            if move["type"] == "feed_cut"
        )
        self.assertLess(drill_index, outer_index)

    def test_external_cut_has_ramp_and_full_loop(self):
        rectangle = [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)]
        moves = build_external_cut_moves(rectangle, 6.0, 3.0, 30.0, 8.0, tool_diameter=6.0)
        self.assertTrue(any(move["type"] == "feed_ramp" for move in moves))
        self.assertEqual(moves[-1], {"type": "rapid", "x": None, "y": None, "z": 8.0})

    def test_common_line_cut_keeps_polyline_open(self):
        polyline = [(0.0, 0.0), (20.0, 0.0), (20.0, 10.0)]
        moves = build_common_line_cut_job(
            [polyline],
            final_depth=3.0,
            stepdown=3.0,
            ramp_length=0.0,
            safe_height=8.0,
        )

        cut_points = [
            (move["x"], move["y"])
            for move in moves
            if move["type"] == "feed_cut"
        ]
        self.assertEqual(cut_points, [(20.0, 0.0), (20.0, 10.0)])
        self.assertFalse(any(move.get("tab") for move in moves))
        self.assertTrue(
            all(move.get("common_line") for move in moves if move["type"].startswith("feed_"))
        )

    def test_common_line_cut_uses_all_depths_without_retracting_between_passes(self):
        moves = build_common_line_cut_job(
            [(0.0, 0.0), (30.0, 0.0)],
            final_depth=7.0,
            stepdown=3.0,
            ramp_length=0.0,
            safe_height=8.0,
            material_thickness=6.0,
        )

        plunges = [move["z"] for move in moves if move["type"] == "feed_plunge"]
        self.assertEqual(plunges, [-0.0, -3.0, -7.0])
        rapid_indexes = [index for index, move in enumerate(moves) if move["type"] == "rapid"]
        self.assertEqual(rapid_indexes, [0, len(moves) - 1])
        self.assertEqual(
            [(move["x"], move["y"], move["z"]) for move in moves if move["type"] == "feed_cut"],
            [(30.0, 0.0, -3.0), (0.0, 0.0, -7.0)],
        )

    def test_common_line_ramp_follows_vertices_and_cleans_same_open_path(self):
        polyline = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (20.0, 10.0)]
        moves = build_common_line_cut_job(
            [polyline],
            final_depth=3.0,
            stepdown=3.0,
            ramp_length=15.0,
            safe_height=8.0,
        )

        ramp_points = [
            (round(move["x"], 6), round(move["y"], 6), move["z"])
            for move in moves
            if move["type"] == "feed_ramp"
        ]
        self.assertEqual(ramp_points, [(10.0, 0.0, -2.0), (10.0, 5.0, -3.0)])
        cut_points = [
            (move["x"], move["y"])
            for move in moves
            if move["type"] == "feed_cut"
        ]
        self.assertEqual(
            cut_points,
            [
                (10.0, 10.0),
                (20.0, 10.0),
                (10.0, 10.0),
                (10.0, 0.0),
                (0.0, 0.0),
            ],
        )
        paired = list(zip(cut_points, cut_points[1:]))
        self.assertNotIn(((20.0, 10.0), (0.0, 0.0)), paired)

    def test_common_line_job_orders_and_reverses_by_nearest_endpoint(self):
        moves = build_common_line_cut_job(
            [
                [(0.0, 0.0), (10.0, 0.0)],
                [(80.0, 0.0), (90.0, 0.0)],
            ],
            final_depth=3.0,
            stepdown=3.0,
            ramp_length=0.0,
            safe_height=8.0,
            start_xy=(100.0, 0.0),
            return_to_start=True,
        )

        positioned_rapids = [
            (move["x"], move["y"])
            for move in moves
            if move["type"] == "rapid" and move["x"] is not None
        ]
        self.assertEqual(positioned_rapids, [(90.0, 0.0), (10.0, 0.0), (100.0, 0.0)])

    def test_common_line_shared_priority_precedes_nearer_release_perimeter(self):
        moves = build_common_line_cut_job(
            [
                {
                    "points": [(0.0, 0.0), (10.0, 0.0)],
                    "edge_tabs": (True,),
                    "priority": 1,
                },
                {
                    "points": [(80.0, 0.0), (90.0, 0.0)],
                    "edge_tabs": (False,),
                    "priority": 0,
                },
            ],
            final_depth=3.0,
            stepdown=3.0,
            ramp_length=0.0,
            safe_height=8.0,
            start_xy=(0.0, 0.0),
            tab_thickness=1.0,
        )
        positioned_rapids = [
            (move["x"], move["y"])
            for move in moves
            if move["type"] == "rapid" and move.get("x") is not None
        ]
        self.assertEqual(positioned_rapids[0], (80.0, 0.0))

    def test_common_line_cut_honors_start_depth_and_custom_passes(self):
        moves = build_common_line_cut_job(
            [[(0.0, 0.0), (20.0, 0.0)]],
            final_depth=8.0,
            stepdown=4.0,
            ramp_length=0.0,
            safe_height=8.0,
            start_depth=2.0,
            pass_depths=[1.0, 5.0],
        )

        self.assertEqual(
            [move["z"] for move in moves if move["type"] == "feed_plunge"],
            [-2.0, -3.0, -8.0],
        )

    def test_common_line_cut_rejects_closed_or_degenerate_input(self):
        with self.assertRaisesRegex(ValueError, "permanecer aberta"):
            build_common_line_cut_job(
                [[(0.0, 0.0), (10.0, 0.0), (0.0, 0.0)]],
                3.0,
                3.0,
                0.0,
                8.0,
            )
        with self.assertRaisesRegex(ValueError, "pelo menos 2 pontos"):
            build_common_line_cut_job(
                [[(0.0, 0.0), (0.0, 0.0)]],
                3.0,
                3.0,
                0.0,
                8.0,
            )
        with self.assertRaisesRegex(ValueError, "passo de profundidade"):
            build_common_line_cut_job(
                [[(0.0, 0.0), (10.0, 0.0)]],
                3.0,
                0.0,
                0.0,
                8.0,
            )

    def test_common_line_tab_interval_is_never_cut_below_retained_height(self):
        moves = build_common_line_cut_job(
            [
                {"points": [(0.0, 0.0), (12.0, 0.0)], "tab": True},
                {"points": [(12.0, 0.0), (30.0, 0.0)], "tab": False},
            ],
            final_depth=15.0,
            stepdown=5.0,
            ramp_length=8.0,
            safe_height=8.0,
            tab_thickness=3.0,
        )

        tab_moves = [move for move in moves if move.get("tab")]
        self.assertTrue(tab_moves)
        self.assertEqual(
            {round(move["z"], 6) for move in tab_moves},
            {0.0, -5.0, -12.0},
        )
        self.assertFalse(
            any(
                move.get("tab") and move["z"] < -12.0 - 1e-9
                for move in moves
            )
        )

    def test_common_line_tab_edges_stay_in_one_continuous_cut_trail(self):
        moves = build_common_line_cut_job(
            [
                {
                    "points": [
                        (0.0, 0.0),
                        (10.0, 0.0),
                        (20.0, 0.0),
                        (30.0, 0.0),
                    ],
                    "edge_tabs": (False, True, False),
                }
            ],
            final_depth=10.0,
            stepdown=5.0,
            ramp_length=0.0,
            safe_height=8.0,
            tab_thickness=2.0,
        )

        self.assertEqual(
            sum(move["type"] == "rapid" and move.get("x") is not None for move in moves),
            1,
        )
        self.assertEqual(
            sum(move["type"] == "rapid" and move.get("x") is None for move in moves),
            1,
        )
        self.assertTrue(any(move.get("tab") and move["z"] == -8.0 for move in moves))

    def test_common_line_network_accepts_closed_owner_only_trail(self):
        moves = build_common_line_cut_job(
            [
                {
                    "points": [
                        (0.0, 0.0),
                        (20.0, 0.0),
                        (20.0, 10.0),
                        (0.0, 10.0),
                        (0.0, 0.0),
                    ],
                    "edge_tabs": (False, True, False, False),
                    "priority": 1,
                },
                {
                    "points": [(30.0, 0.0), (30.0, 10.0)],
                    "edge_tabs": (False,),
                    "priority": 0,
                },
            ],
            final_depth=6.0,
            stepdown=3.0,
            ramp_length=0.0,
            safe_height=8.0,
            tab_thickness=2.0,
        )

        positioned_rapids = [
            (move["x"], move["y"])
            for move in moves
            if move["type"] == "rapid" and move.get("x") is not None
        ]
        self.assertEqual(positioned_rapids, [(30.0, 0.0), (0.0, 0.0)])
        self.assertTrue(any(move.get("tab") and move["z"] == -4.0 for move in moves))
        self.assertFalse(any(move.get("tab") and move["z"] < -8.0 for move in moves))

    def test_best_fixation_adds_geometric_minimum_to_normal_contour(self):
        rectangle = [(0.0, 0.0), (120.0, 0.0), (120.0, 60.0), (0.0, 60.0)]
        moves = build_external_cut_moves(
            rectangle,
            final_depth=10.0,
            stepdown=10.0,
            ramp_length=0.0,
            safe_height=8.0,
            tabs_enabled=True,
            tab_length=8.0,
            tab_thickness=2.0,
            tab_count=1,
            tab_best_fixation=True,
        )
        tab_points = {
            (round(move["x"], 6), round(move["y"], 6))
            for move in moves
            if move.get("tab") and move.get("x") is not None
        }
        self.assertGreaterEqual(len(tab_points), 3)

    def test_best_fixation_limits_the_real_free_span_on_a_long_strip(self):
        path = [
            (0.0, 0.0),
            (1000.0, 0.0),
            (1000.0, 50.0),
            (0.0, 50.0),
            (0.0, 0.0),
        ]
        ranges = _best_fixation_tab_ranges(
            path,
            2100.0,
            10.0,
            4,
            6.0,
        )
        centres = sorted((start + end) * 0.5 for start, end in ranges)
        gaps = [
            second - first for first, second in zip(centres, centres[1:])
        ]
        gaps.append(2100.0 - centres[-1] + centres[0])
        self.assertGreaterEqual(len(ranges), 7)
        self.assertLessEqual(max(gaps), 300.0)

    def test_common_line_custom_depth_cannot_cut_a_tab_then_restore_it(self):
        moves = build_common_line_cut_job(
            [{"points": [(0.0, 0.0), (12.0, 0.0)], "tab": True}],
            final_depth=15.0,
            stepdown=15.0,
            ramp_length=0.0,
            safe_height=8.0,
            tab_thickness=3.0,
            pass_depths=[13.0, 2.0],
        )

        cutting_depths = [
            move["z"]
            for move in moves
            if move.get("x") is not None and move["type"] != "rapid"
        ]
        self.assertGreaterEqual(min(cutting_depths), -12.0)
        self.assertEqual(
            [move["z"] for move in moves if move.get("tab")],
            [-0.0, -0.0, -12.0, -12.0],
        )

    def test_common_line_3d_tab_ramps_up_and_back_without_later_recut(self):
        moves = build_common_line_cut_job(
            [{"points": [(0.0, 0.0), (10.0, 0.0)], "tab": True}],
            final_depth=10.0,
            stepdown=5.0,
            ramp_length=5.0,
            safe_height=8.0,
            tab_thickness=2.0,
            tabs_3d=True,
        )

        final_tab_moves = [move for move in moves if move.get("tab")]
        self.assertEqual(
            [(move["x"], move["z"]) for move in final_tab_moves],
            [(5.0, -0.0), (10.0, -5.0), (5.0, -8.0), (0.0, -10.0)],
        )
        self.assertFalse(
            any(
                move.get("type") == "feed_cut"
                and move.get("x") == 5.0
                and move.get("z") < -8.0
                for move in moves
            )
        )

    def test_orthogonal_common_line_compensation_joins_grid_corners(self):
        rectangle = [(0.0, 0.0), (80.0, 0.0), (80.0, 40.0), (0.0, 40.0)]
        compensated = compensated_polygon(
            rectangle,
            6.0,
            CUT_SIDE_OUTSIDE,
            common_line_join=True,
        )

        self.assertEqual(len(compensated), 4)
        self.assertEqual(
            {(round(x, 6), round(y, 6)) for x, y in compensated},
            {(-3.0, -3.0), (83.0, -3.0), (83.0, 43.0), (-3.0, 43.0)},
        )
    def test_external_cut_does_not_retract_between_passes(self):
        rectangle = [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)]
        moves = build_external_cut_moves(rectangle, 6.0, 3.0, 30.0, 8.0, tool_diameter=6.0)
        rapid_indexes = [idx for idx, move in enumerate(moves) if move["type"] == "rapid"]
        self.assertEqual(rapid_indexes, [0, len(moves) - 1])

    def test_corner_slowdown_is_limited_to_area_around_sharp_vertices(self):
        rectangle = [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)]
        moves = build_external_cut_moves(
            rectangle,
            3.0,
            3.0,
            0.0,
            8.0,
            tool_diameter=0.0,
            compensate_external=False,
            smart_entry=False,
            corner_slowdown_enabled=True,
            corner_angle_threshold=45.0,
            corner_feed_percent=40.0,
            corner_slowdown_distance=8.0,
        )
        cut_moves = [move for move in moves if move["type"] == "feed_cut"]
        slowed = [move for move in cut_moves if move.get("corner_slowdown")]
        normal = [move for move in cut_moves if not move.get("corner_slowdown")]

        self.assertTrue(slowed)
        self.assertTrue(normal)
        self.assertTrue(all(move["feed_scale"] == 0.4 for move in slowed))
        self.assertTrue(any(move["x"] == 100.0 and move["y"] == 0.0 for move in slowed))
        self.assertTrue(any(move["x"] == 100.0 and move["y"] == 8.0 for move in slowed))

    def test_corner_slowdown_ignores_turns_below_angle_threshold(self):
        rectangle = [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)]
        moves = build_external_cut_moves(
            rectangle,
            3.0,
            3.0,
            0.0,
            8.0,
            tool_diameter=0.0,
            compensate_external=False,
            smart_entry=False,
            corner_slowdown_enabled=True,
            corner_angle_threshold=100.0,
            corner_feed_percent=40.0,
            corner_slowdown_distance=8.0,
        )
        self.assertFalse(any(move.get("corner_slowdown") for move in moves))

    def test_long_ramp_keeps_contour_vertices(self):
        rectangle = [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)]
        moves = build_external_cut_moves(
            rectangle,
            3.0,
            3.0,
            113.0,
            8.0,
            tool_diameter=0.0,
            compensate_external=False,
            smart_entry=False,
        )
        ramp_points = [
            (round(move["x"], 6), round(move["y"], 6))
            for move in moves
            if move["type"] == "feed_ramp"
        ]
        self.assertIn((100.0, 0.0), ramp_points)

    def test_zigzag_ramp_stays_on_compensated_contour(self):
        rectangle = [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)]
        moves = build_external_cut_moves(
            rectangle,
            3.0,
            3.0,
            60.0,
            8.0,
            compensate_external=False,
            smart_entry=False,
            ramp_type="zigzag",
        )
        ramp_moves = [move for move in moves if move["type"] == "feed_ramp"]
        self.assertTrue(ramp_moves)
        self.assertTrue(
            all(
                move["x"] in {0.0, 100.0} or move["y"] in {0.0, 50.0}
                for move in ramp_moves
            )
        )
        self.assertAlmostEqual(ramp_moves[-1]["z"], -3.0)

    def test_spiral_ramp_is_a_circular_helix_at_entry(self):
        rectangle = [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)]
        moves = build_external_cut_moves(
            rectangle,
            3.0,
            3.0,
            30.0,
            8.0,
            compensate_external=False,
            smart_entry=False,
            ramp_type="spiral",
        )
        ramp_moves = [move for move in moves if move["type"] == "feed_ramp"]
        self.assertTrue(ramp_moves)
        self.assertEqual(len(ramp_moves), 32)
        center_x, center_y = 0.0, -30.0 / (2.0 * math.pi)
        radii = [
            math.hypot(move["x"] - center_x, move["y"] - center_y)
            for move in ramp_moves
        ]
        self.assertTrue(all(abs(radius - radii[0]) < 1e-6 for radius in radii))
        self.assertAlmostEqual(ramp_moves[-1]["x"], 0.0, places=6)
        self.assertAlmostEqual(ramp_moves[-1]["y"], 0.0, places=6)
        self.assertAlmostEqual(ramp_moves[-1]["z"], -3.0)

    def test_gcode_uses_ramp_feed(self):
        moves = [{"type": "feed_ramp", "x": 10.0, "y": 0.0, "z": -3.0}]
        lines = build_gcode(18000, 500, 1800, 4000, 8, 15, moves, ramp_feed=1080)
        self.assertIn("G1 X10.0000 Y0.0000 Z-3.0000 F1080", lines)

    def test_gcode_uses_reduced_feed_only_for_corner_moves(self):
        moves = [
            {"type": "feed_cut", "x": 90.0, "y": 0.0, "z": -3.0},
            {
                "type": "feed_cut",
                "x": 100.0,
                "y": 0.0,
                "z": -3.0,
                "feed_scale": 0.4,
            },
            {"type": "feed_cut", "x": 100.0, "y": 20.0, "z": -3.0},
        ]
        lines = build_gcode(22000, 500, 2700, 4000, 8, 15, moves)
        self.assertIn("G1 X90.0000 Y0.0000 Z-3.0000 F2700", lines)
        self.assertIn("G1 X100.0000 Y0.0000 Z-3.0000 F1080", lines)
        self.assertIn("G1 X100.0000 Y20.0000 Z-3.0000 F2700", lines)

    def test_gcode_uses_vertical_feed_for_drilling(self):
        moves = [{"type": "feed_drill", "x": 10.0, "y": 20.0, "z": -5.0}]
        lines = build_gcode(18000, 500, 1800, 4000, 8, 15, moves)
        self.assertIn("G1 X10.0000 Y20.0000 Z-5.0000 F500", lines)

    def test_gcode_uses_ramp_feed_for_helical_entry(self):
        moves = [{"type": "feed_helix", "x": 10.0, "y": 20.0, "z": -1.0}]
        lines = build_gcode(
            18000,
            500,
            1800,
            4000,
            8,
            15,
            moves,
            ramp_feed=900,
            job_name="Furos",
        )
        self.assertIn("(Etapa: Furos)", lines)
        self.assertIn("G1 X10.0000 Y20.0000 Z-1.0000 F900", lines)

    def test_gcode_writes_dwell_time(self):
        lines = build_gcode(
            18000,
            500,
            1800,
            4000,
            8,
            15,
            [{"type": "dwell", "seconds": 0.25}],
        )
        self.assertIn("G4 P0.250", lines)

    def test_gcode_goes_directly_to_first_cut_start(self):
        moves = [{"type": "rapid", "x": 10.0, "y": 20.0, "z": 8.0}]
        lines = build_gcode(18000, 500, 1800, 4000, 8, 15, moves)
        self.assertNotIn("G0 X0 Y0", lines[:12])
        self.assertIn("G0 X10.0000 Y20.0000 Z8.0000", lines)

    def test_gcode_returns_to_configured_home(self):
        lines = build_gcode(
            18000,
            500,
            1800,
            4000,
            23,
            30,
            [{"type": "rapid", "x": 10.0, "y": 20.0, "z": 23.0}],
            home_x=12.0,
            home_y=34.0,
            z_zero_mode="machine_bed",
            material_thickness=15.0,
        )
        self.assertIn("(Z zero: mesa da maquina)", lines)
        self.assertIn("(Espessura do material: 15.0000 mm)", lines)
        self.assertEqual(lines[-2], "G0 X12.0000 Y34.0000")

    def test_external_cut_job_processes_multiple_contours(self):
        first = [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)]
        second = [(200.0, 0.0), (260.0, 0.0), (260.0, 40.0), (200.0, 40.0)]
        moves = build_external_cut_job(
            [first, second],
            3.0,
            3.0,
            20.0,
            8.0,
            tool_diameter=6.0,
        )
        rapid_start_moves = [
            move
            for move in moves
            if move["type"] == "rapid" and move["x"] is not None and move["y"] is not None
        ]
        self.assertEqual(len(rapid_start_moves), 2)

    def test_external_cut_job_applies_external_offset_once(self):
        rectangle = [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)]
        moves = build_external_cut_job(
            [rectangle],
            3.0,
            3.0,
            0.0,
            8.0,
            tool_diameter=6.0,
        )
        xs = [move["x"] for move in moves if move.get("x") is not None]
        ys = [move["y"] for move in moves if move.get("y") is not None]

        self.assertGreaterEqual(min(xs), -3.000001)
        self.assertLessEqual(max(xs), 103.000001)
        self.assertGreaterEqual(min(ys), -3.000001)
        self.assertLessEqual(max(ys), 53.000001)

    def test_order_contours_rotates_nearest_start(self):
        contour = [(100.0, 0.0), (100.0, 50.0), (0.0, 50.0), (0.0, 0.0)]
        ordered = order_contours_by_nearest([contour], start_xy=(0.0, 0.0))
        self.assertEqual(ordered[0][0], (0.0, 0.0))

    def test_smart_entry_does_not_pick_exact_corner(self):
        rectangle = [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)]
        entry_distance = select_entry_distance(
            rectangle,
            current_xy=(0.0, 0.0),
            ramp_length=30.0,
            tool_diameter=6.0,
        )
        self.assertNotIn(round(entry_distance, 6), {0.0, 100.0, 150.0, 250.0})

    def test_smart_cut_starts_on_non_corner_candidate(self):
        rectangle = [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)]
        moves = build_external_cut_moves(
            rectangle,
            3.0,
            3.0,
            30.0,
            8.0,
            tool_diameter=0.0,
            compensate_external=False,
            smart_entry=True,
        )
        first_rapid = moves[0]
        self.assertNotIn(
            (round(first_rapid["x"], 6), round(first_rapid["y"], 6)),
            {(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)},
        )

    def test_smart_entry_preserves_original_closing_vertex(self):
        rectangle = [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)]
        moves = build_external_cut_moves(
            rectangle,
            3.0,
            3.0,
            30.0,
            8.0,
            tool_diameter=0.0,
            compensate_external=False,
            smart_entry=True,
        )
        cut_points = [
            (round(move["x"], 6), round(move["y"], 6))
            for move in moves
            if move["type"] == "feed_cut"
        ]
        self.assertIn((0.0, 0.0), cut_points)
        top_left_index = cut_points.index((0.0, 50.0))
        self.assertEqual(cut_points[top_left_index + 1], (0.0, 0.0))

    def test_explicit_cut_direction_changes_contour_order(self):
        rectangle = [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)]

        climb_moves = build_external_cut_moves(
            rectangle,
            3.0,
            3.0,
            0.0,
            8.0,
            compensate_external=False,
            smart_entry=False,
            climb=True,
        )
        conventional_moves = build_external_cut_moves(
            rectangle,
            3.0,
            3.0,
            0.0,
            8.0,
            compensate_external=False,
            smart_entry=False,
            climb=False,
        )

        climb_cut_points = [
            (move["x"], move["y"])
            for move in climb_moves
            if move["type"] == "feed_cut"
        ]
        conventional_cut_points = [
            (move["x"], move["y"])
            for move in conventional_moves
            if move["type"] == "feed_cut"
        ]
        self.assertEqual(climb_cut_points[0], (100.0, 50.0))
        self.assertEqual(conventional_cut_points[0], (100.0, 0.0))

    def test_automatic_tabs_retain_the_piece_from_the_first_pass(self):
        rectangle = [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)]
        moves = build_external_cut_moves(
            rectangle,
            15.0,
            10.0,
            0.0,
            8.0,
            compensate_external=False,
            smart_entry=False,
            tabs_enabled=True,
            tab_length=12.0,
            tab_thickness=3.0,
            tab_count=4,
        )

        tab_moves = [move for move in moves if move.get("tab")]
        self.assertTrue(tab_moves)
        self.assertEqual(
            {round(move["z"], 6) for move in tab_moves},
            {0.0, -12.0},
        )
        self.assertTrue(
            any(
                move["type"] == "feed_cut"
                and not move.get("tab")
                and move["z"] == -15.0
                for move in moves
            )
        )

    def test_tab_height_is_physical_material_left_from_the_top(self):
        self.assertEqual(
            tab_retained_cut_depth(15.0, 3.0, final_depth=15.5),
            12.0,
        )
        self.assertEqual(
            tab_retained_cut_depth(15.0, 3.0, final_depth=18.0),
            12.0,
        )
        moves = build_common_line_cut_job(
            [
                {"points": [(0.0, 0.0), (12.0, 0.0)], "tab": True},
                {"points": [(12.0, 0.0), (30.0, 0.0)], "tab": False},
            ],
            final_depth=15.5,
            stepdown=5.0,
            ramp_length=0.0,
            safe_height=8.0,
            material_thickness=15.0,
            tab_thickness=15.0,
            tabs_3d=True,
        )
        tab_moves = [move for move in moves if move.get("tab")]
        self.assertTrue(tab_moves)
        self.assertTrue(all(move["z"] == 0.2 for move in tab_moves))
        self.assertFalse(any(move.get("tab") and move["z"] < 0.0 for move in moves))

    def test_disabled_tabs_ignore_dormant_height_larger_than_material(self):
        moves = build_external_cut_moves(
            [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)],
            final_depth=6.0,
            stepdown=3.0,
            ramp_length=0.0,
            safe_height=8.0,
            compensate_external=False,
            smart_entry=False,
            material_thickness=3.0,
            tabs_enabled=False,
            tab_thickness=15.0,
        )

        self.assertTrue(moves)
        self.assertFalse(any(move.get("tab") for move in moves))
        self.assertTrue(
            any(
                move.get("type") == "feed_cut" and move.get("z") == -6.0
                for move in moves
            )
        )

        common_moves = build_common_line_cut_job(
            [
                {
                    "points": [(0.0, 0.0), (50.0, 0.0), (100.0, 0.0)],
                    "edge_tabs": (False, False),
                }
            ],
            final_depth=6.0,
            stepdown=3.0,
            ramp_length=0.0,
            safe_height=8.0,
            material_thickness=3.0,
            tab_thickness=15.0,
        )
        self.assertTrue(common_moves)
        self.assertFalse(any(move.get("tab") for move in common_moves))
        self.assertTrue(
            any(
                move.get("type") == "feed_cut" and move.get("z") == -6.0
                for move in common_moves
            )
        )

    def test_initial_depth_does_not_lower_the_physical_tab(self):
        self.assertEqual(
            tab_retained_cut_depth(
                15.0,
                14.0,
                final_depth=15.5,
                start_depth=6.0,
            ),
            1.0,
        )
        moves = build_profile_cut_moves(
            [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)],
            final_depth=15.5,
            stepdown=6.0,
            ramp_length=0.0,
            safe_height=8.0,
            material_thickness=15.0,
            start_depth=6.0,
            tabs_enabled=True,
            tab_length=10.0,
            tab_thickness=14.0,
            tab_count=4,
        )
        tab_depths = [move["z"] for move in moves if move.get("tab")]
        self.assertTrue(tab_depths)
        self.assertGreaterEqual(min(tab_depths), -1.0)

    def test_high_tabs_use_lower_automatic_density(self):
        path = [
            (0.0, 0.0),
            (1000.0, 0.0),
            (1000.0, 50.0),
            (0.0, 50.0),
            (0.0, 0.0),
        ]
        ranges = _best_fixation_tab_ranges(
            path,
            2100.0,
            10.0,
            4,
            6.0,
            15.0,
            14.0,
        )
        centres = sorted((start + end) * 0.5 for start, end in ranges)
        gaps = [
            second - first for first, second in zip(centres, centres[1:])
        ]
        gaps.append(2100.0 - centres[-1] + centres[0])
        self.assertEqual(len(ranges), 5)
        self.assertLessEqual(max(gaps), 500.0)

    def test_manual_tab_positions_override_automatic_distribution(self):
        ranges = _tab_ranges(
            total_length=300.0,
            tab_length=12.0,
            tab_count=0,
            tab_positions=[0.5],
        )

        self.assertEqual(ranges, [(144.0, 156.0)])

    def test_manual_xy_tabs_belong_only_to_the_clicked_contour(self):
        left = [(0.0, 0.0), (100.0, 0.0), (100.0, 60.0), (0.0, 60.0)]
        right = [(300.0, 0.0), (400.0, 0.0), (400.0, 60.0), (300.0, 60.0)]
        moves = build_external_cut_job(
            (left, right),
            final_depth=6.0,
            stepdown=6.0,
            ramp_length=0.0,
            safe_height=8.0,
            compensate_external=False,
            smart_entry=False,
            tabs_enabled=True,
            tab_length=8.0,
            tab_thickness=2.0,
            tab_count=0,
            tab_positions=(
                {"x": 20.0, "y": 0.0},
                {"x": 50.0, "y": 0.0},
                {"x": 80.0, "y": 0.0},
            ),
        )

        self.assertEqual(
            {move["profile_id"] for move in moves if move.get("tab")},
            {"profile-0001"},
        )

    def test_custom_depth_pass_never_descends_below_tab_height(self):
        rectangle = [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)]
        moves = build_external_cut_moves(
            rectangle,
            15.0,
            15.0,
            0.0,
            8.0,
            compensate_external=False,
            smart_entry=False,
            tabs_enabled=True,
            tab_length=12.0,
            tab_thickness=3.0,
            tab_count=4,
            pass_depths=[13.0, 2.0],
        )

        tab_moves = [move for move in moves if move.get("tab")]
        self.assertTrue(tab_moves)
        self.assertGreaterEqual(min(move["z"] for move in tab_moves), -12.0)

    def test_gcode_identifies_selected_3d_tool(self):
        lines = build_gcode(
            18000,
            400,
            1600,
            4000,
            8,
            15,
            [{"type": "rapid", "x": 0.0, "y": 0.0, "z": 8.0}],
            tool_name="Topo esférico 6 mm",
            tool_number=5,
        )
        self.assertIn("(Ferramenta T5: Topo esférico 6 mm)", lines)


if __name__ == "__main__":
    unittest.main()
