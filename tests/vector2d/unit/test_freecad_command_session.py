import unittest

from woodcam_editor.adapters.freecad_commands import FreeCADCommandSession
from woodcam_editor.domain import AddEntitiesCommand, PathEntity, Vec2, VectorDocument


class _Document:
    def __init__(self):
        self.transactions = []

    def openTransaction(self, label):
        self.transactions.append(("open", label))

    def commitTransaction(self):
        self.transactions.append(("commit", None))

    def abortTransaction(self):
        self.transactions.append(("abort", None))


class _Store:
    def __init__(self):
        self.document = _Document()
        self.saved = None

    def save(self, value, **_kwargs):
        self.saved = value.clone()

    def load(self):
        return self.saved.clone() if self.saved is not None else None


class FreeCADCommandSessionTest(unittest.TestCase):
    def test_command_and_persistence_share_one_transaction(self):
        vector_document = VectorDocument.create_default()
        store = _Store()
        session = FreeCADCommandSession(vector_document, store)
        entity = PathEntity.from_points(
            vector_document.active_layer_id,
            (Vec2(0, 0), Vec2(10, 0)),
        )
        session.execute(AddEntitiesCommand((entity,)))
        self.assertIn(entity.id, vector_document.entities_by_id)
        self.assertIn(entity.id, store.saved.entities_by_id)
        self.assertEqual(
            [entry[0] for entry in store.document.transactions],
            ["open", "commit"],
        )

    def test_failed_save_aborts_and_restores_in_memory_document(self):
        vector_document = VectorDocument.create_default()
        store = _Store()
        session = FreeCADCommandSession(vector_document, store)
        entity = PathEntity.from_points(
            vector_document.active_layer_id,
            (Vec2(0, 0), Vec2(10, 0)),
        )

        def fail(*_args, **_kwargs):
            raise RuntimeError("disk failure")

        original_save = store.save
        store.save = fail
        command = AddEntitiesCommand((entity,))
        with self.assertRaisesRegex(RuntimeError, "disk failure"):
            session.execute(command)
        self.assertNotIn(entity.id, vector_document.entities_by_id)
        self.assertEqual(store.document.transactions[-1][0], "abort")
        self.assertFalse(command.is_applied)
        store.save = original_save
        session.execute(command)
        self.assertIn(entity.id, vector_document.entities_by_id)


if __name__ == "__main__":
    unittest.main()
