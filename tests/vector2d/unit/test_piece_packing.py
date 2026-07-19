import math
import unittest

from woodcam_editor.application.piece_organizer import (
    ClassifiedPiece,
    classify_document_pieces,
    organize_pieces,
)
from woodcam_editor.domain import EllipseEntity, PathEntity, Vec2, VectorDocument


def piece(piece_id, width, height, *, min_x=0.0, min_y=0.0, descendants=()):
    return ClassifiedPiece(
        piece_id=piece_id,
        outer_id="%s-outer" % piece_id,
        inner_ids=tuple(descendants),
        descendant_ids=tuple(descendants),
        bounds=(min_x, min_y, min_x + width, min_y + height),
        area=width * height,
    )


def legacy_shelf_bounds(pieces, work_bounds, spacing):
    """The former zero-rotation shelf, retained only as a regression baseline."""

    min_x, min_y, max_x, max_y = work_bounds
    ordered = sorted(
        pieces,
        key=lambda item: (
            -max(item.bounds[2] - item.bounds[0], item.bounds[3] - item.bounds[1]),
            item.piece_id,
        ),
    )
    cursor_x = min_x + spacing
    cursor_y = min_y + spacing
    row_height = 0.0
    placed = []
    for item in ordered:
        width = item.bounds[2] - item.bounds[0]
        height = item.bounds[3] - item.bounds[1]
        if cursor_x + width + spacing > max_x:
            cursor_x = min_x + spacing
            cursor_y += row_height + spacing
            row_height = 0.0
        if cursor_y + height + spacing > max_y:
            continue
        placed.append((cursor_x, cursor_y, cursor_x + width, cursor_y + height))
        cursor_x += width + spacing
        row_height = max(row_height, height)
    if not placed:
        return None
    return (
        min(item[0] for item in placed),
        min(item[1] for item in placed),
        max(item[2] for item in placed),
        max(item[3] for item in placed),
    )


def used_metrics(bounds):
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    return width, height, width * height


def assert_boxes_respect_spacing(test_case, placements, work_bounds, spacing):
    min_x, min_y, max_x, max_y = work_bounds
    for placement in placements:
        bounds = placement.placed_bounds
        test_case.assertGreaterEqual(bounds[0] + 1e-8, min_x + spacing)
        test_case.assertGreaterEqual(bounds[1] + 1e-8, min_y + spacing)
        test_case.assertLessEqual(bounds[2] - 1e-8, max_x - spacing)
        test_case.assertLessEqual(bounds[3] - 1e-8, max_y - spacing)
    for index, first in enumerate(placements):
        for second in placements[index + 1 :]:
            left, right = first.placed_bounds, second.placed_bounds
            separated = (
                left[2] + spacing <= right[0] + 1e-8
                or right[2] + spacing <= left[0] + 1e-8
                or left[3] + spacing <= right[1] + 1e-8
                or right[3] + spacing <= left[1] + 1e-8
            )
            test_case.assertTrue(
                separated,
                "%s overlaps %s" % (first.piece_id, second.piece_id),
            )


