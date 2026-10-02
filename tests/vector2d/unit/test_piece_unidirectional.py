"""Same-direction common-line passes, including real CAM travel moves."""
import unittest
from collections import defaultdict

from operations import build_global_cut_plan_moves
from woodcam_editor.application.common_line import CommonLineContour
from woodcam_editor.application.global_cut_plan import OwnedContour, build_global_cut_plan


def rectangle(owner, x0, y0, x1, y1):
    return CommonLineContour(owner, ((x0, y0), (x1, y0), (x1, y1), (x0, y1)))


class PieceUnidirectionalTests(unittest.TestCase):
    def plan(self, **options):
        settings = dict(depths=(5, 10, 15.5), through_depth=15,
                        strategy="piece_unidirectional", start_xy=(0, 0))
        settings.update(options)
        return build_global_cut_plan(
            (rectangle("A", 0, 0, 80, 40), rectangle("B", 80, 0, 160, 40)),
            **settings,
        )

    def test_first_piece_closed_then_three_sides_in_the_same_direction(self):
        for depths in ((5, 10), (5, 10, 15.5), (4, 8, 12, 15.5)):
            with self.subTest(depths=depths):
                plan = self.plan(depths=depths)
                self.assertEqual(
                    [(op.executing_owner_id, op.depth) for op in plan.operations],
                    [(owner, depth) for owner in ("A", "B") for depth in depths],
                )
                trails = {trail.trail_id: trail for trail in plan.trails}
                signatures = defaultdict(set)
                for op in plan.operations:
                    trail = trails[op.trail_id].oriented(
                        reverse=op.reverse_trail, start_edge_index=op.start_edge_index)
                    self.assertEqual(trail.closed, op.executing_owner_id == "A")
                    signatures[op.trail_id].add(trail.points)
                    length = sum(a.distance_to(b) for a, b in zip(trail.points, trail.points[1:]))
                    self.assertAlmostEqual(length, 240 if trail.closed else 200)
                self.assertTrue(all(len(values) == 1 for values in signatures.values()))
                self.assertTrue(all(op.routing_mode == "piece_unidirectional" for op in plan.operations))
                plan.validate()  # Full physical coverage once per depth, dependencies intact.

    def test_each_open_pass_retracts_and_returns_at_safe_z(self):
        plan = self.plan()
        for ramp_type in ("smooth", "zigzag", "spiral"):
            for ramp_length in (0, 20):
                with self.subTest(ramp_type=ramp_type, ramp_length=ramp_length):
                    moves = build_global_cut_plan_moves(
                        plan, 8, ramp_length=ramp_length, ramp_type=ramp_type)
                    self.assertFalse(any(m.get("cleared_path_link") for m in moves
                                         if m.get("routing_mode") == "piece_unidirectional"
                                         and m.get("cut_operation_id") in {
                                             op.operation_id for op in plan.operations
                                             if op.executing_owner_id == "B"}))
                    for op in plan.operations:
                        if op.executing_owner_id != "B":
                            continue
                        part = [m for m in moves if m.get("cut_operation_id") == op.operation_id]
                        self.assertEqual(part[0]["type"], "rapid")
                        self.assertEqual(part[0]["z"], 8)
                        self.assertIsNotNone(part[0]["x"])
                        self.assertEqual(part[-1]["type"], "rapid")
                        self.assertEqual(part[-1]["z"], 8)
                        self.assertIsNone(part[-1]["x"])
                    z = 8
                    for move in moves:
                        if move["type"] == "rapid" and move.get("x") is not None:
                            self.assertEqual(z, 8)
                            self.assertEqual(move["z"], 8)
                        if move.get("z") is not None:
                            z = move["z"]

    def test_existing_bidirectional_still_alternates_without_return(self):
        plan = self.plan(strategy="piece_bidirectional")
        b_ops = [op for op in plan.operations if op.executing_owner_id == "B"]
        self.assertEqual([op.reverse_trail for op in b_ops], [False, True, False])
        moves = build_global_cut_plan_moves(plan, 8)
        entries = [m for m in moves if m["type"] == "rapid" and m.get("x") is not None
                   and m.get("cut_operation_id") in {op.operation_id for op in b_ops}]
        self.assertEqual(len(entries), 1)

    def test_tabs_and_release_keep_physical_coverage(self):
        contours = (rectangle("A", 0, 0, 80, 40), rectangle("B", 80, 0, 160, 40),
                    rectangle("C", 0, 40, 160, 80))
        for release in ("keep_tabs", "automatic_release"):
            plan = build_global_cut_plan(
                contours, depths=(5, 10, 15.5), strategy="piece_unidirectional",
                through_depth=15, tabs_enabled=True, tab_count=4, tab_width=8, tab_thickness=6,
                tool_diameter=4, release_mode=release)
            plan.validate()
            self.assertTrue(plan.tabs)
            self.assertEqual(bool(plan.tab_release_operations), release == "automatic_release")
            moves = build_global_cut_plan_moves(plan, 8, material_thickness=15, tool_diameter=4)
            self.assertTrue(any(m.get("tab") for m in moves))

    def test_disconnected_remainders_never_receive_a_cutting_connector(self):
        a = ((0, 0), (100, 0), (100, 80), (90, 80), (90, 100), (100, 100),
             (100, 180), (90, 180), (90, 200), (100, 200), (100, 300), (0, 300))
        b = ((100, 0), (200, 0), (200, 300), (100, 300), (100, 200), (110, 200),
             (110, 180), (100, 180), (100, 100), (110, 100), (110, 80), (100, 80))
        plan = build_global_cut_plan(
            (CommonLineContour("A", a), CommonLineContour("B", b)),
            depths=(5, 10, 15.5), strategy="piece_unidirectional")
        b_ops = [op for op in plan.operations if op.executing_owner_id == "B"]
        self.assertEqual(len({op.trail_id for op in b_ops}), 3)
        moves = build_global_cut_plan_moves(plan, 8)
        self.assertFalse(any(m.get("cleared_path_link") for m in moves))
        signatures = defaultdict(set)
        for op in b_ops:
            group = [m for m in moves if m.get("cut_operation_id") == op.operation_id]
            self.assertEqual(group[0]["type"], "rapid")
            self.assertEqual(group[-1]["type"], "rapid")
            self.assertIsNone(group[-1]["x"])
            signatures[op.trail_id].add(tuple(
                (m["x"], m["y"]) for m in group if m["type"] == "feed_cut"))
        self.assertTrue(all(len(values) == 1 for values in signatures.values()))

    def test_internal_contour_finishes_before_its_outer_piece(self):
        plan = self.plan(internal_contours=(OwnedContour(
            "hole-A", "A", ((20, 10), (30, 10), (30, 20), (20, 20))),))
        self.assertEqual([op.phase.value for op in plan.operations[:3]], ["internal"] * 3)
        self.assertEqual([op.depth for op in plan.operations[:3]], [5, 10, 15.5])
        plan.validate()


if __name__ == "__main__":
    unittest.main()
