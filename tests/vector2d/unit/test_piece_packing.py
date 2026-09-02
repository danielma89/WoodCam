import math
import unittest
from dataclasses import replace

import woodcam_editor.application.piece_organizer as organizer
from woodcam_editor.application.piece_organizer import (
    ClassifiedPiece,
    OrganizationResult,
    OrganizationSearchCancelled,
    PiecePlacement,
    classify_document_pieces,
    filter_pieces_for_selection,
    organize_pieces,
    organization_placement_transform,
    suggest_rectangular_remnant_cuts,
    validate_organization_result_geometry,
)
from woodcam_editor.application.common_line import CommonLineContour, plan_common_line_cut
from woodcam_editor.domain import EllipseEntity, PathEntity, Vec2, VectorDocument
from woodcam_editor.geometry.polygon_offset import round_offset_closed_polygon


def piece(piece_id, width, height, *, min_x=0.0, min_y=0.0, descendants=()):
    return ClassifiedPiece(
        piece_id=piece_id,
        outer_id="%s-outer" % piece_id,
        inner_ids=tuple(descendants),
        descendant_ids=tuple(descendants),
        bounds=(min_x, min_y, min_x + width, min_y + height),
        area=width * height,
    )


def picture_like_pieces():
    """Panel/rail topology reconstructed from the reported WoodCAM layout."""

    def shaped(piece_id, points):
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        return ClassifiedPiece(
            piece_id,
            piece_id + "-outer",
            (),
            (),
            (min(xs), min(ys), max(xs), max(ys)),
            1.0,
            tuple(points),
        )

    return (
        shaped("panel", ((90, 0), (690, 0), (690, 1800), (610, 1800),
                         (610, 1880), (0, 1880), (0, 110), (90, 110))),
        shaped("rail-1", ((0, 0), (62, 42), (58, 1660), (0, 1700))),
        shaped("rail-2", ((0, 40), (60, 0), (60, 1650), (3, 1600))),
        shaped("rail-3", ((0, 0), (58, 45), (58, 1510), (2, 1460))),
        shaped("rail-4", ((0, 35), (55, 0), (55, 1450), (0, 1400))),
        shaped("segment-1", ((0, 0), (55, 35), (55, 410), (2, 365))),
        shaped("segment-2", ((0, 30), (55, 0), (55, 390), (0, 350))),
        shaped("segment-3", ((0, 0), (55, 30), (55, 330), (3, 300))),
        shaped("cap-1", ((0, 0), (135, 0), (90, 55), (25, 48))),
        shaped("cap-2", ((0, 0), (120, 0), (80, 50), (20, 44))),
        shaped("wedge", ((0, 0), (80, 40), (0, 80))),
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


class RectangularRemnantCutTests(unittest.TestCase):
    def test_isolates_the_largest_storeable_edge_rectangle(self):
        result = OrganizationResult(
            placements=[
                PiecePlacement("panel", 0, 0, 0, 0, (30, 20, 1820, 2300), ("panel",), 0),
                PiecePlacement("rail", 0, 0, 0, 0, (30, 2300, 900, 2500), ("rail",), 0),
            ],
            sheet_bounds=((0, 0, 1850, 2750),),
        )

        cuts = suggest_rectangular_remnant_cuts(
            result,
            minimum_short_side=100,
            clearance=2,
        )

        self.assertGreaterEqual(len(cuts), 1)
        cut = cuts[0]
        self.assertEqual(cut.start, (0.0, 2502.0))
        self.assertEqual(cut.end, (1850.0, 2502.0))
        self.assertEqual(cut.remnant_bounds, (0.0, 2502.0, 1850.0, 2750.0))
        self.assertAlmostEqual(cut.area, 1850.0 * 248.0)

    def test_omits_a_strip_too_narrow_to_store(self):
        result = OrganizationResult(
            placements=[
                PiecePlacement("panel", 0, 0, 0, 0, (3, 3, 997, 997), ("panel",), 0),
            ],
            sheet_bounds=((0, 0, 1000, 1000),),
        )

        self.assertEqual(
            suggest_rectangular_remnant_cuts(
                result,
                minimum_short_side=100,
                clearance=2,
            ),
            (),
        )

    def test_l_shaped_waste_becomes_two_rectangular_remnants(self):
        result = OrganizationResult(
            placements=[
                PiecePlacement(
                    "panel", 0, 0, 0, 0,
                    (0, 0, 365, 488),
                    ("panel",), 0,
                ),
            ],
            sheet_bounds=((0, 0, 1850, 2750),),
        )

        cuts = suggest_rectangular_remnant_cuts(
            result,
            minimum_short_side=100,
            clearance=2,
        )

        self.assertEqual(len(cuts), 2)
        self.assertEqual(cuts[0].start, (0.0, 490.0))
        self.assertEqual(cuts[0].end, (1850.0, 490.0))
        self.assertEqual(cuts[0].remnant_bounds, (0.0, 490.0, 1850.0, 2750.0))
        self.assertEqual(cuts[1].start, (367.0, 0.0))
        self.assertEqual(cuts[1].end, (367.0, 490.0))
        self.assertEqual(cuts[1].remnant_bounds, (367.0, 0.0, 1850.0, 490.0))

    def test_finds_large_internal_rectangle_above_a_right_hand_piece(self):
        result = OrganizationResult(
            placements=[
                PiecePlacement("lower-left", 0, 0, 0, 0, (0, 0, 500, 500), ("a",), 0),
                PiecePlacement("lower-right", 0, 0, 0, 0, (500, 0, 1000, 300), ("b",), 0),
                PiecePlacement("upper-left", 0, 0, 0, 0, (0, 500, 500, 700), ("c",), 0),
            ],
            sheet_bounds=((0, 0, 1000, 1000),),
        )

        cuts = suggest_rectangular_remnant_cuts(
            result,
            minimum_short_side=100,
            clearance=0,
        )

        regions = {}
        for cut in cuts:
            regions.setdefault(cut.remnant_id, []).append(cut)
        matching = [
            values
            for values in regions.values()
            if values[0].remnant_bounds == (500.0, 300.0, 1000.0, 700.0)
        ]
        self.assertEqual(len(matching), 1)
        self.assertEqual(len(matching[0]), 2)
        self.assertEqual(sum(cut.show_label for cut in matching[0]), 1)
        self.assertEqual(
            {(cut.start, cut.end) for cut in matching[0]},
            {
                ((500.0, 300.0), (1000.0, 300.0)),
                ((500.0, 300.0), (500.0, 700.0)),
            },
        )


class CompactPackingTests(unittest.TestCase):
    def test_single_piece_is_always_anchored_at_sheet_zero(self):
        single = piece("single", 320, 180)

        result = organize_pieces(
            (single,),
            (0, 0, 1850, 2750),
            spacing=4,
            rotations={single.piece_id: (0.0, 90.0, 180.0, 270.0)},
            search_mode="balanced",
        )

        self.assertEqual(len(result.placements), 1)
        self.assertAlmostEqual(result.placements[0].placed_bounds[0], 0.0)
        self.assertAlmostEqual(result.placements[0].placed_bounds[1], 0.0)

    def test_frontier_points_recover_corners_hidden_by_maxrect_splits(self):
        dimensions = (
            (20, 40),
            (60, 25),
            (35, 20),
            (55, 55),
            (15, 20),
            (30, 60),
        )
        pieces = tuple(
            piece("frontier-%d" % index, width, height)
            for index, (width, height) in enumerate(dimensions)
        )
        instances = tuple(
            organizer._PackInstance(
                value,
                1,
                value.bounds[2] - value.bounds[0],
                value.bounds[3] - value.bounds[1],
            )
            for value in pieces
        )
        rotations = {value.piece_id: (0.0, 90.0) for value in pieces}
        maxrect = organizer._pack_bottom_left(
            instances,
            (0.0, 0.0, 100.0, 150.0),
            2.0,
            rotations,
        )
        frontier = organizer._pack_corner_points(
            instances,
            (0.0, 0.0, 100.0, 150.0),
            2.0,
            rotations,
        )

        self.assertFalse(maxrect[1])
        self.assertFalse(frontier[1])
        self.assertLess(
            frontier[2][3] - frontier[2][1],
            maxrect[2][3] - maxrect[2][1],
        )
        self.assertAlmostEqual(frontier[2][3] - frontier[2][1], 104.0)

    def test_selection_scope_keeps_each_piece_as_one_rigid_unit(self):
        first = ClassifiedPiece(
            "first",
            "first-outer",
            ("first-hole",),
            ("first-hole",),
            (0.0, 0.0, 100.0, 50.0),
            5000.0,
            feature_ids=("first-pocket",),
            marking_ids=("first-mark",),
        )
        second = ClassifiedPiece(
            "second",
            "second-outer",
            (),
            (),
            (120.0, 0.0, 180.0, 40.0),
            2400.0,
        )

        for selected_id in (
            "first-outer",
            "first-hole",
            "first-pocket",
            "first-mark",
        ):
            self.assertEqual(
                filter_pieces_for_selection((first, second), (selected_id,)),
                (first,),
            )
        self.assertEqual(
            filter_pieces_for_selection((first, second), ()),
            (first, second),
        )

    def test_pocket_region_stays_attached_without_becoming_an_inner_cut(self):
        document = VectorDocument.create_default()
        layer_id = document.active_layer_id
        outer = replace(
            PathEntity.from_points(
                layer_id,
                (Vec2(0, 0), Vec2(100, 0), Vec2(100, 50), Vec2(0, 50)),
                closed=True,
                id="outer",
            ),
            metadata={"source_shape_component_id": "component-001"},
        )
        pocket = replace(
            PathEntity.from_points(
                layer_id,
                (Vec2(0, 10), Vec2(40, 10), Vec2(40, 30), Vec2(0, 30)),
                closed=True,
                id="pocket",
            ),
            metadata={
                "source_shape_component_id": "component-001",
                "import_role": "pocket_region",
                "pocket_depth_mm": 5.0,
            },
        )
        document.add_entities((outer, pocket), bump_revision=False)

        classification = classify_document_pieces(document)

        self.assertEqual(len(classification.pieces), 1)
        classified = classification.pieces[0]
        self.assertEqual(classified.outer_id, outer.id)
        self.assertEqual(classified.inner_ids, ())
        self.assertEqual(classified.descendant_ids, ())
        self.assertEqual(classified.feature_ids, (pocket.id,))

        organized = organize_pieces(
            classification.pieces,
            (0, 0, 200, 100),
            spacing=4.0,
            rotations={classified.piece_id: (0.0,)},
        )
        self.assertEqual(len(organized.placements), 1)
        self.assertEqual(
            organized.placements[0].entity_ids,
            (outer.id, pocket.id),
        )

    def test_picture_like_panel_and_tapered_rails_organize_then_compensate(self):
        pieces = picture_like_pieces()
        result = organize_pieces(
            pieces,
            (0, 0, 1000, 2050),
            spacing=4.0,
            rotations={piece.piece_id: (0.0,) for piece in pieces},
            search_mode="fast",
            search_budget=(4, 2, 1, 1),
            toolpath_offset=2.0,
        )

        self.assertEqual(len(result.placements), len(pieces))
        self.assertFalse(result.unplaced_piece_ids)
        by_id = {piece.piece_id: piece for piece in pieces}
        compensated = []
        for placement in result.placements:
            source = by_id[placement.piece_id]
            placed = tuple(
                (x_value + placement.dx, y_value + placement.dy)
                for x_value, y_value in source.outer_points
            )
            compensated.append(
                CommonLineContour(
                    placement.piece_id,
                    round_offset_closed_polygon(placed, 2.0),
                )
            )
        plan = plan_common_line_cut(compensated, tolerance=0.02)

        self.assertTrue(plan.is_valid)
        self.assertGreaterEqual(len(plan.shared_segments), 1)

    def test_exact_vector_gate_rejects_crossing_placements_and_accepts_t_junction(self):
        pieces = (
            ClassifiedPiece(
                "a", "a-outer", (), (), (0.0, 0.0, 20.0, 20.0), 400.0,
                ((0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (0.0, 20.0)),
            ),
            ClassifiedPiece(
                "b", "b-outer", (), (), (0.0, 0.0, 20.0, 20.0), 400.0,
                ((0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (0.0, 20.0)),
            ),
        )
        crossing = OrganizationResult(
            placements=[
                PiecePlacement("a", 1, 0.0, 0.0, 0.0, pieces[0].bounds, ("a-outer",)),
                PiecePlacement("b", 1, 5.0, 5.0, 0.0, (5.0, 5.0, 25.0, 25.0), ("b-outer",)),
            ]
        )
        self.assertTrue(validate_organization_result_geometry(pieces, crossing))

        narrow_gap = OrganizationResult(
            placements=[
                PiecePlacement("a", 1, 0.0, 0.0, 0.0, pieces[0].bounds, ("a-outer",)),
                PiecePlacement("b", 1, 25.5, 0.0, 0.0, (25.5, 0.0, 45.5, 20.0), ("b-outer",)),
            ]
        )
        clearance_issues = validate_organization_result_geometry(
            pieces,
            narrow_gap,
            minimum_clearance=6.0,
        )
        self.assertEqual(clearance_issues[0].code.value, "insufficient_clearance")
        self.assertIn("5.500 mm", clearance_issues[0].message)

        exact_gap = OrganizationResult(
            placements=[
                PiecePlacement("a", 1, 0.0, 0.0, 0.0, pieces[0].bounds, ("a-outer",)),
                PiecePlacement("b", 1, 26.0, 0.0, 0.0, (26.0, 0.0, 46.0, 20.0), ("b-outer",)),
            ]
        )
        self.assertFalse(
            validate_organization_result_geometry(
                pieces,
                exact_gap,
                minimum_clearance=6.0,
                toolpath_offset=3.0,
            )
        )

        # Regression from the reported Ø4 mm layout: two orthogonal parts can
        # have 4 mm of diagonal corner clearance, while their mitered
        # common-line centre paths still cross. The organizer must validate
        # the exact same join geometry used later by CAM.
        diagonal = 4.0 / math.sqrt(2.0)
        diagonal_piece = ClassifiedPiece(
            "diagonal",
            "diagonal-o",
            (),
            (),
            (20.0 + diagonal, 20.0 + diagonal, 40.0 + diagonal, 40.0 + diagonal),
            400.0,
            (
                (20.0 + diagonal, 20.0 + diagonal),
                (40.0 + diagonal, 20.0 + diagonal),
                (40.0 + diagonal, 40.0 + diagonal),
                (20.0 + diagonal, 40.0 + diagonal),
            ),
        )
        diagonal_result = OrganizationResult(
            placements=[
                PiecePlacement("a", 1, 0.0, 0.0, 0.0, pieces[0].bounds, ("a-outer",)),
                PiecePlacement(
                    "diagonal",
                    1,
                    0.0,
                    0.0,
                    0.0,
                    diagonal_piece.bounds,
                    ("diagonal-o",),
                ),
            ]
        )
        diagonal_issues = validate_organization_result_geometry(
            (pieces[0], diagonal_piece),
            diagonal_result,
            minimum_clearance=4.0,
            toolpath_offset=2.0,
        )
        self.assertTrue(diagonal_issues)
        self.assertEqual(diagonal_issues[0].code.value, "crossing")

        pointed_pieces = (
            ClassifiedPiece(
                "point-left", "point-left-o", (), (), (0, 0, 20, 200), 2000,
                ((0, 0), (20, 100), (0, 200)),
            ),
            ClassifiedPiece(
                "point-right", "point-right-o", (), (), (24, 0, 44, 200), 2000,
                ((44, 0), (24, 100), (44, 200)),
            ),
        )
        pointed_result = OrganizationResult(
            placements=[
                PiecePlacement(
                    value.piece_id, 1, 0, 0, 0, value.bounds, (value.outer_id,)
                )
                for value in pointed_pieces
            ]
        )
        self.assertFalse(
            validate_organization_result_geometry(
                pointed_pieces,
                pointed_result,
                minimum_clearance=4.0,
                toolpath_offset=2.0,
            )
        )

        t_pieces = (
            ClassifiedPiece("left", "left-o", (), (), (0, 0, 10, 20), 200,
                            ((0, 0), (10, 0), (10, 20), (0, 20))),
            ClassifiedPiece("bottom", "bottom-o", (), (), (10, 0, 20, 10), 100,
                            ((10, 0), (20, 0), (20, 10), (10, 10))),
            ClassifiedPiece("top", "top-o", (), (), (10, 10, 20, 20), 100,
                            ((10, 10), (20, 10), (20, 20), (10, 20))),
        )
        valid = OrganizationResult(
            placements=[
                PiecePlacement(value.piece_id, 1, 0, 0, 0, value.bounds, (value.outer_id,))
                for value in t_pieces
            ]
        )
        self.assertFalse(validate_organization_result_geometry(t_pieces, valid))

    def test_search_budget_override_and_cooperative_cancellation(self):
        pieces = (
            piece("a", 52, 28),
            piece("b", 41, 37),
            piece("c", 33, 61),
        )
        result = organize_pieces(
            pieces,
            (0.0, 0.0, 130.0, 120.0),
            spacing=3.0,
            search_mode="fast",
            search_budget=(6, 3, 2, 2),
        )
        self.assertTrue(result.placements)
        with self.assertRaises(OrganizationSearchCancelled):
            organize_pieces(
                pieces,
                (0.0, 0.0, 130.0, 120.0),
                spacing=3.0,
                search_mode="thorough",
                stop_requested=lambda: True,
            )

        with self.assertRaisesRegex(ValueError, "orçamento"):
            organize_pieces(
                pieces,
                (0.0, 0.0, 130.0, 120.0),
                search_budget=(1, 2, 3),
            )

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
        self.assertIn(
            balanced.strategy,
            {
                "MaxRects",
                "Pontos de fronteira",
                "contorno real",
                "MaxRects + contorno real",
                "contorno real + MaxRects",
            },
        )
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

    def test_preview_transform_replays_the_validated_real_contour_bounds(self):
        points = ((0.0, 0.0), (10.0, 0.0), (110.0, 100.0), (100.0, 100.0))
        source = ClassifiedPiece(
            "diagonal",
            "diagonal-outer",
            (),
            (),
            (0.0, 0.0, 110.0, 100.0),
            1000.0,
            points,
        )
        result = organize_pieces(
            (source,),
            (0.0, 0.0, 250.0, 100.0),
            spacing=3.0,
            rotations={source.piece_id: (135.0,)},
        )
        placement = result.placements[0]
        transform = organization_placement_transform(source, placement)
        transformed = tuple(
            transform.apply_to_point(Vec2(*point)) for point in points
        )
        actual_bounds = (
            min(point.x for point in transformed),
            min(point.y for point in transformed),
            max(point.x for point in transformed),
            max(point.y for point in transformed),
        )
        for actual, expected_value in zip(actual_bounds, placement.placed_bounds):
            self.assertAlmostEqual(actual, expected_value, places=8)

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
