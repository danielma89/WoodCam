import math
import unittest
from unittest import mock

import woodcam_editor.application.piece_organizer as organizer
from woodcam_editor.application.piece_organizer import (
    classify_document_pieces,
    organize_pieces,
)
from woodcam_editor.domain import EllipseEntity, Vec2, VectorDocument


class _Path:
    def __init__(self, entity_id, points, closed=True):
        self.id = entity_id
        self.points = tuple(points)
        self.closed = closed


class _Document:
    def __init__(self, entities):
        self.entities_by_id = {entity.id: entity for entity in entities}


def shifted(points, dx, dy=0.0):
    return tuple((x + dx, y + dy) for x, y in points)


def rectangular_baseline(pieces, work_bounds, spacing, rotations):
    return organizer._organize_pieces_rectangular(
        pieces,
        work_bounds,
        spacing,
        {},
        rotations,
    )


def boxes_overlap(first, second):
    return not (
        first[2] <= second[0]
        or second[2] <= first[0]
        or first[3] <= second[1]
        or second[3] <= first[1]
    )


def assert_raster_replay_has_no_collision(
    test_case,
    pieces,
    result,
    work_bounds,
    spacing,
):
    """Replay returned placements against fresh bitmasks and spacing halos."""

    resolution = organizer._raster_resolution(pieces, work_bounds, spacing)
    sheet_cols = int(math.floor((work_bounds[2] - work_bounds[0]) / resolution))
    sheet_rows = int(math.floor((work_bounds[3] - work_bounds[1]) / resolution))
    sheet_bits = [0] * sheet_rows
    spacing_pixels = int(math.ceil(spacing / resolution - 1e-12))
    by_id = {piece.piece_id: piece for piece in pieces}
    for placement in result.placements:
        piece = by_id[placement.piece_id]
        orientation = organizer._raster_orientations(
            piece,
            (placement.rotation_degrees,),
            resolution,
        )[0]
        col = int(round((placement.placed_bounds[0] - work_bounds[0]) / resolution))
        row = int(round((placement.placed_bounds[1] - work_bounds[1]) / resolution))
        test_case.assertTrue(
            organizer._raster_mask_fits(
                sheet_bits,
                orientation,
                row,
                col,
            )
        )
        organizer._stamp_raster_mask(
            sheet_bits,
            sheet_rows,
            sheet_cols,
            orientation,
            row,
            col,
            spacing_pixels,
        )
        test_case.assertGreaterEqual(
            placement.placed_bounds[0] + 1e-9,
            work_bounds[0] + spacing,
        )
        test_case.assertGreaterEqual(
            placement.placed_bounds[1] + 1e-9,
            work_bounds[1] + spacing,
        )
        test_case.assertLessEqual(
            placement.placed_bounds[2] - 1e-9,
            work_bounds[2] - spacing,
        )
        test_case.assertLessEqual(
            placement.placed_bounds[3] - 1e-9,
            work_bounds[3] - spacing,
        )