class CompactPackingTests(unittest.TestCase):
    def test_smart_search_profiles_report_reproducible_metrics(self):
        pieces = (
            piece("a", 52, 28),
            piece("b", 41, 37),
            piece("c", 33, 61),
            piece("d", 24, 48),
            piece("e", 19, 72),
        )
        bounds = (0.0, 0.0, 130.0, 120.0)
        rotations = {item.piece_id: (0.0, 90.0) for item in pieces}

        fast = organize_pieces(
            pieces, bounds, spacing=3.0, rotations=rotations, search_mode="fast"
        )
        balanced = organize_pieces(
            tuple(reversed(pieces)),
            bounds,
            spacing=3.0,
            rotations=rotations,
            search_mode="balanced",
        )
        repeated = organize_pieces(
            pieces, bounds, spacing=3.0, rotations=rotations, search_mode="balanced"
        )

        self.assertGreater(balanced.evaluated_layouts, fast.evaluated_layouts)
        self.assertIn(balanced.strategy, {"MaxRects", "contorno real", "MaxRects + contorno real", "contorno real + MaxRects"})
        self.assertGreater(balanced.placed_area, 0.0)
        self.assertGreater(balanced.sheet_area, 0.0)
        self.assertGreater(balanced.utilization_percent, 0.0)
        self.assertLessEqual(balanced.utilization_percent, 100.0)
        self.assertEqual(
            [
                (item.piece_id, item.placed_bounds, item.rotation_degrees)
                for item in balanced.placements
            ],
            [
                (item.piece_id, item.placed_bounds, item.rotation_degrees)
                for item in repeated.placements
            ],
        )

    def test_invalid_smart_search_profile_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "perfil de busca"):
            organize_pieces(
                (piece("a", 10, 10),),
                (0.0, 0.0, 100.0, 100.0),
                search_mode="aleatorio",
            )

    def test_bottom_left_fills_shelf_cavity_and_strictly_improves_height_and_area(self):
        pieces = (
            piece("tall", 40, 80),
            piece("short-a", 60, 40),
            piece("short-b", 60, 40),
        )
        work_bounds = (0.0, 0.0, 106.0, 126.0)
        spacing = 2.0
        rotations = {item.piece_id: (0.0,) for item in pieces}

        legacy = legacy_shelf_bounds(pieces, work_bounds, spacing)
        compact = organize_pieces(
            pieces,
            work_bounds,
            spacing=spacing,
            rotations=rotations,
        )
        self.assertFalse(compact.unplaced_piece_ids)
        self.assertEqual(len(compact.placements), 3)
        _old_width, old_height, old_area = used_metrics(legacy)
        _new_width, new_height, new_area = used_metrics(compact.used_bounds)
        self.assertLess(new_height, old_height)
        self.assertLess(new_area, old_area)
        self.assertEqual(new_height, 82.0)
        assert_boxes_respect_spacing(self, compact.placements, work_bounds, spacing)

    def test_rotation_is_chosen_by_compact_score_and_nested_vectors_stay_rigid(self):
        pieces = (
            piece(
                "rotated",
                50,
                80,
                min_x=200,
                min_y=100,
                descendants=("rotated-hole", "rotated-pocket"),
            ),
            piece("ellipse-a", 30, 20),
            piece("ellipse-b", 30, 20),
        )
        work_bounds = (10.0, 20.0, 140.0, 120.0)
        spacing = 5.0
        rotations = {
            "rotated": (0.0, 90.0),
            "ellipse-a": (0.0,),
            "ellipse-b": (0.0,),
        }

        result = organize_pieces(
            pieces,
            work_bounds,
            spacing=spacing,
            rotations=rotations,
        )
        by_id = {placement.piece_id: placement for placement in result.placements}
        self.assertEqual(by_id["rotated"].rotation_degrees, 90.0)
        self.assertAlmostEqual(
            by_id["rotated"].placed_bounds[2] - by_id["rotated"].placed_bounds[0],
            80.0,
        )
        self.assertAlmostEqual(
            by_id["rotated"].placed_bounds[3] - by_id["rotated"].placed_bounds[1],
            50.0,
        )
        self.assertEqual(
            set(by_id["rotated"].entity_ids),
            {"rotated-outer", "rotated-hole", "rotated-pocket"},
        )
        assert_boxes_respect_spacing(self, result.placements, work_bounds, spacing)

    def test_arbitrary_rotation_uses_true_rotated_bounding_box(self):
        source = piece("diagonal", 100, 20, min_x=-30, min_y=40)
        result = organize_pieces(
            (source,),
            (0, 0, 200, 200),
            spacing=0,
            rotations={source.piece_id: (45.0,)},
        )
        placement = result.placements[0]
        expected = abs(100 * math.cos(math.pi / 4)) + abs(20 * math.sin(math.pi / 4))
        self.assertAlmostEqual(
            placement.placed_bounds[2] - placement.placed_bounds[0],
            expected,
            places=8,
        )
        self.assertAlmostEqual(
            placement.placed_bounds[3] - placement.placed_bounds[1],
            expected,
            places=8,
        )

    def test_result_is_deterministic_independent_of_input_and_rotation_order(self):
        pieces = (
            piece("a", 60, 25),
            piece("b", 40, 50),
            piece("c", 30, 35),
            piece("d", 20, 70),
        )
        first = organize_pieces(
            pieces,
            (-20, -10, 130, 120),
            spacing=3,
            rotations={item.piece_id: (90, 0, 180) for item in pieces},
        )
        second = organize_pieces(
            tuple(reversed(pieces)),
            (-20, -10, 130, 120),
            spacing=3,
            rotations={item.piece_id: (180, 0, 90) for item in reversed(pieces)},
        )
        first_records = sorted(
            (
                item.piece_id,
                item.instance,
                item.rotation_degrees,
                item.placed_bounds,
            )
            for item in first.placements
        )
        second_records = sorted(
            (
                item.piece_id,
                item.instance,
                item.rotation_degrees,
                item.placed_bounds,
            )
            for item in second.placements
        )
        self.assertEqual(first_records, second_records)
        self.assertEqual(first.used_bounds, second.used_bounds)


class ClassifiedGeometryPackingTests(unittest.TestCase):
    def test_rectangle_and_nested_ellipse_classify_and_pack_as_two_units(self):
        document = VectorDocument.create_default()
        layer_id = document.active_layer_id
        ellipse_outer = EllipseEntity(
            layer_id,
            Vec2(30, 30),
            20,
            12,
            id="ellipse-outer",
        )
        ellipse_hole = EllipseEntity(
            layer_id,
            Vec2(30, 30),
            5,
            3,
            id="ellipse-hole",
        )
        rectangle = PathEntity.from_points(
            layer_id,
            (Vec2(100, 0), Vec2(130, 0), Vec2(130, 70), Vec2(100, 70)),
            closed=True,
            id="rectangle-outer",
        )
        document.add_entities(
            (ellipse_outer, ellipse_hole, rectangle),
            bump_revision=False,
        )

        classified = classify_document_pieces(document, deflection=0.5)
        self.assertEqual(len(classified.pieces), 2)
        result = organize_pieces(
            classified.pieces,
            (-10, -20, 100, 100),
            spacing=3,
            rotations={item.piece_id: (0, 90) for item in classified.pieces},
        )
        self.assertFalse(result.unplaced_piece_ids)
        self.assertEqual(len(result.placements), 2)
        ellipse_placement = next(
            placement
            for placement in result.placements
            if placement.piece_id.endswith("ellipse-outer")
        )
        self.assertEqual(
            set(ellipse_placement.entity_ids),
            {"ellipse-outer", "ellipse-hole"},
        )
        assert_boxes_respect_spacing(
            self,
            result.placements,
            (-10, -20, 100, 100),
            3,
        )


if __name__ == "__main__":
    unittest.main()
