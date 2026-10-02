import unittest

from woodcam_editor.adapters.freecad_commands import FreeCADCommandSession
from woodcam_editor.application import UpdateLayerCommand, UpdateLayersCommand
from woodcam_editor.domain import (
    AddEntitiesCommand,
    InMemoryCommandHistory,
    Layer,
    PathEntity,
    Vec2,
    VectorDocument,
)


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
        self.save_kwargs = None

    def save(self, value, **kwargs):
        self.saved = value.clone()
        self.save_kwargs = dict(kwargs)

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
        self.assertTrue(store.save_kwargs["refresh_derived_shape"])

    def test_layer_visibility_persists_without_rebuilding_occ_projection(self):
        vector_document = VectorDocument.create_default()
        store = _Store()
        session = FreeCADCommandSession(vector_document, store)

        session.execute(
            UpdateLayerCommand(vector_document.active_layer_id, visible=False)
        )

        self.assertFalse(
            vector_document.layers_by_id[vector_document.active_layer_id].visible
        )
        self.assertFalse(store.save_kwargs["refresh_derived_shape"])

    def test_consolidated_layers_toggle_and_undo_as_one_command(self):
        vector_document = VectorDocument.create_default()
        second = Layer(name="Corte externo", purpose="cut", order=1)
        vector_document.add_layers((second,))
        layer_ids = (vector_document.active_layer_id, second.id)
        history = InMemoryCommandHistory(vector_document)

        change_set = history.execute(
            UpdateLayersCommand(layer_ids, visible=False)
        )

        self.assertEqual(change_set.layers_changed, frozenset(layer_ids))
        self.assertTrue(
            all(not vector_document.layers_by_id[value].visible for value in layer_ids)
        )
        history.undo()
        self.assertTrue(
            all(vector_document.layers_by_id[value].visible for value in layer_ids)
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

    def test_removed_host_feature_does_not_restore_opening_snapshot(self):
        vector_document = VectorDocument.create_default()
        entity = PathEntity.from_points(
            vector_document.active_layer_id,
            (Vec2(0, 0), Vec2(10, 0)),
        )
        vector_document.add_entities((entity,))
        store = _Store()
        store.saved = vector_document.clone()
        saved_before_delete = store.saved.clone()
        session = FreeCADCommandSession(vector_document, store)

        # Deleting the internal feature in FreeCAD makes load() return None.
        store.saved = None
        session.reload()
        self.assertFalse(vector_document.entities_by_id)
        self.assertFalse(vector_document.pieces_by_id)

        # Undo of that host deletion restores the persisted snapshot exactly.
        store.saved = saved_before_delete
        session.reload()
        self.assertIn(entity.id, vector_document.entities_by_id)


if __name__ == "__main__":
    unittest.main()
