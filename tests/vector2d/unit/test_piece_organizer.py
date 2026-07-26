import unittest
from unittest import mock

import woodcam_editor.application.piece_organizer as piece_organizer
from woodcam_editor.application.piece_organizer import (
    classify_document_pieces,
    organize_pieces,
)


class _Path:
    def __init__(self, entity_id, points, closed=True, metadata=None):
        self.id = entity_id
        self.points = points
        self.closed = closed
        self.metadata = dict(metadata or {})


class _Document:
    def __init__(self, entities):
        self.entities_by_id = {entity.id: entity for entity in entities}


class PieceOrganizerTest(unittest.TestCase):
    def test_holes_remain_attached_to_their_piece(self):
        outer = _Path("outer", [(0, 0), (100, 0), (100, 50), (0, 50)])
        hole_a = _Path("hole-a", [(10, 10), (20, 10), (20, 20), (10, 20)])
        hole_b = _Path("hole-b", [(70, 10), (80, 10), (80, 20), (70, 20)])
        result = classify_document_pieces(_Document([outer, hole_a, hole_b]))
        self.assertEqual(len(result.pieces), 1)
        self.assertEqual(set(result.pieces[0].inner_ids), {"hole-a", "hole-b"})
        organized = organize_pieces(result.pieces, (0, 0, 500, 500), spacing=5)
        self.assertEqual(len(organized.placements), 1)
        self.assertEqual(
            set(organized.placements[0].entity_ids),
            {"outer", "hole-a", "hole-b"},
        )

    def test_open_vector_does_not_become_a_piece(self):
        open_path = _Path("open", [(0, 0), (10, 0)], closed=False)
        result = classify_document_pieces(_Document([open_path]))
        self.assertEqual(result.open_entity_ids, ["open"])
        self.assertEqual(result.pieces, [])

    def test_reports_piece_that_does_not_fit(self):
        outer = _Path("large", [(0, 0), (300, 0), (300, 200), (0, 200)])
        classified = classify_document_pieces(_Document([outer]))
        result = organize_pieces(classified.pieces, (0, 0, 100, 100), spacing=2)
        self.assertEqual(result.placements, [])
        self.assertEqual(result.unplaced_piece_ids, [classified.pieces[0].piece_id])

    def test_nested_contours_from_different_imports_are_independent_pieces(self):
        outer = _Path(
            "outer", [(0, 0), (100, 0), (100, 100), (0, 100)],
            metadata={"import_batch_id": "first"},
        )
        nested = _Path(
            "nested", [(20, 20), (40, 20), (40, 40), (20, 40)],
            metadata={"import_batch_id": "second"},
        )

        result = classify_document_pieces(_Document([outer, nested]))

        self.assertEqual(len(result.pieces), 2)
        self.assertTrue(all(not piece.inner_ids for piece in result.pieces))

    def test_panelnest_instances_do_not_scan_every_other_panel(self):
        entities = []
        for index in range(120):
            metadata = {
                "import_batch_id": "cabinet",
                "panelnest_instance_id": "panel-%03d" % index,
            }
            x_value = float(index * 200)
            entities.extend(
                (
                    _Path(
                        "outer-%03d" % index,
                        [(x_value, 0), (x_value + 100, 0), (x_value + 100, 50), (x_value, 50)],
                        metadata=metadata,
                    ),
                    _Path(
                        "hole-%03d" % index,
                        [(x_value + 30, 10), (x_value + 70, 10), (x_value + 70, 40), (x_value + 30, 40)],
                        metadata=metadata,
                    ),
                )
            )

        original = piece_organizer._bounds_contains
        with mock.patch.object(
            piece_organizer,
            "_bounds_contains",
            wraps=original,
        ) as contains:
            result = classify_document_pieces(_Document(entities))

        self.assertEqual(len(result.pieces), 120)
        self.assertTrue(all(len(piece.inner_ids) == 1 for piece in result.pieces))
        # Two loops per physical instance: candidate filtering must stay
        # proportional to the instance, not 240 × 240 comparisons.
        self.assertLess(contains.call_count, 1000)

    def test_overflow_continues_on_side_by_side_sheets(self):
        paths = [
            _Path(
                "piece-%d" % index,
                [(0, 0), (80, 0), (80, 80), (0, 80)],
            )
            for index in range(3)
        ]
        classified = classify_document_pieces(_Document(paths))

        result = organize_pieces(classified.pieces, (0, 0, 100, 100), spacing=2)

        self.assertEqual(len(result.placements), 3)
        self.assertEqual(result.unplaced_piece_ids, [])
        self.assertEqual(len(result.sheet_bounds), 3)
        self.assertEqual({item.sheet_index for item in result.placements}, {0, 1, 2})
        self.assertLess(result.sheet_bounds[0][2], result.sheet_bounds[1][0])


if __name__ == "__main__":
    unittest.main()
