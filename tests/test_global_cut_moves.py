import unittest
import math

from gcode_writer import build_gcode
from operations import build_external_cut_job, build_global_cut_plan_moves
from presets import DEFAULT_PRESETS
from woodcam_editor.application.common_line import CommonLineContour
from woodcam_editor.application.global_cut_plan import OwnedContour, build_global_cut_plan


def rectangle(owner_id, min_x=0.0, min_y=0.0, max_x=100.0, max_y=60.0):
    return CommonLineContour(
        owner_id,
        ((min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y)),
    )


class GlobalCutMoveTests(unittest.TestCase):
    @staticmethod
    def _estimated_seconds(moves):
        current = (0.0, 0.0, 8.0)
        total = 0.0
        for move in moves:
            target = (
                current[0] if move.get("x") is None else float(move["x"]),
                current[1] if move.get("y") is None else float(move["y"]),
                current[2] if move.get("z") is None else float(move["z"]),
            )
            length = math.dist(current, target)
            if move["type"] == "rapid":
                feed = 4000.0
            elif move["type"] in {"feed_plunge", "feed_drill"}:
                feed = 500.0
            elif move["type"] in {"feed_ramp", "feed_helix"}:
                feed = 1080.0
            else:
                feed = 1800.0 * float(move.get("feed_scale", 1.0))
            total += length / max(feed, 1.0) * 60.0
            current = target
        return total

    @staticmethod
    def _grid_contours(columns=4, rows=3, width=300.0, height=200.0):
        contours = []
        owned = []
        for row in range(rows):
            for column in range(columns):
                min_x = column * width
                min_y = row * height
                points = (
                    (min_x, min_y),
                    (min_x + width, min_y),
                    (min_x + width, min_y + height),
                    (min_x, min_y + height),
                )
                contours.append(points)
                owned.append(CommonLineContour("P-%02d-%02d" % (row, column), points))
        return tuple(contours), tuple(owned)

    def test_common_line_default_is_the_optimized_bidirectional_strategy(self):
        self.assertEqual(
            DEFAULT_PRESETS["cut_depth_strategy"],
            "hybrid_piece_bidirectional",
        )

    def test_optimized_common_line_beats_normal_cut_with_tabs_kept(self):
        contours, owned = self._grid_contours()
        normal_moves = build_external_cut_job(
            contours,
            final_depth=15.0,
            stepdown=4.0,
            ramp_length=30.0,
            safe_height=8.0,
            compensate_external=False,
            smart_entry=True,
            tabs_enabled=True,
            tab_count=3,
            tab_length=12.0,
            tab_thickness=3.0,
        )
        plan = build_global_cut_plan(
            owned,
            depths=(4.0, 8.0, 12.0, 15.0),
            strategy="hybrid_piece_bidirectional",
            release_mode="keep_tabs",
            tabs_enabled=True,
            tab_count=3,
            tab_width=12.0,
            tab_thickness=3.0,
            tool_diameter=6.0,
            through_depth=15.0,
        )
        common_moves = build_global_cut_plan_moves(
            plan,
            8.0,
            ramp_length=30.0,
            ramp_type="smooth",
        )

        self.assertLess(
            self._estimated_seconds(common_moves),
            self._estimated_seconds(normal_moves) * 0.85,
        )

    def test_optimized_release_beats_legacy_common_line_release(self):
        _contours, owned = self._grid_contours()
        options = dict(
            depths=(4.0, 8.0, 12.0, 15.0),
            release_mode="automatic_release",
            tabs_enabled=True,
            tab_count=3,
            tab_width=12.0,
            tab_thickness=3.0,
            tool_diameter=6.0,
            through_depth=15.0,
        )
        legacy = build_global_cut_plan(owned, strategy="per_piece", **options)
        optimized = build_global_cut_plan(
            owned,
            strategy="hybrid_piece_bidirectional",
            **options,
        )

        self.assertLess(
            self._estimated_seconds(
                build_global_cut_plan_moves(optimized, 8.0, ramp_length=30.0)
            ),
            self._estimated_seconds(
                build_global_cut_plan_moves(legacy, 8.0, ramp_length=30.0)
            )
            * 0.85,
        )

    def _release_plan(self, tab_width, tool_diameter):
        return build_global_cut_plan(
            (rectangle("A"),),
            depths=(5.0, 10.0, 15.5),
            strategy="hybrid_stability",
            release_mode="automatic_release",
            tabs_enabled=True,
            tab_count=3,
            tab_width=tab_width,
            tab_thickness=3.0,
            best_fixation=True,
            tool_diameter=tool_diameter,
        )

    def test_release_at_or_below_tool_width_is_plunge_then_retract(self):
        moves = build_global_cut_plan_moves(
            self._release_plan(4.0, 6.0), 8.0
        )
        grouped = {}
        for move in moves:
            if move.get("tab_release"):
                grouped.setdefault(move["tab_release_operation_id"], []).append(move)
        self.assertTrue(grouped)
        self.assertEqual(sum(move["type"] == "operator_pause" for move in moves), 1)
        for operation_moves in grouped.values():
            operation_moves = [
                move for move in operation_moves if move["type"] != "operator_pause"
            ]
            self.assertEqual(
                [move["type"] for move in operation_moves],
                ["rapid", "feed_plunge", "rapid"],
            )

    def test_wide_tab_ramps_through_bridge_and_immediately_retracts(self):
        plan = self._release_plan(10.0, 4.0)
        moves = build_global_cut_plan_moves(plan, 8.0)
        releases_by_id = {
            release.operation_id: release
            for release in plan.tab_release_operations
        }
        grouped = {}
        for move in moves:
            if move.get("tab_release"):
                grouped.setdefault(move["tab_release_operation_id"], []).append(move)
        for operation_moves in grouped.values():
            operation_moves = [
                move for move in operation_moves if move["type"] != "operator_pause"
            ]
            self.assertEqual(operation_moves[0]["type"], "rapid")
            self.assertEqual(operation_moves[1]["type"], "feed_plunge")
            self.assertTrue(
                all(move["type"] == "feed_ramp" for move in operation_moves[2:-1])
            )
            self.assertEqual(operation_moves[-1]["type"], "rapid")
            self.assertAlmostEqual(
                operation_moves[2]["tab_release_sweep_length"], 6.0
            )
            self.assertAlmostEqual(operation_moves[1]["z"], -12.5)
            self.assertAlmostEqual(operation_moves[-2]["z"], -15.5)
            self.assertAlmostEqual(operation_moves[-3]["z"], -15.5)
            self.assertTrue(operation_moves[-2]["tab_release_cleanup"])
            release = releases_by_id[
                operation_moves[-2]["tab_release_operation_id"]
            ]
            self.assertAlmostEqual(operation_moves[-3]["x"], release.plunge_point.x)
            self.assertAlmostEqual(operation_moves[-3]["y"], release.plunge_point.y)
            self.assertAlmostEqual(operation_moves[-2]["x"], release.sweep_end.x)
            self.assertAlmostEqual(operation_moves[-2]["y"], release.sweep_end.y)
            self.assertTrue(
                all(move["tab_release_zigzag"] for move in operation_moves[2:-1])
            )
            self.assertIsNone(operation_moves[-1]["x"])

    def test_full_height_tab_descends_in_alternating_zigzag_passes(self):
        plan = build_global_cut_plan(
            (rectangle("A"),),
            depths=(5.0, 10.0, 15.5),
            strategy="hybrid_stability",
            release_mode="automatic_release",
            tabs_enabled=True,
            tab_count=3,
            tab_width=10.0,
            tab_thickness=15.0,
            best_fixation=True,
            tool_diameter=4.0,
            material_thickness=15.0,
        )
        moves = build_global_cut_plan_moves(
            plan,
            8.0,
            material_thickness=15.0,
        )
        first_release_id = plan.tab_release_operations[0].operation_id
        operation_moves = [
            move
            for move in moves
            if move.get("tab_release_operation_id") == first_release_id
            and move["type"] != "operator_pause"
        ]
        self.assertEqual(operation_moves[0]["type"], "rapid")
        self.assertEqual(operation_moves[1]["type"], "feed_plunge")
        self.assertTrue(
            all(move["type"] == "feed_ramp" for move in operation_moves[2:-1])
        )
        self.assertEqual(operation_moves[-1]["type"], "rapid")
        cutting_depths = [-float(move["z"]) for move in operation_moves[1:-1]]
        self.assertEqual(cutting_depths[0], 0.0)
        self.assertAlmostEqual(cutting_depths[-1], 15.5)
        self.assertAlmostEqual(cutting_depths[-2], 15.5)
        self.assertTrue(operation_moves[-2]["tab_release_cleanup"])
        self.assertTrue(
            all(
                0.0 < current - previous <= 1.28
                for previous, current in zip(
                    cutting_depths[:-2], cutting_depths[1:-1]
                )
            )
        )
        ramp_xy = [
            (move["x"], move["y"])
            for move in operation_moves
            if move["type"] == "feed_ramp"
        ]
        self.assertGreater(len(ramp_xy), 3)
        for previous, current in zip(ramp_xy, ramp_xy[1:]):
            self.assertNotEqual(previous, current)
        self.assertIsNone(operation_moves[-1]["x"])
        gcode = build_gcode(
            18000,
            500,
            1800,
            4000,
            8.0,
            15.0,
            moves,
            ramp_feed=720.0,
        )
        self.assertTrue(
            any(line.startswith("G1 ") and "F720" in line for line in gcode)
        )

    def test_tab_release_supervision_pause_counts(self):
        plan = self._release_plan(10.0, 4.0)
        expected = {
            "all_tabs": 1,
            "per_piece": len(
                {operation.owner_id for operation in plan.tab_release_operations}
            ),
            "per_tab": len(plan.tab_release_operations),
        }
        for mode, pause_count in expected.items():
            with self.subTest(mode=mode):
                moves = build_global_cut_plan_moves(
                    plan,
                    8.0,
                    tab_release_supervision=mode,
                )
                self.assertEqual(
                    sum(move["type"] == "operator_pause" for move in moves),
                    pause_count,
                )

    def test_screw_pilots_run_before_all_cuts_with_one_operator_pause(self):
        plan = build_global_cut_plan(
            (rectangle("A", 0, 0, 100, 100),),
            internal_contours=(
                OwnedContour(
                    "cutout-A",
                    "A",
                    ((30, 30), (70, 30), (70, 70), (30, 70)),
                ),
            ),
            depths=(5.0, 10.0, 15.5),
            loose_waste_fixation="screws",
            material_thickness=15.0,
            screw_pilot_diameter=6.0,
            screw_head_diameter=8.0,
            screw_safety_margin=2.0,
            screw_head_height=3.0,
            tool_diameter=6.0,
        )
        self.assertEqual(len(plan.screw_anchors), 1)
        moves = build_global_cut_plan_moves(plan, 8.0)
        pauses = [
            index for index, move in enumerate(moves)
            if move["type"] == "operator_pause"
        ]
        self.assertEqual(len(pauses), 1)
        first_cut = next(
            index for index, move in enumerate(moves)
            if move.get("cut_phase") in {"internal", "shared", "external"}
        )
        self.assertLess(pauses[0], first_cut)
        self.assertTrue(
            all(
                move.get("cut_phase") in {None, "screw_pilot", "screw_setup"}
                for move in moves[: pauses[0] + 1]
            )
        )
        lines = build_gcode(18000, 500, 1800, 4000, 8.0, 15.0, moves)
        pause_line = lines.index("M0")
        self.assertEqual(lines[pause_line - 2], "M5")
        self.assertEqual(lines[pause_line + 1], "M3 S18000")
        self.assertTrue(lines[pause_line + 2].startswith("G4 P"))

    def test_waste_and_piece_tabs_keep_independent_physical_heights(self):
        pieces = (
            rectangle("bottom", 0, 0, 100, 30),
            rectangle("top", 0, 70, 100, 100),
            rectangle("left", 0, 30, 30, 70),
            rectangle("right", 70, 30, 100, 70),
        )
        plan = build_global_cut_plan(
            pieces,
            stock_boundary=((0, 0), (100, 0), (100, 100), (0, 100)),
            depths=(5.0, 10.0, 15.5),
            tabs_enabled=True,
            tab_count=4,
            tab_width=8.0,
            tab_thickness=3.0,
            loose_waste_fixation="tabs",
            material_thickness=15.0,
            tool_diameter=6.0,
        )
        moves = build_global_cut_plan_moves(
            plan,
            8.0,
            material_thickness=15.0,
        )
        final_tab_z = {
            move["z"]
            for move in moves
            if move.get("tab") and move.get("depth_pass") == 15.5
        }
        self.assertEqual(final_tab_z, {-12.0, 0.2})

    def test_final_tab_never_continues_laterally_after_breaking(self):
        moves = build_global_cut_plan_moves(
            self._release_plan(10.0, 4.0), 8.0
        )
        last_tab_index = max(
            index
            for index, move in enumerate(moves)
            if move.get("tab_release_last_for_piece")
            and move["type"] in {"feed_cut", "feed_ramp"}
        )
        self.assertEqual(moves[last_tab_index + 1]["type"], "rapid")
        self.assertIsNone(moves[last_tab_index + 1]["x"])
        self.assertEqual(moves[last_tab_index + 1]["z"], 8.0)

    def test_existing_gcode_writer_accepts_global_and_release_moves(self):
        moves = build_global_cut_plan_moves(
            self._release_plan(10.0, 4.0), 8.0
        )
        lines = build_gcode(
            18000, 500, 1800, 4000, 8.0, 15.0, moves
        )
        self.assertEqual(lines[-1], "M30")
        self.assertTrue(
            any(line.startswith("G1 ") and "Z-15.5000" in line for line in lines)
        )

    @staticmethod
    def _rapid_distance(moves, depths):
        current = (0.0, 0.0)
        total = 0.0
        for move in moves:
            if move.get("x") is None or move.get("y") is None:
                continue
            target = (float(move["x"]), float(move["y"]))
            if move["type"] == "rapid" and move.get("depth_pass") in depths:
                total += math.dist(current, target)
            current = target
        return total

    def _scattered_plan(self, strategy):
        outer = (
            rectangle("A", 0, 0, 100, 100),
            rectangle("B", 1000, 0, 1100, 100),
            rectangle("C", 2000, 0, 2100, 100),
        )
        internal = (
            OwnedContour("iA", "A", ((20, 20), (40, 20), (40, 40), (20, 40))),
            OwnedContour("iB", "B", ((1020, 20), (1040, 20), (1040, 40), (1020, 40))),
            OwnedContour("iC", "C", ((2020, 20), (2040, 20), (2040, 40), (2020, 40))),
        )
        return build_global_cut_plan(
            outer,
            internal_contours=internal,
            depths=(5.0, 10.0, 15.0),
            strategy=strategy,
            start_xy=(0.0, 0.0),
        )

    def test_hybrid_intermediate_layers_reduce_rapid_distance_without_geometry_change(self):
        baseline = self._scattered_plan("global_by_depth")
        hybrid = self._scattered_plan("hybrid_stability")
        baseline_moves = build_global_cut_plan_moves(
            baseline, 8.0, start_xy=(0.0, 0.0)
        )
        hybrid_moves = build_global_cut_plan_moves(
            hybrid, 8.0, start_xy=(0.0, 0.0)
        )
        baseline_distance = self._rapid_distance(baseline_moves, {5.0, 10.0})
        hybrid_distance = self._rapid_distance(hybrid_moves, {5.0, 10.0})
        self.assertLess(hybrid_distance, baseline_distance * 0.40)
        baseline_geometry = {
            (segment.segment_id, segment.start, segment.end, segment.kind)
            for segment in baseline.segments
        }
        hybrid_geometry = {
            (segment.segment_id, segment.start, segment.end, segment.kind)
            for segment in hybrid.segments
        }
        self.assertEqual(hybrid_geometry, baseline_geometry)
        self.assertEqual(
            {
                (segment_id, operation.depth)
                for operation in hybrid.operations
                for segment_id in operation.segment_ids
            },
            {
                (segment_id, operation.depth)
                for operation in baseline.operations
                for segment_id in operation.segment_ids
            },
        )

    def test_every_xy_rapid_between_hybrid_trails_occurs_at_safe_z(self):
        plan = self._scattered_plan("hybrid_stability")
        moves = build_global_cut_plan_moves(plan, 8.0, start_xy=(0.0, 0.0))
        current_z = 8.0
        previous_xy = None
        for move in moves:
            if move.get("z") is not None:
                current_z = float(move["z"])
            if (
                move["type"] == "rapid"
                and move.get("x") is not None
                and move.get("y") is not None
            ):
                target = (float(move["x"]), float(move["y"]))
                if previous_xy is not None and target != previous_xy:
                    self.assertEqual(current_z, 8.0)
                previous_xy = target
            elif move.get("x") is not None and move.get("y") is not None:
                previous_xy = (float(move["x"]), float(move["y"]))

    def test_global_trail_plunges_directly_without_duplicate_z0_stop(self):
        plan = build_global_cut_plan(
            (rectangle("A"),),
            depths=(5.0, 10.0, 15.5),
            strategy="hybrid_stability",
            tabs_enabled=True,
            tab_count=3,
            tab_width=8.0,
            tab_thickness=3.0,
            tool_diameter=4.0,
            through_depth=15.0,
        )
        moves = build_global_cut_plan_moves(plan, 8.0, start_xy=(0.0, 0.0))
        global_moves = [move for move in moves if move.get("global_cut")]
        for index, move in enumerate(global_moves):
            if move["type"] != "rapid" or move.get("x") is None:
                continue
            self.assertLess(index + 1, len(global_moves))
            plunge = global_moves[index + 1]
            self.assertEqual(plunge["type"], "feed_plunge")
            self.assertLess(float(plunge["z"]), -1.0e-7)
        for previous, current in zip(global_moves, global_moves[1:]):
            self.assertFalse(
                previous["type"] == current["type"] == "feed_plunge"
                and previous.get("x") == current.get("x")
                and previous.get("y") == current.get("y")
                and previous.get("z") == current.get("z")
            )

    def test_hybrid_connected_network_uses_one_entry_and_cleared_kerf_links(self):
        plan = build_global_cut_plan(
            (
                rectangle("A", 0.0, 0.0, 80.0, 40.0),
                rectangle("B", 80.0, 0.0, 160.0, 40.0),
            ),
            depths=(15.5,),
            through_depth=15.0,
            strategy="hybrid_stability",
        )
        moves = build_global_cut_plan_moves(plan, 8.0, start_xy=(0.0, 0.0))
        global_moves = [move for move in moves if move.get("global_cut")]
        entries = [
            move
            for move in global_moves
            if move["type"] == "rapid" and move.get("x") is not None
        ]
        self.assertEqual(len(entries), 1)
        self.assertTrue(any(move.get("cleared_path_link") for move in global_moves))
        self.assertFalse(
            any(
                move["type"] == "rapid" and move.get("x") is None
                for move in global_moves
            )
        )
        gcode = build_gcode(18000, 500, 1800, 4000, 8.0, 15.0, moves)
        first_feed = next(
            index for index, line in enumerate(gcode) if line.startswith("G1 ")
        )
        main_retract = next(
            index
            for index in range(first_feed + 1, len(gcode))
            if gcode[index] == "G0 Z8.0000"
        )
        self.assertFalse(
            any(line.startswith("G0 X") for line in gcode[first_feed:main_retract])
        )

        operation_index = {
            operation.operation_id: index
            for index, operation in enumerate(plan.operations)
        }
        emitted_at_depth = set()
        for operation in plan.operations:
            operation_moves = [
                move
                for move in global_moves
                if move.get("cut_operation_id") == operation.operation_id
            ]
            for move in operation_moves:
                if not move.get("cleared_path_link"):
                    continue
                self.assertTrue(
                    set(move["cleared_link_segment_ids"]).issubset(
                        emitted_at_depth
                    )
                )
            emitted_at_depth.update(operation.segment_ids)
        self.assertEqual(len(operation_index), len(plan.operations))

    def test_hybrid_disconnected_network_retracts_before_xy_rapid(self):
        plan = build_global_cut_plan(
            (
                rectangle("A", 0.0, 0.0, 80.0, 40.0),
                rectangle("B", 300.0, 0.0, 380.0, 40.0),
            ),
            depths=(5.0,),
            through_depth=15.0,
            strategy="hybrid_stability",
        )
        moves = build_global_cut_plan_moves(plan, 8.0, start_xy=(0.0, 0.0))
        xy_rapids = [
            index
            for index, move in enumerate(moves)
            if move.get("global_cut")
            and move["type"] == "rapid"
            and move.get("x") is not None
        ]
        self.assertEqual(len(xy_rapids), 2)
        second = xy_rapids[1]
        self.assertGreater(second, 0)
        self.assertEqual(moves[second - 1]["type"], "rapid")
        self.assertIsNone(moves[second - 1]["x"])

    def test_hybrid_next_depth_uses_previous_depth_endpoint(self):
        plan = build_global_cut_plan(
            (
                rectangle("A", 0.0, 0.0, 80.0, 40.0),
                rectangle("B", 80.0, 0.0, 160.0, 40.0),
            ),
            depths=(5.0, 10.0),
            through_depth=15.0,
            strategy="hybrid_stability",
        )
        moves = build_global_cut_plan_moves(plan, 8.0, start_xy=(0.0, 0.0))
        second_depth = [
            move
            for move in moves
            if move.get("global_cut") and move.get("depth_pass") == 10.0
        ]
        self.assertTrue(second_depth)
        self.assertFalse(
            any(
                move["type"] == "rapid" and move.get("x") is not None
                for move in second_depth
            )
        )

    def test_hybrid_depth_change_backtracks_in_previous_kerf_without_reentry(self):
        plan = build_global_cut_plan(
            (
                rectangle("A", 0.0, 0.0, 80.0, 40.0),
                rectangle("B", 80.0, 0.0, 160.0, 40.0),
                rectangle("C", 0.0, 40.0, 80.0, 80.0),
                rectangle("D", 80.0, 40.0, 160.0, 80.0),
            ),
            depths=(5.0, 10.0, 15.0, 15.5),
            through_depth=15.0,
            strategy="hybrid_stability",
            tabs_enabled=True,
            tab_count=3,
            tab_width=8.0,
            tab_thickness=3.0,
            best_fixation=True,
            tool_diameter=6.0,
        )
        moves = build_global_cut_plan_moves(plan, 8.0, start_xy=(0.0, 0.0))
        links = [
            move
            for move in moves
            if move.get("cleared_path_link")
            and move.get("link_target_depth_pass") == 15.5
            and move.get("depth_pass") == 15.0
        ]
        self.assertTrue(links)
        target_entries = [
            move
            for move in moves
            if move.get("global_cut")
            and move.get("depth_pass") == 15.5
            and move["type"] == "rapid"
            and move.get("x") is not None
        ]
        self.assertFalse(target_entries)

    def test_tab_release_geometry_and_order_are_not_changed_by_fast_route(self):
        common = dict(
            depths=(5.0, 10.0, 15.5),
            release_mode="automatic_release",
            tabs_enabled=True,
            tab_count=3,
            tab_width=10.0,
            tab_thickness=3.0,
            best_fixation=True,
            tool_diameter=4.0,
        )
        per_piece = build_global_cut_plan(
            (rectangle("A"),), strategy="per_piece", **common
        )
        hybrid = build_global_cut_plan(
            (rectangle("A"),), strategy="hybrid_stability", **common
        )
        signature = lambda plan: tuple(
            (
                operation.tab_id,
                operation.owner_id,
                operation.plunge_point,
                operation.sweep_end,
                operation.sweep_length,
                operation.last_for_piece,
            )
            for operation in plan.tab_release_operations
        )
        self.assertEqual(signature(hybrid), signature(per_piece))


if __name__ == "__main__":
    unittest.main()
