import unittest

from gcode_writer import build_gcode
from operations import (
    audit_profile_cut_moves,
    build_external_cut_moves,
    build_global_cut_plan_moves,
    build_profile_cut_job,
    build_profile_cut_moves,
    validate_profile_cut_moves,
)
from woodcam_3d.preview import moves_for_preview
from woodcam_editor.application.common_line import CommonLineContour
from woodcam_editor.application.global_cut_plan import build_global_cut_plan


RECTANGLE = [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)]


def legacy_profile_moves(*, tab_count=0, ramp_length=0.0, corner_slowdown=False):
    return build_external_cut_moves(
        RECTANGLE,
        final_depth=15.0,
        stepdown=5.0,
        ramp_length=ramp_length,
        safe_height=8.0,
        compensate_external=False,
        smart_entry=False,
        tabs_enabled=tab_count > 0,
        tab_length=12.0,
        tab_thickness=3.0,
        tab_count=tab_count,
        corner_slowdown_enabled=corner_slowdown,
    )


class TabProfileIntegrityTests(unittest.TestCase):
    def assert_one_closed_loop_per_depth(self, moves, expected_depths=(5.0, 10.0, 15.0)):
        reports = validate_profile_cut_moves(moves)
        self.assertEqual([report["depth"] for report in reports], list(expected_depths))
        for report in reports:
            self.assertEqual(report["loop_count"], 1)
            self.assertEqual(report["closed_loop_count"], 1)
            self.assertEqual(report["restart_count"], 0)
            self.assertEqual(report["duplicate_edge_count"], 0)
            self.assertEqual(report["tab_retract_count"], 0)
            self.assertAlmostEqual(report["coverage_ratio"], 1.0)
        return reports

    def test_closed_profile_without_tabs_has_one_loop_per_depth(self):
        reports = self.assert_one_closed_loop_per_depth(legacy_profile_moves())
        self.assertTrue(all(report["tab_crossing_count"] == 0 for report in reports))

    def test_one_tab_is_local_z_modulation_in_the_same_loop(self):
        moves = legacy_profile_moves(tab_count=1)
        reports = self.assert_one_closed_loop_per_depth(moves)
        final = reports[-1]
        self.assertEqual(final["tab_crossing_count"], 1)
        self.assertEqual(final["tab_transition_count"], 2)

        loop_moves = [
            move
            for move in moves
            if move.get("profile_id") == "profile-0001"
            and move.get("depth_pass") == 15.0
            and move.get("profile_loop")
        ]
        entry_index = next(
            index for index, move in enumerate(loop_moves) if move.get("tab_entry")
        )
        exit_index = next(
            index for index, move in enumerate(loop_moves) if move.get("tab_exit")
        )
        self.assertLess(entry_index, exit_index)
        self.assertTrue(any(move.get("tab") for move in loop_moves[entry_index:exit_index]))
        self.assertFalse(any(move["type"] == "rapid" for move in loop_moves))
        self.assertTrue(loop_moves[0]["profile_loop_start"])
        self.assertTrue(loop_moves[-1]["profile_loop_end"])

    def test_multiple_tabs_do_not_restart_or_schedule_a_second_loop(self):
        reports = self.assert_one_closed_loop_per_depth(
            legacy_profile_moves(tab_count=4)
        )
        self.assertEqual(reports[-1]["tab_crossing_count"], 4)
        self.assertEqual(reports[-1]["tab_transition_count"], 8)

    def test_ramp_is_not_mistaken_for_a_second_profile_loop(self):
        moves = legacy_profile_moves(tab_count=2, ramp_length=30.0)
        reports = self.assert_one_closed_loop_per_depth(moves)
        self.assertTrue(any(move["type"] == "feed_ramp" for move in moves))
        self.assertTrue(all(report["coverage_ratio"] == 1.0 for report in reports))

    def test_corner_slowdown_does_not_reopen_loop_after_final_closure(self):
        moves = legacy_profile_moves(tab_count=3, corner_slowdown=True)
        reports = self.assert_one_closed_loop_per_depth(moves)
        self.assertTrue(any(move.get("corner_slowdown") for move in moves))
        for report in reports:
            end_index = max(report["movement_indexes"])
            self.assertTrue(moves[end_index].get("profile_loop_end"))
            if end_index + 1 < len(moves):
                self.assertFalse(moves[end_index + 1].get("profile_loop"))

    def test_corner_slowdown_does_not_overshoot_mid_edge_ramp_closure(self):
        # Uma haste vertical de texto vetorial expôs o caso real: a rampa
        # termina no meio da lateral, a última quina fica a menos de 8 mm desse
        # fechamento e a antiga saída lenta o ultrapassava antes de voltar.
        glyph_stem = [
            (103.11594633692012, 28.36375165963181),
            (103.11594633692012, 0.51958860947625),
            (98.54427996750094, 0.51958860947625),
            (98.54427996750094, 28.36375165963181),
        ]
        moves = build_profile_cut_moves(
            glyph_stem,
            final_depth=1.0,
            stepdown=1.0,
            ramp_length=25.0,
            safe_height=8.0,
            tool_diameter=0.2,
            cut_side="on_line",
            material_thickness=15.0,
            smart_entry=True,
            corner_slowdown_enabled=True,
            corner_angle_threshold=45.0,
            corner_feed_percent=40.0,
            corner_slowdown_distance=8.0,
            ramp_type="smooth",
            profile_id="internal-0013",
        )
        report = validate_profile_cut_moves(moves)[0]
        self.assertAlmostEqual(report["coverage_ratio"], 1.0)
        self.assertEqual(report["closed_loop_count"], 1)
        self.assertTrue(any(move.get("corner_slowdown") for move in moves))

    def test_per_piece_job_keeps_each_profile_and_depth_unique(self):
        contours = (
            RECTANGLE,
            [(140.0, 0.0), (260.0, 15.0), (250.0, 40.0), (135.0, 25.0)],
        )
        moves = build_profile_cut_job(
            contours,
            final_depth=15.0,
            stepdown=5.0,
            ramp_length=0.0,
            safe_height=8.0,
            cut_side="on_line",
            smart_entry=False,
            tabs_enabled=True,
            tab_length=10.0,
            tab_thickness=3.0,
            tab_count=2,
            corner_slowdown_enabled=True,
        )
        reports = validate_profile_cut_moves(moves)
        self.assertEqual(len(reports), 6)
        self.assertEqual({report["profile_id"] for report in reports}, {
            "profile-0001", "profile-0002"
        })

    def test_global_depth_modes_emit_each_closed_trail_once_per_depth(self):
        contour = CommonLineContour("A", tuple(RECTANGLE))
        for strategy in ("global_by_depth", "hybrid_stability"):
            with self.subTest(strategy=strategy):
                plan = build_global_cut_plan(
                    (contour,),
                    depths=(5.0, 10.0, 15.0),
                    strategy=strategy,
                    tabs_enabled=True,
                    tab_count=3,
                    tab_width=12.0,
                    tab_thickness=3.0,
                    tool_diameter=4.0,
                )
                reports = self.assert_one_closed_loop_per_depth(
                    build_global_cut_plan_moves(plan, 8.0)
                )
                self.assertEqual(reports[-1]["tab_crossing_count"], 4)

    def test_audit_detects_an_injected_second_loop_at_the_same_depth(self):
        once = build_external_cut_moves(
            RECTANGLE,
            final_depth=5.0,
            stepdown=5.0,
            ramp_length=0.0,
            safe_height=8.0,
            compensate_external=False,
            smart_entry=False,
        )
        duplicated = once + [dict(move) for move in once]
        report = audit_profile_cut_moves(duplicated)[0]
        self.assertEqual(report["loop_count"], 2)
        self.assertEqual(report["restart_count"], 1)
        self.assertEqual(report["duplicate_edge_count"], 4)
        self.assertAlmostEqual(report["coverage_ratio"], 2.0)
        with self.assertRaisesRegex(ValueError, "2 voltas|reiniciado"):
            validate_profile_cut_moves(duplicated)

    def test_preview_and_gcode_preserve_the_exact_source_movement_list(self):
        moves = legacy_profile_moves(tab_count=2, corner_slowdown=True)
        displayed = moves_for_preview({"operation_mode": "cut"}, moves)
        self.assertEqual(displayed, moves)
        self.assertIsNot(displayed, moves)
        self.assertEqual(audit_profile_cut_moves(displayed), audit_profile_cut_moves(moves))

        lines = build_gcode(18000, 500, 1800, 4000, 8.0, 15.0, moves)
        expected_g1 = sum(
            move["type"] in {
                "feed_plunge", "feed_drill", "feed_ramp", "feed_helix", "feed_cut"
            }
            for move in moves
        )
        self.assertEqual(sum(line.startswith("G1 ") for line in lines), expected_g1)

    def test_angled_multi_piece_smoke_has_no_second_loop(self):
        contours = (
            CommonLineContour("A", ((0, 0), (180, 12), (170, 36), (0, 24))),
            CommonLineContour("B", ((220, 0), (246, 8), (238, 180), (212, 170))),
            CommonLineContour("C", ((20, 220), (190, 190), (198, 216), (28, 246))),
        )
        plan = build_global_cut_plan(
            contours,
            depths=(5.0, 10.0, 15.0),
            strategy="hybrid_stability",
            tabs_enabled=True,
            tab_count=3,
            tab_width=10.0,
            tab_thickness=3.0,
            tool_diameter=4.0,
        )
        reports = validate_profile_cut_moves(build_global_cut_plan_moves(plan, 8.0))
        self.assertEqual(len(reports), 9)
        for report in reports:
            self.assertAlmostEqual(report["coverage_ratio"], 1.0)


if __name__ == "__main__":
    unittest.main()
