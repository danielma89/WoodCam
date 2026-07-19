import unittest

from woodcam_editor.application import prepare_import_batch
from woodcam_editor.domain import PathEntity, Vec2, VectorDocument


def rectangle(document, x, y, width, height):
    return PathEntity.from_points(
        document.active_layer_id,
        (
            Vec2(x, y),
            Vec2(x + width, y),
            Vec2(x + width, y + height),
            Vec2(x, y + height),
        ),
        closed=True,
    )


class ImportBatchTests(unittest.TestCase):
    def test_colliding_import_is_moved_as_one_rigid_batch(self):
        document = VectorDocument.create_default()
        document.add_entities((rectangle(document, 0, 0, 100, 50),))
        incoming = (
            rectangle(document, 0, 0, 20, 20),
            rectangle(document, 30, 0, 20, 20),
        )

        staged, batch_id, delta = prepare_import_batch(
            document, incoming, batch_id="batch-2", gap=25
        )

        self.assertEqual(batch_id, "batch-2")
        self.assertEqual(delta, Vec2(125, 0))
        self.assertEqual(staged[1].bounds().min_x - staged[0].bounds().min_x, 30)
        self.assertTrue(all(entity.metadata["import_batch_id"] == batch_id for entity in staged))
        self.assertEqual(incoming[0].bounds().min_x, 0)

    def test_disjoint_import_preserves_source_xy(self):
        document = VectorDocument.create_default()
        document.add_entities((rectangle(document, 0, 0, 100, 50),))
        incoming = rectangle(document, 300, 40, 20, 20)

        staged, _batch_id, delta = prepare_import_batch(document, (incoming,))

        self.assertEqual(delta, Vec2(0, 0))
        self.assertEqual(staged[0].bounds(), incoming.bounds())


if __name__ == "__main__":
    unittest.main()