class RasterContourPackingTests(unittest.TestCase):
    def test_reusable_side_strip_outranks_slightly_lower_diagonal_envelope(self):
        sheet = ((0.0, 0.0, 1000.0, 1000.0),)
        def candidate(angle, bounds):
            return organizer.OrganizationResult(
                placements=[organizer.PiecePlacement(
                    "rail", 1, 0.0, 0.0, angle, bounds, ("rail",),
                )], sheet_bounds=sheet, used_bounds=bounds,
            )
        diagonal = candidate(45.0, (4.0, 4.0, 647.5, 647.5))
        aligned = candidate(0.0, (4.0, 4.0, 14.0, 904.0))
        self.assertLess(organizer.organization_result_score(diagonal),
                        organizer.organization_result_score(aligned))
        self.assertLess(organizer.organization_remnant_score(aligned),
                        organizer.organization_remnant_score(diagonal))

    def test_aligned_search_keeps_explicit_rotation_constraints(self):
        options = {"free": (0.0, 30.0, 90.0, 150.0),
                   "locked": (30.0,), "grain": (0.0, 180.0)}
        aligned = organizer.orthogonal_nesting_rotations(options)
        self.assertEqual(aligned["free"], (0.0, 90.0))
        self.assertEqual(aligned["locked"], (30.0,))
        self.assertEqual(aligned["grain"], (0.0, 180.0))

    def test_clearance_pruning_matches_all_segment_pairs(self):
        import random
        randomizer = random.Random(917)
        for _ in range(30):
            contours = []
            for offset in (0, 25):
                points = tuple(Vec2(offset + randomizer.uniform(-10, 10),
                                    randomizer.uniform(-10, 10)) for _ in range(8))
                contours.append(organizer.CommonLineContour(str(offset), points))
            first, second = contours
            distances = []
            for a, b in zip(first.points, first.points[1:]+first.points[:1]):
                for c, d in zip(second.points, second.points[1:]+second.points[:1]):
                    distances.extend(organizer._point_segment_clearance(*args)[0]
                                     for args in ((a,c,d), (b,c,d), (c,a,b), (d,a,b)))
            distance, a, b = organizer._contour_clearance(first, second)
            self.assertAlmostEqual(distance, min(distances), places=10)
            self.assertAlmostEqual(a.distance_to(b), distance, places=10)

    def test_many_large_rotated_masks_stay_within_search_budget(self):
        pieces = classify_document_pieces(_Document(tuple(
            _Path(str(i), shifted(((0, 0), (1400, 0), (0, 2000)), i*2500))
            for i in range(6)
        ))).pieces
        bounds = (0, 0, 1780, 2725)
        rotations = {p.piece_id: (0, 90, 180, 270) for p in pieces}
        resolution = organizer._bounded_raster_resolution(pieces, bounds, 4, rotations)
        cells = sum(math.ceil(o.width/resolution)*math.ceil(o.height/resolution)
                    for p in pieces for o in organizer._rotation_options(p, rotations[p.piece_id]))
        self.assertLessEqual(cells, 12000000)
        self.assertLessEqual(int(1780/resolution)*int(2725/resolution), 1500000)

    def test_spacing_halo_extends_to_the_left_of_a_translated_piece(self):
        mask = organizer._RasterOrientation(0, (15, 15), 2, 4, 8, 4, 2, 0, 0, 1)
        sheet = [0] * 12
        organizer._stamp_raster_mask(sheet, 12, 24, mask, 4, 8, 2)
        # A left-hand neighbour with only one empty pixel between contours
        # must collide with the two-pixel clearance halo, just like a right one.
        self.assertFalse(organizer._raster_mask_fits(sheet, mask, 4, 3))
        self.assertFalse(organizer._raster_mask_fits(sheet, mask, 4, 13))
        self.assertTrue(organizer._raster_mask_fits(sheet, mask, 4, 2))
        self.assertTrue(organizer._raster_mask_fits(sheet, mask, 4, 14))

    def test_frontier_intervals_match_exhaustive_collision_checks(self):
        import random
        randomizer = random.Random(1947)
        for _ in range(100):
            sheet = tuple(randomizer.getrandbits(20) for _ in range(8))
            rows = tuple(randomizer.getrandbits(5) for _ in range(3))
            mask = organizer._RasterOrientation(0, rows, 3, 5, 0, 5, 3, 0, 0, 1)
            for row in range(6):
                feasible = {col for col in range(2, 15)
                            if organizer._raster_mask_fits(sheet, mask, row, col)}
                expected = {col for col in feasible
                            if col-1 not in feasible or col+1 not in feasible}
                actual = organizer._raster_candidate_cols(sheet, mask, row, 2, 14)
                self.assertEqual(set(actual), expected)

    def test_large_sheet_keeps_contour_search_with_small_clearance(self):
        triangle = ((0, 0), (600, 0), (0, 600))
        pieces = classify_document_pieces(_Document((
            _Path("large-a", triangle),
            _Path("large-b", shifted(triangle, 1000)),
        ))).pieces
        bounds = (0.0, 0.0, 1780.0, 2725.0)
        rotations = {piece.piece_id: (0, 90, 180, 270) for piece in pieces}
        result = organizer._organize_pieces_raster(
            pieces, bounds, 3.175, {}, rotations, max_orders=1,
        )
        self.assertIsNotNone(result)
        self.assertEqual(len(result.placements), 2)
        self.assertFalse(organizer.validate_organization_result_geometry(
            pieces, result, minimum_clearance=3.175,
        ))
        self.assertTrue(boxes_overlap(*(v.placed_bounds for v in result.placements)))

    def test_one_order_budget_does_not_run_four_full_searches(self):
        pieces = classify_document_pieces(_Document(tuple(
            _Path(str(i), ((i*100, 0), (i*100+w, 0), (i*100+w, h), (i*100, h)))
            for i, (w, h) in enumerate(((70, 10), (20, 60), (35, 40), (50, 30)))
        ))).pieces
        instances = tuple(organizer._PackInstance(
            p, 1, p.bounds[2]-p.bounds[0], p.bounds[3]-p.bounds[1],
        ) for p in pieces)
        self.assertEqual(len(organizer._candidate_orders(instances, max_orders=1)), 1)

    def test_final_gate_moves_conflicting_piece_to_next_sheet_instead_of_aborting(self):
        square = ((0, 0), (20, 0), (20, 20), (0, 20))
        classified = classify_document_pieces(
            _Document(
                (
                    _Path("gate-a", square),
                    _Path("gate-b", shifted(square, 100.0)),
                )
            )
        )
        first, second = classified.pieces
        unsafe = organizer.OrganizationResult(
            placements=[
                organizer.PiecePlacement(
                    first.piece_id,
                    1,
                    0.0,
                    0.0,
                    0.0,
                    (0.0, 0.0, 20.0, 20.0),
                    (first.outer_id,),
                ),
                organizer.PiecePlacement(
                    second.piece_id,
                    1,
                    -79.992,
                    0.0,
                    0.0,
                    (20.008, 0.0, 40.008, 20.0),
                    (second.outer_id,),
                ),
            ],
            used_bounds=(0.0, 0.0, 40.008, 20.0),
            strategy="forced unsafe candidate",
            evaluated_layouts=1,
        )
        self.assertTrue(
            organizer.validate_organization_result_geometry(
                classified.pieces,
                unsafe,
                minimum_clearance=4.0,
            )
        )

        for raster_candidate in (unsafe, None):
            with self.subTest(raster_available=raster_candidate is not None), \
                    mock.patch.object(
                        organizer,
                        "_organize_pieces_rectangular",
                        return_value=unsafe,
                    ), mock.patch.object(
                        organizer,
                        "_organize_pieces_raster",
                        return_value=raster_candidate,
                    ):
                repaired = organizer._organize_single_sheet(
                    classified.pieces,
                    (0.0, 0.0, 100.0, 100.0),
                    spacing=4.0,
                    rotations={piece.piece_id: (0.0,) for piece in classified.pieces},
                    search_mode="fast",
                )

            self.assertEqual(len(repaired.placements), 1)
            self.assertEqual(len(repaired.unplaced_piece_ids), 1)
            self.assertFalse(
                organizer.validate_organization_result_geometry(
                    classified.pieces,
                    repaired,
                    minimum_clearance=4.0,
                )
            )

    def test_final_gate_repacks_all_parts_on_same_sheet_before_spilling(self):
        square = ((0, 0), (20, 0), (20, 20), (0, 20))
        classified = classify_document_pieces(
            _Document(
                (
                    _Path("repack-a", square),
                    _Path("repack-b", shifted(square, 100.0)),
                )
            )
        )
        first, second = classified.pieces

        def result(second_min_x):
            return organizer.OrganizationResult(
                placements=[
                    organizer.PiecePlacement(
                        first.piece_id, 1, 0.0, 0.0, 0.0,
                        (0.0, 0.0, 20.0, 20.0), (first.outer_id,),
                    ),
                    organizer.PiecePlacement(
                        second.piece_id, 1, second_min_x - 100.0, 0.0, 0.0,
                        (second_min_x, 0.0, second_min_x + 20.0, 20.0),
                        (second.outer_id,),
                    ),
                ],
                used_bounds=(0.0, 0.0, second_min_x + 20.0, 20.0),
                strategy="test candidate",
                evaluated_layouts=1,
            )

        unsafe = result(20.008)
        safe = result(25.0)

        def rectangular_side_effect(_pieces, _bounds, spacing, *_args):
            return unsafe if spacing <= 4.0 else safe

        with mock.patch.object(
            organizer,
            "_organize_pieces_rectangular",
            side_effect=rectangular_side_effect,
        ), mock.patch.object(
            organizer,
            "_organize_pieces_raster",
            return_value=unsafe,
        ):
            repaired = organizer._organize_single_sheet(
                classified.pieces,
                (0.0, 0.0, 100.0, 100.0),
                spacing=4.0,
                rotations={piece.piece_id: (0.0,) for piece in classified.pieces},
                search_mode="fast",
            )

        self.assertEqual(len(repaired.placements), 2)
        self.assertFalse(repaired.unplaced_piece_ids)
        self.assertIn("folga vetorial segura", repaired.strategy)
        self.assertFalse(
            organizer.validate_organization_result_geometry(
                classified.pieces,
                repaired,
                minimum_clearance=4.0,
            )
        )

    def test_progressive_score_prefers_one_sheet_over_a_shorter_two_sheet_layout(self):
        """A second sheet must never win merely because its envelope is lower."""

        one_sheet = organizer.OrganizationResult(
            placements=[
                organizer.PiecePlacement(
                    "piece-a", 1, 0.0, 0.0, 0.0,
                    (5.0, 5.0, 317.0, 161.0), ("a",), 0,
                ),
                organizer.PiecePlacement(
                    "piece-b", 1, 0.0, 0.0, 0.0,
                    (5.0, 166.0, 317.0, 322.0), ("b",), 0,
                ),
            ],
            used_bounds=(5.0, 5.0, 317.0, 322.0),
            sheet_bounds=((0.0, 0.0, 322.0, 400.0),),
        )
        two_sheets = organizer.OrganizationResult(
            placements=[
                organizer.PiecePlacement(
                    "piece-a", 1, 0.0, 0.0, 90.0,
                    (5.0, 5.0, 161.0, 317.0), ("a",), 0,
                ),
                organizer.PiecePlacement(
                    "piece-b", 1, 0.0, 0.0, 90.0,
                    (375.0, 5.0, 531.0, 317.0), ("b",), 1,
                ),
            ],
            used_bounds=(5.0, 5.0, 531.0, 317.0),
            sheet_bounds=(
                (0.0, 0.0, 320.0, 400.0),
                (370.0, 0.0, 690.0, 400.0),
            ),
        )

        self.assertLess(
            organizer.organization_result_score(one_sheet),
            organizer.organization_result_score(two_sheets),
        )

    def test_symmetric_notched_pair_consolidates_scrap_at_sheet_boundary(self):
        """Equivalent envelopes should not leave fragmented scrap in the middle."""

        notched = (
            (0, 0), (236, 0), (204, 31), (230, 58), (176, 112),
            (150, 85), (118, 117), (86, 86), (59, 113), (5, 59),
            (32, 32),
        )
        opposite = tuple((236 - x_value, 117 - y_value) for x_value, y_value in notched)
        classified = classify_document_pieces(
            _Document(
                (
                    _Path("notched-a", notched),
                    _Path("notched-b", shifted(opposite, 400.0)),
                )
            )
        )
        rotations = {
            piece.piece_id: (0.0, 90.0)
            for piece in classified.pieces
        }

        result = organize_pieces(
            classified.pieces,
            (0.0, 0.0, 256.0, 266.0),
            spacing=10.0,
            rotations=rotations,
            search_mode="fast",
        )

        bottom, top = sorted(
            result.placements,
            key=lambda placement: placement.placed_bounds[1],
        )
        self.assertTrue(bottom.piece_id.endswith("notched-b"))
        self.assertTrue(top.piece_id.endswith("notched-a"))
        self.assertEqual(len(result.sheet_bounds), 1)
        self.assertFalse(
            organizer.validate_organization_result_geometry(
                classified.pieces,
                result,
                minimum_clearance=10.0,
            )
        )

    def test_free_rotation_straightens_diagonal_rails_by_the_real_contour(self):
        """Regression for the fan-shaped waste reported in the real layout."""

        entities = []
        for index in range(4):
            entities.append(
                _Path(
                    "board-%d" % index,
                    shifted(
                        ((0, 0), (150, 0), (150, 14), (0, 14)),
                        index * 180.0,
                    ),
                )
            )
        diagonal = ((0, 0), (10, 0), (110, 100), (100, 100))
        for index in range(4):
            entities.append(
                _Path(
                    "diagonal-%d" % index,
                    shifted(diagonal, 900.0 + index * 140.0),
                )
            )
        classified = classify_document_pieces(_Document(tuple(entities)))
        work_bounds = (0.0, 0.0, 360.0, 200.0)
        restricted = {
            piece.piece_id: (0.0, 90.0, 180.0, 270.0)
            for piece in classified.pieces
        }
        free = {
            piece.piece_id: tuple(float(angle) for angle in range(0, 360, 15))
            for piece in classified.pieces
        }

        fan = organize_pieces(
            classified.pieces,
            work_bounds,
            spacing=3.0,
            rotations=restricted,
            search_mode="fast",
        )
        compact = organize_pieces(
            classified.pieces,
            work_bounds,
            spacing=3.0,
            rotations=free,
            search_mode="fast",
        )

        fan_height = fan.used_bounds[3] - fan.used_bounds[1]
        compact_height = compact.used_bounds[3] - compact.used_bounds[1]
        fan_area = (
            (fan.used_bounds[2] - fan.used_bounds[0]) * fan_height
        )
        compact_area = (
            (compact.used_bounds[2] - compact.used_bounds[0]) * compact_height
        )
        diagonal_angles = {
            placement.rotation_degrees
            for placement in compact.placements
            if placement.piece_id.endswith("diagonal-0")
            or "diagonal" in placement.piece_id
        }

        self.assertLess(compact_height, fan_height * 0.6)
        self.assertLess(compact_area, fan_area * 0.6)
        self.assertTrue(diagonal_angles & {45.0, 135.0, 225.0, 315.0})
        self.assertFalse(
            organizer.validate_organization_result_geometry(
                classified.pieces,
                compact,
                minimum_clearance=3.0,
            )
        )

    def test_adaptive_piece_order_fills_mixed_concave_frontier_on_one_sheet(self):
        """Beam search may choose the next piece instead of freezing one order.

        This mix of L profiles, trapezoids and triangles reproduces the
        production failure mode where a valid cavity-filling sequence exists,
        but every fixed large-first prefix strands the last piece on a second
        sheet.  A deeper profile must also retain the valid balanced result
        when a more compact raster candidate fails the exact vector gate.
        """

        contours = (
            ((0, 0), (42, 0), (42, 11), (11, 11), (11, 35), (0, 35)),
            ((0, 0), (34, 0), (25, 35), (9, 35)),
            ((0, 0), (43, 0), (43, 10), (10, 10), (10, 30), (0, 30)),
            ((0, 0), (58, 0), (51, 25), (7, 25)),
            ((0, 0), (35, 0), (10, 45)),
            ((0, 0), (41, 0), (41, 13), (13, 13), (13, 42), (0, 42)),
            ((0, 0), (54, 0), (54, 10), (10, 10), (10, 32), (0, 32)),
        )
        classified = classify_document_pieces(
            _Document(
                tuple(
                    _Path(
                        "frontier-%d" % index,
                        shifted(points, index * 100.0),
                    )
                    for index, points in enumerate(contours)
                )
            )
        )
        work_bounds = (0.0, 0.0, 125.0, 105.0)
        rotations = {
            piece.piece_id: (0.0, 90.0, 180.0, 270.0)
            for piece in classified.pieces
        }

        fast = organize_pieces(
            classified.pieces,
            work_bounds,
            spacing=2.0,
            rotations=rotations,
            search_mode="fast",
        )
        balanced = organize_pieces(
            classified.pieces,
            work_bounds,
            spacing=2.0,
            rotations=rotations,
            search_mode="balanced",
        )
        thorough = organize_pieces(
            classified.pieces,
            work_bounds,
            spacing=2.0,
            rotations=rotations,
            search_mode="thorough",
        )

        # The corrected clearance halo can improve even the shallow search.
        self.assertLessEqual(
            organizer.organization_result_score(balanced),
            organizer.organization_result_score(fast),
        )
        self.assertEqual(len(balanced.sheet_bounds), 1)
        self.assertEqual(len(balanced.placements), len(classified.pieces))
        self.assertFalse(balanced.unplaced_piece_ids)
        self.assertLessEqual(
            organizer.organization_result_score(thorough),
            organizer.organization_result_score(balanced),
        )
        self.assertFalse(
            organizer.validate_organization_result_geometry(
                classified.pieces,
                balanced,
                minimum_clearance=2.0,
            )
        )

    def test_balanced_search_improves_a_tight_tapered_layout(self):
        dimensions = (
            (36, 36, 5),
            (46, 32, 9),
            (52, 38, 6),
            (50, 23, 8),
            (60, 30, 8),
        )
        entities = tuple(
            _Path(
                "taper-%d" % index,
                shifted(
                    ((0, 0), (width, 0), (width - inset, height), (inset, height)),
                    index * 80.0,
                ),
            )
            for index, (width, height, inset) in enumerate(dimensions)
        )
        classified = classify_document_pieces(_Document(entities))
        work_bounds = (0.0, 0.0, 130.0, 105.0)
        rotations = {
            piece.piece_id: (0.0, 90.0, 180.0, 270.0)
            for piece in classified.pieces
        }

        fast = organize_pieces(
            classified.pieces,
            work_bounds,
            spacing=2.0,
            rotations=rotations,
            search_mode="fast",
        )
        balanced = organize_pieces(
            classified.pieces,
            work_bounds,
            spacing=2.0,
            rotations=rotations,
            search_mode="balanced",
        )

        # A stronger first-stage frontier heuristic may already find the same
        # optimum; a deeper profile must preserve or improve it.
        self.assertLessEqual(
            organizer._organization_result_score(balanced),
            organizer._organization_result_score(fast),
        )
        self.assertEqual(len(balanced.placements), 5)

    def test_source_scatter_does_not_change_the_normalized_layout(self):
        shape = ((0, 0), (42, 0), (35, 28), (7, 28))
        compact = classify_document_pieces(
            _Document(
                tuple(
                    _Path("part-%d" % index, shifted(shape, index * 50.0))
                    for index in range(4)
                )
            )
        )
        scattered = classify_document_pieces(
            _Document(
                tuple(
                    _Path(
                        "part-%d" % index,
                        shifted(shape, 700.0 + index * 317.0, -900.0 + index * 211.0),
                    )
                    for index in range(4)
                )
            )
        )
        rotations = {
            piece.piece_id: (0.0, 90.0, 180.0, 270.0)
            for piece in compact.pieces
        }
        kwargs = {
            "work_bounds": (0.0, 0.0, 140.0, 100.0),
            "spacing": 2.0,
            "rotations": rotations,
            "search_mode": "fast",
        }

        compact_result = organize_pieces(compact.pieces, **kwargs)
        scattered_result = organize_pieces(scattered.pieces, **kwargs)
        compact_records = [
            (value.piece_id, value.rotation_degrees, value.placed_bounds)
            for value in compact_result.placements
        ]
        scattered_records = [
            (value.piece_id, value.rotation_degrees, value.placed_bounds)
            for value in scattered_result.placements
        ]
        self.assertEqual(compact_records, scattered_records)

    def test_two_concave_l_parts_interlock_where_two_bounding_boxes_do_not_fit(self):
        l_shape = (
            (0, 0),
            (60, 0),
            (60, 20),
            (20, 20),
            (20, 60),
            (0, 60),
        )
        first = _Path("L-a", l_shape)
        first_hole = _Path(
            "L-a-hole",
            ((5, 5), (12, 5), (12, 12), (5, 12)),
        )
        second = _Path("L-b", shifted(l_shape, 100))
        classified = classify_document_pieces(_Document((first, first_hole, second)))
        work_bounds = (-20.0, 10.0, 65.0, 95.0)
        rotations = {
            piece.piece_id: (0.0, 90.0, 180.0, 270.0)
            for piece in classified.pieces
        }

        rectangular = rectangular_baseline(
            classified.pieces,
            work_bounds,
            0.0,
            rotations,
        )
        result = organize_pieces(
            classified.pieces,
            work_bounds,
            spacing=0.0,
            rotations=rotations,
        )
        self.assertEqual(len(rectangular.placements), 1)
        self.assertEqual(len(result.placements), 2)
        self.assertFalse(result.unplaced_piece_ids)
        self.assertTrue(
            boxes_overlap(
                result.placements[0].placed_bounds,
                result.placements[1].placed_bounds,
            )
        )
        first_unit = next(
            value for value in result.placements if value.piece_id.endswith("L-a")
        )
        self.assertEqual(set(first_unit.entity_ids), {"L-a", "L-a-hole"})
        assert_raster_replay_has_no_collision(
            self,
            classified.pieces,
            result,
            work_bounds,
            0.0,
        )

    def test_rotated_triangles_fit_with_spacing_and_nonzero_work_origin(self):
        triangle = ((0, 0), (60, 0), (0, 60))
        classified = classify_document_pieces(
            _Document(
                (
                    _Path("triangle-a", triangle),
                    _Path("triangle-b", shifted(triangle, 100)),
                )
            )
        )
        work_bounds = (-10.0, 20.0, 59.0, 89.0)
        spacing = 2.0
        rotations = {
            piece.piece_id: (0.0, 90.0, 180.0, 270.0)
            for piece in classified.pieces
        }
        rectangular = rectangular_baseline(
            classified.pieces,
            work_bounds,
            spacing,
            rotations,
        )
        result = organize_pieces(
            classified.pieces,
            work_bounds,
            spacing=spacing,
            rotations=rotations,
        )
        self.assertEqual(len(rectangular.placements), 1)
        self.assertEqual(len(result.placements), 2)
        self.assertIn(180.0, {value.rotation_degrees for value in result.placements})
        assert_raster_replay_has_no_collision(
            self,
            classified.pieces,
            result,
            work_bounds,
            spacing,
        )

    def test_open_sheet_pairs_opposite_slopes_instead_of_spreading_one_row(self):
        """A roomy sheet must still reward real-contour diagonal interlocking.

        This is the small-bracket case seen in production: a bottom-left
        height-only score lays every tapered part in a long row even though a
        180-degree mate creates a materially smaller occupied footprint.
        """

        triangle = ((0, 0), (60, 0), (0, 60))
        classified = classify_document_pieces(
            _Document(
                (
                    _Path("slope-a", triangle),
                    _Path("slope-b", shifted(triangle, 100)),
                )
            )
        )
        work_bounds = (0.0, 0.0, 200.0, 200.0)
        spacing = 2.0
        rotations = {
            piece.piece_id: (0.0, 90.0, 180.0, 270.0)
            for piece in classified.pieces
        }

        result = organize_pieces(
            classified.pieces,
            work_bounds,
            spacing=spacing,
            rotations=rotations,
            search_mode="balanced",
        )

        self.assertEqual(len(result.placements), 2)
        self.assertTrue(
            boxes_overlap(
                result.placements[0].placed_bounds,
                result.placements[1].placed_bounds,
            )
        )
        used = result.used_bounds
        self.assertLess((used[2] - used[0]) * (used[3] - used[1]), 5000.0)
        assert_raster_replay_has_no_collision(
            self,
            classified.pieces,
            result,
            work_bounds,
            spacing,
        )

    def test_production_brackets_keep_exact_clearance_when_interlocked(self):
        """Regression for the 80 mm diagonal brackets from ``pé Em L``."""

        bracket = (
            (15.0, 15.0),
            (32.5, 15.0),
            (32.5, 0.0),
            (62.5, 0.0),
            (62.5, 15.0),
            (80.0, 15.0),
            (15.0, 80.0),
            (15.0, 62.5),
            (0.0, 62.5),
            (0.0, 32.5),
            (15.0, 32.5),
        )
        classified = classify_document_pieces(
            _Document(
                (
                    _Path("bracket-a", bracket),
                    _Path("bracket-b", shifted(bracket, 120.0)),
                )
            )
        )
        work_bounds = (0.0, 0.0, 1780.0, 410.0)
        spacing = 10.0
        rotations = {
            piece.piece_id: (0.0, 90.0, 180.0, 270.0)
            for piece in classified.pieces
        }

        self.assertLessEqual(
            organizer._raster_resolution(
                classified.pieces,
                work_bounds,
                spacing,
            ),
            1.0,
        )

        result = organize_pieces(
            classified.pieces,
            work_bounds,
            spacing=spacing,
            rotations=rotations,
            search_mode="balanced",
        )

        self.assertEqual(result.strategy, "contorno real")
        self.assertTrue(
            boxes_overlap(
                result.placements[0].placed_bounds,
                result.placements[1].placed_bounds,
            )
        )
        self.assertFalse(
            organizer.validate_organization_result_geometry(
                classified.pieces,
                result,
                minimum_clearance=spacing,
            )
        )

    def test_curved_ellipses_pack_diagonally_better_than_their_boxes(self):
        document = VectorDocument.create_default()
        layer_id = document.active_layer_id
        document.add_entities(
            (
                EllipseEntity(layer_id, Vec2(30, 30), 30, 30, id="curve-a"),
                EllipseEntity(layer_id, Vec2(130, 30), 30, 30, id="curve-b"),
            ),
            bump_revision=False,
        )
        classified = classify_document_pieces(document, deflection=0.5)
        work_bounds = (0.0, 0.0, 105.0, 105.0)
        rotations = {piece.piece_id: (0.0,) for piece in classified.pieces}
        rectangular = rectangular_baseline(
            classified.pieces,
            work_bounds,
            0.0,
            rotations,
        )
        result = organize_pieces(
            classified.pieces,
            work_bounds,
            spacing=0.0,
            rotations=rotations,
        )
        self.assertEqual(len(rectangular.placements), 1)
        self.assertEqual(len(result.placements), 2)
        self.assertTrue(
            boxes_overlap(
                result.placements[0].placed_bounds,
                result.placements[1].placed_bounds,
            )
        )
        assert_raster_replay_has_no_collision(
            self,
            classified.pieces,
            result,
            work_bounds,
            0.0,
        )

    def test_raster_result_is_deterministic_and_supports_quantities(self):
        l_shape = (
            (0, 0),
            (50, 0),
            (50, 15),
            (15, 15),
            (15, 50),
            (0, 50),
        )
        classified = classify_document_pieces(_Document((_Path("repeat-L", l_shape),)))
        piece = classified.pieces[0]
        work_bounds = (-5.0, -5.0, 100.0, 100.0)
        rotations = {piece.piece_id: (270.0, 0.0, 180.0, 90.0)}
        quantities = {piece.piece_id: 3}
        first = organize_pieces(
            classified.pieces,
            work_bounds,
            spacing=1.0,
            quantities=quantities,
            rotations=rotations,
        )
        second = organize_pieces(
            tuple(reversed(classified.pieces)),
            work_bounds,
            spacing=1.0,
            quantities=quantities,
            rotations={piece.piece_id: tuple(reversed(rotations[piece.piece_id]))},
        )
        first_records = [
            (
                value.piece_id,
                value.instance,
                value.rotation_degrees,
                value.placed_bounds,
            )
            for value in first.placements
        ]
        second_records = [
            (
                value.piece_id,
                value.instance,
                value.rotation_degrees,
                value.placed_bounds,
            )
            for value in second.placements
        ]
        self.assertEqual(first_records, second_records)
        self.assertEqual(len(first.placements), 3)
        assert_raster_replay_has_no_collision(
            self,
            classified.pieces,
            first,
            work_bounds,
            1.0,
        )


if __name__ == "__main__":
    unittest.main()
