import math
import unittest

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
